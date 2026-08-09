import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type { AgentSchemaResponse } from '@/api';
import { SchemaForm, type SchemaFormValue } from '@/components/form/SchemaForm';
import {
	FieldDescription,
	FieldGroup,
	FieldLegend,
	FieldSeparator,
	FieldSet,
} from '@/components/ui/field';

export type AgentSection = 'identity' | 'context_config' | 'react_config';

export type AgentFormValues = {
	[K in AgentSection]: Record<string, SchemaFormValue>;
};

interface Props {
	schema: AgentSchemaResponse;
	values: AgentFormValues;
	onChange: (section: AgentSection, key: string, value: SchemaFormValue) => void;
	renderInSection?: (section: AgentSection) => ReactNode;
	renderAfterSection?: (section: AgentSection) => ReactNode;
}

const SECTIONS: { key: AgentSection; i18n: string }[] = [
	{ key: 'identity', i18n: 'identity' },
	{ key: 'context_config', i18n: 'context-config' },
	{ key: 'react_config', i18n: 'react-config' },
];

const toKebab = (s: string) => s.replace(/_/g, '-');
const fieldI18nKey = (s: string) => (s === 'description' ? 'description-field' : toKebab(s));

const REACT_CONFIG_SKIP_FIELDS = new Set(['enabled_builtin_tool_groups']);

export function AgentFormFields({
	schema,
	values,
	onChange,
	renderInSection,
	renderAfterSection,
}: Props) {
	const { t } = useTranslation();

	return (
		<FieldGroup>
			{SECTIONS.map(({ key: sectionKey, i18n: sectionI18n }, idx) => {
				const sectionSchema = schema[sectionKey];
				const legend = t(`agent-form.${sectionI18n}.legend`, {
					defaultValue: sectionSchema.title ?? sectionKey,
				});
				const description = t(`agent-form.${sectionI18n}.description`, {
					defaultValue: '',
				});
				return (
					<div key={sectionKey}>
						{idx > 0 && <FieldSeparator className="my-0" />}
						<FieldSet>
							<FieldLegend>{legend}</FieldLegend>
							{description && <FieldDescription>{description}</FieldDescription>}
							<SchemaForm
								schema={sectionSchema}
								values={values[sectionKey]}
								onChange={(k, v) => onChange(sectionKey, k, v)}
								skipFields={
									sectionKey === 'react_config' ? REACT_CONFIG_SKIP_FIELDS : undefined
								}
								idPrefix={`agent-form-${sectionI18n}`}
								labelFor={(k, prop) =>
									t(`agent-form.${sectionI18n}.${fieldI18nKey(k)}.label`, {
										defaultValue: prop.title ?? k.replace(/_/g, ' '),
									})
								}
								placeholderFor={(k, prop) =>
									t(`agent-form.${sectionI18n}.${fieldI18nKey(k)}.placeholder`, {
										defaultValue: prop.description ?? '',
									}) || undefined
								}
                                                                textareaClassNameFor={(k) =>
                                                                        sectionKey === 'identity' && k === 'system_prompt'
                                                                                ? 'min-h-32 max-h-60 overflow-y-auto resize-y'
                                                                                : undefined
                                                                }
							/>
							{renderInSection?.(sectionKey)}
						</FieldSet>
						{renderAfterSection?.(sectionKey)}
					</div>
				);
			})}
		</FieldGroup>
	);
}

/** Build a fresh `AgentFormValues` populated from each section schema's defaults. */
export function defaultAgentFormValues(schema: AgentSchemaResponse): AgentFormValues {
	const fromDefaults = (
		section: AgentSchemaResponse[AgentSection],
	): Record<string, SchemaFormValue> => {
		const out: Record<string, SchemaFormValue> = {};
		for (const [k, prop] of Object.entries(section.properties ?? {})) {
			if (section === schema.react_config && REACT_CONFIG_SKIP_FIELDS.has(k)) continue;
			if (prop.const !== undefined) continue;
			if (prop.default !== undefined) out[k] = prop.default as SchemaFormValue;
		}
		return out;
	};
	return {
		identity: fromDefaults(schema.identity),
		context_config: fromDefaults(schema.context_config),
		react_config: fromDefaults(schema.react_config),
	};
}
