import { client } from './client';
import type {
	AgentEvent,
	CreateSessionRequest,
	CreateSessionResponse,
        InterruptSessionResponse,
        RollbackSessionRequest,
        RollbackSessionResponse,
	SessionListResponse,
        SessionExportOptions,
        SessionExportResponse,
        SessionView,
        SessionWithState,
	UpdateSessionRequest,
	Msg,
} from './types';

export type StreamAgentEvent = AgentEvent & { _entry_id?: string };

export interface MessagesResponse {
	messages: Msg[];
	is_running: boolean;
}

export const sessionApi = {
	list: (agentId: string) => client.get<SessionListResponse>('/sessions/', { agent_id: agentId }),

        getView: (sessionId: string, agentId: string) =>
                client.get<SessionView>(`/sessions/${sessionId}/view`, { agent_id: agentId }),

	create: (body: CreateSessionRequest) => client.post<CreateSessionResponse>('/sessions/', body),

	update: (sessionId: string, agentId: string, body: UpdateSessionRequest) =>
                client.patch<SessionWithState>(`/sessions/${sessionId}`, body, { agent_id: agentId }),

	delete: (sessionId: string, agentId: string) =>
		client.delete(`/sessions/${sessionId}`, { agent_id: agentId }),

        interrupt: (sessionId: string, agentId: string) =>
                client.post<InterruptSessionResponse>(`/sessions/${sessionId}/interrupt`, undefined, {
			agent_id: agentId,
		}),

        rollback: (
                sessionId: string,
                agentId: string,
                body: RollbackSessionRequest,
        ) =>
                client.post<RollbackSessionResponse>(`/sessions/${sessionId}/rollback`, body, {
                        agent_id: agentId,
                }),

	messages: (sessionId: string, agentId: string, offset = 0, limit = 50) =>
		client.get<MessagesResponse>(`/sessions/${sessionId}/messages`, {
			agent_id: agentId,
			offset: String(offset),
			limit: String(limit),
		}),

        exportSession: (
                sessionId: string,
                agentId: string,
                options: SessionExportOptions,
        ) =>
                client.get<SessionExportResponse>(`/sessions/${sessionId}/export`, {
                        agent_id: agentId,
                        include_system_messages: String(options.include_system_messages),
                        include_tool_schemas: String(options.include_tool_schemas),
                        truncate_tool_call_input: String(options.truncate_tool_call_input),
                        tool_call_input_max_length: String(options.tool_call_input_max_length),
                        truncate_tool_result: String(options.truncate_tool_result),
                        tool_result_max_length: String(options.tool_result_max_length),
                }),

	/**
	 * Subscribe to a session's live event stream via SSE.
	 *
	 * Opens a long-lived ``GET /sessions/{sid}/stream`` connection and
	 * yields each ``AgentEvent`` as it arrives. The connection stays
	 * open until the caller aborts via the ``signal`` or closes the
	 * generator.
	 *
	 * Uses fetch-based SSE (not native ``EventSource``) so the
	 * ``X-User-ID`` custom header is sent.
	 *
	 * @param sessionId - The session to subscribe to.
	 * @param agentId - The agent that owns the session.
	 * @param signal - Optional abort signal to close the connection.
	 * @returns An async generator yielding ``AgentEvent`` objects.
	 */
	streamEvents: async function* (
		sessionId: string,
		agentId: string,
		replayAfter?: string | null,
		onOpen?: () => void,
		signal?: AbortSignal,
	): AsyncGenerator<StreamAgentEvent> {
		const res = await client.stream(`/sessions/${sessionId}/stream`, {
			method: 'GET',
			params: {
				agent_id: agentId,
				...(replayAfter ? { replay_after: replayAfter } : {}),
			},
			signal,
		});
		onOpen?.();

		const reader = res.body!.getReader();
		const decoder = new TextDecoder();
		let buffer = '';

		try {
			while (true) {
				const { done, value } = await reader.read();
				if (done) break;

				buffer += decoder.decode(value, { stream: true });
				const lines = buffer.split('\n');
				buffer = lines.pop() ?? '';

				for (const line of lines) {
					if (line.startsWith('data: ')) {
						const json = line.slice(6).trim();
						if (json) yield JSON.parse(json) as StreamAgentEvent;
					}
					// SSE comment frames (`:...\n`) are silently skipped
					// (used for heartbeats).
				}
			}
		} finally {
			reader.releaseLock();
		}
	},
};
