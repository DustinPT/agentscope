import type { ChannelStatus } from '@/api';
import { useTranslation } from '@/i18n/useI18n';

const TONE: Record<string, string> = {
        disabled: 'text-muted-foreground',
        stopped: 'text-muted-foreground',
        connecting: 'text-amber-600',
        retrying: 'text-amber-600',
        connected: 'text-emerald-600',
        failed: 'text-destructive',
};

const DOT: Record<string, string> = {
        disabled: 'bg-muted-foreground/40',
        stopped: 'bg-muted-foreground/40',
        connecting: 'bg-amber-500',
        retrying: 'bg-amber-500',
        connected: 'bg-emerald-500',
        failed: 'bg-destructive',
};

export function ChannelStatusBadge({
        enabled,
        status,
}: {
        enabled: boolean;
        status?: ChannelStatus | null;
}) {
        const { t } = useTranslation();
        const state = !enabled ? 'disabled' : (status?.state ?? 'connecting');
        const label = state === 'disabled' ? t('common.disabled') : t(`channel.state.${state}`);

        return (
                <span
                        className={`inline-flex items-center gap-1.5 text-xs font-medium ${TONE[state] ?? TONE.disabled}`}
                >
                        <span className={`size-1.5 rounded-full ${DOT[state] ?? DOT.disabled}`} />
                        {label}
                </span>
        );
}
