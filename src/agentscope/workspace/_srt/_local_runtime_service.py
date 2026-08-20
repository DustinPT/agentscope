# -*- coding: utf-8 -*-
"""Local SRT runtime service.

Runs inside an ``srt``-sandboxed process tree and exposes:

- backend endpoints for builtin tool filesystem / shell operations
- MCP gateway endpoints reused from ``_mcp_gateway_app``
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from ...tool._builtin._backend import LocalBackend
from .._mcp_gateway._mcp_gateway_app import _State, _build_app, _make_auth_dep


class _ExecRequest(dict):
    """Typed marker for shell execution request payloads."""


def _encode_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _entry_to_dict(entry: Any) -> dict[str, Any]:
    return asdict(entry)


def _build_runtime_app(state: _State) -> FastAPI:
    """Build the combined backend + MCP runtime app."""
    app = _build_app(state)
    auth = Depends(_make_auth_dep(state))
    backend = LocalBackend()

    @app.post("/backend/exec", dependencies=[auth])
    async def _exec(request: Request) -> dict[str, Any]:
        body: _ExecRequest = await request.json()
        command = body.get("command")
        if not isinstance(command, list) or any(
            not isinstance(part, str) for part in command
        ):
            raise HTTPException(status_code=400, detail="command must be list[str]")
        result = await backend.exec_shell(
            command,
            cwd=body.get("cwd"),
            timeout=body.get("timeout"),
        )
        return {
            "exit_code": result.exit_code,
            "stdout": _encode_bytes(result.stdout),
            "stderr": _encode_bytes(result.stderr),
        }

    @app.get("/backend/file", dependencies=[auth])
    async def _read_file(path: str) -> Response:
        try:
            data = await backend.read_file(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(content=data, media_type="application/octet-stream")

    @app.put("/backend/file", dependencies=[auth])
    async def _write_file(path: str, request: Request) -> dict[str, bool]:
        await backend.write_file(path, await request.body())
        return {"ok": True}

    @app.post("/backend/dir", dependencies=[auth])
    async def _ensure_dir(request: Request) -> dict[str, bool]:
        body = await request.json()
        path = body.get("path")
        if not isinstance(path, str) or not path:
            raise HTTPException(status_code=400, detail="path is required")
        await backend.ensure_dir(path)
        return {"ok": True}

    @app.get("/backend/exists", dependencies=[auth])
    async def _file_exists(path: str) -> dict[str, bool]:
        return {"exists": await backend.file_exists(path)}

    @app.get("/backend/is-dir", dependencies=[auth])
    async def _is_dir(path: str) -> dict[str, bool]:
        return {"is_dir": await backend.is_dir(path)}

    @app.get("/backend/list-dir", dependencies=[auth])
    async def _list_dir(path: str, recursive: bool = False) -> dict[str, list[str]]:
        try:
            entries = await backend.list_dir(path, recursive=recursive)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"entries": entries}

    @app.get("/backend/scandir", dependencies=[auth])
    async def _scandir(path: str) -> dict[str, list[dict[str, Any]]]:
        try:
            entries = await backend.scandir(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"entries": [_entry_to_dict(entry) for entry in entries]}

    @app.get("/backend/stat", dependencies=[auth])
    async def _stat(path: str) -> dict[str, Any] | None:
        entry = await backend.stat(path)
        return None if entry is None else _entry_to_dict(entry)

    @app.get("/backend/stat-mtime", dependencies=[auth])
    async def _stat_mtime(path: str) -> dict[str, float | None]:
        return {"mtime": await backend.stat_mtime(path)}

    @app.delete("/backend/path", dependencies=[auth])
    async def _delete_path(path: str) -> dict[str, bool]:
        await backend.delete_path(path)
        return {"ok": True}

    return app


async def _serve(host: str, port: int, token: str) -> None:
    """Run the combined runtime service until process exit."""
    state = _State()
    state.token = token
    app = _build_runtime_app(state)

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
        ),
    )
    try:
        await server.serve()
    finally:
        for clients in list(state.clients.values()):
            for client in list(clients.values()):
                if client.is_stateful and client.is_connected:
                    await client.close()


def main() -> None:
    """CLI entrypoint for the sandboxed local runtime service."""
    parser = argparse.ArgumentParser(
        description="AgentScope local runtime service for SRT workspaces",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    asyncio.run(_serve(args.host, args.port, args.token))


if __name__ == "__main__":
    main()
