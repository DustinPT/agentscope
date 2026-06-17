import { useMemo, useState } from 'react';

import type { AgentRecord, ChatModelConfig, ModelCard } from '@/api';
import { CreateCredentialDialog } from '@/components/dialog/CreateCredentialDialog';
import type { SubAgentConfigValue } from '@/components/form/subAgentConfig';
import { ModelParametersPopover } from '@/components/popover/ModelParametersPopover';
import { LlmSelect } from '@/components/select/LlmSelect';
import { Checkbox } from '@/components/ui/checkbox';
import {
	Field,
	FieldContent,
	FieldDescription,
	FieldGroup,
	FieldLabel,
	FieldTitle,
} from '@/components/ui/field';
import { useAvailableModels } from '@/hooks/useAvailableModels';

interface Props {
	value: SubAgentConfigValue;
	onChange: (value: SubAgentConfigValue) => void;
	agents: AgentRecord[];
	currentAgentId?: string;
}

function resolveModelCard(
	groups: Record<string, Array<{ credential: { id: string }; models: ModelCard[] }>>,
	selectedModel: ChatModelConfig | null,
): ModelCard | null {
	if (!selectedModel) return null;
	const items = groups[selectedModel.type];
	if (!items) return null;
	for (const { credential, models } of items) {
		if (credential.id !== selectedModel.credential_id) continue;
		const match = models.find((model) => model.name === selectedModel.model);
		if (match) return match;
	}
	return null;
}

export function SubAgentConfigFields({
	value,
	onChange,
	agents,
	currentAgentId,
}: Props) {
	const [credentialDialogOpen, setCredentialDialogOpen] = useState(false);
	const [credentialRefetchTrigger, setCredentialRefetchTrigger] = useState(0);
	const { groups } = useAvailableModels();
	const options = useMemo(
		() => agents.filter((agent) => agent.id !== currentAgentId),
		[agents, currentAgentId],
	);
	const selectedModelCard = useMemo(
		() => resolveModelCard(groups, value.default_chat_model_config),
		[groups, value.default_chat_model_config],
	);

	return (
		<>
			<FieldGroup>
			<Field orientation="horizontal">
				<Checkbox
					id="allow-subagent-calls"
					checked={value.allow_subagent_calls}
					onCheckedChange={(checked) =>
						onChange({
							...value,
							allow_subagent_calls: !!checked,
							allowed_subagent_ids: checked ? value.allowed_subagent_ids : [],
						})
					}
				/>
				<FieldLabel htmlFor="allow-subagent-calls" className="font-normal">
					Allow Sub-Agent Calls
				</FieldLabel>
			</Field>
			<Field>
				<FieldLabel>Allowed Sub-Agents</FieldLabel>
				<div className="max-h-48 overflow-y-auto rounded-lg border border-input p-3">
					<div className="flex flex-col gap-3">
						{options.length === 0 ? (
							<p className="text-sm text-muted-foreground">No other managed agents</p>
						) : (
							options.map((agent) => {
								const checked = value.allowed_subagent_ids.includes(agent.id);
								return (
									<FieldLabel key={agent.id}>
										<Field orientation="horizontal">
											<Checkbox
												checked={checked}
												disabled={!value.allow_subagent_calls}
												onCheckedChange={(next) => {
													const allow = !!next;
													onChange({
														...value,
														allowed_subagent_ids: allow
															? [...value.allowed_subagent_ids, agent.id]
															: value.allowed_subagent_ids.filter(
																	(id) => id !== agent.id,
																),
													});
												}}
											/>
											<FieldContent>
												<FieldTitle>{agent.data.name}</FieldTitle>
												<FieldDescription>{agent.id}</FieldDescription>
											</FieldContent>
										</Field>
									</FieldLabel>
								);
							})
						)}
					</div>
				</div>
				<FieldDescription>
					Only checked agents can be called as sub-agents.
				</FieldDescription>
			</Field>
			<Field>
				<FieldLabel>Default Sub-Agent Model</FieldLabel>
				<div className="flex items-center gap-2">
					<LlmSelect
						value={value.default_chat_model_config}
						onChange={(next) =>
							onChange({ ...value, default_chat_model_config: next })
						}
						onAddCredential={() => setCredentialDialogOpen(true)}
						refetchTrigger={credentialRefetchTrigger}
						allowClear
						placeholder="Inherit caller session model"
						clearLabel="Inherit caller session model"
					/>
					<ModelParametersPopover
						selectedModel={value.default_chat_model_config}
						modelCard={selectedModelCard}
						onChange={(parameters) => {
							if (!value.default_chat_model_config) return;
							onChange({
								...value,
								default_chat_model_config: {
									...value.default_chat_model_config,
									parameters,
								},
							});
						}}
						selectedFallbackModel={null}
						onFallbackChange={() => {}}
					/>
				</div>
				<FieldDescription>
					Leave empty to inherit the caller session model.
				</FieldDescription>
			</Field>
			</FieldGroup>
			<CreateCredentialDialog
				open={credentialDialogOpen}
				onOpenChange={setCredentialDialogOpen}
				onCreated={() => setCredentialRefetchTrigger((prev) => prev + 1)}
			/>
		</>
	);
}
