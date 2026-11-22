"""Shared fixtures for the step 6-10 integration tests.

`fresh_db` creates a throwaway database (`nclt_test_steps`) with the schema
applied and points PGDATABASE at it, so your real `nclt_db` is never touched.
test_pipeline.py keeps its own fixture; both can run in the same pytest session.
"""

from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from nclt_lab import db
from nclt_lab.sensors import SENSORS

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "sql" / "schema.sql"
STEPS_TEST_DB = "nclt_test_steps"
BASE_UTIME = 1_326_030_975_000_000


@pytest.fixture
def fresh_db(monkeypatch):
    try:
        admin = psycopg.connect("", autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as err:
        pytest.skip(f"Postgres not reachable: {err}")

    original_db = admin.info.dbname
    assert original_db != STEPS_TEST_DB, "PGDATABASE already points at the test database"
    drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(STEPS_TEST_DB))
    with admin:
        admin.execute(drop)
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(STEPS_TEST_DB)))

    monkeypatch.setenv("PGDATABASE", STEPS_TEST_DB)
    monkeypatch.chdir(REPO)  # modules use repo-relative paths such as sql/ and reports/
    with db.connect() as conn:
        db.apply_schema(conn, SCHEMA)

    yield

    with psycopg.connect("", dbname=original_db, autocommit=True) as admin:
        admin.execute(drop)


def write_session(
    folder: Path, rows: int, gap_after_row: int | None = None, gap_s: float = 5.0
) -> Path:
    """Write one CSV per sensor in the real format (no header, utime first, real
    rate). With gap_after_row, every sensor goes silent for gap_s after that row."""
    folder.mkdir(parents=True, exist_ok=True)
    for spec in SENSORS:
        period_us = int(1e6 / spec.expected_hz)
        lines = []
        for i in range(rows):
            utime = BASE_UTIME + i * period_us
            if gap_after_row is not None and i > gap_after_row:
                utime += int(gap_s * 1e6)
            values = [str(i % 7)] * (len(spec.columns) - 1)
            lines.append(",".join([str(utime), *values]))
        (folder / spec.file).write_text("\n".join(lines) + "\n")
    return folder
