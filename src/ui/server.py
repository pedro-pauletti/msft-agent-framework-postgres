"""
A local web UI for both implementations.
================================================================================

    python -m src.ui.server        then open http://127.0.0.1:8100

The CLIs answer "what did the agent say?". This answers "what did the agent
*do*?" - tool calls, the SQL it wrote, token counts and per-turn metadata, in
the shape the Foundry portal playground shows them.

It is deliberately a thin layer. Every turn goes through the same functions the
CLIs use, so there is no second implementation of the agent hiding in here:

    implementation 1  ->  src.maf.agent.build_agent + agent.run(...)
    implementation 2  ->  src.foundry.main.run_turn

The interesting part is the comparison. Ask both the same question and the trace
panel shows the same SQL arriving by two completely different routes: a child
process on this machine, or a container the Agent Service called for you.

Neither implementation is required. If only one is configured in .env, the other
is reported as unavailable and the UI disables it.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.common.config import ConfigError
from src.common.trace import ToolCall, extract_tool_calls

STATIC_DIR = Path(__file__).parent / "static"

MAF = "maf"
FOUNDRY = "foundry"


# =============================================================================
# The shape the UI renders
# =============================================================================


@dataclass
class TurnResult:
    """One turn, normalised so the front end does not care which SDK ran it."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


def _serialise_calls(calls: list[ToolCall]) -> list[dict[str, Any]]:
    return [
        {
            "name": call.name,
            "arguments": call.arguments,
            "sql": call.sql,
            "code": call.code,
            "result": call.result,
        }
        for call in calls
    ]


# =============================================================================
# Implementation 1 - Microsoft Agent Framework
# =============================================================================


class MafRunner:
    """Keeps one agent, and therefore one MCP child process, alive.

    The CLI wraps `async with build_agent(...)` around the whole session. A web
    server has no such block, so the context manager is held open in an exit
    stack for the lifetime of the process and closed on shutdown. Starting it is
    deferred to the first request: it spawns `uvx` and can take seconds, and it
    should not stop the Foundry side from working when it fails.
    """

    def __init__(self) -> None:
        self._stack = contextlib.AsyncExitStack()
        self._agent: Any = None
        self._session: Any = None
        self._config: Any = None
        self._lock = asyncio.Lock()  # one MCP session, so one turn at a time

    @property
    def available(self) -> bool:
        try:
            self._load_config()
        except ConfigError:
            return False
        return True

    def _load_config(self) -> Any:
        if self._config is None:
            from src.maf.config import load_config

            self._config = load_config()
        return self._config

    def describe(self) -> dict[str, Any]:
        try:
            config = self._load_config()
        except ConfigError as exc:
            return {"available": False, "reason": str(exc)}
        return {
            "available": True,
            "endpoint": config.azure_openai.endpoint,
            "model": config.azure_openai.deployment,
            "auth": "Entra ID" if config.azure_openai.uses_entra_id else "API key",
            "database": f"{config.postgres.host}/{config.postgres.database}",
            "access_mode": config.mcp.access_mode,
        }

    async def _ensure_started(self) -> Any:
        if self._agent is not None:
            return self._agent

        from src.maf.agent import build_agent

        config = self._load_config()
        self._agent = await self._stack.enter_async_context(build_agent(config))
        self._session = self._agent.create_session()
        return self._agent

    async def run(self, message: str) -> TurnResult:
        async with self._lock:
            agent = await self._ensure_started()

            started = time.perf_counter()
            response = await agent.run(message, session=self._session)
            elapsed = int((time.perf_counter() - started) * 1000)

            config = self._load_config()
            return TurnResult(
                text=response.text,
                tool_calls=_serialise_calls(extract_tool_calls(response)),
                usage=_maf_usage(response),
                metadata={
                    "Implementation": "Agent Framework (local)",
                    "Model": config.azure_openai.deployment,
                    "Response id": getattr(response, "response_id", None),
                    "Finish reason": _text_of(getattr(response, "finish_reason", None)),
                    "MCP transport": "stdio (uvx child process)",
                    "Access mode": config.mcp.access_mode,
                },
                duration_ms=elapsed,
            )

    async def reset(self) -> None:
        async with self._lock:
            if self._agent is not None:
                self._session = self._agent.create_session()

    async def aclose(self) -> None:
        await self._stack.aclose()
        self._agent = None
        self._session = None


def _maf_usage(response: Any) -> dict[str, int]:
    """Read the token counts off an `AgentResponse`.

    `UsageDetails` behaves like a mapping rather than a plain object, and it
    also carries provider-specific extras (`openai.cached_input_tokens` and
    friends) alongside the documented fields, so read it both ways.
    """
    details = getattr(response, "usage_details", None)
    if details is None:
        return {}

    def read(name: str) -> Any:
        if isinstance(details, dict) or hasattr(details, "get"):
            value = details.get(name)
            if value is not None:
                return value
        return getattr(details, name, None)

    usage = {
        "input": read("input_token_count"),
        "output": read("output_token_count"),
        "total": read("total_token_count"),
        "cached": read("cache_read_input_token_count"),
    }
    return {key: value for key, value in usage.items() if isinstance(value, int)}


