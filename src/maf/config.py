"""
Configuration specific to the Agent Framework implementation.
================================================================================

`AZURE_OPENAI_*` lives here rather than in `src/common/config.py` because the
Foundry implementation does not need it: there the model is addressed through
the project endpoint. Keeping them apart means you can run either
implementation without configuring the other.

The shared settings - database, MCP server, which prompt - come from
`src/common/config.py`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.common.config import (
    ConfigError,
    McpConfig,
    PostgresConfig,
    load_env,
    load_instructions_file,
    load_mcp_config,
    load_postgres_config,
    require,
)


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
    endpoint = require(
        "AZURE_OPENAI_ENDPOINT",
        "Find it in the Azure portal under your Azure OpenAI / Foundry resource "
        "-> Keys and Endpoint. Use the https://<name>.openai.azure.com/ form.",
    )

    # A very common mistake: pasting the Foundry project endpoint
    # (https://<name>.services.ai.azure.com/) instead of the Azure OpenAI one.
    # `src/foundry/config.py` has the mirror-image check.
    if "services.ai.azure.com" in endpoint:
        raise ConfigError(
            f"AZURE_OPENAI_ENDPOINT looks like a Foundry *project* endpoint:\n"
            f"  {endpoint}\n\n"
            "This implementation uses the Azure OpenAI API surface, so it needs\n"
            "  https://<resource-name>.openai.azure.com/\n"
            "instead. Both endpoints belong to the same resource.\n\n"
            "If you meant to use the Foundry Agent Service implementation, that\n"
            "is `python -m src.foundry.main` - see docs/implementations.md."
        )

    deployment = require(
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


@dataclass(frozen=True)
class AppConfig:
    """All configuration the Agent Framework implementation needs."""

    azure_openai: AzureOpenAIConfig
    postgres: PostgresConfig
    mcp: McpConfig

    instructions_file: Path | None
    """Optional path to a file holding the agent's system prompt.

    Set `AGENT_INSTRUCTIONS_FILE` in `.env` to point the agent at your own
    database instead of the FiberOps sample. See docs/bring-your-own.md.
    When None, the built-in FiberOps prompt is used.
    """


def load_config() -> AppConfig:
    """Load `.env` and validate every setting this implementation needs.

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
