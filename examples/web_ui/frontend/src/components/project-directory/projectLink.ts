export const WORKSPACE_FILE_SCHEME = 'workspace-file://';
export const WORKSPACE_DIRECTORY_SCHEME = 'workspace-dir://';

export type WorkspaceLinkTarget =
        | {
                  kind: 'file';
                  path: string;
          }
        | {
                  kind: 'directory';
                  path: string;
          };

function normalizeWorkspacePath(rawPath: string): string | null {
        const decoded = decodeURIComponent(rawPath).replace(/\\/g, '/').replace(/^\/+/, '').trim();
        if (!decoded) {
                return null;
        }
        return decoded;
}

export function parseProjectLinkHref(href?: string | null): WorkspaceLinkTarget | null {
        if (!href) {
                return null;
        }

        if (href.startsWith(WORKSPACE_FILE_SCHEME)) {
                const path = normalizeWorkspacePath(href.slice(WORKSPACE_FILE_SCHEME.length));
                return path ? { kind: 'file', path } : null;
        }

        if (href.startsWith(WORKSPACE_DIRECTORY_SCHEME)) {
                const path = normalizeWorkspacePath(href.slice(WORKSPACE_DIRECTORY_SCHEME.length));
                return path ? { kind: 'directory', path } : null;
        }

        return null;
}
