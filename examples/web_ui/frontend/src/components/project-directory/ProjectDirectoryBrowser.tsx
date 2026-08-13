import {
        ChevronRight,
        Download,
        Eye,
        File,
        Folder,
        FolderOpen,
        RefreshCw,
} from 'lucide-react';
import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';

import { ProjectFilePreviewDialog } from './ProjectFilePreviewDialog';
import type { WorkspaceFileEntry } from '@/api';
import { Button } from '@/components/ui/button';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';
import { triggerBrowserDownload } from '@/utils/download';

interface ProjectDirectoryBrowserProps {
        initialPath?: string;
        listWorkspaceFiles: (path?: string) => Promise<WorkspaceFileEntry[]>;
        buildWorkspaceFileDownloadUrl: (path?: string) => string | null;
        buildWorkspaceFilePreviewUrl: (path: string) => string | null;
        showDescription?: boolean;
}

function formatBytes(value?: number | null): string {
        if (value === undefined || value === null) {
                return '-';
        }
        if (value < 1024) {
                return `${value} B`;
        }
        const units = ['KB', 'MB', 'GB', 'TB'];
        let size = value;
        let unitIndex = -1;
        while (size >= 1024 && unitIndex < units.length - 1) {
                size /= 1024;
                unitIndex += 1;
        }
        const digits = size >= 10 ? 1 : 2;
        return `${size.toFixed(digits).replace(/\.0+$/, '')} ${units[unitIndex]}`;
}

function formatMtime(value?: number | null): string {
        if (value === undefined || value === null) {
                return '-';
        }
        return new Date(value * 1000).toLocaleString();
}

