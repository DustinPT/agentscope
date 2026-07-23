import { Download, PlusCircle, Trash2, Upload } from 'lucide-react';
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

import type { AgentMCPAsset, AgentSkillAsset, MCPClient } from '@/api';
import { CreateMCPDialog } from '@/components/dialog/MCPDialog';
import { Button } from '@/components/ui/button';
import {
	FieldDescription,
	FieldGroup,
	FieldLabel,
	FieldSet,
} from '@/components/ui/field';
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from '@/components/ui/tooltip';

interface Props {
	mcps: MCPClient[];
	onAddMcps: (mcps: MCPClient[]) => Promise<void>;
	onRemoveMcp: (name: string) => void;
	persistedMcpAssets: AgentMCPAsset[];
	pendingMcpFiles: File[];
	onAddMcpFiles: (files: FileList | null) => void;
	onRemovePersistedMcpAsset: (name: string) => void;
	onRemovePendingMcpFile: (index: number) => void;
	persistedSkills: AgentSkillAsset[];
	pendingSkillFiles: File[];
	onAddSkillFiles: (files: FileList | null) => void;
	onRemovePersistedSkill: (name: string) => void;
	onRemovePendingSkill: (index: number) => void;
	onDownloadSkill: (name: string) => Promise<void>;
}

