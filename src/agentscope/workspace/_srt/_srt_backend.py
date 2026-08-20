# -*- coding: utf-8 -*-
"""HTTP-backed BackendBase implementation for SRT workspaces."""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from ...tool import BackendBase, DirEntry, ExecResult


def _decode_bytes(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


class SRTBackend(BackendBase):
    """Backend that forwards builtin tool operations to the local runtime."""

    _path_module = os.path

    def __init__(
        self,
        base_url: str,
        token: str,
        workdir: str,
        timeout: float | None = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._workdir = workdir
        self._timeout = timeout
        self._http = httpx.AsyncClient(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def aclose(self) -> None:
        """Close the shared HTTP client."""
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected_404: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        resp = await self._http.request(
            method,
            self._url(path),
            headers=self._headers(),
            **kwargs,
        )
        if expected_404 and resp.status_code == 404:
            raise FileNotFoundError(_detail(resp))
        if resp.status_code == 400:
            detail = _detail(resp)
            lowered = detail.lower()
            if "not a directory" in lowered:
                raise NotADirectoryError(detail)
            if "is a directory" in lowered:
                raise IsADirectoryError(detail)
            raise ValueError(detail)
        if resp.status_code == 403:
            raise PermissionError(_detail(resp))
        resp.raise_for_status()
        return resp

    async def getcwd(self) -> str:
        return self._workdir

    async def expanduser(self, path: str) -> str:
        return os.path.expanduser(path)

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        resp = await self._request(
            "POST",
            "/backend/exec",
            json={
                "command": command,
                "cwd": cwd,
                "timeout": timeout,
            },
        )
        payload = resp.json()
        return ExecResult(
            exit_code=int(payload["exit_code"]),
            stdout=_decode_bytes(payload["stdout"]),
            stderr=_decode_bytes(payload["stderr"]),
        )

    async def read_file(self, path: str) -> bytes:
        resp = await self._request(
            "GET",
            "/backend/file",
            params={"path": path},
            expected_404=True,
        )
        return resp.content

    async def write_file(self, path: str, data: bytes) -> None:
        await self._request(
            "PUT",
            "/backend/file",
            params={"path": path},
            content=data,
        )

    async def ensure_dir(self, path: str) -> None:
        await self._request("POST", "/backend/dir", json={"path": path})

    async def file_exists(self, path: str) -> bool:
        resp = await self._request("GET", "/backend/exists", params={"path": path})
        return bool(resp.json()["exists"])

    async def is_dir(self, path: str) -> bool:
        resp = await self._request("GET", "/backend/is-dir", params={"path": path})
        return bool(resp.json()["is_dir"])

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        resp = await self._request(
            "GET",
            "/backend/list-dir",
            params={"path": path, "recursive": recursive},
            expected_404=True,
        )
        return list(resp.json()["entries"])

    async def scandir(self, path: str) -> list[DirEntry]:
        resp = await self._request(
            "GET",
            "/backend/scandir",
            params={"path": path},
            expected_404=True,
        )
        return [DirEntry(**entry) for entry in resp.json()["entries"]]

    async def stat(self, path: str) -> DirEntry | None:
        resp = await self._request("GET", "/backend/stat", params={"path": path})
        payload = resp.json()
        return None if payload is None else DirEntry(**payload)

    async def stat_mtime(self, path: str) -> float | None:
        resp = await self._request(
            "GET",
            "/backend/stat-mtime",
            params={"path": path},
        )
        return resp.json()["mtime"]

    async def delete_path(self, path: str) -> None:
        await self._request("DELETE", "/backend/path", params={"path": path})

    async def read_stream(
        self,
        path: str,
        chunk_size: int = 1024 * 1024,
    ):
        async with self._http.stream(
            "GET",
            self._url("/backend/file"),
            headers=self._headers(),
            params={"path": path},
        ) as resp:
            if resp.status_code == 404:
                raise FileNotFoundError(_detail(resp))
            if resp.status_code == 403:
                raise PermissionError(_detail(resp))
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size):
                yield chunk


def _detail(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        return resp.text or f"HTTP {resp.status_code}"
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return str(payload)
