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
        ItemDescription,
        ItemFooter,
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
                                <div className="flex flex-col gap-3">
                                        {Array.from({ length: 3 }).map((_, index) => (
                                                <Skeleton key={index} className="h-24 rounded-xl" />
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
                        <div className="flex flex-col gap-3">
                                {filteredGrants.map((grant) => (
                                        <Item
                                                key={grantKey(grant)}
                                                variant="outline"
                                                className="items-start shadow-panel"
                                        >
                                                <ItemHeader className="items-start">
                                                        <div className="min-w-0 flex-1">
                                                                <ItemTitle className="max-w-full break-all font-mono whitespace-normal">
                                                                        {grant.pattern}
                                                                </ItemTitle>
                                                                <ItemDescription>
                                                                        {t(
                                                                                `workspace-drawer.permission.resourceTypes.${grant.resource_type}`,
                                                                        )}
                                                                </ItemDescription>
                                                        </div>
                                                        <ItemActions className="shrink-0 self-start">
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
                                                <ItemContent className="basis-full gap-2">
                                                        <div className="flex flex-wrap items-center gap-2">
                                                                <Badge variant="outline">
                                                                        {t(
                                                                                `workspace-drawer.permission.resourceTypes.${grant.resource_type}`,
                                                                        )}
                                                                </Badge>
                                                                {grant.operations.map((operation) => (
                                                                        <Badge
                                                                                key={operation}
                                                                                variant="secondary"
                                                                        >
                                                                                {t(
                                                                                        `workspace-drawer.permission.operations.${operation}`,
                                                                                )}
                                                                        </Badge>
                                                                ))}
                                                        </div>
                                                </ItemContent>
                                                <ItemFooter className="justify-start">
                                                        <span className="text-xs text-muted-foreground">
                                                                {t(
                                                                        `workspace-drawer.permission.scopeDescriptions.${scope}`,
                                                                )}
                                                        </span>
                                                </ItemFooter>
                                        </Item>
                                ))}
                        </div>
                );
        };

        return (
                <>
                        <div className="flex flex-col gap-4">
                                <div className="flex items-start justify-between gap-3">
                                        <div className="space-y-1">
                                                <p className="text-sm text-muted-foreground">
                                                        {t('workspace-drawer.permission.description')}
                                                </p>
                                        </div>
                                        <Button
                                                size="sm"
                                                variant="outline"
                                                onClick={() => {
                                                        setEditingGrant(null);
                                                        setDialogOpen(true);
                                                }}
                                        >
                                                <Plus className="size-3.5" />
                                                {t('workspace-drawer.permission.add')}
                                        </Button>
                                </div>

                                <InputGroup>
                                        <InputGroupInput
                                                placeholder={t('workspace-drawer.permission.searchPlaceholder')}
                                                value={search}
                                                onChange={(event) => setSearch(event.target.value)}
                                        />
                                        <InputGroupAddon align="inline-end">
                                                <Search />
                                        </InputGroupAddon>
                                </InputGroup>

                                <Tabs value={activeScope} onValueChange={(value) => setActiveScope(value as SandboxGrantScope)}>
                                        <TabsList variant="line" className="w-full justify-start">
                                                {SCOPE_ORDER.map((scope) => (
                                                        <TabsTrigger key={scope} value={scope}>
                                                                {scopeIcon(scope)}
                                                                {t(`workspace-drawer.permission.scopes.${scope}`)}
                                                        </TabsTrigger>
                                                ))}
                                        </TabsList>
                                        {SCOPE_ORDER.map((scope) => (
                                                <TabsContent key={scope} value={scope} className="mt-2">
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
