# Troubleshooting

*[Leia em portugues](troubleshooting.pt-BR.md)*

Every error below is one we actually hit while building this sample, together with the fix.

---

## Setup and dependencies

### `ERROR: No matching distribution found for agent-framework-openai>=1.17`

`agent-framework-core` and `agent-framework-openai` are **versioned independently**. At the time of writing, core is at 1.17 while the OpenAI connector is at 1.14 — and 1.14 already requires core >= 1.17. The mismatched numbers are expected.

Use the ranges in `requirements.txt`:

```
agent-framework-core>=1.17,<2
agent-framework-openai>=1.14,<2
```

### `ModuleNotFoundError: The package agent-framework-openai is required to use OpenAIChatClient`

`agent-framework-core` on its own does not include the OpenAI connector, despite what some documentation implies.

```powershell
pip install agent-framework-openai
```

### `uvx` was not found on your PATH

`uvx` ships with [uv](https://docs.astral.sh/uv/):

```powershell
# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your terminal afterwards so `PATH` picks it up.

---

## MCP server

### `MCP server 'uvx postgres-mcp ...' failed to initialize: Connection closed`

The single most confusing error in this stack. The MCP client reports only that the child process went away; the real error was printed by the child before it died.

**The usual cause: the MCP SDK version.** `postgres-mcp` 0.3.x is built against v1 of the MCP Python SDK. v2 renamed `FastMCP` to `MCPServer`, so on v2 the server dies at import with:

```
ModuleNotFoundError: No module named 'mcp.server.fastmcp'
```

`uvx` resolves the newest compatible version by default, which is now v2. The fix is the pin this repo already uses:

```python
args=["--with", "mcp<2", "postgres-mcp", "--access-mode=unrestricted"]
```

**To see the real error yourself,** run the server by hand:

```powershell
$env:DATABASE_URI = "postgresql://user:pass@host:5432/db?sslmode=require"
uvx --with "mcp<2" postgres-mcp --access-mode=unrestricted
```

It should print `Starting PostgreSQL MCP Server in UNRESTRICTED mode` and then wait for input. Anything else is your real problem. Press `Ctrl+C` to exit.

**Other causes:** `uvx` not on PATH; a malformed `DATABASE_URI`; no network access to PyPI on the very first run.

### The agent says it has no way to access the database

You forgot the async context manager. This is what starts the MCP child process:

```python
# WRONG - no tools are ever loaded
agent = build_agent(config)
result = await agent.run("...")

# RIGHT
async with build_agent(config) as agent:
    result = await agent.run("...")
```

### Lots of `INFO Processing request of type CallToolRequest` in my terminal

That is normal. `postgres-mcp` logs to stderr, and stderr is inherited by your process. It is arguably useful — you can watch the tool calls happen live.

To silence it, redirect stderr:

```powershell
python -m src.main 2>$null          # PowerShell
python -m src.main 2>/dev/null      # bash
```

### The agent refuses to modify data

Check `.env`:

```
POSTGRES_MCP_ACCESS_MODE=unrestricted
```

In `restricted` mode the MCP server only permits read-only transactions, and writes fail with a permission error from the server rather than from PostgreSQL.

---

## Azure OpenAI

### `400 BadRequest: API version not supported`

Agent Framework's `OpenAIChatClient` uses the Azure OpenAI **Responses API**. Older `api-version` values do not support it. We confirmed:

| `AZURE_OPENAI_API_VERSION` | Result |
|---|---|
| *(empty)* | works |
| `preview` | works |
| `v1` | works |
| `2025-04-01-preview` | fails |
| `2024-10-21` | fails |

**Leave `AZURE_OPENAI_API_VERSION` empty** and let the SDK choose.

### `401 Unauthorized` or `PermissionDenied`

With Entra ID you need the **Cognitive Services OpenAI User** role on the Azure OpenAI resource — being subscription Owner is not sufficient by itself.

```powershell
az role assignment create `
  --assignee "$(az ad signed-in-user show --query id -o tsv)" `
  --role "Cognitive Services OpenAI User" `
  --scope "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<resource>"
```

Then `az login` again so the new role lands in a fresh token. Role assignments can take a couple of minutes to propagate.

### `DeploymentNotFound` / `The API deployment for this resource does not exist`

`AZURE_OPENAI_DEPLOYMENT` must be the **deployment name**, not the model name. List yours:

```powershell
az cognitiveservices account deployment list `
  -g <resource-group> -n <resource-name> `
  --query "[].{deployment:name, model:properties.model.name}" -o table
```

### My calls go to public OpenAI instead of Azure

`OpenAIChatClient` resolves configuration in this order:

1. Explicit Azure inputs (`credential=` or `azure_endpoint=`)
2. `OPENAI_API_KEY`
3. Azure environment fallback (`AZURE_OPENAI_ENDPOINT`, ...)

If `OPENAI_API_KEY` is exported in your shell and you did not pass an explicit Azure input, you silently hit public OpenAI. This sample always passes `azure_endpoint=`, so it is safe — but `src/config.py` prints a note when it sees that variable, and you should watch for the same trap in your own code.

### `AZURE_OPENAI_ENDPOINT looks like a Foundry project endpoint`

A Foundry resource exposes several endpoints. You need the Azure OpenAI one:

- Correct: `https://<name>.openai.azure.com/`
- Wrong here: `https://<name>.services.ai.azure.com/`

---

## PostgreSQL

### `connection timeout expired`

Almost always the firewall, and almost always because the rule holds the wrong IP.

IP-lookup services such as `api.ipify.org` report the address of whatever proxy your HTTPS traffic goes through. On corporate networks, VPNs and cloud dev boxes, that is frequently **not** the address PostgreSQL sees on a raw TCP connection. We hit exactly this while building the sample: the lookup service reported one address, PostgreSQL saw a different one in the same /24.

Re-running the provisioning script fixes it, because it asks PostgreSQL directly:

```powershell
pwsh infra/provision.ps1 -ServerName <your-server>
```

To check by hand:

```powershell
# 1. open temporarily
az postgres flexible-server firewall-rule create -g <rg> -n <server> `
  --rule-name Temp --start-ip-address 0.0.0.0 --end-ip-address 255.255.255.255

# 2. ask PostgreSQL which address it sees
python -c "import psycopg,os;from dotenv import load_dotenv;load_dotenv();c=psycopg.connect(f\"postgresql://{os.environ['PGUSER']}:{os.environ['PGPASSWORD']}@{os.environ['PGHOST']}:5432/{os.environ['PGDATABASE']}?sslmode=require\");cur=c.cursor();cur.execute('SELECT host(inet_client_addr())');print(cur.fetchone()[0])"

# 3. create a narrow rule for that address, then delete Temp
```

Other possibilities: the server is stopped (`az postgres flexible-server start -g <rg> -n <server>`), or outbound port 5432 is blocked on your network. Check the latter with:

```powershell
Test-NetConnection -ComputerName portquiz.net -Port 5432
```

If that fails, the block is local, not Azure.

### `ERROR: The location is restricted from performing this operation`

Your subscription cannot create PostgreSQL flexible servers in that region. This is common on trial, sponsored and MCAP subscriptions, and it is independent of quota.

The provisioning scripts detect it and suggest working regions. To check manually:

```powershell
$sub = az account show --query id -o tsv
az rest --method get --url "https://management.azure.com/subscriptions/$sub/providers/Microsoft.DBforPostgreSQL/locations/eastus/capabilities?api-version=2024-08-01" --query "value[0].reason" -o tsv
```

An empty result means the region is available. Then:

```powershell
pwsh infra/provision.ps1 -Location centralus
```

### `ERROR: incorrect usage: --database-name can only be used when --cluster-option is set to ElasticCluster`

Newer Azure CLI versions removed `--database-name` from `flexible-server create`. Create the database as a separate step (which is what `infra/provision.ps1` does):

```powershell
az postgres flexible-server db create -g <rg> -s <server> -d fiberops
```

### `sslmode` errors, or connections rejected for no obvious reason

Azure Database for PostgreSQL requires TLS. Keep `PGSSLMODE=require` in `.env`. `disable` will not work, and the resulting error is not helpful.

### I re-ran the provisioning script and now the password is wrong

Azure never returns an admin password. On a re-run the script recovers it from your existing `.env`; if there is no `.env`, it resets the password on the server and writes the new one.

To set it explicitly:

```powershell
pwsh infra/provision.ps1 -ServerName <server> -AdminPassword '<known-password>'
```

---

## Behaviour

### The agent talks about fiber alerts, but that is not my database

You pointed `PGHOST`/`PGDATABASE` at your own database but left the system prompt alone, so the agent is still working from the built-in FiberOps schema description.

Set `AGENT_INSTRUCTIONS_FILE` in `.env`:

```ini
AGENT_INSTRUCTIONS_FILE=prompts/auto-discover-schema.md
```

The CLI prints which prompt is active on startup. See [docs/bring-your-own.md](bring-your-own.md).

### `AGENT_INSTRUCTIONS_FILE points to a file that does not exist`

The path is resolved relative to the repository root, not your current directory. So `prompts/my-database.md` is correct, `./my-database.md` usually is not. Ready-made starting points live in `prompts/`.

### An example refuses to run: "you are probably pointed at your own database"

Working as intended. `read_write.py` and `human_approval.py` write to (and delete from) the FiberOps sample tables. When `AGENT_INSTRUCTIONS_FILE` is set they assume you are on your own data and stop.

Use `python -m src.main`, or edit the `STEPS` / `QUESTION` constants in those files first.

### The interactive CLI seems frozen and will not let me type

Two different causes, and the fix tells you which:

**The database is unreachable.** Before this check existed, the CLI would spawn
the MCP server, which retried the connection for ~30 seconds while flooding the
terminal with warnings — and the `you >` prompt never appeared. It looked
exactly like a hang.

`python -m src.main` now verifies the connection first and fails in a few
seconds with an explanation. If you see `Checking database connection... FAILED`,
read the paragraph underneath — it is almost always the firewall (see
[`connection timeout expired`](#connection-timeout-expired)).

**stdin is not a terminal.** If you piped input, or your IDE console does not
forward stdin, `input()` gets EOF immediately and the CLI prints:

```
[stdin is not interactive, so there is nothing to read]
```

Run it from a real terminal, or use the scripted examples
(`python -m src.examples.read_only`) which need no input.

### It pauses for a long time on the very first run

Expected once. `uvx` downloads and caches the Postgres MCP server the first
time, which takes 10–30 seconds depending on your connection. The CLI prints
`Starting the Postgres MCP server (first run downloads it)...` so you know it
is working. Later runs start in about a second.

### The agent writes bad SQL, or invents column names

Look at `FIBEROPS_INSTRUCTIONS` in `src/agent.py`. If you changed `infra/seed.sql`, the prompt is now out of date — they must be kept in sync.

Also make sure your deployment is a tool-calling capable model. `gpt-4.1`, `gpt-4.1-mini` and `gpt-4o` all work well.

### The agent gives a different answer each run

Expected. We never tell it which SQL to write, only what outcome we want. `src/examples/read_write.py` is written to tolerate this: step 4 re-reads whatever step 1 picked.

### Accented characters look wrong on Windows

`src/main.py` forces UTF-8 on stdout and stderr. If you are running your own script instead, do the same:

```python
sys.stdout.reconfigure(encoding="utf-8")
```

### I want to start over from clean sample data

```powershell
python -m infra.seed
```

It drops and recreates every table.

---

## Still stuck?

Confirm each layer independently:

```powershell
# 1. Azure sign-in
az account show

# 2. Database reachable and seeded
python -m infra.seed

# 3. MCP server starts on its own
$env:DATABASE_URI = "postgresql://user:pass@host:5432/db?sslmode=require"
uvx --with "mcp<2" postgres-mcp --access-mode=unrestricted   # Ctrl+C to exit

# 4. Everything together
python -m src.examples.read_only
```

Step 4 exercises every layer at once, and the traceback tells you which one failed.
