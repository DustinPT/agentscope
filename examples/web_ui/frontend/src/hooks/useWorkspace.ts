import { useState, useEffect, useCallback } from 'react';

import { workspaceApi } from '@/api';
import type { MCPClientStatus, ProjectDirectoryEntry, Skill } from '@/api';

export function useWorkspace(
	agentId: string | null,
	sessionId: string | null,
) {
	const [mcps, setMcps] = useState<MCPClientStatus[]>([]);
	const [skills, setSkills] = useState<Skill[]>([]);
	const [loading, setLoading] = useState(false);
	const [skillsLoading, setSkillsLoading] = useState(false);
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

        const listProjectDirectory = useCallback(
                async (path = ''): Promise<ProjectDirectoryEntry[]> => {
                        if (!agentId || !sessionId) {
                                return [];
                        }
                        return workspaceApi.projectDirectory.list(agentId, sessionId, path);
                },
                [agentId, sessionId],
        );

        const buildProjectDirectoryDownloadUrl = useCallback(
                (path = ''): string | null => {
                        if (!agentId || !sessionId) {
                                return null;
                        }
                        return workspaceApi.projectDirectory.buildDownloadUrl(agentId, sessionId, path);
                },
                [agentId, sessionId],
        );

        const buildProjectDirectoryPreviewUrl = useCallback(
                (path: string): string | null => {
                        if (!agentId || !sessionId) {
                                return null;
                        }
                        return workspaceApi.projectDirectory.buildPreviewUrl(agentId, sessionId, path);
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
		error,
		refetch,
		skills,
		skillsLoading,
                refetchSkills,
                listProjectDirectory,
                buildProjectDirectoryDownloadUrl,
                buildProjectDirectoryPreviewUrl,
	};
}
