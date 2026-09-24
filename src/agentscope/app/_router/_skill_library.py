# -*- coding: utf-8 -*-
"""Skill library router for user-scoped skill management and retrieval."""

from __future__ import annotations

import mimetypes
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from .._service._skill_library import SkillLibraryService
from ..deps import get_current_user_id, get_skill_library_service
from ..storage import SkillLibraryRecord

skill_library_router = APIRouter(prefix="/skill-library", tags=["skill-library"])


class SkillLibraryFileEntry(BaseModel):
    """One file or directory entry inside a stored skill package."""

    name: str
    path: str
    is_dir: bool
    mime_type: str | None = None
    size_bytes: int | None = None
    mtime: float | None = None


class SkillLibraryListResponse(BaseModel):
    """Paginated skill list response."""

    skills: list[SkillLibraryRecord]
    total: int


class SkillLibrarySearchHit(BaseModel):
    """One skill search hit with its optional rerank score."""

    skill: SkillLibraryRecord
    score: float | None = None


class SkillLibrarySearchResponse(BaseModel):
    """Top-N skill search response for tool-style retrieval."""

    skills: list[SkillLibrarySearchHit]


async def get_skill_download_user_id(
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    user_id: str | None = Query(default=None),
) -> str:
    """Resolve browser-native download identity."""
    resolved = x_user_id or user_id
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-ID header or user_id query parameter is required.",
        )
    return resolved


@skill_library_router.post(
    "/",
    response_model=SkillLibraryRecord,
)
async def upload_skill(
    file: UploadFile = File(..., description="ZIP skill package."),
    user_id: str = Depends(get_current_user_id),
    service: SkillLibraryService = Depends(get_skill_library_service),
) -> SkillLibraryRecord:
    """Upload one ZIP skill package into the caller's skill library."""
    return await service.upload_skill(user_id=user_id, file=file)


@skill_library_router.get(
    "/",
    response_model=SkillLibraryListResponse,
)
async def list_skills(
    keyword: str | None = Query(
        default=None,
        description="Keyword filter for skill name or description.",
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
    service: SkillLibraryService = Depends(get_skill_library_service),
) -> SkillLibraryListResponse:
    """List user-owned skills with keyword filtering."""
    skills, total = await service.list_skills(
        user_id=user_id,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )
    return SkillLibraryListResponse(skills=skills, total=total)


@skill_library_router.get(
    "/search",
    response_model=SkillLibrarySearchResponse,
)
async def search_skills(
    query: str = Query(..., min_length=1, description="Search query for top-N relevant skills."),
    limit: int = Query(default=5, ge=1, le=20),
    min_score: float | None = Query(
        default=None,
        description="Only return results whose rerank score is greater than this value.",
    ),
    user_id: str = Depends(get_current_user_id),
    service: SkillLibraryService = Depends(get_skill_library_service),
) -> SkillLibrarySearchResponse:
    """Search the top-N most relevant skills for testing or tool usage."""
    skills = await service.search_skills(
        user_id=user_id,
        query=query,
        limit=limit,
        min_score=min_score,
    )
    return SkillLibrarySearchResponse(
        skills=[
            SkillLibrarySearchHit(skill=item.record, score=item.score)
            for item in skills
        ],
    )


@skill_library_router.get(
    "/{skill_name}",
    response_model=SkillLibraryRecord,
)
async def get_skill_detail(
    skill_name: str,
    user_id: str = Depends(get_current_user_id),
    service: SkillLibraryService = Depends(get_skill_library_service),
) -> SkillLibraryRecord:
    """Return one skill detail record."""
    return await service.get_skill(user_id, skill_name)


@skill_library_router.delete(
    "/{skill_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_skill(
    skill_name: str,
    user_id: str = Depends(get_current_user_id),
    service: SkillLibraryService = Depends(get_skill_library_service),
) -> None:
    """Delete one skill and its associated metadata."""
    await service.delete_skill(user_id=user_id, skill_name=skill_name)


@skill_library_router.get(
    "/{skill_name}/download",
)
async def download_skill_archive(
    skill_name: str,
    user_id: str = Depends(get_skill_download_user_id),
    service: SkillLibraryService = Depends(get_skill_library_service),
) -> StreamingResponse:
    """Download one stored skill as a ZIP archive."""
    filename, payload = await service.zip_skill(
        user_id=user_id,
        skill_name=skill_name,
    )
    quoted = quote(filename)
    headers = {
        "Content-Disposition": (
            f"attachment; filename={filename!r}; filename*=UTF-8''{quoted}"
        ),
    }
    return StreamingResponse(
        iter([payload]),
        media_type="application/zip",
        headers=headers,
    )


@skill_library_router.get(
    "/{skill_name}/files",
    response_model=list[SkillLibraryFileEntry],
)
async def list_skill_files(
    skill_name: str,
    path: str = Query(default="", description="Directory path relative to the skill root."),
    user_id: str = Depends(get_current_user_id),
    service: SkillLibraryService = Depends(get_skill_library_service),
) -> list[SkillLibraryFileEntry]:
    """List one directory inside the skill package."""
    entries = await service.list_skill_files(
        user_id=user_id,
        skill_name=skill_name,
        relative_path=path,
    )
    return [SkillLibraryFileEntry.model_validate(entry) for entry in entries]


@skill_library_router.get(
    "/{skill_name}/files/content",
    response_class=PlainTextResponse,
)
async def preview_skill_file_text(
    skill_name: str,
    path: str = Query(..., description="File path relative to the skill root."),
    user_id: str = Depends(get_current_user_id),
    service: SkillLibraryService = Depends(get_skill_library_service),
) -> PlainTextResponse:
    """Return one file as decoded text for preview panes."""
    _abs_path, payload = await service.read_skill_file(
        user_id=user_id,
        skill_name=skill_name,
        relative_path=path,
    )
    return PlainTextResponse(payload.decode("utf-8", errors="replace"))


@skill_library_router.get(
    "/{skill_name}/files/raw",
)
async def preview_skill_file_raw(
    skill_name: str,
    path: str = Query(..., description="File path relative to the skill root."),
    user_id: str = Depends(get_skill_download_user_id),
    service: SkillLibraryService = Depends(get_skill_library_service),
) -> StreamingResponse:
    """Return one raw file for inline preview."""
    abs_path, payload = await service.read_skill_file(
        user_id=user_id,
        skill_name=skill_name,
        relative_path=path,
    )
    media_type = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
    return StreamingResponse(iter([payload]), media_type=media_type)


@skill_library_router.get(
    "/{skill_name}/files/download",
)
async def download_skill_file(
    skill_name: str,
    path: str = Query(..., description="File path relative to the skill root."),
    user_id: str = Depends(get_skill_download_user_id),
    service: SkillLibraryService = Depends(get_skill_library_service),
) -> StreamingResponse:
    """Download one file from the stored skill package."""
    _abs_path, payload = await service.read_skill_file(
        user_id=user_id,
        skill_name=skill_name,
        relative_path=path,
    )
    filename = path.rsplit("/", 1)[-1]
    quoted = quote(filename)
    headers = {
        "Content-Disposition": (
            f"attachment; filename={filename!r}; filename*=UTF-8''{quoted}"
        ),
    }
    return StreamingResponse(
        iter([payload]),
        media_type="application/octet-stream",
        headers=headers,
    )
