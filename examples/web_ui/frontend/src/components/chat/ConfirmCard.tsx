import type { ToolCallBlock } from '@agentscope-ai/agentscope/message';
import { ChevronRight } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { getDisplayName, renderConfirmBody } from './tool-renderers';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

type SelectOption = 'yes' | 'yes_with_rule' | 'no';
type SandboxScope = 'workspace' | 'agent' | 'user';
type SandboxOperationMode = 'read_only' | 'read_write';

const SANDBOX_ACCESS_TOOL_NAME = 'RequestSandboxAccess';

type SandboxAccessInput = {
        resource_type?: 'domain' | 'path';
        pattern?: string;
        operations?: string[];
        scope?: SandboxScope;
        reason?: string;
};

function parseSandboxAccessInput(toolCall: ToolCallBlock): SandboxAccessInput {
        try {
                return JSON.parse(toolCall.input) as SandboxAccessInput;
        } catch {
                return {};
        }
}

function isSandboxAccessTool(toolCall: ToolCallBlock): boolean {
        return toolCall.name === SANDBOX_ACCESS_TOOL_NAME;
}

export function ConfirmCard({
	toolCall,
	onUserConfirm,
}: {
	toolCall: ToolCallBlock;
        onUserConfirm: (
                confirm: boolean,
                confirmedToolCall?: ToolCallBlock,
                rules?: ToolCallBlock['suggested_rules'],
        ) => void;
}) {
	const { t } = useTranslation();
	const hasSuggestedRules = !!toolCall.suggested_rules?.length;
        const suggestedRule = toolCall.suggested_rules?.[0];
        const options = useMemo<SelectOption[]>(
                () => (hasSuggestedRules ? ['yes', 'yes_with_rule', 'no'] : ['yes', 'no']),
                [hasSuggestedRules],
        );
	const [selected, setSelected] = useState<SelectOption>('yes');
        const sandboxInput = useMemo(() => parseSandboxAccessInput(toolCall), [toolCall]);
        const isSandboxAccess = isSandboxAccessTool(toolCall);
        const [scope, setScope] = useState<SandboxScope>(sandboxInput.scope ?? 'workspace');
        const [pathMode, setPathMode] = useState<SandboxOperationMode>(
                sandboxInput.operations?.includes('write') ? 'read_write' : 'read_only',
        );

        useEffect(() => {
                setScope(sandboxInput.scope ?? 'workspace');
                setPathMode(sandboxInput.operations?.includes('write') ? 'read_write' : 'read_only');
        }, [sandboxInput]);

        const buildConfirmedToolCall = useMemo(() => {
                if (!isSandboxAccess) return toolCall;
                const nextInput: SandboxAccessInput = {
                        ...sandboxInput,
                        scope,
                        operations:
                                sandboxInput.resource_type === 'path'
                                        ? pathMode === 'read_write'
                                                ? ['read', 'write']
                                                : ['read']
                                        : ['connect'],
                };
                return {
                        ...toolCall,
                        input: JSON.stringify(nextInput),
                } satisfies ToolCallBlock;
        }, [isSandboxAccess, pathMode, sandboxInput, scope, toolCall]);

	useEffect(() => {
		const handleKeyDown = (e: KeyboardEvent) => {
			const currentIndex = options.indexOf(selected);
			switch (e.key) {
				case 'ArrowUp':
					e.preventDefault();
					setSelected(options[(currentIndex - 1 + options.length) % options.length]);
					break;
				case 'ArrowDown':
					e.preventDefault();
					setSelected(options[(currentIndex + 1) % options.length]);
					break;
				case 'Enter':
					e.preventDefault();
                                        if (selected === 'yes_with_rule' && suggestedRule) {
                                                onUserConfirm(true, buildConfirmedToolCall, [suggestedRule]);
					} else {
                                                onUserConfirm(selected === 'yes', buildConfirmedToolCall);
					}
					break;
			}
		};

		window.addEventListener('keydown', handleKeyDown);
		return () => window.removeEventListener('keydown', handleKeyDown);
        }, [buildConfirmedToolCall, onUserConfirm, options, selected, suggestedRule]);

	return (
		<div className="ring ring-border rounded-xl w-full p-4 space-y-4 text-sm overflow-hidden">
			<div className="flex flex-col gap-y-2">
				<strong className="text-secondary-foreground">{getDisplayName(toolCall, t)}</strong>
				<div className="px-4 py-2 bg-white rounded-sm">
                                        {renderConfirmBody(buildConfirmedToolCall, t)}
				</div>
			</div>
                        {isSandboxAccess && (
                                <div className="space-y-3">
                                        <div className="space-y-2">
                                                <strong className="text-secondary-foreground">
                                                        {t('confirmCard.sandboxAccess.scope')}
                                                </strong>
                                                <div className="flex flex-wrap gap-2">
                                                        {(['workspace', 'agent', 'user'] as SandboxScope[]).map((value) => (
                                                                <Button
                                                                        key={value}
                                                                        type="button"
                                                                        size="sm"
                                                                        variant={scope === value ? 'default' : 'outline'}
                                                                        onClick={(e) => {
                                                                                e.stopPropagation();
                                                                                e.preventDefault();
                                                                                setScope(value);
                                                                        }}
                                                                >
                                                                        {t(
                                                                                `confirmCard.sandboxAccess.scopeOptions.${value}`,
                                                                        )}
                                                                </Button>
                                                        ))}
                                                </div>
                                        </div>
                                        {sandboxInput.resource_type === 'path' && (
                                                <div className="space-y-2">
                                                        <strong className="text-secondary-foreground">
                                                                {t('confirmCard.sandboxAccess.operations')}
                                                        </strong>
                                                        <div className="flex flex-wrap gap-2">
                                                                <Button
                                                                        type="button"
                                                                        size="sm"
                                                                        variant={pathMode === 'read_only' ? 'default' : 'outline'}
                                                                        onClick={(e) => {
                                                                                e.stopPropagation();
                                                                                e.preventDefault();
                                                                                setPathMode('read_only');
                                                                        }}
                                                                >
                                                                        {t(
                                                                                'confirmCard.sandboxAccess.operationOptions.read_only',
                                                                        )}
                                                                </Button>
                                                                <Button
                                                                        type="button"
                                                                        size="sm"
                                                                        variant={pathMode === 'read_write' ? 'default' : 'outline'}
                                                                        onClick={(e) => {
                                                                                e.stopPropagation();
                                                                                e.preventDefault();
                                                                                setPathMode('read_write');
                                                                        }}
                                                                >
                                                                        {t(
                                                                                'confirmCard.sandboxAccess.operationOptions.read_write',
                                                                        )}
                                                                </Button>
                                                        </div>
                                                </div>
                                        )}
                                </div>
                        )}
			<div className="flex flex-col">
				<strong className="text-secondary-foreground mb-1">
					{t('chat.confirmToolCall')}
				</strong>
				<Button
					className={cn(
						'flex justify-start cursor-pointer',
						selected === 'yes' ? 'text-primary' : 'text-muted-foreground',
					)}
					size="sm"
					variant="ghost"
					onMouseEnter={() => setSelected('yes')}
					onClick={(e) => {
						e.stopPropagation();
						e.preventDefault();
                                                onUserConfirm(true, buildConfirmedToolCall);
					}}
				>
					<ChevronRight
						className={cn('size-4', selected === 'yes' ? 'visible' : 'invisible')}
					/>
					1. {t('common.yes')}
					<div className={cn(selected === 'yes' ? 'text-muted-foreground' : 'invisible')}>
						(<Kbd>Enter</Kbd> {t('confirmCard.toConfirm')})
					</div>
				</Button>
				{hasSuggestedRules && (
					<Button
						className={cn(
							'flex flex-wrap justify-start items-start cursor-pointer h-auto text-left',
							selected === 'yes_with_rule' ? 'text-primary' : 'text-muted-foreground',
						)}
						size="sm"
						variant="ghost"
						onMouseEnter={() => setSelected('yes_with_rule')}
						onClick={(e) => {
							e.stopPropagation();
							e.preventDefault();
                                                          if (suggestedRule) {
                                                                  onUserConfirm(true, buildConfirmedToolCall, [suggestedRule]);
                                                          }
						}}
					>
						<span className="flex items-start gap-1 w-full break-words whitespace-normal min-w-0">
							<ChevronRight
								className={cn(
									'size-4 shrink-0 mt-0.5',
									selected === 'yes_with_rule' ? 'visible' : 'invisible',
								)}
							/>
							<span className="break-words min-w-0">
								2.{' '}
								{t('confirmCard.yesWithRule', {
                                                                                  toolName: suggestedRule?.tool_name,
                                                                                  ruleContent: suggestedRule?.rule_content,
								})}
								{selected === 'yes_with_rule' && (
									<span className="text-muted-foreground ml-1 whitespace-nowrap">
										(<Kbd>Enter</Kbd> {t('confirmCard.toConfirm')})
									</span>
								)}
							</span>
						</span>
					</Button>
				)}
				<Button
					className={cn(
						'flex justify-start cursor-pointer',
						selected === 'no' ? 'text-primary' : 'text-muted-foreground',
					)}
					size="sm"
					variant="ghost"
					onMouseEnter={() => setSelected('no')}
					onClick={(e) => {
						e.stopPropagation();
						e.preventDefault();
                                                onUserConfirm(false, buildConfirmedToolCall);
					}}
				>
					<ChevronRight
						className={cn('size-4', selected === 'no' ? 'visible' : 'invisible')}
					/>
					{hasSuggestedRules ? '3' : '2'}. {t('common.no')}
					<div className={cn(selected === 'no' ? 'text-muted-foreground' : 'invisible')}>
						(<Kbd>Enter</Kbd> {t('confirmCard.toConfirm')})
					</div>
				</Button>
			</div>
		</div>
	);
}
