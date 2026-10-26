import { FolderTree, Pencil, Plus, Search, ShieldCheck, Trash2, UserRound } from 'lucide-react';
import * as React from 'react';
import { toast } from 'sonner';

import type {
        CreateSandboxPermissionRequest,
        DeleteSandboxPermissionRequest,
        SandboxGrantScope,
        SandboxPermissionGrant,
        SandboxPermissionGrantInput,
        UpdateSandboxPermissionRequest,
        WorkspaceSandboxPermissionsResponse,
} from '@/api';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { SandboxPermissionDialog } from '@/components/drawer/SandboxPermissionDialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
        Empty,
        EmptyDescription,
        EmptyHeader,
        EmptyMedia,
        EmptyTitle,
} from '@/components/ui/empty';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group';
import {
        Item,
        ItemActions,
        ItemContent,
        ItemHeader,
        ItemTitle,
} from '@/components/ui/item';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs.tsx';
import { useTranslation } from '@/i18n/useI18n';

interface Props {
        records: WorkspaceSandboxPermissionsResponse;
        loading: boolean;
        onCreate: (body: CreateSandboxPermissionRequest) => Promise<unknown>;
        onUpdate: (body: UpdateSandboxPermissionRequest) => Promise<unknown>;
        onDelete: (body: DeleteSandboxPermissionRequest) => Promise<unknown>;
}

const SCOPE_ORDER: SandboxGrantScope[] = ['workspace', 'agent', 'user'];

function toGrantInput(grant: SandboxPermissionGrant): SandboxPermissionGrantInput {
        return {
                resource_type: grant.resource_type,
                pattern: grant.pattern,
                operations: grant.operations,
        };
}

function grantKey(grant: SandboxPermissionGrant) {
        return [
                grant.scope,
                grant.resource_type,
                grant.pattern,
                grant.operations.join(','),
        ].join('::');
}

