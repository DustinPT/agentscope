import { Search } from 'lucide-react';
import { type ReactNode, useState } from 'react';

import type { MCPClientStatus, ProjectDirectoryEntry, Skill } from '@/api';
import { ProjectDirectoryTab } from '@/components/drawer/ProjectDirectoryTab';
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
	mcps: MCPClientStatus[];
	loading?: boolean;
	skills: Skill[];
	skillsLoading?: boolean;
        listProjectDirectory: (path?: string) => Promise<ProjectDirectoryEntry[]>;
        buildProjectDirectoryDownloadUrl: (path?: string) => string | null;
        buildProjectDirectoryPreviewUrl: (path: string) => string | null;
}

export function WorkspaceDrawer({
	children,
	mcps,
	loading = false,
	skills,
	skillsLoading = false,
        listProjectDirectory,
        buildProjectDirectoryDownloadUrl,
        buildProjectDirectoryPreviewUrl,
}: WorkspaceDrawerProps) {
	const { t } = useTranslation();
	const [search, setSearch] = useState('');
	const [skillSearch, setSkillSearch] = useState('');

	const filtered = search
		? mcps.filter((m) => m.name.toLowerCase().includes(search.toLowerCase()))
		: mcps;

	const filteredSkills = skillSearch
		? skills.filter((s) => s.name.toLowerCase().includes(skillSearch.toLowerCase()))
		: skills;

	return (
		<Drawer direction="right">
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
                                                        <TabsTrigger value={'project'}>
                                                                {t('workspace-drawer.projectTab')}
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
												<ItemDescription>
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
                                                <TabsContent value={'project'} asChild>
                                                        <ProjectDirectoryTab
                                                                listProjectDirectory={listProjectDirectory}
                                                                buildProjectDirectoryDownloadUrl={
                                                                        buildProjectDirectoryDownloadUrl
                                                                }
                                                                buildProjectDirectoryPreviewUrl={
                                                                        buildProjectDirectoryPreviewUrl
                                                                }
                                                        />
                                                </TabsContent>
					</Tabs>
				</div>
			</DrawerContent>
		</Drawer>
	);
}
