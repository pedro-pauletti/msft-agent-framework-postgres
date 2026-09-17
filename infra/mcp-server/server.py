"""
The Postgres MCP server, over Streamable HTTP, behind a shared secret.
================================================================================

Why this file exists at all
---------------------------

A Foundry prompt agent runs inside the Agent Service, so an MCP tool has to be a
**remote HTTPS endpoint**. `postgres-mcp` can serve one - but two things are
missing from the published artifacts:

1. **Streamable HTTP is unreleased.** `--transport=streamable-http` was merged
   after v0.3.0 (May 2025), so `crystaldba/postgres-mcp:latest` speaks only
   stdio and the legacy SSE transport, which the Agent Service does not accept.
   The Dockerfile next to this file therefore builds from a pinned commit.

2. **There is no authentication.** None. An endpoint that executes arbitrary SQL
   cannot sit on the public internet unprotected, so this module wraps the MCP
   app in a bearer-token check.

Everything else is `postgres-mcp` untouched: the same nine tools, the same
access modes, the same SQL safety logic.

A note on the token
-------------------

A shared secret is the simplest thing that is not negligent, and it is what this
sample uses. The better answer on Azure is to put Entra ID in front (Container
Apps built-in auth) and let the Foundry project's managed identity call it, so
there is no secret to leak or rotate. See docs/implementations.md.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import sys

import postgres_mcp.server as postgres_mcp
import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse

LOG = logging.getLogger("postgres-mcp-http")

HEALTH_PATH = "/healthz"


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Reject anything that does not present the shared secret."""

    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._expected = f"Bearer {token}".encode()

    async def dispatch(self, request, call_next):
        # Container Apps probes this before the app is considered healthy, and
        # it must answer without credentials.
        if request.url.path == HEALTH_PATH:
            return PlainTextResponse("ok")

        supplied = request.headers.get("authorization", "").encode()
        if not secrets.compare_digest(supplied, self._expected):
            LOG.warning("Rejected unauthenticated request to %s", request.url.path)
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        return await call_next(request)


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Missing required environment variable: {name}")
    return value


def _register_execute_sql(access_mode: postgres_mcp.AccessMode) -> None:
    """Add the tool that `postgres-mcp` registers at start-up, not at import.

    Its description and annotations depend on the access mode, which is why the
    upstream package does this in `main()` rather than with a decorator. We are
    not going through `main()`, so we have to do it here - copied from upstream
    so the model sees exactly the same tool it would otherwise.
    """
    if access_mode is postgres_mcp.AccessMode.UNRESTRICTED:
        postgres_mcp.mcp.add_tool(
            postgres_mcp.execute_sql,
            description="Execute any SQL query",
            annotations=ToolAnnotations(title="Execute SQL", destructiveHint=True),
        )
    else:
        postgres_mcp.mcp.add_tool(
            postgres_mcp.execute_sql,
            description="Execute a read-only SQL query",
            annotations=ToolAnnotations(title="Execute SQL (Read-Only)", readOnlyHint=True),
        )


def build_app(database_uri: str, token: str, access_mode: postgres_mcp.AccessMode):
    postgres_mcp.current_access_mode = access_mode
    _register_execute_sql(access_mode)

    # The MCP SDK guards against DNS rebinding by rejecting any Host header it
    # does not recognise, and out of the box it only recognises localhost. Behind
    # Container Apps ingress the Host is the public FQDN, so every request comes
    # back as `421 Misdirected Request` with `Invalid Host header` in the log
    # until that hostname is allowed here.
    allowed_host = os.environ.get("ALLOWED_HOST", "").strip()
    postgres_mcp.mcp.settings.transport_security = TransportSecuritySettings(
        allowed_hosts=[allowed_host] if allowed_host else ["*"],
        allowed_origins=[f"https://{allowed_host}"] if allowed_host else ["*"],
    )

    app = postgres_mcp.mcp.streamable_http_app()

    # FastMCP owns the app's lifespan - it runs the MCP session manager there.
    # Wrap it rather than replace it, so the database pool opens and closes
    # around it instead of fighting with it.
    mcp_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(scoped_app):
        await postgres_mcp.db_connection.pool_connect(database_uri)
        LOG.info("Connected to PostgreSQL. Serving MCP in %s mode.", access_mode.value)
        try:
            async with mcp_lifespan(scoped_app):
                yield
        finally:
            await postgres_mcp.db_connection.close()

    app.router.lifespan_context = lifespan
    app.add_middleware(BearerTokenMiddleware, token=token)
    return app


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    database_uri = _require("DATABASE_URI")
    token = _require("MCP_AUTH_TOKEN")
    access_mode = postgres_mcp.AccessMode(os.environ.get("ACCESS_MODE", "restricted").strip())
    port = int(os.environ.get("PORT", "8000"))

    app = build_app(database_uri, token, access_mode)

    # Binding to all interfaces is the point: this runs as a container whose
    # only reachable surface is the Container Apps ingress.
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
