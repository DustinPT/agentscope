# -*- coding: utf-8 -*-
"""Embedding model service: builds an EmbeddingModelBase from stored
credential + config."""
from fastapi import HTTPException, status

from ..storage import CredentialRecord, EmbeddingModelConfig, StorageBase
from ...credential import CredentialFactory
from ...embedding import EmbeddingModelBase


def build_embedding_model(
    credential_record: CredentialRecord,
    config: EmbeddingModelConfig,
) -> EmbeddingModelBase:
    """Construct an embedding model from an already-resolved credential.

    Args:
        credential_record (`CredentialRecord`):
            The credential record whose ``data`` will be handed to the
            provider client.
        config (`EmbeddingModelConfig`):
            The embedding model configuration (``type``, ``model``,
            ``parameters``, ``dimensions``).

    Returns:
        `EmbeddingModelBase`:
            A configured embedding model instance.

    Raises:
        `HTTPException`:
            400 if the provider does not support embedding.
    """
    credential = CredentialFactory.from_dict(credential_record.data)

    credential_cls = CredentialFactory.get_credential_class(config.type)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {config.type!r} not found.",
        )

    embedding_cls = credential_cls.get_embedding_model_class()
    if embedding_cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Provider {config.type!r} does not support "
                f"embedding models."
            ),
        )

    context_size: int | None = None
    for card in embedding_cls.list_models():
        if card.name == config.model:
            context_size = card.context_size
            break

    parameters = (
        embedding_cls.Parameters(**config.parameters)
        if config.parameters
        else None
    )

    kwargs: dict = {
        "credential": credential,
        "model": config.model,
        "dimensions": config.dimensions,
        "parameters": parameters,
    }
    if context_size is not None:
        kwargs["context_size"] = context_size

    return embedding_cls(**kwargs)


async def get_embedding_model(
    user_id: str,
    config: EmbeddingModelConfig,
    storage: StorageBase,
) -> EmbeddingModelBase:
    """Resolve the configured credential from storage and build the
    corresponding embedding model.

    Args:
        user_id (`str`):
            The user id.
        config (`EmbeddingModelConfig`):
            The embedding model configuration.
        storage (`StorageBase`):
            The storage instance.

    Returns:
        `EmbeddingModelBase`:
            A configured embedding model instance.

    Raises:
        `HTTPException`:
            404 if the credential does not exist.
            400 if the provider does not support embedding.
    """
    credential_record = await storage.get_credential(
        user_id,
        config.credential_id,
    )
    if credential_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credential {config.credential_id!r} not found.",
        )
    return build_embedding_model(credential_record, config)
