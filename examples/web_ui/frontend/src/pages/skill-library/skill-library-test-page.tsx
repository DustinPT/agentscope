import { ArrowLeft, Search } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { skillLibraryApi, type SkillLibrarySearchHit } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useTranslation } from '@/i18n/useI18n';

/**
 * Standalone page for validating search_skills retrieval behavior without
 * mixing test tooling into the main skill library management view.
 */
export function SkillLibraryTestPage() {
        const navigate = useNavigate();
        const { t } = useTranslation();

        const [toolQuery, setToolQuery] = useState('');
        const [toolLimit, setToolLimit] = useState('5');
        const [toolMinScore, setToolMinScore] = useState('');
        const [toolSearchLoading, setToolSearchLoading] = useState(false);
        const [hasToolSearchRun, setHasToolSearchRun] = useState(false);
        const [toolSearchSkills, setToolSearchSkills] = useState<SkillLibrarySearchHit[]>([]);

        const runToolSearch = async () => {
                const normalizedQuery = toolQuery.trim();
                if (!normalizedQuery) return;

                const normalizedMinScore = toolMinScore.trim();
                const parsedMinScore = normalizedMinScore ? Number(normalizedMinScore) : undefined;

                setToolSearchLoading(true);
                setHasToolSearchRun(false);
                setToolSearchSkills([]);
                try {
                        const response = await skillLibraryApi.search({
                                query: normalizedQuery,
                                limit: Number(toolLimit) || 5,
                                ...(parsedMinScore !== undefined && Number.isFinite(parsedMinScore)
                                        ? {
                                                  min_score: parsedMinScore,
                                          }
                                        : {}),
                        });
                        setToolSearchSkills(response.skills);
                        setHasToolSearchRun(true);
                } finally {
                        setToolSearchLoading(false);
                }
        };

        return (
                <div className="w-full h-full flex flex-col bg-sidebar overflow-hidden">
                        <div className="flex items-center justify-between gap-3 p-4 flex-shrink-0">
                                <div className="space-y-1">
                                        <Button variant="ghost" className="w-fit px-2" onClick={() => navigate('/skill-library')}>
                                                <ArrowLeft className="size-4" />
                                                {t('settings.skillLibrary.backToLibrary')}
                                        </Button>
                                        <h1 className="text-2xl font-semibold">{t('settings.skillLibrary.testPageTitle')}</h1>
                                        <p className="text-sm text-muted-foreground">
                                                {t('settings.skillLibrary.testPageDescription')}
                                        </p>
                                </div>
                        </div>

                        <div className="flex-1 overflow-auto rounded-t-3xl bg-white p-4">
                                <div className="mx-auto flex max-w-5xl flex-col gap-4">
                                        <Card>
                                                <CardHeader>
                                                        <CardTitle>{t('settings.skillLibrary.testSearchTitle')}</CardTitle>
                                                        <CardDescription>
                                                                {t('settings.skillLibrary.testSearchDescription')}
                                                        </CardDescription>
                                                </CardHeader>
                                                <CardContent className="space-y-4">
                                                        <div className="flex flex-col gap-3 md:flex-row md:items-end">
                                                                <div className="w-full md:flex-1">
                                                                        <Input
                                                                                placeholder={t('settings.skillLibrary.testSearchQueryPlaceholder')}
                                                                                value={toolQuery}
                                                                                onChange={(event) => setToolQuery(event.target.value)}
                                                                                onKeyDown={(event) => {
                                                                                        if (event.key === 'Enter') {
                                                                                                void runToolSearch();
                                                                                        }
                                                                                }}
                                                                        />
                                                                </div>
                                                                <div className="w-full md:w-32">
                                                                        <div className="mb-2 text-sm text-muted-foreground">
                                                                                {t('settings.skillLibrary.testSearchLimitLabel')}
                                                                        </div>
                                                                        <Input
                                                                                type="number"
                                                                                min={1}
                                                                                max={20}
                                                                                step="1"
                                                                                value={toolLimit}
                                                                                onChange={(event) => setToolLimit(event.target.value)}
                                                                        />
                                                                </div>
                                                                <div className="w-full md:w-36">
                                                                        <div className="mb-2 text-sm text-muted-foreground">
                                                                                {t('settings.skillLibrary.testSearchMinScoreLabel')}
                                                                        </div>
                                                                        <Input
                                                                                type="number"
                                                                                min={0}
                                                                                max={1}
                                                                                step="0.01"
                                                                                placeholder={t('settings.skillLibrary.testSearchMinScorePlaceholder')}
                                                                                value={toolMinScore}
                                                                                onChange={(event) => setToolMinScore(event.target.value)}
                                                                        />
                                                                </div>
                                                                <Button
                                                                        variant="outline"
                                                                        onClick={() => void runToolSearch()}
                                                                        disabled={toolSearchLoading || !toolQuery.trim()}
                                                                >
                                                                        <Search className="size-4" />
                                                                        {t('settings.skillLibrary.testSearchRun')}
                                                                </Button>
                                                        </div>
                                                </CardContent>
                                        </Card>

                                        {toolSearchLoading ? (
                                                <Card>
                                                        <CardContent className="py-10 text-center text-sm text-muted-foreground">
                                                                {t('common.loading')}
                                                        </CardContent>
                                                </Card>
                                        ) : toolSearchSkills.length > 0 ? (
                                                <div className="grid gap-3 md:grid-cols-2">
                                                        {toolSearchSkills.map((hit) => (
                                                                <Card key={hit.skill.id}>
                                                                        <CardContent className="space-y-4 pt-6">
                                                                                <div className="flex items-start justify-between gap-3">
                                                                                        <div className="font-medium">{hit.skill.name}</div>
                                                                                        {hit.score !== null ? (
                                                                                                <Badge variant="secondary">
                                                                                                        {t('settings.skillLibrary.testSearchScore', {
                                                                                                                score: hit.score.toFixed(4),
                                                                                                        })}
                                                                                                </Badge>
                                                                                        ) : null}
                                                                                </div>
                                                                                <div className="text-sm text-muted-foreground">
                                                                                        {hit.skill.description}
                                                                                </div>
                                                                                <div className="flex justify-end">
                                                                                        <Button
                                                                                                variant="outline"
                                                                                                onClick={() =>
                                                                                                        navigate(
                                                                                                                `/skill-library/${encodeURIComponent(hit.skill.name)}`,
                                                                                                        )
                                                                                                }
                                                                                        >
                                                                                                {t('settings.skillLibrary.viewDetail')}
                                                                                        </Button>
                                                                                </div>
                                                                        </CardContent>
                                                                </Card>
                                                        ))}
                                                </div>
                                        ) : hasToolSearchRun ? (
                                                <Card>
                                                        <CardContent className="py-10 text-center text-sm text-muted-foreground">
                                                                {t('settings.skillLibrary.testSearchEmpty')}
                                                        </CardContent>
                                                </Card>
                                        ) : null}
                                </div>
                        </div>
                </div>
        );
}
