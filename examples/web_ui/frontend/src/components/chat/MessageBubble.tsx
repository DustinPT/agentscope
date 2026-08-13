import type {
	ContentBlock,
	DataBlock,
	Msg,
	TextBlock,
	ToolCallBlock,
} from '@agentscope-ai/agentscope/message';
import {
        AlertCircle,
	ArrowDown,
	ArrowUp,
	Bot,
	CalendarClock,
	CheckCircle,
	ChevronDownIcon,
	CirclePlay,
	Copy,
        FileText,
	Gauge,
	Loader2,
	MessageSquareQuote,
        RotateCcw,
	Wrench,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { toast } from 'sonner';

import { ConfirmCard } from './ConfirmCard';
import { renderToolGroup } from './tool-renderers';
import type { TFunction, ToolCallWithResult } from './tool-renderers/types';
import type { WorkspaceFileEntry } from '@/api';
import { workspaceApi } from '@/api';
import { ProjectDirectoryDialog } from '@/components/project-directory/ProjectDirectoryDialog';
import { ProjectFilePreviewDialog } from '@/components/project-directory/ProjectFilePreviewDialog';
import { parseProjectLinkHref } from '@/components/project-directory/projectLink';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from '@/components/ui/collapsible.tsx';
import { Item, ItemContent } from '@/components/ui/item.tsx';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useAudioBlock, useReplayController } from '@/context/AudioContext';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';
import { formatNumber, formatTime } from '@/utils/common';

interface ToolCallGroupBlock {
	type: 'tool_call_group';
	id: string;
	toolName: string;
	calls: ToolCallWithResult[];
}

type ExtendedContentBlock = ContentBlock | ToolCallGroupBlock;

interface ContextUsageMetadata {
	current_tokens: number;
	max_context_tokens: number;
	usage_ratio: number;
}

interface ReplyRunErrorMetadata {
        kind: 'rate_limit' | 'auth' | 'timeout' | 'connection' | 'http' | 'unknown';
        summary?: string;
        detail?: string;
        retryable?: boolean;
        status_code?: number | null;
}

function getTerminalState(message: Msg): 'interrupted' | null {
        return message.metadata?.terminal_state === 'interrupted' ? 'interrupted' : null;
}

function getContextUsageMetadata(message: Msg): ContextUsageMetadata | null {
	const raw = message.metadata?.context_usage;
	if (!raw || typeof raw !== 'object') return null;

	const candidate = raw as Partial<ContextUsageMetadata>;
	if (
		typeof candidate.current_tokens !== 'number' ||
		typeof candidate.max_context_tokens !== 'number' ||
		typeof candidate.usage_ratio !== 'number' ||
		candidate.max_context_tokens <= 0
	) {
		return null;
	}

	return {
		current_tokens: candidate.current_tokens,
		max_context_tokens: candidate.max_context_tokens,
		usage_ratio: candidate.usage_ratio,
	};
}

function getReplyRunErrorMetadata(message: Msg): ReplyRunErrorMetadata | null {
        const raw = message.metadata?.run_error;
        if (!raw || typeof raw !== 'object') return null;

        const candidate = raw as Partial<ReplyRunErrorMetadata>;
        const kind = candidate.kind;
        if (
                kind !== 'rate_limit' &&
                kind !== 'auth' &&
                kind !== 'timeout' &&
                kind !== 'connection' &&
                kind !== 'http' &&
                kind !== 'unknown'
        ) {
                return null;
        }

        return {
                kind,
                summary: typeof candidate.summary === 'string' ? candidate.summary : undefined,
                detail: typeof candidate.detail === 'string' ? candidate.detail : undefined,
                retryable: typeof candidate.retryable === 'boolean' ? candidate.retryable : undefined,
                status_code: typeof candidate.status_code === 'number' ? candidate.status_code : null,
        };
}

function getRunFailedAt(message: Msg): string | null {
        const raw = message.metadata?.run_failed_at;
        return typeof raw === 'string' && raw.length > 0 ? raw : null;
}

function formatContextUsage(metadata: ContextUsageMetadata): string {
	const percentage = Math.round(Math.max(0, metadata.usage_ratio) * 100);
	return `${percentage}%(${formatNumber(metadata.current_tokens)}/${formatNumber(metadata.max_context_tokens)})`;
}

