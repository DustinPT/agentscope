# -*- coding: utf-8 -*-
"""Storage models for user-scoped skill library records."""

from pydantic import BaseModel, Field

from ._base import _RecordBase


class SkillLibraryFileRecord(BaseModel):
    """One file stored inside a managed skill package."""

    path: str = Field(
        description="Path relative to the skill root directory.",
    )
    size_bytes: int = Field(
        description="File size in bytes.",
        ge=0,
    )
    mime_type: str | None = Field(
        default=None,
        description="Best-effort MIME type for UI preview.",
    )


class SkillLibraryRecord(_RecordBase):
    """Persisted metadata for one skill in the user skill library."""

    user_id: str = Field(
        description="Owner user id.",
    )
    name: str = Field(
        description="Unique skill name within one user scope.",
        min_length=1,
    )
    description: str = Field(
        description="Skill description from SKILL.md front matter.",
        default="",
    )
    archive_name: str = Field(
        description="Original uploaded archive name.",
    )
    dir: str = Field(
        description="Root-relative managed skill directory.",
    )
    content_hash: str = Field(
        description="Stable hash generated from all files in the package.",
    )
    skill_markdown: str = Field(
        description="Rendered SKILL.md source for detail display.",
        default="",
    )
    file_manifest: list[SkillLibraryFileRecord] = Field(
        default_factory=list,
        description="All files contained in the skill package.",
    )
    embedding: list[float] = Field(
        default_factory=list,
        description="Embedding vector generated from the skill description.",
    )
