import type { ContentBlock, Msg, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import React from 'react';
import { useRef, useEffect } from 'react';

import { EmptyMessage } from './Empty';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { TextInput, type ProcessedFile } from '@/components/chat/TextInput.tsx';
import { cn } from '@/lib/utils';

interface ChatContentProps {
	msgs: Msg[];
        agentId?: string | null;
        sessionId?: string | null;
	sessionKey?: string | null;
	sending: boolean;
	stoppable?: boolean;
        stopDisabled?: boolean;
	disabled: boolean;
	onSend: (content: ContentBlock[]) => void;
	onStop?: () => void | Promise<void>;
        onRollbackMessage?: (message: Msg) => void | Promise<void>;
        rollbackingMessageId?: string | null;
	onUserConfirm: (
		toolCall: ToolCallBlock,
		confirm: boolean,
		replyId: string,
		rules?: ToolCallBlock['suggested_rules'],
	) => void;
	autoComplete?: (input: string) => string | null;
	className?: string;
	scrollTargetMessageId?: string | null;
	onScrollTargetHandled?: () => void;
	activeMessageId?: string | null;
	scrollViewportCommand?: {
		type: 'top' | 'bottom';
		nonce: number;
	} | null;
	/** @see TextInputProps.allowedInputTypes */
        allowedInputTypes?: string[];
	/** @see TextInputProps.fileProcessor */
	fileProcessor: (file: File) => Promise<ContentBlock | null>;
        value?: string;
        onValueChange?: (value: string) => void;
        files?: ProcessedFile[];
        onFilesChange?: (files: ProcessedFile[]) => void;
}

const ChatContentComponent: React.FC<ChatContentProps> = ({
	msgs,
        agentId,
        sessionId,
	sessionKey,
	sending,
	stoppable = false,
        stopDisabled = false,
	disabled,
	onSend,
	onStop,
        onRollbackMessage,
        rollbackingMessageId,
	onUserConfirm,
	autoComplete,
	className,
	scrollTargetMessageId,
	onScrollTargetHandled,
	activeMessageId,
	scrollViewportCommand,
	allowedInputTypes,
	fileProcessor,
        value,
        onValueChange,
        files,
        onFilesChange,
}) => {
	const scrollAreaRef = useRef<HTMLDivElement>(null);
	const messageRefs = useRef<Map<string, HTMLDivElement>>(new Map());
	const prevScrollHeightRef = useRef<number>(0);
	const wasNearBottomRef = useRef<boolean>(true);
        const followBottomRef = useRef<boolean>(true);
	const pendingInitialScrollRef = useRef<boolean>(true);
	const skipNextScrollCheckRef = useRef<boolean>(false);
        const ignoreScrollEventsUntilRef = useRef<number>(0);

        const scrollToBottom = React.useCallback((behavior: ScrollBehavior) => {
                const scrollArea = scrollAreaRef.current;
                if (!scrollArea) return;
                ignoreScrollEventsUntilRef.current = Date.now() + (behavior === 'smooth' ? 400 : 100);
                scrollArea.scrollTo({
                        top: scrollArea.scrollHeight,
                        behavior,
                });
        }, []);

	const registerMessageRef = React.useCallback(
		(messageId: string) => (node: HTMLDivElement | null) => {
			if (node) {
				messageRefs.current.set(messageId, node);
				return;
			}
			messageRefs.current.delete(messageId);
		},
		[],
	);

	// When the user opens a different session, force exactly one initial
	// jump to the latest message after that session's history is rendered.
	useEffect(() => {
		prevScrollHeightRef.current = 0;
		wasNearBottomRef.current = true;
                followBottomRef.current = true;
		pendingInitialScrollRef.current = true;
		skipNextScrollCheckRef.current = true;
                ignoreScrollEventsUntilRef.current = 0;
	}, [sessionKey]);

	// Auto-scroll to bottom only if user is already near the bottom
	useEffect(() => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;

		const currentScrollHeight = scrollArea.scrollHeight;
		const prevScrollHeight = prevScrollHeightRef.current;

		// On session switch the first render may still contain the previous
		// session's messages before `useMessages` clears and reloads them.
		// Skip exactly one check so the forced initial scroll is consumed only
		// by the newly selected session's content.
		if (skipNextScrollCheckRef.current) {
			skipNextScrollCheckRef.current = false;
			prevScrollHeightRef.current = currentScrollHeight;
			return;
		}

		const shouldForceInitialScroll =
			pendingInitialScrollRef.current && (msgs.length > 0 || sending);
		const contentExpanded =
			currentScrollHeight > prevScrollHeight &&
			(prevScrollHeight > 0 || msgs.length > 0);

		const shouldCheck =
			shouldForceInitialScroll ||
			contentExpanded;

		if (shouldCheck) {
                        // Keep following after an explicit "scroll to bottom"
                        // until the user manually scrolls away.
                        const isNearBottom =
                                shouldForceInitialScroll ||
                                followBottomRef.current ||
                                wasNearBottomRef.current;

			if (isNearBottom) {
				const behavior: ScrollBehavior =
					shouldForceInitialScroll || sending ? 'auto' : 'smooth';
                                scrollToBottom(behavior);
                                wasNearBottomRef.current = true;
                                followBottomRef.current = true;
			}
		}

		if (shouldForceInitialScroll) {
			pendingInitialScrollRef.current = false;
		}

		prevScrollHeightRef.current = currentScrollHeight;
        }, [msgs, sending, sessionKey, scrollToBottom]);

	// Track if user is near bottom whenever they scroll
	useEffect(() => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;

		const handleScroll = () => {
                        if (Date.now() < ignoreScrollEventsUntilRef.current) {
                                return;
                        }
			const { scrollTop, scrollHeight, clientHeight } = scrollArea;
                        const isNearBottom = scrollTop + clientHeight >= scrollHeight - 50;
                        wasNearBottomRef.current = isNearBottom;
                        followBottomRef.current = isNearBottom;
		};

		scrollArea.addEventListener('scroll', handleScroll);
		return () => scrollArea.removeEventListener('scroll', handleScroll);
	}, []);

	useEffect(() => {
		if (!scrollTargetMessageId) return;
		const node = messageRefs.current.get(scrollTargetMessageId);
		if (!node) return;
		node.scrollIntoView({ behavior: 'smooth', block: 'start' });
		wasNearBottomRef.current = false;
                followBottomRef.current = false;
                ignoreScrollEventsUntilRef.current = Date.now() + 400;
		onScrollTargetHandled?.();
	}, [scrollTargetMessageId, onScrollTargetHandled, msgs]);

	useEffect(() => {
		if (!scrollViewportCommand) return;
		if (scrollViewportCommand.type === 'top') {
			wasNearBottomRef.current = false;
                        followBottomRef.current = false;
                        const scrollArea = scrollAreaRef.current;
                        if (!scrollArea) return;
                        ignoreScrollEventsUntilRef.current = Date.now() + 400;
                        scrollArea.scrollTo({ top: 0, behavior: 'smooth' });
			return;
		}
		wasNearBottomRef.current = true;
                followBottomRef.current = true;
                scrollToBottom('auto');
        }, [scrollViewportCommand, scrollToBottom]);

	return (
		<div className={cn('flex flex-col h-full w-full items-center p-2 gap-4', className)}>
			<div
				ref={scrollAreaRef}
				className="flex-1 w-full max-w-full overflow-auto no-scrollbar overflow-x-hidden"
			>
				<div className="flex flex-col gap-4 size-full max-w-full">
					{msgs.length > 0 ? (
						msgs.map((message) => (
							<MessageBubble
								key={message.id}
								message={message}
                                                                agentId={agentId}
                                                                sessionId={sessionId}
								onUserConfirm={onUserConfirm}
                                                                onRollback={onRollbackMessage}
                                                                rollbacking={rollbackingMessageId === message.id}
								containerRef={registerMessageRef(message.id)}
								highlighted={activeMessageId === message.id}
							/>
						))
					) : (
						<EmptyMessage />
					)}
				</div>
			</div>
			<TextInput
				className="min-w-full max-w-full w-full"
				onSend={onSend}
				onStop={onStop}
				focusKey={sessionKey}
				sending={stoppable}
                                stopDisabled={stopDisabled}
				disabled={disabled}
				autoComplete={autoComplete}
				allowedInputTypes={allowedInputTypes}
				fileProcessor={fileProcessor}
                                value={value}
                                onValueChange={onValueChange}
                                files={files}
                                onFilesChange={onFilesChange}
			/>
		</div>
	);
};

export const ChatContent = React.memo(ChatContentComponent);