function getAttachmentDisplayName(block: DataBlock): string {
        if (block.name?.trim()) return block.name.trim();
        if (block.source.type === 'url') {
                try {
                        const pathname = new URL(block.source.url).pathname;
                        const rawName = pathname.split('/').filter(Boolean).at(-1);
                        if (rawName) {
                                return decodeURIComponent(rawName);
                        }
                } catch {
                        // Ignore malformed URLs and fall through.
                }
        }
        return block.source.media_type;
}

function getProjectParentPath(path: string): string {
        const segments = path.split('/').filter(Boolean);
        if (segments.length <= 1) {
                return '';
        }
        return segments.slice(0, -1).join('/');
}

/**
 * Group tool_call blocks of the same name into a single
 * `tool_call_group`, with each call paired to its matching
 * tool_result by id.
 *
 * Unlike the previous implementation this does NOT require calls of
 * the same name to be consecutive. When the agent issues multiple
 * concurrent tool calls (e.g. Glob + Grep), the content layout is
 * `[call_Glob, call_Grep, result_Glob, result_Grep]` — the old
 * "consecutive-same-name" approach would split call and result into
 * separate groups. This version collects all calls first (preserving
 * encounter order), then matches results, and finally emits groups
 * in the order the first call of each tool name appeared,
 * interleaved with non-tool blocks at their original positions.
 */
function groupToolCalls(content: ContentBlock[]): ExtendedContentBlock[] {
	// Pass 1: pair calls ↔ results by id, track non-tool blocks.
	const callMap = new Map<string, ToolCallWithResult>();
	const resultMap = new Map<string, ContentBlock>();
	const ordering: Array<{ type: 'tool'; id: string } | { type: 'other'; block: ContentBlock }> =
		[];

	for (const block of content) {
		if (block.type === 'tool_call') {
			const entry: ToolCallWithResult = { call: block };
			callMap.set(block.id, entry);
			ordering.push({ type: 'tool', id: block.id });
		} else if (block.type === 'tool_result') {
			const matching = callMap.get(block.id);
			if (matching) {
				matching.result = block;
			} else {
				resultMap.set(block.id, block);
			}
		} else {
			ordering.push({ type: 'other', block });
		}
	}

	// Pass 2: walk the ordering, group consecutive same-name calls
	// (now that results are already attached).
	const result: ExtendedContentBlock[] = [];
	let currentGroup: ToolCallWithResult[] = [];
	let currentToolName: string | null = null;

	const flush = () => {
		if (currentGroup.length > 0 && currentToolName) {
			result.push({
				type: 'tool_call_group',
				id: crypto.randomUUID(),
				toolName: currentToolName,
				calls: currentGroup,
			});
			currentGroup = [];
			currentToolName = null;
		}
	};

	for (const item of ordering) {
		if (item.type === 'other') {
			flush();
			result.push(item.block);
		} else {
			const entry = callMap.get(item.id);
			if (!entry) continue;
			if (currentToolName !== null && currentToolName !== entry.call.name) {
				flush();
			}
			currentToolName = entry.call.name;
			currentGroup.push(entry);
		}
	}
	flush();

	// Orphan results (no matching call) — render as synthetic groups.
	for (const [id, block] of resultMap) {
		if (block.type === 'tool_result') {
			result.push({
				type: 'tool_call_group',
				id: crypto.randomUUID(),
				toolName: block.name,
				calls: [
					{
						call: {
							type: 'tool_call',
							id,
							name: block.name,
							input: '',
							state: 'finished' as const,
						},
						result: block,
					},
				],
			});
		}
	}

	return result;
}

const AUDIO_WAVE_LINES: Array<{ x: number; y1: number; y2: number }> = [
	{ x: 2, y1: 10, y2: 13 },
	{ x: 6, y1: 6, y2: 17 },
	{ x: 10, y1: 3, y2: 21 },
	{ x: 14, y1: 8, y2: 15 },
	{ x: 18, y1: 5, y2: 18 },
	{ x: 22, y1: 10, y2: 13 },
];

