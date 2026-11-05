import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import { CircleHelp, Eye, EyeOff, Loader2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import type { ToolResultMetadata } from '@/hooks/useMessages';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface AskUserOption {
        label: string;
        description: string;
        preview?: string | null;
}

interface AskUserQuestion {
        question: string;
        header: string;
        context?: string | null;
        options: AskUserOption[];
        multi_select?: boolean;
}

interface AskUserParams {
        questions: AskUserQuestion[];
}

interface AskUserAnswer {
        question: string;
        selected: string[];
        other: string | null;
}

interface MutableAnswer {
        selected: string[];
        other: string;
}

interface RequestSecretField {
        key: string;
        label: string;
        description?: string | null;
        placeholder?: string | null;
        required?: boolean;
}

interface RequestSecretParams {
        title?: string;
        description?: string | null;
        header?: string | null;
        fields: RequestSecretField[];
        ttl_minutes?: number;
}

interface RequestSecretValue {
        key: string;
        value: string;
}

function parseAskUserParams(input: string): AskUserParams | null {
        try {
                const parsed = JSON.parse(input) as AskUserParams;
                if (!Array.isArray(parsed.questions) || parsed.questions.length === 0) {
                        return null;
                }
                return parsed;
        } catch {
                return null;
        }
}

function buildAskUserOutput(answers: AskUserAnswer[]): string {
        return answers
                .map((answer) => {
                        const value = answer.other?.trim() || answer.selected.join(', ');
                        return `${answer.question}\n${value}`;
                })
                .join('\n\n');
}

function parseRequestSecretParams(input: string): RequestSecretParams | null {
        try {
                const parsed = JSON.parse(input) as RequestSecretParams;
                if (!Array.isArray(parsed.fields) || parsed.fields.length === 0) {
                        return null;
                }
                return parsed;
        } catch {
                return null;
        }
}

function maskSecretValue(value: string): string {
        return value.replace(/[^\n]/g, '•');
}

export function ExternalExecutionCard({
        toolCall,
        title,
        onSubmit,
}: {
        toolCall: ToolCallBlock;
        title?: string;
        onSubmit: (
                output: string,
                state?: ToolResultBlock['state'],
                metadata?: ToolResultMetadata,
        ) => Promise<void>;
}) {
        const { t } = useTranslation();
        const askUserParams = useMemo(() => {
                if (toolCall.name !== 'AskUser') return null;
                return parseAskUserParams(toolCall.input);
        }, [toolCall.input, toolCall.name]);
        const requestSecretParams = useMemo(() => {
                if (toolCall.name !== 'RequestSecret') return null;
                return parseRequestSecretParams(toolCall.input);
        }, [toolCall.input, toolCall.name]);
        const [answers, setAnswers] = useState<MutableAnswer[]>([]);
        const [secretValues, setSecretValues] = useState<RequestSecretValue[]>([]);
        const [visibleSecretKeys, setVisibleSecretKeys] = useState<string[]>([]);
        const [genericOutput, setGenericOutput] = useState('');
        const [submitting, setSubmitting] = useState(false);
        const [error, setError] = useState<string | null>(null);

        useEffect(() => {
                setGenericOutput('');
                setError(null);
                setSubmitting(false);
                setAnswers(
                        askUserParams?.questions.map(() => ({
                                selected: [],
                                other: '',
                        })) ?? [],
                );
                setSecretValues(
                        requestSecretParams?.fields.map((field) => ({
                                key: field.key,
                                value: '',
                        })) ?? [],
                );
                setVisibleSecretKeys([]);
        }, [askUserParams, requestSecretParams, toolCall.id]);

        const handleAskUserSingleSelect = (questionIndex: number, label: string) => {
                setAnswers((prev) =>
                        prev.map((answer, index) =>
                                index === questionIndex
                                        ? {
                                                        ...answer,
                                                        selected: [label],
                                                        other: '',
                                                }
                                        : answer,
                        ),
                );
        };

        const handleAskUserMultiSelect = (
                questionIndex: number,
                label: string,
                checked: boolean,
        ) => {
                setAnswers((prev) =>
                        prev.map((answer, index) => {
                                if (index !== questionIndex) return answer;
                                const selected = checked
                                        ? Array.from(new Set([...answer.selected, label]))
                                        : answer.selected.filter((item) => item !== label);
                                return {
                                        ...answer,
                                        selected,
                                        other: '',
                                };
                        }),
                );
        };

        const handleAskUserOtherChange = (questionIndex: number, value: string) => {
                setAnswers((prev) =>
                        prev.map((answer, index) =>
                                index === questionIndex
                                        ? {
                                                        ...answer,
                                                        selected: value.trim() ? [] : answer.selected,
                                                        other: value,
                                                }
                                        : answer,
                        ),
                );
        };

        const submitAskUser = async () => {
                if (!askUserParams) return;
                const resultAnswers: AskUserAnswer[] = askUserParams.questions.map((question, index) => ({
                        question: question.question,
                        selected: answers[index]?.selected ?? [],
                        other: answers[index]?.other.trim() || null,
                }));
                const metadata: ToolResultMetadata = {
                        answers: resultAnswers.map((answer) => ({
                                question: answer.question,
                                selected: answer.selected,
                                other: answer.other,
                        })),
                };

                setSubmitting(true);
                setError(null);
                try {
                        await onSubmit(buildAskUserOutput(resultAnswers), 'success', metadata);
                } catch {
                        setError(t('chat.externalExecutionSubmitFailed'));
                        setSubmitting(false);
                }
        };

        const handleSecretValueChange = (key: string, value: string) => {
                setSecretValues((prev) =>
                        prev.map((entry) => (entry.key === key ? { ...entry, value } : entry)),
                );
        };

        const toggleSecretVisibility = (key: string) => {
                setVisibleSecretKeys((prev) =>
                        prev.includes(key) ? prev.filter((item) => item !== key) : [...prev, key],
                );
        };

        const submitRequestSecret = async () => {
                if (!requestSecretParams) return;

                const missingField = requestSecretParams.fields.find((field) => {
                        const currentValue =
                                secretValues.find((entry) => entry.key === field.key)?.value ?? '';
                        return (field.required ?? true) && currentValue.length === 0;
                });
                if (missingField) {
                        setError(
                                t('chat.requestSecretFieldRequired', {
                                        field: missingField.label,
                                }),
                        );
                        return;
                }

                const bindings = secretValues
                        .map((entry) => ({
                                env_var_name: entry.key,
                                secret_value: entry.value,
                        }))
                        .filter((entry) => entry.secret_value.length > 0);

                const metadata: ToolResultMetadata = {
                        request_secret_submission: {
                                bindings,
                                ttl_minutes: requestSecretParams.ttl_minutes ?? 30,
                        },
                };

                setSubmitting(true);
                setError(null);
                try {
                        await onSubmit(
                                t('chat.requestSecretSubmitSuccess'),
                                'success',
                                metadata,
                        );
                } catch {
                        setError(t('chat.externalExecutionSubmitFailed'));
                        setSubmitting(false);
                }
        };

        const submitGeneric = async () => {
                setSubmitting(true);
                setError(null);
                try {
                        await onSubmit(genericOutput, 'success');
                } catch {
                        setError(t('chat.externalExecutionSubmitFailed'));
                        setSubmitting(false);
                }
        };

        return (
                <div className="ring ring-border rounded-xl w-full p-4 space-y-4 text-sm overflow-hidden">
                        <div className="flex items-center gap-2 text-secondary-foreground">
                                <CircleHelp className="size-4 shrink-0" />
                                <strong>{title ?? t('chat.externalExecutionTitle')}</strong>
                        </div>

                        {askUserParams ? (
                                <div className="space-y-4">
                                        {askUserParams.questions.map((question, questionIndex) => {
                                                const answer = answers[questionIndex];
                                                const hasCustomAnswer = Boolean(answer?.other.trim());
                                                return (
                                                        <div
                                                                key={`${toolCall.id}-${questionIndex}`}
                                                                className="rounded-lg border bg-muted/20 p-3 space-y-3"
                                                        >
                                                                <div className="space-y-1">
                                                                        <div className="text-xs font-medium text-muted-foreground">
                                                                                {question.header}
                                                                        </div>
                                                                        <div className="font-medium">
                                                                                {question.question}
                                                                        </div>
                                                                        {question.context && (
                                                                                <div className="text-muted-foreground whitespace-pre-wrap">
                                                                                        {question.context}
                                                                                </div>
                                                                        )}
                                                                </div>

                                                                <div className="space-y-2">
                                                                        {question.options.map((option) =>
                                                                                question.multi_select ? (
                                                                                        <Label
                                                                                                key={option.label}
                                                                                                className="items-start gap-3 rounded-lg border px-3 py-2"
                                                                                        >
                                                                                                <Checkbox
                                                                                                        checked={answer?.selected.includes(
                                                                                                                option.label,
                                                                                                        )}
                                                                                                        disabled={
                                                                                                                submitting ||
                                                                                                                hasCustomAnswer
                                                                                                        }
                                                                                                        onCheckedChange={(checked) =>
                                                                                                                handleAskUserMultiSelect(
                                                                                                                        questionIndex,
                                                                                                                        option.label,
                                                                                                                        checked === true,
                                                                                                                )
                                                                                                        }
                                                                                                />
                                                                                                <div className="space-y-1">
                                                                                                        <div className="font-medium">
                                                                                                                {option.label}
                                                                                                        </div>
                                                                                                        <div className="text-muted-foreground whitespace-pre-wrap">
                                                                                                                {option.description}
                                                                                                        </div>
                                                                                                </div>
                                                                                        </Label>
                                                                                ) : (
                                                                                        <button
                                                                                                key={option.label}
                                                                                                type="button"
                                                                                                className={cn(
                                                                                                        'w-full rounded-lg border px-3 py-2 text-left transition-colors',
                                                                                                        answer?.selected[0] === option.label
                                                                                                                ? 'border-primary bg-primary/8'
                                                                                                                : 'hover:bg-muted/40',
                                                                                                        hasCustomAnswer &&
                                                                                                                'cursor-not-allowed opacity-60 hover:bg-transparent',
                                                                                                )}
                                                                                                disabled={
                                                                                                        submitting ||
                                                                                                        hasCustomAnswer
                                                                                                }
                                                                                                onClick={() =>
                                                                                                        handleAskUserSingleSelect(
                                                                                                                questionIndex,
                                                                                                                option.label,
                                                                                                        )
                                                                                                }
                                                                                        >
                                                                                                <div className="font-medium">
                                                                                                        {option.label}
                                                                                                </div>
                                                                                                <div className="text-muted-foreground whitespace-pre-wrap">
                                                                                                        {option.description}
                                                                                                </div>
                                                                                                {option.preview && (
                                                                                                        <pre className="mt-2 whitespace-pre-wrap rounded-md bg-muted/50 px-3 py-2 text-xs text-foreground">
                                                                                                                {option.preview}
                                                                                                        </pre>
                                                                                                )}
                                                                                        </button>
                                                                                ),
                                                                        )}
                                                                </div>

                                                                <div className="space-y-2">
                                                                        <Label htmlFor={`${toolCall.id}-other-${questionIndex}`}>
                                                                                {t('chat.askUserOtherAnswer')}
                                                                        </Label>
                                                                        <Input
                                                                                id={`${toolCall.id}-other-${questionIndex}`}
                                                                                value={answer?.other ?? ''}
                                                                                placeholder={t('chat.askUserOtherPlaceholder')}
                                                                                onChange={(event) =>
                                                                                        handleAskUserOtherChange(
                                                                                                questionIndex,
                                                                                                event.target.value,
                                                                                        )
                                                                                }
                                                                                disabled={submitting}
                                                                        />
                                                                        <div className="text-xs text-muted-foreground">
                                                                                {t('chat.askUserMutuallyExclusiveHint')}
                                                                        </div>
                                                                </div>
                                                        </div>
                                                );
                                        })}
                                        <div className="flex items-center justify-between gap-3">
                                                {error ? (
                                                        <div className="text-sm text-destructive">{error}</div>
                                                ) : (
                                                        <div className="text-sm text-muted-foreground">
                                                                {t('chat.askUserSubmitHint')}
                                                        </div>
                                                )}
                                                <Button
                                                        type="button"
                                                        onClick={() => void submitAskUser()}
                                                        disabled={submitting}
                                                >
                                                        {submitting && <Loader2 className="size-4 animate-spin" />}
                                                        {t('chat.submitExternalExecution')}
                                                </Button>
                                        </div>
                                </div>
                        ) : requestSecretParams ? (
                                <div className="space-y-4">
                                        {(requestSecretParams.title || requestSecretParams.header) && (
                                                <div className="space-y-1">
                                                        {requestSecretParams.header && (
                                                                <div className="text-xs font-medium text-muted-foreground">
                                                                        {requestSecretParams.header}
                                                                </div>
                                                        )}
                                                        {requestSecretParams.title && (
                                                                <div className="font-medium">
                                                                        {requestSecretParams.title}
                                                                </div>
                                                        )}
                                                </div>
                                        )}
                                        {requestSecretParams.description && (
                                                <div className="text-muted-foreground whitespace-pre-wrap">
                                                        {requestSecretParams.description}
                                                </div>
                                        )}
                                        {requestSecretParams.fields.map((field) => {
                                                const value =
                                                        secretValues.find((entry) => entry.key === field.key)?.value ??
                                                        '';
                                                const isVisible = visibleSecretKeys.includes(field.key);
                                                return (
                                                        <div
                                                                key={`${toolCall.id}-${field.key}`}
                                                                className="rounded-lg border bg-muted/20 p-3 space-y-2"
                                                        >
                                                                <Label htmlFor={`${toolCall.id}-secret-${field.key}`}>
                                                                        {field.label}
                                                                </Label>
                                                                {field.description && (
                                                                        <div className="text-sm text-muted-foreground whitespace-pre-wrap">
                                                                                {field.description}
                                                                        </div>
                                                                )}
                                                                <div className="relative">
                                                                        <Textarea
                                                                                id={`${toolCall.id}-secret-${field.key}`}
                                                                                autoComplete="off"
                                                                                value={value}
                                                                                placeholder={field.placeholder ?? ''}
                                                                                onChange={(event) =>
                                                                                        handleSecretValueChange(
                                                                                                field.key,
                                                                                                event.target.value,
                                                                                        )
                                                                                }
                                                                                disabled={submitting}
                                                                                spellCheck={false}
                                                                                rows={3}
                                                                                className={cn(
                                                                                        'min-h-24 pr-10 font-mono',
                                                                                        !isVisible &&
                                                                                                'text-transparent caret-foreground',
                                                                                )}
                                                                        />
                                                                        {!isVisible && value.length > 0 && (
                                                                                <div className="pointer-events-none absolute inset-0 overflow-hidden whitespace-pre-wrap break-words px-2.5 py-2 pr-10 font-mono text-base md:text-sm">
                                                                                        {maskSecretValue(value)}
                                                                                </div>
                                                                        )}
                                                                        <Button
                                                                                type="button"
                                                                                variant="ghost"
                                                                                size="icon-sm"
                                                                                className="absolute top-2 right-1"
                                                                                onClick={() =>
                                                                                        toggleSecretVisibility(field.key)
                                                                                }
                                                                                disabled={submitting}
                                                                        >
                                                                                {isVisible ? (
                                                                                        <EyeOff className="size-4" />
                                                                                ) : (
                                                                                        <Eye className="size-4" />
                                                                                )}
                                                                        </Button>
                                                                </div>
                                                                <div className="text-xs text-muted-foreground">
                                                                        {t('chat.requestSecretBoundAs', {
                                                                                field: field.key,
                                                                        })}{' '}
                                                                        <code>{field.key}</code>{' '}
                                                                        {t('chat.requestSecretBoundAsSuffix')}
                                                                </div>
                                                        </div>
                                                );
                                        })}
                                        <div className="flex items-center justify-between gap-3">
                                                {error ? (
                                                        <div className="text-sm text-destructive">{error}</div>
                                                ) : (
                                                        <div className="text-sm text-muted-foreground">
                                                                {t('chat.requestSecretSubmitHint')}
                                                        </div>
                                                )}
                                                <Button
                                                        type="button"
                                                        onClick={() => void submitRequestSecret()}
                                                        disabled={submitting}
                                                >
                                                        {submitting && <Loader2 className="size-4 animate-spin" />}
                                                        {t('chat.submitExternalExecution')}
                                                </Button>
                                        </div>
                                </div>
                        ) : (
                                <div className="space-y-3">
                                        <div className="text-muted-foreground whitespace-pre-wrap">
                                                {toolCall.input}
                                        </div>
                                        <Textarea
                                                value={genericOutput}
                                                placeholder={t('chat.externalExecutionPlaceholder')}
                                                onChange={(event) => setGenericOutput(event.target.value)}
                                                disabled={submitting}
                                        />
                                        <div className="flex items-center justify-between gap-3">
                                                {error ? (
                                                        <div className="text-sm text-destructive">{error}</div>
                                                ) : (
                                                        <div className="text-sm text-muted-foreground">
                                                                {t('chat.externalExecutionDescription')}
                                                        </div>
                                                )}
                                                <Button
                                                        type="button"
                                                        onClick={() => void submitGeneric()}
                                                        disabled={submitting}
                                                >
                                                        {submitting && <Loader2 className="size-4 animate-spin" />}
                                                        {t('chat.submitExternalExecution')}
                                                </Button>
                                        </div>
                                </div>
                        )}
                </div>
        );
}
