import type { ContentBlock, DataBlock, Msg, TextBlock } from '@agentscope-ai/agentscope/message';

export interface UserMessageOutlineItem {
	messageId: string;
	title: string;
	index: number;
}

function normalizeOutlineText(text: string): string {
	return text.replace(/\s+/g, ' ').trim();
}

function truncateOutlineText(text: string, length: number): string {
	return Array.from(text).slice(0, length).join('');
}

function getTextFromHintContent(hint: string | Array<TextBlock | DataBlock>): string {
	if (typeof hint === 'string') {
		return hint;
	}
	return hint
		.filter((block): block is TextBlock => block.type === 'text')
		.map((block) => block.text)
		.join(' ');
}

function getOutlineTextFromBlocks(content: ContentBlock[]): string {
	return content
		.map((block) => {
			if (block.type === 'text') {
				return block.text;
			}
			if (block.type === 'hint') {
				return getTextFromHintContent(block.hint);
			}
			return '';
		})
		.filter((text) => text.length > 0)
		.join(' ');
}

export function buildUserMessageOutline(
	msgs: Msg[],
	getFallbackTitle: (index: number) => string,
): UserMessageOutlineItem[] {
	const userMessages = msgs.filter(
		(message) =>
			message.role === 'user' || message.content.some((block) => block.type === 'hint'),
	);

	return userMessages.map((message, messageIndex) => {
		const index = messageIndex + 1;
		const textContent = getOutlineTextFromBlocks(message.content);
		const normalizedText = normalizeOutlineText(textContent);

		return {
			messageId: message.id,
			title:
				normalizedText.length > 0
					? truncateOutlineText(normalizedText, 500)
					: getFallbackTitle(index),
			index,
		};
	});
}