export function AgentWorkspaceConfigFields({
	mcps,
	onAddMcps,
	onRemoveMcp,
	persistedMcpAssets,
	pendingMcpFiles,
	onAddMcpFiles,
	onRemovePersistedMcpAsset,
	onRemovePendingMcpFile,
	persistedSkills,
	pendingSkillFiles,
	onAddSkillFiles,
	onRemovePersistedSkill,
	onRemovePendingSkill,
	onDownloadSkill,
}: Props) {
	const { t } = useTranslation();
	const skillInputRef = useRef<HTMLInputElement | null>(null);
	const mcpInputRef = useRef<HTMLInputElement | null>(null);

	return (
		<FieldSet>
			<FieldGroup>
				<div className="space-y-3">
					<div>
						<FieldLabel>{t('agent-workspace.legend')}</FieldLabel>
						<FieldDescription>{t('agent-workspace.description')}</FieldDescription>
					</div>

					<div className="space-y-2">
						<div className="flex items-center justify-between">
							<div>
								<div className="text-sm font-medium">{t('agent-workspace.skills.title')}</div>
								<div className="text-muted-foreground text-sm">
									{t('agent-workspace.skills.description')}
								</div>
							</div>
							<Button
								type="button"
								variant="outline"
								onClick={() => skillInputRef.current?.click()}
							>
								<Upload className="size-3.5" />
								{t('agent-workspace.skills.upload')}
							</Button>
						</div>
						<input
							ref={skillInputRef}
							type="file"
							accept=".zip"
							multiple
							className="hidden"
							onChange={(e) => {
								onAddSkillFiles(e.target.files);
								e.currentTarget.value = '';
							}}
						/>
						{persistedSkills.length === 0 && pendingSkillFiles.length === 0 ? (
							<p className="text-muted-foreground text-sm">
								{t('agent-workspace.skills.empty')}
							</p>
						) : (
							<div className="space-y-2">
								{persistedSkills.map((skill) => (
									<div
										key={`persisted-${skill.name}`}
										className="border-border flex items-center justify-between rounded-md border px-3 py-2"
									>
										<div className="min-w-0">
											<div className="truncate text-sm font-medium">{skill.name}</div>
											<TooltipProvider>
												<Tooltip>
													<TooltipTrigger asChild>
														<div className="text-muted-foreground truncate text-xs">
															{skill.description}
														</div>
													</TooltipTrigger>
													<TooltipContent side="top" className="max-w-sm whitespace-pre-wrap">
														{skill.description}
													</TooltipContent>
												</Tooltip>
											</TooltipProvider>
										</div>
										<div className="flex items-center gap-2">
											<Button
												type="button"
												variant="ghost"
												size="sm"
												onClick={() => void onDownloadSkill(skill.name)}
											>
												<Download className="size-3.5" />
												{t('common.download')}
											</Button>
											<Button
												type="button"
												variant="ghost"
												size="sm"
												onClick={() => onRemovePersistedSkill(skill.name)}
											>
												<Trash2 className="size-3.5" />
												{t('common.delete')}
											</Button>
										</div>
									</div>
								))}
								{pendingSkillFiles.map((file, index) => (
									<div
										key={`pending-${file.name}-${index}`}
										className="border-border flex items-center justify-between rounded-md border border-dashed px-3 py-2"
									>
										<div className="min-w-0">
											<div className="truncate text-sm font-medium">{file.name}</div>
											<div className="text-muted-foreground text-xs">
												{t('agent-workspace.skills.pending')}
											</div>
										</div>
										<Button
											type="button"
											variant="ghost"
											size="sm"
											onClick={() => onRemovePendingSkill(index)}
										>
											<Trash2 className="size-3.5" />
											{t('common.delete')}
										</Button>
									</div>
								))}
							</div>
						)}
					</div>

					<div className="space-y-2">
						<div className="flex items-center justify-between">
							<div>
								<div className="text-sm font-medium">{t('agent-workspace.mcps.title')}</div>
								<div className="text-muted-foreground text-sm">
									{t('agent-workspace.mcps.description')}
								</div>
							</div>
							<div className="flex items-center gap-2">
								<Button
									type="button"
									variant="outline"
									onClick={() => mcpInputRef.current?.click()}
								>
									<Upload className="size-3.5" />
									{t('agent-workspace.mcps.upload')}
								</Button>
								<CreateMCPDialog onAdd={onAddMcps}>
									<Button type="button" variant="outline">
										<PlusCircle className="size-3.5" />
										{t('agent-workspace.mcps.add')}
									</Button>
								</CreateMCPDialog>
							</div>
						</div>
						<input
							ref={mcpInputRef}
							type="file"
							accept=".zip"
							multiple
							className="hidden"
							onChange={(e) => {
								onAddMcpFiles(e.target.files);
								e.currentTarget.value = '';
							}}
						/>
						{mcps.length === 0 &&
						persistedMcpAssets.length === 0 &&
						pendingMcpFiles.length === 0 ? (
							<p className="text-muted-foreground text-sm">
								{t('agent-workspace.mcps.empty')}
							</p>
						) : (
							<div className="space-y-2">
								{persistedMcpAssets.map((asset) => (
									<div
										key={`persisted-asset-${asset.name}`}
										className="border-border flex items-center justify-between rounded-md border px-3 py-2"
									>
										<div className="min-w-0">
											<div className="truncate text-sm font-medium">{asset.name}</div>
											<div className="text-muted-foreground truncate text-xs">
												{t('agent-workspace.mcps.asset-saved')}
												{': '}
												{asset.archive_name}
											</div>
										</div>
										<Button
											type="button"
											variant="ghost"
											size="sm"
											onClick={() => onRemovePersistedMcpAsset(asset.name)}
										>
											<Trash2 className="size-3.5" />
											{t('common.delete')}
										</Button>
									</div>
								))}
								{pendingMcpFiles.map((file, index) => (
									<div
										key={`pending-mcp-${file.name}-${index}`}
										className="border-border flex items-center justify-between rounded-md border border-dashed px-3 py-2"
									>
										<div className="min-w-0">
											<div className="truncate text-sm font-medium">{file.name}</div>
											<div className="text-muted-foreground text-xs">
												{t('agent-workspace.mcps.pending')}
											</div>
										</div>
										<Button
											type="button"
											variant="ghost"
											size="sm"
											onClick={() => onRemovePendingMcpFile(index)}
										>
											<Trash2 className="size-3.5" />
											{t('common.delete')}
										</Button>
									</div>
								))}
								{mcps.map((mcp) => (
									<div
										key={mcp.name}
										className="border-border flex items-center justify-between rounded-md border px-3 py-2"
									>
										<div className="min-w-0">
											<div className="truncate text-sm font-medium">{mcp.name}</div>
											<div className="text-muted-foreground truncate text-xs">
												{mcp.mcp_config.type === 'http_mcp'
													? mcp.mcp_config.url
													: `${mcp.mcp_config.command} ${(mcp.mcp_config.args ?? []).join(' ')}`}
											</div>
										</div>
										<Button
											type="button"
											variant="ghost"
											size="sm"
											onClick={() => onRemoveMcp(mcp.name)}
										>
											<Trash2 className="size-3.5" />
											{t('common.delete')}
										</Button>
									</div>
								))}
							</div>
						)}
					</div>
				</div>
			</FieldGroup>
		</FieldSet>
	);
}
