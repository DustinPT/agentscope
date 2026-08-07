import { CircleAlert, Loader2, Save } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { agentApi } from '@/api';
import type {
        AgentComposeResponse,
	AgentMCPAsset,
	AgentRecord,
	AgentSkillAsset,
	ContextConfig,
	MCPClient,
	ReActConfig,
} from '@/api';
import {
	AgentFormFields,
	defaultAgentFormValues,
	type AgentFormValues,
	type AgentSection,
} from '@/components/form/AgentFormFields';
import { AgentWorkspaceConfigFields } from '@/components/form/AgentWorkspaceConfigFields';
import {
	createAgentModelConfigValue,
	parseAgentModelConfigValue,
	type AgentModelConfigValue,
} from '@/components/form/agentModelConfig';
import { AgentModelConfigFields } from '@/components/form/AgentModelConfigFields';
import type { SchemaFormValue } from '@/components/form/SchemaForm';
import {
	createSubAgentConfigValue,
	parseSubAgentConfigValue,
	type SubAgentConfigValue,
} from '@/components/form/subAgentConfig';
import {
	createReActToolGroupConfigValue,
	parseReActToolGroupConfigValue,
	type ReActToolGroupConfigValue,
} from '@/components/form/reactToolGroupConfig';
import { ReActToolGroupFields } from '@/components/form/ReActToolGroupFields';
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
} from '@/components/ui/dialog';
import { useAgentSchema } from '@/hooks/useAgentSchema';

interface Props {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	agent: AgentRecord;
	agents: AgentRecord[];
        composeUpdate: (agentId: string, body: FormData) => Promise<AgentComposeResponse>;
	onUpdated?: () => void;
}

export function EditAgentDialog({
	open,
	onOpenChange,
	agent,
	agents,
	composeUpdate,
	onUpdated,
}: Props) {
	const { t } = useTranslation();
	const { schema } = useAgentSchema();
	const [submitting, setSubmitting] = useState(false);
	const [values, setValues] = useState<AgentFormValues | null>(null);
	const [modelConfigValue, setModelConfigValue] = useState<AgentModelConfigValue>(
		createAgentModelConfigValue(agent),
	);
	const [subAgentValue, setSubAgentValue] = useState<SubAgentConfigValue>(
		createSubAgentConfigValue(agent),
	);
	const [reactToolGroupValue, setReactToolGroupValue] = useState<ReActToolGroupConfigValue>(
		createReActToolGroupConfigValue(agent),
	);
	const [mcps, setMcps] = useState<MCPClient[]>(agent.data.mcps ?? []);
	const [persistedMcpAssets, setPersistedMcpAssets] = useState<AgentMCPAsset[]>(
		agent.data.mcp_assets ?? [],
	);
	const [pendingMcpFiles, setPendingMcpFiles] = useState<File[]>([]);
	const [persistedSkills, setPersistedSkills] = useState<AgentSkillAsset[]>(agent.data.skills ?? []);
	const [pendingSkillFiles, setPendingSkillFiles] = useState<File[]>([]);

	useEffect(() => {
		if (!open || !schema) {
			if (!open) setValues(null);
			return;
		}
		// Start from schema defaults, then overlay the existing agent's data so
		// any unset fields fall back to defaults rather than empty.
		const base = defaultAgentFormValues(schema);
		const d = agent.data;
		const {
			enabled_builtin_tool_groups: _enabledBuiltinToolGroups,
			...reactConfigFields
		} = d.react_config ?? {};
		setValues({
			identity: {
				...base.identity,
				name: d.name,
				description: d.description,
				system_prompt: d.system_prompt,
			},
			context_config: { ...base.context_config, ...(d.context_config ?? {}) },
			react_config: { ...base.react_config, ...reactConfigFields },
		});
		setModelConfigValue(createAgentModelConfigValue(agent));
		setSubAgentValue(createSubAgentConfigValue(agent));
		setReactToolGroupValue(createReActToolGroupConfigValue(agent));
		setMcps(agent.data.mcps ?? []);
		setPersistedMcpAssets(agent.data.mcp_assets ?? []);
		setPendingMcpFiles([]);
		setPersistedSkills(agent.data.skills ?? []);
		setPendingSkillFiles([]);
	}, [open, schema, agent]);

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
				retained_mcp_asset_names: persistedMcpAssets.map((mcp) => mcp.name),
				retained_skill_names: persistedSkills.map((skill) => skill.name),
			};
			formData.append('config', JSON.stringify(config));
			for (const file of pendingMcpFiles) {
				formData.append('mcp_files', file);
			}
			for (const file of pendingSkillFiles) {
				formData.append('skill_files', file);
			}
			await composeUpdate(agent.id, formData);
			onOpenChange(false);
			onUpdated?.();
		} finally {
			setSubmitting(false);
		}
	};

	const handleAddMcps = async (clients: MCPClient[]) => {
		const existingNames = new Set(mcps.map((mcp) => mcp.name));
		for (const client of clients) {
			if (existingNames.has(client.name)) {
				throw new Error(`MCP server "${client.name}" already exists.`);
			}
		}
		setMcps((prev) => [...prev, ...clients]);
	};

	const handleDownloadSkill = async (skillName: string) => {
		const blob = await agentApi.downloadSkill(agent.id, skillName);
		const url = URL.createObjectURL(blob);
		const link = document.createElement('a');
		link.href = url;
		link.download = `${skillName}.zip`;
		link.click();
		URL.revokeObjectURL(url);
	};

	const nameValid = !!(values?.identity.name as string | undefined)?.trim();

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="!w-[640px] !max-w-[640px]">
				<DialogHeader>
					<DialogTitle>{t('dialog-agent-edit.title')}</DialogTitle>
					<DialogDescription className="sr-only">
						{t('dialog-agent-edit.description')}
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
								currentAgentId={agent.id}
								initialSelectedAgentIds={agent.data.allowed_subagent_ids ?? []}
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
									setPersistedMcpAssets((prev) => {
										const replacedNames = new Set(
											nextFiles.map((file) => file.name.replace(/\.zip$/i, '')),
										);
										return prev.filter((asset) => !replacedNames.has(asset.name));
									});
								}}
								onRemovePersistedMcpAsset={(name) =>
									setPersistedMcpAssets((prev) =>
										prev.filter((mcpAsset) => mcpAsset.name !== name),
									)
								}
								onRemovePendingMcpFile={(index) =>
									setPendingMcpFiles((prev) =>
										prev.filter((_, currentIndex) => currentIndex !== index),
									)
								}
								persistedSkills={persistedSkills}
								pendingSkillFiles={pendingSkillFiles}
								onAddSkillFiles={(files) =>
									setPendingSkillFiles((prev) => [
										...prev,
										...(files ? Array.from(files) : []),
									])
								}
								onRemovePersistedSkill={(name) =>
									setPersistedSkills((prev) =>
										prev.filter((skill) => skill.name !== name),
									)
								}
								onRemovePendingSkill={(index) =>
									setPendingSkillFiles((prev) =>
										prev.filter((_, currentIndex) => currentIndex !== index),
									)
								}
								onDownloadSkill={handleDownloadSkill}
							/>
						</div>
					) : (
						<p className="text-muted-foreground text-sm">{t('common.loading')}</p>
					)}
				</div>
				<DialogFooter>
					<Button
						variant="ghost"
						onClick={() => onOpenChange(false)}
						disabled={submitting}
					>
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
							<Save className="size-3.5" />
						)}
						{submitting ? t('common.saving') : t('common.save')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
