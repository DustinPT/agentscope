import { Check, ChevronDown } from 'lucide-react';

import type { AgentRecord } from '@/api';
import { Button } from '@/components/ui/button';
import {
        DropdownMenu,
        DropdownMenuContent,
        DropdownMenuItem,
        DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface Props extends Omit<React.ComponentPropsWithoutRef<typeof Button>, 'onChange' | 'value'> {
        agents: AgentRecord[];
        value?: string | null;
        onChange?: (agentId: string) => void;
        placeholder?: string;
        className?: string;
}

export function AgentSelect({ agents, value, onChange, placeholder, className, ...props }: Props) {
        const { t } = useTranslation();
        const selected = agents.find((agent) => agent.id === value) ?? null;
        const displayLabel = selected
                ? selected.data.name
                : (placeholder ?? t('chat.agent.selectPlaceholder'));

        return (
                <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                                <Button
                                        variant="outline"
                                        size="sm"
                                        className={cn('justify-between gap-1 font-normal', className)}
                                        {...props}
                                >
                                        <span className="truncate">{displayLabel}</span>
                                        <ChevronDown className="size-3.5 text-muted-foreground" />
                                </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" className="min-w-56 max-h-72 overflow-y-auto">
                                {agents.length === 0 ? (
                                        <div className="px-2 py-3 text-center text-sm text-muted-foreground">
                                                <p className="font-medium">{t('chat.agent.emptyTitle')}</p>
                                                <p className="mt-1 text-xs">{t('chat.agent.emptyDescription')}</p>
                                        </div>
                                ) : (
                                        agents.map((agent) => {
                                                const isSelected = agent.id === value;
                                                return (
                                                        <DropdownMenuItem
                                                                key={agent.id}
                                                                onSelect={() => onChange?.(agent.id)}
                                                        >
                                                                <Check
                                                                        className={`size-3.5 shrink-0 ${isSelected ? 'opacity-100' : 'opacity-0'}`}
                                                                />
                                                                <span className="min-w-0 flex-1 truncate">
                                                                        {agent.data.name}
                                                                </span>
                                                        </DropdownMenuItem>
                                                );
                                        })
                                )}
                        </DropdownMenuContent>
                </DropdownMenu>
        );
}
