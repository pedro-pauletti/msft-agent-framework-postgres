"""
Interactive chat with the Foundry-hosted agent.
================================================================================

    python -m src.foundry.main

Run `pwsh infra/deploy-mcp.ps1` and `python -m src.foundry.sync` first.

Compare this file with `src/maf/main.py`. Both are a read-eval-print loop, but
look at what is missing here:

* no MCP server to start,
* no database connection to check,
* no tool loop - the Agent Service calls the MCP server itself.

One HTTPS request per turn, and the answer comes back. That is the practical
consequence of the agent living in the project rather than in this process: this
CLI is only one of the ways to reach it. The portal playground, a routine, or
another agent all get the same behaviour without running any of this code.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError

from src.common.config import ConfigError
from src.common.trace import ToolCall, print_calls
from src.foundry.agent import build_project_client
from src.foundry.config import load_foundry_app_config

BANNER = r"""
==============================================================================
  Postgres Agent
  Microsoft Foundry Agent Service + remote Postgres MCP Server
==============================================================================
"""

HELP = """
Commands:
  /help     show this help
  /trace    toggle the "what the agent did" SQL trace (currently: {trace})
  /new      start a fresh conversation (drops the response chain)
  /exit     quit

Try asking (FiberOps sample data):
  - Which critical alerts are currently open?
  - Show the 5 links with the most alerts in the last 30 days.
  - Is my database missing any indexes for that query?
"""


def chat() -> int:
    try:
        config = load_foundry_app_config()
    except ConfigError as exc:
        print(f"\nConfiguration problem:\n\n{exc}\n", file=sys.stderr)
        return 1

    print(BANNER)
    print(f"Project    : {config.foundry.project_endpoint}")
    print(f"Agent      : {config.foundry.agent_name}")
    print(f"Model      : {config.foundry.model_deployment}")
    print(f"MCP server : {config.foundry.mcp_server_url}")
    print(
        "Prompt     : "
        + (
            config.instructions_file.name
            if config.instructions_file is not None
            else "built-in FiberOps sample"
        )
    )
    print(HELP.format(trace="on"))

    show_trace = True
    previous_response_id: str | None = None

    with build_project_client(config.foundry) as project:
        openai_client = project.get_openai_client()

        print("Connected. Ask me something (/exit to quit).\n", flush=True)

        while True:
            try:
                sys.stdout.flush()
                user_input = input("you > ").strip()
            except EOFError:
                print(
                    "\n[stdin is not interactive, so there is nothing to read]\n"
                    "Run this from a real terminal:\n"
                    "  python -m src.foundry.main",
                    file=sys.stderr,
                )
                break
            except KeyboardInterrupt:
                print()
                break

            if not user_input:
                continue

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
                previous_response_id = None
                print("Started a new conversation.\n")
                continue

            try:
                response = run_turn(
                    openai_client,
                    agent_name=config.foundry.agent_name,
                    model=config.foundry.model_deployment,
                    user_input=user_input,
                    previous_response_id=previous_response_id,
                )
            except KeyboardInterrupt:
                print("\n[cancelled]\n")
                continue
            except ClientAuthenticationError as exc:
                print(f"\n[auth error] {exc}\nRun `az login` and try again.\n")
                continue
            except HttpResponseError as exc:
                print(f"\n[error] {exc}\n")
                print("See docs/implementations.md if this keeps happening.\n")
                continue
            except Exception as exc:  # noqa: BLE001 - a REPL should not die
                print(f"\n[error] {type(exc).__name__}: {exc}\n")
                continue

            previous_response_id = response.id
            print(f"\nagent > {response.output_text}\n")
            if show_trace:
                print_calls(extract_mcp_calls(response))

    print("Bye.")
    return 0


def run_turn(
    openai_client: Any,
    *,
    agent_name: str,
    model: str,
    user_input: str,
    previous_response_id: str | None,
) -> Any:
    """One turn. One request.

    `extra_body` is what selects the stored agent rather than a bare model
    deployment - the instructions and the tool come from the definition in the
    project, so we never resend them. Note the key is `agent_reference`; the
    older `agent` key is rejected as deprecated.
    """
    # On the first turn there is nothing to chain to, and the service rejects an
    # explicit null - the parameter has to be absent, not empty.
    carry = {"previous_response_id": previous_response_id} if previous_response_id else {}

    return openai_client.responses.create(
        model=model,
        input=user_input,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
        **carry,
    )


def extract_mcp_calls(response: Any) -> list[ToolCall]:
    """Pull the MCP tool calls out of a response so the SQL stays visible.

    The Agent Service executed these remotely, but it still reports them, which
    is what keeps this implementation as auditable as the local one.
    """
    calls: list[ToolCall] = []
    for item in getattr(response, "output", None) or []:
        if "mcp" not in str(getattr(item, "type", "")):
            continue
        name = getattr(item, "name", None)
        if not name:
            continue
        calls.append(
            ToolCall(
                name=name,
                arguments=_parse_arguments(getattr(item, "arguments", None)),
                result=_as_text(getattr(item, "output", None) or getattr(item, "error", None)),
            )
        )
    return calls


def _parse_arguments(arguments: Any) -> dict[str, Any] | str:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments
    return parsed if isinstance(parsed, dict) else arguments


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value, default=str)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    try:
        return chat()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
