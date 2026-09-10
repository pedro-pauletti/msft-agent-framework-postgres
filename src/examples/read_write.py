"""
Example 2 - writing to the database, with proof that it stuck.
================================================================================

    python -m src.examples.read_write

This is the example that shows the whole point of the sample. The agent:

  1. reads   - finds an open critical alert,
  2. writes  - adds a technician note and acknowledges the alert,
  3. reads   - re-reads the same rows so you can see the change persisted.

Two ideas worth paying attention to
-----------------------------------

**AgentSession.** All four turns share one `session`, so the agent remembers
which alert it picked in step 1. That is what makes step 2 ("add a note to
*that* alert") work without us repeating the id.

**Nothing here is deterministic.** We never tell the agent which SQL to run or
which alert to choose. We describe an outcome and let it work out the
statements. Run it twice and it may pick a different alert - that is normal, and
it is why step 4 exists.

Safety
------
This example writes to `alert_notes` and updates `fiber_alerts.status`. Both are
easy to undo: re-run `python -m infra.seed` to rebuild the sample data from
scratch.
"""

from __future__ import annotations

import asyncio
import sys

from src.agent import build_agent
from src.config import ConfigError, load_config
from src.preflight import check_database_reachable
from src.trace import print_tool_calls

# The four steps of the walkthrough. Each is one turn in the same conversation.
STEPS: list[tuple[str, str]] = [
    (
        "1. READ - pick a target",
        (
            "Find the oldest alert that is currently open with severity 'critical'. "
            "Tell me its alert id, its type, the link code and the two sites the "
            "link connects. Do not change anything yet."
        ),
    ),
    (
        "2. WRITE - add a note",
        (
            "Add a note to that alert saying: 'Field team dispatched. OTDR "
            "measurement scheduled, awaiting duct access permission.' "
            "Use author 'ai-agent'. Tell me the id of the note you created."
        ),
    ),
    (
        "3. WRITE - change the alert status",
        (
            "Now set that same alert's status to 'acknowledged'. Tell me exactly how "
            "many rows you updated."
        ),
    ),
    (
        "4. READ - prove it persisted",
        (
            "Re-read that alert from the database and list all of its notes, oldest "
            "first. Show the current status so I can confirm the update landed."
        ),
    ),
]


async def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration problem:\n\n{exc}", file=sys.stderr)
        return 1

    if config.mcp.access_mode != "unrestricted":
        print(
            "This example needs POSTGRES_MCP_ACCESS_MODE=unrestricted in .env.\n"
            f"It is currently '{config.mcp.access_mode}', which only allows "
            "read-only transactions, so steps 2 and 3 would fail.",
            file=sys.stderr,
        )
        return 1

    # This walkthrough writes to the FiberOps sample tables. Refuse to run it
    # against someone's real database, where "add a note to the oldest critical
    # alert" is at best meaningless and at worst destructive.
    if config.instructions_file is not None:
        print(
            "This example writes to the FiberOps sample tables (alert_notes,\n"
            f"fiber_alerts), but AGENT_INSTRUCTIONS_FILE is set to "
            f"{config.instructions_file.name},\n"
            "so you are probably pointed at your own database. Refusing to run.\n\n"
            "To try the write walkthrough against your own data, edit STEPS in\n"
            "this file first. Or just use `python -m src.main`.",
            file=sys.stderr,
        )
        return 1

    # Fail fast if the database is unreachable, instead of waiting ~30 seconds
    # for the MCP server to give up.
    try:
        check_database_reachable(config.postgres)
    except ConfigError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    async with build_agent(config) as agent:
        # ONE session shared by every step. This is the agent's short-term
        # memory: without it, step 2 would not know what "that alert" means.
        session = agent.create_session()

        for title, prompt in STEPS:
            print("=" * 78)
            print(title)
            print("=" * 78)
            print(f"> {prompt}\n")

            response = await agent.run(prompt, session=session)

            print(f"{response.text}\n")
            print_tool_calls(response)

    print("Done. Run `python -m infra.seed` to reset the sample data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
