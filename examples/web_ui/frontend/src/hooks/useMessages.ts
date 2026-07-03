import { EventType } from '@agentscope-ai/agentscope/event';
import type {
	AgentEvent,
	CustomEvent,
	DataBlockStartEvent,
	DataBlockDeltaEvent,
	DataBlockEndEvent,
	ReplyEndEvent,
	ReplyStartEvent,
	UserConfirmResultEvent,
} from '@agentscope-ai/agentscope/event';
import { appendEvent, AssistantMsg, UserMsg } from '@agentscope-ai/agentscope/message';
import type { Msg, ContentBlock } from '@agentscope-ai/agentscope/message';
import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { useState, useCallback, useRef, useEffect } from 'react';

import { sessionApi } from '@/api';
import { chatApi } from '@/api';
import type { StreamAgentEvent } from '@/api/session';
import { useAudioManager } from '@/context/AudioContext';

type JsonLike =
	| string
	| number
	| boolean
	| null
	| JsonLike[]
	| { [key: string]: JsonLike };

const REPLY_CHECKPOINT_REPLAY_ENTRY_ID_METADATA_KEY = 'checkpoint_replay_entry_id';
/** Metadata key marking the replay entry already folded into a persisted reply. */

function compareReplayEntryId(a: string, b: string): number {
	const [aMsRaw = '0', aSeqRaw = '0'] = a.split('-');
	const [bMsRaw = '0', bSeqRaw = '0'] = b.split('-');
	const aMs = Number(aMsRaw);
	const bMs = Number(bMsRaw);
	if (aMs !== bMs) return aMs - bMs;
	return Number(aSeqRaw) - Number(bSeqRaw);
}

/**
 * Manages messages for a single ``(agentId, sessionId)`` pair.
 *
 * Event delivery has two independent channels:
 *
 * - **History** — ``GET /sessions/{sid}/messages`` fetches persisted
 *   ``Msg`` objects (each a complete reply).
 * - **Live stream** — ``GET /sessions/{sid}/stream`` is a long-lived
 *   SSE connection that pushes ``AgentEvent`` deltas as they are
 *   produced by any chat run on this session (user-triggered,
 *   background retrigger, team member message, …).
 *
 * The hook opens the SSE connection first, buffers any incoming
 * events, then fetches history and applies only the buffered tail that
 * sits strictly after the last persisted reply checkpoint. User input
 * and human-in-the-loop confirmations are sent via ``POST /chat/``
 * (fire-and-forget); the resulting events arrive through the same SSE
 * connection.
 *
 * ``streaming`` is driven by event content, not HTTP lifecycle:
 * ``true`` after receiving ``ReplyStartEvent``, ``false`` after
 * ``ReplyEndEvent``.
 *
 * @param agentId - The agent whose session to subscribe. ``null`` to
 *   skip.
 * @param sessionId - The session to subscribe. ``null`` to skip.
 * @returns Object with ``msgs``, ``loading``, ``streaming``, ``error``,
 *   ``send``, ``onUserConfirm``, ``cancelCurrentRun``, and ``abort``.
 */
