import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import type {
        ChatModelConfig,
        GlobalModelCategoryKey,
        GlobalModelFallbackCategoryKey,
        ModelCard,
        UserModelDefaults,
} from '@/api';
import { CreateCredentialDialog } from '@/components/dialog/CreateCredentialDialog';
import { ModelParametersPopover } from '@/components/popover/ModelParametersPopover';
import { LlmSelect } from '@/components/select/LlmSelect';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { useAvailableModels } from '@/hooks/useAvailableModels';
import { useUserModelDefaults } from '@/hooks/useUserModelDefaults';
import { useTranslation } from '@/i18n/useI18n';

const CATEGORY_META: Array<{
        key: GlobalModelCategoryKey;
        fallbackKey: GlobalModelFallbackCategoryKey;
        i18nKey: string;
}> = [
        {
                key: 'visual_engineering',
                fallbackKey: 'visual_engineering_fallback_chat_model_config',
                i18nKey: 'visualEngineering',
        },
        {
                key: 'ultrabrain',
                fallbackKey: 'ultrabrain_fallback_chat_model_config',
                i18nKey: 'ultrabrain',
        },
        { key: 'deep', fallbackKey: 'deep_fallback_chat_model_config', i18nKey: 'deep' },
        { key: 'artistry', fallbackKey: 'artistry_fallback_chat_model_config', i18nKey: 'artistry' },
        { key: 'quick', fallbackKey: 'quick_fallback_chat_model_config', i18nKey: 'quick' },
        {
                key: 'unspecified_low',
                fallbackKey: 'unspecified_low_fallback_chat_model_config',
                i18nKey: 'unspecifiedLow',
        },
        {
                key: 'unspecified_high',
                fallbackKey: 'unspecified_high_fallback_chat_model_config',
                i18nKey: 'unspecifiedHigh',
        },
        { key: 'writing', fallbackKey: 'writing_fallback_chat_model_config', i18nKey: 'writing' },
];

function cloneDefaults(value: UserModelDefaults): UserModelDefaults {
        return {
                visual_engineering: value.visual_engineering
                        ? { ...value.visual_engineering, parameters: { ...value.visual_engineering.parameters } }
                        : null,
                visual_engineering_fallback_chat_model_config:
                        value.visual_engineering_fallback_chat_model_config
                                ? {
                                          ...value.visual_engineering_fallback_chat_model_config,
                                          parameters: {
                                                  ...value.visual_engineering_fallback_chat_model_config.parameters,
                                          },
                                  }
                                : null,
                ultrabrain: value.ultrabrain
                        ? { ...value.ultrabrain, parameters: { ...value.ultrabrain.parameters } }
                        : null,
                ultrabrain_fallback_chat_model_config: value.ultrabrain_fallback_chat_model_config
                        ? {
                                  ...value.ultrabrain_fallback_chat_model_config,
                                  parameters: { ...value.ultrabrain_fallback_chat_model_config.parameters },
                          }
                        : null,
                deep: value.deep ? { ...value.deep, parameters: { ...value.deep.parameters } } : null,
                deep_fallback_chat_model_config: value.deep_fallback_chat_model_config
                        ? {
                                  ...value.deep_fallback_chat_model_config,
                                  parameters: { ...value.deep_fallback_chat_model_config.parameters },
                          }
                        : null,
                artistry: value.artistry ? { ...value.artistry, parameters: { ...value.artistry.parameters } } : null,
                artistry_fallback_chat_model_config: value.artistry_fallback_chat_model_config
                        ? {
                                  ...value.artistry_fallback_chat_model_config,
                                  parameters: { ...value.artistry_fallback_chat_model_config.parameters },
                          }
                        : null,
                quick: value.quick ? { ...value.quick, parameters: { ...value.quick.parameters } } : null,
                quick_fallback_chat_model_config: value.quick_fallback_chat_model_config
                        ? {
                                  ...value.quick_fallback_chat_model_config,
                                  parameters: { ...value.quick_fallback_chat_model_config.parameters },
                          }
                        : null,
                unspecified_low: value.unspecified_low
                        ? { ...value.unspecified_low, parameters: { ...value.unspecified_low.parameters } }
                        : null,
                unspecified_low_fallback_chat_model_config: value.unspecified_low_fallback_chat_model_config
                        ? {
                                  ...value.unspecified_low_fallback_chat_model_config,
                                  parameters: {
                                          ...value.unspecified_low_fallback_chat_model_config.parameters,
                                  },
                          }
                        : null,
                unspecified_high: value.unspecified_high
                        ? { ...value.unspecified_high, parameters: { ...value.unspecified_high.parameters } }
                        : null,
                unspecified_high_fallback_chat_model_config:
                        value.unspecified_high_fallback_chat_model_config
                                ? {
                                          ...value.unspecified_high_fallback_chat_model_config,
                                          parameters: {
                                                  ...value.unspecified_high_fallback_chat_model_config.parameters,
                                          },
                                  }
                                : null,
                writing: value.writing ? { ...value.writing, parameters: { ...value.writing.parameters } } : null,
                writing_fallback_chat_model_config: value.writing_fallback_chat_model_config
                        ? {
                                  ...value.writing_fallback_chat_model_config,
                                  parameters: { ...value.writing_fallback_chat_model_config.parameters },
                          }
                        : null,
        };
}

