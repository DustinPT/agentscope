import type { ToolCallBlock, ToolResultBlock } from '@agentscope-ai/agentscope/message';
import { CircleHelp, Loader2 } from 'lucide-react';
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
        const [answers, setAnswers] = useState<MutableAnswer[]>([]);
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
        }, [askUserParams, toolCall.id]);

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
