"""Koncile MCP Server — Streamable HTTP transport for hosted deployment."""

from __future__ import annotations

import contextvars
import os
from typing import Any

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from koncile_mcp.client import KoncileClient
from koncile_mcp.config import DEFAULT_API_URL, Config
from koncile_mcp.server import TOOLS, handle_tool

# Per-request API key, set by auth middleware before tool handlers run.
_current_api_key: contextvars.ContextVar[str] = contextvars.ContextVar("_current_api_key")


def _config_from_bearer(api_key: str) -> Config:
    """Build a Config using the caller's Bearer token as the API key."""
    api_url = os.environ.get("KONCILE_API_URL", DEFAULT_API_URL).rstrip("/")
    timeout = float(os.environ.get("KONCILE_REQUEST_TIMEOUT", "120"))
    return Config(api_url=api_url, api_key=api_key, request_timeout=timeout)


class _AuthenticatedMCPEndpoint:
    """ASGI app that extracts Bearer token and delegates to the session manager."""

    def __init__(self, session_manager: StreamableHTTPSessionManager) -> None:
        self._session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive, send)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            response = Response("Missing or invalid Authorization header", status_code=401)
            await response(scope, receive, send)
            return

        token = auth[7:].strip()
        if not token:
            response = Response("Empty Bearer token", status_code=401)
            await response(scope, receive, send)
            return

        _current_api_key.set(token)
        await self._session_manager.handle_request(scope, receive, send)


def create_http_app() -> Starlette:
    """Create a Starlette ASGI app serving the MCP protocol over Streamable HTTP."""

    server = Server("koncile")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        api_key = _current_api_key.get()
        config = _config_from_bearer(api_key)
        client = KoncileClient(config)
        try:
            return await handle_tool(client, name, arguments or {})
        finally:
            await client.close()

    session_manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=False,
    )

    mcp_endpoint = _AuthenticatedMCPEndpoint(session_manager)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app = Starlette(
        routes=[
            Route("/health", endpoint=health),
            Route("/mcp", endpoint=mcp_endpoint),
            Mount("/mcp", app=mcp_endpoint),
        ],
        lifespan=lambda app: session_manager.run(),
    )

    return app


def main() -> None:
    """Entry point — run the MCP server over Streamable HTTP."""
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))

    app = create_http_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