function resolveModelCard(
        groups: Record<string, Array<{ credential: { id: string }; models: ModelCard[] }>>,
        selectedModel: ChatModelConfig | null,
): ModelCard | null {
        if (!selectedModel) return null;
        const items = groups[selectedModel.type];
        if (!items) return null;
        for (const { credential, models } of items) {
                if (credential.id !== selectedModel.credential_id) continue;
                const match = models.find((model) => model.name === selectedModel.model);
                if (match) return match;
        }
        return null;
}

export function ModelDefaultsPage() {
        const { t } = useTranslation();
        const { data, loading, saving, error, save } = useUserModelDefaults();
        const { groups } = useAvailableModels();
        const [draft, setDraft] = useState<UserModelDefaults | null>(null);
        const [credentialOpen, setCredentialOpen] = useState(false);
        const [credentialRefetchTrigger, setCredentialRefetchTrigger] = useState(0);

        useEffect(() => {
                if (!data) return;
                setDraft(cloneDefaults(data));
        }, [data]);

        const isDirty = useMemo(() => {
                if (!data || !draft) return false;
                return JSON.stringify(draft) !== JSON.stringify(data);
        }, [data, draft]);

        const modelCards = useMemo(() => {
                if (!draft) return {} as Partial<Record<GlobalModelCategoryKey, ModelCard | null>>;
                const entries = CATEGORY_META.map(({ key }) => [key, resolveModelCard(groups, draft[key])]);
                return Object.fromEntries(entries) as Record<GlobalModelCategoryKey, ModelCard | null>;
        }, [draft, groups]);

        const handleModelChange = (
                key: GlobalModelCategoryKey,
                fallbackKey: GlobalModelFallbackCategoryKey,
                value: ChatModelConfig | null,
        ) => {
                setDraft((prev) =>
                        prev
                                ? {
                                          ...prev,
                                          [key]: value,
                                          [fallbackKey]: value ? prev[fallbackKey] : null,
                                  }
                                : prev,
                );
        };

        const handleParametersChange = (key: GlobalModelCategoryKey, parameters: Record<string, unknown>) => {
                setDraft((prev) => {
                        if (!prev) return prev;
                        const current = prev[key];
                        if (!current) return prev;
                        return {
                                ...prev,
                                [key]: {
                                        ...current,
                                        parameters,
                                },
                        };
                });
        };

        const handleContextSizeChange = (key: GlobalModelCategoryKey, contextSize: number | null) => {
                setDraft((prev) => {
                        if (!prev) return prev;
                        const current = prev[key];
                        if (!current) return prev;
                        return {
                                ...prev,
                                [key]: {
                                        ...current,
                                        context_size: contextSize,
                                },
                        };
                });
        };

        const handleFallbackModelChange = (
                key: GlobalModelFallbackCategoryKey,
                value: ChatModelConfig | null,
        ) => {
                setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
        };

        const handleSave = async () => {
                if (!draft) return;
                const saved = await save(draft);
                if (!saved) return;
                toast.success(t('settings.modelDefaults.messages.saved'));
        };

        return (
                <>
                        <div className="w-full h-full flex flex-col bg-sidebar overflow-hidden">
                                <div className="flex items-center justify-between p-4 flex-shrink-0">
                                        <div className="space-y-1">
                                                <h1 className="text-2xl font-semibold">{t('settings.modelDefaults.title')}</h1>
                                                <p className="text-sm text-muted-foreground">
                                                        {t('settings.modelDefaults.description')}
                                                </p>
                                        </div>
                                        <Button onClick={handleSave} disabled={!draft || !isDirty || saving}>
                                                {saving ? t('common.saving') : t('common.save')}
                                        </Button>
                                </div>

                                <div className="flex-1 overflow-auto rounded-t-3xl bg-white p-4">
                                        <div className="mx-auto flex max-w-5xl flex-col gap-4">
                                                {loading && !draft ? (
                                                        <Card>
                                                                <CardContent className="py-8 text-sm text-muted-foreground">
                                                                        {t('common.loading')}
                                                                </CardContent>
                                                        </Card>
                                                ) : null}

                                                {!loading && error && !draft ? (
                                                        <Card>
                                                                <CardContent className="py-8 text-sm text-destructive">
                                                                        {error.message || t('common.error')}
                                                                </CardContent>
                                                        </Card>
                                                ) : null}

                                                {draft
                                                        ? CATEGORY_META.map(({ key, fallbackKey, i18nKey }) => (
                                                                          <Card key={key}>
                                                                                  <CardHeader>
                                                                                          <CardTitle>
                                                                                                  {t(`settings.modelDefaults.categories.${i18nKey}.title`)}
                                                                                          </CardTitle>
                                                                                          <CardDescription>
                                                                                                  {t(
                                                                                                          `settings.modelDefaults.categories.${i18nKey}.description`,
                                                                                                  )}
                                                                                          </CardDescription>
                                                                                  </CardHeader>
                                                                                  <CardContent>
                                                                                          <div className="flex flex-wrap items-center gap-2">
                                                                                                  <LlmSelect
                                                                                                          value={draft[key]}
                                                                                                          onChange={(value) =>
                                                                                                                  handleModelChange(
                                                                                                                          key,
                                                                                                                          fallbackKey,
                                                                                                                          value,
                                                                                                                  )
                                                                                                          }
                                                                                                          onAddCredential={() =>
                                                                                                                  setCredentialOpen(true)
                                                                                                          }
                                                                                                          refetchTrigger={credentialRefetchTrigger}
                                                                                                          allowClear
                                                                                                          placeholder={t(
                                                                                                                  'settings.modelDefaults.selectPlaceholder',
                                                                                                          )}
                                                                                                          clearLabel={t(
                                                                                                                  'settings.modelDefaults.clear',
                                                                                                          )}
                                                                                                  />
                                                                                                  <ModelParametersPopover
                                                                                                          selectedModel={draft[key]}
                                                                                                          modelCard={modelCards[key] ?? null}
                                                                                                          onChange={(parameters) =>
                                                                                                                  handleParametersChange(
                                                                                                                          key,
                                                                                                                          parameters,
                                                                                                                  )
                                                                                                          }
                                                                                                          onContextSizeChange={(contextSize) =>
                                                                                                                  handleContextSizeChange(
                                                                                                                          key,
                                                                                                                          contextSize,
                                                                                                                  )
                                                                                                          }
                                                                                                          selectedFallbackModel={
                                                                                                                  draft[fallbackKey]
                                                                                                          }
                                                                                                          onFallbackChange={(value) =>
                                                                                                                  handleFallbackModelChange(
                                                                                                                          fallbackKey,
                                                                                                                          value,
                                                                                                                  )
                                                                                                          }
                                                                                                  />
                                                                                          </div>
                                                                                  </CardContent>
                                                                                  <CardFooter>
                                                                                          <p className="text-xs text-muted-foreground">
                                                                                                  {t(
                                                                                                          draft[key]
                                                                                                                  ? 'settings.modelDefaults.messages.configured'
                                                                                                                  : 'settings.modelDefaults.messages.empty',
                                                                                                  )}
                                                                                          </p>
                                                                                  </CardFooter>
                                                                          </Card>
                                                                  ))
                                                        : null}
                                        </div>
                                </div>
                        </div>
                        <CreateCredentialDialog
                                open={credentialOpen}
                                onOpenChange={setCredentialOpen}
                                onCreated={() => setCredentialRefetchTrigger((prev) => prev + 1)}
                        />
                </>
        );
}
