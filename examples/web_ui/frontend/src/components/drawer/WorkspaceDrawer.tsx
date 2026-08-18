import { Eye, Search } from 'lucide-react';
import { type ReactNode, useState } from 'react';

import type { MCPClientStatus, Skill, WorkspaceFileEntry } from '@/api';
import { ProjectDirectoryTab } from '@/components/drawer/ProjectDirectoryTab';
import { Button } from '@/components/ui/button';
import {
        Dialog,
        DialogContent,
        DialogDescription,
        DialogHeader,
        DialogTitle,
} from '@/components/ui/dialog';
import {
	Drawer,
	DrawerContent,
	DrawerDescription,
	DrawerHeader,
	DrawerTitle,
	DrawerTrigger,
} from '@/components/ui/drawer';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import { Item, ItemContent, ItemDescription, ItemTitle } from '@/components/ui/item';
import { Kbd, KbdGroup } from '@/components/ui/kbd';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs.tsx';
import { useTranslation } from '@/i18n/useI18n.ts';

interface WorkspaceDrawerProps {
	children: ReactNode;
        open?: boolean;
        onOpenChange?: (open: boolean) => void;
	mcps: MCPClientStatus[];
	loading?: boolean;
        reconnectingMcpName?: string | null;
        reconnectMcp?: (name: string) => Promise<unknown>;
	skills: Skill[];
	skillsLoading?: boolean;
        listWorkspaceFiles: (path?: string) => Promise<WorkspaceFileEntry[]>;
        buildWorkspaceFileDownloadUrl: (path?: string) => string | null;
        buildWorkspaceFilePreviewUrl: (path: string) => string | null;
}

