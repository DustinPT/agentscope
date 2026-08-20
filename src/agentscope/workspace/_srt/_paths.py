# -*- coding: utf-8 -*-
"""Shared Python runtime path helpers for SRT workspaces."""

from __future__ import annotations

import importlib
import os
import sys


def active_virtualenv_dir() -> str | None:
    """Return the active virtualenv root when running inside a virtualenv."""
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        return os.path.abspath(venv)

    base_prefix = os.path.abspath(getattr(sys, "base_prefix", sys.prefix))
    prefix = os.path.abspath(sys.prefix)
    if prefix != base_prefix:
        return prefix

    real_prefix = getattr(sys, "real_prefix", None)
    if real_prefix:
        return os.path.abspath(sys.prefix)

    return None


def runtime_module_search_paths(module_name: str) -> list[str]:
    """Return key filesystem paths needed to import one runtime module."""
    module = importlib.import_module(module_name)
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return []

    root_package_name = module_name.split(".", 1)[0]
    root_package = importlib.import_module(root_package_name)
    package_file = getattr(root_package, "__file__", None)
    if package_file:
        package_dir = os.path.dirname(os.path.abspath(package_file))
        return [os.path.dirname(package_dir)]

    return [os.path.dirname(os.path.abspath(module_file))]
