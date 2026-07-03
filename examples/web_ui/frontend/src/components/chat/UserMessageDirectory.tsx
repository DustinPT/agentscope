import { List } from 'lucide-react';

import type { UserMessageOutlineItem } from './userMessageOutline';
import { Button } from '@/components/ui/button';
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from '@/components/ui/sheet';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useTranslation } from '@/i18n/useI18n';
import { cn } from '@/lib/utils';

interface UserMessageDirectoryProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	items: UserMessageOutlineItem[];
	activeMessageId?: string | null;
	onSelect: (messageId: string) => void;
}

export function UserMessageDirectory({
	open,
	onOpenChange,
	items,
	activeMessageId,
	onSelect,
}: UserMessageDirectoryProps) {
	const { t } = useTranslation();

	return (
		<Sheet open={open} onOpenChange={onOpenChange}>
			<SheetContent side="right" className="p-0">
				<SheetHeader className="border-b">
					<SheetTitle>{t('user-message-directory.title')}</SheetTitle>
					<SheetDescription>
						{t('user-message-directory.description')}
					</SheetDescription>
				</SheetHeader>
				<div className="flex min-h-0 flex-1 flex-col p-4">
					{items.length === 0 ? (
						<div className="flex flex-1 items-center justify-center rounded-lg border border-dashed text-sm text-muted-foreground">
							{t('user-message-directory.empty')}
						</div>
					) : (
						<div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
							{items.map((item) => (
								<Button
									key={item.messageId}
									type="button"
									variant="ghost"
									className={cn(
										'h-auto justify-start gap-3 rounded-lg border px-3 py-2 text-left',
										activeMessageId === item.messageId
											? 'border-primary/40 bg-primary/8 text-foreground'
											: 'border-transparent',
									)}
									onClick={() => onSelect(item.messageId)}
								>
									<span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-muted-foreground">
										{item.index}
									</span>
									<Tooltip>
										<TooltipTrigger asChild>
											<span className="min-w-0 flex-1 truncate text-sm">
												{item.title}
											</span>
										</TooltipTrigger>
										<TooltipContent
											side="left"
											sideOffset={8}
											className="max-w-md whitespace-pre-wrap break-words"
										>
											{item.title}
										</TooltipContent>
									</Tooltip>
									<List className="size-4 text-muted-foreground" />
								</Button>
							))}
						</div>
					)}
				</div>
			</SheetContent>
		</Sheet>
	);
}
