"""
Interactive chat with the FiberOps agent.
================================================================================

Run it from the repository root:

    python -m src.maf.main

Type a question in any language. Type `/exit` to quit, `/help` for commands.

What this file demonstrates
---------------------------
* Wiring an Agent Framework agent to an MCP server (see `src/maf/agent.py`).
* Keeping conversation state across turns with an `AgentSession`.
* Streaming the answer token by token with `agent.run(..., stream=True)`.
* Showing the SQL the model wrote, so nothing feels like magic.
"""

from __future__ import annotations

import asyncio
import sys

from agent_framework.exceptions import ToolException

from src.common.config import ConfigError
from src.common.preflight import check_database_reachable
from src.common.trace import print_tool_calls
from src.maf.agent import build_agent
from src.maf.config import load_config
from src.maf.mcp_server import explain_startup_failure

BANNER = r"""
==============================================================================
  Postgres Agent
  Microsoft Agent Framework + Postgres MCP Server + Azure OpenAI
==============================================================================
"""

HELP = """
Commands:
  /help     show this help
  /trace    toggle the "what the agent did" SQL trace (currently: {trace})
  /new      start a fresh conversation (clears the agent's memory)
  /exit     quit

Try asking (FiberOps sample data):
  - Which critical alerts are currently open?
  - Show the 5 links with the most alerts in the last 30 days.
  - Add a note to alert 3 saying the field team confirmed a fiber cut.
  - Acknowledge every open alert on the Sao Paulo - Belo Horizonte link.
  - Resolve alert 24 and explain what you changed.
"""

# Some example questions in Portuguese, since the agent answers in whatever
# language you write in.
EXAMPLES_PT = """
Em portugues:
  - Quais alertas criticos estao abertos agora?
  - Quantos alertas cada enlace teve nos ultimos 30 dias?
  - Adicione uma nota no alerta 3 dizendo que a equipe confirmou rompimento.
"""


async def chat() -> int:
    # -------------------------------------------------------------------------
    # 1. Configuration. Fails fast with an actionable message if .env is wrong.
    # -------------------------------------------------------------------------
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"\nConfiguration problem:\n\n{exc}\n", file=sys.stderr)
        return 1

    print(BANNER)
    print(f"Model      : {config.azure_openai.deployment}")
    print(f"Endpoint   : {config.azure_openai.endpoint}")
    print(f"Auth       : {'Entra ID (az login)' if config.azure_openai.uses_entra_id else 'API key'}")
    print(f"Database   : {config.postgres.host}/{config.postgres.database}")
    print(f"MCP access : {config.mcp.access_mode}  (approval: {config.mcp.approval_mode})")

    # Make it obvious which prompt is in play - otherwise "why does it keep
    # talking about fiber alerts?" is a confusing first experience for anyone
    # who pointed the sample at their own database.
    if config.instructions_file is not None:
        print(f"Prompt     : {config.instructions_file.name}")
    else:
        print("Prompt     : built-in FiberOps sample")
        print("             (using your own database? see docs/bring-your-own.md)")

    if config.mcp.access_mode == "unrestricted":
        print("\n  WARNING: the agent can INSERT, UPDATE, DELETE and run DDL on this")
        print("           database. Only point it at data you can afford to lose.")
    print(HELP.format(trace="on"))
    if config.instructions_file is None:
        print(EXAMPLES_PT)

    # Check the database before spawning the MCP server. Without this, an
    # unreachable database means ~30 seconds of silence and stderr noise before
    # anything happens, which looks exactly like a frozen program.
    print("Checking database connection... ", end="", flush=True)
    try:
        check_database_reachable(config.postgres)
    except ConfigError as exc:
        print("FAILED\n", flush=True)
        print(f"{exc}\n", file=sys.stderr)
        return 1
    print("ok", flush=True)

    # Starting the MCP server takes a few seconds, and on the very first run
    # `uvx` downloads it, which takes longer. Say so, otherwise the wait looks
    # like a hang.
    print("Starting the Postgres MCP server (first run downloads it)...", flush=True)

    show_trace = True

    # -------------------------------------------------------------------------
    # 2. The agent. `async with` is what starts the Postgres MCP child process;
    #    leaving the block shuts it down cleanly.
    # -------------------------------------------------------------------------
    try:
        async with build_agent(config) as agent:
            # An AgentSession is the conversation memory. Pass the same session
            # to every `run` call and the agent remembers earlier turns, so
            # follow-ups like "now resolve that one" work.
            session = agent.create_session()

            print("\nConnected. Ask me something (/exit to quit).\n", flush=True)

            while True:
                try:
                    # Flush first: the MCP server logs to stderr, and on a busy
                    # terminal the prompt can otherwise get buried in its output.
                    sys.stdout.flush()
                    user_input = input("you > ").strip()
                except EOFError:
                    # stdin closed. Usually means the CLI was run without a
                    # terminal - piped input, a CI job, or some IDE consoles.
                    print(
                        "\n[stdin is not interactive, so there is nothing to read]\n"
                        "Run this from a real terminal:\n"
                        "  python -m src.maf.main\n"
                        "Or try the scripted examples instead:\n"
                        "  python -m src.maf.examples.read_only",
                        file=sys.stderr,
                    )
                    break
                except KeyboardInterrupt:
                    print()
                    break

                if not user_input:
                    continue

                # --- slash commands -------------------------------------------
                lowered = user_input.lower()
                if lowered in {"/exit", "/quit"}:
                    break
                if lowered == "/help":
                    print(HELP.format(trace="on" if show_trace else "off"))
                    continue
                if lowered == "/trace":
                    show_trace = not show_trace
                    print(f"SQL trace is now {'on' if show_trace else 'off'}.\n")
                    continue
                if lowered == "/new":
                    session = agent.create_session()
                    print("Started a new conversation. The agent has forgotten the previous turns.\n")
                    continue

                # --- one turn --------------------------------------------------
                print("\nagent > ", end="", flush=True)
                try:
                    # `stream=True` returns a ResponseStream. Iterating it yields
                    # partial updates; `get_final_response()` afterwards gives us
                    # the complete transcript, including the tool calls.
                    stream = agent.run(user_input, session=session, stream=True)

                    async for update in stream:
                        if update.text:
                            print(update.text, end="", flush=True)

                    print()

                    if show_trace:
                        response = await stream.get_final_response()
                        print_tool_calls(response)
                    else:
                        print()

                except KeyboardInterrupt:
                    print("\n[cancelled]\n")
                except Exception as exc:  # noqa: BLE001 - a REPL should not die
                    print(f"\n\n[error] {type(exc).__name__}: {exc}\n")
                    print("See docs/troubleshooting.md if this keeps happening.\n")

    except ConfigError as exc:
        print(f"\nConfiguration problem:\n\n{exc}\n", file=sys.stderr)
        return 1
    except ToolException as exc:
        print(f"\n{explain_startup_failure(exc)}\n", file=sys.stderr)
        return 1

    print("Bye.")
    return 0


def main() -> int:
    # Windows terminals still default to a legacy code page, which mangles
    # accented characters (and the agent answers in whatever language you use).
    # Forcing UTF-8 on the streams avoids that. No-op on other platforms.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    try:
        return asyncio.run(chat())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
