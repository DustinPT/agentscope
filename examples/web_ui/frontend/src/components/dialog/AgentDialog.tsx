import { CircleAlert, Loader2, PlusCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import type { AgentMCPAsset, ContextConfig, MCPClient, ReActConfig } from '@/api';
import {
	AgentFormFields,
	defaultAgentFormValues,
	type AgentFormValues,
	type AgentSection,
} from '@/components/form/AgentFormFields';
import {
	createAgentModelConfigValue,
	parseAgentModelConfigValue,
	type AgentModelConfigValue,
} from '@/components/form/agentModelConfig';
import { AgentModelConfigFields } from '@/components/form/AgentModelConfigFields';
import { AgentWorkspaceConfigFields } from '@/components/form/AgentWorkspaceConfigFields';
import {
	createReActToolGroupConfigValue,
	parseReActToolGroupConfigValue,
	type ReActToolGroupConfigValue,
} from '@/components/form/reactToolGroupConfig';
import { ReActToolGroupFields } from '@/components/form/ReActToolGroupFields';
import type { SchemaFormValue } from '@/components/form/SchemaForm';
import {
	createSubAgentConfigValue,
	parseSubAgentConfigValue,
	type SubAgentConfigValue,
} from '@/components/form/subAgentConfig';
import {
	SubAgentConfigFields,
} from '@/components/form/SubAgentConfigFields';
import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogDescription,
	DialogTrigger,
} from '@/components/ui/dialog';
import { useAgents } from '@/hooks/useAgents';
import { useAgentSchema } from '@/hooks/useAgentSchema';

interface Props {
	onCreated?: () => void;
	triggerId?: string;
}

