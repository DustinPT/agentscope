import * as React from 'react';

import type {
        SandboxGrantResourceType,
        SandboxGrantScope,
        SandboxPermissionGrant,
        SandboxPermissionGrantInput,
} from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
        Dialog,
        DialogContent,
        DialogDescription,
        DialogFooter,
        DialogHeader,
        DialogTitle,
} from '@/components/ui/dialog';
import {
        Field,
        FieldContent,
        FieldDescription,
        FieldGroup,
        FieldLabel,
} from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import {
        Select,
        SelectContent,
        SelectItem,
        SelectTrigger,
        SelectValue,
} from '@/components/ui/select';
import { useTranslation } from '@/i18n/useI18n';

type PathMode = 'read_only' | 'read_write';

interface Props {
        open: boolean;
        onOpenChange: (open: boolean) => void;
        scope: SandboxGrantScope;
        initialGrant?: SandboxPermissionGrant | null;
        onSubmit: (value: SandboxPermissionGrantInput) => Promise<void>;
}

function operationsFrom(resourceType: SandboxGrantResourceType, pathMode: PathMode) {
        if (resourceType === 'domain') {
                return ['connect'] as const;
        }
        return pathMode === 'read_write' ? (['read', 'write'] as const) : (['read'] as const);
}

export function SandboxPermissionDialog({
        open,
        onOpenChange,
        scope,
        initialGrant = null,
        onSubmit,
}: Props) {
        const { t } = useTranslation();
        const isEdit = initialGrant !== null;
        const [resourceType, setResourceType] = React.useState<SandboxGrantResourceType>('path');
        const [pattern, setPattern] = React.useState('');
        const [pathMode, setPathMode] = React.useState<PathMode>('read_only');
        const [saving, setSaving] = React.useState(false);

        React.useEffect(() => {
                if (!open) return;
                if (initialGrant) {
                        setResourceType(initialGrant.resource_type);
                        setPattern(initialGrant.pattern);
                        setPathMode(initialGrant.operations.includes('write') ? 'read_write' : 'read_only');
                        return;
                }
                setResourceType('path');
                setPattern('');
                setPathMode('read_only');
        }, [initialGrant, open]);

        const handleSubmit = async () => {
                const trimmedPattern = pattern.trim();
                if (!trimmedPattern) {
                        return;
                }
                setSaving(true);
                try {
                        await onSubmit({
                                resource_type: resourceType,
                                pattern: trimmedPattern,
                                operations: [...operationsFrom(resourceType, pathMode)],
                        });
                        onOpenChange(false);
                } finally {
                        setSaving(false);
                }
        };

        return (
                <Dialog open={open} onOpenChange={onOpenChange}>
                        <DialogContent className="!w-[520px] !max-w-[520px]">
                                <DialogHeader>
                                        <DialogTitle>
                                                {isEdit
                                                        ? t('workspace-drawer.permission.dialog.editTitle')
                                                        : t('workspace-drawer.permission.dialog.createTitle')}
                                        </DialogTitle>
                                        <DialogDescription>
                                                {isEdit
                                                        ? t('workspace-drawer.permission.dialog.editDescription')
                                                        : t('workspace-drawer.permission.dialog.createDescription')}
                                        </DialogDescription>
                                </DialogHeader>

                                <FieldGroup>
                                        <Field orientation="horizontal">
                                                <FieldContent>
                                                        <FieldLabel>
                                                                {t('workspace-drawer.permission.fields.scope')}
                                                        </FieldLabel>
                                                        <FieldDescription>
                                                                {t('workspace-drawer.permission.dialog.scopeHint')}
                                                        </FieldDescription>
                                                </FieldContent>
                                                <Badge variant="outline">
                                                        {t(`workspace-drawer.permission.scopes.${scope}`)}
                                                </Badge>
                                        </Field>

                                        <Field orientation="horizontal">
                                                <FieldContent>
                                                        <FieldLabel>
                                                                {t('workspace-drawer.permission.fields.resourceType')}
                                                        </FieldLabel>
                                                        <FieldDescription>
                                                                {t('workspace-drawer.permission.fields.resourceTypeHint')}
                                                        </FieldDescription>
                                                </FieldContent>
                                                <Select
                                                        value={resourceType}
                                                        onValueChange={(value) =>
                                                                setResourceType(value as SandboxGrantResourceType)
                                                        }
                                                >
                                                        <SelectTrigger className="w-40">
                                                                <SelectValue />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                                <SelectItem value="path">
                                                                        {t('workspace-drawer.permission.resourceTypes.path')}
                                                                </SelectItem>
                                                                <SelectItem value="domain">
                                                                        {t('workspace-drawer.permission.resourceTypes.domain')}
                                                                </SelectItem>
                                                        </SelectContent>
                                                </Select>
                                        </Field>

                                        <Field>
                                                <FieldLabel>
                                                        {t('workspace-drawer.permission.fields.pattern')}
                                                </FieldLabel>
                                                <Input
                                                        value={pattern}
                                                        onChange={(event) => setPattern(event.target.value)}
                                                        placeholder={t(
                                                                resourceType === 'domain'
                                                                        ? 'workspace-drawer.permission.fields.domainPlaceholder'
                                                                        : 'workspace-drawer.permission.fields.pathPlaceholder',
                                                        )}
                                                />
                                                <FieldDescription>
                                                        {t(
                                                                resourceType === 'domain'
                                                                        ? 'workspace-drawer.permission.fields.domainHint'
                                                                        : 'workspace-drawer.permission.fields.pathHint',
                                                        )}
                                                </FieldDescription>
                                        </Field>

                                        <Field orientation="horizontal">
                                                <FieldContent>
                                                        <FieldLabel>
                                                                {t('workspace-drawer.permission.fields.operations')}
                                                        </FieldLabel>
                                                        <FieldDescription>
                                                                {t('workspace-drawer.permission.fields.operationsHint')}
                                                        </FieldDescription>
                                                </FieldContent>
                                                {resourceType === 'domain' ? (
                                                        <Badge variant="secondary">
                                                                {t('workspace-drawer.permission.operations.connect')}
                                                        </Badge>
                                                ) : (
                                                        <Select
                                                                value={pathMode}
                                                                onValueChange={(value) =>
                                                                        setPathMode(value as PathMode)
                                                                }
                                                        >
                                                                <SelectTrigger className="w-40">
                                                                        <SelectValue />
                                                                </SelectTrigger>
                                                                <SelectContent>
                                                                        <SelectItem value="read_only">
                                                                                {t(
                                                                                        'workspace-drawer.permission.operationModes.read_only',
                                                                                )}
                                                                        </SelectItem>
                                                                        <SelectItem value="read_write">
                                                                                {t(
                                                                                        'workspace-drawer.permission.operationModes.read_write',
                                                                                )}
                                                                        </SelectItem>
                                                                </SelectContent>
                                                        </Select>
                                                )}
                                        </Field>
                                </FieldGroup>

                                <DialogFooter>
                                        <Button
                                                variant="ghost"
                                                onClick={() => onOpenChange(false)}
                                                disabled={saving}
                                        >
                                                {t('common.cancel')}
                                        </Button>
                                        <Button
                                                onClick={handleSubmit}
                                                disabled={saving || !pattern.trim()}
                                        >
                                                {saving ? t('common.saving') : t('common.save')}
                                        </Button>
                                </DialogFooter>
                        </DialogContent>
                </Dialog>
        );
}
