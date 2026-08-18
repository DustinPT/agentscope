import { useCallback, useEffect, useState } from 'react';

import { credentialApi, modelApi } from '@/api';
import type { CredentialRecord, ModelCard } from '@/api';

export interface CredentialWithModels {
	credential: CredentialRecord;
	models: ModelCard[];
}

interface AvailableModelsSnapshot {
        groups: Record<string, CredentialWithModels[]>;
        loading: boolean;
        error: Error | null;
}

let cachedGroups: Record<string, CredentialWithModels[]> | null = null;
let cachedError: Error | null = null;
let inflight: Promise<Record<string, CredentialWithModels[]>> | null = null;
const listeners = new Set<(snapshot: AvailableModelsSnapshot) => void>();

function getSnapshot(): AvailableModelsSnapshot {
        return {
                groups: cachedGroups ?? {},
                loading: inflight !== null,
                error: cachedError,
        };
}

function emitSnapshot() {
        const snapshot = getSnapshot();
        for (const listener of listeners) {
                listener(snapshot);
        }
}

async function fetchAvailableModelsFromApi() {
        const { credentials } = await credentialApi.list();
        const result: Record<string, CredentialWithModels[]> = {};

        await Promise.all(
                credentials.map(async (credential) => {
                        const type = credential.data.type as string | undefined;
                        if (!type) return;
                        if (!result[type]) result[type] = [];
                        try {
                                const { models } = await modelApi.list(type);
                                result[type].push({ credential, models });
                        } catch {
                                result[type].push({ credential, models: [] });
                        }
                }),
        );

        return result;
}

async function loadAvailableModels(force = false) {
        if (!force && cachedGroups) {
                return cachedGroups;
        }
        if (!force && inflight) {
                return inflight;
        }

        cachedError = null;
        inflight = fetchAvailableModelsFromApi()
                .then((result) => {
                        cachedGroups = result;
                        cachedError = null;
                        return result;
                })
                .catch((error: Error) => {
                        cachedError = error;
                        throw error;
                })
                .finally(() => {
                        inflight = null;
                        emitSnapshot();
                });
        emitSnapshot();
        return inflight;
}

/**
 * Fetches all credentials and their available models, grouped by
 * provider type, and shares the result across all hook consumers.
 *
 * Provider type is read from `credential.data.type`. Credentials
 * without a `type` field or whose model fetch fails are silently
 * skipped.
 */
export function useAvailableModels() {
        const [snapshot, setSnapshot] = useState<AvailableModelsSnapshot>(() => getSnapshot());

	const refetch = useCallback(async () => {
		try {
                        await loadAvailableModels(true);
		} catch (e) {
                        setSnapshot((prev) => ({ ...prev, error: e as Error }));
		}
	}, []);

	useEffect(() => {
                listeners.add(setSnapshot);
                setSnapshot(getSnapshot());
                if (!cachedGroups && !inflight) {
                        void loadAvailableModels();
                }
                return () => {
                        listeners.delete(setSnapshot);
                };
        }, []);

        return { ...snapshot, refetch };
}