export function ProjectDirectoryBrowser({
        initialPath = '',
        listWorkspaceFiles,
        buildWorkspaceFileDownloadUrl,
        buildWorkspaceFilePreviewUrl,
        showDescription = true,
}: ProjectDirectoryBrowserProps) {
        const { t } = useTranslation();
        const [rootEntries, setRootEntries] = useState<WorkspaceFileEntry[]>([]);
        const [rootLoading, setRootLoading] = useState(false);
        const [rootError, setRootError] = useState<string | null>(null);
        const [expandedPaths, setExpandedPaths] = useState<Record<string, boolean>>({});
        const [loadingPaths, setLoadingPaths] = useState<Record<string, boolean>>({});
        const [childrenByPath, setChildrenByPath] = useState<Record<string, WorkspaceFileEntry[]>>({});
        const [errorsByPath, setErrorsByPath] = useState<Record<string, string | null>>({});
        const [previewEntry, setPreviewEntry] = useState<WorkspaceFileEntry | null>(null);

        const rootDownloadUrl = useMemo(
                () => buildWorkspaceFileDownloadUrl(initialPath),
                [buildWorkspaceFileDownloadUrl, initialPath],
        );

        const isPreviewable = useCallback((entry: WorkspaceFileEntry): boolean => {
                if (entry.is_dir || !entry.mime_type) {
                        return false;
                }
                return (
                        entry.mime_type.startsWith('image/') ||
                        entry.mime_type.startsWith('text/') ||
                        entry.mime_type === 'application/json' ||
                        entry.mime_type.endsWith('+json')
                );
        }, []);

        const handleDownload = useCallback(
                (path = initialPath) => {
                        const url = buildWorkspaceFileDownloadUrl(path);
                        if (!url) return;
                        triggerBrowserDownload(url);
                },
                [buildWorkspaceFileDownloadUrl, initialPath],
        );

        const loadRoot = useCallback(async () => {
                setRootLoading(true);
                setRootError(null);
                try {
                        setRootEntries(await listWorkspaceFiles(initialPath));
                } catch (error) {
                        setRootError((error as Error).message);
                        setRootEntries([]);
                } finally {
                        setRootLoading(false);
                }
        }, [initialPath, listWorkspaceFiles]);

        useEffect(() => {
                setExpandedPaths({});
                setLoadingPaths({});
                setChildrenByPath({});
                setErrorsByPath({});
                void loadRoot();
        }, [loadRoot]);

        const handleRefresh = useCallback(async () => {
                setExpandedPaths({});
                setLoadingPaths({});
                setChildrenByPath({});
                setErrorsByPath({});
                await loadRoot();
        }, [loadRoot]);

        const handleToggleDirectory = useCallback(
                async (entry: WorkspaceFileEntry) => {
                        if (expandedPaths[entry.path]) {
                                setExpandedPaths((prev) => ({ ...prev, [entry.path]: false }));
                                return;
                        }

                        setExpandedPaths((prev) => ({ ...prev, [entry.path]: true }));
                        if (childrenByPath[entry.path] || loadingPaths[entry.path]) {
                                return;
                        }

                        setLoadingPaths((prev) => ({ ...prev, [entry.path]: true }));
                        setErrorsByPath((prev) => ({ ...prev, [entry.path]: null }));
                        try {
                                const children = await listWorkspaceFiles(entry.path);
                                setChildrenByPath((prev) => ({ ...prev, [entry.path]: children }));
                        } catch (error) {
                                setErrorsByPath((prev) => ({
                                        ...prev,
                                        [entry.path]: (error as Error).message,
                                }));
                        } finally {
                                setLoadingPaths((prev) => ({ ...prev, [entry.path]: false }));
                        }
                },
                [childrenByPath, expandedPaths, listWorkspaceFiles, loadingPaths],
        );

        const renderEntries = useCallback(
                (entries: WorkspaceFileEntry[], depth: number): ReactNode =>
                        entries.map((entry) => {
                                const expanded = !!expandedPaths[entry.path];
                                const childEntries = childrenByPath[entry.path] ?? [];
                                const childError = errorsByPath[entry.path];
                                const isLoadingChildren = !!loadingPaths[entry.path];

                                return (
                                        <div key={entry.path}>
                                                <div
                                                        className="flex items-center gap-2 rounded-md border px-2 py-2 text-sm"
                                                        style={{ paddingLeft: `${depth * 16 + 8}px` }}
                                                >
                                                        {entry.is_dir ? (
                                                                <Button
                                                                        size="icon-xs"
                                                                        variant="ghost"
                                                                        className="shrink-0"
                                                                        onClick={() => void handleToggleDirectory(entry)}
                                                                >
                                                                        <ChevronRight
                                                                                className={cn(
                                                                                        'transition-transform',
                                                                                        expanded && 'rotate-90',
                                                                                )}
                                                                        />
                                                                </Button>
                                                        ) : (
                                                                <span className="inline-flex size-6 shrink-0" />
                                                        )}
                                                        <span className="shrink-0 text-muted-foreground">
                                                                {entry.is_dir ? (
                                                                        expanded ? (
                                                                                <FolderOpen className="size-4" />
                                                                        ) : (
                                                                                <Folder className="size-4" />
                                                                        )
                                                                ) : (
                                                                        <File className="size-4" />
                                                                )}
                                                        </span>
                                                        <div className="min-w-0 flex-1">
                                                                <div className="truncate font-medium">{entry.name}</div>
                                                                <div className="text-xs text-muted-foreground">
                                                                        {entry.is_dir
                                                                                ? t('workspace-drawer.file.directoryMeta', {
                                                                                          updatedAt: formatMtime(entry.mtime),
                                                                                  })
                                                                                : t('workspace-drawer.file.fileMeta', {
                                                                                          size: formatBytes(entry.size_bytes),
                                                                                          updatedAt: formatMtime(entry.mtime),
                                                                                  })}
                                                                </div>
                                                        </div>
                                                        <Button
                                                                size="icon-xs"
                                                                variant="ghost"
                                                                tooltip={t('workspace-drawer.file.preview')}
                                                                onClick={() => setPreviewEntry(entry)}
                                                                disabled={!isPreviewable(entry)}
                                                                className={cn(!isPreviewable(entry) && 'invisible')}
                                                        >
                                                                <Eye />
                                                        </Button>
                                                        <Button
                                                                size="icon-xs"
                                                                variant="ghost"
                                                                tooltip={
                                                                        entry.is_dir
                                                                                ? t('workspace-drawer.file.downloadDirectory')
                                                                                : t('workspace-drawer.file.downloadFile')
                                                                }
                                                                onClick={() => handleDownload(entry.path)}
                                                        >
                                                                <Download />
                                                        </Button>
                                                </div>
                                                {entry.is_dir && expanded ? (
                                                        <div className="mt-1 space-y-1">
                                                                {isLoadingChildren ? (
                                                                        <div
                                                                                className="px-3 py-2 text-xs text-muted-foreground"
                                                                                style={{
                                                                                        paddingLeft: `${(depth + 1) * 16 + 8}px`,
                                                                                }}
                                                                        >
                                                                                {t('common.loading')}
                                                                        </div>
                                                                ) : childError ? (
                                                                        <div
                                                                                className="px-3 py-2 text-xs text-destructive"
                                                                                style={{
                                                                                        paddingLeft: `${(depth + 1) * 16 + 8}px`,
                                                                                }}
                                                                        >
                                                                                {t('workspace-drawer.file.loadFailed')}
                                                                        </div>
                                                                ) : childEntries.length === 0 ? (
                                                                        <div
                                                                                className="px-3 py-2 text-xs text-muted-foreground"
                                                                                style={{
                                                                                        paddingLeft: `${(depth + 1) * 16 + 8}px`,
                                                                                }}
                                                                        >
                                                                                {t('workspace-drawer.file.emptyDirectory')}
                                                                        </div>
                                                                ) : (
                                                                        renderEntries(childEntries, depth + 1)
                                                                )}
                                                        </div>
                                                ) : null}
                                        </div>
                                );
                        }),
                [
                        childrenByPath,
                        errorsByPath,
                        expandedPaths,
                        handleDownload,
                        handleToggleDirectory,
                        isPreviewable,
                        loadingPaths,
                        t,
                ],
        );

        return (
                <div className="flex flex-col no-scrollbar overflow-y-auto gap-y-3">
                        {showDescription ? (
                                <span className="text-muted-foreground text-sm">
                                        {t('workspace-drawer.file.description')}
                                </span>
                        ) : null}
                        <div className="flex items-center gap-2">
                                <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() => void handleRefresh()}
                                        disabled={rootLoading}
                                >
                                        <RefreshCw className="size-4" />
                                        {t('workspace-drawer.file.refresh')}
                                </Button>
                                <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() => handleDownload(initialPath)}
                                        disabled={!rootDownloadUrl}
                                >
                                        <Download className="size-4" />
                                        {initialPath
                                                ? t('workspace-drawer.file.downloadDirectory')
                                                : t('workspace-drawer.file.downloadWorkspace')}
                                </Button>
                        </div>
                        {rootLoading ? (
                                <p className="py-4 text-center text-sm text-muted-foreground">{t('common.loading')}</p>
                        ) : rootError ? (
                                <p className="py-4 text-center text-sm text-destructive">
                                        {t('workspace-drawer.file.loadFailed')}
                                </p>
                        ) : rootEntries.length === 0 ? (
                                <p className="py-4 text-center text-sm text-muted-foreground">
                                        {initialPath
                                                ? t('workspace-drawer.file.emptyDirectory')
                                                : t('workspace-drawer.file.emptyRoot')}
                                </p>
                        ) : (
                                <div className="space-y-1">{renderEntries(rootEntries, 0)}</div>
                        )}
                        <ProjectFilePreviewDialog
                                open={previewEntry !== null}
                                onOpenChange={(open) => {
                                        if (!open) {
                                                setPreviewEntry(null);
                                        }
                                }}
                                entry={previewEntry}
                                buildWorkspaceFilePreviewUrl={buildWorkspaceFilePreviewUrl}
                                buildWorkspaceFileDownloadUrl={buildWorkspaceFileDownloadUrl}
                        />
                </div>
        );
}
