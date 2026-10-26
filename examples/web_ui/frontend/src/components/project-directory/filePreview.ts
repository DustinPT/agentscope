import type { WorkspaceFileEntry } from '@/api';

const IMAGE_EXTENSIONS = new Set([
        '.apng',
        '.avif',
        '.bmp',
        '.gif',
        '.ico',
        '.jpeg',
        '.jpg',
        '.png',
        '.svg',
        '.webp',
]);

const TEXT_EXTENSIONS = new Set([
        '.bat',
        '.c',
        '.cc',
        '.cfg',
        '.conf',
        '.cpp',
        '.cs',
        '.css',
        '.csv',
        '.env',
        '.go',
        '.h',
        '.hpp',
        '.html',
        '.ini',
        '.java',
        '.js',
        '.json',
        '.json5',
        '.jsonc',
        '.jsonl',
        '.jsx',
        '.log',
        '.mjs',
        '.md',
        '.mdx',
        '.markdown',
        '.properties',
        '.py',
        '.rb',
        '.rs',
        '.sh',
        '.sql',
        '.text',
        '.toml',
        '.ts',
        '.tsx',
        '.txt',
        '.vue',
        '.xml',
        '.yaml',
        '.yml',
]);

function getExtension(name?: string | null): string {
        if (!name) {
                return '';
        }
        const index = name.lastIndexOf('.');
        return index >= 0 ? name.slice(index).toLowerCase() : '';
}

export function isImagePreviewable(entry: WorkspaceFileEntry | null): boolean {
        if (!entry || entry.is_dir) {
                return false;
        }
        return (
                entry.mime_type?.startsWith('image/') === true ||
                IMAGE_EXTENSIONS.has(getExtension(entry.name))
        );
}

export function isMarkdownPreviewable(entry: WorkspaceFileEntry | null): boolean {
        if (!entry || entry.is_dir) {
                return false;
        }
        const extension = getExtension(entry.name);
        return (
                entry.mime_type === 'text/markdown' ||
                extension === '.md' ||
                extension === '.mdx' ||
                extension === '.markdown'
        );
}

export function isJsonPreviewable(entry: WorkspaceFileEntry | null): boolean {
        if (!entry || entry.is_dir) {
                return false;
        }
        const extension = getExtension(entry.name);
        return (
                entry.mime_type === 'application/json' ||
                entry.mime_type?.endsWith('+json') === true ||
                extension === '.json' ||
                extension === '.json5' ||
                extension === '.jsonc' ||
                extension === '.jsonl'
        );
}

export function isTextPreviewable(entry: WorkspaceFileEntry | null): boolean {
        if (!entry || entry.is_dir) {
                return false;
        }
        return (
                entry.mime_type?.startsWith('text/') === true ||
                isJsonPreviewable(entry) ||
                isMarkdownPreviewable(entry) ||
                TEXT_EXTENSIONS.has(getExtension(entry.name))
        );
}

export function isFilePreviewable(entry: WorkspaceFileEntry | null): boolean {
        return isImagePreviewable(entry) || isTextPreviewable(entry);
}
