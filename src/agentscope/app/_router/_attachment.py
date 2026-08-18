# -*- coding: utf-8 -*-
"""Attachment download router."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import Response

from ..deps import (
    get_attachment_store,
    get_storage,
    get_workspace_manager,
)
from .._service import AttachmentStore
from ..storage import StorageBase
from ..workspace_manager import WorkspaceManagerBase

attachment_router = APIRouter(
    prefix="/attachments",
    tags=["attachments"],
    responses={404: {"description": "Not found"}},
)


async def get_attachment_request_user_id(
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    user_id: str | None = Query(default=None),
) -> str:
    """Return caller identity for attachment fetches.

    Browser-native attachment loads (e.g. ``<img src>`` / ``<a href>``)
    cannot attach the custom ``X-User-ID`` header. For these routes we
    therefore allow the same temporary identity to be supplied via the
    ``user_id`` query parameter as a fallback.
    """
    resolved = x_user_id or user_id
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-ID header or user_id query parameter is required.",
        )
    return resolved


@attachment_router.get(
    "/{session_id}/{message_id}/{attachment_name}",
    name="download_attachment",
    summary="Download one message attachment",
)
async def download_attachment(
    session_id: str,
    message_id: str,
    attachment_name: str,
    user_id: str = Depends(get_attachment_request_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    attachment_store: AttachmentStore = Depends(get_attachment_store),
) -> Response:
    """Proxy one attachment from the session workspace to the client."""
    session = await storage.get_session_meta(user_id, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    workspace = await workspace_manager.get_workspace(
        user_id,
        session.agent_id,
        session.id,
        session.config.workspace_id,
    )
    try:
        payload = await attachment_store.read_attachment_bytes(
            workspace,
            session_id=session_id,
            message_id=message_id,
            attachment_name=attachment_name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found.",
        ) from exc

    filename = attachment_name
    headers = {
        "Content-Disposition": (
            f"inline; filename*=UTF-8''{quote(filename)}"
        ),
    }
    return Response(
        content=payload,
        media_type=attachment_store.guess_media_type(filename),
        headers=headers,
    )
