"""
Configuration shared by both implementations.
================================================================================

Everything the sample needs comes from environment variables, which are loaded
from a `.env` file at the repository root. `infra/provision.ps1` / `.sh` writes
that file for you; `.env.example` documents every key.

This module holds only the settings **both** implementations need: the database,
the Postgres MCP server, and which system prompt to use. The bits that are
specific to one runtime live next to it:

    src/maf/config.py       AZURE_OPENAI_* - the Agent Framework path
    src/foundry/config.py   FOUNDRY_*      - the Agent Service path

That split is deliberate. It means you can run either implementation without
configuring the other one.

Its other job is to fail *loudly and helpfully* when something is missing.
Nothing here is framework specific - it is plain Python.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

# The repository root, i.e. the folder that contains `.env`, `src/` and `infra/`.
# `__file__` is `<root>/src/common/config.py`, so three `.parent` hops get us there.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class ConfigError(RuntimeError):
    """Raised when the environment is not set up correctly.

    We use a dedicated exception type so the entry points can catch it and
    print a friendly message instead of a stack trace.
    """


def load_env() -> None:
    """Load `<repo root>/.env` into `os.environ`.

    `override=False` (the default) means variables already present in your shell
    win over the ones in `.env`. That is usually what you want, but see the
    warning about `OPENAI_API_KEY` in `_validate_no_openai_key_hijack()` below.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        raise ConfigError(
            f"No .env file found at {env_path}.\n\n"
            "Fix it with either:\n"
            "  1. Run the provisioning script, which creates Azure resources "
            "and writes .env for you:\n"
            "       pwsh infra/provision.ps1      (Windows)\n"
            "       bash infra/provision.sh       (macOS / Linux)\n"
            "  2. Or copy the template and fill it in by hand:\n"
            "       cp .env.example .env"
        )
    load_dotenv(env_path)


def require(name: str, hint: str = "") -> str:
    """Read a required environment variable or raise a helpful ConfigError.

    Public because `src/maf/config.py` and `src/foundry/config.py` build on it.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        suffix = f"\n  Hint: {hint}" if hint else ""
        raise ConfigError(f"Required environment variable {name} is not set.{suffix}")
    return value


# =============================================================================
# PostgreSQL
# =============================================================================


@dataclass(frozen=True)
class PostgresConfig:
    """Connection details for the Azure Database for PostgreSQL server.

    Only `connection_uri` ever leaves this module - it is handed to the Postgres
    MCP server through an environment variable. Our Python process never opens a
    database connection itself (except in `infra/seed.py`).
    """

    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str

    @property
    def connection_uri(self) -> str:
        """A libpq-style URI: postgresql://user:password@host:port/db?sslmode=...

        The username and password are percent-encoded because Azure admin names
        and generated passwords can legitimately contain characters (`@`, `/`,
        `:`, `+`) that would otherwise break URI parsing.
        """
        user = quote(self.user, safe="")
        password = quote(self.password, safe="")
        return (
            f"postgresql://{user}:{password}@{self.host}:{self.port}"
            f"/{self.database}?sslmode={self.sslmode}"
        )

    @property
    def safe_display_uri(self) -> str:
        """The same URI with the password masked - safe to print to a terminal."""
        return self.connection_uri.replace(quote(self.password, safe=""), "***")


def load_postgres_config() -> PostgresConfig:
    host = require(
        "PGHOST",
        "Something like my-server.postgres.database.azure.com. "
        "Run infra/provision.ps1 (or .sh) if you have not created the server yet.",
    )

    # sslmode=require is not optional on Azure: the service rejects plaintext
    # connections outright, and the resulting error message is famously unclear.
    sslmode = os.environ.get("PGSSLMODE", "require").strip() or "require"
    if host.endswith(".postgres.database.azure.com") and sslmode == "disable":
        raise ConfigError(
            "PGSSLMODE=disable will not work against Azure Database for "
            "PostgreSQL - the service requires TLS. Use PGSSLMODE=require."
        )

    return PostgresConfig(
        host=host,
        port=int(os.environ.get("PGPORT", "5432").strip() or "5432"),
        database=require("PGDATABASE", "The database name, e.g. 'fiberops'."),
        user=require("PGUSER", "The administrator login, e.g. 'fiberadmin'."),
        password=require(
            "PGPASSWORD",
            "The administrator password you chose when creating the server. "
            "It is written to .env by the provisioning script.",
        ),
        sslmode=sslmode,
    )


# =============================================================================
# Postgres MCP server behaviour
# =============================================================================


@dataclass(frozen=True)
class McpConfig:
    """How the Postgres MCP server and its tools should behave."""

    access_mode: str
    """'unrestricted' -> the agent may write and change schema.
    'restricted'   -> read-only transactions with resource limits.
    """

    approval_mode: str
    """'never_require'  -> tool calls execute immediately.
    'always_require' -> every tool call must be approved by a human first.
    """


def load_mcp_config() -> McpConfig:
    access_mode = os.environ.get("POSTGRES_MCP_ACCESS_MODE", "unrestricted").strip()
    if access_mode not in {"unrestricted", "restricted"}:
        raise ConfigError(
            f"POSTGRES_MCP_ACCESS_MODE must be 'unrestricted' or 'restricted', "
            f"got {access_mode!r}."
        )

    approval_mode = os.environ.get("POSTGRES_MCP_APPROVAL_MODE", "never_require").strip()
    if approval_mode not in {"never_require", "always_require"}:
        raise ConfigError(
            f"POSTGRES_MCP_APPROVAL_MODE must be 'never_require' or "
            f"'always_require', got {approval_mode!r}."
        )

    return McpConfig(access_mode=access_mode, approval_mode=approval_mode)


# =============================================================================
# Which system prompt to use
# =============================================================================


def load_instructions_file() -> Path | None:
    """Resolve `AGENT_INSTRUCTIONS_FILE`, if the user set one.

    Relative paths are resolved against the repository root, so
    `AGENT_INSTRUCTIONS_FILE=prompts/my-database.md` works regardless of the
    directory you run Python from.

    Both implementations honour this, which is what lets you point either of
    them at your own database without touching any code.
    """
    raw = os.environ.get("AGENT_INSTRUCTIONS_FILE", "").strip()
    if not raw:
        return None

    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path

    if not path.exists():
        raise ConfigError(
            f"AGENT_INSTRUCTIONS_FILE points to a file that does not exist:\n"
            f"  {path}\n\n"
            "Either create it, fix the path, or remove the variable from .env "
            "to fall back to the built-in FiberOps prompt.\n"
            "Ready-made starting points live in the prompts/ folder."
        )

    if not path.read_text(encoding="utf-8").strip():
        raise ConfigError(f"AGENT_INSTRUCTIONS_FILE is empty: {path}")

    return path
