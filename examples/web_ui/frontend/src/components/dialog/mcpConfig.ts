import type { MCPClient, HttpMCPConfig, StdioMCPConfig } from '@/api/types';

export function parseMcpConfig(
	raw: string,
	keepAlive: boolean,
	t: (key: string, opts?: Record<string, string>) => string,
): MCPClient[] {
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch (e) {
		throw new Error(t('dialog-mcp-create.parseError', { message: (e as Error).message }));
	}

	const obj = parsed as Record<string, unknown>;
	const servers = obj.mcpServers as Record<string, Record<string, unknown>> | undefined;
	if (!servers || typeof servers !== 'object') {
		throw new Error(t('dialog-mcp-create.missingMcpServers'));
	}

	const entries = Object.entries(servers);
	if (entries.length === 0) {
		throw new Error(t('dialog-mcp-create.emptyMcpServers'));
	}

	return entries.map(([name, config]) => {
		let mcp_config: StdioMCPConfig | HttpMCPConfig;
		if ('url' in config) {
			mcp_config = {
				type: 'http_mcp',
				url: config.url as string,
				headers: (config.headers as Record<string, string> | undefined) ?? null,
				timeout: (config.timeout as number | undefined) ?? null,
			};
		} else {
			mcp_config = {
				type: 'stdio_mcp',
				command: config.command as string,
				args: (config.args as string[] | undefined) ?? null,
				env: (config.env as Record<string, string> | undefined) ?? null,
				cwd: (config.cwd as string | undefined) ?? null,
			};
		}
		return { name, is_stateful: keepAlive, mcp_config };
	});
}
