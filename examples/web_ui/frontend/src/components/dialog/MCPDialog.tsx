import { PlusCircle, Loader2, Check } from 'lucide-react';
import { CircleAlert } from 'lucide-react';
import { useState, useCallback } from 'react';
import type { ReactNode } from 'react';

import type { MCPClient } from '@/api/types';
import { Alert, AlertDescription } from '@/components/ui/alert.tsx';
import { Button } from '@/components/ui/button.tsx';
import { Checkbox } from '@/components/ui/checkbox.tsx';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from '@/components/ui/dialog.tsx';
import {
	Field,
	FieldContent,
	FieldDescription,
	FieldGroup,
	FieldLabel,
	FieldSet,
} from '@/components/ui/field.tsx';
import { InputGroup, InputGroupTextarea } from '@/components/ui/input-group.tsx';
import { useTranslation } from '@/i18n/useI18n.ts';
import { parseMcpConfig } from './mcpConfig';

type Status = 'idle' | 'loading' | 'success' | 'error';

interface Props {
	children: ReactNode;
	onAdd: (mcps: MCPClient[]) => Promise<void>;
}

export const CreateMCPDialog = ({ children, onAdd }: Props) => {
	const { t } = useTranslation();
	const [open, setOpen] = useState(false);
	const [configValue, setConfigValue] = useState('');
	const [keepAlive, setKeepAlive] = useState(true);
	const [status, setStatus] = useState<Status>('idle');
	const [errorMsg, setErrorMsg] = useState('');

	const reset = useCallback(() => {
		setConfigValue('');
		setKeepAlive(true);
		setStatus('idle');
		setErrorMsg('');
	}, []);

	const handleOpenChange = useCallback(
		(next: boolean) => {
			if (!next) reset();
			setOpen(next);
		},
		[reset],
	);

	const handleAdd = useCallback(async () => {
		setErrorMsg('');
		let mcpClients: MCPClient[];
		try {
			mcpClients = parseMcpConfig(configValue, keepAlive, t);
		} catch (e) {
			setErrorMsg((e as Error).message);
			setStatus('error');
			return;
		}

		setStatus('loading');
		try {
			await onAdd(mcpClients);
			handleOpenChange(false);
		} catch (e) {
			// ApiErrors are already shown via the global toast in client.ts.
			// Show only local validation errors (e.g. duplicate name) inline.
			const isApiError = e instanceof Error && e.name === 'ApiError';
			if (!isApiError) {
				setErrorMsg(e instanceof Error ? e.message : String(e));
			}
			setStatus('idle');
		}
	}, [configValue, keepAlive, t, onAdd, handleOpenChange]);

	return (
		<Dialog open={open} onOpenChange={handleOpenChange}>
			<DialogTrigger asChild>{children}</DialogTrigger>
			<DialogContent className="!w-[500px] !max-w-[500px]">
				<DialogHeader>
					<DialogTitle>{t('dialog-mcp-create.title')}</DialogTitle>
					<DialogDescription>{t('dialog-mcp-create.description')}</DialogDescription>
				</DialogHeader>
				<FieldSet>
					<FieldGroup>
						<Field>
							<FieldContent>
								<FieldLabel>{t('dialog-mcp-create.configLabel')}</FieldLabel>
							</FieldContent>
							<InputGroup>
								<InputGroupTextarea
									className="max-h-100"
									value={configValue}
									onChange={(e) => setConfigValue(e.target.value)}
									placeholder={
										'{\n  "mcpServers": {\n    "playwright": {\n      "command": "npx",\n      "args": ["@playwright/mcp@latest"]\n    }\n  }\n}'
									}
								/>
							</InputGroup>
						</Field>
						<Field orientation="horizontal">
							<Checkbox
								id="mcp-keep-alive"
								checked={keepAlive}
								onCheckedChange={(v) => setKeepAlive(v === true)}
							/>
							<FieldContent>
								<FieldLabel htmlFor="mcp-keep-alive">
									{t('dialog-mcp-create.keepAlive')}
								</FieldLabel>
								<FieldDescription>
									{t('dialog-mcp-create.keepAliveDesc')}
								</FieldDescription>
							</FieldContent>
						</Field>
					</FieldGroup>
				</FieldSet>
				{errorMsg && (
					<Alert variant="destructive">
						<CircleAlert />
						<AlertDescription>{errorMsg}</AlertDescription>
					</Alert>
				)}
				<DialogFooter>
					<Button variant="ghost" onClick={() => handleOpenChange(false)}>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button
						onClick={handleAdd}
						disabled={status === 'loading' || status === 'success'}
					>
						{status === 'loading' && <Loader2 className="size-3.5 animate-spin" />}
						{status === 'success' && <Check className="size-3.5" />}
						{status !== 'loading' && status !== 'success' && (
							<PlusCircle className="size-3.5" />
						)}
						{status === 'loading'
							? t('dialog-mcp-create.adding')
							: status === 'success'
								? t('dialog-mcp-create.added')
								: t('common.add')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
};
