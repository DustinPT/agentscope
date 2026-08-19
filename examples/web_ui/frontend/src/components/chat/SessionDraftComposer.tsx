import type { ContentBlock } from '@agentscope-ai/agentscope/message';
import { useCallback, useEffect, useMemo, useState } from 'react';

import type { PermissionMode, ChatModelConfig } from '@/api';
import { EmptyMessage } from '@/components/chat/Empty';
import {
	buildContentBlockFromFile,
} from '@/components/chat/inputUtils';
import { TextInput, type ProcessedFile } from '@/components/chat/TextInput';
import { CreateCredentialDialog } from '@/components/dialog/CreateCredentialDialog';
import { ModelParametersPopover } from '@/components/popover/ModelParametersPopover';
import { LlmSelect } from '@/components/select/LlmSelect';
import { PermissionModeSelect } from '@/components/select/PermissionModeSelect';
import { useAvailableModels } from '@/hooks/useAvailableModels';

export interface SessionDraftState {
	text: string;
	files: ProcessedFile[];
	chatModelConfig: ChatModelConfig | null;
	fallbackChatModelConfig: ChatModelConfig | null;
	permissionMode: PermissionMode;
}

interface SessionDraftComposerProps {
	agentId: string;
	draft: SessionDraftState;
	onDraftChange: (
		updater: SessionDraftState | ((prev: SessionDraftState) => SessionDraftState),
	) => void;
	onSubmit: (content: ContentBlock[]) => Promise<void>;
	submitting?: boolean;
}

export function SessionDraftComposer({
	agentId,
	draft,
	onDraftChange,
	onSubmit,
	submitting = false,
}: SessionDraftComposerProps) {
	const { groups } = useAvailableModels();
	const [credentialOpen, setCredentialOpen] = useState(false);
	const [credentialRefetchTrigger, setCredentialRefetchTrigger] = useState(0);

	const updateDraft = useCallback((
		updater: SessionDraftState | ((prev: SessionDraftState) => SessionDraftState),
	) => {
		onDraftChange(updater);
	}, [onDraftChange]);

	const getFirstAvailableModel = useCallback(() => {
			const firstType = Object.keys(groups)[0];
			if (!firstType) return null;
			const items = groups[firstType];
			if (!items || items.length === 0) return null;
			const firstItem = items[0];
			const firstModel = (firstItem.models as { name?: string; id?: string }[])[0];
			if (!firstModel) return null;
			const modelName = firstModel.name ?? firstModel.id ?? null;
			if (!modelName) return null;
			return {
				type: firstType,
				credential_id: firstItem.credential.id,
				model: modelName,
				parameters: {},
			};
	}, [groups]);

	useEffect(() => {
		if (draft.chatModelConfig) return;
		const firstModel = getFirstAvailableModel();
		if (!firstModel) return;
		updateDraft((prev) => ({ ...prev, chatModelConfig: firstModel }));
	}, [draft.chatModelConfig, getFirstAvailableModel, updateDraft]);

	const selectedModelCard = useMemo(() => {
		if (!draft.chatModelConfig) return null;
		const items = groups[draft.chatModelConfig.type];
		if (!items) return null;
		for (const { models } of items) {
			const card = models.find((model) => model.name === draft.chatModelConfig?.model);
			if (card) return card;
		}
		return null;
	}, [draft.chatModelConfig, groups]);

	return (
		<>
			<main className="flex size-full">
				<div className="flex flex-1 flex-col min-h-0 p-2">
					<div className="flex flex-row justify-between gap-x-2">
						<div className="flex flex-row items-center gap-x-1">
							<LlmSelect
								value={draft.chatModelConfig}
								onChange={(value) =>
									updateDraft((prev) => ({
										...prev,
										chatModelConfig: value,
									}))
								}
								onAddCredential={() => setCredentialOpen(true)}
								refetchTrigger={credentialRefetchTrigger}
							/>
							<ModelParametersPopover
								selectedModel={draft.chatModelConfig}
								modelCard={selectedModelCard}
								onChange={(parameters) =>
									updateDraft((prev) => ({
										...prev,
										chatModelConfig: prev.chatModelConfig
											? { ...prev.chatModelConfig, parameters }
											: prev.chatModelConfig,
									}))
								}
								selectedFallbackModel={draft.fallbackChatModelConfig}
								onFallbackChange={(value) =>
									updateDraft((prev) => ({
										...prev,
										fallbackChatModelConfig: value,
									}))
								}
							/>
						</div>
						<div className="flex flex-row gap-x-2">
							<PermissionModeSelect
								value={draft.permissionMode}
								disabled={submitting}
								onChange={(value) =>
									updateDraft((prev) => ({
										...prev,
										permissionMode: value,
									}))
								}
							/>
						</div>
					</div>
                                        <div className="flex flex-1 justify-center min-h-0 overflow-hidden [--chat-content-w:750px]">
						<div className="flex h-full w-full max-w-[var(--chat-content-w)] flex-col gap-4 p-2">
							<div className="flex-1 overflow-auto no-scrollbar">
								<div className="flex size-full flex-col">
									<EmptyMessage />
								</div>
							</div>
							<TextInput
								className="min-w-full max-w-full w-full"
								onSend={onSubmit}
								focusKey={`draft:${agentId}`}
								disabled={submitting || draft.chatModelConfig === null}
                                                                allowedInputTypes={undefined}
								fileProcessor={buildContentBlockFromFile}
								value={draft.text}
								onValueChange={(value) =>
									updateDraft((prev) => ({
										...prev,
										text: value,
									}))
								}
								files={draft.files}
								onFilesChange={(files) =>
									updateDraft((prev) => ({
										...prev,
										files,
									}))
								}
							/>
						</div>
					</div>
				</div>
			</main>
			<CreateCredentialDialog
				open={credentialOpen}
				onOpenChange={setCredentialOpen}
				onCreated={() => setCredentialRefetchTrigger((n) => n + 1)}
			/>
		</>
	);
}
