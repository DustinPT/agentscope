import { useState, useEffect, useCallback } from 'react';

import { workspaceApi } from '../api';
import type { Skill } from '../api';

/**
 * Manages skills available in a session's workspace.
 * Re-fetches whenever agentId or sessionId changes.
 *
 * @param agentId   - The owning agent. Pass null to skip fetching.
 * @param sessionId - The target session. Pass null to skip fetching.
 */
export function useSkills(agentId: string | null, sessionId: string | null) {
	const [skills, setSkills] = useState<Skill[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<Error | null>(null);

	const refetch = useCallback(async () => {
		if (!agentId || !sessionId) {
			setSkills([]);
			return;
		}
		setLoading(true);
		setError(null);
		try {
			setSkills(await workspaceApi.skill.list(agentId, sessionId));
		} catch (e) {
			setError(e as Error);
		} finally {
			setLoading(false);
		}
	}, [agentId, sessionId]);

	useEffect(() => {
		refetch();
	}, [refetch]);

        return { skills, loading, error, refetch };
}