function AudioWave({ isPlaying = true, className }: { isPlaying?: boolean; className?: string }) {
	return (
		<>
			{isPlaying && (
				<style>{`
					@keyframes audioWave {
						0%, 100% { transform: scaleY(1); }
						50%      { transform: scaleY(0.3); }
					}
				`}</style>
			)}
			<svg
				xmlns="http://www.w3.org/2000/svg"
				width="24"
				height="24"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				strokeWidth={2}
				strokeLinecap="round"
				strokeLinejoin="round"
				className={className}
			>
				{AUDIO_WAVE_LINES.map(({ x, y1, y2 }, i) => (
					<line
						key={x}
						x1={x}
						x2={x}
						y1={y1}
						y2={y2}
						style={{
							transformOrigin: `${x}px 12px`,
							animation: isPlaying
								? `audioWave 0.8s ease-in-out ${i * 0.12}s infinite`
								: 'none',
						}}
					/>
				))}
			</svg>
		</>
	);
}

function ThinkingBlock({
        label,
        thinking,
        autoOpen,
}: {
        label: string;
        thinking: string;
        autoOpen: boolean;
}) {
        const [open, setOpen] = useState(autoOpen);

        useEffect(() => {
                setOpen(autoOpen);
        }, [autoOpen]);

        return (
                <Collapsible open={open} onOpenChange={setOpen} className="text-muted-foreground">
                        <CollapsibleTrigger asChild>
                                <button
                                        type="button"
                                        className="flex w-full items-center gap-1 text-left text-sm cursor-pointer"
                                >
                                        <span>{label}</span>
                                        <ChevronDownIcon
                                                className={cn(
                                                        'size-4 transition-transform',
                                                        open && 'rotate-180',
                                                )}
                                        />
                                </button>
                        </CollapsibleTrigger>
                        <CollapsibleContent>
                                <p className="mt-1 whitespace-pre-wrap">{thinking}</p>
                        </CollapsibleContent>
                </Collapsible>
        );
}

/**
 * Inline audio control rendered *inside* the time/usage Badge so the play
 * icon visually merges into the same chip rather than floating as its own
 * pill.
 */
function AudioInlineControl({ block }: { block: DataBlock }) {
	const { t } = useTranslation();
	const audioState = useAudioBlock(block.id);
	const replayController = useReplayController();
	const audioRef = useRef<HTMLAudioElement | null>(null);
	const [isPlaying, setIsPlaying] = useState(false);

	const isStreaming = audioState?.status === 'streaming';

	// Don't build the giant base64 data URL while bytes are still streaming —
	// it would re-allocate on every DATA_BLOCK_DELTA. Live playback during
	// that window is handled by the manager's WavStreamPlayer; we only need
	// `src` for replay after the stream ends (or for historical messages).
	let src: string | null = null;
	if (!isStreaming) {
		if (audioState?.url) {
			src = audioState.url;
		} else if (block.source.type === 'url') {
			src = block.source.url;
		} else if (block.source.type === 'base64' && block.source.data) {
			src = `data:${block.source.media_type};base64,${block.source.data}`;
		}
	}

	// Reset the hidden <audio> when the source URL changes (e.g. streaming
	// just transitioned to a Blob URL). Without an explicit load() some
	// browsers keep the previous (or empty) source bound to the element.
	useEffect(() => {
		const el = audioRef.current;
		if (!el || !src) return;
		setIsPlaying(false);
		el.load();
	}, [src]);

	// Pause when a newer reply interrupts this block's playback.
	const interruptCount = audioState?.interruptCount ?? 0;
	useEffect(() => {
		if (interruptCount === 0) return;
		const el = audioRef.current;
		if (el && !el.paused) {
			el.pause();
		}
	}, [interruptCount]);

	if (isStreaming) {
		return <AudioWave isPlaying className="ml-1" />;
	}

	if (!src) return null;

	const toggle = async () => {
		const el = audioRef.current;
		if (!el) return;
		if (el.paused) {
			replayController?.play(el);
			try {
				await el.play();
			} catch (err) {
				console.error('Audio playback failed', err);
			}
		} else {
			el.pause();
			replayController?.stop();
		}
	};

	return (
		<>
			<button
				type="button"
				onClick={toggle}
				aria-label={
					isPlaying ? t('messageBubble.pauseAudio') : t('messageBubble.playAudio')
				}
				className="ml-1 inline-flex cursor-pointer items-center transition-opacity hover:opacity-70"
			>
				{isPlaying ? (
					<AudioWave isPlaying className="size-3" />
				) : (
					<CirclePlay className="size-3" />
				)}
			</button>
			<audio
				ref={audioRef}
				src={src}
				preload="auto"
				onPlay={() => setIsPlaying(true)}
				onPause={() => setIsPlaying(false)}
				onEnded={() => setIsPlaying(false)}
			/>
		</>
	);
}

