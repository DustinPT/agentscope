import type { AgentRecord } from '@/api';

export interface SubAgentConfigValue {
	allow_subagent_calls: boolean;
	allowed_subagent_ids: string[];
}

export function createSubAgentConfigValue(agent?: AgentRecord): SubAgentConfigValue {
	return {
		allow_subagent_calls: agent?.data.allow_subagent_calls ?? false,
		allowed_subagent_ids: agent?.data.allowed_subagent_ids ?? [],
	};
}

export function parseSubAgentConfigValue(value: SubAgentConfigValue): {
	allow_subagent_calls: boolean;
	allowed_subagent_ids: string[];
} {
	return {
		allow_subagent_calls: value.allow_subagent_calls,
		allowed_subagent_ids: value.allow_subagent_calls ? value.allowed_subagent_ids : [],
	};
}
