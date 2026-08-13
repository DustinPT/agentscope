import type { WorkspaceFileEntry } from '@/api';
import { ProjectDirectoryBrowser } from '@/components/project-directory/ProjectDirectoryBrowser';

interface ProjectDirectoryTabProps {
        listWorkspaceFiles: (path?: string) => Promise<WorkspaceFileEntry[]>;
        buildWorkspaceFileDownloadUrl: (path?: string) => string | null;
        buildWorkspaceFilePreviewUrl: (path: string) => string | null;
}

export function ProjectDirectoryTab({
        listWorkspaceFiles,
        buildWorkspaceFileDownloadUrl,
        buildWorkspaceFilePreviewUrl,
}: ProjectDirectoryTabProps) {
        return (
                <ProjectDirectoryBrowser
                        listWorkspaceFiles={listWorkspaceFiles}
                        buildWorkspaceFileDownloadUrl={buildWorkspaceFileDownloadUrl}
                        buildWorkspaceFilePreviewUrl={buildWorkspaceFilePreviewUrl}
                />
        );
}
