import { Check, ChevronDown, Plus, Trash2 } from 'lucide-react';

import type { AgentRecord, ChannelBinding, SessionScope } from '@/api';
import { AgentSelect } from '@/components/select/AgentSelect';
import { Button } from '@/components/ui/button';
import {
        DropdownMenu,
        DropdownMenuContent,
        DropdownMenuItem,
        DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { useTranslation } from '@/i18n/useI18n';

const SESSION_SCOPES: SessionScope[] = ['per_chat', 'per_chat_user'];

function ScopeSelect({
        value,
        onChange,
}: {
        value: SessionScope;
        onChange: (scope: SessionScope) => void;
}) {
        const { t } = useTranslation();
        return (
                <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                                <Button
                                        variant="outline"
                                        size="default"
                                        className="w-full justify-between gap-1 font-normal"
                                >
                                        <span className="truncate">{t(`channel.sessionScope.${value}`)}</span>
                                        <ChevronDown className="size-3.5 text-muted-foreground" />
                                </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" className="min-w-40">
                                {SESSION_SCOPES.map((scope) => (
                                        <DropdownMenuItem key={scope} onSelect={() => onChange(scope)}>
                                                <Check
                                                        className={`size-3.5 shrink-0 ${scope === value ? 'opacity-100' : 'opacity-0'}`}
                                                />
                                                <span className="flex-1">{t(`channel.sessionScope.${scope}`)}</span>
                                        </DropdownMenuItem>
                                ))}
                        </DropdownMenuContent>
                </DropdownMenu>
        );
}

interface Props {
        value: ChannelBinding[];
        onChange: (bindings: ChannelBinding[]) => void;
        agents: AgentRecord[];
}

export function BindingsEditor({ value, onChange, agents }: Props) {
        const { t } = useTranslation();

        const update = (index: number, patch: Partial<ChannelBinding>) => {
                onChange(value.map((binding, idx) => (idx === index ? { ...binding, ...patch } : binding)));
        };

        const removeRule = (index: number) => {
                        onChange(value.filter((_, idx) => idx !== index));
        };

        const addRule = () => {
                const catchAll = value[value.length - 1];
                const rule: ChannelBinding = {
                        match_key: 'chat_id',
                        match_value: '',
                        agent_id: catchAll?.agent_id ?? agents[0]?.id ?? '',
                        session_scope: 'per_chat',
                };
                onChange([...value.slice(0, -1), rule, ...value.slice(-1)]);
        };

        const targets = (binding: ChannelBinding, index: number) => (
                <div className="grid grid-cols-2 gap-2.5">
                        <div className="flex flex-col gap-1">
                                <span className="text-xs text-muted-foreground">{t('channel.binding.routeTo')}</span>
                                <AgentSelect
                                        size="default"
                                        className="w-full"
                                        agents={agents}
                                        value={binding.agent_id || null}
                                        onChange={(agentId) => update(index, { agent_id: agentId })}
                                />
                        </div>
                        <div className="flex flex-col gap-1">
                                <span className="text-xs text-muted-foreground">{t('channel.binding.scope')}</span>
                                <ScopeSelect
                                        value={binding.session_scope}
                                        onChange={(scope) => update(index, { session_scope: scope })}
                                />
                        </div>
                </div>
        );

        return (
                <div className="flex flex-col gap-2.5">
                        {value.map((binding, index) => {
                                const isCatchAll = index === value.length - 1;
                                if (isCatchAll) {
                                        return (
                                                <div
                                                        key={index}
                                                        className="flex flex-col gap-2.5 rounded-lg border border-dashed bg-muted/30 p-3"
                                                >
                                                        <div>
                                                                <span className="text-xs font-medium">
                                                                        {t('channel.binding.fallback')}
                                                                </span>
                                                                <p className="mt-0.5 text-xs text-muted-foreground">
                                                                        {t('channel.binding.fallbackDesc')}
                                                                </p>
                                                        </div>
                                                        {targets(binding, index)}
                                                </div>
                                        );
                                }

                                return (
                                        <div key={index} className="flex flex-col gap-2.5 rounded-lg border p-3">
                                                <div className="flex items-center justify-between">
                                                        <span className="text-xs font-medium text-muted-foreground">
                                                                {t('channel.binding.rule')} {index + 1}
                                                        </span>
                                                        <Button
                                                                size="icon-sm"
                                                                variant="ghost"
                                                                className="-mr-1 size-6 text-destructive"
                                                                onClick={() => removeRule(index)}
                                                        >
                                                                <Trash2 className="size-3.5" />
                                                        </Button>
                                                </div>
                                                <div className="grid grid-cols-2 gap-2.5">
                                                        <div className="flex flex-col gap-1">
                                                                <span className="text-xs text-muted-foreground">
                                                                        {t('channel.binding.matchKey')}
                                                                </span>
                                                                <Input
                                                                        className="font-mono text-xs"
                                                                        value={binding.match_key}
                                                                        onChange={(e) =>
                                                                                update(index, { match_key: e.target.value })
                                                                        }
                                                                        placeholder="chat_id"
                                                                />
                                                        </div>
                                                        <div className="flex flex-col gap-1">
                                                                <span className="text-xs text-muted-foreground">
                                                                        {t('channel.binding.matchValue')}
                                                                </span>
                                                                <Input
                                                                        className="font-mono text-xs"
                                                                        value={binding.match_value}
                                                                        onChange={(e) =>
                                                                                update(index, { match_value: e.target.value })
                                                                        }
                                                                        placeholder={t(
                                                                                'channel.binding.matchValuePlaceholder',
                                                                        )}
                                                                />
                                                        </div>
                                                </div>
                                                {targets(binding, index)}
                                        </div>
                                );
                        })}

                        <Button variant="ghost" size="sm" className="self-start" onClick={addRule}>
                                <Plus className="size-3.5" />
                                {t('channel.binding.addRule')}
                        </Button>
                </div>
        );
}
