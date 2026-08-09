import { useTranslation } from 'react-i18next';

import { Checkbox } from '@/components/ui/checkbox';
import {
	Field,
	FieldContent,
	FieldDescription,
	FieldGroup,
	FieldSeparator,
	FieldTitle,
} from '@/components/ui/field';

import type {
	BuiltinToolGroup,
	ReActToolGroupConfigValue,
} from '@/components/form/reactToolGroupConfig';

interface Props {
	value: ReActToolGroupConfigValue;
	onChange: (value: ReActToolGroupConfigValue) => void;
}

const TOOL_GROUPS: BuiltinToolGroup[] = [
        'read',
        'edit',
        'schedule',
        'terminal',
        'network',
        'team',
        'agent_management',
        'session_management',
];

export function ReActToolGroupFields({ value, onChange }: Props) {
	const { t } = useTranslation();

	return (
		<>
			<FieldSeparator className="my-0" />
			<FieldGroup>
				<div className="space-y-1">
					<FieldTitle>{t('agent-form.react-config.builtin-tool-groups.label')}</FieldTitle>
					<FieldDescription>
						{t('agent-form.react-config.builtin-tool-groups.description')}
					</FieldDescription>
				</div>
				{TOOL_GROUPS.map((group) => {
					const checked = value.enabled_builtin_tool_groups.includes(group);
					return (
						<Field key={group} orientation="horizontal">
							<Checkbox
								id={`react-tool-group-${group}`}
								checked={checked}
								onCheckedChange={(next) => {
									const allow = !!next;
									onChange({
										enabled_builtin_tool_groups: allow
											? [...value.enabled_builtin_tool_groups, group]
											: value.enabled_builtin_tool_groups.filter(
													(item) => item !== group,
												),
									});
								}}
							/>
							<FieldContent>
								<FieldTitle>
									{t(`agent-form.react-config.builtin-tool-groups.${group}.label`)}
								</FieldTitle>
								<FieldDescription>
									{t(
										`agent-form.react-config.builtin-tool-groups.${group}.description`,
									)}
								</FieldDescription>
							</FieldContent>
						</Field>
					);
				})}
			</FieldGroup>
		</>
	);
}
