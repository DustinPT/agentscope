import { useCallback, useEffect, useState } from 'react';

import { sessionApi } from '../api';
import type { SessionView } from '../api';

interface SessionViewSnapshot {
        sessionView: SessionView | null;
        loading: boolean;
        error: Error | null;
}

export function useSessionView(agentId: string | null, sessionId: string | null) {
        const [snapshot, setSnapshot] = useState<SessionViewSnapshot>({
                sessionView: null,
                loading: false,
                error: null,
        });

        const refetch = useCallback(async () => {
                if (!agentId || !sessionId) {
                        setSnapshot({ sessionView: null, loading: false, error: null });
                        return null;
                }
                setSnapshot((prev) => ({ ...prev, loading: true, error: null }));
                try {
                        const sessionView = await sessionApi.getView(sessionId, agentId);
                        setSnapshot({ sessionView, loading: false, error: null });
                        return sessionView;
                } catch (error) {
                        setSnapshot({
                                sessionView: null,
                                loading: false,
                                error: error as Error,
                        });
                        throw error;
                }
        }, [agentId, sessionId]);

        useEffect(() => {
            void refetch();
        }, [refetch]);

        return { ...snapshot, refetch };
}