export function useMessages(
	agentId: string | null,
	sessionId: string | null,
	options?: {
		/**
		 * Called when a ``CUSTOM`` event with ``name="team_updated"``
		 * arrives — the team membership has changed (TeamCreate /
		 * AgentCreate / TeamDelete ran). The typical response is to
		 * refetch the session list so the team sidebar updates.
		 */
		onTeamUpdated?: () => void;
		/**
		 * Called when a ``CUSTOM`` event with ``name="state_updated"``
		 * arrives — agent state (tasks / permission) changed during a
		 * tool call. The ``value`` payload contains the latest
		 * ``tasks_context`` and ``permission_context``.
		 */
		onStateUpdated?: (value: Record<string, unknown>) => void;
	},
) {
	const isAwaitingToolInteraction = useCallback((message: Msg | null | undefined) => {
		if (!message || message.role !== 'assistant') return false;
		return message.content.some(
			(block) =>
				block.type === 'tool_call' &&
				(block.state === 'asking' ||
					block.state === 'submitted' ||
					block.state === 'pending' ||
					block.state === 'allowed'),
		);
	}, []);

	const [msgs, setMsgs] = useState<Msg[]>([]);
	const [loading, setLoading] = useState(false);
	const [streaming, setStreaming] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const msgsRef = useRef<Msg[]>([]);
	const currentReplyRef = useRef<Msg | null>(null);
	const abortRef = useRef<AbortController | null>(null);
	const rafRef = useRef<number | null>(null);
	const pendingEventsRef = useRef<StreamAgentEvent[]>([]);

	const audioManager = useAudioManager();

	const optionsRef = useRef(options);
	useEffect(() => {
		optionsRef.current = options;
	}, [options]);

	const mergeReplyMetadata = useCallback((msg: Msg, event: AgentEvent) => {
		if (event.type !== EventType.REPLY_END) return;
		const replyEndEvent = event as ReplyEndEvent;
		const contextUsage = replyEndEvent.metadata?.context_usage;
		if (!contextUsage || typeof contextUsage !== 'object') return;
		msg.metadata = {
			...msg.metadata,
			context_usage: contextUsage as JsonLike,
		};
	}, []);
	const getHistoryReplayBoundary = useCallback((messages: Msg[]): string | null => {
		for (let i = messages.length - 1; i >= 0; i -= 1) {
			const entryId = messages[i]?.metadata?.[REPLY_CHECKPOINT_REPLAY_ENTRY_ID_METADATA_KEY];
			if (typeof entryId === 'string' && entryId.length > 0) {
				return entryId;
			}
		}
		return null;
	}, []);
	const scheduleUpdate = useCallback(() => {
		if (rafRef.current !== null) return;
		rafRef.current = requestAnimationFrame(() => {
			rafRef.current = null;
			setMsgs([...msgsRef.current]);
		});
	}, []);

	/**
	 * Resolve the reply targeted by an incoming event.
	 *
	 * Most streaming events carry ``reply_id``. Re-resolving the reply from
	 * ``msgsRef`` keeps duplicated subscriptions to the same session in sync
	 * even when only one of them initiated the continuation (for example, a
	 * human-in-the-loop confirmation from another expanded card).
	 */
	const resolveReplyForEvent = useCallback((event: AgentEvent) => {
		if (!('reply_id' in event)) return null;
		if (currentReplyRef.current?.id === event.reply_id) {
			return currentReplyRef.current;
		}
		const target = msgsRef.current.find((msg) => msg.id === event.reply_id) ?? null;
		if (target) {
			currentReplyRef.current = target;
		}
		return target;
	}, []);

	/**
	 * Keep locally reconstructed tool-call state aligned with the backend's
	 * event-to-message semantics.
	 *
	 * Python-side ``Msg.append_event`` treats ``TOOL_RESULT_END`` as the end
	 * of the paired tool-call lifecycle and flips the call to ``finished``.
	 * The frontend mirrors that implicit transition here because SSE does not
	 * emit a dedicated "tool_call finished" event.
	 */
	const reconcileToolCallState = useCallback((msg: Msg, event: AgentEvent) => {
		if (!('reply_id' in event) || event.reply_id !== msg.id) return;

		const hasToolResultEvent =
			event.type === EventType.TOOL_RESULT_START ||
			event.type === EventType.TOOL_RESULT_TEXT_DELTA ||
			event.type === EventType.TOOL_RESULT_DATA_DELTA ||
			event.type === EventType.TOOL_RESULT_END;
		if (!hasToolResultEvent) return;

		const toolCallId = event.tool_call_id;
		const toolCall = msg.content.find(
			(block): block is ToolCallBlock =>
				block.type === 'tool_call' && block.id === toolCallId,
		);
		if (!toolCall) return;

		if (event.type === EventType.TOOL_RESULT_END) {
			toolCall.state = 'finished';
			return;
		}

		if (
			toolCall.state === 'asking' ||
			toolCall.state === 'pending' ||
			toolCall.state === 'submitted'
		) {
			toolCall.state = 'allowed';
		}
	}, []);

	/** Apply a single AgentEvent to the in-progress reply. */
	const processEvent = useCallback(
		(event: AgentEvent | StreamAgentEvent) => {
			// Custom events are service-layer notifications, not agent
			// reply content — route them to callbacks and skip appendEvent.
			if (event.type === EventType.CUSTOM) {
				const custom = event as CustomEvent;
				if (
					custom.name === 'team_updated' ||
					custom.name === 'subagent_sessions_updated'
				) {
					optionsRef.current?.onTeamUpdated?.();
				} else if (custom.name === 'state_updated' && custom.value) {
					optionsRef.current?.onStateUpdated?.(custom.value as Record<string, unknown>);
				}
				return;
			}
			if (event.type === EventType.REPLY_START) {
				audioManager?.stopAllPlayback();
				const e = event as ReplyStartEvent;
				const msg = AssistantMsg({ id: e.reply_id, name: e.name, content: [] });
				msgsRef.current = [...msgsRef.current, msg];
				currentReplyRef.current = msg;
				setStreaming(true);
			} else if (event.type === EventType.REPLY_END) {
				const targetReply = resolveReplyForEvent(event);
				if (targetReply) {
					appendEvent(targetReply, event);
					mergeReplyMetadata(targetReply, event);
					reconcileToolCallState(targetReply, event);
				}
				setStreaming(false);
				if (
					'reply_id' in event &&
					currentReplyRef.current?.id === event.reply_id
				) {
					currentReplyRef.current = null;
				}
			} else {
				const targetReply = resolveReplyForEvent(event);
				if (targetReply) {
					appendEvent(targetReply, event);
					reconcileToolCallState(targetReply, event);
				}
			}

			// Route streaming audio DataBlocks to the audio manager. They still
			// flow through `appendEvent` above (which builds up `source.data`
			// in the Msg), but MessageBubble reads playback state from the
			// manager so it can show progress and autoplay on completion.
			if (audioManager) {
				if (event.type === EventType.DATA_BLOCK_START) {
					const e = event as DataBlockStartEvent;
					if (e.media_type.startsWith('audio/')) {
						audioManager.start(e.block_id, e.media_type);
					}
				} else if (event.type === EventType.DATA_BLOCK_DELTA) {
					const e = event as DataBlockDeltaEvent;
					if (e.media_type.startsWith('audio/')) {
						audioManager.append(e.block_id, e.data);
					}
				} else if (event.type === EventType.DATA_BLOCK_END) {
					const e = event as DataBlockEndEvent;
					// `end` is a no-op when the block isn't being tracked, so
					// we can call it unconditionally.
					audioManager.end(e.block_id);
				}
			}

			scheduleUpdate();
		},
		[
			mergeReplyMetadata,
			scheduleUpdate,
			audioManager,
			resolveReplyForEvent,
			reconcileToolCallState,
		],
	);

	// ── Lifecycle: fetch history + open SSE stream ──────────────────
	useEffect(() => {
		msgsRef.current = [];
		currentReplyRef.current = null;
		pendingEventsRef.current = [];
		setMsgs([]);
		setError(null);
		setStreaming(false);
		audioManager?.disposeAll();

		if (!agentId || !sessionId) return;

		const controller = new AbortController();
		abortRef.current = controller;
		let cancelled = false;

		(async () => {
			try {
				let historyLoaded = false;
				let historyReplayBoundary: string | null = null;
				let streamOpened = false;
				const streamReady = new Promise<void>((resolve, reject) => {
					void (async () => {
						try {
							for await (const event of sessionApi.streamEvents(
								sessionId,
								agentId,
								null,
								() => {
									if (!streamOpened) {
										streamOpened = true;
										resolve();
									}
								},
								controller.signal,
							)) {
								if (cancelled) break;
								if (!historyLoaded) {
									pendingEventsRef.current.push(event);
									continue;
								}
								const entryId = event._entry_id;
								if (
									historyReplayBoundary &&
									typeof entryId === 'string' &&
									compareReplayEntryId(entryId, historyReplayBoundary) <= 0
								) {
									continue;
								}
								processEvent(event);
							}
						} catch (e) {
							if (!streamOpened) {
								streamOpened = true;
								reject(e);
								return;
							}
							if ((e as Error).name !== 'AbortError' && !cancelled) {
								setError(e as Error);
							}
						}
					})();
				});

				// 1. Establish the SSE connection first, then fetch history.
				await streamReady;
				if (cancelled) return;

				setLoading(true);
				const { messages } = await sessionApi.messages(
					sessionId,
					agentId,
				);
				if (cancelled) return;
				msgsRef.current = messages;
				scheduleUpdate();

				historyReplayBoundary = getHistoryReplayBoundary(messages);
				historyLoaded = true;
				const bufferedEvents = pendingEventsRef.current;
				pendingEventsRef.current = [];
				for (const event of bufferedEvents) {
					const entryId = event._entry_id;
					if (
						historyReplayBoundary &&
						typeof entryId === 'string' &&
						compareReplayEntryId(entryId, historyReplayBoundary) <= 0
					) {
						continue;
					}
					processEvent(event);
				}
			} catch (e) {
				if ((e as Error).name !== 'AbortError' && !cancelled) {
					setError(e as Error);
				}
			} finally {
				if (!cancelled) setLoading(false);
			}
		})();

		return () => {
			cancelled = true;
			controller.abort();
			abortRef.current = null;
		};
	}, [
		agentId,
		sessionId,
		scheduleUpdate,
		processEvent,
		audioManager,
		getHistoryReplayBoundary,
	]);

	/**
	 * Send a user message. Appends the message to the local list
	 * optimistically, then fires a ``POST /chat/`` trigger. Events
	 * arrive via the already-open SSE connection.
	 *
	 * @param content - The message content blocks.
	 */
	const send = useCallback(
		async (content: ContentBlock[]) => {
			if (!agentId || !sessionId) return;

			const userMsg = UserMsg({ name: 'user', content });
			msgsRef.current = [...msgsRef.current, userMsg];
			scheduleUpdate();

			try {
				await chatApi.trigger({
					agent_id: agentId,
					session_id: sessionId,
					input: userMsg,
				});
			} catch (e) {
				setError(e as Error);
			}
		},
		[agentId, sessionId, scheduleUpdate],
	);

	/**
	 * Request cancellation of the current backend run while keeping the
	 * session stream subscribed.
	 */
	const cancelCurrentRun = useCallback(async () => {
		if (!agentId || !sessionId) return;
		await sessionApi.cancel(sessionId, agentId);
		audioManager?.stopAllPlayback();
		setStreaming(false);
	}, [agentId, sessionId, audioManager]);

	/**
	 * Confirm or deny a tool call (human-in-the-loop). Fires a
	 * ``POST /chat/`` with a ``UserConfirmResultEvent``; events
	 * arrive via SSE.
	 *
	 * @param toolCall - The tool call block to confirm/deny.
	 * @param confirm - Whether the user confirmed.
	 * @param replyId - The reply id the tool call belongs to.
	 * @param rules - Optional permission rules to attach.
	 */
	const onUserConfirm = useCallback(
		async (
			toolCall: ToolCallBlock,
			confirm: boolean,
			replyId: string,
			rules?: ToolCallBlock['suggested_rules'],
		) => {
			if (!agentId || !sessionId) return;

			// Restore the ref so continuation events (no REPLY_START)
			// have a target.
			currentReplyRef.current = msgsRef.current.find((m) => m.id === replyId) ?? null;

			const event: UserConfirmResultEvent = {
				type: EventType.USER_CONFIRM_RESULT,
				id: crypto.randomUUID(),
				created_at: new Date().toISOString(),
				reply_id: replyId,
				confirm_results: [
					{ confirmed: confirm, tool_call: toolCall, rules: rules ?? null },
				],
			};

			try {
				await chatApi.trigger({
					agent_id: agentId,
					session_id: sessionId,
					input: event,
				});
			} catch (e) {
				setError(e as Error);
			}
		},
		[agentId, sessionId],
	);

	/** Abort the current SSE connection. */
	const abort = useCallback(() => {
		abortRef.current?.abort();
	}, []);

	return {
		msgs,
		loading,
		streaming,
		canStop:
			streaming ||
			isAwaitingToolInteraction(
				[...msgs].reverse().find((msg) => msg.role === 'assistant') ?? null,
			),
		error,
		send,
		onUserConfirm,
		cancelCurrentRun,
		abort,
	};
}