export function AgentDialog({ onCreated, triggerId }: Props) {
	const { agents, composeCreate } = useAgents();
	const { t } = useTranslation();
	const { schema } = useAgentSchema();
	const [open, setOpen] = useState(false);
	const [submitting, setSubmitting] = useState(false);
	const [values, setValues] = useState<AgentFormValues | null>(null);
	const [modelConfigValue, setModelConfigValue] = useState<AgentModelConfigValue>(
		createAgentModelConfigValue(),
	);
	const [subAgentValue, setSubAgentValue] = useState<SubAgentConfigValue>(
		createSubAgentConfigValue(),
	);
	const [reactToolGroupValue, setReactToolGroupValue] = useState<ReActToolGroupConfigValue>(
		createReActToolGroupConfigValue(),
	);
	const [mcps, setMcps] = useState<MCPClient[]>([]);
	const [persistedMcpAssets] = useState<AgentMCPAsset[]>([]);
	const [pendingMcpFiles, setPendingMcpFiles] = useState<File[]>([]);
	const [pendingSkillFiles, setPendingSkillFiles] = useState<File[]>([]);

	useEffect(() => {
		if (open && schema && !values) {
			setValues(defaultAgentFormValues(schema));
			setModelConfigValue(createAgentModelConfigValue());
			setSubAgentValue(createSubAgentConfigValue());
			setReactToolGroupValue(createReActToolGroupConfigValue());
			setMcps([]);
			setPendingMcpFiles([]);
			setPendingSkillFiles([]);
		}
		if (!open) {
			setValues(null);
			setModelConfigValue(createAgentModelConfigValue());
			setSubAgentValue(createSubAgentConfigValue());
			setReactToolGroupValue(createReActToolGroupConfigValue());
			setMcps([]);
			setPendingMcpFiles([]);
			setPendingSkillFiles([]);
		}
	}, [open, schema, values]);

	const handleChange = (section: AgentSection, key: string, value: SchemaFormValue) => {
		setValues((prev) =>
			prev ? { ...prev, [section]: { ...prev[section], [key]: value } } : prev,
		);
	};

	const handleSubmit = async () => {
		if (!values) return;
		const name = (values.identity.name as string | undefined)?.trim();
		if (!name) return;
		setSubmitting(true);
		try {
			const modelConfig = parseAgentModelConfigValue(modelConfigValue);
			const subAgentConfig = parseSubAgentConfigValue(subAgentValue);
			const reactToolGroupConfig = parseReActToolGroupConfigValue(reactToolGroupValue);
			const formData = new FormData();
			const config = {
				name,
				description: values.identity.description as string | undefined,
				system_prompt: values.identity.system_prompt as string | undefined,
				context_config: values.context_config as unknown as ContextConfig,
				react_config: {
					...(values.react_config as unknown as ReActConfig),
					...reactToolGroupConfig,
				},
				...modelConfig,
				...subAgentConfig,
				mcps,
			};
			formData.append('config', JSON.stringify(config));
			for (const file of pendingMcpFiles) {
				formData.append('mcp_files', file);
			}
			for (const file of pendingSkillFiles) {
				formData.append('skill_files', file);
			}
			await composeCreate(formData);
			setOpen(false);
			onCreated?.();
		} finally {
			setSubmitting(false);
		}
	};

	const handleAddMcps = async (clients: MCPClient[]) => {
		setMcps((prev) => {
			const next = [...prev];
			for (const client of clients) {
				if (next.some((item) => item.name === client.name)) {
					throw new Error(`MCP server "${client.name}" already exists.`);
				}
				next.push(client);
			}
			return next;
		});
	};

	const nameValid = !!(values?.identity.name as string | undefined)?.trim();

	return (
		<Dialog open={open} onOpenChange={setOpen}>
			<DialogTrigger asChild>
				<Button id={triggerId}>
					<PlusCircle />
					<span>{t('dialog-agent-create.trigger')}</span>
				</Button>
			</DialogTrigger>
			<DialogContent className="!w-[640px] !max-w-[640px]">
				<DialogHeader>
					<DialogTitle>{t('dialog-agent-create.title')}</DialogTitle>
					<DialogDescription className="sr-only">
						{t('dialog-agent-create.description')}
					</DialogDescription>
				</DialogHeader>
				<div className="no-scrollbar -mx-4 max-h-[75vh] overflow-y-auto px-4">
					{schema && values ? (
						<div className="space-y-6">
							<AgentFormFields
								schema={schema}
								values={values}
								onChange={handleChange}
								renderInSection={(section) =>
									section === 'react_config' ? (
										<ReActToolGroupFields
											value={reactToolGroupValue}
											onChange={setReactToolGroupValue}
										/>
									) : null
								}
								renderAfterSection={(section) =>
									section === 'identity' ? (
										<AgentModelConfigFields
											value={modelConfigValue}
											onChange={setModelConfigValue}
										/>
									) : null
								}
							/>
							<SubAgentConfigFields
								value={subAgentValue}
								onChange={setSubAgentValue}
								agents={agents}
								initialSelectedAgentIds={subAgentValue.allowed_subagent_ids}
							/>
							<AgentWorkspaceConfigFields
								mcps={mcps}
								onAddMcps={handleAddMcps}
								onRemoveMcp={(name) =>
									setMcps((prev) => prev.filter((item) => item.name !== name))
								}
								persistedMcpAssets={persistedMcpAssets}
								pendingMcpFiles={pendingMcpFiles}
								onAddMcpFiles={(files) => {
									const nextFiles = files ? Array.from(files) : [];
									const existingJsonNames = new Set(mcps.map((mcp) => mcp.name));
									for (const file of nextFiles) {
										const baseName = file.name.replace(/\.zip$/i, '');
										if (existingJsonNames.has(baseName)) {
											toast.error(
												t('agent-workspace.mcps.conflict-json', { name: baseName }),
											);
											return;
										}
									}
									setPendingMcpFiles((prev) => [...prev, ...nextFiles]);
								}}
								onRemovePersistedMcpAsset={() => undefined}
								onRemovePendingMcpFile={(index) =>
									setPendingMcpFiles((prev) =>
										prev.filter((_, currentIndex) => currentIndex !== index),
									)
								}
								persistedSkills={[]}
								pendingSkillFiles={pendingSkillFiles}
								onAddSkillFiles={(files) =>
									setPendingSkillFiles((prev) => [
										...prev,
										...(files ? Array.from(files) : []),
									])
								}
								onRemovePersistedSkill={() => undefined}
								onRemovePendingSkill={(index) =>
									setPendingSkillFiles((prev) =>
										prev.filter((_, currentIndex) => currentIndex !== index),
									)
								}
								onDownloadSkill={async () => undefined}
							/>
						</div>
					) : (
						<p className="text-muted-foreground text-sm">{t('common.loading')}</p>
					)}
				</div>
				<DialogFooter>
					<Button variant="ghost" onClick={() => setOpen(false)} disabled={submitting}>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button
						onClick={handleSubmit}
						disabled={!nameValid || submitting || !schema || !values}
					>
						{submitting ? (
							<Loader2 className="size-3.5 animate-spin" />
						) : (
							<PlusCircle className="size-3.5" />
						)}
						{submitting ? t('common.creating') : t('common.create')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
