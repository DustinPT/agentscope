export const PROJECT_FILE_SCHEME = 'project-file://';
export const PROJECT_DIRECTORY_SCHEME = 'project-dir://';

export type ProjectLinkTarget =
        | {
                  kind: 'file';
                  path: string;
          }
        | {
                  kind: 'directory';
                  path: string;
          };

function normalizeProjectPath(rawPath: string): string | null {
        const decoded = decodeURIComponent(rawPath).replace(/\\/g, '/').replace(/^\/+/, '').trim();
        if (!decoded) {
                return null;
        }
        return decoded;
}

export function parseProjectLinkHref(href?: string | null): ProjectLinkTarget | null {
        if (!href) {
                return null;
        }

        if (href.startsWith(PROJECT_FILE_SCHEME)) {
                const path = normalizeProjectPath(href.slice(PROJECT_FILE_SCHEME.length));
                return path ? { kind: 'file', path } : null;
        }

        if (href.startsWith(PROJECT_DIRECTORY_SCHEME)) {
                const path = normalizeProjectPath(href.slice(PROJECT_DIRECTORY_SCHEME.length));
                return path ? { kind: 'directory', path } : null;
        }

        return null;
}
