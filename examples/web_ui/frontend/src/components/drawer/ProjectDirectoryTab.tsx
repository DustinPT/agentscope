import type { ProjectDirectoryEntry } from '@/api';
import { ProjectDirectoryBrowser } from '@/components/project-directory/ProjectDirectoryBrowser';

interface ProjectDirectoryTabProps {
        listProjectDirectory: (path?: string) => Promise<ProjectDirectoryEntry[]>;
        buildProjectDirectoryDownloadUrl: (path?: string) => string | null;
        buildProjectDirectoryPreviewUrl: (path: string) => string | null;
}

export function ProjectDirectoryTab({
        listProjectDirectory,
        buildProjectDirectoryDownloadUrl,
        buildProjectDirectoryPreviewUrl,
}: ProjectDirectoryTabProps) {
        return (
                <ProjectDirectoryBrowser
                        listProjectDirectory={listProjectDirectory}
                        buildProjectDirectoryDownloadUrl={buildProjectDirectoryDownloadUrl}
                        buildProjectDirectoryPreviewUrl={buildProjectDirectoryPreviewUrl}
                />
        );
}
