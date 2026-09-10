"""
Configuration loading and validation.
================================================================================

Everything the sample needs comes from environment variables, which are loaded
from a `.env` file at the repository root. `infra/provision.ps1` / `.sh` writes
that file for you; `.env.example` documents every key.

This module has one job: turn those loose environment variables into two small,
validated objects (`AzureOpenAIConfig` and `PostgresConfig`) and fail *loudly
and helpfully* when something is missing. Nothing here is Agent Framework
specific - it is plain Python, kept separate so `agent.py` stays focused on the
interesting part.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

# The repository root, i.e. the folder that contains `.env`, `src/` and `infra/`.
# `__file__` is `<root>/src/config.py`, so two `.parent` hops get us there.
REPO_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Raised when the environment is not set up correctly.

    We use a dedicated exception type so `main.py` can catch it and print a
    friendly message instead of a stack trace.
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


def _require(name: str, hint: str = "") -> str:
    """Read a required environment variable or raise a helpful ConfigError."""
    value = os.environ.get(name, "").strip()
    if not value:
        suffix = f"\n  Hint: {hint}" if hint else ""
        raise ConfigError(f"Required environment variable {name} is not set.{suffix}")
    return value


# =============================================================================
# Azure OpenAI
# =============================================================================


@dataclass(frozen=True)
class AzureOpenAIConfig:
    """Everything needed to build an `OpenAIChatClient` pointed at Azure."""

    endpoint: str
    """e.g. https://my-resource.openai.azure.com/"""

    deployment: str
    """The *deployment* name from the Foundry portal, e.g. 'gpt-4.1'.

    Note: Agent Framework's `OpenAIChatClient` calls this parameter `model`.
    On Azure OpenAI, the "model" you pass on the wire is the deployment name,
    not the underlying model id. This trips up almost everyone once.
    """

    api_version: str | None
    """Usually best left as None.

    Agent Framework's `OpenAIChatClient` talks to the Azure OpenAI **Responses**
    API, which older `api-version` values do not support - pinning something
    like `2024-10-21` here fails with `400 BadRequest: API version not
    supported`. Leave `AZURE_OPENAI_API_VERSION` empty and let the SDK pick a
    compatible default.
    """

    api_key: str | None
    """When None (recommended), we authenticate with Entra ID via `az login`."""

    @property
    def uses_entra_id(self) -> bool:
        return self.api_key is None


def load_azure_openai_config() -> AzureOpenAIConfig:
    endpoint = _require(
        "AZURE_OPENAI_ENDPOINT",
        "Find it in the Azure portal under your Azure OpenAI / Foundry resource "
        "-> Keys and Endpoint. Use the https://<name>.openai.azure.com/ form.",
    )

    # A very common mistake: pasting the Foundry project endpoint
    # (https://<name>.services.ai.azure.com/) instead of the Azure OpenAI one.
    if "services.ai.azure.com" in endpoint:
        raise ConfigError(
            f"AZURE_OPENAI_ENDPOINT looks like a Foundry *project* endpoint:\n"
            f"  {endpoint}\n\n"
            "This sample uses the Azure OpenAI API surface, so it needs the\n"
            "  https://<resource-name>.openai.azure.com/\n"
            "form instead. Both endpoints belong to the same resource."
        )

    deployment = _require(
        "AZURE_OPENAI_DEPLOYMENT",
        "This is the deployment name shown in the Foundry portal under "
        "'Deployments', not the model name. It must be a model that supports "
        "tool calling (gpt-4.1, gpt-4.1-mini, gpt-4o, ...).",
    )

    # An empty AZURE_OPENAI_API_KEY means "use Entra ID", which is the path we
    # recommend, so we normalise empty string to None here.
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip() or None

    return AzureOpenAIConfig(
        endpoint=endpoint.rstrip("/") + "/",
        deployment=deployment,
        # Empty / unset means "let the SDK choose", which is what we recommend.
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "").strip() or None,
        api_key=api_key,
    )


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
    host = _require(
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
        database=_require("PGDATABASE", "The database name, e.g. 'fiberops'."),
        user=_require("PGUSER", "The administrator login, e.g. 'fiberadmin'."),
        password=_require(
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
# Convenience
# =============================================================================


@dataclass(frozen=True)
class AppConfig:
    """All configuration, loaded and validated in one shot."""

    azure_openai: AzureOpenAIConfig
    postgres: PostgresConfig
    mcp: McpConfig

    instructions_file: Path | None
    """Optional path to a file holding the agent's system prompt.

    Set `AGENT_INSTRUCTIONS_FILE` in `.env` to point the agent at your own
    database instead of the FiberOps sample. See docs/bring-your-own.md.
    When None, the built-in FiberOps prompt is used.
    """


def load_instructions_file() -> Path | None:
    """Resolve `AGENT_INSTRUCTIONS_FILE`, if the user set one.

    Relative paths are resolved against the repository root, so
    `AGENT_INSTRUCTIONS_FILE=prompts/my-database.md` works regardless of the
    directory you run Python from.
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


def load_config() -> AppConfig:
    """Load `.env` and validate every setting the sample needs.

    Call this once at the top of any entry point.
    """
    load_env()
    _warn_about_openai_api_key_hijack()
    return AppConfig(
        azure_openai=load_azure_openai_config(),
        postgres=load_postgres_config(),
        mcp=load_mcp_config(),
        instructions_file=load_instructions_file(),
    )


def _warn_about_openai_api_key_hijack() -> None:
    """Guard against a genuinely confusing Agent Framework behaviour.

    `OpenAIChatClient` resolves its configuration in this order:

        1. Explicit Azure inputs (`credential=` or `azure_endpoint=`)
        2. `OPENAI_API_KEY`
        3. Azure environment fallback (`AZURE_OPENAI_ENDPOINT`, ...)

    We always pass explicit Azure inputs in `agent.py`, so we are safe. But if
    you copy that code into a project that does not, and you happen to have
    `OPENAI_API_KEY` exported, your calls will silently go to public OpenAI
    instead of Azure. Worth a heads-up.
    """
    if os.environ.get("OPENAI_API_KEY"):
        print(
            "[note] OPENAI_API_KEY is set in your environment. This sample "
            "always passes an explicit Azure endpoint, so Azure OpenAI will "
            "still be used - but be aware that Agent Framework would otherwise "
            "prefer OPENAI_API_KEY and route to public OpenAI."
        )
