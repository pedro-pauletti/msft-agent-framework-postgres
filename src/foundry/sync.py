"""
Register the agent in your Foundry project.
================================================================================

    python -m src.foundry.sync

Run `pwsh infra/deploy-mcp.ps1` first - the agent needs an MCP endpoint to point
at - then run this whenever the system prompt or the endpoint changes.

Running it twice is safe. The agent is identified by `FOUNDRY_AGENT_NAME`, so a
second run does not create a second agent - it creates version 2 of the same
one. Versions are immutable, which makes this the closest thing this repo has to
"infrastructure as code" for an agent: the definition lives in source control,
and every deployment of it is recorded in the project.

Note how little happens here compared to the Agent Framework path: no MCP server
to start, no database to reach. This process only submits a document.
"""

from __future__ import annotations

import sys

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError

from src.common.config import ConfigError
from src.common.workbook import ensure_workbook_uploaded
from src.foundry.agent import (
    AGENT_DESCRIPTION,
    SERVER_LABEL,
    build_agent_definition,
    build_project_client,
)
from src.foundry.config import load_foundry_app_config


def sync() -> int:
    try:
        config = load_foundry_app_config()
    except ConfigError as exc:
        print(f"\nConfiguration problem:\n\n{exc}\n", file=sys.stderr)
        return 1

    print(f"Project    : {config.foundry.project_endpoint}")
    print(f"Agent      : {config.foundry.agent_name}")
    print(f"Model      : {config.foundry.model_deployment}")
    print(f"MCP server : {config.foundry.mcp_server_url}")
    print(f"Connection : {config.foundry.mcp_connection_name}")
    print(
        "Prompt     : "
        + (
            config.instructions_file.name
            if config.instructions_file is not None
            else "built-in FiberOps sample"
        )
    )

    print("\nSubmitting the agent definition...", flush=True)
    try:
        with build_project_client(config.foundry) as client:
            # The workbook has to exist before the definition that references
            # it, so this upload happens inside the same client session.
            workbook_file_id = ensure_workbook_uploaded(client.get_openai_client().files)
            print(f"Workbook   : {workbook_file_id}")

            version = client.agents.create_version(
                agent_name=config.foundry.agent_name,
                definition=build_agent_definition(config, workbook_file_id=workbook_file_id),
                description=AGENT_DESCRIPTION,
            )
    except ClientAuthenticationError as exc:
        print(f"\nCould not sign in to Azure: {exc}\n", file=sys.stderr)
        print("Run `az login` and try again.\n", file=sys.stderr)
        return 1
    except HttpResponseError as exc:
        print(f"\n{_explain(exc)}\n", file=sys.stderr)
        return 1

    print(f"\nDone. '{version.name}' is now at version {version.version}.")
    print(f"It has two tools: '{SERVER_LABEL}' (MCP) and a code interpreter")
    print("holding data/fiberops-contracts.xlsx.")
    print("\nTry it in the Foundry portal playground, or locally with:")
    print("  python -m src.foundry.main")
    return 0


def _explain(exc: HttpResponseError) -> str:
    """Turn the failures people actually hit into something actionable."""
    message = str(exc)
    lowered = message.lower()

    if "agents/write" in lowered or exc.status_code == 403:
        return (
            "The project refused the write.\n\n"
            "Reading agents and writing them are different permissions, so "
            "listing\n"
            "agents can work while this fails. You need a role that grants "
            "agent write\n"
            "on the project - 'Azure AI User' is the usual one. Being "
            "subscription\n"
            "Owner is not sufficient by itself.\n\n"
            "Ask whoever owns the Foundry resource to assign it, then run "
            "`az login`\n"
            "again so the new role lands in a fresh token.\n\n"
            f"Original error: {message}"
        )

    if "model" in lowered and ("not found" in lowered or "does not exist" in lowered):
        return (
            "The project does not have that model deployment.\n\n"
            "FOUNDRY_MODEL_DEPLOYMENT must name a deployment in the same "
            "resource as\n"
            "the project, and it is the *deployment* name, not the model "
            "name. Check\n"
            "the Models + endpoints page of the project in the Foundry "
            "portal.\n\n"
            f"Original error: {message}"
        )

    return f"The project rejected the agent definition.\n\nOriginal error: {message}"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    return sync()


if __name__ == "__main__":
    raise SystemExit(main())
