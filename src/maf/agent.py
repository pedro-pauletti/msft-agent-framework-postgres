"""
The agent itself - this is the file worth reading.
================================================================================

Three things are assembled here:

  1. A **chat client** - `OpenAIChatClient` pointed at Azure OpenAI. This is the
     agent's "brain": it decides what to do, but it cannot touch anything.

  2. A **tool** - `MCPStdioTool`, which launches the Postgres MCP server as a
     child process and exposes its tools (run a query, inspect the schema,
     explain a plan, ...) to the model. These are the agent's "hands".

  3. An **Agent** that ties the two together with a system prompt.

The important idea: we never write SQL, and we never open a database connection.
The model writes the SQL and calls an MCP tool to execute it. Our job is to give
it accurate context about the schema and sensible guardrails.


How the MCP piece works
-----------------------

MCP (Model Context Protocol) is a standard way for a program to expose tools to
an LLM. The Postgres MCP server (`crystaldba/postgres-mcp`) is a separate
process that speaks that protocol over stdin/stdout.

    Python process                      child process
    +--------------------+   stdio     +---------------------+   TCP/TLS
    |  Agent (MAF)       |<----------->|  postgres-mcp       |<---------->  Azure
    |  + MCPStdioTool    |             |  (started by uvx)   |            Postgres
    +--------------------+             +---------------------+

Agent Framework starts and stops that child process for us, as long as the Agent
is used as an async context manager:

    async with build_agent() as agent:
        ...

Forgetting the `async with` is the single most common mistake with MCP tools:
the tool list comes back empty and the model insists it has no way to query the
database.
"""

from __future__ import annotations

from typing import Any

from agent_framework import Agent, MCPStdioTool
from agent_framework.openai import OpenAIChatClient
from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential

from src.common.config import McpConfig, PostgresConfig
from src.common.prompts import load_instructions
from src.maf.config import AppConfig, AzureOpenAIConfig
from src.maf.mcp_server import ensure_uvx_available, uvx_environment
from src.maf.workbook import upload_workbook

# =============================================================================
# 1. The chat client (Azure OpenAI)
# =============================================================================


def build_chat_client(cfg: AzureOpenAIConfig) -> OpenAIChatClient:
    """Create the Agent Framework chat client bound to your Azure OpenAI deployment.

    Note two naming quirks that catch people out:

    * The class is `OpenAIChatClient`, not `AzureOpenAIChatClient`. Older samples
      (and most blog posts) import `AzureOpenAIChatClient` from
      `agent_framework.azure`; that class was removed in the 1.x GA release.
      Passing `azure_endpoint=` is what makes this client talk to Azure.

    * The `model=` parameter expects your **deployment name**, not the model id.
      On Azure OpenAI those are frequently different.
    """
    if cfg.uses_entra_id:
        # ---------------------------------------------------------------------
        # Recommended path: Entra ID (Azure AD). No secrets anywhere.
        #
        # `DefaultAzureCredential` tries a chain of sources (environment,
        # managed identity, Azure CLI, Azure PowerShell, ...). We put
        # `AzureCliCredential` first because on a developer machine `az login`
        # is what you actually did, and going straight to it avoids several
        # seconds of failed probes on every start-up.
        #
        # You need the "Cognitive Services OpenAI User" role on the Azure OpenAI
        # resource for this to work. See docs/troubleshooting.md.
        # ---------------------------------------------------------------------
        credential = ChainedTokenCredential(
            AzureCliCredential(),
            DefaultAzureCredential(),
        )
        return OpenAIChatClient(
            model=cfg.deployment,
            azure_endpoint=cfg.endpoint,
            api_version=cfg.api_version,
            credential=credential,
        )

    # -------------------------------------------------------------------------
    # Fallback path: API key. Simpler to get going, but the key ends up in .env
    # and in your process environment. Prefer Entra ID when you can.
    # -------------------------------------------------------------------------
    return OpenAIChatClient(
        model=cfg.deployment,
        azure_endpoint=cfg.endpoint,
        api_version=cfg.api_version,
        api_key=cfg.api_key,
    )


# =============================================================================
# 2. The tool (Postgres MCP server)
# =============================================================================


