"""
Building the prompt agent.
================================================================================

The whole implementation is now three fields:

    PromptAgentDefinition(
        model=...,
        instructions=...,
        tools=[MCPTool(server_label="postgres", server_url=...)],
    )

**One tool, not nine.** The agent points at an MCP server and the Agent Service
does the rest: it calls `tools/list` itself, decides what to invoke, and calls
`tools/call` directly. Nothing runs on your machine, which is why the agent also
works in the Foundry playground, in a scheduled routine, or from any other
caller.

That is the difference from `src/maf/agent.py`. There, the agent exists only
while your process runs and the MCP server is a child process you own. Here the
definition is *submitted* to the project, which stores it, versions it and shows
it in the portal - and the MCP server is a container that outlives both.
"""

from __future__ import annotations

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition
from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential

from src.common.prompts import load_instructions
from src.foundry.config import FoundryAppConfig, FoundryConfig

AGENT_DESCRIPTION = (
    "Answers questions about a PostgreSQL database and, depending on the "
    "server's access mode, changes it. Backed by a Postgres MCP server."
)

SERVER_LABEL = "postgres"


def build_project_client(cfg: FoundryConfig) -> AIProjectClient:
    """Connect to the Foundry project with Entra ID.

    `AzureCliCredential` goes first for the same reason as in `src/maf/agent.py`:
    on a developer machine `az login` is what you actually did, and trying it
    directly avoids several seconds of failed probes at every start-up.

    You need a role that grants agent read *and write* on the project -
    `Azure AI User` is the usual one. Subscription Owner is not sufficient by
    itself, and the failure is a 403 mentioning `agents/write`.
    """
    return AIProjectClient(
        endpoint=cfg.project_endpoint,
        credential=ChainedTokenCredential(AzureCliCredential(), DefaultAzureCredential()),
    )


def build_postgres_mcp_tool(cfg: FoundryConfig) -> MCPTool:
    """Point the agent at the published MCP server.

    `require_approval="never"` keeps the demo usable. With `"always"` every call
    comes back to the caller as an `mcp_approval_request` - the right setting for
    anything real, but it means the playground stops at every step. The guardrail
    that actually matters is the server's own access mode, set when you publish
    it: `infra/deploy-mcp.ps1 -AccessMode restricted`.
    """
    return MCPTool(
        server_label=SERVER_LABEL,
        server_url=cfg.mcp_server_url,
        server_description=(
            "Runs SQL against a PostgreSQL database and inspects its schema, "
            "query plans and health."
        ),
        # Not `headers={"Authorization": ...}` - the Agent Service rejects that
        # outright, because the agent definition is readable by anyone with
        # access to the project. The credential stays in the connection.
        project_connection_id=cfg.mcp_connection_name,
        require_approval="never",
    )


def build_agent_definition(config: FoundryAppConfig) -> PromptAgentDefinition:
    """Assemble the definition that gets stored in the project.

    The instructions come from the same place as on the Agent Framework path:
    `AGENT_INSTRUCTIONS_FILE` if you set it, otherwise the built-in FiberOps
    prompt. Pointing this implementation at your own database therefore needs no
    code change either - see docs/bring-your-own.md.
    """
    return PromptAgentDefinition(
        model=config.foundry.model_deployment,
        instructions=load_instructions(config.instructions_file),
        tools=[build_postgres_mcp_tool(config.foundry)],
    )
