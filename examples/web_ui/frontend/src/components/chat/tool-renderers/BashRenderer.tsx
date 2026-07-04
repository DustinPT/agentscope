/* eslint-disable react-refresh/only-export-components -- renderer constant is co-located with its inline component by design */
import type { ToolResultBlock } from '@agentscope-ai/agentscope/message';
import * as mime from 'mime-types';
import { useState } from 'react';

import { CornerLine, ToolStateIcon } from './_shared';
import type { TFunction, ToolCallWithResult, ToolRenderer } from './types';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';

function parseInput(input: string): Record<string, unknown> {
	try {
		return JSON.parse(input);
	} catch {
		return {};
	}
}

export const BashRenderer: ToolRenderer = {
	getDisplayName: (_call, t) => t('tool.bash.name'),

	renderCallArgs: (call) => {
		const { command } = parseInput(call.input) as { command?: string };
		return command || call.input;
	},

	renderResult: (call, result, t) => {
		if (call.state === 'asking' || !result || result.state === 'running') {
			return t('common.running');
		}
		if (result.state === 'interrupted') {
			return t('common.interrupted');
		}
		return undefined;
	},

	renderConfirmBody: (call, t) => {
		const { command, description } = parseInput(call.input) as {
			command?: string;
			description?: string;
		};
		return (
			<div className="w-full max-w-full">
				<div className="text-xs text-muted-foreground">{t('tool.bash.command')}</div>
				<div className="text-secondary-foreground font-mono whitespace-pre-wrap break-all">
					{command || call.input}
				</div>
				{description && (
					<>
						<div className="mt-2 text-xs text-muted-foreground">
							{t('tool.bash.description')}
						</div>
						<div className="text-muted-foreground whitespace-pre-wrap break-all">
							{description}
						</div>
					</>
				)}
			</div>
		);
	},

	renderGroup: (calls, t) => <BashGroup calls={calls} t={t} />,
};

function getCommand(input: string): string {
	const { command } = parseInput(input) as { command?: string };
	return command || input;
}

function getDescription(input: string): string {
	const { description } = parseInput(input) as { description?: string };
	return description || '';
}

function stringifyResultOutput(result?: ToolResultBlock): string {
	if (!result) return '';
	if (typeof result.output === 'string') return result.output;
	return result.output
		.map((block) => {
			if (block.type === 'text') return block.text;
			const mainType = block.source.media_type.split('/')[0].toUpperCase();
			const ext = (mime.extension(block.source.media_type) || 'bin').toLowerCase();
			return `[${mainType}.${ext}]`;
		})
		.join('\n');
}

function BashCallItem({
	call,
	result,
	t,
}: {
	call: ToolCallWithResult['call'];
	result?: ToolCallWithResult['result'];
	t: TFunction;
}) {
	const [open, setOpen] = useState(false);
	const command = getCommand(call.input);
	const description = getDescription(call.input);
	const summary = description || command;
	const output = stringifyResultOutput(result);
	const isRunning = call.state === 'asking' || !result || result.state === 'running';
	const isInterrupted = result?.state === 'interrupted';
	const hasOutput = output.length > 0;

	return (
		<Collapsible open={open} onOpenChange={setOpen} className="flex flex-col w-full">
			<CollapsibleTrigger className="flex flex-row gap-x-2 w-full max-w-full items-center cursor-pointer text-left">
				<ToolStateIcon states={[result?.state]} />
				<span className="text-sm flex-1 min-w-0 truncate">
					<strong className="text-primary">{t('tool.bash.name')}</strong>
					{summary && (
						<span className={`ml-1 ${description ? '' : 'font-mono'}`}>
							{summary}
						</span>
					)}
					{!open && <span className="text-muted-foreground"> ...</span>}
				</span>
			</CollapsibleTrigger>
			<CollapsibleContent className="pl-6 pt-2">
				<div className="flex flex-row gap-x-2 w-full max-w-full">
					<CornerLine className="w-3 h-6" />
					<div className="flex flex-col gap-y-3 flex-1 min-w-0 text-sm">
						<div className="min-w-0">
							<div className="text-xs text-muted-foreground mb-1">
								{t('tool.bash.command')}
							</div>
							<pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-all rounded-md bg-muted/40 px-3 py-2 font-mono text-xs text-foreground">
								{command}
							</pre>
						</div>
						{description && (
							<div className="min-w-0">
								<div className="text-xs text-muted-foreground mb-1">
									{t('tool.bash.description')}
								</div>
								<div className="whitespace-pre-wrap break-all text-muted-foreground">
									{description}
								</div>
							</div>
						)}
						<div className="min-w-0">
							<div className="text-xs text-muted-foreground mb-1">
								{t('tool.bash.output')}
							</div>
							{isRunning ? (
								<div className="text-muted-foreground">{t('common.running')}</div>
							) : isInterrupted ? (
								<div className="text-muted-foreground">
									{t('common.interrupted')}
								</div>
							) : hasOutput ? (
								<pre className="max-h-96 max-w-full overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/40 px-3 py-2 font-mono text-xs text-foreground">
									{output}
								</pre>
							) : (
								<div className="text-muted-foreground">
									{t('tool.bash.noOutput')}
								</div>
							)}
						</div>
					</div>
				</div>
			</CollapsibleContent>
		</Collapsible>
	);
}

function BashGroup({ calls, t }: { calls: ToolCallWithResult[]; t: TFunction }) {
	return (
		<div className="flex flex-col gap-y-2 w-full">
			{calls.map(({ call, result }) => (
				<BashCallItem key={call.id} call={call} result={result} t={t} />
			))}
		</div>
	);
}
