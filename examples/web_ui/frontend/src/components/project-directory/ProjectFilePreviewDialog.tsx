import { Copy, Download } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
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
import { copyToClipboard } from '@/utils/common';
import { triggerBrowserDownload } from '@/utils/download';

interface ProjectFilePreviewDialogProps {
        open: boolean;
        onOpenChange: (open: boolean) => void;
        entry: ProjectDirectoryEntry | null;
        buildProjectDirectoryPreviewUrl: (path: string) => string | null;
        buildProjectDirectoryDownloadUrl: (path?: string) => string | null;
}

export function ProjectFilePreviewDialog({
        open,
        onOpenChange,
        entry,
        buildProjectDirectoryPreviewUrl,
        buildProjectDirectoryDownloadUrl,
}: ProjectFilePreviewDialogProps) {
        const { t } = useTranslation();
        const [previewText, setPreviewText] = useState('');
        const [previewLoading, setPreviewLoading] = useState(false);
        const [previewError, setPreviewError] = useState<string | null>(null);

        const previewUrl = useMemo(
                () => (entry ? buildProjectDirectoryPreviewUrl(entry.path) : null),
                [buildProjectDirectoryPreviewUrl, entry],
        );
        const downloadUrl = useMemo(
                () => (entry ? buildProjectDirectoryDownloadUrl(entry.path) : null),
                [buildProjectDirectoryDownloadUrl, entry],
        );

        const isImagePreview = !!entry?.mime_type?.startsWith('image/');
        const isMarkdownPreview =
                entry?.mime_type === 'text/markdown' ||
                entry?.name.toLowerCase().endsWith('.md') === true ||
                entry?.name.toLowerCase().endsWith('.markdown') === true;
        const isJsonPreview =
                entry?.mime_type === 'application/json' ||
                entry?.mime_type?.endsWith('+json') === true ||
                entry?.name.toLowerCase().endsWith('.json') === true ||
                entry?.name.toLowerCase().endsWith('.jsonl') === true;
        const isTextPreview =
                !!entry?.mime_type?.startsWith('text/') ||
                entry?.mime_type === 'application/json' ||
                entry?.mime_type?.endsWith('+json') === true;

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
                if (!open || !entry || !isTextPreview || !previewUrl) {
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
                                if (controller.signal.aborted) {
                                        return;
                                }
                                setPreviewError((error as Error).message);
                        })
                        .finally(() => {
                                if (!controller.signal.aborted) {
                                        setPreviewLoading(false);
                                }
                        });

                return () => controller.abort();
        }, [entry, isTextPreview, open, previewUrl]);

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

        const handleDownload = useCallback(() => {
                if (!downloadUrl) {
                        return;
                }
                triggerBrowserDownload(downloadUrl);
        }, [downloadUrl]);

        return (
                <Dialog
                        open={open}
                        onOpenChange={(nextOpen) => {
                                if (!nextOpen) {
                                        setPreviewText('');
                                        setPreviewError(null);
                                        setPreviewLoading(false);
                                }
                                onOpenChange(nextOpen);
                        }}
                >
                        <DialogContent className="!max-w-4xl">
                                <DialogHeader className="pr-12">
                                        <div className="flex items-start justify-between gap-3">
                                                <div className="min-w-0">
                                                        <DialogTitle>
                                                                {entry?.name ?? t('workspace-drawer.project.previewTitle')}
                                                        </DialogTitle>
                                                        <DialogDescription>
                                                                {entry?.path ?? t('workspace-drawer.project.previewDescription')}
                                                        </DialogDescription>
                                                </div>
                                                <div className="flex items-center gap-2">
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
                                                        <Button
                                                                size="sm"
                                                                variant="outline"
                                                                onClick={handleDownload}
                                                                disabled={!downloadUrl}
                                                        >
                                                                <Download className="size-4" />
                                                                {t('workspace-drawer.project.downloadFile')}
                                                        </Button>
                                                </div>
                                        </div>
                                </DialogHeader>
                                {entry ? (
                                        isImagePreview && previewUrl ? (
                                                <div className="max-h-[70vh] overflow-auto rounded-md border bg-muted/20 p-2">
                                                        <img
                                                                src={previewUrl}
                                                                alt={entry.name}
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
        );
}
