import { useCallback, useEffect, useState } from 'react';

import { userApi } from '@/api';
import type { UserModelDefaults } from '@/api';

export function useUserModelDefaults() {
        const [data, setData] = useState<UserModelDefaults | null>(null);
        const [loading, setLoading] = useState(false);
        const [saving, setSaving] = useState(false);
        const [error, setError] = useState<Error | null>(null);

        const refetch = useCallback(async () => {
                setLoading(true);
                setError(null);
                try {
                        const res = await userApi.getModelDefaults();
                        setData(res);
                        return res;
                } catch (e) {
                        setError(e as Error);
                        return null;
                } finally {
                        setLoading(false);
                }
        }, []);

        const save = useCallback(async (body: UserModelDefaults) => {
                setSaving(true);
                setError(null);
                try {
                        const res = await userApi.updateModelDefaults(body);
                        setData(res);
                        return res;
                } catch (e) {
                        setError(e as Error);
                        return null;
                } finally {
                        setSaving(false);
                }
        }, []);

        useEffect(() => {
                void refetch();
        }, [refetch]);

        return { data, loading, saving, error, refetch, save };
}
