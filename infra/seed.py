"""
Applies `infra/seed.sql` to the configured PostgreSQL database.
================================================================================

Run it from the repository root:

    python -m infra.seed

This is the only place in the repository that opens a database connection
directly. The *agent* never does - it reaches Postgres exclusively through the
Postgres MCP server. We use `psycopg` here purely so you do not need the `psql`
command-line client installed to set up the sample.

If you do have `psql`, this is equivalent:

    psql "$DATABASE_URI" -f infra/seed.sql
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

# `infra/seed.py` -> repo root is one level up.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.common.config import ConfigError, load_env, load_postgres_config

SEED_SQL_PATH = REPO_ROOT / "infra" / "seed.sql"

# Simple sanity checks run after seeding, so you get immediate proof that the
# data landed instead of discovering it later through the agent.
VERIFICATION_QUERIES = [
    ("sites", "SELECT count(*) FROM sites"),
    ("fiber_links", "SELECT count(*) FROM fiber_links"),
    ("fiber_alerts", "SELECT count(*) FROM fiber_alerts"),
    ("alert_notes", "SELECT count(*) FROM alert_notes"),
    ("open critical alerts",
     "SELECT count(*) FROM fiber_alerts WHERE status = 'open' AND severity = 'critical'"),
]


def main() -> int:
    try:
        load_env()
        pg = load_postgres_config()
    except ConfigError as exc:
        print(f"Configuration problem:\n\n{exc}", file=sys.stderr)
        return 1

    if not SEED_SQL_PATH.exists():
        print(f"Could not find {SEED_SQL_PATH}", file=sys.stderr)
        return 1

    sql = SEED_SQL_PATH.read_text(encoding="utf-8")

    print(f"Connecting to {pg.safe_display_uri}")
    try:
        # `autocommit=False` (the default) wraps the whole script in a single
        # transaction: either the entire schema is created or nothing is.
        with psycopg.connect(pg.connection_uri, connect_timeout=30) as conn:
            with conn.cursor() as cur:
                print(f"Applying {SEED_SQL_PATH.name} ...")
                cur.execute(sql)
            conn.commit()

            print("\nVerifying:")
            with conn.cursor() as cur:
                for label, query in VERIFICATION_QUERIES:
                    cur.execute(query)
                    row = cur.fetchone()
                    count = row[0] if row else 0
                    print(f"  {label:<22} {count}")
    except psycopg.OperationalError as exc:
        print(
            f"\nCould not connect to PostgreSQL.\n\n{exc}\n"
            "Common causes:\n"
            "  - Your public IP is not in the server firewall. Re-run\n"
            "    infra/provision.ps1 (or .sh), which refreshes the rule.\n"
            "  - PGSSLMODE is not 'require' (Azure requires TLS).\n"
            "  - Wrong PGHOST / PGUSER / PGPASSWORD in .env.",
            file=sys.stderr,
        )
        return 1

    print("\nDone. The FiberOps database is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
