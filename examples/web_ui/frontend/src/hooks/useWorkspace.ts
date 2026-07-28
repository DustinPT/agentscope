import { useState, useEffect, useCallback } from 'react';

import { workspaceApi } from '@/api';
import type { MCPClientStatus, Skill } from '@/api';

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
	};
}
