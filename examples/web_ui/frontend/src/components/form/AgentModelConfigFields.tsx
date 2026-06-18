import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { ChatModelConfig, ModelCard } from '@/api';
import { CreateCredentialDialog } from '@/components/dialog/CreateCredentialDialog';
import type { AgentModelConfigValue } from '@/components/form/agentModelConfig';
import { ModelParametersPopover } from '@/components/popover/ModelParametersPopover';
import { LlmSelect } from '@/components/select/LlmSelect';
import {
	Field,
	FieldDescription,
	FieldGroup,
	FieldLabel,
	FieldLegend,
	FieldSeparator,
	FieldSet,
} from '@/components/ui/field';
import { useAvailableModels } from '@/hooks/useAvailableModels';

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

interface Props {
	value: AgentModelConfigValue;
	onChange: (value: AgentModelConfigValue) => void;
}

export function AgentModelConfigFields({ value, onChange }: Props) {
	const { t } = useTranslation();
	const [credentialDialogOpen, setCredentialDialogOpen] = useState(false);
	const [credentialRefetchTrigger, setCredentialRefetchTrigger] = useState(0);
	const { groups } = useAvailableModels();
	const selectedModelCard = useMemo(
		() => resolveModelCard(groups, value.default_chat_model_config),
		[groups, value.default_chat_model_config],
	);

	return (
		<>
			<FieldSeparator className="my-0" />
			<FieldSet>
				<FieldLegend>{t('agent-form.model-config.legend')}</FieldLegend>
				<FieldDescription>{t('agent-form.model-config.description')}</FieldDescription>
				<FieldGroup>
					<Field>
						<FieldLabel>{t('agent-form.model-config.default-model.label')}</FieldLabel>
						<div className="flex items-center gap-2">
							<LlmSelect
								value={value.default_chat_model_config}
								onChange={(next) =>
									onChange({ ...value, default_chat_model_config: next })
								}
								onAddCredential={() => setCredentialDialogOpen(true)}
								refetchTrigger={credentialRefetchTrigger}
								allowClear
								placeholder={t('agent-form.model-config.default-model.placeholder')}
								clearLabel={t('agent-form.model-config.default-model.clear')}
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
							{t('agent-form.model-config.default-model.description')}
						</FieldDescription>
					</Field>
				</FieldGroup>
			</FieldSet>
			<CreateCredentialDialog
				open={credentialDialogOpen}
				onOpenChange={setCredentialDialogOpen}
				onCreated={() => setCredentialRefetchTrigger((prev) => prev + 1)}
			/>
		</>
	);
}
