import type { ContentBlock } from '@agentscope-ai/agentscope/message';
import type { Msg } from '@agentscope-ai/agentscope/message';
import { UserMsg } from '@agentscope-ai/agentscope/message';
import {
	BotMessageSquare,
	CalendarClock,
	Ellipsis,
	MessageSquareDashed,
	Pencil,
	Plus,
	Settings2,
	Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useMatch, useNavigate, useParams } from 'react-router-dom';

import { ChatViewport } from './ChatViewport';
import { chatApi } from '@/api';
import type { PermissionMode, SessionRecord, SessionView } from '@/api';
import { buildTemporarySessionTitle } from '@/components/chat/inputUtils';
import {
	SessionDraftComposer,
	type SessionDraftState,
} from '@/components/chat/SessionDraftComposer';
import { AgentDialog } from '@/components/dialog/AgentDialog';
import { AgentPackageImportDialog } from '@/components/dialog/AgentPackageImportDialog';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { EditAgentDialog } from '@/components/dialog/EditAgentDialog';
import { RenameSessionDialog } from '@/components/dialog/RenameSessionDialog';
import { SubAgentSidebar } from '@/components/subagent/SubAgentSidebar';
import { TeamSidebar } from '@/components/team/TeamSidebar';
import { ChatTourController } from '@/components/tour/ChatTourController';
import { Button } from '@/components/ui/button';
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
	Empty,
	EmptyHeader,
	EmptyTitle,
	EmptyDescription,
	EmptyContent,
	EmptyMedia,
} from '@/components/ui/empty';
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from '@/components/ui/select';
import {
	Sidebar,
	SidebarContent,
	SidebarFooter,
	SidebarGroup,
	SidebarGroupAction,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarHeader,
	SidebarMenu,
	SidebarMenuAction,
	SidebarMenuButton,
	SidebarMenuItem,
} from '@/components/ui/sidebar';
import { AudioProvider } from '@/context/AudioContext';
import { useAgents } from '@/hooks/useAgents';
import { useSessions } from '@/hooks/useSessions';
import { useTranslation } from '@/i18n/useI18n.ts';

/**
 * The chat page's outer shell. Responsibilities split cleanly:
 *
 * - **This component** owns *which* `(agent, session)` is being
 *   viewed. The URL is the single source of truth: every selection
 *   (agent dropdown, session row, team member, new session) is a
 *   ``navigate(...)`` call. State is derived from ``useParams``,
 *   never duplicated in React state. Renders the main left sidebar
 *   (agent picker + session list + create/rename/delete actions) and
 *   computes the ``effective`` ids to feed the chat viewport.
 * - **`ChatViewport`** owns *what* to render for that pair: messages,
 *   model selector, permission mode, workspace drawer, team sidebar.
 *
 * Splitting along this seam means switching between the leader's
 * session and a focused team member is just a prop change for the
 * viewport — the leader's session list stays anchored in this outer
 * sidebar. Driving everything off URL also gets us browser back /
 * forward, shareable links, and refresh-preserving state for free.
 *
 * @returns The chat page JSX.
 */
