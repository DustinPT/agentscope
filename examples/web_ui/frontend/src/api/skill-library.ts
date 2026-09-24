import { client, getBaseUrl, getUserId } from './client';
import type { SkillLibraryListResponse, SkillLibraryRecord, SkillLibrarySearchResponse, WorkspaceFileEntry } from './types';

export const skillLibraryApi = {
        list: (params?: { keyword?: string; limit?: number; offset?: number }) =>
                client.get<SkillLibraryListResponse>('/skill-library/', {
                        ...(params?.keyword ? { keyword: params.keyword } : {}),
                        ...(params?.limit !== undefined ? { limit: String(params.limit) } : {}),
                        ...(params?.offset !== undefined ? { offset: String(params.offset) } : {}),
                }),

        search: (params: { query: string; limit?: number; min_score?: number }) =>
                client.get<SkillLibrarySearchResponse>('/skill-library/search', {
                        query: params.query,
                        ...(params?.limit !== undefined ? { limit: String(params.limit) } : {}),
                        ...(params?.min_score !== undefined ? { min_score: String(params.min_score) } : {}),
                }),

        upload: (body: FormData) => client.postForm<SkillLibraryRecord>('/skill-library/', body),

        get: (skillName: string) =>
                client.get<SkillLibraryRecord>(`/skill-library/${encodeURIComponent(skillName)}`),

        delete: (skillName: string) => client.delete(`/skill-library/${encodeURIComponent(skillName)}`),

        listFiles: (skillName: string, path = '') =>
                client.get<WorkspaceFileEntry[]>(`/skill-library/${encodeURIComponent(skillName)}/files`, path ? { path } : undefined),

        downloadArchive: (skillName: string) =>
                client.getBlob(`/skill-library/${encodeURIComponent(skillName)}/download`),

        buildFilePreviewUrl: (skillName: string, path: string) => {
                const baseUrl = getBaseUrl();
                const userId = getUserId();
                if (!baseUrl || !userId) return null;
                const url = new URL(`/skill-library/${encodeURIComponent(skillName)}/files/raw`, baseUrl);
                url.searchParams.set('path', path);
                url.searchParams.set('user_id', userId);
                return url.toString();
        },

        buildFileTextPreviewUrl: (skillName: string, path: string) => {
                const baseUrl = getBaseUrl();
                const userId = getUserId();
                if (!baseUrl || !userId) return null;
                const url = new URL(`/skill-library/${encodeURIComponent(skillName)}/files/content`, baseUrl);
                url.searchParams.set('path', path);
                url.searchParams.set('user_id', userId);
                return url.toString();
        },

        buildFileDownloadUrl: (skillName: string, path?: string) => {
                const baseUrl = getBaseUrl();
                const userId = getUserId();
                if (!baseUrl || !userId) return null;
                const url = new URL(
                        path
                                ? `/skill-library/${encodeURIComponent(skillName)}/files/download`
                                : `/skill-library/${encodeURIComponent(skillName)}/download`,
                        baseUrl,
                );
                if (path) {
                        url.searchParams.set('path', path);
                }
                url.searchParams.set('user_id', userId);
                return url.toString();
        },
};
