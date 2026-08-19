import type { AgentSchemaResponse } from '@/api';
import type { SchemaFormValue } from '@/components/form/SchemaForm';

type AgentFormDefaults = {
        identity: Record<string, SchemaFormValue>;
        context_config: Record<string, SchemaFormValue>;
        react_config: Record<string, SchemaFormValue>;
};

const REACT_CONFIG_SKIP_FIELDS = new Set(['enabled_builtin_tool_groups']);

/** Build a fresh agent form state populated from each section schema's defaults. */
export function defaultAgentFormValues(schema: AgentSchemaResponse): AgentFormDefaults {
        const fromDefaults = (
                section: AgentSchemaResponse['identity' | 'context_config' | 'react_config'],
        ): Record<string, SchemaFormValue> => {
                const out: Record<string, SchemaFormValue> = {};
                for (const [key, prop] of Object.entries(section.properties ?? {})) {
                        if (section === schema.react_config && REACT_CONFIG_SKIP_FIELDS.has(key)) continue;
                        if (prop.const !== undefined) continue;
                        if (prop.default !== undefined) out[key] = prop.default as SchemaFormValue;
                }
                return out;
        };

        return {
                identity: fromDefaults(schema.identity),
                context_config: fromDefaults(schema.context_config),
                react_config: fromDefaults(schema.react_config),
        };
}
