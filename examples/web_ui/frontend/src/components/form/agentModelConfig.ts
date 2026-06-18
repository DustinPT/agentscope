import type { AgentRecord, ChatModelConfig } from '@/api';

export interface AgentModelConfigValue {
	default_chat_model_config: ChatModelConfig | null;
}

export function createAgentModelConfigValue(agent?: AgentRecord): AgentModelConfigValue {
	return {
		default_chat_model_config: agent?.data.default_chat_model_config ?? null,
	};
}

export function parseAgentModelConfigValue(value: AgentModelConfigValue): {
	default_chat_model_config: ChatModelConfig | null;
} {
	return {
		default_chat_model_config: value.default_chat_model_config,
	};
}
