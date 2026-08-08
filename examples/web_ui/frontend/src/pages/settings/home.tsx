import { ArrowRight, PlugZap, SlidersHorizontal } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useTranslation } from '@/i18n/useI18n';

const SETTINGS_SECTIONS = [
        {
                key: 'connection',
                icon: PlugZap,
                path: '/settings/connection',
        },
        {
                key: 'modelDefaults',
                icon: SlidersHorizontal,
                path: '/settings/model-defaults',
        },
];

export function SettingsHomePage() {
        const navigate = useNavigate();
        const { t } = useTranslation();

        return (
                <div className="w-full h-full flex flex-col bg-sidebar overflow-hidden">
                        <div className="flex items-center justify-between p-4 flex-shrink-0">
                                <div className="space-y-1">
                                        <h1 className="text-2xl font-semibold">{t('settings.home.title')}</h1>
                                        <p className="text-sm text-muted-foreground">{t('settings.home.description')}</p>
                                </div>
                        </div>

                        <div className="flex-1 overflow-auto rounded-t-3xl bg-white p-4">
                                <div className="mx-auto grid max-w-5xl gap-4 md:grid-cols-2">
                                        {SETTINGS_SECTIONS.map(({ key, icon: Icon, path }) => (
                                                <Card key={key}>
                                                        <CardHeader>
                                                                <div className="mb-2 flex size-10 items-center justify-center rounded-lg bg-muted">
                                                                        <Icon className="size-5" />
                                                                </div>
                                                                <CardTitle>{t(`settings.home.sections.${key}.title`)}</CardTitle>
                                                                <CardDescription>
                                                                        {t(`settings.home.sections.${key}.description`)}
                                                                </CardDescription>
                                                        </CardHeader>
                                                        <CardContent>
                                                                <Button variant="outline" onClick={() => navigate(path)}>
                                                                        {t('settings.home.open')}
                                                                        <ArrowRight />
                                                                </Button>
                                                        </CardContent>
                                                </Card>
                                        ))}
                                </div>
                        </div>
                </div>
        );
}
