# -*- coding: utf-8 -*-
"""Managed skill and MCP asset storage for agent-level workspace configuration."""

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import frontmatter
from fastapi import HTTPException, UploadFile, status

from ...mcp import MCPClient
from ..storage import AgentMCPAsset, AgentSkillAsset


@dataclass
class StagedSkillAsset:
    """Temporary extracted skill awaiting commit."""

    name: str
    description: str
    archive_name: str
    temp_dir: str
    content_hash: str


@dataclass
class StagedMCPAsset:
    """Temporary extracted MCP awaiting commit."""

    name: str
    archive_name: str
    temp_dir: str
    content_hash: str
    client: MCPClient


@dataclass
class StagedAgentPackageAgent:
    """One agent entry extracted from an uploaded agent package."""

    id: str
    name: str
    description: str
    allowed_subagent_ids: list[str]
    system_prompt: str
    skill_dirs: list[str]
    mcp_dirs: list[str]


@dataclass
class StagedAgentPackage:
    """Temporary extracted agent package awaiting import."""

    temp_dir: str
    main_agent_id: str
    agents: list[StagedAgentPackageAgent]


class AgentAssetStore:
    """Manage staged and committed agent skill and MCP assets on local disk."""

    _AGENT_ID_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,31}")

    def __init__(self, root_dir: str) -> None:
        self._root_dir = os.path.abspath(root_dir)
        self._staging_dir = os.path.join(self._root_dir, ".staging")

    @property
    def root_dir(self) -> str:
        """Return the absolute root directory."""
        return self._root_dir

    def _ensure_path_within_root(self, path: str) -> str:
        """Normalize one absolute path and ensure it stays under ``root_dir``."""
        absolute = os.path.abspath(path)
        try:
            common = os.path.commonpath([self._root_dir, absolute])
        except ValueError as exc:
            raise ValueError(
                f"Asset path {path!r} is not under asset root {self._root_dir!r}.",
            ) from exc
        if common != self._root_dir:
            raise ValueError(
                f"Asset path {path!r} resolves outside asset root {self._root_dir!r}.",
            )
        return absolute

    def to_relative_dir(self, dir_path: str) -> str:
        """Convert one absolute asset directory to a root-relative path."""
        absolute = self._ensure_path_within_root(dir_path)
        relative = os.path.relpath(absolute, self._root_dir)
        normalized = relative.replace(os.sep, "/")
        if normalized in {"", "."}:
            raise ValueError("Asset directory cannot be the asset root itself.")
        return normalized

    def resolve_dir(self, dir_path: str) -> str:
        """Resolve one persisted asset directory to an absolute local path.

        Legacy records may still contain absolute paths, so those are accepted
        unchanged. New records are expected to be stored relative to the
        managed asset root.
        """
        if os.path.isabs(dir_path):
            return os.path.abspath(dir_path)
        normalized = dir_path.replace("\\", os.sep)
        candidate = os.path.join(self._root_dir, normalized)
        return self._ensure_path_within_root(candidate)

    def _agent_dir(self, user_id: str, agent_id: str) -> str:
        return os.path.join(self._root_dir, user_id, agent_id)

    def _agent_skill_root_dir(self, user_id: str, agent_id: str) -> str:
        return os.path.join(self._agent_dir(user_id, agent_id), "skills")

    def _agent_skill_dir(
        self,
        user_id: str,
        agent_id: str,
        skill_name: str,
    ) -> str:
        return os.path.join(
            self._agent_skill_root_dir(user_id, agent_id),
            skill_name,
        )

    def _agent_mcp_dir(self, user_id: str, agent_id: str) -> str:
        return os.path.join(self._agent_dir(user_id, agent_id), "mcps")

    @staticmethod
    def _assert_safe_zip(members: list[zipfile.ZipInfo]) -> None:
        for member in members:
            path = member.filename
            if not path or path.endswith("/"):
                continue
            normalized = Path(path)
            if normalized.is_absolute() or ".." in normalized.parts:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid ZIP entry path: {path}",
                )

    @staticmethod
    def _find_skill_root(extract_dir: str) -> str:
        candidates: list[str] = []
        for root, _dirs, files in os.walk(extract_dir):
            if "SKILL.md" in files:
                candidates.append(root)
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Skill ZIP must contain a SKILL.md file.",
            )
        if len(candidates) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Skill ZIP must contain exactly one skill root.",
            )
        return candidates[0]

    @staticmethod
    def _find_mcp_root(extract_dir: str) -> str:
        candidates: list[str] = []
        for root, _dirs, files in os.walk(extract_dir):
            if "mcp.json" in files:
                candidates.append(root)
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MCP ZIP must contain a mcp.json file.",
            )
        if len(candidates) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MCP ZIP must contain exactly one MCP root.",
            )
        return candidates[0]

    @staticmethod
    def _find_agent_package_root(extract_dir: str) -> str:
        candidates: list[str] = []
        for root, _dirs, files in os.walk(extract_dir):
            if "config.json" in files:
                candidates.append(root)
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent package ZIP must contain a config.json file.",
            )
        if len(candidates) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent package ZIP must contain exactly one config.json file.",
            )
        return candidates[0]

    @staticmethod
    def _hash_directory(dir_path: str) -> str:
        digest = hashlib.sha256()
        for root, dirs, files in os.walk(dir_path):
            dirs.sort()
            files.sort()
            for file_name in files:
                abs_path = os.path.join(root, file_name)
                rel_path = os.path.relpath(abs_path, dir_path).replace(
                    os.sep,
                    "/",
                )
                digest.update(rel_path.encode("utf-8"))
                digest.update(b"\0")
                with open(abs_path, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
        return digest.hexdigest()

    def _copy_dir_to_staging(self, source_dir: str, prefix: str) -> str:
        os.makedirs(self._staging_dir, exist_ok=True)
        staging_root = tempfile.mkdtemp(prefix=prefix, dir=self._staging_dir)
        staged_dir = os.path.join(staging_root, "payload")
        shutil.copytree(source_dir, staged_dir)
        return staged_dir

    @staticmethod
    def _read_agent_package_config(
        package_root: str,
    ) -> tuple[str, list[dict[str, object]]]:
        config_path = os.path.join(package_root, "config.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid config.json: {exc}",
            ) from exc

        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="config.json must be a JSON object.",
            )

        agents = payload.get("agents")
        if not isinstance(agents, list) or not agents:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="config.json must include a non-empty 'agents' array.",
            )

        normalized_agents: list[dict[str, object]] = []
        agent_ids: list[str] = []
        for item in agents:
            if not isinstance(item, dict):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Each agent entry in config.json must be an object.",
                )
            agent_id = item.get("id")
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Each agent entry must include a non-empty 'id'.",
                )
            if not AgentAssetStore._AGENT_ID_PATTERN.fullmatch(agent_id):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Invalid agent id '{agent_id}'. Agent ids must start "
                        "with a letter or underscore, contain only letters, "
                        "digits, underscores, or hyphens, and be at most 32 "
                        "characters long."
                    ),
                )
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Agent '{agent_id}' must include a non-empty 'name'.",
                )
            description = item.get("description", "")
            if not isinstance(description, str):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Agent '{agent_id}' has an invalid 'description'.",
                )
            allowed_subagent_ids = item.get("allowed_subagent_ids", [])
            if not isinstance(allowed_subagent_ids, list) or any(
                not isinstance(subagent_id, str)
                or not subagent_id.strip()
                for subagent_id in allowed_subagent_ids
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Agent '{agent_id}' must use a string array for "
                        "'allowed_subagent_ids'."
                    ),
                )
            if agent_id in allowed_subagent_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Agent '{agent_id}' cannot allow itself as a "
                        "sub-agent target."
                    ),
                )
            normalized_agents.append(
                {
                    "id": agent_id,
                    "name": name.strip(),
                    "description": description,
                    "allowed_subagent_ids": list(allowed_subagent_ids),
                },
            )
            agent_ids.append(agent_id)

        if len(agent_ids) != len(set(agent_ids)):
            dup = next(
                agent_id
                for agent_id in agent_ids
                if agent_ids.count(agent_id) > 1
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate agent id '{dup}' in config.json.",
            )

        main_agent_id = payload.get("main_agent")
        if not isinstance(main_agent_id, str) or main_agent_id not in set(agent_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="config.json.main_agent must reference one agent id.",
            )

        agent_id_set = set(agent_ids)
        for item in normalized_agents:
            missing = sorted(
                set(item["allowed_subagent_ids"]) - agent_id_set,  # type: ignore[arg-type]
            )
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Agent '{item['id']}' references unknown sub-agent "
                        f"'{missing[0]}'."
                    ),
                )

        return main_agent_id, normalized_agents

    @staticmethod
    def _read_skill_metadata(skill_dir: str) -> tuple[str, str, str]:
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md_path, "r", encoding="utf-8") as f:
            raw = f.read()
        doc = frontmatter.loads(raw)
        name = doc.get("name")
        description = doc.get("description")
        if not name or not description:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SKILL.md must include both 'name' and 'description'.",
            )
        return str(name), str(description), hashlib.sha256(
            raw.encode("utf-8"),
        ).hexdigest()

    @staticmethod
    def _read_mcp_metadata(mcp_dir: str) -> tuple[str, MCPClient]:
        mcp_json_path = os.path.join(mcp_dir, "mcp.json")
        try:
            with open(mcp_json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid mcp.json: {exc}",
            ) from exc

        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mcp.json must be a JSON object.",
            )

        name = payload.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "MCP name must contain only letters, digits, underscores, "
                    "or hyphens."
                ),
            )

        mcp_config = payload.get("mcp_config")
        if not isinstance(mcp_config, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mcp.json must include an object field 'mcp_config'.",
            )

        type_ = mcp_config.get("type")
        if type_ not in {"stdio_mcp", "http_mcp"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mcp_config.type must be 'stdio_mcp' or 'http_mcp'.",
            )

        model_payload = {
            "name": name,
            "is_stateful": payload.get("is_stateful"),
            "mcp_config": mcp_config,
            "execution_timeout": payload.get("execution_timeout"),
        }
        try:
            client = MCPClient.model_validate(model_payload)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid mcp.json: {exc}",
            ) from exc

        if type_ == "stdio_mcp" and not client.is_stateful:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="stdio_mcp requires is_stateful=true.",
            )

        return name, client

    async def stage_skill_zip(
        self,
        file: UploadFile,
    ) -> StagedSkillAsset:
        """Validate and extract an uploaded ZIP file into a staging area."""
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only .zip skill packages are supported.",
            )

        payload = await file.read()
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded skill package is empty.",
            )

        def _stage() -> StagedSkillAsset:
            os.makedirs(self._staging_dir, exist_ok=True)
            staging_root = tempfile.mkdtemp(
                prefix="skill_",
                dir=self._staging_dir,
            )
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    self._assert_safe_zip(zf.infolist())
                    zf.extractall(staging_root)
                skill_root = self._find_skill_root(staging_root)
                name, description, content_hash = self._read_skill_metadata(
                    skill_root,
                )
                return StagedSkillAsset(
                    name=name,
                    description=description,
                    archive_name=file.filename or f"{name}.zip",
                    temp_dir=skill_root,
                    content_hash=content_hash,
                )
            except zipfile.BadZipFile as exc:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid ZIP archive: {exc}",
                ) from exc
            except Exception:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise

        return await asyncio.to_thread(_stage)

    async def stage_mcp_zip(
        self,
        file: UploadFile,
    ) -> StagedMCPAsset:
        """Validate and extract an uploaded MCP ZIP file into staging."""
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only .zip MCP packages are supported.",
            )

        payload = await file.read()
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded MCP package is empty.",
            )

        def _stage() -> StagedMCPAsset:
            os.makedirs(self._staging_dir, exist_ok=True)
            staging_root = tempfile.mkdtemp(
                prefix="mcp_",
                dir=self._staging_dir,
            )
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    self._assert_safe_zip(zf.infolist())
                    zf.extractall(staging_root)
                mcp_root = self._find_mcp_root(staging_root)
                content_hash = hashlib.sha256(payload).hexdigest()
                name, client = self._read_mcp_metadata(mcp_root)
                return StagedMCPAsset(
                    name=name,
                    archive_name=file.filename or f"{name}.zip",
                    temp_dir=mcp_root,
                    content_hash=content_hash,
                    client=client,
                )
            except zipfile.BadZipFile as exc:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid ZIP archive: {exc}",
                ) from exc
            except Exception:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise

        return await asyncio.to_thread(_stage)

    async def stage_agent_package_zip(
        self,
        file: UploadFile,
    ) -> StagedAgentPackage:
        """Validate and extract an uploaded agent package ZIP into staging."""
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only .zip agent packages are supported.",
            )

        payload = await file.read()
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded agent package is empty.",
            )

        def _stage() -> StagedAgentPackage:
            os.makedirs(self._staging_dir, exist_ok=True)
            staging_root = tempfile.mkdtemp(
                prefix="agent_package_",
                dir=self._staging_dir,
            )
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    self._assert_safe_zip(zf.infolist())
                    zf.extractall(staging_root)
                package_root = self._find_agent_package_root(staging_root)
                main_agent_id, agent_configs = self._read_agent_package_config(
                    package_root,
                )
                agents: list[StagedAgentPackageAgent] = []
                for config in agent_configs:
                    agent_id = str(config["id"])
                    agent_dir = os.path.join(package_root, agent_id)
                    if not os.path.isdir(agent_dir):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Agent directory '{agent_id}' is missing.",
                        )
                    prompt_path = os.path.join(agent_dir, "system_prompt.md")
                    if not os.path.isfile(prompt_path):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=(
                                f"Agent '{agent_id}' must contain "
                                "'system_prompt.md'."
                            ),
                        )
                    with open(prompt_path, "r", encoding="utf-8") as f:
                        system_prompt = f.read()
                    skill_dirs: list[str] = []
                    skills_root = os.path.join(agent_dir, "skills")
                    if os.path.isdir(skills_root):
                        for entry in sorted(os.listdir(skills_root)):
                            abs_path = os.path.join(skills_root, entry)
                            if os.path.isdir(abs_path):
                                skill_dirs.append(abs_path)
                    mcp_dirs: list[str] = []
                    mcps_root = os.path.join(agent_dir, "mcps")
                    if os.path.isdir(mcps_root):
                        for entry in sorted(os.listdir(mcps_root)):
                            abs_path = os.path.join(mcps_root, entry)
                            if os.path.isdir(abs_path):
                                mcp_dirs.append(abs_path)
                    agents.append(
                        StagedAgentPackageAgent(
                            id=agent_id,
                            name=str(config["name"]),
                            description=str(config["description"]),
                            allowed_subagent_ids=list(
                                config["allowed_subagent_ids"],  # type: ignore[arg-type]
                            ),
                            system_prompt=system_prompt,
                            skill_dirs=skill_dirs,
                            mcp_dirs=mcp_dirs,
                        ),
                    )
                return StagedAgentPackage(
                    temp_dir=staging_root,
                    main_agent_id=main_agent_id,
                    agents=agents,
                )
            except zipfile.BadZipFile as exc:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid ZIP archive: {exc}",
                ) from exc
            except Exception:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise

        return await asyncio.to_thread(_stage)

    async def cleanup_staged_agent_package(
        self,
        staged: StagedAgentPackage,
    ) -> None:
        """Remove staging directories for an uploaded agent package."""

        def _cleanup() -> None:
            shutil.rmtree(staged.temp_dir, ignore_errors=True)

        await asyncio.to_thread(_cleanup)

    async def stage_skill_dir(
        self,
        skill_dir: str,
        *,
        archive_name: str | None = None,
    ) -> StagedSkillAsset:
        """Validate an existing skill directory and copy it into staging."""

        def _stage() -> StagedSkillAsset:
            source_dir = os.path.abspath(skill_dir)
            if not os.path.isdir(source_dir):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Skill path does not exist: {skill_dir}",
                )
            skill_root = self._find_skill_root(source_dir)
            name, description, content_hash = self._read_skill_metadata(skill_root)
            staged_dir = self._copy_dir_to_staging(skill_root, "skill_dir_")
            return StagedSkillAsset(
                name=name,
                description=description,
                archive_name=archive_name or f"{os.path.basename(source_dir)}.zip",
                temp_dir=staged_dir,
                content_hash=content_hash,
            )

        return await asyncio.to_thread(_stage)

    async def stage_mcp_dir(
        self,
        mcp_dir: str,
        *,
        archive_name: str | None = None,
    ) -> StagedMCPAsset:
        """Validate an existing MCP directory and copy it into staging."""

        def _stage() -> StagedMCPAsset:
            source_dir = os.path.abspath(mcp_dir)
            if not os.path.isdir(source_dir):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"MCP path does not exist: {mcp_dir}",
                )
            mcp_root = self._find_mcp_root(source_dir)
            name, client = self._read_mcp_metadata(mcp_root)
            staged_dir = self._copy_dir_to_staging(mcp_root, "mcp_dir_")
            return StagedMCPAsset(
                name=name,
                archive_name=archive_name or f"{os.path.basename(source_dir)}.zip",
                temp_dir=staged_dir,
                content_hash=self._hash_directory(mcp_root),
                client=client,
            )

        return await asyncio.to_thread(_stage)

    async def commit_staged_skills(
        self,
        user_id: str,
        agent_id: str,
        staged: list[StagedSkillAsset],
        *,
        replace_names: set[str] | None = None,
    ) -> list[AgentSkillAsset]:
        """Move staged skills into the managed agent asset directory."""

        def _commit() -> list[AgentSkillAsset]:
            skill_root_dir = self._agent_skill_root_dir(user_id, agent_id)
            os.makedirs(skill_root_dir, exist_ok=True)
            committed: list[AgentSkillAsset] = []
            replace = replace_names or set()
            for item in staged:
                target_dir = self._agent_skill_dir(user_id, agent_id, item.name)
                if os.path.exists(target_dir):
                    if item.name in replace:
                        shutil.rmtree(target_dir, ignore_errors=True)
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Skill '{item.name}' already exists.",
                        )
                shutil.move(item.temp_dir, target_dir)
                committed.append(
                    AgentSkillAsset(
                        name=item.name,
                        description=item.description,
                        archive_name=item.archive_name,
                        dir=self.to_relative_dir(target_dir),
                        content_hash=item.content_hash,
                    ),
                )
            return committed

        return await asyncio.to_thread(_commit)

    async def cleanup_staged_skills(
        self,
        staged: list[StagedSkillAsset],
    ) -> None:
        """Remove staging directories for uncommitted skill uploads."""

        def _cleanup() -> None:
            for item in staged:
                shutil.rmtree(
                    os.path.dirname(item.temp_dir),
                    ignore_errors=True,
                )

        await asyncio.to_thread(_cleanup)

    async def commit_staged_mcps(
        self,
        user_id: str,
        agent_id: str,
        staged: list[StagedMCPAsset],
        *,
        replace_names: set[str] | None = None,
    ) -> list[AgentMCPAsset]:
        """Move staged MCPs into the managed agent asset directory."""

        def _commit() -> list[AgentMCPAsset]:
            agent_dir = self._agent_mcp_dir(user_id, agent_id)
            os.makedirs(agent_dir, exist_ok=True)
            committed: list[AgentMCPAsset] = []
            replace = replace_names or set()
            for item in staged:
                target_dir = os.path.join(agent_dir, item.name)
                if os.path.exists(target_dir):
                    if item.name in replace:
                        shutil.rmtree(target_dir, ignore_errors=True)
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"MCP '{item.name}' already exists.",
                        )
                shutil.move(item.temp_dir, target_dir)
                committed.append(
                    AgentMCPAsset(
                        name=item.name,
                        archive_name=item.archive_name,
                        dir=self.to_relative_dir(target_dir),
                        content_hash=item.content_hash,
                        client=item.client,
                    ),
                )
            return committed

        return await asyncio.to_thread(_commit)

    async def cleanup_staged_mcps(
        self,
        staged: list[StagedMCPAsset],
    ) -> None:
        """Remove staging directories for uncommitted MCP uploads."""

        def _cleanup() -> None:
            for item in staged:
                shutil.rmtree(
                    os.path.dirname(item.temp_dir),
                    ignore_errors=True,
                )

        await asyncio.to_thread(_cleanup)

    async def import_skill_dir(
        self,
        user_id: str,
        agent_id: str,
        skill_path: str,
    ) -> AgentSkillAsset:
        """Copy an existing local skill directory into managed storage."""

        def _import() -> AgentSkillAsset:
            source_dir = os.path.abspath(skill_path)
            if not os.path.isdir(source_dir):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Skill path does not exist: {skill_path}",
                )
            name, description, content_hash = self._read_skill_metadata(source_dir)
            target_dir = self._agent_skill_dir(user_id, agent_id, name)
            if os.path.exists(target_dir):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Skill '{name}' already exists.",
                )
            os.makedirs(os.path.dirname(target_dir), exist_ok=True)
            shutil.copytree(source_dir, target_dir)
            return AgentSkillAsset(
                name=name,
                description=description,
                archive_name=f"{name}.zip",
                dir=self.to_relative_dir(target_dir),
                content_hash=content_hash,
            )

        return await asyncio.to_thread(_import)

    async def delete_skills(
        self,
        user_id: str,
        agent_id: str,
        skill_names: list[str],
    ) -> None:
        """Delete committed skill directories by name."""

        def _delete() -> None:
            for skill_name in skill_names:
                shutil.rmtree(
                    self._agent_skill_dir(user_id, agent_id, skill_name),
                    ignore_errors=True,
                )

        await asyncio.to_thread(_delete)

    async def delete_mcps(
        self,
        user_id: str,
        agent_id: str,
        mcp_names: list[str],
    ) -> None:
        """Delete committed MCP directories by name."""

        def _delete() -> None:
            agent_dir = self._agent_mcp_dir(user_id, agent_id)
            for mcp_name in mcp_names:
                shutil.rmtree(
                    os.path.join(agent_dir, mcp_name),
                    ignore_errors=True,
                )

        await asyncio.to_thread(_delete)

    async def zip_skill(
        self,
        user_id: str,
        agent_id: str,
        skill_name: str,
    ) -> tuple[str, bytes]:
        """Create a ZIP archive from a committed skill directory."""

        def _zip() -> tuple[str, bytes]:
            skill_dir = self._agent_skill_dir(user_id, agent_id, skill_name)
            if not os.path.isdir(skill_dir):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Skill '{skill_name}' not found.",
                )
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(skill_dir):
                    for file_name in files:
                        abs_path = os.path.join(root, file_name)
                        rel_path = os.path.relpath(abs_path, skill_dir)
                        zf.write(abs_path, arcname=os.path.join(skill_name, rel_path))
            return f"{skill_name}.zip", buf.getvalue()

        return await asyncio.to_thread(_zip)
