# Agent Framework + Postgres MCP Server

**A small, reproducible sample: a local AI agent that reads *and writes* an Azure PostgreSQL database, using the [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python) and the [Postgres MCP Server](https://github.com/crystaldba/postgres-mcp).**

*[Leia em portugues](README.pt-BR.md)*

The agent never has SQL written for it. You describe an outcome in plain language, the model writes the SQL, and an MCP server executes it against the database.

```mermaid
flowchart LR
    User["👤 You<br/><small>CLI</small>"]
    Agent["🤖 Agent<br/><small>Agent Framework<br/>+ MCPStdioTool</small>"]
    MCP["🔌 postgres-mcp<br/><small>started by uvx</small>"]
    DB[("🐘 Azure<br/>PostgreSQL")]
    AOAI["🧠 Azure OpenAI<br/><small>gpt-4.1</small>"]

    User <--> Agent
    Agent <-->|"stdio<br/>JSON-RPC"| MCP
    MCP <-->|"TCP 5432<br/>+ TLS"| DB
    Agent <-->|HTTPS| AOAI
```

The sample database belongs to a fictional telecom operator monitoring its optical fiber backbone: sites, fiber links, alerts and technician notes.

---

## Two ways to use this repo

**A. Full walkthrough** — provision a throwaway Azure PostgreSQL, load the sample telecom data, and explore. Start at [Quickstart](#quickstart).

**B. Bring your own** — you already have a PostgreSQL database and an Azure OpenAI deployment, and you just want to point the agent at them. **No code changes required**, only `.env`. Start at **[Using your own database and model](docs/bring-your-own.md)**.

---

## Table of contents

- [What you will learn](#what-you-will-learn)
- [Prerequisites](#prerequisites)
- [Quickstart](#quickstart)
- [Using your own database](#using-your-own-database)
- [What each file does](#what-each-file-does)
- [The three examples](#the-three-examples)
- [Things to try](#things-to-try)
- [How it works](#how-it-works)
- [Security](#security)
- [Cost and cleanup](#cost-and-cleanup)
- [Troubleshooting](#troubleshooting)
- [Where to go next](#where-to-go-next)

---

## What you will learn

By the end you will be able to:

1. Connect a Microsoft Agent Framework agent to an Azure OpenAI deployment using Entra ID, with no API keys.
2. Give that agent database access through an MCP server, without writing a single tool function yourself.
3. Let the agent run `SELECT`, `INSERT` and `UPDATE` statements it wrote itself, and prove the writes persisted.
4. Keep multi-turn context with an `AgentSession`, so follow-ups like *"now resolve that one"* work.
5. Put a human in the loop, so no statement runs without approval.
6. Provision and tear down the Azure resources with one command.

> **Heads-up if you have seen other samples.** The Agent Framework API changed between the `1.0.0bYYMMDD` betas and the 1.x GA release. Most blog posts and samples online — including the ones this repo was inspired by — use the old names and will not run today. See [API changes](#api-changes-from-the-betas).

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.10+** | 3.11 or newer recommended. |
| **[uv](https://docs.astral.sh/uv/)** | Provides `uvx`, used to run the Postgres MCP server in an isolated environment. Install: `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` (Windows) or `curl -LsSf https://astral.sh/uv/install.sh \| sh`. |
| **[Azure CLI](https://aka.ms/installazurecli)** | Used for provisioning and for Entra ID sign-in. |
| **An Azure subscription** | With permission to create a PostgreSQL flexible server. |
| **An Azure OpenAI / Foundry deployment** | Any tool-calling model: `gpt-4.1`, `gpt-4.1-mini`, `gpt-4o`, ... You need the **Cognitive Services OpenAI User** role on the resource. |

You do **not** need `psql`, Docker, or any prior MCP knowledge.

---

## Quickstart

### 1. Get the code and install dependencies

```powershell
git clone <this-repo> postgres-maf
cd postgres-maf

python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Windows
# source .venv/bin/activate           # macOS / Linux

pip install -r requirements.txt
```

### 2. Sign in to Azure

```powershell
az login
```

### 3. Provision PostgreSQL

```powershell
pwsh infra/provision.ps1              # Windows
# bash infra/provision.sh             # macOS / Linux
```

This creates a resource group and the cheapest possible PostgreSQL flexible server (Burstable `Standard_B1ms`, 32 GB), opens the firewall for your machine, and writes a ready-to-use `.env`.

It also handles two things that trip people up: subscriptions that are **restricted from provisioning PostgreSQL in certain regions** (it probes and suggests working ones), and networks where **your real egress IP differs from what IP-lookup services report** (it asks PostgreSQL directly). See [How the firewall detection works](docs/architecture.md#firewall-detection).

> Open `.env` and check that `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_DEPLOYMENT` match a deployment you actually have. The script guesses; it can guess wrong.

### 4. Load the sample data

```powershell
python -m infra.seed
```

```
Verifying:
  sites                  6
  fiber_links            8
  fiber_alerts           25
  alert_notes            5
  open critical alerts   3
```

### 5. Talk to the agent

```powershell
python -m src.main
```

```
you > Which critical alerts are open right now?

agent > Three critical alerts are currently open:

| alert_id | link_code       | hours_open |
|----------|-----------------|------------|
| 1        | LNK-SPO-BHZ-01  | 3.2        |
| 21       | LNK-CWB-POA-01  | 2.2        |
| 3        | LNK-SPO-RIO-01  | 0.9        |

--- what the agent did (1 tool call(s)) ---
[1] execute_sql
    SELECT a.alert_id, l.code AS link_code,
           EXTRACT(EPOCH FROM (now() - a.opened_at))/3600 AS hours_open
    FROM fiber_alerts a
    JOIN fiber_links l ON a.link_id = l.link_id
    WHERE a.status = 'open' AND a.severity = 'critical'
    ORDER BY a.opened_at ASC;
    -> [{'alert_id': 1, 'link_code': 'LNK-SPO-BHZ-01', ...}]
--- end of trace ---
```

The trace under each answer is the point of the whole sample: **nothing is hidden**. The model wrote that SQL.

---

## Using your own database

Everything above assumes you ran the provisioning script. If you already have a PostgreSQL database and an Azure OpenAI deployment, you can skip all of that — **no code changes needed**.

```bash
pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env`:

```ini
# Your database - Azure, on-prem, Docker, RDS, Neon, Supabase, anything
PGHOST=my-server.example.com
PGDATABASE=my_database
PGUSER=my_user
PGPASSWORD=my_password
PGSSLMODE=require            # `disable` for a local Docker Postgres

# Your model
AZURE_OPENAI_ENDPOINT=https://my-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=my-deployment-name
AZURE_OPENAI_API_VERSION=

# Teach the agent YOUR schema instead of the FiberOps sample
AGENT_INSTRUCTIONS_FILE=prompts/auto-discover-schema.md

# Start read-only against data you care about
POSTGRES_MCP_ACCESS_MODE=restricted
```

```bash
az login
python -m src.main
```

**The step that matters is `AGENT_INSTRUCTIONS_FILE`.** By default the agent uses a prompt describing the FiberOps sample schema; point it at your database without changing that and it will look for tables that do not exist. You have two options:

| | |
|---|---|
| `prompts/auto-discover-schema.md` | The agent inspects your schema at run time. Zero effort, works anywhere, costs a few extra model calls. |
| `prompts/template.md` | Copy it, describe your tables, point `AGENT_INSTRUCTIONS_FILE` at your copy. Best results — and the agent can write the first draft for you. |

The CLI shows which prompt is active on startup, so you are never guessing.

The scripts in `src/examples/` are written for the sample data and will refuse to run against your database rather than doing something meaningless or destructive.

**→ Full walkthrough, including safety, permissions and a Docker-only setup: [docs/bring-your-own.md](docs/bring-your-own.md)**

---

## What each file does

```
postgres-maf/
├── src/
│   ├── config.py       Loads and validates .env. Plain Python, no framework.
│   ├── agent.py        >>> THE FILE WORTH READING <<<
│   │                   Builds the chat client, the MCP tool and the Agent.
│   ├── main.py         Interactive CLI: streaming, sessions, slash commands.
│   ├── trace.py        Digs tool calls out of a response so you can see the SQL.
│   └── examples/
│       ├── read_only.py       Example 1 - queries only
│       ├── read_write.py      Example 2 - read, write, prove it persisted
│       └── human_approval.py  Example 3 - approve every statement
├── infra/                      ONLY needed to create the demo environment
│   ├── provision.ps1 / .sh    Create Azure resources + write .env
│   ├── seed.sql               Telecom schema and sample data
│   ├── seed.py                Applies seed.sql (no psql needed)
│   └── teardown.ps1 / .sh     Stop or delete everything
├── prompts/                    System prompts - swap these for your own schema
│   ├── auto-discover-schema.md  Agent introspects any database at run time
│   └── template.md              Copy and describe your own tables
├── docs/
│   ├── bring-your-own.md      Using your own database and model
│   ├── architecture.md        How the pieces fit together
│   └── troubleshooting.md     Every error we hit, and the fix
├── .env.example               Every setting, documented
└── requirements.txt / pyproject.toml
```

Start with [`src/agent.py`](src/agent.py). It is about 80 lines of actual code and the rest is explanation.

---

## The three examples

Run them in order.

### Example 1 — reading

```powershell
python -m src.examples.read_only
```

Four independent questions, from a simple count to an average time-to-resolution per severity. No `session=` is passed, so each question is isolated.

### Example 2 — writing

```powershell
python -m src.examples.read_write
```

The one that matters. Four turns sharing **one session**:

1. **Read** — find the oldest open critical alert.
2. **Write** — add a technician note to *that* alert.
3. **Write** — set *that* alert's status to `acknowledged`.
4. **Read** — re-read it and list the notes, proving the writes stuck.

Steps 2 and 3 work without repeating the alert id because the session carries the context.

Reset the data any time with `python -m infra.seed`.

### Example 3 — human in the loop

```powershell
python -m src.examples.human_approval
```

Flips the MCP tool to `approval_mode="always_require"` and asks the agent to delete old alerts. Every statement is shown to you first:

```
APPROVAL REQUIRED - the agent wants to run this:
  tool: execute_sql
  SQL:
      DELETE FROM fiber_alerts
      WHERE status = 'resolved' AND resolved_at < NOW() - INTERVAL '7 days'
      RETURNING alert_id;
Allow it? [y/N]
```

Answer `n` and nothing happens. This is the pattern you want before pointing an agent at anything real.

---

## Things to try

Once `python -m src.main` is running:

**Reading**
- Which links are degraded, and how many alerts has each of them raised?
- What is the average attenuation of open alerts, per alert type?
- Show me every alert on links longer than 700 km.

**Writing**
- Add a note to alert 3 saying the field team confirmed a fiber cut near Resende.
- Acknowledge every open alert with severity `low`.
- Resolve alert 24 and explain exactly what you changed.

**Multi-turn** (this is where sessions earn their keep)
- `Which link has the most open alerts?`
- `Add a note to all of them saying an emergency crew is being dispatched.`
- `Now show me those notes.`

**Beyond CRUD** — `postgres-mcp` also ships analysis tools:
- Explain the query plan for counting open alerts per link.
- Is my database missing any indexes for that query?
- Check the health of this database.

The agent answers in whatever language you write in, so Portuguese works fine.

---

## How it works

Three objects, assembled in [`src/agent.py`](src/agent.py).

### 1. The chat client — the brain

```python
from agent_framework.openai import OpenAIChatClient
from azure.identity import AzureCliCredential

client = OpenAIChatClient(
    model="gpt-4.1",                                   # your DEPLOYMENT name
    azure_endpoint="https://<res>.openai.azure.com/",  # this is what selects Azure
    credential=AzureCliCredential(),                   # Entra ID, no keys
)
```

Two things that catch people out:

- The class is `OpenAIChatClient`, **not** `AzureOpenAIChatClient`. Passing `azure_endpoint=` is what points it at Azure.
- `model=` wants your **deployment name**, which on Azure is often not the model id.

### 2. The tool — the hands

```python
from agent_framework import MCPStdioTool

pg = MCPStdioTool(
    name="postgres",
    command="uvx",
    args=["--with", "mcp<2", "postgres-mcp", "--access-mode=unrestricted"],
    env={"DATABASE_URI": "postgresql://user:pass@host:5432/db?sslmode=require"},
    approval_mode="never_require",
)
```

`uvx` downloads and runs the MCP server in its own environment. Agent Framework starts it as a child process, speaks MCP over stdin/stdout, and automatically discovers every tool it offers — `execute_sql`, `list_objects`, `get_object_details`, `explain_query`, `analyze_db_health` and more. **We describe none of them.** MCP advertises their names, descriptions and JSON schemas, and the framework forwards all of that to the model.

The connection string goes in `env`, never in `args`: command-line arguments are visible to anything that can list the process table.

The `--with "mcp<2"` pin is required. `postgres-mcp` is built against v1 of the MCP SDK; without the pin you get a confusing `Connection closed` error. Details in [troubleshooting](docs/troubleshooting.md#mcp-server-failed-to-initialize-connection-closed).

### 3. The agent — putting them together

```python
from agent_framework import Agent

agent = Agent(client=client, instructions=SCHEMA_PROMPT, tools=pg)

async with agent:                      # <-- starts the MCP child process
    session = agent.create_session()   # <-- conversation memory
    result = await agent.run("How many alerts are open?", session=session)
    print(result.text)
```

**The `async with` is not optional.** It is what starts and stops the MCP server. Forget it and the model reports that it has no way to reach the database — the single most common mistake with MCP tools.

The system prompt in `agent.py` spells out the whole schema. The MCP server *could* introspect it, but stating it up front means fewer round trips, far fewer hallucinated column names, and a natural place for rules the schema cannot express (*"when you resolve an alert you must also set `resolved_at`"*).

### API changes from the betas

If you are porting code from an older sample:

| Beta API (most samples online) | 1.x GA (this repo) |
|---|---|
| `from agent_framework.azure import AzureOpenAIChatClient` | `from agent_framework.openai import OpenAIChatClient` |
| `AzureOpenAIChatClient(endpoint=..., deployment_name=...)` | `OpenAIChatClient(azure_endpoint=..., model=...)` |
| `client.create_agent(...)` | `Agent(client=..., ...)` |
| `agent.run_stream(...)` | `agent.run(..., stream=True)` |
| `AgentThread`, `agent.get_new_thread()` | `AgentSession`, `agent.create_session()` |
| `MCPSSETool` | removed — use `MCPStdioTool` or `MCPStreamableHTTPTool` |

---

## Security

This is a teaching sample. It is deliberately permissive, and you should not copy its defaults into anything real.

**`--access-mode=unrestricted` means the agent can `DROP TABLE`.** Combined with `approval_mode="never_require"`, an unlucky prompt can destroy data with nobody in the loop. That is an acceptable trade for a disposable demo database and nothing else.

Before pointing an agent at data you care about, apply these in order of effectiveness:

1. **A restricted database role.** `GRANT SELECT, INSERT ON ...` and nothing more. This is the only guardrail an LLM cannot talk its way around.
2. **`POSTGRES_MCP_ACCESS_MODE=restricted`.** The MCP server then permits only read-only transactions.
3. **`POSTGRES_MCP_APPROVAL_MODE=always_require`.** A human approves every statement — see Example 3.
4. **`allowed_tools=[...]`** on `MCPStdioTool`, hiding everything except the calls you want.

Also:

- `.env` holds a database password and is git-ignored. Keep it that way.
- Prefer Entra ID (`az login`) over `AZURE_OPENAI_API_KEY`; the sample defaults to it.
- The provisioning script opens the firewall to a /24 range for convenience. Narrow it to a single IP for anything long-lived.

---

## Cost and cleanup

A Burstable `Standard_B1ms` server with 32 GB costs roughly **USD 15–20 per month** if you leave it running. Azure OpenAI usage for this sample is a few cents.

**Pause it between sessions** (keeps your data; Azure auto-resumes after 7 days):

```powershell
pwsh infra/teardown.ps1 -Stop
# bash infra/teardown.sh --stop
```

**Delete everything:**

```powershell
pwsh infra/teardown.ps1
# bash infra/teardown.sh
```

---

## Troubleshooting

The short version of [docs/troubleshooting.md](docs/troubleshooting.md):

| Symptom | Cause |
|---|---|
| `MCP server ... failed to initialize: Connection closed` | Missing `--with "mcp<2"`, or `uvx` not on PATH. |
| `400 BadRequest: API version not supported` | `AZURE_OPENAI_API_VERSION` is pinned to an old value. Leave it **empty**. |
| `connection timeout expired` | Firewall rule does not match your real egress IP. Re-run the provisioning script. |
| `The location is restricted from performing this operation` | Your subscription cannot create PostgreSQL in that region. The script suggests alternatives. |
| The agent says it cannot access the database | You forgot `async with agent:`. |
| `401` / `PermissionDenied` from Azure OpenAI | You need the **Cognitive Services OpenAI User** role, then `az login` again. |
| Lots of `INFO Processing request of type ...` | Normal. That is the MCP server logging to stderr. |

---

## Where to go next

- **Add your own tools.** `tools=` accepts plain Python functions alongside the MCP tool; Agent Framework turns them into tool definitions automatically.
- **Add a second MCP server.** Pass a list to `tools=`. The [Microsoft Learn MCP server](https://learn.microsoft.com/api/mcp) works nicely with `MCPStreamableHTTPTool`.
- **Vector search.** Enable `pgvector` on the server and give the agent semantic search over alert descriptions — see [AI agents in Azure Database for PostgreSQL](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-agents).
- **Persist conversations.** Agent Framework ships `SessionStore` / `FileSessionStore`; store sessions in Postgres itself.
- **Go multi-agent.** Agent Framework has workflows and orchestrations for agents that hand work to each other.

### References

- [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python)
- [AI agents in Azure Database for PostgreSQL flexible server](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-agents)
- [Postgres MCP Server (crystaldba/postgres-mcp)](https://github.com/crystaldba/postgres-mcp)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Ignite 2025 LAB515 — Advanced AI agents with PostgreSQL](https://github.com/microsoft/ignite25-LAB515-build-advanced-ai-agents-with-postgresql)
- [schneidenbach/using-agent-framework-with-postgres](https://github.com/schneidenbach/using-agent-framework-with-postgres)

## License

MIT.
