"""
Configuration specific to the Foundry Agent Service implementation.
================================================================================

Note what is *not* here: no database credentials.

That is the point of this implementation. The agent reaches PostgreSQL through
an MCP server that the Agent Service calls directly, so the connection string
lives in that container - not on your machine, and not in this process. The
Agent Framework implementation in `src/maf/` is the opposite: it starts the MCP
server locally and therefore needs `PG*`.

The one shared setting both still agree on - which system prompt to use - comes
from `src/common/config.py`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.common.config import ConfigError, load_env, load_instructions_file, require

DEFAULT_AGENT_NAME = "fiberops-agent"


@dataclass(frozen=True)
class FoundryConfig:
    """Everything needed to register and invoke a prompt agent in a project."""

    project_endpoint: str
    """https://<resource>.services.ai.azure.com/api/projects/<project>

    Note this is the *opposite* of what `AZURE_OPENAI_ENDPOINT` wants. The
    Agent Framework implementation talks to the Azure OpenAI API surface
    (`openai.azure.com`); the Agent Service lives on the project endpoint.
    Both belong to the same resource.
    """

    agent_name: str
    """The name the agent gets in the project. This is its identity: syncing
    again under the same name creates a new *version* rather than a new agent.
    """

    model_deployment: str
    """A model deployment that exists in *this project's* resource."""

    mcp_server_url: str
    """The public HTTPS endpoint of the Postgres MCP server.

    Published by `infra/deploy-mcp.ps1`. It has to be reachable from Azure -
    the Agent Service calls it, you do not.
    """

    mcp_connection_name: str
    """The Foundry project connection that holds the endpoint's credential.

    The Agent Service *refuses* an inline `Authorization` header on an MCP tool
    - "Headers that can include sensitive information are not allowed" - so the
    secret lives in a connection and the agent only references it by name.
    `infra/deploy-mcp.ps1` creates it.
    """


def load_foundry_config() -> FoundryConfig:
    endpoint = require(
        "FOUNDRY_PROJECT_ENDPOINT",
        "Foundry portal -> your project -> Overview. It looks like "
        "https://<resource>.services.ai.azure.com/api/projects/<project>.",
    )

    # The mirror image of the check in src/maf/config.py: people paste the
    # Azure OpenAI endpoint here just as often as the other way round.
    if "openai.azure.com" in endpoint:
        raise ConfigError(
            f"FOUNDRY_PROJECT_ENDPOINT looks like an Azure OpenAI endpoint:\n"
            f"  {endpoint}\n\n"
            "The Agent Service needs the project endpoint instead:\n"
            "  https://<resource>.services.ai.azure.com/api/projects/<project>\n"
            "Both belong to the same resource."
        )
    if "/api/projects/" not in endpoint:
        raise ConfigError(
            f"FOUNDRY_PROJECT_ENDPOINT is missing the project path:\n"
            f"  {endpoint}\n\n"
            "It must include /api/projects/<project-name>. The account-level\n"
            "endpoint on its own is not enough - agents belong to a project."
        )

    server_url = require(
        "MCP_SERVER_URL",
        "The Agent Service can only call an MCP server over HTTPS, so the "
        "Postgres MCP server has to be published first:\n"
        "       pwsh infra/deploy-mcp.ps1\n"
        "     That script writes MCP_SERVER_URL and MCP_CONNECTION_NAME into .env. "
        "See docs/implementations.md.",
    )
    if not server_url.startswith("https://"):
        raise ConfigError(
            f"MCP_SERVER_URL must be an https:// endpoint, got:\n  {server_url}\n\n"
            "The Agent Service will not call a local or plaintext address - it "
            "runs in Azure,\nnot on your machine."
        )

    return FoundryConfig(
        project_endpoint=endpoint.rstrip("/"),
        agent_name=os.environ.get("FOUNDRY_AGENT_NAME", "").strip() or DEFAULT_AGENT_NAME,
        model_deployment=require(
            "FOUNDRY_MODEL_DEPLOYMENT",
            "A deployment in the same resource as the project, e.g. 'gpt-4.1'. "
            "This is the deployment name, not the model name.",
        ),
        mcp_server_url=server_url,
        mcp_connection_name=require(
            "MCP_CONNECTION_NAME",
            "The Foundry project connection holding the MCP endpoint's credential. "
            "infra/deploy-mcp.ps1 creates it and writes the name into .env.",
        ),
    )


@dataclass(frozen=True)
class FoundryAppConfig:
    """All configuration the Foundry Agent Service implementation needs."""

    foundry: FoundryConfig
    instructions_file: Path | None


def load_foundry_app_config() -> FoundryAppConfig:
    """Load `.env` and validate every setting this implementation needs."""
    load_env()
    return FoundryAppConfig(
        foundry=load_foundry_config(),
        instructions_file=load_instructions_file(),
    )
