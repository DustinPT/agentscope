import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import type { ReactNode } from 'react';
import { useState } from 'react';

import { CornerLine, ToolStateIcon } from './_shared';
import type { TFunction, ToolCallWithResult } from './types';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';

function hasDetailContent(content: ReactNode): boolean {
        return content !== null && content !== undefined && content !== false && content !== '';
}

function DetailSection({
        label,
        content,
        emptyText,
}: {
        label: string;
        content: ReactNode;
        emptyText: string;
}) {
        const hasContent = hasDetailContent(content);

        return (
                <div className="flex flex-col gap-y-1 min-w-0">
                        <div className="text-xs text-muted-foreground">{label}</div>
                        {hasContent ? (
                                typeof content === 'string' || typeof content === 'number' ? (
                                        <pre className="max-h-96 max-w-full overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/40 px-3 py-2 font-mono text-xs text-foreground">
                                                {content}
                                        </pre>
                                ) : (
                                        content
                                )
                        ) : (
                                <div className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
                                        {emptyText}
                                </div>
                        )}
                </div>
        );
}

function toInlineSummary(content: ReactNode): string {
        if (content === null || content === undefined || content === false) return '';
        if (typeof content === 'string' || typeof content === 'number') {
                return String(content).replace(/\s+/g, ' ').trim();
        }
        return '';
}

function DefaultToolCallItem({
        call,
        result,
        t,
        resolvers,
}: {
        call: ToolCallBlock;
        result?: ToolResultBlock;
        t: TFunction;
        resolvers: {
                getDisplayName: (call: ToolCallBlock) => string;
                renderCallArgs: (call: ToolCallBlock) => ReactNode;
                renderResult: (call: ToolCallBlock, result: ToolResultBlock) => ReactNode;
        };
}) {
        const [open, setOpen] = useState(false);
        const displayName = resolvers.getDisplayName(call);
        const args = resolvers.renderCallArgs(call);
        const argsSummary = toInlineSummary(args);
        const output =
                result !== undefined ? resolvers.renderResult(call, result) : <span>{t('common.running')} ...</span>;

        return (
                <Collapsible open={open} onOpenChange={setOpen} className="flex flex-col w-full">
                        <CollapsibleTrigger className="flex flex-row gap-x-2 w-full max-w-full items-center cursor-pointer text-left">
                                <ToolStateIcon states={[result?.state]} />
                                <span className="text-sm flex-1 min-w-0 truncate">
                                        <strong className="text-primary">{displayName}</strong>
                                        {argsSummary && <span className="ml-1">{argsSummary}</span>}
                                        {!open && <span className="text-muted-foreground"> ...</span>}
                                </span>
                        </CollapsibleTrigger>
                        <CollapsibleContent className="pl-6 pt-2">
                                <div className="flex flex-row gap-x-2 w-full max-w-full">
                                        <CornerLine className="w-3 h-6" />
                                        <div className="flex flex-col gap-y-3 flex-1 min-w-0 text-sm">
                                                <DetailSection
                                                        label={t('tool.generic.input')}
                                                        content={args}
                                                        emptyText={t('tool.generic.noInput')}
                                                />
                                                <DetailSection
                                                        label={t('tool.generic.output')}
                                                        content={output}
                                                        emptyText={t('tool.generic.noOutput')}
                                                />
                                        </div>
                                </div>
                        </CollapsibleContent>
                </Collapsible>
        );
}

interface Props {
        calls: ToolCallWithResult[];
        t: TFunction;
        resolvers: {
                getDisplayName: (call: ToolCallBlock) => string;
                renderCallArgs: (call: ToolCallBlock) => ReactNode;
                renderResult: (call: ToolCallBlock, result: ToolResultBlock) => ReactNode;
        };
}

export function DefaultToolCallList({ calls, t, resolvers }: Props) {
        return (
                <div className="flex flex-col gap-y-2 w-full">
                        {calls.map(({ call, result }) => (
                                <DefaultToolCallItem
                                        key={call.id}
                                        call={call}
                                        result={result}
                                        t={t}
                                        resolvers={resolvers}
                                />
                        ))}
                </div>
        );
}
