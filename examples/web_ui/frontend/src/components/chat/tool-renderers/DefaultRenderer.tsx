import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import * as mime from 'mime-types';
import type { ReactNode } from 'react';

import { DefaultToolCallList } from './DefaultRenderer.view';
import type { TFunction, ToolCallWithResult } from './types';

function processToolInput(input: string): string {
	try {
		const obj = JSON.parse(input);
                return JSON.stringify(obj, null, 2);
	} catch {
		return input;
	}
}

export function defaultGetDisplayName(call: ToolCallBlock): string {
	return call.name;
}

export function defaultRenderCallArgs(call: ToolCallBlock): ReactNode {
	if (call.input.length <= 2) return null;
	return processToolInput(call.input);
}

export function defaultRenderResult(
	call: ToolCallBlock,
	result: ToolResultBlock,
	t: TFunction,
): ReactNode {
	if (call.state === 'asking' || !result || result.state === 'running') {
		return <span>{t('common.running')} ...</span>;
	}
	if (result.state === 'interrupted') {
		return <span>{t('common.interrupted')}</span>;
	}

	if (typeof result.output === 'string') {
                return (
                        <pre className="max-h-96 max-w-full overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/40 px-3 py-2 font-mono text-xs text-foreground">
                                {result.output}
                        </pre>
                );
	} else {
                return (
                        <div className="flex flex-col gap-y-2">
                                {result.output.map((block, index) => {
                                        if (block.type === 'text') {
                                                return (
                                                        <pre
                                                                key={`${block.id}-${index}`}
                                                                className="max-h-96 max-w-full overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/40 px-3 py-2 font-mono text-xs text-foreground"
                                                        >
                                                                {block.text}
                                                        </pre>
                                                );
                                        }

                                        const mainType = block.source.media_type.split('/')[0].toUpperCase();
                                        const ext =
                                                (mime.extension(block.source.media_type) || 'bin').toLowerCase();
                                        return (
                                                <div
                                                        key={`${block.id}-${index}`}
                                                        className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground"
                                                >
                                                        {block.name || `[${mainType}.${ext}]`}
                                                </div>
                                        );
                                })}
                        </div>
                );
	}
}

export function defaultRenderConfirmBody(call: ToolCallBlock): ReactNode {
	return (
		<div className="w-full max-w-full overflow-hidden text-ellipsis truncate">
			<div className="text-secondary-foreground">{call.input}</div>
		</div>
	);
}

/**
 * Default group layout: each call is an independent block with a state icon,
 * `displayName(args)` header line, and (optionally) a corner-line-prefixed
 * result block beneath it. Used by tools without a custom `renderGroup`,
 * e.g. arbitrary MCP tools.
 *
 * `getDisplayName` / `renderCallArgs` / `renderResult` are passed in from
 * `index.ts` so this function stays decoupled from the renderer registry.
 */
export function defaultRenderGroup(
	calls: ToolCallWithResult[],
        t: TFunction,
	resolvers: {
		getDisplayName: (call: ToolCallBlock) => string;
		renderCallArgs: (call: ToolCallBlock) => ReactNode;
		renderResult: (call: ToolCallBlock, result: ToolResultBlock) => ReactNode;
	},
): ReactNode {
        return <DefaultToolCallList calls={calls} t={t} resolvers={resolvers} />;
}
