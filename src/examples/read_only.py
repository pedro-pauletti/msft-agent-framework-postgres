"""
Example 1 - reading from the database.
================================================================================

    python -m src.examples.read_only

The simplest possible use of the agent: ask questions, get answers backed by
real SQL. Nothing is modified.

Notice that we do not pass a `session=` argument to `agent.run()`. Without a
session each question is completely independent - the agent has no memory of
the previous one. That is exactly what you want for a batch of unrelated
questions, and it is the cheapest option because no history is resent.

Example 2 (`read_write.py`) shows the opposite: a session that remembers.
"""

from __future__ import annotations

import asyncio
import sys

from src.agent import build_agent
from src.config import ConfigError, load_config
from src.preflight import check_database_reachable
from src.trace import print_tool_calls

# Each of these becomes one independent turn. They are deliberately varied:
# a simple count, an aggregation, a join, and an open-ended analytical question.
QUESTIONS = [
    "How many alerts are open, acknowledged and resolved? Give me one small table.",
    (
        "Which critical alerts are open right now? Show the alert id, the link code "
        "and how long the alert has been open."
    ),
    (
        "Which fiber link has raised the most alerts overall? Show the link code and "
        "the names of the two sites it connects."
    ),
    "Among resolved alerts, what was the average time to resolution per severity?",
]


async def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration problem:\n\n{exc}", file=sys.stderr)
        return 1

    # These questions are written for the FiberOps sample data. If the user
    # pointed the sample at their own database, say so rather than letting the
    # agent flounder against a schema that has no alerts in it.
    if config.instructions_file is not None:
        print(
            "NOTE: this example asks questions about the FiberOps sample data,\n"
            f"but AGENT_INSTRUCTIONS_FILE is set to {config.instructions_file.name},\n"
            "so you are probably pointed at your own database. The questions\n"
            "below will not make sense. Use `python -m src.main` instead, or\n"
            "edit QUESTIONS in this file.\n",
            file=sys.stderr,
        )

    # Fail fast if the database is unreachable, instead of waiting ~30 seconds
    # for the MCP server to give up.
    try:
        check_database_reachable(config.postgres)
    except ConfigError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    # `async with` starts the Postgres MCP server as a child process and stops
    # it on exit. Without it the agent has no tools at all.
    async with build_agent(config) as agent:
        for number, question in enumerate(QUESTIONS, start=1):
            print("=" * 78)
            print(f"Q{number}: {question}")
            print("=" * 78)

            # No `session=`, so this turn knows nothing about the previous ones.
            response = await agent.run(question)

            print(f"\n{response.text}\n")

            # Show the SQL the model wrote. This is the interesting part.
            print_tool_calls(response)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
