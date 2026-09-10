"""
Showing what the agent actually did.
================================================================================

The whole point of this sample is that *the model writes the SQL*. That is only
convincing if you can see the SQL, so these helpers dig the tool calls out of an
`AgentResponse` and print them in a readable form.

An `AgentResponse` contains the full transcript of one turn in
`response.messages`. A turn that used a tool looks like this:

    Message(role='assistant', contents=[<call: execute_sql, arguments={...}>])
    Message(role='tool',      contents=[<result: '[{"count": 10}]'>])
    Message(role='assistant', contents=[<the natural-language answer>])

Content objects are duck-typed here rather than isinstance-checked, so this
keeps working if Agent Framework reorganises its content classes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# ANSI colours. Kept tiny and dependency-free.
DIM = "\033[2m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RESET = "\033[0m"


@dataclass
class ToolCall:
    """One tool invocation and, when available, its result."""

    name: str
    arguments: dict[str, Any] | str
    result: str | None = None

    @property
    def sql(self) -> str | None:
        """The SQL text, when this was a `execute_sql`-style call.

        The Postgres MCP server takes the statement in an argument called `sql`.
        """
        if isinstance(self.arguments, dict):
            value = self.arguments.get("sql")
            if isinstance(value, str):
                return value.strip()
        return None


def extract_tool_calls(response: Any) -> list[ToolCall]:
    """Pull every tool call (and matching result) out of an `AgentResponse`."""
    calls: dict[str, ToolCall] = {}
    order: list[str] = []

    for message in getattr(response, "messages", []) or []:
        for content in getattr(message, "contents", []) or []:
            call_id = getattr(content, "call_id", None)
            if not call_id:
                continue

            name = getattr(content, "name", None)
            if name:
                # This content is the *request*: the model asking for a tool.
                arguments = getattr(content, "arguments", None)
                calls[call_id] = ToolCall(name=name, arguments=_parse_arguments(arguments))
                order.append(call_id)
                continue

            result = getattr(content, "result", None)
            if result is not None and call_id in calls:
                # This content is the *response* coming back from the tool.
                calls[call_id].result = _shorten(_clean_result(str(result)))

    return [calls[cid] for cid in order if cid in calls]


def _parse_arguments(arguments: Any) -> dict[str, Any] | str:
    """Tool arguments arrive as a JSON string. Decode when we can."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
        return parsed if isinstance(parsed, dict) else arguments
    return {}


def _clean_result(text: str) -> str:
    """Drop the raw MCP envelope from a tool result.

    The MCP server returns its payload twice: once as plain text, and once
    wrapped in a `{"result": [{"type": "text", ...}]}` JSON envelope. Showing
    both doubles the noise for zero extra information, so we keep only the
    human-readable part.
    """
    lines = [line for line in text.splitlines() if not line.lstrip().startswith('{"result":')]
    return "\n".join(lines).strip() or text.strip()


def _shorten(text: str, limit: int = 600) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f" ... [{len(text)} chars total]"


def print_tool_calls(response: Any, *, show_results: bool = True, colour: bool = True) -> None:
    """Print a readable trace of everything the agent did to the database."""
    calls = extract_tool_calls(response)
    if not calls:
        print(_c("(the agent answered without touching the database)", DIM, colour))
        return

    print(_c(f"\n--- what the agent did ({len(calls)} tool call(s)) ---", DIM, colour))
    for index, call in enumerate(calls, start=1):
        print(_c(f"[{index}] {call.name}", CYAN, colour))

        sql = call.sql
        if sql:
            # Indent the SQL so multi-line statements stay readable.
            for line in sql.splitlines():
                print(f"    {_c(line, YELLOW, colour)}")
        else:
            print(f"    {_c(json.dumps(call.arguments, default=str), YELLOW, colour)}")

        if show_results and call.result:
            for line in call.result.splitlines():
                print(f"    {_c('-> ' + line, DIM, colour)}")
    print(_c("--- end of trace ---\n", DIM, colour))


def _c(text: str, code: str, colour: bool) -> str:
    return f"{code}{text}{RESET}" if colour else text
