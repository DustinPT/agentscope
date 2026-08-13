# -*- coding: utf-8 -*-
"""Filesystem helpers shared across agentscope modules."""

from __future__ import annotations

import hashlib
import os
from os import PathLike


def _hash_directory(
    dir_path: str | PathLike[str],
    *,
    digest_bytes: bool = False,
) -> str | bytes:
    """Compute a stable hash for one directory tree.

    The hash includes both relative file paths and file contents, using a
    deterministic traversal order.
    """
    root_dir = os.fspath(dir_path)
    digest = hashlib.sha256()
    for root, dirs, files in os.walk(root_dir):
        dirs.sort()
        files.sort()
        for file_name in files:
            abs_path = os.path.join(root, file_name)
            rel_path = os.path.relpath(abs_path, root_dir).replace(
                os.sep,
                "/",
            )
            digest.update(rel_path.encode("utf-8"))
            digest.update(b"\0")
            with open(abs_path, "rb") as file_obj:
                while True:
                    chunk = file_obj.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
    if digest_bytes:
        return digest.digest()
    return digest.hexdigest()
