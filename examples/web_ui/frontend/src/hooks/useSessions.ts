import { useCallback, useEffect, useState } from 'react';

import { sessionApi } from '../api';
import type {
        SessionSummaryView,
        CreateSessionRequest,
        UpdateSessionRequest,
} from '../api';

interface SessionsSnapshot {
        sessions: SessionSummaryView[];
        loading: boolean;
        error: Error | null;
}

interface SessionStore {
        sessions: SessionSummaryView[] | null;
        error: Error | null;
        inflight: Promise<SessionSummaryView[]> | null;
        listeners: Set<(snapshot: SessionsSnapshot) => void>;
}

const sessionStores = new Map<string, SessionStore>();

function getStore(agentId: string): SessionStore {
        const existing = sessionStores.get(agentId);
        if (existing) {
                return existing;
        }
        const created: SessionStore = {
                sessions: null,
                error: null,
                inflight: null,
                listeners: new Set(),
        };
        sessionStores.set(agentId, created);
        return created;
}

function getSnapshot(agentId: string | null): SessionsSnapshot {
        if (!agentId) {
                return {
                        sessions: [],
                        loading: false,
                        error: null,
                };
        }
        const store = getStore(agentId);
        return {
                sessions: store.sessions ?? [],
                loading: store.inflight !== null,
                error: store.error,
        };
}

function emitSnapshot(agentId: string) {
        const snapshot = getSnapshot(agentId);
        for (const listener of getStore(agentId).listeners) {
                listener(snapshot);
        }
}

async function loadSessions(agentId: string, force = false) {
        const store = getStore(agentId);
        if (!force && store.sessions) {
                return store.sessions;
        }
        if (!force && store.inflight) {
                return store.inflight;
        }

        store.error = null;
        store.inflight = sessionApi
                .list(agentId)
                .then((res) => {
                        store.sessions = res.sessions;
                        store.error = null;
                        return res.sessions;
                })
                .catch((error: Error) => {
                        store.error = error;
                        throw error;
                })
                .finally(() => {
                        store.inflight = null;
                        emitSnapshot(agentId);
                });
        emitSnapshot(agentId);
        return store.inflight;
}

/**
 * Manages lightweight session views for a given agent. Concurrent consumers that
 * point at the same agent share the same in-flight fetch and cache.
 *
 * Each entry is a `SessionSummaryView` (record + is_running). The hook
 * clears and re-fetches whenever agentId changes.
 *
 * @param agentId - The agent whose sessions to load. Pass null to skip fetching.
 * @returns Object with the loaded `sessions` array plus `loading` /
 *   `error` flags and `refetch` / `create` / `update` / `remove`
 *   helpers that all keep the local list in sync.
 */
export function useSessions(agentId: string | null) {
        const [snapshot, setSnapshot] = useState<SessionsSnapshot>(() => getSnapshot(agentId));

	const refetch = useCallback(async () => {
		if (!agentId) {
			return;
		}
		try {
                        await loadSessions(agentId, true);
		} catch (e) {
                        setSnapshot((prev) => ({ ...prev, error: e as Error }));
		}
	}, [agentId]);

	useEffect(() => {
                if (!agentId) {
                        setSnapshot(getSnapshot(null));
                        return;
                }
                const store = getStore(agentId);
                store.listeners.add(setSnapshot);
                setSnapshot(getSnapshot(agentId));
                if (!store.sessions && !store.inflight) {
                        void loadSessions(agentId);
                }
                return () => {
                        store.listeners.delete(setSnapshot);
                };
        }, [agentId]);

	/** Creates a new session and refreshes the list. */
	const create = useCallback(
		async (body: CreateSessionRequest) => {
			const res = await sessionApi.create(body);
			await refetch();
			return res;
		},
		[refetch],
	);

	/** Updates a session's model config and refreshes the list. */
	const update = useCallback(
		async (sessionId: string, body: UpdateSessionRequest) => {
			if (!agentId) throw new Error('No agent selected');
			const res = await sessionApi.update(sessionId, agentId, body);
			await refetch();
			return res;
		},
		[agentId, refetch],
	);

	/** Deletes a session and refreshes the list. */
	const remove = useCallback(
		async (sessionId: string) => {
			if (!agentId) throw new Error('No agent selected');
			await sessionApi.delete(sessionId, agentId);
			await refetch();
		},
		[agentId, refetch],
	);

        return { ...snapshot, refetch, create, update, remove };
}
