import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import type { ReactNode } from 'react';

import type { TFunction, ToolRenderer } from './types';

type SandboxAccessInput = {
        resource_type?: 'domain' | 'path';
        pattern?: string;
        operations?: string[];
        scope?: 'workspace' | 'agent' | 'user';
        reason?: string;
};

function parseInput(input: string): SandboxAccessInput {
        try {
                return JSON.parse(input) as SandboxAccessInput;
        } catch {
                return {};
        }
}

function renderPermissionSummary(input: SandboxAccessInput, t: TFunction): ReactNode {
        const pattern = input.pattern || t('tool.requestSandboxAccess.unknownTarget');
        const hasWrite = input.operations?.includes('write') ?? false;
        const summaryKey =
                input.resource_type === 'domain'
                        ? 'tool.requestSandboxAccess.summary.domainConnect'
                        : hasWrite
                          ? 'tool.requestSandboxAccess.summary.pathReadWrite'
                          : 'tool.requestSandboxAccess.summary.pathRead';

        return (
                <div className="space-y-2 text-sm leading-6">
                        <p className="text-foreground">
                                {t(summaryKey, {
                                        target: pattern,
                                })}
                        </p>
                        {input.reason && (
                                <p className="text-muted-foreground">
                                        {t('tool.requestSandboxAccess.summary.reason', {
                                                reason: input.reason,
                                        })}
                                </p>
                        )}
                </div>
        );
}

export const RequestSandboxAccessRenderer: ToolRenderer = {
        getDisplayName: (_call, t) => t('tool.requestSandboxAccess.name'),
        renderConfirmBody: (call: ToolCallBlock, t) => {
                const input = parseInput(call.input);
                return renderPermissionSummary(input, t);
        },
};
