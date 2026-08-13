import { ProjectDirectoryBrowser } from './ProjectDirectoryBrowser';
import type { WorkspaceFileEntry } from '@/api';
import {
        Dialog,
        DialogContent,
        DialogDescription,
        DialogHeader,
        DialogTitle,
} from '@/components/ui/dialog';
import { useTranslation } from '@/i18n/useI18n';

interface ProjectDirectoryDialogProps {
        open: boolean;
        onOpenChange: (open: boolean) => void;
        initialPath: string;
        listWorkspaceFiles: (path?: string) => Promise<WorkspaceFileEntry[]>;
        buildWorkspaceFileDownloadUrl: (path?: string) => string | null;
        buildWorkspaceFilePreviewUrl: (path: string) => string | null;
}

export function ProjectDirectoryDialog({
        open,
        onOpenChange,
        initialPath,
        listWorkspaceFiles,
        buildWorkspaceFileDownloadUrl,
        buildWorkspaceFilePreviewUrl,
}: ProjectDirectoryDialogProps) {
        const { t } = useTranslation();
        const title = initialPath || t('workspace-drawer.file.rootDirectory');

        return (
                <Dialog open={open} onOpenChange={onOpenChange}>
                        <DialogContent className="!max-w-4xl">
                                <DialogHeader>
                                        <DialogTitle>{title}</DialogTitle>
                                        <DialogDescription>
                                                {t('workspace-drawer.file.directoryDialogDescription')}
                                        </DialogDescription>
                                </DialogHeader>
                                <div className="max-h-[70vh] overflow-auto pr-1">
                                        <ProjectDirectoryBrowser
                                                initialPath={initialPath}
                                                listWorkspaceFiles={listWorkspaceFiles}
                                                buildWorkspaceFileDownloadUrl={
                                                        buildWorkspaceFileDownloadUrl
                                                }
                                                buildWorkspaceFilePreviewUrl={buildWorkspaceFilePreviewUrl}
                                                showDescription={false}
                                        />
                                </div>
                        </DialogContent>
                </Dialog>
        );
}
