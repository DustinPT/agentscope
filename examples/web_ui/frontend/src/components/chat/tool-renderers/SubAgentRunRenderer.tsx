import type { ReactNode } from 'react';

import { SubAgentRunGroup } from './SubAgentRunRenderer.view';
import type { ToolCallWithResult, ToolRenderer } from './types';

type SubAgentRunResult = {
	agent_id?: string;
	agent_name?: string;
	session_id?: string;
	session_name?: string;
	mode?: string;
	status?: string;
};

type SubAgentRunInput = {
	agent_id?: string;
	session_name?: string;
	session_id?: string;
};

function parseResult(call: ToolCallWithResult): SubAgentRunResult | null {
	const output = call.result?.output;
	if (!output || typeof output === 'string') return null;
	const text = output.find((block) => block.type === 'text');
	if (!text || text.type !== 'text') return null;
	try {
		return JSON.parse(text.text) as SubAgentRunResult;
	} catch {
		return null;
	}
}

function parseInput(input: string): SubAgentRunInput | null {
	try {
		return JSON.parse(input) as SubAgentRunInput;
	} catch {
		return null;
	}
}

export const SubAgentRunRenderer: ToolRenderer = {
	getDisplayName: () => 'SubAgentRun',
	renderGroup: (calls: ToolCallWithResult[]): ReactNode => (
		<SubAgentRunGroup calls={calls} parseInput={parseInput} parseResult={parseResult} />
	),
};
