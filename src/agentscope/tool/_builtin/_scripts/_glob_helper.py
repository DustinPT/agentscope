# -*- coding: utf-8 -*-
"""Helper script for the builtin Glob tool."""

from __future__ import annotations

import fnmatch
import json
import os
import sys


def _collect_matches(base_dir: str, pattern: str) -> list[tuple[float, str]]:
    matches: list[tuple[float, str]] = []
    for root, _dirs, files in os.walk(base_dir):
        for name in files:
            full_path = os.path.join(root, name)
            rel_path = os.path.relpath(full_path, base_dir)
            rel_path_posix = rel_path.replace(os.sep, "/")
            if fnmatch.fnmatch(rel_path_posix, pattern) or fnmatch.fnmatch(
                full_path.replace(os.sep, "/"),
                pattern,
            ):
                try:
                    mtime = os.path.getmtime(full_path)
                except OSError:
                    mtime = 0.0
                matches.append((mtime, full_path))
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches


def main() -> int:
    if len(sys.argv) != 3:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "usage: _glob_helper.py <base_dir> <pattern>",
                },
            ),
        )
        return 1

    base_dir, pattern = sys.argv[1], sys.argv[2]
    if not os.path.isdir(base_dir):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"base directory does not exist: {base_dir}",
                },
            ),
        )
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "matches": [path for _mtime, path in _collect_matches(base_dir, pattern)],
            },
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
