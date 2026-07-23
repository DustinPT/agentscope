import { CircleAlert, Loader2, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from '@/components/ui/dialog';
import { FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { useTranslation } from '@/i18n/useI18n';
import type { AgentPackageImportResponse } from '@/api';

interface Props {
	importPackage: (body: FormData) => Promise<AgentPackageImportResponse>;
	onImported?: () => void;
}

export function AgentPackageImportDialog({ importPackage, onImported }: Props) {
	const { t } = useTranslation();
	const inputRef = useRef<HTMLInputElement | null>(null);
	const [open, setOpen] = useState(false);
	const [submitting, setSubmitting] = useState(false);
	const [selectedFile, setSelectedFile] = useState<File | null>(null);

	const handleOpenChange = (nextOpen: boolean) => {
		setOpen(nextOpen);
		if (!nextOpen) {
			setSelectedFile(null);
		}
	};

	const handleSubmit = async () => {
		if (!selectedFile) return;
		setSubmitting(true);
		try {
			const formData = new FormData();
			formData.append('package_file', selectedFile);
			const result = await importPackage(formData);
			toast.success(
				t('dialog-agent-package-import.success', {
					created: result.created_count,
					updated: result.updated_count,
				}),
			);
			handleOpenChange(false);
			onImported?.();
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={handleOpenChange}>
			<DialogTrigger asChild>
				<Button variant="outline">
					<Upload className="size-3.5" />
					<span>{t('dialog-agent-package-import.trigger')}</span>
				</Button>
			</DialogTrigger>
			<DialogContent className="max-w-md">
				<DialogHeader>
					<DialogTitle>{t('dialog-agent-package-import.title')}</DialogTitle>
					<DialogDescription>
						{t('dialog-agent-package-import.description')}
					</DialogDescription>
				</DialogHeader>
				<div className="py-2">
					<FieldGroup>
						<div className="space-y-2">
							<FieldLabel>{t('dialog-agent-package-import.fileLabel')}</FieldLabel>
							<FieldDescription>
								{t('dialog-agent-package-import.fileDescription')}
							</FieldDescription>
							<input
								ref={inputRef}
								type="file"
								accept=".zip"
								className="hidden"
								onChange={(e) => {
									setSelectedFile(e.target.files?.[0] ?? null);
									e.currentTarget.value = '';
								}}
							/>
							<div className="flex items-center gap-3">
								<Button
									type="button"
									variant="outline"
									onClick={() => inputRef.current?.click()}
									disabled={submitting}
								>
									<Upload className="size-3.5" />
									{t('dialog-agent-package-import.select')}
								</Button>
								<span className="text-muted-foreground truncate text-sm">
									{selectedFile?.name ?? t('dialog-agent-package-import.empty')}
								</span>
							</div>
						</div>
					</FieldGroup>
				</div>
				<DialogFooter>
					<Button
						variant="ghost"
						onClick={() => handleOpenChange(false)}
						disabled={submitting}
					>
						<CircleAlert className="size-3.5" />
						{t('common.cancel')}
					</Button>
					<Button
						onClick={handleSubmit}
						disabled={!selectedFile || submitting}
					>
						{submitting ? (
							<Loader2 className="size-3.5 animate-spin" />
						) : (
							<Upload className="size-3.5" />
						)}
						{submitting
							? t('dialog-agent-package-import.importing')
							: t('dialog-agent-package-import.confirm')}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