export function WorkspaceDrawer({
	children,
        open,
        onOpenChange,
	mcps,
	loading = false,
        reconnectingMcpName = null,
        reconnectMcp,
	skills,
	skillsLoading = false,
        listWorkspaceFiles,
        buildWorkspaceFileDownloadUrl,
        buildWorkspaceFilePreviewUrl,
}: WorkspaceDrawerProps) {
	const { t } = useTranslation();
	const [search, setSearch] = useState('');
	const [skillSearch, setSkillSearch] = useState('');
        const [detailMcp, setDetailMcp] = useState<MCPClientStatus | null>(null);

	const filtered = search
		? mcps.filter((m) => m.name.toLowerCase().includes(search.toLowerCase()))
		: mcps;

	const filteredSkills = skillSearch
		? skills.filter((s) => s.name.toLowerCase().includes(skillSearch.toLowerCase()))
		: skills;

	return (
                <Drawer direction="right" open={open} onOpenChange={onOpenChange}>
			<DrawerTrigger asChild>{children}</DrawerTrigger>
			<DrawerContent>
				<DrawerHeader>
                                        <DrawerTitle>{t('workspace-drawer.title')}</DrawerTitle>
                                        <DrawerDescription>{t('workspace-drawer.description')}</DrawerDescription>
				</DrawerHeader>
				<div className="flex flex-col no-scrollbar overflow-y-auto px-4 gap-y-2">
                                        <Tabs defaultValue="mcp">
						<TabsList className={'w-full'}>
							<TabsTrigger value={'mcp'}>MCP</TabsTrigger>
                                                        <TabsTrigger value={'skill'}>{t('workspace-drawer.skillTab')}</TabsTrigger>
                                                        <TabsTrigger value={'file'}>
                                                                {t('workspace-drawer.fileTab')}
                                                        </TabsTrigger>
						</TabsList>
						<TabsContent value={'mcp'} asChild>
							<div className="flex flex-col no-scrollbar overflow-y-auto gap-y-2">
								<span className={'text-muted-foreground text-sm'}>
                                                                        {t('workspace-drawer.mcp.description')}
								</span>
								<InputGroup className="mt-4">
									<InputGroupInput
                                                                                placeholder={t('workspace-drawer.mcp.searchPlaceholder')}
										value={search}
										onChange={(e) => setSearch(e.target.value)}
									/>
									<InputGroupAddon align="inline-end">
										<Search />
									</InputGroupAddon>
								</InputGroup>
								{loading ? (
									<p className="text-muted-foreground text-sm text-center py-4">
                                                                                {t('common.loading')}
									</p>
								) : filtered.length === 0 ? (
									<p className="text-muted-foreground text-sm text-center py-4">
                                                                                {t('workspace-drawer.mcp.empty')}
									</p>
								) : (
									filtered.map((mcp) => (
										<Item key={mcp.name} variant="outline">
											<ItemContent>
												<ItemTitle className="flex items-center gap-x-2">
													<span
														className={`size-2 shrink-0 rounded-full ${mcp.is_healthy ? 'bg-green-500' : 'bg-red-500'}`}
													/>
													{mcp.name}
												</ItemTitle>
                                                                                                <ItemDescription className="flex flex-col gap-y-2">
													<KbdGroup>
														<Kbd>
															{mcp.mcp_config.type === 'stdio_mcp'
																? 'STDIO'
																: 'HTTP'}
														</Kbd>
                                                                                                                <Kbd>
                                                                                                                        {t('workspace-drawer.mcp.toolCount', {
                                                                                                                                count: mcp.tools.length,
                                                                                                                        })}
                                                                                                                </Kbd>
													</KbdGroup>
                                                                                                        {mcp.connection_error ? (
                                                                                                                <div className="flex items-start gap-2">
                                                                                                                        <p className="text-destructive text-xs break-words line-clamp-2 flex-1">
                                                                                                                                {mcp.connection_error}
                                                                                                                        </p>
                                                                                                                        <Button
                                                                                                                                size="icon-xs"
                                                                                                                                variant="ghost"
                                                                                                                                className="shrink-0"
                                                                                                                                onClick={() =>
                                                                                                                                        setDetailMcp(mcp)
                                                                                                                                }
                                                                                                                                aria-label={t(
                                                                                                                                        'workspace-drawer.mcp.viewErrorDetail',
                                                                                                                                )}
                                                                                                                                title={t(
                                                                                                                                        'workspace-drawer.mcp.viewErrorDetail',
                                                                                                                                )}
                                                                                                                        >
                                                                                                                                <Eye className="size-3.5" />
                                                                                                                        </Button>
                                                                                                                </div>
                                                                                                        ) : null}
                                                                                                        {!mcp.is_healthy && reconnectMcp ? (
                                                                                                                <div>
                                                                                                                        <Button
                                                                                                                                size="xs"
                                                                                                                                variant="outline"
                                                                                                                                disabled={
                                                                                                                                        reconnectingMcpName ===
                                                                                                                                        mcp.name
                                                                                                                                }
                                                                                                                                onClick={() =>
                                                                                                                                        reconnectMcp(mcp.name)
                                                                                                                                }
                                                                                                                        >
                                                                                                                                {reconnectingMcpName ===
                                                                                                                                mcp.name
                                                                                                                                        ? t(
                                                                                                                                                  'workspace-drawer.mcp.reconnecting',
                                                                                                                                          )
                                                                                                                                        : t(
                                                                                                                                                  'workspace-drawer.mcp.reconnect',
                                                                                                                                          )}
                                                                                                                        </Button>
                                                                                                                </div>
                                                                                                        ) : null}
												</ItemDescription>
											</ItemContent>
										</Item>
									))
								)}
							</div>
						</TabsContent>
						<TabsContent value={'skill'} asChild>
							<div className="flex flex-col no-scrollbar overflow-y-auto gap-y-2">
								<span className={'text-muted-foreground text-sm'}>
                                                                        {t('workspace-drawer.skill.description')}
								</span>
								<InputGroup className="mt-4">
									<InputGroupInput
                                                                                placeholder={t('workspace-drawer.skill.searchPlaceholder')}
										value={skillSearch}
										onChange={(e) => setSkillSearch(e.target.value)}
									/>
									<InputGroupAddon align="inline-end">
										<Search />
									</InputGroupAddon>
								</InputGroup>
								{skillsLoading ? (
									<p className="text-muted-foreground text-sm text-center py-4">
                                                                                {t('common.loading')}
									</p>
								) : filteredSkills.length === 0 ? (
									<p className="text-muted-foreground text-sm text-center py-4">
                                                                                {t('workspace-drawer.skill.empty')}
									</p>
								) : (
									filteredSkills.map((skill) => (
										<Item key={skill.name} variant="outline">
											<ItemContent>
												<ItemTitle>{skill.name}</ItemTitle>
												<ItemDescription>
													{skill.description}
												</ItemDescription>
											</ItemContent>
										</Item>
									))
								)}
							</div>
						</TabsContent>
                                                <TabsContent value={'file'} asChild>
                                                        <ProjectDirectoryTab
                                                                listWorkspaceFiles={listWorkspaceFiles}
                                                                buildWorkspaceFileDownloadUrl={buildWorkspaceFileDownloadUrl}
                                                                buildWorkspaceFilePreviewUrl={buildWorkspaceFilePreviewUrl}
                                                        />
                                                </TabsContent>
					</Tabs>
				</div>
                                <Dialog open={detailMcp !== null} onOpenChange={(open) => !open && setDetailMcp(null)}>
                                        <DialogContent className="!max-w-4xl">
                                                <DialogHeader>
                                                        <DialogTitle>
                                                                {t('workspace-drawer.mcp.errorDialogTitle', {
                                                                        name: detailMcp?.name ?? '',
                                                                })}
                                                        </DialogTitle>
                                                        <DialogDescription>
                                                                {t('workspace-drawer.mcp.errorDialogDescription')}
                                                        </DialogDescription>
                                                </DialogHeader>
                                                <pre className="max-h-[70vh] overflow-auto rounded-md bg-muted p-3 font-mono text-xs leading-5 whitespace-pre-wrap break-all">
                                                        {detailMcp?.connection_error_detail || detailMcp?.connection_error}
                                                </pre>
                                        </DialogContent>
                                </Dialog>
			</DrawerContent>
		</Drawer>
	);
}
