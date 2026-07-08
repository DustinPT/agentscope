import type { ContentBlock } from '@agentscope-ai/agentscope/message';

const TEMP_TITLE_MAX_LENGTH = 40;

function truncateText(value: string, maxLength: number): string {
	if (value.length <= maxLength) return value;
	return `${value.slice(0, maxLength - 1).trimEnd()}…`;
}

export async function buildContentBlockFromFile(file: File): Promise<ContentBlock | null> {
	const filePath = (file as File & { path?: string }).path;
	if (filePath) {
		return {
			id: crypto.randomUUID(),
			type: 'data' as const,
			source: {
				type: 'url' as const,
				url: `file://${filePath}`,
				media_type: file.type || 'application/octet-stream',
			},
			name: file.name,
		};
	}

	if (file.type === 'text/plain') {
		const text = await file.text();
		return {
			id: crypto.randomUUID(),
			type: 'text' as const,
			text: `[File: ${file.name}]\n${text}`,
		};
	}

	const buffer = await file.arrayBuffer();
	const bytes = new Uint8Array(buffer);
	let binary = '';
	for (let i = 0; i < bytes.byteLength; i += 1) {
		binary += String.fromCharCode(bytes[i]);
	}
	const base64 = btoa(binary);
	return {
		id: crypto.randomUUID(),
		type: 'data' as const,
		source: {
			type: 'base64' as const,
			media_type: file.type || 'application/octet-stream',
			data: base64,
		},
		name: file.name,
	};
}

export function buildTemporarySessionTitle(
	content: ContentBlock[],
	fallback = '新会话',
): string {
	const text = content
		.filter((block): block is Extract<ContentBlock, { type: 'text' }> => block.type === 'text')
		.map((block) => block.text)
		.join(' ')
		.replace(/\s+/g, ' ')
		.trim();

	if (text) {
		return truncateText(text, TEMP_TITLE_MAX_LENGTH);
	}

	const firstAttachment = content.find(
		(block): block is Extract<ContentBlock, { type: 'data'; name?: string }> =>
			block.type === 'data',
	);
	if (firstAttachment?.name) {
		return truncateText(firstAttachment.name.trim(), TEMP_TITLE_MAX_LENGTH);
	}

	return fallback;
}

export function getSupportedInputTypes(inputTypes: string[] | undefined): string[] {
	return (inputTypes ?? []).filter(
		(type) =>
			/^(image|video|audio|text)\/.+/.test(type) ||
			type === 'application/pdf' ||
			type.startsWith('application/vnd.') ||
			type.startsWith('application/msword') ||
			type.startsWith('application/vnd.openxmlformats'),
	);
}
