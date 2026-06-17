import { Bot } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import type { SubAgentSessionView } from '@/api';
import {
	Sidebar,
	SidebarContent,
	SidebarFooter,
	SidebarGroup,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarHeader,
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
} from '@/components/ui/sidebar';

interface Props {
	rootAgentId: string;
	rootSessionId: string;
	currentSessionId: string;
	children: SubAgentSessionView[];
}

function flattenChildren(
	children: SubAgentSessionView[],
	level = 0,
): Array<{ node: SubAgentSessionView; level: number }> {
	return children.flatMap((node) => [
		{ node, level },
		...flattenChildren(node.children, level + 1),
	]);
}

export function SubAgentSidebar({
	rootAgentId,
	rootSessionId,
	currentSessionId,
	children,
}: Props) {
	const navigate = useNavigate();
	const rows = flattenChildren(children);

	return (
		<Sidebar collapsible="none" className="w-64 border-r">
			<SidebarHeader>
				<div className="flex flex-col gap-y-1 px-2 py-1">
					<span className="text-muted-foreground text-xs uppercase tracking-wide">
						Sub Sessions
					</span>
				</div>
			</SidebarHeader>
			<SidebarContent>
				<SidebarGroup>
					<SidebarGroupLabel>Children</SidebarGroupLabel>
					<SidebarGroupContent>
						{rows.length === 0 ? (
							<p className="px-3 py-2 text-xs text-muted-foreground">
								No sub sessions
							</p>
						) : (
							<SidebarMenu>
								{rows.map(({ node, level }) => (
									<SidebarMenuItem key={node.session.id}>
										<SidebarMenuButton
											isActive={node.session.id === currentSessionId}
											onClick={() =>
												navigate(
													`/chat/${rootAgentId}/${rootSessionId}/${node.session.id}`,
												)
											}
										>
											<Bot />
											<span
												className="truncate"
												style={{ paddingLeft: `${level * 12}px` }}
											>
												{node.session.config.name}
											</span>
										</SidebarMenuButton>
									</SidebarMenuItem>
								))}
							</SidebarMenu>
						)}
					</SidebarGroupContent>
				</SidebarGroup>
			</SidebarContent>
			<SidebarFooter />
		</Sidebar>
	);
}
