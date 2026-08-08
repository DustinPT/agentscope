import {
        ChevronRight,
        Copy,
        Download,
        Eye,
        File,
        Folder,
        FolderOpen,
        RefreshCw,
} from 'lucide-react';
import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { toast } from 'sonner';

import type { ProjectDirectoryEntry } from '@/api';
import { Button } from '@/components/ui/button';
import {
        Dialog,
        DialogContent,
        DialogDescription,
        DialogHeader,
        DialogTitle,
} from '@/components/ui/dialog';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';
import { copyToClipboard } from '@/utils/common';
import { triggerBrowserDownload } from '@/utils/download';

interface ProjectDirectoryTabProps {
        listProjectDirectory: (path?: string) => Promise<ProjectDirectoryEntry[]>;
        buildProjectDirectoryDownloadUrl: (path?: string) => string | null;
        buildProjectDirectoryPreviewUrl: (path: string) => string | null;
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

export function ProjectDirectoryTab({
        listProjectDirectory,
        buildProjectDirectoryDownloadUrl,
        buildProjectDirectoryPreviewUrl,
}: ProjectDirectoryTabProps) {
        const { t } = useTranslation();
        const [rootEntries, setRootEntries] = useState<ProjectDirectoryEntry[]>([]);
        const [rootLoading, setRootLoading] = useState(false);
        const [rootError, setRootError] = useState<string | null>(null);
        const [expandedPaths, setExpandedPaths] = useState<Record<string, boolean>>({});
        const [loadingPaths, setLoadingPaths] = useState<Record<string, boolean>>({});
        const [childrenByPath, setChildrenByPath] = useState<Record<string, ProjectDirectoryEntry[]>>({});
        const [errorsByPath, setErrorsByPath] = useState<Record<string, string | null>>({});
        const [previewEntry, setPreviewEntry] = useState<ProjectDirectoryEntry | null>(null);
        const [previewText, setPreviewText] = useState('');
        const [previewLoading, setPreviewLoading] = useState(false);
        const [previewError, setPreviewError] = useState<string | null>(null);

        const rootDownloadUrl = useMemo(
                () => buildProjectDirectoryDownloadUrl(),
                [buildProjectDirectoryDownloadUrl],
        );

        const handleDownload = useCallback(
                (path = '') => {
                        const url = buildProjectDirectoryDownloadUrl(path);
                        if (!url) return;
                        triggerBrowserDownload(url);
                },
                [buildProjectDirectoryDownloadUrl],
        );

        const isPreviewable = useCallback((entry: ProjectDirectoryEntry): boolean => {
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

        const isImagePreview = !!previewEntry?.mime_type?.startsWith('image/');
        const isMarkdownPreview =
                previewEntry?.mime_type === 'text/markdown' ||
                previewEntry?.name.toLowerCase().endsWith('.md') === true ||
                previewEntry?.name.toLowerCase().endsWith('.markdown') === true;
        const isJsonPreview =
                previewEntry?.mime_type === 'application/json' ||
                previewEntry?.mime_type?.endsWith('+json') === true ||
                previewEntry?.name.toLowerCase().endsWith('.json') === true ||
                previewEntry?.name.toLowerCase().endsWith('.jsonl') === true;
        const isTextPreview =
                !!previewEntry?.mime_type?.startsWith('text/') ||
                previewEntry?.mime_type === 'application/json' ||
                previewEntry?.mime_type?.endsWith('+json') === true;
        const previewUrl = useMemo(
                () => (previewEntry ? buildProjectDirectoryPreviewUrl(previewEntry.path) : null),
                [buildProjectDirectoryPreviewUrl, previewEntry],
        );
        const prettyPreviewText = useMemo(() => {
                if (!isJsonPreview) {
                        return previewText;
                }
                try {
                        return JSON.stringify(JSON.parse(previewText), null, 2);
                } catch {
                        return previewText;
                }
        }, [isJsonPreview, previewText]);
        const copyablePreviewText = useMemo(
                () => (isJsonPreview ? prettyPreviewText : previewText),
                [isJsonPreview, prettyPreviewText, previewText],
        );

        useEffect(() => {
                if (!previewEntry || !isTextPreview || !previewUrl) {
                        setPreviewText('');
                        setPreviewError(null);
                        setPreviewLoading(false);
                        return;
                }
                const controller = new AbortController();
                setPreviewLoading(true);
                setPreviewError(null);
                setPreviewText('');
                fetch(previewUrl, { signal: controller.signal })
                        .then(async (response) => {
                                if (!response.ok) {
                                        throw new Error(await response.text());
                                }
                                return response.text();
                        })
                        .then((text) => {
                                setPreviewText(text);
                        })
                        .catch((error) => {
                                if (controller.signal.aborted) return;
                                setPreviewError((error as Error).message);
                        })
                        .finally(() => {
                                if (!controller.signal.aborted) {
                                        setPreviewLoading(false);
                                }
                        });
                return () => controller.abort();
        }, [isTextPreview, previewEntry, previewUrl]);

        const loadRoot = useCallback(async () => {
                setRootLoading(true);
                setRootError(null);
                try {
                        setRootEntries(await listProjectDirectory(''));
                } catch (error) {
                        setRootError((error as Error).message);
                        setRootEntries([]);
                } finally {
                        setRootLoading(false);
                }
        }, [listProjectDirectory]);

        useEffect(() => {
                void loadRoot();
        }, [loadRoot]);

        const handleRefresh = useCallback(async () => {
                setExpandedPaths({});
                setLoadingPaths({});
                setChildrenByPath({});
                setErrorsByPath({});
                await loadRoot();
        }, [loadRoot]);

        const handleCopyPreview = useCallback(async () => {
                if (!isTextPreview || previewLoading || previewError || !copyablePreviewText) {
                        return;
                }
                const copied = await copyToClipboard(copyablePreviewText);
                if (copied) {
                        toast.success(t('workspace-drawer.project.copySuccess'));
                } else {
                        toast.error(t('workspace-drawer.project.copyFailed'));
                }
        }, [copyablePreviewText, isTextPreview, previewError, previewLoading, t]);

        const handleToggleDirectory = useCallback(
                async (entry: ProjectDirectoryEntry) => {
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
                                const children = await listProjectDirectory(entry.path);
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
                [childrenByPath, expandedPaths, listProjectDirectory, loadingPaths],
        );

        const renderEntries = useCallback(
                (entries: ProjectDirectoryEntry[], depth: number): ReactNode =>
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
                                                                                ? t('workspace-drawer.project.directoryMeta', {
                                                                                          updatedAt: formatMtime(entry.mtime),
                                                                                  })
                                                                                : t('workspace-drawer.project.fileMeta', {
                                                                                          size: formatBytes(entry.size_bytes),
                                                                                          updatedAt: formatMtime(entry.mtime),
                                                                                  })}
                                                                </div>
                                                        </div>
                                                        <Button
                                                                size="icon-xs"
                                                                variant="ghost"
                                                                tooltip={t('workspace-drawer.project.preview')}
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
                                                                                ? t('workspace-drawer.project.downloadDirectory')
                                                                                : t('workspace-drawer.project.downloadFile')
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
                                                                                {t('workspace-drawer.project.loadFailed')}
                                                                        </div>
                                                                ) : childEntries.length === 0 ? (
                                                                        <div
                                                                                className="px-3 py-2 text-xs text-muted-foreground"
                                                                                style={{
                                                                                        paddingLeft: `${(depth + 1) * 16 + 8}px`,
                                                                                }}
                                                                        >
                                                                                {t('workspace-drawer.project.emptyDirectory')}
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
                        loadingPaths,
                        t,
                ],
        );

        return (
                <div className="flex flex-col no-scrollbar overflow-y-auto gap-y-3">
                        <span className="text-muted-foreground text-sm">
                                {t('workspace-drawer.project.description')}
                        </span>
                        <div className="flex items-center gap-2">
                                <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() => void handleRefresh()}
                                        disabled={rootLoading}
                                >
                                        <RefreshCw className="size-4" />
                                        {t('workspace-drawer.project.refresh')}
                                </Button>
                                <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() => handleDownload('')}
                                        disabled={!rootDownloadUrl}
                                >
                                        <Download className="size-4" />
                                        {t('workspace-drawer.project.downloadProject')}
                                </Button>
                        </div>
                        {rootLoading ? (
                                <p className="text-muted-foreground text-sm text-center py-4">
                                        {t('common.loading')}
                                </p>
                        ) : rootError ? (
                                <p className="text-destructive text-sm text-center py-4">
                                        {t('workspace-drawer.project.loadFailed')}
                                </p>
                        ) : rootEntries.length === 0 ? (
                                <p className="text-muted-foreground text-sm text-center py-4">
                                        {t('workspace-drawer.project.emptyRoot')}
                                </p>
                        ) : (
                                <div className="space-y-1">{renderEntries(rootEntries, 0)}</div>
                        )}
                        <Dialog
                                open={previewEntry !== null}
                                onOpenChange={(open) => {
                                        if (!open) {
                                                setPreviewEntry(null);
                                                setPreviewText('');
                                                setPreviewError(null);
                                                setPreviewLoading(false);
                                        }
                                }}
                        >
                                <DialogContent className="!max-w-4xl">
                                        <DialogHeader className="pr-12">
                                        <div className="flex items-start justify-between gap-3">
                                                <div className="min-w-0">
                                                        <DialogTitle>
                                                                {previewEntry?.name ??
                                                                        t('workspace-drawer.project.previewTitle')}
                                                        </DialogTitle>
                                                        <DialogDescription>
                                                                {previewEntry?.path ??
                                                                        t('workspace-drawer.project.previewDescription')}
                                                        </DialogDescription>
                                                </div>
                                                {isTextPreview ? (
                                                        <Button
                                                                size="sm"
                                                                variant="outline"
                                                                onClick={() => void handleCopyPreview()}
                                                                disabled={
                                                                        previewLoading ||
                                                                        !!previewError ||
                                                                        !copyablePreviewText
                                                                }
                                                        >
                                                                <Copy className="size-4" />
                                                                {t('workspace-drawer.project.copy')}
                                                        </Button>
                                                ) : null}
                                        </div>
                                        </DialogHeader>
                                        {previewEntry ? (
                                                isImagePreview && previewUrl ? (
                                                        <div className="max-h-[70vh] overflow-auto rounded-md border bg-muted/20 p-2">
                                                                <img
                                                                        src={previewUrl}
                                                                        alt={previewEntry.name}
                                                                        className="mx-auto max-h-[65vh] max-w-full rounded-md object-contain"
                                                                />
                                                        </div>
                                                ) : isTextPreview ? (
                                                        previewLoading ? (
                                                                <div className="py-8 text-center text-sm text-muted-foreground">
                                                                        {t('common.loading')}
                                                                </div>
                                                        ) : previewError ? (
                                                                <div className="py-8 text-center text-sm text-destructive">
                                                                        {t('workspace-drawer.project.previewLoadFailed')}
                                                                </div>
                                                        ) : isMarkdownPreview ? (
                                                                <div className="max-h-[70vh] overflow-auto rounded-md border bg-muted/20 p-4">
                                                                        <div className="prose prose-sm max-w-none text-foreground dark:prose-invert">
                                                                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                                                        {previewText}
                                                                                </ReactMarkdown>
                                                                        </div>
                                                                </div>
                                                        ) : (
                                                                <pre className="max-h-[70vh] overflow-auto rounded-md border bg-muted/20 p-4 text-xs whitespace-pre-wrap break-words">
                                                                        {prettyPreviewText}
                                                                </pre>
                                                        )
                                                ) : (
                                                        <div className="py-8 text-center text-sm text-muted-foreground">
                                                                {t('workspace-drawer.project.previewUnsupported')}
                                                        </div>
                                                )
                                        ) : null}
                                </DialogContent>
                        </Dialog>
                </div>
        );
}
