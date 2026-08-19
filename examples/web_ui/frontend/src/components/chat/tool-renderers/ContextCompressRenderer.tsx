/* eslint-disable react-refresh/only-export-components -- renderer constant is co-located with its inline component by design */
import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import { ChevronDownIcon } from 'lucide-react';
import { useState } from 'react';

import { ToolStateIcon } from './_shared';
import type { TFunction, ToolCallWithResult, ToolRenderer } from './types';
import { MarkdownRenderer } from '@/components/markdown/MarkdownRenderer';
import { Badge } from '@/components/ui/badge';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { formatNumber } from '@/utils/common';

interface ContextCompressCallInput {
	estimated_tokens?: number;
	input_tokens?: number;
	threshold_tokens?: number;
	compressed_message_count?: number;
	reserved_message_count?: number;
}

interface ContextCompressResultOutput {
	input_tokens?: number;
	output_tokens?: number;
	summary?: string;
	offload_path?: string | null;
}

function parseJson<T>(value: string): T | null {
	try {
		return JSON.parse(value) as T;
	} catch {
		return null;
	}
}

function getCallInput(call: ToolCallBlock): ContextCompressCallInput {
	return parseJson<ContextCompressCallInput>(call.input) ?? {};
}

function getResultOutputText(result?: ToolResultBlock): string {
	if (!result) return '';
	if (typeof result.output === 'string') return result.output;
	if (Array.isArray(result.output)) {
		return result.output
			.map((block) => (block.type === 'text' ? block.text : ''))
			.filter(Boolean)
			.join('\n');
	}
	return '';
}

function getResultOutput(result?: ToolResultBlock): ContextCompressResultOutput {
	const outputText = getResultOutputText(result);
	if (!outputText) return {};
	return parseJson<ContextCompressResultOutput>(outputText) ?? {};
}

function getSummary(result?: ToolResultBlock): string {
	const output = getResultOutput(result);
	if (output.summary) return output.summary;
	const outputText = getResultOutputText(result);
	if (outputText) return outputText;
	return '';
}

function MetricBadge({
	label,
	tooltip,
}: {
	label: string;
	tooltip?: string;
}) {
	const badge = <Badge variant="secondary">{label}</Badge>;
	if (!tooltip) return badge;
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<span className="inline-flex cursor-help">{badge}</span>
			</TooltipTrigger>
			<TooltipContent side="top" sideOffset={6}>
				<p>{tooltip}</p>
			</TooltipContent>
		</Tooltip>
	);
}

function ContextCompressGroup({
	calls,
	t,
}: {
	calls: ToolCallWithResult[];
	t: TFunction;
}) {
	const [openIds, setOpenIds] = useState<Record<string, boolean>>({});

	return (
		<div className="flex flex-col gap-y-2 w-full">
			{calls.map(({ call, result }) => {
				const input = getCallInput(call);
				const output = getResultOutput(result);
				const summary = getSummary(result);
				const isOpen = !!openIds[call.id];

				return (
					<div
						key={call.id}
						className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-sm"
					>
						<div className="flex items-center gap-x-2 min-w-0">
							<ToolStateIcon states={[result?.state]} />
							<strong className="truncate text-primary">
								{t('tool.contextCompress.name')}
							</strong>
						</div>

						<div className="mt-2 flex flex-wrap gap-2">
							{typeof input.estimated_tokens === 'number' && (
								<MetricBadge
									label={t('tool.contextCompress.estimatedTokens', {
										count: formatNumber(input.estimated_tokens),
									})}
									tooltip={t('tool.contextCompress.estimatedTokensTip')}
								/>
							)}
							{typeof input.threshold_tokens === 'number' && (
								<MetricBadge
									label={t('tool.contextCompress.thresholdTokens', {
										count: formatNumber(input.threshold_tokens),
									})}
									tooltip={t('tool.contextCompress.thresholdTokensTip')}
								/>
							)}
							{typeof input.compressed_message_count === 'number' && (
								<MetricBadge
									label={t('tool.contextCompress.compressedMessages', {
										count: formatNumber(input.compressed_message_count),
									})}
								/>
							)}
							{typeof input.reserved_message_count === 'number' && (
								<MetricBadge
									label={t('tool.contextCompress.reservedMessages', {
										count: formatNumber(input.reserved_message_count),
									})}
								/>
							)}
						</div>

						<Collapsible
							open={isOpen}
							onOpenChange={(nextOpen) =>
								setOpenIds((prev) => ({ ...prev, [call.id]: nextOpen }))
							}
							className="mt-2"
						>
							<CollapsibleTrigger className="flex w-full items-center gap-x-2 text-left text-xs text-muted-foreground">
								<span>{t('tool.contextCompress.details')}</span>
								<ChevronDownIcon
									className={`size-4 transition-transform ${isOpen ? 'rotate-180' : ''}`}
								/>
							</CollapsibleTrigger>
							<CollapsibleContent className="pt-2">
								<div className="flex flex-wrap gap-2">
									{typeof input.input_tokens === 'number' && (
										<MetricBadge
											label={t('tool.contextCompress.inputTokens', {
												count: formatNumber(input.input_tokens),
											})}
											tooltip={t('tool.contextCompress.inputTokensTip')}
										/>
									)}
									{typeof output.input_tokens === 'number' && (
										<MetricBadge
											label={t('tool.contextCompress.actualInputTokens', {
												count: formatNumber(output.input_tokens),
											})}
										/>
									)}
									{typeof output.output_tokens === 'number' && (
										<MetricBadge
											label={t('tool.contextCompress.actualOutputTokens', {
												count: formatNumber(output.output_tokens),
											})}
										/>
									)}
								</div>
								{summary && (
									<div className="prose prose-sm max-w-none rounded-md bg-background px-3 py-2 text-foreground dark:prose-invert">
                                                                                <MarkdownRenderer>
											{summary}
                                                                                </MarkdownRenderer>
									</div>
								)}
							</CollapsibleContent>
						</Collapsible>
					</div>
				);
			})}
		</div>
	);
}

export const ContextCompressRenderer: ToolRenderer = {
	getDisplayName: (_call, t) => t('tool.contextCompress.name'),
	renderCallArgs: () => null,
	renderResult: () => null,
	renderGroup: (calls, t) => <ContextCompressGroup calls={calls} t={t} />,
};
