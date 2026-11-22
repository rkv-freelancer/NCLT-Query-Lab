"""Database helpers. Connection settings come from the PG* environment variables."""

from pathlib import Path

import psycopg
from psycopg import sql

from nclt_lab.sensors import SENSORS

SCHEMA_SQL = Path("sql/schema.sql")


def connect() -> psycopg.Connection:
    """Autocommit connection: every statement commits on its own unless it runs
    inside an explicit `with conn.transaction():` block. VACUUM requires this."""
    return psycopg.connect("", autocommit=True)


def apply_schema(conn: psycopg.Connection, path: Path = SCHEMA_SQL) -> None:
    """Run schema.sql. Every statement in it is IF NOT EXISTS, so this is idempotent."""
    conn.execute(path.read_text())


def loaded_sessions(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT session_date::text FROM dim_session WHERE status = 'loaded'"
    ).fetchall()
    return {date for (date,) in rows}


def vacuum_analyze(conn: psycopg.Connection) -> None:
    """Refresh planner statistics and clean up dead rows left by reloads."""
    for spec in SENSORS:
        conn.execute(sql.SQL("VACUUM (ANALYZE) {}").format(sql.Identifier(spec.table)))
