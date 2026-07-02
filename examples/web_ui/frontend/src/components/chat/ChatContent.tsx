import type { ContentBlock, Msg, ToolCallBlock } from '@agentscope-ai/agentscope/message';
import React from 'react';
import { useRef, useEffect } from 'react';

import { EmptyMessage } from './Empty';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { TextInput } from '@/components/chat/TextInput.tsx';
import { cn } from '@/lib/utils';

interface ChatContentProps {
	msgs: Msg[];
	sessionKey?: string | null;
	sending: boolean;
	stoppable?: boolean;
	disabled: boolean;
	onSend: (content: ContentBlock[]) => void;
	onStop?: () => void | Promise<void>;
	onUserConfirm: (
		toolCall: ToolCallBlock,
		confirm: boolean,
		replyId: string,
		rules?: ToolCallBlock['suggested_rules'],
	) => void;
	autoComplete?: (input: string) => string | null;
	className?: string;
	/** @see TextInputProps.allowedInputTypes */
	allowedInputTypes: string[];
	/** @see TextInputProps.fileProcessor */
	fileProcessor: (file: File) => Promise<ContentBlock | null>;
}

const ChatContentComponent: React.FC<ChatContentProps> = ({
	msgs,
	sessionKey,
	sending,
	stoppable = false,
	disabled,
	onSend,
	onStop,
	onUserConfirm,
	autoComplete,
	className,
	allowedInputTypes,
	fileProcessor,
}) => {
	const scrollAreaRef = useRef<HTMLDivElement>(null);
	const prevMsgCountRef = useRef<number>(0);
	const wasNearBottomRef = useRef<boolean>(true);
	const pendingInitialScrollRef = useRef<boolean>(true);
	const skipNextScrollCheckRef = useRef<boolean>(false);

	// When the user opens a different session, force exactly one initial
	// jump to the latest message after that session's history is rendered.
	useEffect(() => {
		prevMsgCountRef.current = 0;
		wasNearBottomRef.current = true;
		pendingInitialScrollRef.current = true;
		skipNextScrollCheckRef.current = true;
	}, [sessionKey]);

	// Auto-scroll to bottom only if user is already near the bottom
	useEffect(() => {
		const currentCount = msgs.length;
		const prevCount = prevMsgCountRef.current;

		// On session switch the first render may still contain the previous
		// session's messages before `useMessages` clears and reloads them.
		// Skip exactly one check so the forced initial scroll is consumed only
		// by the newly selected session's content.
		if (skipNextScrollCheckRef.current) {
			skipNextScrollCheckRef.current = false;
			prevMsgCountRef.current = currentCount;
			return;
		}

		const shouldForceInitialScroll =
			pendingInitialScrollRef.current && (currentCount > 0 || sending);

		const shouldCheck =
			shouldForceInitialScroll ||
			(currentCount > prevCount && prevCount > 0) || (sending && prevCount > 0);

		if (shouldCheck && scrollAreaRef.current) {
			const { scrollHeight } = scrollAreaRef.current;

			// Check if user was near bottom before content changed
			const isNearBottom = shouldForceInitialScroll || wasNearBottomRef.current;

			if (isNearBottom) {
				scrollAreaRef.current.scrollTo({
					top: scrollHeight,
					behavior: shouldForceInitialScroll ? 'auto' : 'smooth',
				});
			}
		}

		if (shouldForceInitialScroll) {
			pendingInitialScrollRef.current = false;
		}

		prevMsgCountRef.current = currentCount;
	}, [msgs, sending, sessionKey]);

	// Track if user is near bottom whenever they scroll
	useEffect(() => {
		const scrollArea = scrollAreaRef.current;
		if (!scrollArea) return;

		const handleScroll = () => {
			const { scrollTop, scrollHeight, clientHeight } = scrollArea;
			wasNearBottomRef.current = scrollTop + clientHeight >= scrollHeight - 50;
		};

		scrollArea.addEventListener('scroll', handleScroll);
		return () => scrollArea.removeEventListener('scroll', handleScroll);
	}, []);

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
								onUserConfirm={onUserConfirm}
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
				disabled={disabled}
				autoComplete={autoComplete}
				allowedInputTypes={allowedInputTypes}
				fileProcessor={fileProcessor}
			/>
		</div>
	);
};

export const ChatContent = React.memo(ChatContentComponent);
