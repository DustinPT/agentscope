import { Download, FileArchive, FileText, RotateCcw, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { skillLibraryApi, type SkillLibraryRecord } from '@/api';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { MarkdownRenderer } from '@/components/markdown/MarkdownRenderer';
import { ProjectDirectoryBrowser } from '@/components/project-directory/ProjectDirectoryBrowser';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
        Drawer,
        DrawerContent,
        DrawerDescription,
        DrawerHeader,
        DrawerTitle,
} from '@/components/ui/drawer';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useTranslation } from '@/i18n/useI18n';

interface SkillVersionDetailDrawerProps {
        skillName: string;
        versionId: string | null;
        open: boolean;
        onOpenChange: (open: boolean) => void;
        onRollback: (record: SkillLibraryRecord) => void;
        onDeleted: (versionId: string) => void;
}

function formatDateTime(value: string): string {
        return new Date(value).toLocaleString();
}

export function SkillVersionDetailDrawer({
        skillName,
        versionId,
        open,
        onOpenChange,
        onRollback,
        onDeleted,
}: SkillVersionDetailDrawerProps) {
        const { t } = useTranslation();
        const [detail, setDetail] = useState<SkillLibraryRecord | null>(null);
        const [loading, setLoading] = useState(false);
        const [deleteOpen, setDeleteOpen] = useState(false);

        useEffect(() => {
                if (!open || !versionId) {
                        setDetail(null);
                        return;
                }
                setLoading(true);
                skillLibraryApi
                        .getVersion(skillName, versionId)
                        .then(setDetail)
                        .finally(() => setLoading(false));
        }, [open, skillName, versionId]);

        const fileCount = useMemo(
                () => detail?.file_manifest.length ?? 0,
                [detail],
        );

        const handleDownload = async () => {
                if (!detail) return;
                const blob = await skillLibraryApi.downloadVersionArchive(skillName, detail.id);
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = `${skillName}-v${detail.version_number}.zip`;
                link.click();
                URL.revokeObjectURL(url);
        };

        const handleRollback = async () => {
                if (!detail || detail.is_current) return;
                const next = await skillLibraryApi.rollbackVersion(skillName, detail.id);
                toast.success(
                        t('settings.skillLibrary.toast.rollbackSuccess', {
                                version: next.version_number,
                        }),
                );
                onRollback(next);
                onOpenChange(false);
        };

        const handleDelete = async () => {
                if (!detail || detail.is_current) return;
                await skillLibraryApi.deleteVersion(skillName, detail.id);
                toast.success(
                        t('settings.skillLibrary.toast.deleteVersionSuccess', {
                                version: detail.version_number,
                        }),
                );
                onDeleted(detail.id);
                setDeleteOpen(false);
                onOpenChange(false);
        };

        return (
                <>
                        <Drawer open={open} onOpenChange={onOpenChange} direction="right">
                                <DrawerContent className="max-h-full h-full min-w-[32rem] ml-auto border-none ring-none! shadow-none!">
                                        <DrawerHeader>
                                                <DrawerTitle>
                                                        {detail
                                                                ? t('settings.skillLibrary.version.versionTitle', {
                                                                          version: detail.version_number,
                                                                  })
                                                                : t('settings.skillLibrary.version.loading')}
                                                </DrawerTitle>
                                                <DrawerDescription>
                                                        {detail?.name ?? skillName}
                                                </DrawerDescription>
                                        </DrawerHeader>
                                        <div className="flex flex-1 flex-col overflow-auto p-4">
                                                {loading || !detail ? (
                                                        <div className="py-10 text-sm text-muted-foreground">
                                                                {t('settings.skillLibrary.version.loading')}
                                                        </div>
                                                ) : (
                                                        <div className="flex flex-col gap-4">
                                                                <Card>
                                                                        <CardHeader className="gap-4">
                                                                                <div className="flex flex-wrap items-center gap-2">
                                                                                        <Badge variant={detail.is_current ? 'default' : 'secondary'}>
                                                                                                {detail.is_current
                                                                                                        ? t('settings.skillLibrary.version.current')
                                                                                                        : t('settings.skillLibrary.version.history')}
                                                                                        </Badge>
                                                                                        <Badge variant="outline">
                                                                                                <FileArchive className="size-3.5" />
                                                                                                {t('settings.skillLibrary.detail.fileCount', {
                                                                                                        count: fileCount,
                                                                                                })}
                                                                                        </Badge>
                                                                                </div>
                                                                                <CardTitle>
                                                                                        {t('settings.skillLibrary.version.versionTitle', {
                                                                                                version: detail.version_number,
                                                                                        })}
                                                                                </CardTitle>
                                                                                <CardDescription>{detail.description}</CardDescription>
                                                                                <div className="grid gap-2 text-sm text-muted-foreground">
                                                                                        <div>{t('settings.skillLibrary.detail.archiveName', { name: detail.archive_name })}</div>
                                                                                        <div>{t('settings.skillLibrary.detail.createdAt', { value: formatDateTime(detail.created_at) })}</div>
                                                                                        <div>{t('settings.skillLibrary.detail.updatedAt', { value: formatDateTime(detail.updated_at) })}</div>
                                                                                        <div>{detail.content_hash}</div>
                                                                                </div>
                                                                                <div className="flex flex-wrap gap-2">
                                                                                        <Button variant="outline" onClick={() => void handleDownload()}>
                                                                                                <Download className="size-4" />
                                                                                                {t('settings.skillLibrary.download')}
                                                                                        </Button>
                                                                                        {!detail.is_current ? (
                                                                                                <Button variant="outline" onClick={() => void handleRollback()}>
                                                                                                        <RotateCcw className="size-4" />
                                                                                                        {t('settings.skillLibrary.version.rollback')}
                                                                                                </Button>
                                                                                        ) : null}
                                                                                        {!detail.is_current ? (
                                                                                                <Button variant="outline" onClick={() => setDeleteOpen(true)}>
                                                                                                        <Trash2 className="size-4" />
                                                                                                        {t('common.delete')}
                                                                                                </Button>
                                                                                        ) : null}
                                                                                </div>
                                                                        </CardHeader>
                                                                </Card>

                                                                <Tabs defaultValue="skill-md" className="min-h-[500px]">
                                                                        <TabsList variant="line">
                                                                                <TabsTrigger value="skill-md">
                                                                                        <FileText className="size-4" />
                                                                                        {t('settings.skillLibrary.detail.tabs.skillMd')}
                                                                                </TabsTrigger>
                                                                                <TabsTrigger value="files">
                                                                                        <FileArchive className="size-4" />
                                                                                        {t('settings.skillLibrary.detail.tabs.files')}
                                                                                </TabsTrigger>
                                                                        </TabsList>
                                                                        <TabsContent value="skill-md">
                                                                                <Card>
                                                                                        <CardContent className="pt-6">
                                                                                                <div className="prose prose-sm max-w-none text-foreground dark:prose-invert">
                                                                                                        <MarkdownRenderer>{detail.skill_markdown}</MarkdownRenderer>
                                                                                                </div>
                                                                                        </CardContent>
                                                                                </Card>
                                                                        </TabsContent>
                                                                        <TabsContent value="files">
                                                                                <Card>
                                                                                        <CardContent className="pt-6">
                                                                                                <ProjectDirectoryBrowser
                                                                                                        listWorkspaceFiles={(path) =>
                                                                                                                skillLibraryApi.listVersionFiles(
                                                                                                                        skillName,
                                                                                                                        detail.id,
                                                                                                                        path ?? '',
                                                                                                                )
                                                                                                        }
                                                                                                        buildWorkspaceFileDownloadUrl={(path) =>
                                                                                                                skillLibraryApi.buildVersionFileDownloadUrl(
                                                                                                                        skillName,
                                                                                                                        detail.id,
                                                                                                                        path,
                                                                                                                )
                                                                                                        }
                                                                                                        buildWorkspaceFilePreviewUrl={(path) =>
                                                                                                                skillLibraryApi.buildVersionFilePreviewUrl(
                                                                                                                        skillName,
                                                                                                                        detail.id,
                                                                                                                        path,
                                                                                                                )
                                                                                                        }
                                                                                                        buildWorkspaceFileTextPreviewUrl={(path) =>
                                                                                                                skillLibraryApi.buildVersionFileTextPreviewUrl(
                                                                                                                        skillName,
                                                                                                                        detail.id,
                                                                                                                        path,
                                                                                                                )
                                                                                                        }
                                                                                                />
                                                                                        </CardContent>
                                                                                </Card>
                                                                        </TabsContent>
                                                                </Tabs>
                                                        </div>
                                                )}
                                        </div>
                                </DrawerContent>
                        </Drawer>

                        <DeleteDialog
                                open={deleteOpen}
                                onOpenChange={setDeleteOpen}
                                title={t('settings.skillLibrary.version.deleteTitle', {
                                        version: detail?.version_number ?? '',
                                })}
                                description={t('settings.skillLibrary.version.deleteDescription')}
                                confirmLabel={t('common.delete')}
                                onConfirm={handleDelete}
                        />
                </>
        );
}
