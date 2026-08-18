import { client, getBaseUrl, getUserId } from './client';
import type { MCPClientStatus, Skill, WorkspaceFileEntry } from './types';

export const workspaceApi = {
	mcp: {
		list: (agentId: string, sessionId: string) =>
			client.get<MCPClientStatus[]>('/workspace/mcp', {
				agent_id: agentId,
				session_id: sessionId,
			}),
                reconnect: (agentId: string, sessionId: string, name: string) =>
                        client.post<MCPClientStatus>(`/workspace/mcp/${encodeURIComponent(name)}/reconnect`, undefined, {
                                agent_id: agentId,
                                session_id: sessionId,
                        }),
	},

	skill: {
		list: (agentId: string, sessionId: string) =>
			client.get<Skill[]>('/workspace/skill', { agent_id: agentId, session_id: sessionId }),
	},

        files: {
                list: (agentId: string, sessionId: string, path = '') =>
                        client.get<WorkspaceFileEntry[]>('/workspace/files', {
                                agent_id: agentId,
                                session_id: sessionId,
                                path,
                        }),
                buildDownloadUrl: (agentId: string, sessionId: string, path = '') => {
                        const baseUrl = getBaseUrl() || window.location.origin;
                        const url = new URL('/workspace/files/download', baseUrl);
                        url.searchParams.set('agent_id', agentId);
                        url.searchParams.set('session_id', sessionId);
                        url.searchParams.set('user_id', getUserId());
                        if (path) {
                                url.searchParams.set('path', path);
                        }
                        return url.toString();
                },
                buildPreviewUrl: (agentId: string, sessionId: string, path: string) => {
                        const baseUrl = getBaseUrl() || window.location.origin;
                        const url = new URL('/workspace/files/preview', baseUrl);
                        url.searchParams.set('agent_id', agentId);
                        url.searchParams.set('session_id', sessionId);
                        url.searchParams.set('user_id', getUserId());
                        url.searchParams.set('path', path);
                        return url.toString();
                },
        },
};
