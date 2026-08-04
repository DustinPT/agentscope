import type { Msg } from '@agentscope-ai/agentscope/message';
import type { DataBlock, TextBlock } from '@agentscope-ai/agentscope/message';
import type { TaskContext } from '@agentscope-ai/agentscope/state';
import { ArrowDownToLine, ArrowLeft, ArrowUpToLine, Bot, Download, List, Toolbox } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import type { ChatModelConfig, SessionExportOptions, SessionView, SubAgentSessionView } from '@/api';
import { sessionApi } from '@/api';
import { ChatContent } from '@/components/chat/ChatContent.tsx';
import {
	buildContentBlockFromFile,
	getSupportedInputTypes,
} from '@/components/chat/inputUtils';
import { TaskPanel } from '@/components/chat/TaskPanel';
import type { ProcessedFile } from '@/components/chat/TextInput';
import { UserMessageDirectory } from '@/components/chat/UserMessageDirectory';
import { buildUserMessageOutline } from '@/components/chat/userMessageOutline';
import { CreateCredentialDialog } from '@/components/dialog/CreateCredentialDialog';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { SessionExportDialog } from '@/components/dialog/SessionExportDialog';
import { WorkspaceDrawer } from '@/components/drawer/WorkspaceDrawer.tsx';
import { ModelParametersPopover } from '@/components/popover/ModelParametersPopover';
import { LlmSelect } from '@/components/select/LlmSelect';
import { PermissionModeSelect } from '@/components/select/PermissionModeSelect.tsx';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useAvailableModels } from '@/hooks/useAvailableModels';
import { useMessages } from '@/hooks/useMessages';
import { useSessions } from '@/hooks/useSessions';
import { useWorkspace } from '@/hooks/useWorkspace.ts';
import { useTranslation } from '@/i18n/useI18n';
import { buildSessionExportFilename, downloadJsonFile } from '@/utils/sessionExport';

interface ChatViewportProps {
	/**
	 * The agent that owns the session being viewed. May be the
	 * user-facing leader agent or — when drilled into a team member
	 * via the URL's `:memberId` slot — a worker agent.
	 */
	agentId: string | null;
	/**
	 * The session whose messages, model config, permission mode, and
	 * workspace drive every control rendered here.
	 */
	sessionId: string | null;
	/**
	 * Optional pre-resolved session snapshot supplied by the outer page.
	 * Used for nested views such as sub-agent sessions, which are not
	 * returned by the child agent's top-level session list endpoint.
	 */
	sessionViewOverride?: SessionView | SubAgentSessionView | null;
	subSessionMeta?: {
		agentName: string;
		sessionName: string;
		parentSessionName: string;
	} | null;
	onReturnToRootSession?: () => void;
	pendingInitialUserMsg?: Msg | null;
	onPendingInitialUserMsgConsumed?: () => void;
	/**
	 * Optional hook invoked when a team membership change arrives on
	 * this viewport's SSE stream. The outer page owns the session list
	 * that backs the team sidebar, so it must be told to refetch too;
	 * passing this callback wires that signal up.
	 */
	onTeamUpdated?: () => void;
	onAgentUpdated?: () => void | Promise<void>;
}

/**
 * The right-hand main panel of the chat page — every UI element that
 * operates on a single `(agentId, sessionId)` pair lives here:
 * model selector, permission mode select, message stream, workspace
 * drawer, and the team sidebar.
 *
 * Self-contained by design. The outer page passes in the
 * `(agentId, sessionId)` it wants displayed (which may be the leader
 * session or a focused team member's session) and this component
 * does the rest — fetching the session view, syncing local UI state
 * with it, and writing changes back to the same session. Switching
 * between leader and member is just a prop change; no internal
 * branching is needed.
 *
 * @param agentId - The agent to operate on. `null` while no agent is
 *   selected yet (renders an empty / disabled state).
 * @param sessionId - The session to operate on. `null` while no
 *   session is selected yet.
 * @returns The right-side main JSX of the chat page.
 */
