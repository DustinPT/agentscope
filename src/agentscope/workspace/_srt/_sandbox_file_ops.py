"""Helper CLI for sandboxed filesystem operations."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import stat as stat_module
import sys
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class _DirEntry:
    name: str
    is_dir: bool
    size_bytes: int | None = None
    mtime: float | None = None


def _strict_stat(path: str) -> _DirEntry | None:
    try:
        stat_result = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None

    is_dir = stat_module.S_ISDIR(stat_result.st_mode)
    return _DirEntry(
        name=os.path.basename(path),
        is_dir=is_dir,
        size_bytes=None if is_dir else stat_result.st_size,
        mtime=stat_result.st_mtime,
    )


def _strict_delete_path(path: str) -> None:
    try:
        stat_result = os.lstat(path)
    except FileNotFoundError:
        return

    is_dir = stat_module.S_ISDIR(stat_result.st_mode)
    is_link = stat_module.S_ISLNK(stat_result.st_mode)
    if is_dir and not is_link:
        shutil.rmtree(path)
        return
    os.remove(path)


def _handle_read_file(path: str) -> None:
    with open(path, "rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)


def _handle_write_file(path: str, data_b64: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as file_obj:
        file_obj.write(base64.b64decode(data_b64.encode("ascii")))


def _handle_ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _handle_exists(path: str) -> None:
    sys.stdout.write("1" if _strict_stat(path) is not None else "0")


def _handle_is_dir(path: str) -> None:
    entry = _strict_stat(path)
    sys.stdout.write("1" if entry is not None and entry.is_dir else "0")


def _handle_list_dir(path: str, recursive: bool) -> None:
    if recursive:
        entries: list[str] = []
        for root, _dirs, files in os.walk(path):
            for file_name in files:
                entries.append(os.path.join(root, file_name))
        sys.stdout.write(json.dumps(entries))
        return
    sys.stdout.write(json.dumps(os.listdir(path)))


def _handle_scandir(path: str) -> None:
    entries: list[dict[str, object]] = []
    with os.scandir(path) as iterator:
        for entry in iterator:
            stat_result = entry.stat(follow_symlinks=True)
            is_dir = entry.is_dir(follow_symlinks=True)
            entries.append(
                asdict(
                    _DirEntry(
                        name=entry.name,
                        is_dir=is_dir,
                        size_bytes=None if is_dir else stat_result.st_size,
                        mtime=stat_result.st_mtime,
                    ),
                ),
            )
    sys.stdout.write(json.dumps(entries))


def _handle_scandir_with_samples(path: str, sample_size: int) -> None:
    entries: list[dict[str, object]] = []
    with os.scandir(path) as iterator:
        for entry in iterator:
            stat_result = entry.stat(follow_symlinks=True)
            is_dir = entry.is_dir(follow_symlinks=True)
            payload = asdict(
                _DirEntry(
                    name=entry.name,
                    is_dir=is_dir,
                    size_bytes=None if is_dir else stat_result.st_size,
                    mtime=stat_result.st_mtime,
                ),
            )
            if is_dir:
                payload["sample_b64"] = None
            else:
                with open(entry.path, "rb") as file_obj:
                    payload["sample_b64"] = base64.b64encode(
                        file_obj.read(sample_size),
                    ).decode("ascii")
            entries.append(payload)
    sys.stdout.write(json.dumps(entries))


def _handle_stat(path: str) -> None:
    entry = _strict_stat(path)
    sys.stdout.write(json.dumps(None if entry is None else asdict(entry)))


def _handle_stat_with_sample(path: str, sample_size: int) -> None:
    entry = _strict_stat(path)
    if entry is None:
        sys.stdout.write(json.dumps(None))
        return
    payload = asdict(entry)
    if entry.is_dir:
        payload["sample_b64"] = None
    else:
        with open(path, "rb") as file_obj:
            payload["sample_b64"] = base64.b64encode(
                file_obj.read(sample_size),
            ).decode("ascii")
    sys.stdout.write(json.dumps(payload))


def _handle_stat_mtime(path: str) -> None:
    entry = _strict_stat(path)
    sys.stdout.write(json.dumps({"mtime": None if entry is None else entry.mtime}))


def _handle_delete_path(path: str) -> None:
    _strict_delete_path(path)


def _write_structured_error(exc: Exception) -> None:
    payload = {
        "error_type": type(exc).__name__,
        "message": str(exc),
    }
    sys.stderr.write(json.dumps(payload))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sandboxed filesystem helper for SRT runtime service",
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)

    read_parser = subparsers.add_parser("read-file")
    read_parser.add_argument("--path", required=True)

    write_parser = subparsers.add_parser("write-file")
    write_parser.add_argument("--path", required=True)
    write_parser.add_argument("--data-b64", required=True)

    dir_parser = subparsers.add_parser("ensure-dir")
    dir_parser.add_argument("--path", required=True)

    exists_parser = subparsers.add_parser("exists")
    exists_parser.add_argument("--path", required=True)

    is_dir_parser = subparsers.add_parser("is-dir")
    is_dir_parser.add_argument("--path", required=True)

    list_dir_parser = subparsers.add_parser("list-dir")
    list_dir_parser.add_argument("--path", required=True)
    list_dir_parser.add_argument("--recursive", action="store_true")

    scandir_parser = subparsers.add_parser("scandir")
    scandir_parser.add_argument("--path", required=True)

    scandir_with_samples_parser = subparsers.add_parser("scandir-with-samples")
    scandir_with_samples_parser.add_argument("--path", required=True)
    scandir_with_samples_parser.add_argument(
        "--sample-size",
        type=int,
        default=4096,
    )

    stat_parser = subparsers.add_parser("stat")
    stat_parser.add_argument("--path", required=True)

    stat_with_sample_parser = subparsers.add_parser("stat-with-sample")
    stat_with_sample_parser.add_argument("--path", required=True)
    stat_with_sample_parser.add_argument(
        "--sample-size",
        type=int,
        default=4096,
    )

    stat_mtime_parser = subparsers.add_parser("stat-mtime")
    stat_mtime_parser.add_argument("--path", required=True)

    delete_parser = subparsers.add_parser("delete-path")
    delete_parser.add_argument("--path", required=True)

    args = parser.parse_args()

    try:
        if args.operation == "read-file":
            _handle_read_file(args.path)
        elif args.operation == "write-file":
            _handle_write_file(args.path, args.data_b64)
        elif args.operation == "ensure-dir":
            _handle_ensure_dir(args.path)
        elif args.operation == "exists":
            _handle_exists(args.path)
        elif args.operation == "is-dir":
            _handle_is_dir(args.path)
        elif args.operation == "list-dir":
            _handle_list_dir(args.path, args.recursive)
        elif args.operation == "scandir":
            _handle_scandir(args.path)
        elif args.operation == "scandir-with-samples":
            _handle_scandir_with_samples(args.path, args.sample_size)
        elif args.operation == "stat":
            _handle_stat(args.path)
        elif args.operation == "stat-with-sample":
            _handle_stat_with_sample(args.path, args.sample_size)
        elif args.operation == "stat-mtime":
            _handle_stat_mtime(args.path)
        elif args.operation == "delete-path":
            _handle_delete_path(args.path)
        else:
            raise ValueError(f"Unsupported operation: {args.operation}")
    except Exception as exc:  # noqa: BLE001
        _write_structured_error(exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
