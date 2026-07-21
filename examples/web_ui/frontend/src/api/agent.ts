import { client } from './client';
import type {
	AgentComposeResponse,
	AgentListResponse,
	AgentRecord,
	AgentSchemaResponse,
	CreateAgentRequest,
	CreateAgentResponse,
	UpdateAgentRequest,
} from './types';

export const agentApi = {
	list: () => client.get<AgentListResponse>('/agent/'),

	getSchema: () => client.get<AgentSchemaResponse>('/agent/schema'),

	create: (body: CreateAgentRequest) => client.post<CreateAgentResponse>('/agent/', body),

	update: (agentId: string, body: UpdateAgentRequest) =>
		client.patch<AgentRecord>(`/agent/${agentId}`, body),

	composeCreate: (body: FormData) =>
		client.postForm<AgentComposeResponse>('/agent/compose', body),

	composeUpdate: (agentId: string, body: FormData) =>
		client.putForm<AgentComposeResponse>(`/agent/${agentId}/compose`, body),

	downloadSkill: (agentId: string, skillName: string) =>
		client.getBlob(`/agent/${agentId}/skills/${encodeURIComponent(skillName)}/download`),

	delete: (agentId: string) => client.delete(`/agent/${agentId}`),
};