export function ChatViewport({
	agentId,
	sessionId,
	sessionViewOverride,
	subSessionMeta,
	onReturnToRootSession,
	pendingInitialUserMsg,
	onPendingInitialUserMsgConsumed,
	onTeamUpdated,
}: ChatViewportProps) {
	const { sessions, refetch: refetchSessions } = useSessions(agentId);
	const { groups } = useAvailableModels();
	const { t } = useTranslation();

	// When the viewport agent differs from the outer page's selected
	// agent (i.e. user drilled into a team member), `refetchSessions`
	// only refreshes the member's session list. The team sidebar is
	// driven by the leader's session list owned by the outer page, so
	// we also fire the parent's refetch to keep that in sync.
	const handleTeamUpdated = useCallback(() => {
		refetchSessions();
		onTeamUpdated?.();
	}, [refetchSessions, onTeamUpdated]);
	const refetchRelatedSessions = useCallback(async () => {
		await refetchSessions();
		onTeamUpdated?.();
	}, [refetchSessions, onTeamUpdated]);

	const [selectedModel, setSelectedModel] = useState<ChatModelConfig | null>(null);
	const [selectedFallbackModel, setSelectedFallbackModel] = useState<ChatModelConfig | null>(
		null,
	);
	const [selectedPermissionMode, setSelectedPermissionMode] = useState<string>('default');
	const [credentialOpen, setCredentialOpen] = useState(false);
	const [credentialRefetchTrigger, setCredentialRefetchTrigger] = useState(0);
	const [tasksContext, setTasksContext] = useState<TaskContext | null>(null);
	const [outlineOpen, setOutlineOpen] = useState(false);
        const [exportOpen, setExportOpen] = useState(false);
        const [draftText, setDraftText] = useState('');
        const [draftFiles, setDraftFiles] = useState<ProcessedFile[]>([]);
        const [rollbackingMessageId, setRollbackingMessageId] = useState<string | null>(null);
        const [rollbackConfirmMessage, setRollbackConfirmMessage] = useState<Msg | null>(null);
	const [scrollTargetMessageId, setScrollTargetMessageId] = useState<string | null>(null);
	const [scrollViewportCommand, setScrollViewportCommand] = useState<{
		type: 'top' | 'bottom';
		nonce: number;
	} | null>(null);

	const handleStateUpdated = useCallback((value: Record<string, unknown>) => {
		if (value.tasks_context) {
			setTasksContext(value.tasks_context as TaskContext);
		}
		// TODO: handle permission_context updates when permission UI is built
	}, []);

        const { msgs, streaming, canStop, send, onUserConfirm, cancelCurrentRun, reload } = useMessages(
		agentId,
		sessionId,
		{
			onTeamUpdated: handleTeamUpdated,
			onSessionUpdated: refetchRelatedSessions,
			pendingInitialUserMsg,
			onPendingInitialUserMsgConsumed,
			onStateUpdated: handleStateUpdated,
		},
	);
	const {
		mcps,
		loading: mcpsLoading,
		skills,
		skillsLoading,
        } = useWorkspace(agentId, sessionId);

	const view = sessionViewOverride ?? sessions.find((v) => v.session.id === sessionId) ?? null;

	const userMessageOutline = useMemo(
		() =>
			buildUserMessageOutline(msgs, (index) =>
				t('user-message-directory.attachmentFallback', { index }),
			),
		[msgs, t],
	);

	// ChatViewport keeps its own `useSessions(agentId)` instance (the
	// outer page has a separate one). Its built-in fetch only fires on
	// `agentId` change, so when the outer page creates a new session
	// under the same agent, this list doesn't auto-refresh. Without
	// this refetch, `view` would stay `null` for the brand-new session
	// id and every effect below would early-return on `!view`,
	// leaving the model select and friends pinned to whatever the
	// previously-viewed session had configured.
	useEffect(() => {
		if (!sessionId) return;
		if (view) return;
		if (sessionViewOverride) return;
		refetchSessions();
	}, [sessionId, view, sessionViewOverride, refetchSessions]);

	// Reset local UI state when the target session changes. Otherwise
	// the model select (and disabled-state guards on `send`) would
	// show the previous session's model during the in-flight window
	// before `view` repopulates — and an immediate send would post to
	// a session whose backend config doesn't actually have that model.
	useEffect(() => {
		setSelectedModel(null);
		setSelectedFallbackModel(null);
	}, [sessionId]);

	useEffect(() => {
		setOutlineOpen(false);
                setDraftText('');
                setDraftFiles([]);
                setRollbackingMessageId(null);
                setRollbackConfirmMessage(null);
		setScrollTargetMessageId(null);
		setScrollViewportCommand(null);
	}, [sessionId]);

        const restoreDraftFromMessage = useCallback((message: Msg) => {
                const text = message.content
                        .filter((block): block is TextBlock => block.type === 'text')
                        .map((block) => block.text)
                        .join('\n');
                const files = message.content
                        .filter((block): block is DataBlock => block.type === 'data')
                        .map((block) => ({
                                name: block.name ?? 'attachment',
                                status: 'done' as const,
                                block,
                        }));
                setDraftText(text);
                setDraftFiles(files);
        }, []);

	const selectedModelCard = useMemo(() => {
		if (!selectedModel) return null;
		const items = groups[selectedModel.type];
		if (!items) return null;
		for (const { models } of items) {
			const card = models.find((m) => m.name === selectedModel.model);
			if (card) return card;
		}
		return null;
	}, [groups, selectedModel]);

	/**
	 * Pick the first model the available-models endpoint surfaces, used
	 * as a sensible default when the current session has no model
	 * configured yet.
	 *
	 * @returns The first available `ChatModelConfig`, or `null` when
	 *   no credentials / models are configured.
	 */
	const getFirstAvailableModel = useCallback((): ChatModelConfig | null => {
		const firstType = Object.keys(groups)[0];
		if (!firstType) return null;
		const items = groups[firstType];
		if (!items || items.length === 0) return null;
		const firstItem = items[0];
		const firstModel = (firstItem.models as { name?: string; id?: string }[])[0];
		if (!firstModel) return null;
		const modelName = firstModel.name ?? firstModel.id ?? null;
		if (!modelName) return null;
		return {
			type: firstType,
			credential_id: firstItem.credential.id,
			model: modelName,
			parameters: {},
		};
	}, [groups]);

	// Sync tasksContext from the session snapshot. Real-time updates
	// arrive via the CustomEvent(name="state_updated") → the
	// onStateUpdated callback above. We always mirror the snapshot
	// (including clearing to null when the session is gone or has no
	// tasks yet) so that switching sessions doesn't leak stale tasks
	// from the previous one.
	useEffect(() => {
		if (!view) {
			setTasksContext(null);
			return;
		}
		const tc = (view.session.state as Record<string, unknown>)?.tasks_context as
			| TaskContext
			| undefined;
		setTasksContext(tc ?? null);
	}, [view]);

	// Sync selectedModel + selectedFallbackModel from the session
	// record. If the session has no model configured yet, auto-pick
	// the first available one and persist it back so subsequent
	// reasoning has a model to call.
	//
	// Important: skip while `view` is still loading. Otherwise the
	// in-flight window between "agentId changed" and "useSessions
	// returned the new list" looks like "session has no model" and
	// we would racily auto-select + persist the first available
	// model, clobbering whatever the user had configured.
	useEffect(() => {
		if (!view) return;
		const sessionModel = view.session.config.chat_model_config;

		if (sessionModel) {
			setSelectedModel(sessionModel);
		} else {
			const firstModel = getFirstAvailableModel();
			if (firstModel) {
				setSelectedModel(firstModel);
				if (sessionId && agentId) {
					sessionApi
						.update(sessionId, agentId, { chat_model_config: firstModel })
						.then(() => refetchRelatedSessions())
						.catch(() => {});
				}
			} else {
				setSelectedModel(null);
			}
		}

		setSelectedFallbackModel(view.session.config.fallback_chat_model_config ?? null);
	}, [view, sessionId, agentId, getFirstAvailableModel, refetchRelatedSessions]);

	// Sync selectedPermissionMode when the session changes. Same
	// loading-window guard as above — don't reset the displayed mode
	// to "default" while the new session view is still on the wire.
	useEffect(() => {
		if (!view) return;
		const mode = (view.session.state?.permission_context as Record<string, unknown>)
			?.mode as string;
		setSelectedPermissionMode(mode ?? 'default');
	}, [sessionId, view]);

	/**
	 * Persist a model change to the session and refetch so the local
	 * view picks up the new value.
	 *
	 * @param config - New chat model config; `null` is ignored
	 *   because the primary selector does not allow clearing.
	 */
	const handleLlmChange = async (config: ChatModelConfig | null) => {
		if (!config || !sessionId || !agentId) return;
		setSelectedModel(config);
		await sessionApi.update(sessionId, agentId, { chat_model_config: config });
		await refetchRelatedSessions();
	};

	/**
	 * Persist a parameter change on the currently selected model.
	 *
	 * @param parameters - New parameter map (model-provider specific).
	 */
	const handleParametersChange = async (parameters: Record<string, unknown>) => {
		if (!selectedModel || !sessionId || !agentId) return;
		const updated = { ...selectedModel, parameters };
		setSelectedModel(updated);
		await sessionApi.update(sessionId, agentId, { chat_model_config: updated });
		await refetchRelatedSessions();
	};

	/**
	 * Persist a fallback-model change. `null` clears the fallback.
	 *
	 * @param config - New fallback config or `null` to clear.
	 */
	const handleFallbackChange = async (config: ChatModelConfig | null) => {
		if (!sessionId || !agentId) return;
		setSelectedFallbackModel(config);
		await sessionApi.update(sessionId, agentId, { fallback_chat_model_config: config });
		await refetchRelatedSessions();
	};

	/**
	 * Persist a permission-mode change.
	 *
	 * @param mode - New permission mode (e.g. `default`, `explore`).
	 */
	const handlePermissionModeChange = async (mode: string) => {
		setSelectedPermissionMode(mode);
		if (!sessionId || !agentId) return;
		await sessionApi.update(sessionId, agentId, { permission_mode: mode });
		await refetchRelatedSessions();
	};

	const handleDirectorySelect = useCallback((messageId: string) => {
		setScrollTargetMessageId(messageId);
		setOutlineOpen(false);
	}, []);

	const handleScrollViewport = useCallback((type: 'top' | 'bottom') => {
		setScrollViewportCommand({ type, nonce: Date.now() });
	}, []);

        const handleSessionExport = useCallback(
                async (options: SessionExportOptions) => {
                        if (!sessionId || !agentId) return;
                        const payload = await sessionApi.exportSession(sessionId, agentId, options);
                        downloadJsonFile(
                                payload,
                                buildSessionExportFilename(
                                        payload.session.agent_name,
                                        payload.session.session_name,
                                        payload.exported_at,
                                ),
                        );
                        toast.success(t('dialog-session-export.success'));
                },
                [agentId, sessionId, t],
        );

        const handleRollbackMessage = useCallback((message: Msg) => {
                setRollbackConfirmMessage(message);
        }, []);

        const confirmRollbackMessage = useCallback(
                async (message: Msg) => {
                        if (!sessionId || !agentId) return;
                        setRollbackingMessageId(message.id);
                        try {
                                const response = await sessionApi.rollback(sessionId, agentId, {
                                        message_id: message.id,
                                });
                                await reload();
                                restoreDraftFromMessage(response.restored_draft_message);
                                setRollbackConfirmMessage(null);
                        } catch {
                                toast.error(t('chat.rollbackFailed'));
                        } finally {
                                setRollbackingMessageId(null);
                        }
                },
                [agentId, reload, restoreDraftFromMessage, sessionId, t],
        );

	return (
		<>
			<main className="flex size-full">
				<div className="flex flex-col flex-1 min-h-0 p-2">
					{subSessionMeta && (
						<div className="mb-2 flex items-center justify-between gap-3 rounded-lg border bg-muted/40 px-3 py-2">
							<div className="min-w-0">
								<div className="flex items-center gap-2 text-sm">
									<Badge variant="secondary" className="gap-1">
										<Bot className="size-3.5" />
										子会话
									</Badge>
									<span className="truncate font-medium">
										{subSessionMeta.agentName} / {subSessionMeta.sessionName}
									</span>
								</div>
								<div className="mt-1 text-xs text-muted-foreground">
									当前正在查看子 agent 会话，所属主会话：{subSessionMeta.parentSessionName}
								</div>
							</div>
							<Button
								variant="outline"
								size="sm"
								onClick={onReturnToRootSession}
								disabled={!onReturnToRootSession}
							>
								<ArrowLeft className="size-4" />
								返回主会话
							</Button>
						</div>
					)}
					<div className="flex flex-row gap-x-2 justify-between">
						<div id="tour-llm-select" className="flex flex-row items-center gap-x-1">
							<LlmSelect
								value={selectedModel}
								onChange={handleLlmChange}
								onAddCredential={() => setCredentialOpen(true)}
								refetchTrigger={credentialRefetchTrigger}
							/>
							<ModelParametersPopover
								selectedModel={selectedModel}
								modelCard={selectedModelCard}
								onChange={handleParametersChange}
								selectedFallbackModel={selectedFallbackModel}
								onFallbackChange={handleFallbackChange}
							/>
						</div>
						<div id="tour-permission-mode" className="flex flex-row gap-x-2">
							<PermissionModeSelect
								value={selectedPermissionMode}
								disabled={!sessionId}
								onChange={handlePermissionModeChange}
							/>
						</div>
					</div>
					<div className="flex flex-1 justify-center min-h-0 overflow-hidden relative [--chat-content-w:36rem]">
						<TaskPanel
							className="absolute left-0 top-0 h-full max-w-[calc(50%-var(--chat-content-w)/2)]"
							tasksContext={tasksContext}
						/>
						<ChatContent
							className={'max-w-[var(--chat-content-w)] w-full'}
							msgs={msgs}
							sessionKey={sessionId}
							scrollTargetMessageId={scrollTargetMessageId}
							activeMessageId={scrollTargetMessageId}
							onScrollTargetHandled={() => setScrollTargetMessageId(null)}
							scrollViewportCommand={scrollViewportCommand}
							sending={streaming}
							stoppable={canStop}
							disabled={selectedModel === null}
							onSend={send}
							onStop={cancelCurrentRun}
                                                        onRollbackMessage={handleRollbackMessage}
                                                        rollbackingMessageId={rollbackingMessageId}
							onUserConfirm={onUserConfirm}
							allowedInputTypes={getSupportedInputTypes(
								selectedModelCard?.input_types,
							)}
							fileProcessor={buildContentBlockFromFile}
                                                        value={draftText}
                                                        onValueChange={setDraftText}
                                                        files={draftFiles}
                                                        onFilesChange={setDraftFiles}
						/>
					</div>
				</div>
				<div className="flex flex-col h-full gap-2 p-2">
					<Button
						size="icon-sm"
						variant="ghost"
						tooltip={t('chat.toolbar.scrollToTop')}
						onClick={() => handleScrollViewport('top')}
					>
						<ArrowUpToLine />
					</Button>
					<Button
						size="icon-sm"
						variant="ghost"
						tooltip={t('chat.toolbar.scrollToBottom')}
						onClick={() => handleScrollViewport('bottom')}
					>
						<ArrowDownToLine />
					</Button>
					<Button
						size="icon-sm"
						variant="ghost"
						tooltip={t('user-message-directory.openTooltip')}
						onClick={() => setOutlineOpen(true)}
					>
						<List />
					</Button>
                                        <Button
                                                size="icon-sm"
                                                variant="ghost"
                                                tooltip={t('chat.toolbar.exportSession')}
                                                onClick={() => setExportOpen(true)}
                                                disabled={!sessionId || !agentId}
                                        >
                                                <Download />
                                        </Button>
					<WorkspaceDrawer
						mcps={mcps}
						loading={mcpsLoading}
						skills={skills}
						skillsLoading={skillsLoading}
					>
						<Button size="icon-sm" variant="ghost">
							<Toolbox />
						</Button>
					</WorkspaceDrawer>
				</div>
			</main>
			<CreateCredentialDialog
				open={credentialOpen}
				onOpenChange={setCredentialOpen}
				onCreated={() => setCredentialRefetchTrigger((n) => n + 1)}
			/>
			<UserMessageDirectory
				open={outlineOpen}
				onOpenChange={setOutlineOpen}
				items={userMessageOutline}
				activeMessageId={scrollTargetMessageId}
				onSelect={handleDirectorySelect}
			/>
                        <SessionExportDialog
                                open={exportOpen}
                                onOpenChange={setExportOpen}
                                onConfirm={handleSessionExport}
                        />
                        <DeleteDialog
                                open={!!rollbackConfirmMessage}
                                onOpenChange={(open) => {
                                        if (!open) {
                                                setRollbackConfirmMessage(null);
                                        }
                                }}
                                title={t('chat.rollbackConfirmTitle')}
                                description={t('chat.rollbackConfirmDescription')}
                                confirmLabel={t('chat.rollbackConfirmAction')}
                                onConfirm={async () => {
                                        if (rollbackConfirmMessage) {
                                                await confirmRollbackMessage(rollbackConfirmMessage);
                                        }
                                }}
                        />
		</>
	);
}
