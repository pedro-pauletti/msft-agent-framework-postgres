"""
Example 3 - human-in-the-loop approval (the "graduation" step).
================================================================================

    python -m src.maf.examples.human_approval

Examples 1 and 2 run with `approval_mode="never_require"`: whatever SQL the
model writes is executed immediately. That is fine for a throwaway demo
database. It is *not* fine for anything you care about - an LLM that can run
arbitrary SQL can also run `DROP TABLE`.

This example flips the switch to `approval_mode="always_require"`. Now every
single tool call comes back to your code first, and nothing touches the database
until you say yes.

How the protocol works
----------------------

    await agent.run(question, session=session)
        |
        +--> response.text is empty
        +--> response.user_input_requests contains one item per pending call

    for each request:
        request.function_call.name        -> e.g. 'execute_sql'
        request.function_call.arguments   -> e.g. '{"sql": "SELECT ..."}'
        request.to_function_approval_response(approved=True|False)

    await agent.run(Message(role="user", contents=approvals), session=session)
        |
        +--> approved calls execute, and you get the real answer

The loop repeats until `user_input_requests` comes back empty, because a single
question can require several rounds of tool calls.

Note that the `session` is mandatory here: approval state lives in the session.

Other guardrails worth knowing
------------------------------
* `POSTGRES_MCP_ACCESS_MODE=restricted` - the MCP server refuses anything that
  is not a read-only transaction. Belt and braces alongside approvals.
* `MCPStdioTool(..., allowed_tools=["execute_sql", "list_objects"])` - hides
  every other tool from the model entirely.
* A dedicated PostgreSQL role with `GRANT SELECT, INSERT` only. Database
  permissions are the guardrail an LLM genuinely cannot argue its way past.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from typing import Any

from agent_framework import Message
from agent_framework.exceptions import ToolException

from src.common.config import ConfigError
from src.common.preflight import check_database_reachable
from src.maf.agent import build_agent
from src.maf.config import load_config
from src.maf.mcp_server import explain_startup_failure

# A deliberately destructive-sounding request, so that saying "no" is meaningful.
QUESTION = (
    "Delete every resolved alert older than 7 days. Tell me how many rows you "
    "removed."
)

MAX_ROUNDS = 6  # A safety net so a confused agent cannot loop forever.


def _describe(request: Any) -> str:
    """Render a pending tool call so a human can make an informed decision."""
    call = request.function_call
    name = getattr(call, "name", "<unknown tool>")

    arguments = getattr(call, "arguments", None)
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            pass

    # `execute_sql` is the call that matters, so show the SQL prominently.
    if isinstance(arguments, dict) and "sql" in arguments:
        sql = str(arguments["sql"]).strip()
        indented = "\n".join(f"      {line}" for line in sql.splitlines())
        return f"  tool: {name}\n  SQL:\n{indented}"

    return f"  tool: {name}\n  arguments: {json.dumps(arguments, default=str)}"


def _ask_human(request: Any) -> bool:
    """Prompt on the terminal. Anything other than 'y' is a refusal."""
    print("\n" + "-" * 78)
    print("APPROVAL REQUIRED - the agent wants to run this:")
    print(_describe(request))
    print("-" * 78)
    answer = input("Allow it? [y/N] ").strip().lower()
    return answer in {"y", "yes", "s", "sim"}


async def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration problem:\n\n{exc}", file=sys.stderr)
        return 1

    # The question below is a DELETE against the FiberOps sample tables. Do not
    # even offer it against someone's real database.
    if config.instructions_file is not None:
        print(
            "This example asks the agent to DELETE from the FiberOps sample\n"
            f"tables, but AGENT_INSTRUCTIONS_FILE is set to "
            f"{config.instructions_file.name},\n"
            "so you are probably pointed at your own database. Refusing to run.\n\n"
            "To see the approval flow against your own data, change QUESTION in\n"
            "this file to something harmless first.",
            file=sys.stderr,
        )
        return 1

    # Force approvals on regardless of what .env says. `AppConfig` is a frozen
    # dataclass, so `dataclasses.replace` gives us a modified copy.
    config = replace(config, mcp=replace(config.mcp, approval_mode="always_require"))

    # Fail fast if the database is unreachable, instead of waiting ~30 seconds
    # for the MCP server to give up.
    try:
        check_database_reachable(config.postgres)
    except ConfigError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    print("=" * 78)
    print("Human-in-the-loop mode: every tool call needs your approval.")
    print("=" * 78)
    print(f"\nQuestion: {QUESTION}\n")

    try:
        async with build_agent(config) as agent:
            # Approval state is stored in the session, so a session is required.
            session = agent.create_session()

            # The first turn is the user's question. Subsequent turns carry approval
            # decisions instead.
            next_input: str | Message = QUESTION

            for round_number in range(1, MAX_ROUNDS + 1):
                response = await agent.run(next_input, session=session)

                pending = list(response.user_input_requests)
                if not pending:
                    # No approvals outstanding: this is the final answer.
                    print(f"\nagent > {response.text}\n")
                    break

                print(f"\n[round {round_number}] {len(pending)} call(s) awaiting approval")

                decisions = []
                for request in pending:
                    approved = _ask_human(request)
                    print("  -> approved" if approved else "  -> denied")
                    # Turn the request into a response the agent understands.
                    decisions.append(request.to_function_approval_response(approved))

                # Feed the decisions back as the next user message.
                next_input = Message(role="user", contents=decisions)
            else:
                print(
                    f"\nStopped after {MAX_ROUNDS} approval rounds without a final "
                    "answer. That usually means the agent kept retrying calls you "
                    "denied.",
                    file=sys.stderr,
                )
                return 1
    except ToolException as exc:
        print(f"\n{explain_startup_failure(exc)}\n", file=sys.stderr)
        return 1

    print("Tip: run this again and answer 'n' to see how the agent reacts to a refusal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
