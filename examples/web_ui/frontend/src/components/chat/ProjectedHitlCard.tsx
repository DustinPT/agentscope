import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import { CalendarClock, GitBranch, Users } from 'lucide-react';

import { ConfirmCard } from './ConfirmCard';
import { ExternalExecutionCard } from './ExternalExecutionCard';
import type { ProjectedHitlEntry, ToolResultMetadata } from '@/hooks/useMessages';
import { useTranslation } from '@/i18n/useI18n';

function renderProjectionIcon(source: ProjectedHitlEntry['projection_source']) {
        switch (source) {
                case 'schedule':
                        return <CalendarClock className="size-4 shrink-0" />;
                case 'mixed':
                        return <GitBranch className="size-4 shrink-0" />;
                default:
                        return <Users className="size-4 shrink-0" />;
        }
}

export function ProjectedHitlCard({
        entry,
        onUserConfirm,
        onExternalExecutionSubmit,
}: {
        entry: ProjectedHitlEntry;
        onUserConfirm: (
                toolCall: ToolCallBlock,
                confirm: boolean,
                rules?: ToolCallBlock['suggested_rules'],
        ) => Promise<void>;
        onExternalExecutionSubmit: (
                toolCall: ToolCallBlock,
                output: string,
                state?: ToolResultBlock['state'],
                metadata?: ToolResultMetadata,
        ) => Promise<void>;
}) {
        const { t } = useTranslation();
        const toolCalls = entry.event.tool_calls ?? [];

        if (toolCalls.length === 0) return null;

        return (
                <div className="ring ring-border rounded-[28px] w-full p-3 space-y-3 bg-card">
                        <div className="flex items-center gap-2 px-2 text-sm font-medium text-secondary-foreground">
                                {renderProjectionIcon(entry.projection_source)}
                                <span>
                                        {t('chat.projectedHitlTitle', {
                                                name: entry.leaf_agent_name,
                                        })}
                                </span>
                        </div>
                        <div className="px-2 text-xs text-muted-foreground">
                                {t(`chat.projectedHitlSource.${entry.projection_source}`)}
                        </div>
                        <div className="space-y-2">
                                {toolCalls.map((toolCall) =>
                                        entry.event_type === 'require_user_confirm' ? (
                                                <ConfirmCard
                                                        key={toolCall.id}
                                                        toolCall={toolCall}
                                                        onUserConfirm={(confirm, confirmedToolCall, rules) =>
                                                                onUserConfirm(
                                                                        confirmedToolCall ?? toolCall,
                                                                        confirm,
                                                                        rules,
                                                                )
                                                        }
                                                />
                                        ) : (
                                                <ExternalExecutionCard
                                                        key={toolCall.id}
                                                        toolCall={toolCall}
                                                        title={t('chat.projectedExternalExecutionTitle', {
                                                                name: entry.leaf_agent_name,
                                                        })}
                                                        onSubmit={(output, state, metadata) =>
                                                                onExternalExecutionSubmit(
                                                                        toolCall,
                                                                        output,
                                                                        state,
                                                                        metadata,
                                                                )
                                                        }
                                                />
                                        ),
                                )}
                        </div>
                </div>
        );
}
