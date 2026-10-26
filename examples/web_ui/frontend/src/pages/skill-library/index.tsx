import { ArrowLeft, Download, FileArchive, FileSearch, FileText, Hash, Search, Trash2, Upload } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';

import { SkillVersionDetailDrawer } from './skill-version-detail-drawer';
import { skillLibraryApi, type SkillLibraryRecord } from '@/api';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { MarkdownRenderer } from '@/components/markdown/MarkdownRenderer';
import { ProjectDirectoryBrowser } from '@/components/project-directory/ProjectDirectoryBrowser';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useTranslation } from '@/i18n/useI18n';

function formatDateTime(value: string): string {
        return new Date(value).toLocaleString();
}

export function SkillLibraryPage() {
        const navigate = useNavigate();
        const { skillName } = useParams<{ skillName?: string }>();
        const { t } = useTranslation();
        const fileInputRef = useRef<HTMLInputElement | null>(null);

        const [query, setQuery] = useState('');
        const [skills, setSkills] = useState<SkillLibraryRecord[]>([]);
        const [total, setTotal] = useState(0);
        const [skillsLoading, setSkillsLoading] = useState(false);
        const [detailLoading, setDetailLoading] = useState(false);
        const [detail, setDetail] = useState<SkillLibraryRecord | null>(null);
        const [versionsLoading, setVersionsLoading] = useState(false);
        const [versions, setVersions] = useState<SkillLibraryRecord[]>([]);
        const [activeVersionId, setActiveVersionId] = useState<string | null>(null);
        const [versionDrawerOpen, setVersionDrawerOpen] = useState(false);
        const [deleteTarget, setDeleteTarget] = useState<SkillLibraryRecord | null>(null);
        const [uploading, setUploading] = useState(false);

        const selectedSkillName = skillName ? decodeURIComponent(skillName) : null;

        const loadSkills = useCallback(async (keyword = '') => {
                setSkillsLoading(true);
                try {
                        const response = await skillLibraryApi.list({
                                keyword: keyword.trim(),
                                limit: 100,
                                offset: 0,
                        });
                        setSkills(response.skills);
                        setTotal(response.total);
                } finally {
                        setSkillsLoading(false);
                }
        }, []);

        const loadDetail = useCallback(async (name: string) => {
                setDetailLoading(true);
                try {
                        const response = await skillLibraryApi.get(name);
                        setDetail(response);
                } finally {
                        setDetailLoading(false);
                }
        }, []);

        const loadVersions = useCallback(async (name: string) => {
                setVersionsLoading(true);
                try {
                        const response = await skillLibraryApi.listVersions(name);
                        setVersions(response);
                } finally {
                        setVersionsLoading(false);
                }
        }, []);

        useEffect(() => {
                void loadSkills('');
        }, [loadSkills]);

        useEffect(() => {
                if (selectedSkillName) {
                        void loadDetail(selectedSkillName);
                        void loadVersions(selectedSkillName);
                } else {
                        setDetail(null);
                        setVersions([]);
                }
        }, [loadDetail, loadVersions, selectedSkillName]);

        const handleUploadClick = () => {
                fileInputRef.current?.click();
        };

        const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (!file) return;

                const formData = new FormData();
                formData.set('file', file);
                setUploading(true);
                try {
                        const created = await skillLibraryApi.upload(formData);
                        toast.success(t('settings.skillLibrary.toast.uploadSuccess', { name: created.name }));
                        await loadSkills(query);
                        navigate(`/skill-library/${encodeURIComponent(created.name)}`);
                } finally {
                        setUploading(false);
                }
        };

        const handleArchiveDownload = async (name: string) => {
                const blob = await skillLibraryApi.downloadArchive(name);
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = `${name}.zip`;
                link.click();
                URL.revokeObjectURL(url);
        };

        const handleDelete = async (name: string) => {
                await skillLibraryApi.delete(name);
                toast.success(t('settings.skillLibrary.toast.deleteSuccess', { name }));
                if (selectedSkillName === name) {
                        navigate('/skill-library');
                }
                await loadSkills(query);
        };

        const headerTitle = selectedSkillName ? detail?.name ?? selectedSkillName : t('settings.skillLibrary.title');
        const fileCount = useMemo(
                () => detail?.file_manifest.length ?? 0,
                [detail],
        );

        return (
                <div className="w-full h-full flex flex-col bg-sidebar overflow-hidden">
                        <div className="flex items-center justify-between gap-3 p-4 flex-shrink-0">
                                <div className="space-y-1">
                                        <div className="flex items-center gap-2">
                                                {selectedSkillName ? (
                                                        <Button variant="ghost" size="icon-sm" onClick={() => navigate('/skill-library')}>
                                                                <ArrowLeft className="size-4" />
                                                        </Button>
                                                ) : null}
                                                <h1 className="text-2xl font-semibold">{headerTitle}</h1>
                                        </div>
                                        {selectedSkillName ? null : (
                                                <p className="text-sm text-muted-foreground">
                                                        {t('settings.skillLibrary.description')}
                                                </p>
                                        )}
                                </div>
                                {selectedSkillName ? null : (
                                        <div className="flex items-center gap-2">
                                                <Button variant="outline" onClick={() => navigate('/skill-library-test')}>
                                                        <FileSearch className="size-4" />
                                                        {t('settings.skillLibrary.openTestPage')}
                                                </Button>
                                                <input
                                                        ref={fileInputRef}
                                                        type="file"
                                                        accept=".zip,application/zip"
                                                        className="hidden"
                                                        onChange={(event) => void handleFileChange(event)}
                                                />
                                                <Button variant="outline" onClick={handleUploadClick} disabled={uploading}>
                                                        <Upload className="size-4" />
                                                        {uploading
                                                                ? t('settings.skillLibrary.uploading')
                                                                : t('settings.skillLibrary.upload')}
                                                </Button>
                                        </div>
                                )}
                        </div>

                        <div className="flex-1 overflow-auto rounded-t-3xl bg-white p-4">
                                {selectedSkillName ? (
                                        detailLoading || !detail ? (
                                                <div className="py-10 text-sm text-muted-foreground">
                                                        {t('settings.skillLibrary.detail.loading')}
                                                </div>
                                        ) : (
                                                <div className="mx-auto flex max-w-6xl flex-col gap-4">
                                                        <Card>
                                                                <CardHeader className="gap-4 md:flex-row md:items-start md:justify-between">
                                                                        <div className="space-y-3">
                                                                                <div className="flex flex-wrap items-center gap-2">
                                                                                        <Badge variant="default">
                                                                                                {t('settings.skillLibrary.version.versionBadge', {
                                                                                                        version: detail.version_number,
                                                                                                })}
                                                                                        </Badge>
                                                                                        <Badge variant="secondary">
                                                                                                <Hash className="size-3.5" />
                                                                                                {detail.content_hash}
                                                                                        </Badge>
                                                                                        <Badge variant="outline">
                                                                                                <FileArchive className="size-3.5" />
                                                                                                {t('settings.skillLibrary.detail.fileCount', {
                                                                                                        count: fileCount,
                                                                                                })}
                                                                                        </Badge>
                                                                                </div>
                                                                                <CardTitle className="text-2xl">{detail.name}</CardTitle>
                                                                                <CardDescription className="text-sm">
                                                                                        {detail.description}
                                                                                </CardDescription>
                                                                                <div className="grid gap-2 text-sm text-muted-foreground md:grid-cols-2">
                                                                                        <div>{t('settings.skillLibrary.detail.archiveName', { name: detail.archive_name })}</div>
                                                                                        <div>{t('settings.skillLibrary.detail.createdAt', { value: formatDateTime(detail.created_at) })}</div>
                                                                                        <div>{t('settings.skillLibrary.detail.updatedAt', { value: formatDateTime(detail.updated_at) })}</div>
                                                                                </div>
                                                                        </div>
                                                                        <div className="flex shrink-0 items-center gap-2">
                                                                                <Button
                                                                                        variant="outline"
                                                                                        onClick={() => void handleArchiveDownload(detail.name)}
                                                                                >
                                                                                        <Download className="size-4" />
                                                                                        {t('settings.skillLibrary.download')}
                                                                                </Button>
                                                                                <Button
                                                                                        variant="outline"
                                                                                        onClick={() => setDeleteTarget(detail)}
                                                                                >
                                                                                        <Trash2 className="size-4" />
                                                                                        {t('common.delete')}
                                                                                </Button>
                                                                        </div>
                                                                </CardHeader>
                                                        </Card>

                                                        <Tabs defaultValue="skill-md" className="min-h-[600px]">
                                                                <TabsList variant="line">
                                                                        <TabsTrigger value="skill-md">
                                                                                <FileText className="size-4" />
                                                                                {t('settings.skillLibrary.detail.tabs.skillMd')}
                                                                        </TabsTrigger>
                                                                        <TabsTrigger value="files">
                                                                                <FileArchive className="size-4" />
                                                                                {t('settings.skillLibrary.detail.tabs.files')}
                                                                        </TabsTrigger>
                                                                        <TabsTrigger value="history">
                                                                                <FileArchive className="size-4" />
                                                                                {t('settings.skillLibrary.detail.tabs.history')}
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
                                                                                                        skillLibraryApi.listFiles(detail.name, path ?? '')
                                                                                                }
                                                                                                buildWorkspaceFileDownloadUrl={(path) =>
                                                                                                        skillLibraryApi.buildFileDownloadUrl(
                                                                                                                detail.name,
                                                                                                                path,
                                                                                                        )
                                                                                                }
                                                                                                buildWorkspaceFilePreviewUrl={(path) =>
                                                                                                        skillLibraryApi.buildFilePreviewUrl(
                                                                                                                detail.name,
                                                                                                                path,
                                                                                                        )
                                                                                                }
                                                                                                buildWorkspaceFileTextPreviewUrl={(path) =>
                                                                                                        skillLibraryApi.buildFileTextPreviewUrl(
                                                                                                                detail.name,
                                                                                                                path,
                                                                                                        )
                                                                                                }
                                                                                        />
                                                                                </CardContent>
                                                                        </Card>
                                                                </TabsContent>
                                                                <TabsContent value="history">
                                                                        <Card>
                                                                                <CardContent className="pt-6">
                                                                                        {versionsLoading ? (
                                                                                                <div className="text-sm text-muted-foreground">
                                                                                                        {t('common.loading')}
                                                                                                </div>
                                                                                        ) : (
                                                                                                <div className="grid gap-3">
                                                                                                        {versions.map((version) => (
                                                                                                                <Card key={version.id}>
                                                                                                                        <CardContent className="flex flex-col gap-4 pt-6 md:flex-row md:items-start md:justify-between">
                                                                                                                                <div className="space-y-2">
                                                                                                                                        <div className="flex flex-wrap items-center gap-2">
                                                                                                                                                <Badge variant={version.is_current ? 'default' : 'secondary'}>
                                                                                                                                                        {version.is_current
                                                                                                                                                                ? t('settings.skillLibrary.version.current')
                                                                                                                                                                : t('settings.skillLibrary.version.history')}
                                                                                                                                                </Badge>
                                                                                                                                                <Badge variant="outline">
                                                                                                                                                        {t('settings.skillLibrary.version.versionBadge', {
                                                                                                                                                                version: version.version_number,
                                                                                                                                                        })}
                                                                                                                                                </Badge>
                                                                                                                                        </div>
                                                                                                                                        <div className="font-medium">
                                                                                                                                                {version.archive_name}
                                                                                                                                        </div>
                                                                                                                                        <div className="text-sm text-muted-foreground break-all">
                                                                                                                                                {version.content_hash}
                                                                                                                                        </div>
                                                                                                                                        <div className="text-sm text-muted-foreground">
                                                                                                                                                {t('settings.skillLibrary.detail.updatedAt', {
                                                                                                                                                        value: formatDateTime(version.updated_at),
                                                                                                                                                })}
                                                                                                                                        </div>
                                                                                                                                </div>
                                                                                                                                <div className="flex shrink-0 items-center gap-2">
                                                                                                                                        <Button
                                                                                                                                                variant="outline"
                                                                                                                                                onClick={() => {
                                                                                                                                                        setActiveVersionId(version.id);
                                                                                                                                                        setVersionDrawerOpen(true);
                                                                                                                                                }}
                                                                                                                                        >
                                                                                                                                                {t('settings.skillLibrary.viewDetail')}
                                                                                                                                        </Button>
                                                                                                                                </div>
                                                                                                                        </CardContent>
                                                                                                                </Card>
                                                                                                        ))}
                                                                                                </div>
                                                                                        )}
                                                                                </CardContent>
                                                                        </Card>
                                                                </TabsContent>
                                                        </Tabs>
                                                </div>
                                        )
                                ) : (
                                        <div className="mx-auto flex max-w-6xl flex-col gap-4">
                                                <Card>
                                                        <CardContent className="pt-6">
                                                                <div className="flex flex-col gap-3">
                                                                        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                                                                                <div className="relative w-full md:max-w-md">
                                                                                        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                                                                                        <Input
                                                                                                className="pl-9"
                                                                                                placeholder={t('settings.skillLibrary.searchPlaceholder')}
                                                                                                value={query}
                                                                                                onChange={(event) => setQuery(event.target.value)}
                                                                                                onKeyDown={(event) => {
                                                                                                        if (event.key === 'Enter') {
                                                                                                                void loadSkills(query);
                                                                                                        }
                                                                                                }}
                                                                                        />
                                                                                </div>
                                                                                <Button variant="outline" onClick={() => void loadSkills(query)} disabled={skillsLoading}>
                                                                                        <Search className="size-4" />
                                                                                        {t('settings.skillLibrary.search')}
                                                                                </Button>
                                                                        </div>
                                                                        <div className="text-sm text-muted-foreground">
                                                                                {t('settings.skillLibrary.totalCount', { count: total })}
                                                                        </div>
                                                                </div>
                                                        </CardContent>
                                                </Card>

                                                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                                                        {skills.map((skill) => (
                                                                <Card key={skill.id} className="flex flex-col">
                                                                        <CardHeader className="space-y-3">
                                                                                <div className="flex items-start justify-between gap-3">
                                                                                        <div className="space-y-1">
                                                                                                <CardTitle className="text-lg">{skill.name}</CardTitle>
                                                                                                <CardDescription className="line-clamp-2">
                                                                                                        {skill.description}
                                                                                                </CardDescription>
                                                                                        </div>
                                                                                        <Badge variant="secondary">
                                                                                                {t('settings.skillLibrary.card.fileCount', {
                                                                                                        count: skill.file_manifest.length,
                                                                                                })}
                                                                                        </Badge>
                                                                                </div>
                                                                                <div className="flex flex-wrap gap-2">
                                                                                        <Badge variant="outline">
                                                                                                {t('settings.skillLibrary.version.versionBadge', {
                                                                                                        version: skill.version_number,
                                                                                                })}
                                                                                        </Badge>
                                                                                </div>
                                                                                <div className="text-xs break-all text-muted-foreground">
                                                                                        {skill.content_hash}
                                                                                </div>
                                                                        </CardHeader>
                                                                        <Separator />
                                                                        <CardContent className="flex flex-1 flex-col justify-between gap-4 pt-6">
                                                                                <div className="space-y-2 text-sm text-muted-foreground">
                                                                                        <div>{t('settings.skillLibrary.card.archiveName', { name: skill.archive_name })}</div>
                                                                                        <div>{t('settings.skillLibrary.card.updatedAt', { value: formatDateTime(skill.updated_at) })}</div>
                                                                                </div>
                                                                                <div className="flex items-center gap-2">
                                                                                        <Button
                                                                                                className="flex-1"
                                                                                                variant="outline"
                                                                                                onClick={() =>
                                                                                                        navigate(
                                                                                                                `/skill-library/${encodeURIComponent(skill.name)}`,
                                                                                                        )
                                                                                                }
                                                                                        >
                                                                                                {t('settings.skillLibrary.viewDetail')}
                                                                                        </Button>
                                                                                        <Button
                                                                                                size="icon"
                                                                                                variant="outline"
                                                                                                onClick={() => setDeleteTarget(skill)}
                                                                                        >
                                                                                                <Trash2 className="size-4" />
                                                                                        </Button>
                                                                                </div>
                                                                        </CardContent>
                                                                </Card>
                                                        ))}
                                                </div>

                                                {!skillsLoading && skills.length === 0 ? (
                                                        <Card>
                                                                <CardContent className="py-10 text-center text-sm text-muted-foreground">
                                                                        {t('settings.skillLibrary.empty')}
                                                                </CardContent>
                                                        </Card>
                                                ) : null}
                                        </div>
                                )}
                        </div>

                        {selectedSkillName ? (
                                <SkillVersionDetailDrawer
                                        skillName={selectedSkillName}
                                        versionId={activeVersionId}
                                        open={versionDrawerOpen}
                                        onOpenChange={setVersionDrawerOpen}
                                        onRollback={(record) => {
                                                setDetail(record);
                                                void loadVersions(selectedSkillName);
                                                void loadSkills(query);
                                        }}
                                        onDeleted={() => {
                                                void loadVersions(selectedSkillName);
                                        }}
                                />
                        ) : null}

                        <DeleteDialog
                                open={!!deleteTarget}
                                onOpenChange={(open) => {
                                        if (!open) setDeleteTarget(null);
                                }}
                                title={t('settings.skillLibrary.deleteTitle', {
                                        name: deleteTarget?.name ?? '',
                                })}
                                description={t('settings.skillLibrary.deleteDescription')}
                                confirmLabel={t('common.delete')}
                                onConfirm={async () => {
                                        if (!deleteTarget) return;
                                        await handleDelete(deleteTarget.name);
                                        setDeleteTarget(null);
                                }}
                        />
                </div>
        );
}
