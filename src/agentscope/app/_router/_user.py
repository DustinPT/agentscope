# -*- coding: utf-8 -*-
"""User settings router."""

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_current_user_id, get_storage
from ._schema import (
    UpdateUserModelDefaultsRequest,
    UserModelDefaultsResponse,
)
from ..storage import GlobalDefaultModels, StorageBase, UserRecord

user_router = APIRouter(
    prefix="/user",
    tags=["user"],
    responses={404: {"description": "Not found"}},
)


async def _ensure_credential_exists(
    storage: StorageBase,
    user_id: str,
    credential_id: str | None,
) -> None:
    """Validate that a referenced credential belongs to the user."""
    if credential_id is None:
        return
    credentials = await storage.list_credentials(user_id)
    if not any(c.id == credential_id for c in credentials):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credential '{credential_id}' not found.",
        )


async def _validate_model_defaults(
    storage: StorageBase,
    user_id: str,
    body: UpdateUserModelDefaultsRequest,
) -> None:
    """Validate all non-empty default model entries."""
    for field_name in body.__class__.model_fields:
        config = getattr(body, field_name)
        if config is None:
            continue
        await _ensure_credential_exists(
            storage,
            user_id,
            config.credential_id,
        )


@user_router.get(
    "/model-defaults",
    response_model=UserModelDefaultsResponse,
    summary="Get the current user's default model settings",
)
async def get_model_defaults(
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> UserModelDefaultsResponse:
    """Return the current user's default model settings."""
    record = await storage.get_user(user_id)
    if record is None:
        return UserModelDefaultsResponse()
    return UserModelDefaultsResponse.model_validate(
        record.global_default_models.model_dump(mode="python"),
    )


@user_router.put(
    "/model-defaults",
    response_model=UserModelDefaultsResponse,
    summary="Replace the current user's default model settings",
)
async def update_model_defaults(
    body: UpdateUserModelDefaultsRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> UserModelDefaultsResponse:
    """Replace the current user's default model settings."""
    await _validate_model_defaults(storage, user_id, body)

    existing = await storage.get_user(user_id)
    record = existing or UserRecord(user_id=user_id)
    record.global_default_models = GlobalDefaultModels.model_validate(
        body.model_dump(mode="python"),
    )
    stored = await storage.upsert_user(user_id, record)
    return UserModelDefaultsResponse.model_validate(
        stored.global_default_models.model_dump(mode="python"),
    )
