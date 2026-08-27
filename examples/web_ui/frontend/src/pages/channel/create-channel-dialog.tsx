import { CircleAlert, Loader2, PlusCircle, X } from 'lucide-react';
import * as React from 'react';

import type { ChannelTypeSchema } from '@/api';
import { channelApi } from '@/api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
        Dialog,
        DialogContent,
        DialogDescription,
        DialogFooter,
        DialogHeader,
        DialogTitle,
} from '@/components/ui/dialog';
import { useAgents } from '@/hooks/useAgents';
import { useTranslation } from '@/i18n/useI18n';
import {
        ChannelForm,
        defaultChannelForm,
        isChannelFormValid,
        toCreateRequest,
} from '@/pages/channel/channel-form';

interface Props {
        open: boolean;
        onOpenChange: (open: boolean) => void;
        onCreated?: () => void;
        initialType?: string;
}

export function CreateChannelDialog({ open, onOpenChange, onCreated, initialType }: Props) {
        const { t } = useTranslation();
        const { agents } = useAgents();
        const [form, setForm] = React.useState(defaultChannelForm);
        const [channelTypes, setChannelTypes] = React.useState<ChannelTypeSchema[]>([]);
        const [loading, setLoading] = React.useState(false);
        const [error, setError] = React.useState('');

        React.useEffect(() => {
                if (open) {
                        setForm({
                                ...defaultChannelForm(agents[0]?.id ?? ''),
                                channelType: initialType ?? 'feishu',
                        });
                        setError('');
                        channelApi
                                .listTypes()
                                .then((types) => {
                                        setChannelTypes(types);
                                        setForm((current) =>
                                                types.some((type) => type.channel_type === current.channelType)
                                                        ? current
                                                        : {
                                                                  ...current,
                                                                  channelType:
                                                                          types[0]?.channel_type ??
                                                                          current.channelType,
                                                          },
                                        );
                                })
                                .catch(() => {});
                }
        }, [open, agents, initialType]);

        const valid = isChannelFormValid(form, 'create');
        const picked = channelTypes.find((type) => type.channel_type === form.channelType);

        const handleSubmit = async () => {
                setError('');
                if (!valid) return;
                setLoading(true);
                try {
                        await channelApi.create(toCreateRequest(form));
                        onCreated?.();
                        onOpenChange(false);
                } catch (e) {
                        setError(e instanceof Error ? e.message : String(e));
                } finally {
                        setLoading(false);
                }
        };

        return (
                <Dialog open={open} onOpenChange={onOpenChange}>
                        <DialogContent className="!w-[560px] !max-w-[560px]">
                                <DialogHeader>
                                        <DialogTitle>
                                                {picked
                                                        ? t('channel.create.titleFor', {
                                                                  name: picked.display_name,
                                                          })
                                                        : t('channel.create.title')}
                                        </DialogTitle>
                                        <DialogDescription>{t('channel.create.description')}</DialogDescription>
                                </DialogHeader>

                                <div className="no-scrollbar -mx-4 max-h-[75vh] overflow-y-auto px-4 pt-1">
                                        <ChannelForm
                                                mode="create"
                                                value={form}
                                                onChange={setForm}
                                                agents={agents}
                                                channelTypes={channelTypes}
                                        />
                                        {error && (
                                                <Alert variant="destructive" className="mt-2">
                                                        <CircleAlert />
                                                        <AlertDescription>{error}</AlertDescription>
                                                </Alert>
                                        )}
                                </div>

                                <DialogFooter>
                                        <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={loading}>
                                                <X className="size-3.5" />
                                                {t('common.cancel')}
                                        </Button>
                                        <Button onClick={handleSubmit} disabled={loading || !valid}>
                                                {loading ? (
                                                        <Loader2 className="size-3.5 animate-spin" />
                                                ) : (
                                                        <PlusCircle className="size-3.5" />
                                                )}
                                                {loading ? t('common.creating') : t('common.create')}
                                        </Button>
                                </DialogFooter>
                        </DialogContent>
                </Dialog>
        );
}