const ChatPageInner = () => {
	const navigate = useNavigate();
	const draftMatch = useMatch('/chat/:agentId/new');
	const {
		agentId: urlAgentId,
		sessionId: urlSessionId,
		memberId: urlFocusedSessionId,
	} = useParams<{
		agentId?: string;
		sessionId?: string;
		memberId?: string;
	}>();
	const { t } = useTranslation();
	const {
		agents,
		refetch: refetchAgents,
		remove: removeAgent,
		importPackage,
		composeUpdate,
	} = useAgents();
	const {
		sessions,
		refetch: refetchSessions,
		create: createSession,
		update: updateSession,
		remove: removeSession,
	} = useSessions(urlAgentId ?? null);

	const [sidebarOpen, setSidebarOpen] = useState(true);
	const [editOpen, setEditOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [renameOpen, setRenameOpen] = useState(false);
	const [renameSession, setRenameSession] = useState<SessionRecord | null>(null);
	const [deleteSessionOpen, setDeleteSessionOpen] = useState(false);
	const [sessionToDelete, setSessionToDelete] = useState<SessionRecord | null>(null);
	const [draftsByAgentId, setDraftsByAgentId] = useState<Record<string, SessionDraftState>>(
		{},
	);
	const [draftSubmitting, setDraftSubmitting] = useState(false);
	const [pendingInitialUserMsgs, setPendingInitialUserMsgs] = useState<Record<string, Msg>>({});

	const isDraftRoute = draftMatch !== null && !urlFocusedSessionId;
	const selectedAgent = agents.find((a) => a.id === urlAgentId) ?? null;
	const currentView = isDraftRoute
		? null
		: (sessions.find((v) => v.session.id === urlSessionId) ?? null);
	const hasScheduleSessions = sessions.some((v) => v.session.source === 'schedule');

	const getPermissionMode = useCallback((view: SessionView | null): PermissionMode => {
		const mode = (view?.session.state?.permission_context as Record<string, unknown> | undefined)
			?.mode;
		return typeof mode === 'string' ? (mode as PermissionMode) : 'default';
	}, []);

	const buildDraftSeed = useCallback(
		(view: SessionView | null): SessionDraftState => ({
			text: '',
			files: [],
			chatModelConfig: view?.session.config.chat_model_config ?? null,
			fallbackChatModelConfig: view?.session.config.fallback_chat_model_config ?? null,
			permissionMode: getPermissionMode(view),
		}),
		[getPermissionMode],
	);

	const activeDraft =
		urlAgentId === undefined
			? null
			: (draftsByAgentId[urlAgentId] ?? buildDraftSeed(currentView ?? sessions[0] ?? null));

	// "Inner focus" — when the URL carries a third `:memberId` segment
	// the user is drilling into a team member's chat. The main sidebar
	// stays anchored on the outer (leader) session; only the chat
	// viewport follows this inner focus. When `urlMemberId` is
	// undefined or doesn't resolve to a known team member, the inner
	// focus collapses back to the outer (leader) session.
	const flattenChildren = (children = currentView?.children ?? []) => {
		const walk = (nodes: typeof children): typeof children =>
			nodes.flatMap((node) => [node, ...walk(node.children)]);
		return walk(children);
	};
	const focusedChildSession = urlFocusedSessionId
		? (flattenChildren().find((child) => child.session.id === urlFocusedSessionId) ?? null)
		: null;
	const focusedMember =
		urlFocusedSessionId && currentView?.team
			? (currentView.team.members.find((m) => m.session_id === urlFocusedSessionId) ?? null)
			: null;
	const effectiveAgentId = focusedChildSession
		? focusedChildSession.agent.id
		: focusedMember && focusedMember.session_id
			? focusedMember.agent.id
			: (urlAgentId ?? null);
	const effectiveSessionId = focusedChildSession
		? focusedChildSession.session.id
		: focusedMember && focusedMember.session_id
			? focusedMember.session_id
			: (urlSessionId ?? null);
	const subSessionMeta = focusedChildSession
		? {
				agentName:
					focusedChildSession.agent.data.name || focusedChildSession.agent.id,
				sessionName:
					focusedChildSession.session.config.name ||
					focusedChildSession.session.id,
				parentSessionName: currentView?.session.config.name || urlSessionId || '',
			}
		: null;

	// Redirect: URL is missing an agent → pick the first one and rewrite
	// the URL in-place (replace so we don't pollute history).
	useEffect(() => {
		if (!urlAgentId && agents.length > 0) {
			navigate(`/chat/${agents[0].id}`, { replace: true });
		}
	}, [agents, urlAgentId, navigate]);

	// Redirect: URL has an agent but no session, or its sessionId no
	// longer exists for this agent → pick the first available session.
	useEffect(() => {
		if (isDraftRoute) return;
		if (!urlAgentId || sessions.length === 0) return;
		const matches = urlSessionId && sessions.some((v) => v.session.id === urlSessionId);
		if (matches) return;
		navigate(`/chat/${urlAgentId}/${sessions[0].session.id}`, { replace: true });
	}, [isDraftRoute, urlAgentId, urlSessionId, sessions, navigate]);

	useEffect(() => {
		if (!isDraftRoute || !urlAgentId) return;
		setDraftsByAgentId((prev) => {
			if (prev[urlAgentId]) return prev;
			return {
				...prev,
				[urlAgentId]: buildDraftSeed(sessions[0] ?? null),
			};
		});
	}, [buildDraftSeed, isDraftRoute, urlAgentId, sessions]);

	/**
	 * Create a new session under the currently selected agent and
	 * pre-fill it with the model + fallback the currently open session
	 * is using (so "new chat" inherits whatever the user just had
	 * configured). Falls back to any other session under this agent
	 * when there is no current one — keeps the model choice sticky
	 * across "delete last → create new" instead of dropping back to
	 * whatever ChatViewport's auto-pick happens to land on. Navigates
	 * to the freshly created session.
	 */
	const handleCreateSession = async () => {
		if (!urlAgentId) return;
		setDraftsByAgentId((prev) => {
			if (prev[urlAgentId]) return prev;
			return {
				...prev,
				[urlAgentId]: buildDraftSeed(currentView ?? sessions[0] ?? null),
			};
		});
		navigate(`/chat/${urlAgentId}/new`);
	};

	const handleDraftChange = (
		updater: SessionDraftState | ((prev: SessionDraftState) => SessionDraftState),
	) => {
		if (!urlAgentId) return;
		setDraftsByAgentId((prev) => {
			const base = prev[urlAgentId] ?? buildDraftSeed(currentView ?? sessions[0] ?? null);
			const nextDraft = typeof updater === 'function' ? updater(base) : updater;
			return {
				...prev,
				[urlAgentId]: nextDraft,
			};
		});
	};

	const handleDraftSubmit = async (content: ContentBlock[]) => {
		if (!urlAgentId || !activeDraft?.chatModelConfig || draftSubmitting) return;

		setDraftSubmitting(true);
		const tempTitle = buildTemporarySessionTitle(content, t('chat.newSession'));
		try {
			const res = await createSession({
				agent_id: urlAgentId,
				name: tempTitle,
				chat_model_config: activeDraft.chatModelConfig,
				fallback_chat_model_config: activeDraft.fallbackChatModelConfig,
				permission_mode: activeDraft.permissionMode,
			});
			const userMsg = UserMsg({ name: 'user', content });
			setPendingInitialUserMsgs((prev) => ({
				...prev,
				[res.session_id]: userMsg,
			}));
			navigate(`/chat/${urlAgentId}/${res.session_id}`);
			await chatApi.trigger({
				agent_id: urlAgentId,
				session_id: res.session_id,
				input: userMsg,
			});
			setDraftsByAgentId((prev) => {
				if (!(urlAgentId in prev)) return prev;
				const next = { ...prev };
				delete next[urlAgentId];
				return next;
			});
		} finally {
			setDraftSubmitting(false);
		}
	};

	const handleAgentDeleted = async () => {
		navigate('/chat', { replace: true });
		await refetchAgents();
	};

	const handleDeleteSession = async (sessionId: string) => {
		await removeSession(sessionId);
		// If we just removed the session the URL is pointing at, fall
		// back to the parent /chat/:agentId path; the redirect effect
		// will then pick the next available session.
		if (sessionId === urlSessionId && urlAgentId) {
			navigate(`/chat/${urlAgentId}`, { replace: true });
		}
	};

	const requestDeleteSession = (session: SessionRecord) => {
		setSessionToDelete(session);
		setDeleteSessionOpen(true);
	};

	const handleRenameConfirm = async (name: string) => {
		if (!renameSession) return;
		await updateSession(renameSession.id, { name });
	};

	return (
		<div className="flex h-full w-full">
			{sidebarOpen && (
				<Sidebar collapsible="none" className="border-r">
					<SidebarHeader>
						<div className="flex flex-col gap-y-2">
							<span className="text-muted-foreground text-xs">
								{localStorage.getItem('server_url')}
							</span>
							<div className="flex flex-row gap-x-2 items-center">
								<Select
									value={urlAgentId ?? ''}
									onValueChange={(id) => navigate(`/chat/${id}`)}
								>
									<SelectTrigger className="w-full" size="sm">
										<SelectValue
											placeholder={t('chat.agent.selectPlaceholder')}
										/>
									</SelectTrigger>
									<SelectContent position="popper">
										{agents.length === 0 ? (
											<Empty className="border-none py-4">
												<EmptyHeader>
													<EmptyTitle>
														{t('chat.agent.emptyTitle')}
													</EmptyTitle>
													<EmptyDescription>
														{t('chat.agent.emptyDescription')}
													</EmptyDescription>
												</EmptyHeader>
											</Empty>
										) : (
											agents.map((agent) => (
												<SelectItem key={agent.id} value={agent.id}>
													{agent.data.name}
												</SelectItem>
											))
										)}
									</SelectContent>
								</Select>
								<Button
									size="icon"
									variant="ghost"
									disabled={!urlAgentId}
									onClick={() => setEditOpen(true)}
								>
									<Settings2 />
								</Button>
								<Button
									size="icon"
									variant="ghost"
									disabled={!urlAgentId}
									onClick={() => setDeleteOpen(true)}
								>
									<Trash2 className="text-destructive" />
								</Button>
							</div>
							<div className="flex flex-wrap gap-2">
								<AgentDialog onCreated={refetchAgents} triggerId="tour-create-agent" />
								<AgentPackageImportDialog
									importPackage={importPackage}
									onImported={refetchAgents}
								/>
							</div>
						</div>
					</SidebarHeader>
					<SidebarContent className="my-5">
						<SidebarGroup>
							<SidebarGroupLabel>{t('chat.session.label')}</SidebarGroupLabel>
							<SidebarGroupAction>
								<Button
									id="tour-create-session"
									size="icon-xs"
									variant="default"
									disabled={!urlAgentId}
									onClick={handleCreateSession}
								>
									<Plus />
								</Button>
							</SidebarGroupAction>
							<SidebarGroupContent>
								{sessions.length === 0 ? (
									<Empty className="border-none py-4 min-h-50">
										<EmptyHeader>
											<EmptyMedia variant="icon">
												<MessageSquareDashed />
											</EmptyMedia>
											<EmptyTitle>{t('chat.session.emptyTitle')}</EmptyTitle>
											<EmptyDescription>
												{urlAgentId
													? t('chat.session.emptyHasAgent')
													: t('chat.session.emptyNoAgent')}
											</EmptyDescription>
										</EmptyHeader>
										<EmptyContent>
											<Button
												variant="outline"
												size="sm"
												disabled={!urlAgentId}
												onClick={handleCreateSession}
											>
												{t('chat.newSession')}
											</Button>
										</EmptyContent>
									</Empty>
								) : (
									<SidebarMenu>
										{sessions.map((view) => {
											const session = view.session;
											return (
												<SidebarMenuItem key={session.id}>
													<SidebarMenuButton
														isActive={urlSessionId === session.id}
														onClick={() =>
															navigate(
																`/chat/${urlAgentId}/${session.id}`,
															)
														}
													>
														{hasScheduleSessions &&
															(session.source === 'schedule' ? (
																<CalendarClock />
															) : (
																<BotMessageSquare />
															))}
														<span className="truncate">
															{session.config.name || session.id}
														</span>
													</SidebarMenuButton>
													<SidebarMenuAction showOnHover>
														<DropdownMenu>
															<DropdownMenuTrigger asChild>
																<Ellipsis />
															</DropdownMenuTrigger>
															<DropdownMenuContent
																side="right"
																align="start"
															>
																<DropdownMenuItem
																	onClick={() => {
																		setRenameSession(session);
																		setRenameOpen(true);
																	}}
																>
																	<Pencil />
																	{t('session-menu.rename')}
																</DropdownMenuItem>
																<DropdownMenuItem
																	variant="destructive"
																	onClick={() =>
																		requestDeleteSession(
																			session,
																		)
																	}
																>
																	<Trash2 />
																	{t('session-menu.delete')}
																</DropdownMenuItem>
															</DropdownMenuContent>
														</DropdownMenu>
													</SidebarMenuAction>
												</SidebarMenuItem>
											);
										})}
									</SidebarMenu>
								)}
							</SidebarGroupContent>
						</SidebarGroup>
					</SidebarContent>
					<SidebarFooter />
				</Sidebar>
			)}
			{/*
			 * Team sidebar lives at the outer page level (not inside
			 * ChatViewport) so navigating between leader and member
			 * sessions does NOT unmount it. The team data comes from
			 * the leader's session view, which is stable across that
			 * navigation; only `currentSessionId` changes to drive
			 * row highlighting.
			 */}
			{currentView?.team && effectiveSessionId && (
				<TeamSidebar team={currentView.team} currentSessionId={effectiveSessionId} />
			)}
			{currentView?.children && currentView.children.length > 0 && urlAgentId && urlSessionId && (
				<SubAgentSidebar
					rootAgentId={urlAgentId}
					rootSessionId={urlSessionId}
					currentSessionId={effectiveSessionId ?? urlSessionId}
					children={currentView.children}
				/>
			)}
			<div className="flex flex-1 min-w-0">
				{isDraftRoute && urlAgentId && activeDraft ? (
					<SessionDraftComposer
						agentId={urlAgentId}
						draft={activeDraft}
						onDraftChange={handleDraftChange}
						onSubmit={handleDraftSubmit}
						submitting={draftSubmitting}
					/>
				) : (
					<ChatViewport
						agentId={effectiveAgentId}
						sessionId={effectiveSessionId}
						sessionViewOverride={focusedChildSession}
						subSessionMeta={subSessionMeta}
						pendingInitialUserMsg={
							effectiveSessionId
								? (pendingInitialUserMsgs[effectiveSessionId] ?? null)
								: null
						}
						onPendingInitialUserMsgConsumed={() => {
							if (!effectiveSessionId) return;
							setPendingInitialUserMsgs((prev) => {
								if (!(effectiveSessionId in prev)) return prev;
								const next = { ...prev };
								delete next[effectiveSessionId];
								return next;
							});
						}}
						onReturnToRootSession={
							urlAgentId && urlSessionId
								? () => navigate(`/chat/${urlAgentId}/${urlSessionId}`)
								: undefined
						}
						onTeamUpdated={refetchSessions}
						onAgentUpdated={refetchAgents}
					/>
				)}
			</div>
			{selectedAgent && (
				<>
					<EditAgentDialog
						open={editOpen}
						onOpenChange={setEditOpen}
						agent={selectedAgent}
						agents={agents}
						composeUpdate={composeUpdate}
						onUpdated={refetchAgents}
					/>
					<DeleteDialog
						open={deleteOpen}
						onOpenChange={setDeleteOpen}
						title={t('common.deleteTitle', {
							entity: t('dialog-agent-delete.entity'),
							name: selectedAgent.data.name,
						})}
						description={t('common.deleteDescription')}
						confirmLabel={t('dialog-agent-delete.confirm')}
						onConfirm={async () => {
							await removeAgent(selectedAgent.id);
							await handleAgentDeleted();
						}}
					/>
				</>
			)}
			<RenameSessionDialog
				open={renameOpen}
				onOpenChange={setRenameOpen}
				currentName={renameSession?.config.name ?? renameSession?.id ?? ''}
				onConfirm={handleRenameConfirm}
			/>
			<DeleteDialog
				open={deleteSessionOpen}
				onOpenChange={setDeleteSessionOpen}
				title={t('common.deleteTitle', {
					entity: t('dialog-session-delete.entity'),
					name: sessionToDelete?.config.name || sessionToDelete?.id || '',
				})}
				description={t('common.deleteDescription')}
				confirmLabel={t('dialog-session-delete.confirm')}
				onConfirm={async () => {
					if (sessionToDelete) {
						await handleDeleteSession(sessionToDelete.id);
					}
				}}
			/>
			<ChatTourController
				agentsCount={agents.length}
				sessionsCount={sessions.length}
				onEnsureSidebarOpen={() => setSidebarOpen(true)}
			/>
		</div>
	);
};

export const ChatPage = () => (
	<AudioProvider>
		<ChatPageInner />
	</AudioProvider>
);