export function SandboxPermissionTab({ records, loading, onCreate, onUpdate, onDelete }: Props) {
        const { t } = useTranslation();
        const [activeScope, setActiveScope] = React.useState<SandboxGrantScope>('workspace');
        const [dialogOpen, setDialogOpen] = React.useState(false);
        const [editingGrant, setEditingGrant] = React.useState<SandboxPermissionGrant | null>(null);
        const [deletingGrant, setDeletingGrant] = React.useState<SandboxPermissionGrant | null>(null);
        const [search, setSearch] = React.useState('');

        const scopedRecords = React.useMemo(
                () => ({
                        workspace: records.workspace,
                        agent: records.agent,
                        user: records.user,
                }),
                [records],
        );

        const scopeIcon = (scope: SandboxGrantScope) => {
                if (scope === 'workspace') return <FolderTree className="size-4" />;
                if (scope === 'agent') return <ShieldCheck className="size-4" />;
                return <UserRound className="size-4" />;
        };

        const handleCreate = async (value: SandboxPermissionGrantInput) => {
                await onCreate({
                        scope: activeScope,
                        ...value,
                });
                toast.success(t('workspace-drawer.permission.messages.createSuccess'));
        };

        const handleUpdate = async (value: SandboxPermissionGrantInput) => {
                if (!editingGrant) return;
                await onUpdate({
                        scope: activeScope,
                        original: toGrantInput(editingGrant),
                        updated: value,
                });
                toast.success(t('workspace-drawer.permission.messages.updateSuccess'));
                setEditingGrant(null);
        };

        const handleDelete = async () => {
                if (!deletingGrant) return;
                await onDelete({
                        scope: activeScope,
                        ...toGrantInput(deletingGrant),
                });
                toast.success(t('workspace-drawer.permission.messages.deleteSuccess'));
                setDeletingGrant(null);
        };

        const renderList = (scope: SandboxGrantScope) => {
                const record = scopedRecords[scope];
                const normalizedSearch = search.trim().toLowerCase();
                const filteredGrants = normalizedSearch
                        ? record.grants.filter((grant) =>
                                  grant.pattern.toLowerCase().includes(normalizedSearch),
                          )
                        : record.grants;
                if (loading) {
                        return (
                                <div className="flex flex-col gap-2">
                                        {Array.from({ length: 3 }).map((_, index) => (
                                                <Skeleton key={index} className="h-16 rounded-lg" />
                                        ))}
                                </div>
                        );
                }

                if (record.grants.length === 0) {
                        return (
                                <Empty className="border border-dashed">
                                        <EmptyHeader>
                                                <EmptyMedia variant="icon">{scopeIcon(scope)}</EmptyMedia>
                                                <EmptyTitle>
                                                        {t('workspace-drawer.permission.empty.title')}
                                                </EmptyTitle>
                                                <EmptyDescription>
                                                        {t('workspace-drawer.permission.empty.description')}
                                                </EmptyDescription>
                                        </EmptyHeader>
                                </Empty>
                        );
                }

                if (filteredGrants.length === 0) {
                        return (
                                <Empty className="border border-dashed">
                                        <EmptyHeader>
                                                <EmptyMedia variant="icon">
                                                        <Search className="size-4" />
                                                </EmptyMedia>
                                                <EmptyTitle>
                                                        {t('workspace-drawer.permission.empty.searchTitle')}
                                                </EmptyTitle>
                                                <EmptyDescription>
                                                        {t('workspace-drawer.permission.empty.searchDescription')}
                                                </EmptyDescription>
                                        </EmptyHeader>
                                </Empty>
                        );
                }

                return (
                        <div className="flex flex-col gap-2">
                                <p className="text-xs leading-5 text-muted-foreground">
                                        {t(`workspace-drawer.permission.scopeDescriptions.${scope}`)}
                                </p>
                                {filteredGrants.map((grant) => (
                                        <Item
                                                key={grantKey(grant)}
                                                size="xs"
                                                variant="outline"
                                                className="items-start gap-1.5 rounded-md shadow-none"
                                        >
                                                <ItemHeader className="items-start">
                                                        <div className="min-w-0 flex-1">
                                                                <ItemTitle className="max-w-full break-all font-mono text-[13px] leading-5 whitespace-normal">
                                                                        {grant.pattern}
                                                                </ItemTitle>
                                                        </div>
                                                        <ItemActions className="shrink-0 self-start gap-1">
                                                                <Button
                                                                        size="icon-xs"
                                                                        variant="ghost"
                                                                        tooltip={t('common.edit')}
                                                                        onClick={() => {
                                                                                setEditingGrant(grant);
                                                                                setDialogOpen(true);
                                                                        }}
                                                                >
                                                                        <Pencil />
                                                                </Button>
                                                                <Button
                                                                        size="icon-xs"
                                                                        variant="ghost"
                                                                        tooltip={t('common.delete')}
                                                                        onClick={() => setDeletingGrant(grant)}
                                                                >
                                                                        <Trash2 />
                                                                </Button>
                                                        </ItemActions>
                                                </ItemHeader>
                                                <ItemContent className="basis-full gap-1">
                                                        <div className="flex flex-wrap items-center gap-1.5">
                                                                <Badge variant="outline" className="h-4 px-1.5 text-[11px]">
                                                                        {t(
                                                                                `workspace-drawer.permission.resourceTypes.${grant.resource_type}`,
                                                                        )}
                                                                </Badge>
                                                                {grant.operations.map((operation) => (
                                                                        <Badge
                                                                                key={operation}
                                                                                variant="secondary"
                                                                                className="h-4 px-1.5 text-[11px]"
                                                                        >
                                                                                {t(
                                                                                        `workspace-drawer.permission.operations.${operation}`,
                                                                                )}
                                                                        </Badge>
                                                                ))}
                                                        </div>
                                                </ItemContent>
                                        </Item>
                                ))}
                        </div>
                );
        };

        return (
                <>
                        <div className="flex flex-col gap-3">
                                <div className="flex items-start justify-between gap-2">
                                        <div className="min-w-0">
                                                <p className="text-sm text-muted-foreground">
                                                        {t('workspace-drawer.permission.description')}
                                                </p>
                                        </div>
                                        <Button
                                                size="xs"
                                                variant="outline"
                                                className="shrink-0"
                                                onClick={() => {
                                                        setEditingGrant(null);
                                                        setDialogOpen(true);
                                                }}
                                        >
                                                <Plus className="size-3.5" />
                                                {t('workspace-drawer.permission.add')}
                                        </Button>
                                </div>

                                <Tabs value={activeScope} onValueChange={(value) => setActiveScope(value as SandboxGrantScope)}>
                                        <div className="flex items-center gap-2">
                                                <InputGroup className="min-w-0 flex-1">
                                                        <InputGroupInput
                                                                placeholder={t(
                                                                        'workspace-drawer.permission.searchPlaceholder',
                                                                )}
                                                                value={search}
                                                                onChange={(event) => setSearch(event.target.value)}
                                                        />
                                                        <InputGroupAddon align="inline-end">
                                                                <Search />
                                                        </InputGroupAddon>
                                                </InputGroup>
                                        </div>
                                        <TabsList
                                                variant="line"
                                                className="w-full justify-start gap-0.5 overflow-x-auto no-scrollbar"
                                        >
                                                {SCOPE_ORDER.map((scope) => (
                                                        <TabsTrigger
                                                                key={scope}
                                                                value={scope}
                                                                className="h-7 flex-none gap-1 px-2 text-xs"
                                                        >
                                                                {scopeIcon(scope)}
                                                                {t(`workspace-drawer.permission.scopes.${scope}`)}
                                                        </TabsTrigger>
                                                ))}
                                        </TabsList>
                                        {SCOPE_ORDER.map((scope) => (
                                                <TabsContent key={scope} value={scope} className="mt-1.5">
                                                        {renderList(scope)}
                                                </TabsContent>
                                        ))}
                                </Tabs>
                        </div>

                        <SandboxPermissionDialog
                                open={dialogOpen}
                                onOpenChange={(open) => {
                                        setDialogOpen(open);
                                        if (!open) {
                                                setEditingGrant(null);
                                        }
                                }}
                                scope={activeScope}
                                initialGrant={editingGrant}
                                onSubmit={editingGrant ? handleUpdate : handleCreate}
                        />

                        <DeleteDialog
                                open={deletingGrant !== null}
                                onOpenChange={(open) => {
                                        if (!open) {
                                                setDeletingGrant(null);
                                        }
                                }}
                                title={t('workspace-drawer.permission.deleteTitle')}
                                description={
                                        deletingGrant ? (
                                                <span className="font-mono">{deletingGrant.pattern}</span>
                                        ) : undefined
                                }
                                confirmLabel={t('common.delete')}
                                onConfirm={handleDelete}
                        />
                </>
        );
}
