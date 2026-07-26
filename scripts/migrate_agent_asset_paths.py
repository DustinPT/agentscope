#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Migrate managed agent asset paths from absolute to root-relative form."""

import argparse
import asyncio
from dataclasses import dataclass
import os
import re
from typing import Any

import redis.asyncio as redis

from agentscope.app.storage import AgentRecord, RedisStorage

DEFAULT_OLD_ROOT = "/Users/wengjing/vscode/my-agent-creator/.agent_assets"
AGENT_INDEX_KEY_PATTERN = "agentscope:user:*:agents"
AGENT_INDEX_KEY_RE = re.compile(r"^agentscope:user:(?P<user_id>.+):agents$")


@dataclass
class MigrationStats:
    """Collect migration counters for the final summary."""

    agent_index_keys: int = 0
    agents_scanned: int = 0
    agents_updated: int = 0
    migrated_skill_paths: int = 0
    migrated_mcp_paths: int = 0
    already_relative_paths: int = 0
    skipped_external_absolute_paths: int = 0
    missing_records: int = 0


def _decode_redis_value(value: Any) -> str:
    """Convert one Redis scalar value to ``str``."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _to_relative_path(
    path: str,
    old_root: str,
) -> tuple[str, str]:
    """Convert one persisted path to a root-relative path when applicable."""
    if not path or not os.path.isabs(path):
        return path, "already_relative"

    absolute = os.path.abspath(path)
    try:
        common = os.path.commonpath([old_root, absolute])
    except ValueError:
        return path, "outside_old_root"

    if common != old_root:
        return path, "outside_old_root"

    relative = os.path.relpath(absolute, old_root).replace(os.sep, "/")
    if relative in {"", "."}:
        return path, "outside_old_root"
    return relative, "migrated"


async def _rewrite_agent_record(
    record: AgentRecord,
    *,
    old_root: str,
    agent_key: str,
    stats: MigrationStats,
) -> AgentRecord | None:
    """Return an updated record when at least one asset path changes."""
    changed = False

    updated_skills = []
    for skill in record.data.skills:
        new_dir, status = _to_relative_path(skill.dir, old_root)
        if status == "migrated":
            changed = True
            stats.migrated_skill_paths += 1
        elif status == "already_relative":
            stats.already_relative_paths += 1
        else:
            stats.skipped_external_absolute_paths += 1
            print(
                f"[warn] Skip skill path outside old root: {agent_key} "
                f"{skill.name} -> {skill.dir}",
            )
        updated_skills.append(
            skill if new_dir == skill.dir else skill.model_copy(update={"dir": new_dir}),
        )

    updated_mcp_assets = []
    for asset in record.data.mcp_assets:
        new_dir, status = _to_relative_path(asset.dir, old_root)
        if status == "migrated":
            changed = True
            stats.migrated_mcp_paths += 1
        elif status == "already_relative":
            stats.already_relative_paths += 1
        else:
            stats.skipped_external_absolute_paths += 1
            print(
                f"[warn] Skip MCP path outside old root: {agent_key} "
                f"{asset.name} -> {asset.dir}",
            )
        updated_mcp_assets.append(
            asset if new_dir == asset.dir else asset.model_copy(update={"dir": new_dir}),
        )

    if not changed:
        return None

    return record.model_copy(
        update={
            "data": record.data.model_copy(
                update={
                    "skills": updated_skills,
                    "mcp_assets": updated_mcp_assets,
                },
            ),
        },
    )


async def migrate_agent_asset_paths(args: argparse.Namespace) -> int:
    """Migrate all managed agent asset paths stored in Redis."""
    old_root = os.path.abspath(args.old_root)
    client = redis.Redis(
        host=args.redis_host,
        port=args.redis_port,
        db=args.redis_db,
        password=args.redis_password,
    )
    key_config = RedisStorage.KeyConfig()
    stats = MigrationStats()

    try:
        async for raw_index_key in client.scan_iter(match=AGENT_INDEX_KEY_PATTERN):
            index_key = _decode_redis_value(raw_index_key)
            match = AGENT_INDEX_KEY_RE.fullmatch(index_key)
            if match is None:
                continue
            stats.agent_index_keys += 1
            user_id = match.group("user_id")
            agent_ids = await client.smembers(index_key)
            for raw_agent_id in agent_ids:
                agent_id = _decode_redis_value(raw_agent_id)
                agent_key = key_config.agent.format(
                    user_id=user_id,
                    agent_id=agent_id,
                )
                raw_record = await client.get(agent_key)
                if raw_record is None:
                    stats.missing_records += 1
                    continue

                stats.agents_scanned += 1
                record = AgentRecord.model_validate_json(raw_record)
                updated_record = await _rewrite_agent_record(
                    record,
                    old_root=old_root,
                    agent_key=agent_key,
                    stats=stats,
                )
                if updated_record is None:
                    continue

                stats.agents_updated += 1
                print(
                    f"[{'dry-run' if args.dry_run else 'write'}] "
                    f"{agent_key}",
                )
                if args.dry_run:
                    continue

                ttl = await client.ttl(agent_key)
                await client.set(agent_key, updated_record.model_dump_json())
                if ttl > 0:
                    await client.expire(agent_key, ttl)
    finally:
        await client.aclose()

    print("")
    print("Migration summary")
    print(f"- agent index keys: {stats.agent_index_keys}")
    print(f"- agents scanned: {stats.agents_scanned}")
    print(f"- agents updated: {stats.agents_updated}")
    print(f"- migrated skill paths: {stats.migrated_skill_paths}")
    print(f"- migrated MCP paths: {stats.migrated_mcp_paths}")
    print(f"- already relative paths: {stats.already_relative_paths}")
    print(
        "- skipped absolute paths outside old root: "
        f"{stats.skipped_external_absolute_paths}",
    )
    print(f"- missing agent records: {stats.missing_records}")
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Migrate AgentSkillAsset.dir and AgentMCPAsset.dir from "
            "absolute paths to paths relative to the managed asset root."
        ),
    )
    parser.add_argument("--redis-host", default="localhost")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--redis-db", type=int, default=0)
    parser.add_argument("--redis-password", default=None)
    parser.add_argument("--old-root", default=DEFAULT_OLD_ROOT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print the records that would be updated.",
    )
    return parser


def main() -> int:
    """Run the CLI entry point."""
    parser = build_argument_parser()
    args = parser.parse_args()
    return asyncio.run(migrate_agent_asset_paths(args))


if __name__ == "__main__":
    raise SystemExit(main())