/**
 * Render a single content block. Tool call groups are dispatched to
 * `renderToolGroup`; the per-group truncation at the first `asking` call
 * (and the trailing ConfirmCard) lives here so renderers only see a clean
 * list of calls.
 */
function renderBlock(
	block: ExtendedContentBlock,
	index: number,
	t: TFunction,
        options?: {
                activeThinkingBlockId?: string | null;
                onProjectFileLink?: (path: string) => void | Promise<void>;
                onProjectDirectoryLink?: (path: string) => void | Promise<void>;
        },
	onUserConfirm?: (
		toolCallBlock: ToolCallBlock,
		confirm: boolean,
		rules?: ToolCallBlock['suggested_rules'],
	) => void,
) {
	switch (block.type) {
		case 'tool_call_group': {
			const firstAsk = block.calls.findIndex((item) => item.call.state === 'asking');
			const visible = firstAsk === -1 ? block.calls : block.calls.slice(0, firstAsk + 1);
			const askingCall = firstAsk === -1 ? null : block.calls[firstAsk].call;
			return (
				<div key={index} className="flex flex-col gap-y-4 text-muted-foreground">
					{renderToolGroup(block.toolName, visible, t)}
					{askingCall && (
						<ConfirmCard
							toolCall={askingCall}
							onUserConfirm={(confirm, rules) => {
								if (onUserConfirm) onUserConfirm(askingCall, confirm, rules);
							}}
						/>
					)}
				</div>
			);
		}
		case 'text':
			return (
				<div key={index} className="prose w-full min-w-full">
					<ReactMarkdown
						remarkPlugins={[remarkGfm]}
                                                urlTransform={(url) => {
                                                        if (parseProjectLinkHref(url)) {
                                                                return url;
                                                        }
                                                        return defaultUrlTransform(url);
                                                }}
						components={{
                                                        a: (anchorProps) => {
                                                                const {
                                                                        href,
                                                                        children,
                                                                        className,
                                                                        style,
                                                                        title,
                                                                } = anchorProps;
                                                                const target = parseProjectLinkHref(href);
                                                                const isWorkspaceLink =
                                                                        href?.startsWith('workspace-file://') ||
                                                                        href?.startsWith('workspace-dir://');
                                                                if (!target && isWorkspaceLink) {
                                                                        return (
                                                                                <button
                                                                                        type="button"
                                                                                        className={cn(
                                                                                                className,
                                                                                                'inline-flex cursor-pointer items-center rounded-md bg-muted px-2 py-0.5 no-underline transition-colors hover:bg-muted/80 !text-blue-600 hover:!text-blue-700 dark:!text-blue-400 dark:hover:!text-blue-300',
                                                                                        )}
                                                                                        style={style}
                                                                                        title={title}
                                                                                        onClick={(event) => {
                                                                                                event.preventDefault();
                                                                                                toast.error(
                                                                                                        t(
                                                                                                                'messageBubble.invalidWorkspaceLink',
                                                                                                        ),
                                                                                                );
                                                                                        }}
                                                                                >
                                                                                        {children}
                                                                                </button>
                                                                        );
                                                                }
                                                                if (!target) {
                                                                        return (
                                                                                <a
                                                                                        href={href}
                                                                                        target="_blank"
                                                                                        rel="noreferrer"
                                                                                        className={className}
                                                                                        style={style}
                                                                                        title={title}
                                                                                >
                                                                                        {children}
                                                                                </a>
                                                                        );
                                                                }

                                                                return (
                                                                        <button
                                                                                type="button"
                                                                                className={cn(
                                                                                        className,
                                                                                        'inline-flex cursor-pointer items-center rounded-md bg-muted px-2 py-0.5 no-underline transition-colors hover:bg-muted/80 !text-blue-600 hover:!text-blue-700 dark:!text-blue-400 dark:hover:!text-blue-300',
                                                                                )}
                                                                                style={style}
                                                                                title={title}
                                                                                onClick={(event) => {
                                                                                        event.preventDefault();
                                                                                        if (target.kind === 'file') {
                                                                                                void options?.onProjectFileLink?.(
                                                                                                        target.path,
                                                                                                );
                                                                                                return;
                                                                                        }
                                                                                        void options?.onProjectDirectoryLink?.(
                                                                                                target.path,
                                                                                        );
                                                                                }}
                                                                        >
                                                                                {children}
                                                                        </button>
                                                                );
                                                        },
							code: ({ className, children, ...props }) => {
								const isInline = !String(className ?? '').startsWith('language-');
								if (isInline) {
									return (
										<code className={`${className ?? ''} break-all`} {...props}>
											{children}
										</code>
									);
								}
								return (
									<div className="relative w-full">
										<Button
											size="icon-xs"
											variant="ghost"
											className="absolute top-0 right-0 z-10"
											onClick={async (e) => {
												e.preventDefault();
												e.stopPropagation();
												await navigator.clipboard.writeText(
													String(children),
												);
											}}
										>
											<Copy />
										</Button>
										<div className="overflow-x-auto max-w-full w-full">
											<code className={className} {...props}>
												{children}
											</code>
										</div>
									</div>
								);
							},
						}}
					>
						{block.text}
					</ReactMarkdown>
				</div>
			);

		case 'thinking':
			return (
                                <ThinkingBlock
                                        key={block.id || index}
                                        label={t('messageBubble.thinking')}
                                        thinking={block.thinking}
                                        autoOpen={options?.activeThinkingBlockId === block.id}
                                />
			);

		case 'data': {
			const dataType = block.source.media_type.split('/')[0];
			// Audio data blocks render in the footer (see AudioFooterControl),
			// not inline alongside text.
			if (dataType === 'audio') return null;
			let data: string;
			if (block.source.type === 'url') {
				data = block.source.url;
			} else {
				data = `data:${block.source.media_type};base64,${block.source.data}`;
			}
			switch (dataType) {
				case 'image':
					return <img key={index} src={data} alt="Uploaded image" />;
				case 'video':
					return <video key={index} controls src={data} />;
			}
                        return (
                                <Item key={index} variant="outline" className="max-w-full">
                                        <ItemContent className="flex items-center justify-between gap-3">
                                                <div className="min-w-0 flex items-center gap-2">
                                                        <FileText className="size-4 shrink-0 text-muted-foreground" />
                                                        <span className="truncate text-sm">
                                                                {getAttachmentDisplayName(block)}
                                                        </span>
                                                </div>
                                                <Button asChild size="sm" variant="ghost" className="shrink-0">
                                                        <a href={data} target="_blank" rel="noreferrer">
                                                                Download
                                                        </a>
                                                </Button>
                                        </ItemContent>
                                </Item>
                        );
		}

		case 'hint': {
			// Parse source: try JSON, fall back to plain string, default to t('common.message').
			let hintLabel: string;
			let hintSublabel: string | null = null;
			let HintIcon = MessageSquareQuote;

			if (block.source) {
				try {
					const parsed = JSON.parse(block.source) as {
						label?: string;
						sublabel?: string;
					};
					hintLabel = parsed.label
						? t(`messageBubble.hintSource.${parsed.label}`)
						: block.source;
					hintSublabel = parsed.sublabel ?? null;
					if (parsed.label === 'team_message') HintIcon = Bot;
					else if (parsed.label === 'schedule') HintIcon = CalendarClock;
					else if (parsed.label === 'tool_output') HintIcon = Wrench;
				} catch {
					hintLabel = block.source;
				}
			} else {
				hintLabel = t('common.message');
			}
			const items: (TextBlock | DataBlock)[] =
				typeof block.hint === 'string'
					? [{ type: 'text', id: `${block.id}-text`, text: block.hint }]
					: block.hint;
			return (
				<Item variant={'outline'} className="max-w-full">
					<ItemContent className="max-w-full">
						<Collapsible>
							<CollapsibleTrigger asChild>
								<Button className="group w-full max-w-full" variant="ghost">
									<HintIcon className="size-3.5" />
									<span className="tracking-tight">{hintLabel}</span>
									{hintSublabel && (
										<span className="text-muted-foreground font-normal truncate max-w-[200px]">
											{hintSublabel}
										</span>
									)}
									<ChevronDownIcon className="ml-auto group-data-[state=open]:rotate-180" />
								</Button>
							</CollapsibleTrigger>
							<CollapsibleContent className="p-2.5 pt-0 max-w-full overflow-hidden break-all text-muted-foreground">
                                                                {items.map((inner, i) =>
                                                                        renderBlock(inner, i, t, options),
                                                                )}
							</CollapsibleContent>
						</Collapsible>
					</ItemContent>
				</Item>
			);
		}

		default:
			return null;
	}
}

