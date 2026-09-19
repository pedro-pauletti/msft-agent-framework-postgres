# Postgres AI Agent — Agent Framework *and* Foundry Agent Service

**A small, reproducible sample: a local AI agent that reads *and writes* an Azure PostgreSQL database through the [Postgres MCP Server](https://github.com/crystaldba/postgres-mcp) — built twice, once with the [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python) and once with the [Foundry Agent Service](https://learn.microsoft.com/azure/ai-foundry/agents/overview), so you can compare them side by side.**

*[Leia em portugues](README.pt-BR.md)*

The agent never has SQL written for it. You describe an outcome in plain language, the model writes the SQL, and an MCP server executes it against the database.

```mermaid
flowchart LR
    User["👤 You<br/><small>CLI</small>"]

    subgraph impl[" Pick an implementation "]
        direction TB
        MAF["🤖 <b>src/maf</b><br/><small>Agent Framework<br/>agent built in memory</small>"]
        FDY["☁️ <b>src/foundry</b><br/><small>Agent Service<br/>agent stored in a project</small>"]
    end

    MCP["🔌 postgres-mcp<br/><small>started by uvx</small>"]
    DB[("🐘 Azure<br/>PostgreSQL")]
    Model["🧠 gpt-4.1"]

    User <--> impl
    MAF <-->|"stdio<br/>JSON-RPC"| MCP
    FDY <-->|"stdio<br/>JSON-RPC"| MCP
    MCP <-->|"TCP 5432<br/>+ TLS"| DB
    impl <-->|HTTPS| Model
```

The sample database belongs to a fictional telecom operator monitoring its optical fiber backbone: sites, fiber links, alerts and technician notes.

---

## The two implementations

Same database, same MCP server, same system prompt, same CLI experience. **Only the runtime changes.** That is the whole point of the repo: everything that differs between the two is a genuine difference between the SDKs, not a difference in the scenario.

```
src/
├── common/     shared by both: .env, the system prompt, the SQL trace
├── maf/        implementation 1 — Microsoft Agent Framework
└── foundry/    implementation 2 — Foundry Agent Service
```

| | `src/maf/` | `src/foundry/` |
|---|---|---|
| **SDK** | `agent-framework-core` + `agent-framework-openai` | `azure-ai-projects` |
| **Where the agent lives** | In memory, for as long as your process runs | Stored in a Foundry project |
| **Visible in the portal** | No — it is a Python object | **Yes**, under Agents, with its instructions and tools |
| **Versioning** | None. Edit the code, restart | Every sync creates a new immutable version |
| **Deployment step** | None | `python -m src.foundry.sync` |
| **Who runs the tool loop** | The framework, invisibly | The Agent Service, remotely |
| **How Postgres is attached** | `MCPStdioTool` — a child process the framework manages | One `mcp` tool pointing at a hosted MCP server |
| **Where the MCP server runs** | On your machine, started by `uvx` | In Azure Container Apps |
| **Works without your code running** | No | **Yes** — portal playground, routines, other agents |
| **Conversation memory** | `AgentSession` | `previous_response_id` |
| **Token streaming** | Yes, built in | Not implemented here |
| **Model config** | `AZURE_OPENAI_*` (`openai.azure.com`) | `FOUNDRY_*` (`services.ai.azure.com/api/projects/...`) |
| **Database credentials** | On your machine, in `.env` | In the container only — this process never sees them |
| **Extra Azure permissions** | Cognitive Services OpenAI User | Azure AI User on the project |
| **Extra infrastructure** | None | Container Registry + Container Apps |
| **Run it** | `python -m src.maf.main` | `pwsh infra/deploy-mcp.ps1`, `python -m src.foundry.sync`, then `python -m src.foundry.main` |

### When to use which

**Use the Agent Framework when the agent is part of an application you are writing.** It is less code, it handles streaming and sessions for you, and the agent is just an object — no deployment step, nothing to keep in sync, no extra Azure permissions. This is the right default for embedding an agent inside a service, a job or a CLI.

**Use the Agent Service when the agent itself is the artefact.** When it has to be discoverable by other people, versioned and governed, evaluated with the Foundry evaluation tooling, edited in the portal by someone who does not have your repo, or invoked by something that is not your code — scheduled routines, Teams, another agent.

**They are not exclusive.** A reasonable end state is both: the definition and governance in the Foundry project, the orchestration in your application.

> ⚠️ **One constraint that shapes everything.** A Foundry prompt agent runs *inside the service*, and the Agent Service only accepts MCP servers that are **remote HTTPS endpoints**. `postgres-mcp` is a local stdio process — nothing in Azure can launch it. So implementation 2 publishes it as a container first (`infra/deploy-mcp.ps1`) and the agent points at that. This is the reason implementation 2 needs infrastructure that implementation 1 does not, and the reason it works with no client running. See **[docs/implementations.md](docs/implementations.md)**.

---

## Two ways to get started

**A. Full walkthrough** — provision a throwaway Azure PostgreSQL, load the sample telecom data, and explore. Start at [Quickstart](#quickstart).

**B. Bring your own** — you already have a PostgreSQL database and a model deployment, and you just want to point the agent at them. **No code changes required**, only `.env`. Works with either implementation. Start at **[Using your own database and model](docs/bring-your-own.md)**.

---

## Table of contents

- [The two implementations](#the-two-implementations)
- [What you will learn](#what-you-will-learn)
- [Prerequisites](#prerequisites)
- [Quickstart](#quickstart)
- [Implementation 2 — the Foundry Agent Service](#implementation-2--the-foundry-agent-service)
- [The contracts workbook](#the-contracts-workbook)
- [The web UI](#the-web-ui)
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
7. Build the *same* agent on the Foundry Agent Service, see it versioned in the portal, and understand exactly what the framework was doing for you.

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
| **A Foundry project** | *Implementation 2 only.* Plus the **Azure AI User** role on it, which is what grants agent write. |
| **Container Registry + Container Apps** | *Implementation 2 only.* `infra/deploy-mcp.ps1` creates both; you just need permission to. The image is built in Azure, so local Docker is not required. |

You do **not** need `psql`, Docker, or any prior MCP knowledge. Implementation 1 does not need a Foundry project, and implementation 2 does not need `AZURE_OPENAI_*` — you can run either without configuring the other.

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

This is **implementation 1**, the Agent Framework one:

```powershell
python -m src.maf.main
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

## Implementation 2 — the Foundry Agent Service

Everything above builds the agent **in memory**: it exists while `python` is running and vanishes when it exits.

The second implementation registers the very same agent in a Foundry project instead. Add three lines to `.env`:

```ini
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_AGENT_NAME=fiberops-agent
FOUNDRY_MODEL_DEPLOYMENT=gpt-4.1
```

### 1. Publish the MCP server

The Agent Service calls the MCP server itself, so it has to be somewhere Azure can reach. This builds the image (in Azure — no local Docker), deploys it to Container Apps, opens the database firewall for it, and stores the endpoint's credential in a Foundry project connection:

```powershell
pwsh infra/deploy-mcp.ps1 -AccessMode restricted
```

```
==> Building the image (this runs in Azure, not on your machine)
    Built mafmcp....azurecr.io/postgres-mcp:20260916181350
==> Deploying the container app 'postgres-mcp'
    Endpoint: https://postgres-mcp....azurecontainerapps.io/mcp
==> Creating the Foundry project connection
    Connection 'postgres-mcp' holds the secret; the agent references it by name.
```

It writes `MCP_SERVER_URL` and `MCP_CONNECTION_NAME` into `.env` for you.

### 2. Register the agent

```powershell
python -m src.foundry.sync
```

```
Done. 'fiberops-agent' is now at version 1.
It has one tool, 'postgres', pointing at the MCP server above.
```

The agent now appears under **Agents** in the Foundry portal with exactly **one tool**:

```json
{
  "type": "mcp",
  "server_label": "postgres",
  "server_url": "https://postgres-mcp....azurecontainerapps.io/mcp",
  "require_approval": "never",
  "project_connection_id": "postgres-mcp"
}
```

Run the sync again and you get version 2 — the definition lives in source control, and every deployment of it is recorded in the project.

### 3. Talk to it

```powershell
python -m src.foundry.main
```

The chat experience is deliberately identical to `python -m src.maf.main`, down to the SQL trace. **But this CLI is now optional** — open the agent in the Foundry portal playground and it answers there too, because nothing runs on your machine.

You need the **Azure AI User** role on the project. Reading agents and writing them are different permissions, so listing agents can succeed while the sync fails with `403 ... agents/write`.

> 💰 The container runs with `min-replicas 1`, so it costs money while it exists. When you are done: `az containerapp delete -g rg-maf-postgres-demo -n postgres-mcp --yes`

**→ Why it is built this way, the four things that only surfaced at runtime, and how to harden it: [docs/implementations.md](docs/implementations.md)**

---

## The contracts workbook

A database is rarely the only source of truth. Both agents also get a **code
interpreter** with one spreadsheet attached, [data/fiberops-contracts.xlsx](data/fiberops-contracts.xlsx):

| Sheet | Holds | Joins to |
|---|---|---|
| `SLA` | Customer, service tier, resolution deadline, penalty per hour | `fiber_links.code` |
| `Maintenance` | Planned windows, work type, field crew | `fiber_links.code` |
| `OnCall` | Engineer, phone, escalation manager | `sites.name` |

None of that exists in Postgres, which is the point. Ask this:

> Which open alert has the largest financial exposure?

and the agent has to use **both tools in one turn** — SQL through the MCP server
to find the open alerts, then pandas over the workbook to join them to the SLA
sheet and multiply the overdue hours by the contractual penalty. Neither tool
can answer it alone.

The code runs in a sandboxed container at the provider, never on your machine.
That is why this repo has no pandas dependency even though the agent uses
pandas constantly.

The workbook is committed, so there is nothing to run. To change the sample
data, edit the tables in [infra/make_workbook.py](infra/make_workbook.py) and
regenerate:

```powershell
pip install openpyxl
python -m infra.make_workbook
python -m src.foundry.sync     # implementation 2 pins a file id, so re-sync
```

The two implementations upload the file to **different places** — Azure OpenAI
for implementation 1, the Foundry project for implementation 2 — so the same
workbook has a different id in each. Neither re-uploads if a file with the same
name is already there.

---

## The web UI

The CLIs answer *what did the agent say?*. This answers *what did the agent do?*

```powershell
pip install -e ".[ui]"       # or: pip install -r requirements.txt
python -m src.ui.server      # then open http://127.0.0.1:8100
```

One chat window, a switch between the two implementations, and a trace panel
modelled on the Foundry portal playground:

| Panel | Shows |
|---|---|
| **Tokens** | Input, output and total for the turn, plus how many were served from cache |
| **Tool calls** | Every call the model made, with the SQL highlighted and the result it got back |
| **Metadata** | Model, response id, finish reason, MCP transport, access mode, latency |
| **Turns** | The history for this conversation — click any turn to re-inspect it |

The switch at the top right is the point of the whole thing: ask both
implementations the same question and watch the same SQL arrive by two
completely different routes. Each keeps its own conversation, so switching
back and forth is non-destructive.

It is a thin layer over the same functions the CLIs call, so there is no
second agent implementation hiding in it. If only one implementation is
configured in `.env`, the other is greyed out with the reason.

---

## Using your own database

Everything above assumes you ran the provisioning script. If you already have a PostgreSQL database and a model deployment, you can skip all of that — **no code changes needed**, and it works with either implementation.

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
python -m src.maf.main
```

**The step that matters is `AGENT_INSTRUCTIONS_FILE`.** By default the agent uses a prompt describing the FiberOps sample schema; point it at your database without changing that and it will look for tables that do not exist. You have two options:

| | |
|---|---|
| `prompts/auto-discover-schema.md` | The agent inspects your schema at run time. Zero effort, works anywhere, costs a few extra model calls. |
| `prompts/template.md` | Copy it, describe your tables, point `AGENT_INSTRUCTIONS_FILE` at your copy. Best results — and the agent can write the first draft for you. |

The CLI shows which prompt is active on startup, so you are never guessing.

The scripts in `src/maf/examples/` are written for the sample data and will refuse to run against your database rather than doing something meaningless or destructive.

**→ Full walkthrough, including safety, permissions and a Docker-only setup: [docs/bring-your-own.md](docs/bring-your-own.md)**

---

## What each file does

```
postgres-maf/
├── src/
│   ├── common/          SHARED BY BOTH IMPLEMENTATIONS
│   │   ├── config.py       Loads and validates the shared part of .env.
│   │   ├── prompts.py      The system prompt describing the schema.
│   │   ├── preflight.py    Fails fast when the database is unreachable.
│   │   └── trace.py        Prints the SQL the model wrote.
│   ├── maf/             IMPLEMENTATION 1 - MICROSOFT AGENT FRAMEWORK
│   │   ├── agent.py        >>> THE FILE WORTH READING <<<
│   │   │                   Builds the chat client, the MCP tool and the Agent.
│   │   ├── config.py       AZURE_OPENAI_* only.
│   │   ├── mcp_server.py   Starts postgres-mcp with uvx.
│   │   ├── workbook.py     Uploads the xlsx to Azure OpenAI.
│   │   ├── main.py         Interactive CLI: streaming, sessions, slash commands.
│   │   └── examples/
│   │       ├── read_only.py       Example 1 - queries only
│   │       ├── read_write.py      Example 2 - read, write, prove it persisted
│   │       └── human_approval.py  Example 3 - approve every statement
│   └── foundry/         IMPLEMENTATION 2 - FOUNDRY AGENT SERVICE
│       ├── agent.py        >>> THE FILE WORTH READING <<<
│       │                   The prompt agent definition: model, prompt, one MCP tool.
│       ├── config.py       FOUNDRY_* and the MCP endpoint. No database credentials.
│       ├── sync.py         Registers the agent in your Foundry project.
│       └── main.py         Interactive CLI. One request per turn.
├── src/ui/                     LOCAL WEB UI - optional, talks to both
│   ├── server.py              Thin FastAPI layer over the two runners
│   └── static/                One HTML, CSS and JS file. No build step.
├── infra/                      ONLY needed to create the demo environment
│   ├── provision.ps1 / .sh    Create Azure resources + write .env
│   ├── deploy-mcp.ps1         Publish the MCP server (implementation 2 only)
│   ├── mcp-server/            Dockerfile + auth wrapper for that container
│   ├── make_workbook.py       Regenerates data/fiberops-contracts.xlsx
│   ├── seed.sql               Telecom schema and sample data
│   ├── seed.py                Applies seed.sql (no psql needed)
│   └── teardown.ps1 / .sh     Stop or delete everything
├── prompts/                    System prompts - swap these for your own schema
│   ├── auto-discover-schema.md  Agent introspects any database at run time
│   └── template.md              Copy and describe your own tables
├── docs/
│   ├── implementations.md     The two implementations, compared in depth
│   ├── bring-your-own.md      Using your own database and model
│   ├── architecture.md        How the pieces fit together
│   └── troubleshooting.md     Every error we hit, and the fix
├── .env.example               Every setting, documented
└── requirements.txt / pyproject.toml
```

**Nothing in `src/common/` knows which implementation is running**, and neither implementation imports from the other. That is what makes the comparison honest: if the two behave differently, it is the SDK, not the setup.

Start with [`src/maf/agent.py`](src/maf/agent.py) or [`src/foundry/agent.py`](src/foundry/agent.py). Each is about 80 lines of actual code; the rest is explanation.

---

## The three examples

These belong to implementation 1. Run them in order.

### Example 1 — reading

```powershell
python -m src.maf.examples.read_only
```

Four independent questions, from a simple count to an average time-to-resolution per severity. No `session=` is passed, so each question is isolated.

### Example 2 — writing

```powershell
python -m src.maf.examples.read_write
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
python -m src.maf.examples.human_approval
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

Works with either implementation — `python -m src.maf.main` or `python -m src.foundry.main`:

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

### Implementation 1 — Agent Framework

Three objects, assembled in [`src/maf/agent.py`](src/maf/agent.py).

#### 1. The chat client — the brain

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

#### 2. The tool — the hands

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

#### 3. The agent — putting them together

```python
from agent_framework import Agent

agent = Agent(client=client, instructions=SCHEMA_PROMPT, tools=pg)

async with agent:                      # <-- starts the MCP child process
    session = agent.create_session()   # <-- conversation memory
    result = await agent.run("How many alerts are open?", session=session)
    print(result.text)
```

**The `async with` is not optional.** It is what starts and stops the MCP server. Forget it and the model reports that it has no way to reach the database — the single most common mistake with MCP tools.

The system prompt comes from [`src/common/prompts.py`](src/common/prompts.py) and spells out the whole schema. The MCP server *could* introspect it, but stating it up front means fewer round trips, far fewer hallucinated column names, and a natural place for rules the schema cannot express (*"when you resolve an alert you must also set `resolved_at`"*).

#### API changes from the betas

If you are porting code from an older sample:

| Beta API (most samples online) | 1.x GA (this repo) |
|---|---|
| `from agent_framework.azure import AzureOpenAIChatClient` | `from agent_framework.openai import OpenAIChatClient` |
| `AzureOpenAIChatClient(endpoint=..., deployment_name=...)` | `OpenAIChatClient(azure_endpoint=..., model=...)` |
| `client.create_agent(...)` | `Agent(client=..., ...)` |
| `agent.run_stream(...)` | `agent.run(..., stream=True)` |
| `AgentThread`, `agent.get_new_thread()` | `AgentSession`, `agent.create_session()` |
| `MCPSSETool` | removed — use `MCPStdioTool` or `MCPStreamableHTTPTool` |

### Implementation 2 — Foundry Agent Service

Three steps, in [`src/foundry/`](src/foundry/) and [`infra/mcp-server/`](infra/mcp-server/).

#### 1. Publish the MCP server

`postgres-mcp` speaks stdio by default, and the Agent Service cannot launch a process on your machine. So it runs as a container instead, serving the same tools over Streamable HTTP:

```python
app = postgres_mcp.mcp.streamable_http_app()
app.add_middleware(BearerTokenMiddleware, token=token)   # it has no auth of its own
```

Two things about this are worth knowing before you copy it. First, the transport was only on `main` when this was written, so the image pins a commit rather than a release. Second, `postgres-mcp` ships **no authentication whatsoever** — the bearer middleware above is ours, and it is the only reason the endpoint is not an open SQL proxy.

The database credentials live in that container. Nothing on your machine needs them.

#### 2. Describe the agent, and store it in the project

```python
definition = PromptAgentDefinition(
    model="gpt-4.1",
    instructions=SCHEMA_PROMPT,
    tools=[MCPTool(
        server_label="postgres",
        server_url=cfg.mcp_server_url,
        project_connection_id=cfg.mcp_connection_name,   # not headers - see below
        require_approval="never",
    )],
)
client.agents.create_version(agent_name="fiberops-agent", definition=definition)
```

**One tool, not nine.** The agent definition says *"there is a Postgres MCP server over there"*; the service connects, lists the tools and calls them. The nine tools never appear in the definition, which is what an MCP integration should look like.

Passing the bearer token as an inline header is rejected outright:

> *Headers that can include sensitive information are not allowed in the headers property for MCP tools. Use project_connection_id instead.*

So the secret goes in a **project connection** and the agent references it by name. `deploy-mcp.ps1` creates it for you.

Unlike implementation 1, this object now *persists*. It is in the project, versioned, and anyone with access can see it.

#### 3. Send one request

```python
AGENT = {"agent_reference": {"name": agent_name, "type": "agent_reference"}}

# The service rejects an explicit null, so the first turn must omit this.
carry = {"previous_response_id": previous_response_id} if previous_response_id else {}

response = openai_client.responses.create(
    model=model, input=user_input, extra_body=AGENT, **carry,
)
```

That is the whole turn. There is no tool loop here — the service runs it, and the SQL our CLI prints is read back out of `response.output` afterwards, purely so you can see what happened.

`extra_body` is what selects the stored agent — without it you are talking to a bare model deployment, with no instructions and no tools. Note the key is `agent_reference`; the older `agent` key is rejected as deprecated.

Because no part of this is required for the agent to work, the same agent answers in the **portal playground**, from a scheduled routine, or from another agent. That is the real difference between the two implementations.

---

## Security

This is a teaching sample. It is deliberately permissive, and you should not copy its defaults into anything real.

**`--access-mode=unrestricted` means the agent can `DROP TABLE`.** Combined with `approval_mode="never_require"`, an unlucky prompt can destroy data with nobody in the loop. That is an acceptable trade for a disposable demo database and nothing else.

Before pointing an agent at data you care about, apply these in order of effectiveness:

1. **A restricted database role.** `GRANT SELECT, INSERT ON ...` and nothing more. This is the only guardrail an LLM cannot talk its way around.
2. **`POSTGRES_MCP_ACCESS_MODE=restricted`.** The MCP server then permits only read-only transactions.
3. **`POSTGRES_MCP_APPROVAL_MODE=always_require`.** A human approves every statement — see Example 3.
4. **`allowed_tools=[...]`** on `MCPStdioTool`, hiding everything except the calls you want.

On implementation 2 the first three apply unchanged — they are properties of the database and the MCP server, not of the runtime. To restrict the tool list there, sync fewer of them: filter what `discover_tools()` returned before building the definition.

Also:

- `.env` holds a database password and is git-ignored. Keep it that way.
- Prefer Entra ID (`az login`) over `AZURE_OPENAI_API_KEY`; the sample defaults to it.
- The provisioning script opens the firewall to a /24 range for convenience. Narrow it to a single IP for anything long-lived.

---

## Cost and cleanup

A Burstable `Standard_B1ms` server with 32 GB costs roughly **USD 15–20 per month** if you leave it running. Azure OpenAI usage for this sample is a few cents.

If you ran implementation 2, the MCP container app is deployed with `min-replicas 1` so the Agent Service never hits a cold start — which means it bills continuously. Delete it when you are done:

```powershell
az containerapp delete -g rg-maf-postgres-demo -n postgres-mcp --yes
```

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
| `403 ... agents/write` on sync | Implementation 2. You need the **Azure AI User** role on the Foundry project. |
| `421 Misdirected Request` from the MCP endpoint | The MCP SDK's DNS-rebinding protection rejecting the `Host` header. Set `ALLOWED_HOST` on the container app to its own FQDN. |
| `Headers that can include sensitive information are not allowed` | You put the bearer token in the tool's `headers`. Use `project_connection_id` instead. |
| `connection timeout expired` *from the container*, not from you | The database firewall does not allow Container Apps egress. Re-run `infra/deploy-mcp.ps1`. |
| Lots of `INFO Processing request of type ...` | Normal. That is the MCP server logging to stderr. |

---

## Where to go next

- **Add your own tools.** On implementation 1, `tools=` accepts plain Python functions alongside the MCP tool. On implementation 2, add another entry to `tools=` in the definition — a second `MCPTool`, or a `FunctionTool` you handle client-side.
- **Add a second MCP server.** Pass a list to `tools=`. The [Microsoft Learn MCP server](https://learn.microsoft.com/api/mcp) works nicely with `MCPStreamableHTTPTool` — or, on implementation 2, as a native `MCPTool`, since it is already a remote HTTPS endpoint.
- **Replace the bearer token with Entra ID.** The container's auth is a shared secret, which is the weakest part of implementation 2. Put Container Apps auth or API Management in front of it and give the project a managed identity — see [docs/implementations.md](docs/implementations.md).
- **Evaluate it.** A stored Foundry agent can be run through the Foundry evaluation tooling, which is one of the better reasons to pick implementation 2.
- **Vector search.** Enable `pgvector` on the server and give the agent semantic search over alert descriptions — see [AI agents in Azure Database for PostgreSQL](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-agents).
- **Persist conversations.** Agent Framework ships `SessionStore` / `FileSessionStore`; store sessions in Postgres itself.
- **Go multi-agent.** Agent Framework has workflows and orchestrations for agents that hand work to each other.

### References

- [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python)
- [Foundry Agent Service](https://learn.microsoft.com/azure/ai-foundry/agents/overview)
- [AI agents in Azure Database for PostgreSQL flexible server](https://learn.microsoft.com/en-us/azure/postgresql/azure-ai/generative-ai-agents)
- [Postgres MCP Server (crystaldba/postgres-mcp)](https://github.com/crystaldba/postgres-mcp)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Ignite 2025 LAB515 — Advanced AI agents with PostgreSQL](https://github.com/microsoft/ignite25-LAB515-build-advanced-ai-agents-with-postgresql)
- [schneidenbach/using-agent-framework-with-postgres](https://github.com/schneidenbach/using-agent-framework-with-postgres)

## License

MIT.
