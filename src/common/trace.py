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

    @property
    def code(self) -> str | None:
        """The Python the code interpreter ran, when this was that tool."""
        if isinstance(self.arguments, dict):
            value = self.arguments.get("code")
            if isinstance(value, str):
                return value.strip()
        return None


CODE_INTERPRETER = "code_interpreter"


def extract_tool_calls(response: Any) -> list[ToolCall]:
    """Pull every tool call (and matching result) out of an `AgentResponse`."""
    calls: dict[str, ToolCall] = {}
    order: list[str] = []

    for message in getattr(response, "messages", []) or []:
        for content in getattr(message, "contents", []) or []:
            if _collect_code_interpreter(content, calls, order):
                continue

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


def _collect_code_interpreter(
    content: Any,
    calls: dict[str, ToolCall],
    order: list[str],
) -> bool:
    """Record a hosted code interpreter run. Returns True when it handled one.

    Hosted tools do not look like function calls: there is no `name` and no
    `result`, just a pair of contents typed `code_interpreter_tool_call` and
    `code_interpreter_tool_result`. The Python the model wrote is not on the
    content itself either - it is on the provider object underneath.
    """
    content_type = str(getattr(content, "type", ""))
    if CODE_INTERPRETER not in content_type:
        return False

    call_id = getattr(content, "call_id", None)
    if not call_id:
        return True

    raw = getattr(content, "raw_representation", None)
    code = _code_from_raw(raw)

    if call_id not in calls:
        calls[call_id] = ToolCall(name=CODE_INTERPRETER, arguments={"code": code or ""})
        order.append(call_id)
    elif code and not calls[call_id].arguments.get("code"):
        calls[call_id].arguments = {"code": code}

    outputs = getattr(content, "outputs", None) or getattr(raw, "outputs", None)
    if outputs:
        calls[call_id].result = _shorten(_clean_result(str(outputs)))

    return True


def _code_from_raw(raw: Any) -> str | None:
    """Dig the generated Python out of the provider object.

    Streaming and non-streaming disagree about shape. Awaiting the whole
    response gives one object with `.code`; streaming gives the list of events
    that produced it, where the final `...code.done` event carries the full
    text and the ones before it carry fragments.
    """
    if raw is None:
        return None

    if not isinstance(raw, list):
        return getattr(raw, "code", None)

    for event in reversed(raw):
        code = getattr(event, "code", None)
        if code:
            return code

    fragments = [getattr(event, "delta", "") or "" for event in raw]
    return "".join(fragments) or None


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


def clean_tool_result(text: str) -> str:
    """Make a raw tool result readable: drop the MCP envelope, then truncate.

    Callers that collect tool calls themselves - `src/foundry/main.py` and the
    web UI - use this so their traces look like the Agent Framework ones.
    """
    return _shorten(_clean_result(text))


def _clean_result(text: str) -> str:
    """Drop the raw MCP envelope from a tool result.

    The MCP server returns its payload twice: once as plain text, and once
    wrapped in a JSON envelope. Showing both doubles the noise for zero extra
    information, so we keep only the human-readable part.

    The envelope has two spellings. Over stdio it arrives on its own line as
    `{"result": [...]}`. The Agent Service appends a pretty-printed
    `{"structuredResponse": ...}` object instead, whose opening brace sits on a
    line of its own - so we find the key and cut back to the brace that opens it.
    """
    lines = [line for line in text.splitlines() if not line.lstrip().startswith('{"result":')]
    cleaned = "\n".join(lines).strip() or text.strip()

    key = cleaned.find('"structuredResponse"')
    if key > 0:
        brace = cleaned.rfind("{", 0, key)
        if brace > 0:
            cleaned = cleaned[:brace].strip()

    return cleaned


def _shorten(text: str, limit: int = 600) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f" ... [{len(text)} chars total]"


def print_tool_calls(response: Any, *, show_results: bool = True, colour: bool = True) -> None:
    """Print a readable trace of everything the agent did to the database."""
    print_calls(extract_tool_calls(response), show_results=show_results, colour=colour)


def print_calls(calls: list[ToolCall], *, show_results: bool = True, colour: bool = True) -> None:
    """Same output, for callers that collected the tool calls themselves.

    `src/foundry/main.py` drives the tool loop by hand, so it has the calls
    already and never builds an `AgentResponse`.
    """
    if not calls:
        print(_c("(the agent answered without touching the database)", DIM, colour))
        return

    print(_c(f"\n--- what the agent did ({len(calls)} tool call(s)) ---", DIM, colour))
    for index, call in enumerate(calls, start=1):
        print(_c(f"[{index}] {call.name}", CYAN, colour))

        body = call.sql or call.code
        if body:
            # Indent so multi-line SQL and Python stay readable.
            for line in body.splitlines():
                print(f"    {_c(line, YELLOW, colour)}")
        else:
            print(f"    {_c(json.dumps(call.arguments, default=str), YELLOW, colour)}")

        if show_results and call.result:
            for line in _shorten(_clean_result(call.result)).splitlines():
                print(f"    {_c('-> ' + line, DIM, colour)}")
    print(_c("--- end of trace ---\n", DIM, colour))


def _c(text: str, code: str, colour: bool) -> str:
    return f"{code}{text}{RESET}" if colour else text
