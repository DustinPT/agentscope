import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import type { AgentRecord } from '@/api';
import type { SubAgentConfigValue } from '@/components/form/subAgentConfig';
import { Checkbox } from '@/components/ui/checkbox';
import {
	Field,
	FieldContent,
	FieldDescription,
	FieldGroup,
	FieldLabel,
	FieldLegend,
	FieldSeparator,
	FieldSet,
	FieldTitle,
} from '@/components/ui/field';

interface Props {
	value: SubAgentConfigValue;
	onChange: (value: SubAgentConfigValue) => void;
	agents: AgentRecord[];
	currentAgentId?: string;
}

export function SubAgentConfigFields({
	value,
	onChange,
	agents,
	currentAgentId,
}: Props) {
	const { t } = useTranslation();
	const options = useMemo(
		() => agents.filter((agent) => agent.id !== currentAgentId),
		[agents, currentAgentId],
	);

	return (
		<>
			<FieldSeparator className="my-0" />
			<FieldSet>
				<FieldLegend>{t('agent-form.subagent-config.legend')}</FieldLegend>
				<FieldDescription>{t('agent-form.subagent-config.description')}</FieldDescription>
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
							{t('agent-form.subagent-config.allow-subagent-calls.label')}
						</FieldLabel>
					</Field>
					<Field>
						<FieldLabel>{t('agent-form.subagent-config.allowed-subagents.label')}</FieldLabel>
						<div className="max-h-48 overflow-y-auto rounded-lg border border-input p-3">
							<div className="flex flex-col gap-3">
								{options.length === 0 ? (
									<p className="text-sm text-muted-foreground">
										{t('agent-form.subagent-config.allowed-subagents.empty')}
									</p>
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
							{t('agent-form.subagent-config.allowed-subagents.description')}
						</FieldDescription>
					</Field>
				</FieldGroup>
			</FieldSet>
		</>
	);
}
