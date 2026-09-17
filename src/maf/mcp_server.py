"""
Launching the Postgres MCP server.
================================================================================

Only this implementation starts the server itself:

    uvx --with "mcp<2" postgres-mcp --access-mode=<mode>

The Foundry implementation does not - there the server is a container the Agent
Service calls over HTTPS, so none of this applies to it. The two details below
are easy to get wrong, which is why they are here rather than inline.
"""

from __future__ import annotations

import os
import shutil

from src.common.config import ConfigError

# The `uv` and network settings `uvx` itself reads. They matter because the MCP
# client does not pass your whole environment to the child process: the MCP SDK
# forwards a small allow-list (PATH, HOME, TEMP, ...) plus whatever you put in
# `env=`. That is a sensible default, but it also strips these - so on a
# corporate network where `uv` needs a proxy, a private index, a custom CA or
# its offline cache, the server dies with a bare "Connection closed" and no
# explanation.
_UVX_ENV_PREFIX = "UV_"
_UVX_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
)


def ensure_uvx_available() -> None:
    """Fail with installation instructions rather than a cryptic MCP error."""
    if shutil.which("uvx") is not None:
        return

    raise ConfigError(
        "`uvx` was not found on your PATH.\n\n"
        "It ships with `uv`, the Python package manager used to run the\n"
        "Postgres MCP server in an isolated environment. Install it with:\n\n"
        '  Windows:        powershell -c "irm https://astral.sh/uv/install.ps1 | iex"\n'
        "  macOS / Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh\n\n"
        "Then restart your terminal so PATH picks it up."
    )


def uvx_environment() -> dict[str, str]:
    """Forward the `uv` and proxy settings the MCP client would otherwise drop."""
    return {
        key: value
        for key, value in os.environ.items()
        if key.startswith(_UVX_ENV_PREFIX) or key.upper() in _UVX_ENV_NAMES
    }


def explain_startup_failure(exc: Exception) -> str:
    """Turn `Connection closed` into something you can act on.

    The MCP client cannot see why the child died - it only notices that the pipe
    closed - so every cause below produces the same message.
    """
    offline = "set" if os.environ.get("UV_OFFLINE") else "not set"

    return (
        f"The Postgres MCP server did not start.\n\n"
        f"  {type(exc).__name__}: {exc}\n\n"
        "The MCP client only sees that the process exited, so the real cause is\n"
        "one of these:\n\n"
        "  1. `uvx` could not reach PyPI. On a corporate network that usually\n"
        "     means TLS inspection breaking the handshake. If the package is\n"
        "     already in uv's cache, skip the network by adding this to .env:\n\n"
        "         UV_OFFLINE=1\n\n"
        f"     (UV_OFFLINE is currently {offline})\n\n"
        "  2. The `--with \"mcp<2\"` pin is missing. `postgres-mcp` is built\n"
        "     against v1 of the MCP SDK and crashes on import without it.\n\n"
        "  3. `uvx` is on PATH but broken. Check with:\n\n"
        '         uvx --with "mcp<2" postgres-mcp --help\n\n'
        "     Running it by hand is the fastest way to see the real error.\n\n"
        "See docs/troubleshooting.md for the full list."
    )
