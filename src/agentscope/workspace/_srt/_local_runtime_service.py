# -*- coding: utf-8 -*-
"""Local SRT runtime service.

Runs inside an ``srt``-sandboxed process tree and exposes MCP gateway
endpoints reused from ``_mcp_gateway_app``.
"""

from __future__ import annotations

import argparse
import asyncio

from fastapi import FastAPI

from .._mcp_gateway._mcp_gateway_app import _State, _build_app


def _build_runtime_app(state: _State) -> FastAPI:
    """Build the MCP-only runtime app."""
    return _build_app(state)


async def _serve(
    host: str,
    port: int,
    token: str,
) -> None:
    """Run the MCP runtime service until process exit."""
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
    asyncio.run(
        _serve(
            args.host,
            args.port,
            args.token,
        ),
    )


if __name__ == "__main__":
    main()
