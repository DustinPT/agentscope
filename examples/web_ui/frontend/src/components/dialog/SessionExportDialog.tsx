import { CheckCircle, CircleAlert, Loader2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import type { SessionExportOptions } from '@/api';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
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
        FieldError,
        FieldGroup,
        FieldLabel,
} from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { useTranslation } from '@/i18n/useI18n';

const DEFAULT_EXPORT_OPTIONS: SessionExportOptions = {
        include_system_messages: false,
        include_tool_schemas: false,
        truncate_tool_call_input: true,
        tool_call_input_max_length: 200,
        truncate_tool_result: true,
        tool_result_max_length: 200,
};

interface SessionExportDialogProps {
        open: boolean;
        onOpenChange: (open: boolean) => void;
        onConfirm: (options: SessionExportOptions) => Promise<void>;
}

export function SessionExportDialog({
        open,
        onOpenChange,
        onConfirm,
}: SessionExportDialogProps) {
        const { t } = useTranslation();
        const [options, setOptions] = useState<SessionExportOptions>(DEFAULT_EXPORT_OPTIONS);
        const [loading, setLoading] = useState(false);

        useEffect(() => {
                if (open) {
                        setOptions(DEFAULT_EXPORT_OPTIONS);
                }
        }, [open]);

        const validation = useMemo(() => {
                const errors = {
                        toolCallInput: '',
                        toolResult: '',
                };

                if (
                        options.truncate_tool_call_input &&
                        (!Number.isInteger(options.tool_call_input_max_length) || options.tool_call_input_max_length <= 0)
                ) {
                        errors.toolCallInput = t('dialog-session-export.validation.positiveInteger');
                }

                if (
                        options.truncate_tool_result &&
                        (!Number.isInteger(options.tool_result_max_length) || options.tool_result_max_length <= 0)
                ) {
                        errors.toolResult = t('dialog-session-export.validation.positiveInteger');
                }

                return errors;
        }, [options, t]);

        const isValid = !validation.toolCallInput && !validation.toolResult;

        const handleConfirm = async () => {
                if (!isValid) return;
                setLoading(true);
                try {
                        await onConfirm(options);
                        onOpenChange(false);
                } catch {
                        // API errors are already surfaced by the shared client toast.
                } finally {
                        setLoading(false);
                }
        };

        return (
                <Dialog open={open} onOpenChange={onOpenChange}>
                        <DialogContent className="!w-[520px] !max-w-[520px]">
                                <DialogHeader>
                                        <DialogTitle>{t('dialog-session-export.title')}</DialogTitle>
                                        <DialogDescription>{t('dialog-session-export.description')}</DialogDescription>
                                </DialogHeader>
                                <FieldGroup>
                                        <Field orientation="horizontal">
                                                <Checkbox
                                                        id="session-export-include-system"
                                                        checked={options.include_system_messages}
                                                        onCheckedChange={(checked) =>
                                                                setOptions((current) => ({
                                                                        ...current,
                                                                        include_system_messages: checked === true,
                                                                }))
                                                        }
                                                />
                                                <FieldContent>
                                                        <FieldLabel htmlFor="session-export-include-system">
                                                                {t('dialog-session-export.includeSystemMessages')}
                                                        </FieldLabel>
                                                </FieldContent>
                                        </Field>

                                        <Field orientation="horizontal">
                                                <Checkbox
                                                        id="session-export-include-tool-schemas"
                                                        checked={options.include_tool_schemas}
                                                        onCheckedChange={(checked) =>
                                                                setOptions((current) => ({
                                                                        ...current,
                                                                        include_tool_schemas: checked === true,
                                                                }))
                                                        }
                                                />
                                                <FieldContent>
                                                        <FieldLabel htmlFor="session-export-include-tool-schemas">
                                                                {t('dialog-session-export.includeToolSchemas')}
                                                        </FieldLabel>
                                                </FieldContent>
                                        </Field>

                                        <Field orientation="horizontal">
                                                <Checkbox
                                                        id="session-export-truncate-tool-call"
                                                        checked={options.truncate_tool_call_input}
                                                        onCheckedChange={(checked) =>
                                                                setOptions((current) => ({
                                                                        ...current,
                                                                        truncate_tool_call_input: checked === true,
                                                                }))
                                                        }
                                                />
                                                <FieldContent>
                                                        <FieldLabel htmlFor="session-export-truncate-tool-call">
                                                                {t('dialog-session-export.truncateToolCallInput')}
                                                        </FieldLabel>
                                                </FieldContent>
                                        </Field>
                                        <Field>
                                                <FieldLabel htmlFor="session-export-tool-call-length">
                                                        {t('dialog-session-export.toolCallInputMaxLength')}
                                                </FieldLabel>
                                                <Input
                                                        id="session-export-tool-call-length"
                                                        type="number"
                                                        min={1}
                                                        step={1}
                                                        disabled={!options.truncate_tool_call_input || loading}
                                                        value={options.tool_call_input_max_length}
                                                        onChange={(event) =>
                                                                setOptions((current) => ({
                                                                        ...current,
                                                                        tool_call_input_max_length: Number(event.target.value),
                                                                }))
                                                        }
                                                />
                                                <FieldDescription>
                                                        {t('dialog-session-export.lengthDescription')}
                                                </FieldDescription>
                                                <FieldError>{validation.toolCallInput}</FieldError>
                                        </Field>

                                        <Field orientation="horizontal">
                                                <Checkbox
                                                        id="session-export-truncate-tool-result"
                                                        checked={options.truncate_tool_result}
                                                        onCheckedChange={(checked) =>
                                                                setOptions((current) => ({
                                                                        ...current,
                                                                        truncate_tool_result: checked === true,
                                                                }))
                                                        }
                                                />
                                                <FieldContent>
                                                        <FieldLabel htmlFor="session-export-truncate-tool-result">
                                                                {t('dialog-session-export.truncateToolResult')}
                                                        </FieldLabel>
                                                </FieldContent>
                                        </Field>
                                        <Field>
                                                <FieldLabel htmlFor="session-export-tool-result-length">
                                                        {t('dialog-session-export.toolResultMaxLength')}
                                                </FieldLabel>
                                                <Input
                                                        id="session-export-tool-result-length"
                                                        type="number"
                                                        min={1}
                                                        step={1}
                                                        disabled={!options.truncate_tool_result || loading}
                                                        value={options.tool_result_max_length}
                                                        onChange={(event) =>
                                                                setOptions((current) => ({
                                                                        ...current,
                                                                        tool_result_max_length: Number(event.target.value),
                                                                }))
                                                        }
                                                />
                                                <FieldDescription>
                                                        {t('dialog-session-export.lengthDescription')}
                                                </FieldDescription>
                                                <FieldError>{validation.toolResult}</FieldError>
                                        </Field>
                                </FieldGroup>
                                <DialogFooter>
                                        <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={loading}>
                                                <CircleAlert className="size-3.5" />
                                                {t('common.cancel')}
                                        </Button>
                                        <Button onClick={handleConfirm} disabled={loading || !isValid}>
                                                {loading ? (
                                                        <Loader2 className="size-3.5 animate-spin" />
                                                ) : (
                                                        <CheckCircle className="size-3.5" />
                                                )}
                                                {loading ? t('dialog-session-export.exporting') : t('dialog-session-export.confirm')}
                                        </Button>
                                </DialogFooter>
                        </DialogContent>
                </Dialog>
        );
}