# =============================================================================
# Implementation 2 - Foundry Agent Service
# =============================================================================


class FoundryRunner:
    """One request per turn, chained with `previous_response_id`."""

    def __init__(self) -> None:
        self._stack = contextlib.ExitStack()
        self._client: Any = None
        self._config: Any = None
        self._previous_response_id: str | None = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        try:
            self._load_config()
        except ConfigError:
            return False
        return True

    def _load_config(self) -> Any:
        if self._config is None:
            from src.foundry.config import load_foundry_app_config

            self._config = load_foundry_app_config()
        return self._config

    def describe(self) -> dict[str, Any]:
        try:
            config = self._load_config()
        except ConfigError as exc:
            return {"available": False, "reason": str(exc)}
        return {
            "available": True,
            "endpoint": config.foundry.project_endpoint,
            "model": config.foundry.model_deployment,
            "agent": config.foundry.agent_name,
            "mcp_server": config.foundry.mcp_server_url,
            "connection": config.foundry.mcp_connection_name,
        }

    def _ensure_client(self) -> Any:
        if self._client is None:
            from src.foundry.agent import build_project_client

            config = self._load_config()
            project = self._stack.enter_context(build_project_client(config.foundry))
            self._client = project.get_openai_client()
        return self._client

    async def run(self, message: str) -> TurnResult:
        from src.foundry.main import extract_mcp_calls, run_turn

        async with self._lock:
            client = await asyncio.to_thread(self._ensure_client)
            config = self._load_config()

            started = time.perf_counter()
            response = await asyncio.to_thread(
                run_turn,
                client,
                agent_name=config.foundry.agent_name,
                model=config.foundry.model_deployment,
                user_input=message,
                previous_response_id=self._previous_response_id,
            )
            elapsed = int((time.perf_counter() - started) * 1000)

            self._previous_response_id = response.id

            return TurnResult(
                text=response.output_text,
                tool_calls=_serialise_calls(extract_mcp_calls(response)),
                usage=_foundry_usage(response),
                metadata={
                    "Implementation": "Foundry Agent Service (hosted)",
                    "Agent": config.foundry.agent_name,
                    "Model": config.foundry.model_deployment,
                    "Response id": response.id,
                    "Status": _text_of(getattr(response, "status", None)),
                    "MCP transport": "streamable HTTP (Container Apps)",
                    "MCP server": config.foundry.mcp_server_url,
                },
                duration_ms=elapsed,
            )

    async def reset(self) -> None:
        async with self._lock:
            self._previous_response_id = None

    async def aclose(self) -> None:
        await asyncio.to_thread(self._stack.close)
        self._client = None


def _foundry_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None)
    values = {
        "input": getattr(usage, "input_tokens", None),
        "output": getattr(usage, "output_tokens", None),
        "total": getattr(usage, "total_tokens", None),
        "cached": cached,
    }
    return {key: value for key, value in values.items() if isinstance(value, int)}


def _text_of(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", None) or str(value)


# =============================================================================
# The web app
# =============================================================================


class ChatRequest(BaseModel):
    implementation: str
    message: str


class ResetRequest(BaseModel):
    implementation: str


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runners = {MAF: MafRunner(), FOUNDRY: FoundryRunner()}
    try:
        yield
    finally:
        for runner in app.state.runners.values():
            with contextlib.suppress(Exception):
                await runner.aclose()


app = FastAPI(title="Postgres Agent UI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _runner(app: FastAPI, implementation: str) -> Any:
    runner = app.state.runners.get(implementation)
    if runner is None:
        raise HTTPException(status_code=400, detail=f"Unknown implementation {implementation!r}.")
    return runner


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    runners = app.state.runners
    return {
        MAF: runners[MAF].describe(),
        FOUNDRY: runners[FOUNDRY].describe(),
    }


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict[str, Any]:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="The message is empty.")

    runner = _runner(app, request.implementation)
    try:
        result = await runner.run(message)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_explain(exc)) from exc

    return asdict(result)


@app.post("/api/reset")
async def reset(request: ResetRequest) -> dict[str, bool]:
    await _runner(app, request.implementation).reset()
    return {"ok": True}


def _explain(exc: Exception) -> str:
    """Give the browser the actionable message, not just the exception text."""
    name = type(exc).__name__
    if name == "ToolException":
        from src.maf.mcp_server import explain_startup_failure

        return explain_startup_failure(exc)
    return f"{name}: {exc}"


def main() -> int:
    import uvicorn

    host, port = "127.0.0.1", 8100
    print(f"\n  Postgres Agent UI  ->  http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
