import type { AgentRecord, ReActConfig } from '@/api';

export type BuiltinToolGroup = NonNullable<
	ReActConfig['enabled_builtin_tool_groups']
>[number];

export interface ReActToolGroupConfigValue {
	enabled_builtin_tool_groups: BuiltinToolGroup[];
}

export const ALL_BUILTIN_TOOL_GROUPS: BuiltinToolGroup[] = [
	'read',
	'edit',
	'schedule',
	'terminal',
        'network',
	'team',
        'agent_management',
        'session_management',
];

export function normalizeBuiltinToolGroups(
	groups?: ReadonlyArray<BuiltinToolGroup> | null,
): BuiltinToolGroup[] {
	const enabled = new Set(groups ?? ALL_BUILTIN_TOOL_GROUPS);
	return ALL_BUILTIN_TOOL_GROUPS.filter((group) => enabled.has(group));
}

export function createReActToolGroupConfigValue(
	agent?: AgentRecord,
): ReActToolGroupConfigValue {
	return {
		enabled_builtin_tool_groups: normalizeBuiltinToolGroups(
			agent?.data.react_config?.enabled_builtin_tool_groups,
		),
	};
}

export function parseReActToolGroupConfigValue(
	value: ReActToolGroupConfigValue,
): Pick<ReActConfig, 'enabled_builtin_tool_groups'> {
	return {
		enabled_builtin_tool_groups: normalizeBuiltinToolGroups(
			value.enabled_builtin_tool_groups,
		),
	};
}
