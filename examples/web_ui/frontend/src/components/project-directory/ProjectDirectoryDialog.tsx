import { ProjectDirectoryBrowser } from './ProjectDirectoryBrowser';
import type { ProjectDirectoryEntry } from '@/api';
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
        listProjectDirectory: (path?: string) => Promise<ProjectDirectoryEntry[]>;
        buildProjectDirectoryDownloadUrl: (path?: string) => string | null;
        buildProjectDirectoryPreviewUrl: (path: string) => string | null;
}

export function ProjectDirectoryDialog({
        open,
        onOpenChange,
        initialPath,
        listProjectDirectory,
        buildProjectDirectoryDownloadUrl,
        buildProjectDirectoryPreviewUrl,
}: ProjectDirectoryDialogProps) {
        const { t } = useTranslation();
        const title = initialPath || t('workspace-drawer.project.rootDirectory');

        return (
                <Dialog open={open} onOpenChange={onOpenChange}>
                        <DialogContent className="!max-w-4xl">
                                <DialogHeader>
                                        <DialogTitle>{title}</DialogTitle>
                                        <DialogDescription>
                                                {t('workspace-drawer.project.directoryDialogDescription')}
                                        </DialogDescription>
                                </DialogHeader>
                                <div className="max-h-[70vh] overflow-auto pr-1">
                                        <ProjectDirectoryBrowser
                                                initialPath={initialPath}
                                                listProjectDirectory={listProjectDirectory}
                                                buildProjectDirectoryDownloadUrl={
                                                        buildProjectDirectoryDownloadUrl
                                                }
                                                buildProjectDirectoryPreviewUrl={buildProjectDirectoryPreviewUrl}
                                                showDescription={false}
                                        />
                                </div>
                        </DialogContent>
                </Dialog>
        );
}
