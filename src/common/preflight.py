"""
Fail fast, with a useful message.
================================================================================

Starting the agent means spawning the Postgres MCP server, which then tries to
connect to your database. If that connection cannot be made, the failure is
slow and deeply unhelpful:

  * the MCP server retries for ~30 seconds before giving up,
  * it prints its warnings to stderr, which floods your terminal,
  * the interactive prompt never appears in that time,
  * and the whole thing looks like the CLI has frozen and will not let you
    type anything.

So we check the connection ourselves first. It takes a fraction of a second
when things are healthy, and when they are not you get one clear paragraph
telling you what to fix.
"""

from __future__ import annotations

from src.common.config import ConfigError, PostgresConfig

# Short on purpose. This is a reachability check, not a workload - if the
# server cannot answer in this long, something is wrong and we want to say so
# rather than make the user wait.
CONNECT_TIMEOUT_SECONDS = 10


def check_database_reachable(pg: PostgresConfig) -> None:
    """Verify we can reach the database, or raise ConfigError explaining why.

    `psycopg` is only imported here so that the rest of the sample does not
    depend on it. If it is missing we skip the check rather than failing - the
    agent itself never needs it.
    """
    try:
        import psycopg
    except ImportError:
        return

    try:
        with psycopg.connect(pg.connection_uri, connect_timeout=CONNECT_TIMEOUT_SECONDS):
            return
    except psycopg.OperationalError as exc:
        raise ConfigError(_explain(pg, exc)) from exc


def _explain(pg: PostgresConfig, exc: Exception) -> str:
    """Turn a psycopg error into advice, based on what it actually says."""
    message = str(exc).strip()
    lowered = message.lower()

    if "resolve host" in lowered or "getaddrinfo" in lowered or "name or service not known" in lowered:
        cause = (
            f"The hostname '{pg.host}' could not be resolved, so we never even "
            "reached\n"
            "a server. This is a typo in PGHOST, or a DNS problem.\n\n"
            "Check the value in .env against the real server name:\n"
            "  az postgres flexible-server list -o table"
        )
    elif "timeout" in lowered or "could not connect to server" in lowered:
        cause = (
            "The server did not answer at all. That is almost always the "
            "firewall.\n\n"
            "The most common reason is that your public IP changed - moving "
            "between\n"
            "office, home, a VPN or a hotspot is enough. The rule created "
            "earlier no\n"
            "longer matches you.\n\n"
            "Fix it by re-running the provisioning script, which detects your "
            "real\n"
            "egress IP and updates the rule:\n\n"
            "  pwsh infra/provision.ps1 -ServerName <your-server>\n"
            "  bash infra/provision.sh\n\n"
            "Other possibilities:\n"
            "  - the server is stopped:\n"
            "      az postgres flexible-server start -g <rg> -n <server>\n"
            "  - outbound port 5432 is blocked on your network. Check with:\n"
            "      Test-NetConnection -ComputerName portquiz.net -Port 5432"
        )
    elif "password authentication failed" in lowered or "role" in lowered:
        cause = (
            "The server answered but rejected your credentials.\n\n"
            "Check PGUSER and PGPASSWORD in .env. If you re-ran the "
            "provisioning\n"
            "script without an existing .env, it may have reset the admin "
            "password."
        )
    elif "does not exist" in lowered:
        cause = (
            f"The server is reachable but the database '{pg.database}' does "
            "not exist.\n\n"
            "Check PGDATABASE in .env, or create it:\n"
            f"  az postgres flexible-server db create -g <rg> -s <server> "
            f"-d {pg.database}"
        )
    elif "ssl" in lowered:
        cause = (
            "A TLS problem. Azure Database for PostgreSQL requires TLS, so "
            "PGSSLMODE\n"
            "must be 'require'. For a local Docker Postgres, use 'disable'."
        )
    else:
        cause = "See docs/troubleshooting.md for the full list of causes."

    return (
        f"Cannot reach the database at {pg.host}:{pg.port}/{pg.database}\n\n"
        f"{cause}\n\n"
        f"Original error: {message}"
    )
