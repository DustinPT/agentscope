function sanitizeFilenamePart(value: string): string {
        const normalized = value.trim().replace(/[\\/:*?"<>|]+/g, '-').replace(/\s+/g, '-');
        return normalized || 'unknown';
}

export function buildSessionExportFilename(
        agentName: string,
        sessionName: string,
        exportedAt: string,
): string {
        const timestamp = exportedAt.replace(/[:.]/g, '-');
        return `${sanitizeFilenamePart(agentName)}-${sanitizeFilenamePart(sessionName)}-${timestamp}.json`;
}

export function downloadJsonFile(data: unknown, filename: string): void {
        const blob = new Blob([JSON.stringify(data, null, 2)], {
                type: 'application/json;charset=utf-8',
        });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
}
