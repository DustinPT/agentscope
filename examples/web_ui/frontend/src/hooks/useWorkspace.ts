import { useState, useEffect, useCallback } from 'react';

import { workspaceApi } from '@/api';
import type { MCPClientStatus, Skill, WorkspaceFileEntry } from '@/api';

export function useWorkspace(
	agentId: string | null,
	sessionId: string | null,
) {
	const [mcps, setMcps] = useState<MCPClientStatus[]>([]);
	const [skills, setSkills] = useState<Skill[]>([]);
	const [loading, setLoading] = useState(false);
	const [skillsLoading, setSkillsLoading] = useState(false);
        const [reconnectingMcpName, setReconnectingMcpName] = useState<string | null>(null);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		if (!agentId || !sessionId) {
			setMcps([]);
			return;
		}
		setLoading(true);
		setError(null);
		try {
			setMcps(await workspaceApi.mcp.list(agentId, sessionId));
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, [agentId, sessionId]);

	const refetchSkills = useCallback(async () => {
		if (!agentId || !sessionId) {
			setSkills([]);
			return;
		}
		setSkillsLoading(true);
		try {
			setSkills(await workspaceApi.skill.list(agentId, sessionId));
		} catch (e) {
			setError(e as Error);
		} finally {
			setSkillsLoading(false);
		}
	}, [agentId, sessionId]);

        const reconnectMcp = useCallback(
                async (name: string) => {
                        if (!agentId || !sessionId) {
                                return null;
                        }
                        setReconnectingMcpName(name);
                        try {
                                const updated = await workspaceApi.mcp.reconnect(agentId, sessionId, name);
                                await refetch();
                                return updated;
                        } finally {
                                setReconnectingMcpName(null);
                        }
                },
                [agentId, sessionId, refetch],
        );

        const listWorkspaceFiles = useCallback(
                async (path = ''): Promise<WorkspaceFileEntry[]> => {
                        if (!agentId || !sessionId) {
                                return [];
                        }
                        return workspaceApi.files.list(agentId, sessionId, path);
                },
                [agentId, sessionId],
        );

        const buildWorkspaceFileDownloadUrl = useCallback(
                (path = ''): string | null => {
                        if (!agentId || !sessionId) {
                                return null;
                        }
                        return workspaceApi.files.buildDownloadUrl(agentId, sessionId, path);
                },
                [agentId, sessionId],
        );

        const buildWorkspaceFilePreviewUrl = useCallback(
                (path: string): string | null => {
                        if (!agentId || !sessionId) {
                                return null;
                        }
                        return workspaceApi.files.buildPreviewUrl(agentId, sessionId, path);
                },
                [agentId, sessionId],
        );

	useEffect(() => {
		refetch();
	}, [refetch]);
	useEffect(() => {
		refetchSkills();
	}, [refetchSkills]);

	return {
		mcps,
		loading,
                reconnectingMcpName,
		error,
		refetch,
                reconnectMcp,
		skills,
		skillsLoading,
                refetchSkills,
                listWorkspaceFiles,
                buildWorkspaceFileDownloadUrl,
                buildWorkspaceFilePreviewUrl,
	};
}