def build_postgres_mcp_tool(pg: PostgresConfig, mcp: McpConfig) -> MCPStdioTool:
    """Create the MCP tool that gives the agent access to PostgreSQL.

    We run `uvx postgres-mcp`, which downloads and caches the server on first
    use and runs it in its own isolated environment. That isolation is not
    cosmetic: `postgres-mcp` requires Python >= 3.12, while this sample supports
    Python >= 3.10, so it genuinely cannot live in the same virtual environment.

    The `--with "mcp<2"` pin is required. `postgres-mcp` 0.3.x is built against
    v1 of the MCP Python SDK, and v2 renamed `FastMCP` to `MCPServer`. Without
    the pin, `uvx` resolves `mcp` 2.x and the server dies on start-up with
    `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`, which surfaces
    here as an unhelpful "MCP server failed to initialize: Connection closed".

    Docker is an equally good alternative if you prefer it (the published image
    already has the right pins baked in):

        MCPStdioTool(
            name="postgres",
            command="docker",
            args=["run", "-i", "--rm", "-e", "DATABASE_URI",
                  "crystaldba/postgres-mcp", "--access-mode=unrestricted"],
            env={"DATABASE_URI": pg.connection_uri},
        )

    Tools the server exposes include `execute_sql`, `list_schemas`,
    `list_objects`, `get_object_details`, `explain_query`, `analyze_workload`
    and `analyze_db_health`. We do not have to describe any of them - MCP
    advertises their names, descriptions and JSON schemas automatically, and
    Agent Framework forwards all of that to the model.
    """
    ensure_uvx_available()

    return MCPStdioTool(
        # The name is what the model sees as the tool namespace. Keep it short
        # and descriptive.
        name="postgres",
        description=(
            "Query and modify a PostgreSQL database. Supports running SQL, "
            "inspecting the schema, explaining query plans and checking "
            "database health."
        ),
        # --- how to start the server ---
        command="uvx",
        args=[
            # Pin the MCP SDK to v1: postgres-mcp 0.3.x does not work with v2.
            # See the docstring above for the exact failure mode.
            "--with",
            "mcp<2",
            "postgres-mcp",
            # unrestricted  -> INSERT / UPDATE / DELETE / DDL allowed
            # restricted    -> read-only transactions with resource limits
            f"--access-mode={mcp.access_mode}",
        ],
        # The connection string is passed as an environment variable, never as a
        # command-line argument - arguments are visible to any process that can
        # list the process table, and this string contains your password.
        env={"DATABASE_URI": pg.connection_uri, **uvx_environment()},
        # --- guardrails ---
        # 'never_require'  -> tools run immediately (convenient for a demo)
        # 'always_require' -> every call is handed back to your code for a
        #                     human decision. See src/maf/examples/human_approval.py.
        approval_mode=mcp.approval_mode,
        # Long-haul queries plus a cold `uvx` download can exceed the default,
        # so give the first call some room.
        request_timeout=120,
    )


# =============================================================================
# 3. The agent
# =============================================================================


def build_agent(
    config: AppConfig,
    *,
    instructions: str | None = None,
    name: str = "PostgresAgent",
    with_workbook: bool = True,
) -> Agent:
    """Assemble the full agent: Azure OpenAI + the Postgres MCP tool.

    IMPORTANT: the returned `Agent` must be used as an async context manager,
    because that is what starts and stops the MCP child process::

        async with build_agent(config) as agent:
            result = await agent.run("How many alerts are open?")
            print(result.text)

    `Agent` is a normal object, so this function does no I/O beyond uploading
    the workbook, and never fails because of a bad password - database problems
    surface when you enter the `async with` block.

    When `instructions` is None (the default) the prompt comes from
    `AGENT_INSTRUCTIONS_FILE` if you set it in `.env`, and otherwise from the
    built-in FiberOps prompt. That is what lets you point this sample at your
    own database without editing any code.

    `with_workbook=False` drops the code interpreter, which is what you want if
    your Azure OpenAI resource does not have the container feature enabled.
    """
    tools: list[Any] = [build_postgres_mcp_tool(config.postgres, config.mcp)]

    if with_workbook:
        tools.append(build_code_interpreter_tool(config.azure_openai))

    return Agent(
        client=build_chat_client(config.azure_openai),
        instructions=instructions
        if instructions is not None
        else load_instructions(config.instructions_file),
        name=name,
        # `tools` accepts a single tool or a sequence. Adding a plain Python
        # function here would work too - MAF turns it into a tool automatically.
        tools=tools,
    )


def build_code_interpreter_tool(cfg: AzureOpenAIConfig) -> Any:
    """Attach the contracts workbook to a sandboxed Python environment.

    The tool is hosted: the model writes Python, Azure OpenAI runs it in a
    container with the file mounted, and only the result comes back. Nothing
    executes on this machine, which is why no pandas import appears anywhere in
    this repo despite the agent using pandas constantly.

    `get_code_interpreter_tool` is a static method on the client class rather
    than something you construct yourself, because the tool payload is
    provider-specific. The `SupportsCodeInterpreterTool` protocol is how you
    check at runtime whether a given client offers one.
    """
    return OpenAIChatClient.get_code_interpreter_tool(file_ids=[upload_workbook(cfg)])