interface MessageBubbleProps {
	message: Msg;
        agentId?: string | null;
        sessionId?: string | null;
	onUserConfirm: (
		toolCallBlock: ToolCallBlock,
		confirm: boolean,
		replyId: string,
		rules?: ToolCallBlock['suggested_rules'],
	) => void;
        onRollback?: (message: Msg) => void | Promise<void>;
        rollbacking?: boolean;
	containerRef?: (node: HTMLDivElement | null) => void;
	highlighted?: boolean;
}

/**
 * A message bubble component that displays a chat message.
 *
 * Running state is derived from `message.finished_at`: a missing or null
 * `finished_at` means the agent is still producing this reply. The bottom
 * status row shows a single left-aligned badge laid out as
 * `[state-icon] [duration] [↑in ↓out]`:
 *   - State icon: spinning `Loader2` while running, static `CheckCircle`
 *     once finished.
 *   - Duration is `now - created_at` while running (ticking each second),
 *     `finished_at - created_at` once complete.
 *   - Token counts only appear once `usage` is populated with non-zero
 *     values — typically after the message finishes.
 *
 * When `content` is empty and the message is still running, the bubble
 * body is omitted entirely so only the bottom status row renders.
 */
export function MessageBubble({
	message,
        agentId,
        sessionId,
	onUserConfirm,
        onRollback,
        rollbacking = false,
	containerRef,
	highlighted = false,
}: MessageBubbleProps) {
	const isUser = message.role === 'user';
	const { t } = useTranslation();
        const [previewEntry, setPreviewEntry] = useState<WorkspaceFileEntry | null>(null);
        const [directoryDialogPath, setDirectoryDialogPath] = useState<string | null>(null);

        const runError = getReplyRunErrorMetadata(message);
        const terminalState = getTerminalState(message);
        const runFailedAt = getRunFailedAt(message);
        const isFailed = !!runError;
        const isInterrupted = terminalState === 'interrupted';
        const isRunning = !message.finished_at && !isFailed && !isInterrupted;
	const hasUsage =
		!!message.usage &&
		((message.usage.input_tokens ?? 0) > 0 || (message.usage.output_tokens ?? 0) > 0);
	const contextUsage = getContextUsageMetadata(message);
        const failureSummary = runError
                ? t(`messageBubble.errorKinds.${runError.kind}`, { defaultValue: runError.summary })
                : null;
        const failureDetail = runError
                ? [runError.detail, runError.status_code ? `HTTP ${runError.status_code}` : null]
                          .filter(Boolean)
                          .join('\n')
                : null;

	// Tick once per second while running so the elapsed time updates live.
	const [now, setNow] = useState(() => Date.now());
	useEffect(() => {
		if (!isRunning) return;
		const id = setInterval(() => setNow(Date.now()), 1000);
		return () => clearInterval(id);
	}, [isRunning]);

	const blocks = groupToolCalls(message.content);
	const audioBlocks = message.content.filter(
		(b): b is DataBlock => b.type === 'data' && b.source.media_type.split('/')[0] === 'audio',
	);
	// Audio data blocks are rendered in the footer, so they shouldn't keep an
	// otherwise-empty body bubble alive.
	const hasBodyContent = blocks.some(
		(b) => !(b.type === 'data' && b.source.media_type.split('/')[0] === 'audio'),
	);
	const showBody = hasBodyContent;
	const showFooter = !isUser;
        const lastContentBlock = message.content.at(-1);
        const activeThinkingBlockId =
                isRunning && lastContentBlock?.type === 'thinking' ? lastContentBlock.id : null;

	const startMs = new Date(message.created_at).getTime();
        const terminalAt = message.finished_at ?? runFailedAt;
        const endMs = isRunning ? now : new Date(terminalAt ?? message.created_at).getTime();
	const elapsedSeconds = Math.max(0, (endMs - startMs) / 1000);
	const elapsedText = formatTime(elapsedSeconds);
	const contextUsageText = contextUsage ? formatContextUsage(contextUsage) : null;

        const listWorkspaceFiles = useCallback(
                (path = '') => {
                        if (!agentId || !sessionId) {
                                return Promise.reject(new Error('Missing workspace context'));
                        }
                        return workspaceApi.files.list(agentId, sessionId, path);
                },
                [agentId, sessionId],
        );
        const buildWorkspaceFileDownloadUrl = useCallback(
                (path = '') => {
                        if (!agentId || !sessionId) {
                                return null;
                        }
                        return workspaceApi.files.buildDownloadUrl(agentId, sessionId, path);
                },
                [agentId, sessionId],
        );
        const buildWorkspaceFilePreviewUrl = useCallback(
                (path: string) => {
                        if (!agentId || !sessionId) {
                                return null;
                        }
                        return workspaceApi.files.buildPreviewUrl(agentId, sessionId, path);
                },
                [agentId, sessionId],
        );

        const handleProjectLinkError = useCallback(
                (fallbackKey: string, error: unknown) => {
                        const messageText =
                                error instanceof Error && error.message ? error.message : t(fallbackKey);
                        toast.error(messageText);
                },
                [t],
        );

        const handleWorkspaceFileLink = useCallback(
                async (path: string) => {
                        if (!agentId || !sessionId) {
                                toast.error(t('messageBubble.workspaceLinkUnavailable'));
                                return;
                        }
                        try {
                                const parentPath = getProjectParentPath(path);
                                const entries = await workspaceApi.files.list(
                                        agentId,
                                        sessionId,
                                        parentPath,
                                );
                                const entry = entries.find((item) => item.path === path && !item.is_dir);
                                if (!entry) {
                                        throw new Error(t('messageBubble.workspaceFileNotFound'));
                                }
                                setPreviewEntry(entry);
                        } catch (error) {
                                handleProjectLinkError('messageBubble.workspaceFileNotFound', error);
                        }
                },
                [agentId, handleProjectLinkError, sessionId, t],
        );

        const handleWorkspaceDirectoryLink = useCallback(
                async (path: string) => {
                        if (!agentId || !sessionId) {
                                toast.error(t('messageBubble.workspaceLinkUnavailable'));
                                return;
                        }
                        try {
                                await workspaceApi.files.list(agentId, sessionId, path);
                                setDirectoryDialogPath(path);
                        } catch (error) {
                                handleProjectLinkError('messageBubble.workspaceDirectoryNotFound', error);
                        }
                },
                [agentId, handleProjectLinkError, sessionId, t],
        );

	return (
		<div
			ref={containerRef}
			data-message-id={message.id}
			className={cn(
                                'group mb-4 flex w-full max-w-full flex-col rounded-2xl transition-colors',
				isUser ? 'items-end' : 'items-start',
				highlighted && 'bg-primary/6 ring-1 ring-primary/20',
			)}
		>
			{showBody && (
				<div
					className={`p-4 rounded-xl space-y-2 max-w-full ${
						isUser ? 'w-fit bg-secondary' : 'w-full min-w-full'
					}`}
				>
					{blocks.map((block, i) =>
						renderBlock(
							block,
							i,
							t,
                                                        {
                                                                activeThinkingBlockId,
                                                                onProjectFileLink: handleWorkspaceFileLink,
                                                                onProjectDirectoryLink: handleWorkspaceDirectoryLink,
                                                        },
							(
								toolCall: ToolCallBlock,
								confirm: boolean,
								rules?: ToolCallBlock['suggested_rules'],
							) => {
								onUserConfirm(toolCall, confirm, message.id, rules);
								toolCall.state = confirm ? 'allowed' : 'finished';
							},
						),
					)}
				</div>
			)}
                        {isUser && onRollback && (
                                <div className="mt-2 flex w-fit items-center px-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                                        <Button
                                                type="button"
                                                variant="ghost"
                                                size="sm"
                                                className="h-7 px-2 text-xs text-muted-foreground"
                                                onClick={() => void onRollback(message)}
                                                disabled={rollbacking}
                                        >
                                                {rollbacking ? (
                                                        <Loader2 className="size-3 animate-spin" />
                                                ) : (
                                                        <RotateCcw className="size-3" />
                                                )}
                                                {rollbacking
                                                        ? t('messageBubble.rollbacking')
                                                        : t('messageBubble.rollback')}
                                        </Button>
                                </div>
                        )}
			{showFooter && (
				<div className="flex flex-row items-center text-muted-foreground gap-x-4 px-2 w-full">
					<Badge
						variant="secondary"
                                                aria-label={
                                                        isRunning
                                                                ? t('messageBubble.running')
                                                                : isInterrupted
                                                                  ? t('messageBubble.interrupted')
                                                                : isFailed
                                                                  ? t('messageBubble.failed')
                                                                  : undefined
                                                }
					>
                                                {isRunning ? (
							<Loader2 data-icon="inline-start" className="animate-spin" />
                                                ) : isInterrupted ? (
                                                        <AlertCircle
                                                                data-icon="inline-start"
                                                                className="text-amber-600"
                                                        />
                                                ) : isFailed ? (
                                                        <AlertCircle
                                                                data-icon="inline-start"
                                                                className="text-destructive"
                                                        />
						) : (
							<CheckCircle data-icon="inline-start" />
						)}
						<span className="tabular-nums tracking-tighter">{elapsedText}</span>
                                                {isFailed && failureSummary && (
                                                        <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                        <span className="ml-1 inline-flex max-w-[220px] cursor-help items-center gap-1 text-destructive">
                                                                                <span className="truncate">
                                                                                        {failureSummary}
                                                                                </span>
                                                                        </span>
                                                                </TooltipTrigger>
                                                                <TooltipContent
                                                                        sideOffset={6}
                                                                        className="max-w-sm whitespace-pre-wrap break-words"
                                                                >
                                                                        {failureDetail || failureSummary}
                                                                </TooltipContent>
                                                        </Tooltip>
                                                )}
                                                {isInterrupted && (
                                                        <span className="ml-1 text-amber-700">
                                                                {t('messageBubble.interrupted')}
                                                        </span>
                                                )}
						{hasUsage && (
							<>
								<ArrowUp data-icon="inline-start" className="ml-1" />
								<span className="tabular-nums">
									{formatNumber(message.usage?.input_tokens ?? 0)}
								</span>
								<ArrowDown data-icon="inline-start" className="ml-1" />
								<span className="tabular-nums">
									{formatNumber(message.usage?.output_tokens ?? 0)}
								</span>
							</>
						)}
						{contextUsageText && (
							<>
								<Gauge data-icon="inline-start" className="ml-1" />
								<span
									className="tabular-nums"
									title={t('messageBubble.contextUsageTooltip')}
								>
									{contextUsageText}
								</span>
							</>
						)}
						{audioBlocks.map((block) => (
							<AudioInlineControl key={block.id} block={block} />
						))}
					</Badge>
				</div>
			)}
                        <ProjectFilePreviewDialog
                                open={previewEntry !== null}
                                onOpenChange={(open) => {
                                        if (!open) {
                                                setPreviewEntry(null);
                                        }
                                }}
                                entry={previewEntry}
                                buildWorkspaceFilePreviewUrl={buildWorkspaceFilePreviewUrl}
                                buildWorkspaceFileDownloadUrl={buildWorkspaceFileDownloadUrl}
                        />
                        <ProjectDirectoryDialog
                                open={directoryDialogPath !== null}
                                onOpenChange={(open) => {
                                        if (!open) {
                                                setDirectoryDialogPath(null);
                                        }
                                }}
                                initialPath={directoryDialogPath ?? ''}
                                listWorkspaceFiles={listWorkspaceFiles}
                                buildWorkspaceFileDownloadUrl={buildWorkspaceFileDownloadUrl}
                                buildWorkspaceFilePreviewUrl={buildWorkspaceFilePreviewUrl}
                        />
		</div>
	);
}
