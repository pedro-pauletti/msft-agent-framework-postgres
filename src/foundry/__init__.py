"""Implementation 2: the Foundry Agent Service.

The agent is registered in a Foundry project, where it is versioned and visible
in the portal, with a single MCP tool pointing at a hosted Postgres MCP server:

    pwsh infra/deploy-mcp.ps1      publish the MCP server (once)
    python -m src.foundry.sync     register (or update) the agent
    python -m src.foundry.main     chat with it

Same database, same system prompt as `src/maf/` - but nothing runs locally, so
the agent works from the portal playground too. Start with `agent.py`;
docs/implementations.md compares the two.
"""
