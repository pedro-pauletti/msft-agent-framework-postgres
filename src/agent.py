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

import shutil

from agent_framework import Agent, MCPStdioTool
from agent_framework.openai import OpenAIChatClient
from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential

from src.config import AppConfig, AzureOpenAIConfig, ConfigError, McpConfig, PostgresConfig

# =============================================================================
# System prompt
# =============================================================================
#
# Giving the model the schema up front matters a lot. The MCP server *can*
# introspect the schema itself, but spelling it out here means:
#   - fewer round trips (less latency, lower cost),
#   - far fewer hallucinated column names,
#   - a natural place to state business rules the schema cannot express.
#
# Treat this string as part of the source code: when you change seed.sql, change
# this too.

FIBEROPS_INSTRUCTIONS = """
You are FiberOps Assistant, an AI assistant for the network operations centre of
a telecom operator. You help engineers investigate optical fiber alerts by
querying and updating a PostgreSQL database through the tools available to you.

## Database schema

sites
  site_id      SERIAL PK
  code         TEXT UNIQUE      -- e.g. 'SPO-01'
  name         TEXT
  city         TEXT
  state        CHAR(2)          -- Brazilian state code, e.g. 'SP'
  latitude     NUMERIC
  longitude    NUMERIC

fiber_links
  link_id          SERIAL PK
  code             TEXT UNIQUE   -- e.g. 'LNK-SPO-RIO-01'
  site_a_id        INT -> sites.site_id
  site_b_id        INT -> sites.site_id
  length_km        NUMERIC
  capacity_gbps    INT
  status           TEXT          -- 'active' | 'degraded' | 'maintenance'
  commissioned_on  DATE

fiber_alerts
  alert_id        SERIAL PK
  link_id         INT -> fiber_links.link_id
  alert_type      TEXT           -- 'fiber_cut' | 'high_attenuation' | 'power_loss'
                                 -- | 'degraded_signal' | 'equipment_failure'
  severity        TEXT           -- 'critical' | 'high' | 'medium' | 'low'
  status          TEXT           -- 'open' | 'acknowledged' | 'resolved'
  attenuation_db  NUMERIC        -- optical attenuation in dB; higher is worse
  opened_at       TIMESTAMPTZ
  resolved_at     TIMESTAMPTZ    -- NULL unless status = 'resolved'
  description     TEXT

alert_notes
  note_id     SERIAL PK
  alert_id    INT -> fiber_alerts.alert_id
  author      TEXT               -- technician username, or 'ai-agent'
  note        TEXT
  created_at  TIMESTAMPTZ

## Rules you must follow

1. Always inspect real data before answering. Never guess numbers, never invent
   alert ids, link codes or technician names.
2. A link is identified by two sites. To show a human-readable link name, join
   fiber_links to sites twice (once for site_a_id, once for site_b_id).
3. When you write to the database:
   - Insert notes into alert_notes with author = 'ai-agent'.
   - When you set fiber_alerts.status = 'resolved', you MUST also set
     resolved_at = now(). A CHECK constraint rejects the row otherwise.
   - Never UPDATE or DELETE without a WHERE clause.
   - Only ever change the specific rows the user asked about.
4. Say out loud what you changed, including the ids of affected rows, so the
   engineer can audit it.
5. Prefer one well-written SQL statement over several round trips.
6. Answer concisely, in the same language the user wrote in. When you present
   several rows, use a compact table.
""".strip()


# =============================================================================
# A schema-agnostic prompt, for when you bring your own database
# =============================================================================
#
# The FiberOps prompt above hard-codes a schema, which is the right thing to do
# when you know it. If you point this sample at your own database, you have two
# options:
#
#   1. Write your own prompt describing your schema (best results). Put it in a
#      file and set AGENT_INSTRUCTIONS_FILE in .env.
#   2. Use this one, which tells the agent to discover the schema at run time
#      through the MCP server's introspection tools. Slower and slightly less
#      reliable, but it works against any database with zero configuration.
#
# Option 2 is available out of the box:
#     AGENT_INSTRUCTIONS_FILE=prompts/auto-discover-schema.md
#
# See docs/bring-your-own.md.

GENERIC_INSTRUCTIONS = """
You are a helpful database assistant. You answer questions and make changes by
querying and updating a PostgreSQL database through the tools available to you.

You do NOT know the schema in advance. Discover it.

## How to work

1. On the first question of a conversation, inspect the database before
   answering: list the schemas, list the tables, and get the column details of
   the tables that look relevant. Remember what you learn - do not re-introspect
   the same tables on every turn.
2. Never guess table or column names. If you are unsure, look it up.
3. If the question is ambiguous given the real schema, ask a short clarifying
   question instead of guessing.

## Rules you must follow

1. Always base answers on real query results. Never invent data.
2. Never UPDATE or DELETE without a WHERE clause.
3. Only ever change the specific rows the user asked about.
4. Before a write, check the table's constraints (NOT NULL, CHECK, foreign keys)
   so your statement does not fail or leave the row inconsistent.
5. Say out loud what you changed, including the ids of affected rows, so the
   user can audit it.
6. Prefer one well-written SQL statement over several round trips.
7. Answer concisely, in the same language the user wrote in. When you present
   several rows, use a compact table.
""".strip()


def load_instructions(config: AppConfig) -> str:
    """Pick the system prompt: the user's file if set, otherwise FiberOps.

    This is what makes the sample reusable against your own database without
    touching any code - see docs/bring-your-own.md.
    """
    if config.instructions_file is not None:
        return config.instructions_file.read_text(encoding="utf-8").strip()
    return FIBEROPS_INSTRUCTIONS


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
    if shutil.which("uvx") is None:
        raise ConfigError(
            "`uvx` was not found on your PATH.\n\n"
            "It ships with `uv`, the Python package manager used to run the\n"
            "Postgres MCP server in an isolated environment. Install it with:\n\n"
            "  Windows:        powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"\n"
            "  macOS / Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh\n\n"
            "Then restart your terminal so PATH picks it up."
        )

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
        env={"DATABASE_URI": pg.connection_uri},
        # --- guardrails ---
        # 'never_require'  -> tools run immediately (convenient for a demo)
        # 'always_require' -> every call is handed back to your code for a
        #                     human decision. See src/examples/human_approval.py.
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
) -> Agent:
    """Assemble the full agent: Azure OpenAI + the Postgres MCP tool.

    IMPORTANT: the returned `Agent` must be used as an async context manager,
    because that is what starts and stops the MCP child process::

        async with build_agent(config) as agent:
            result = await agent.run("How many alerts are open?")
            print(result.text)

    `Agent` is a normal object, so this function does no I/O and never fails
    because of a bad password - problems surface when you enter the `async with`
    block.

    When `instructions` is None (the default) the prompt comes from
    `AGENT_INSTRUCTIONS_FILE` if you set it in `.env`, and otherwise from the
    built-in FiberOps prompt. That is what lets you point this sample at your
    own database without editing any code.
    """
    return Agent(
        client=build_chat_client(config.azure_openai),
        instructions=instructions if instructions is not None else load_instructions(config),
        name=name,
        # `tools` accepts a single tool or a sequence. Adding a plain Python
        # function here would work too - MAF turns it into a tool automatically.
        tools=build_postgres_mcp_tool(config.postgres, config.mcp),
    )
