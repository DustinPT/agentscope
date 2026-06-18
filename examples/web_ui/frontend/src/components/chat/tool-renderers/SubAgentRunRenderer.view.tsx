import { ChevronDown, Loader2, Radio, RefreshCw } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ToolStateIcon } from './_shared';
import type { ToolCallWithResult } from './types';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { useMessages } from '@/hooks/useMessages';

type SubAgentRunResult = {
	agent_id?: string;
	agent_name?: string;
	session_id?: string;
	session_name?: string;
	mode?: string;
	status?: string;
};

type SubAgentRunInput = {
	agent_id?: string;
	session_name?: string;
	session_id?: string;
};

interface Props {
	calls: ToolCallWithResult[];
	parseInput: (input: string) => SubAgentRunInput | null;
	parseResult: (call: ToolCallWithResult) => SubAgentRunResult | null;
}

function SubAgentTranscript({ payload }: { payload: SubAgentRunResult }) {
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);
	const viewportRef = useRef<HTMLDivElement | null>(null);
	const activeSessionId = open ? payload.session_id ?? null : null;
	const activeAgentId = open ? payload.agent_id ?? null : null;
	const { msgs, loading, streaming, error, abort, onUserConfirm } = useMessages(
		activeAgentId,
		activeSessionId,
	);
	const errorText = useMemo(() => {
		if (!error) return null;
		return error.message || t('subagent.renderer.subscribeError');
	}, [error, t]);

	useEffect(() => {
		if (!open) abort();
	}, [open, abort]);

	useEffect(() => {
		if (!open) return;
		const viewport = viewportRef.current;
		if (!viewport) return;
		viewport.scrollTop = viewport.scrollHeight;
	}, [open, msgs.length, streaming]);

	return (
		<Collapsible open={open} onOpenChange={setOpen}>
			<div className="mt-2">
				<CollapsibleTrigger asChild>
					<Button variant="ghost" size="sm" className="h-7 px-2">
						<ChevronDown className={`size-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
						<span>{t('subagent.renderer.viewHistory')}</span>
					</Button>
				</CollapsibleTrigger>
			</div>
			<CollapsibleContent className="pt-2">
				<Card size="sm" className="w-full">
					<CardHeader className="border-b">
						<div className="flex items-center justify-between gap-3">
							<div className="min-w-0">
								<CardTitle className="truncate">
									{payload.session_name ?? payload.session_id ?? t('subagent.renderer.sessionFallback')}
								</CardTitle>
								<div className="flex items-center gap-2 text-xs text-muted-foreground">
									<span>{payload.agent_name ?? payload.agent_id ?? t('subagent.renderer.agentFallback')}</span>
									{open && (
										<span className="inline-flex items-center gap-1">
											<Radio
												className={`size-3 ${
													streaming ? 'text-primary animate-pulse' : 'text-muted-foreground'
												}`}
											/>
											{streaming
												? t('subagent.renderer.streaming')
												: t('subagent.renderer.connected')}
										</span>
									)}
								</div>
							</div>
							<Button
								variant="ghost"
								size="sm"
								className="h-7 px-2"
								onClick={() => {
									if (!open) {
										setOpen(true);
										return;
									}
									abort();
									setOpen(false);
									requestAnimationFrame(() => setOpen(true));
								}}
								disabled={loading}
							>
								<RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} />
								{t('subagent.renderer.reconnect')}
							</Button>
						</div>
					</CardHeader>
					<CardContent ref={viewportRef} className="max-h-80 overflow-y-auto space-y-3">
						{loading ? (
							<div className="flex items-center gap-2 text-sm text-muted-foreground">
								<Loader2 className="size-4 animate-spin" />
								{t('common.loading')}
							</div>
						) : errorText ? (
							<div className="text-sm text-destructive">{errorText}</div>
						) : msgs.length === 0 ? (
							<div className="text-sm text-muted-foreground">
								{streaming
									? t('subagent.renderer.waiting')
									: t('subagent.renderer.empty')}
							</div>
						) : (
							msgs.map((message) => (
								<MessageBubble
									key={message.id}
									message={message}
									onUserConfirm={onUserConfirm}
								/>
							))
						)}
					</CardContent>
				</Card>
			</CollapsibleContent>
		</Collapsible>
	);
}

export function SubAgentRunGroup({ calls, parseInput, parseResult }: Props) {
	const { t } = useTranslation();
	return (
		<div className="flex flex-col gap-3 w-full">
			{calls.map((callWithResult) => {
				const { call, result } = callWithResult;
				const parsedInput = parseInput(call.input);
				const parsedResult = parseResult(callWithResult);
				return (
					<div key={call.id} className="flex flex-col w-full max-w-full text-sm">
						<div className="flex flex-row gap-x-2 w-full max-w-full items-center">
							<ToolStateIcon states={[result?.state]} />
							<span className="truncate">
								<strong className="truncate text-primary">
									{t('subagent.renderer.toolName')}
								</strong>
								{parsedInput && (
									<>
										(
										{[
											parsedInput.agent_id,
											parsedInput.session_name ?? parsedInput.session_id,
										]
											.filter(Boolean)
											.join(', ')}
										)
									</>
								)}
							</span>
						</div>
						{parsedResult && (
							<div className="pl-6 pt-2">
								<div className="text-sm text-muted-foreground">
									{`${parsedResult.agent_name ?? parsedResult.agent_id ?? t('subagent.renderer.agentFallback')} -> ${
										parsedResult.session_name ?? parsedResult.session_id ?? ''
									}`}
								</div>
								<SubAgentTranscript payload={parsedResult} />
							</div>
						)}
					</div>
				);
			})}
		</div>
	);
}
