import { useTranslation } from '@/i18n/useI18n';
import { SetupPage } from '@/pages/setup';

export function SettingsConnectionPage() {
        const { t } = useTranslation();

        return (
                <div className="w-full h-full flex flex-col bg-sidebar overflow-hidden">
                        <div className="flex items-center justify-between p-4 flex-shrink-0">
                                <div className="space-y-1">
                                        <h1 className="text-2xl font-semibold">{t('settings.connection.pageTitle')}</h1>
                                        <p className="text-sm text-muted-foreground">
                                                {t('settings.connection.pageDescription')}
                                        </p>
                                </div>
                        </div>

                        <div className="flex-1 overflow-auto rounded-t-3xl bg-white p-4">
                                <div className="mx-auto flex h-full max-w-5xl items-center justify-center">
                                        <SetupPage
                                                onComplete={() => {}}
                                                titleKey="settings.connection.formTitle"
                                                descriptionKey="settings.connection.formDescription"
                                                submitKey="settings.connection.submit"
                                                hintKey="settings.connection.hint"
                                        />
                                </div>
                        </div>
                </div>
        );
}
