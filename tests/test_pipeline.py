"""
[OVERVIEW]
Integration tests for the loader: idempotency and crash safety (outcome O1).

They need a running Postgres (the PG* environment variables, as in the dev
container and CI). Each test creates its own throwaway database, `nclt_test`, so
your real data in `nclt_db` is never touched. The data is a tiny generated
fixture session, never the NCLT dataset.

Run only these:   pytest -m integration
Skip them:        pytest -m "not integration"
"""

import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from nclt_lab import db
from nclt_lab.loader import SessionLoader
from nclt_lab.sensors import SENSORS

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "sql" / "schema.sql"
TEST_DB = "nclt_test"
SESSION = "2012-01-08"  # must be in config.ALL_SESSIONS
BASE_UTIME = 1_326_030_975_000_000
CRASH_EXIT_CODE = 137  # loader.maybe_crash() exits with this

# Runs the loader in a separate process, so os._exit() kills only that process.
LOAD_SCRIPT = """
import sys
from pathlib import Path
from nclt_lab import db
from nclt_lab.loader import SessionLoader
with db.connect() as conn:
    SessionLoader(conn).load(sys.argv[1], Path(sys.argv[2]))
"""


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def test_db(monkeypatch):
    """A fresh, empty database with the schema applied; dropped afterwards."""
    try:
        admin = psycopg.connect("", autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as err:
        pytest.skip(f"Postgres not reachable: {err}")

    original_db = admin.info.dbname
    assert original_db != TEST_DB, "refusing to run: PGDATABASE already points at the test database"

    with admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB))
        )
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))

    monkeypatch.setenv("PGDATABASE", TEST_DB)  # db.connect() and subprocesses now use it
    with db.connect() as conn:
        db.apply_schema(conn, SCHEMA)

    yield

    with psycopg.connect("", dbname=original_db, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB))
        )


def write_fixture_session(folder: Path, rows: int) -> Path:
    """Write one small CSV per sensor, in the real files' format: no header,
    utime first, at the sensor's real rate, with 'nan' in every 10th row."""
    folder.mkdir(parents=True, exist_ok=True)
    for spec in SENSORS:
        period_us = int(1e6 / spec.expected_hz)
        lines = []
        for i in range(rows):
            values = [str(i % 7)] * (len(spec.columns) - 1)  # integers parse as smallint and float8
            if i % 10 == 9:
                values[-1] = "nan"  # last column is float8 in every table
            lines.append(",".join([str(BASE_UTIME + i * period_us), *values]))
        (folder / spec.file).write_text("\n".join(lines) + "\n")
    return folder


def snapshot(conn: psycopg.Connection) -> dict:
    """Everything a load can change: row counts, a hash of every row, and the
    dim_session row (minus loaded_at, which changes on every reload)."""
    state = {}
    for spec in SENSORS:
        state[spec.table] = conn.execute(
            sql.SQL(
                "SELECT count(*), md5(coalesce(string_agg("
                "t::text, '|' ORDER BY session_id, utime), '')) "
                "FROM {} AS t"
            ).format(sql.Identifier(spec.table))
        ).fetchone()
    state["dim_session"] = conn.execute(
        "SELECT session_id, session_date, start_utime, end_utime, rows_loaded, status "
        "FROM dim_session ORDER BY session_id"
    ).fetchall()

    return state


def load_in_subprocess(folder: Path, crash_at: str | None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("NCLT_CRASH_AT", None)
    if crash_at:
        env["NCLT_CRASH_AT"] = crash_at
    return subprocess.run(
        [sys.executable, "-c", LOAD_SCRIPT, SESSION, str(folder)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


# --- idempotency ------------------------------------------------------------


def test_loading_twice_changes_nothing(test_db, tmp_path):
    folder = write_fixture_session(tmp_path / SESSION, rows=50)
    with db.connect() as conn:
        loader = SessionLoader(conn)
        first = loader.load(SESSION, folder)
        after_first = snapshot(conn)
        loader.load(SESSION, folder)
        after_second = snapshot(conn)

        assert first.total_rows == 50 * len(SENSORS)
        assert after_second == after_first
        for spec in SENSORS:
            result = conn.execute(
                sql.SQL("SELECT count(*), count(DISTINCT (session_id, utime)) FROM {}").format(
                    sql.Identifier(spec.table)
                )
            ).fetchone()
            assert result is not None
            total, distinct = result
            assert total == distinct == 50, spec.table


def test_nan_is_loaded_as_null(test_db, tmp_path):
    folder = write_fixture_session(tmp_path / SESSION, rows=50)
    with db.connect() as conn:
        SessionLoader(conn).load(SESSION, folder)
        result = conn.execute("SELECT count(*) FROM gps_rtk_err WHERE error_m IS NULL").fetchone()
        assert result is not None
        nulls = result[0]
    assert nulls == 5  # rows 9, 19, 29, 39, 49


# --- crash safety -------------------------------------------------------------

STAGES = ["copy:imu", "copy:gps_rtk_err", "swap"]  # first COPY, last COPY, mid-swap


@pytest.mark.parametrize("stage", STAGES)
def test_crash_on_empty_database_leaves_nothing(test_db, tmp_path, stage):
    folder = write_fixture_session(tmp_path / SESSION, rows=50)

    result = load_in_subprocess(folder, crash_at=stage)
    assert result.returncode == CRASH_EXIT_CODE, result.stderr  # proves the crash really happened

    with db.connect() as conn:
        state = snapshot(conn)
    assert all(state[spec.table][0] == 0 for spec in SENSORS)
    assert state["dim_session"] == []


@pytest.mark.parametrize("stage", STAGES)
def test_crash_during_reload_keeps_previous_version(test_db, tmp_path, stage):
    # Version A is loaded and committed.
    version_a = write_fixture_session(tmp_path / "a" / SESSION, rows=50)
    with db.connect() as conn:
        SessionLoader(conn).load(SESSION, version_a)
        before = snapshot(conn)

    # Version B has a different row count. Reloading identical data could hide
    # a partial write, because a half-applied swap would look the same.
    version_b = write_fixture_session(tmp_path / "b" / SESSION, rows=30)
    result = load_in_subprocess(version_b, crash_at=stage)
    assert result.returncode == CRASH_EXIT_CODE, result.stderr

    with db.connect() as conn:
        assert snapshot(conn) == before  # nothing from version B is visible

    # Recovery: the next normal run simply succeeds.
    result = load_in_subprocess(version_b, crash_at=None)
    assert result.returncode == 0, result.stderr
    with db.connect() as conn:
        assert all(snapshot(conn)[spec.table][0] == 30 for spec in SENSORS)


def test_the_check_can_fail(test_db, tmp_path):
    """Control: without a crash, version B does change the database. If this
    failed, the crash tests above would be passing for the wrong reason."""
    with db.connect() as conn:
        SessionLoader(conn).load(SESSION, write_fixture_session(tmp_path / "a" / SESSION, rows=50))
        before = snapshot(conn)

    result = load_in_subprocess(
        write_fixture_session(tmp_path / "b" / SESSION, rows=30), crash_at=None
    )
    assert result.returncode == 0, result.stderr
    with db.connect() as conn:
        assert snapshot(conn) != before


def test_duplicate_utime_keeps_last_row(test_db, tmp_path):
    folder = write_fixture_session(tmp_path / SESSION, rows=50)
    odometry = next(s for s in SENSORS if s.table == "odometry")
    path = folder / odometry.file
    first = path.read_text().splitlines()[0]
    dup_utime = int(first.split(",")[0])
    # same utime as row 0, different values, appended at the END of the file
    later = ",".join([str(dup_utime), *(["99"] * (len(odometry.columns) - 1))])
    path.write_text(path.read_text() + later + "\n")

    with db.connect() as conn:
        report = SessionLoader(conn).load(SESSION, folder)
        result = conn.execute(
            "SELECT (SELECT count(*) FROM odometry), (SELECT x FROM odometry WHERE utime = %s)",
            (dup_utime,),
        ).fetchone()
        assert result is not None
        rows, x = result
        rows_loaded_row = conn.execute("SELECT rows_loaded FROM dim_session").fetchone()
        assert rows_loaded_row is not None
        rows_loaded = rows_loaded_row[0]

    assert report.duplicate_utimes["odometry"] == 1
    assert rows == 50  # 51 lines in the file, 1 replaced
    assert x == 99  # the later line won
    assert rows_loaded == report.total_rows == 50 * len(SENSORS)


def test_row_count_check_rolls_back(test_db, tmp_path, monkeypatch):
    """Control: if _swap inserts a different number of rows than expected, the
    whole load must roll back."""
    with db.connect() as conn:
        SessionLoader(conn).load(SESSION, write_fixture_session(tmp_path / "a" / SESSION, rows=50))
        before = snapshot(conn)

    from nclt_lab import loader as loader_module

    real_validate = loader_module.SessionLoader._validate

    def under_report(self, cur, spec, file_lines):
        staged, duplicates = real_validate(self, cur, spec, file_lines)
        return staged, duplicates + 1  # claims one duplicate that isn't there

    monkeypatch.setattr(loader_module.SessionLoader, "_validate", under_report)
    with db.connect() as conn:
        with pytest.raises(loader_module.ValidationError, match="expected"):
            SessionLoader(conn).load(
                SESSION, write_fixture_session(tmp_path / "b" / SESSION, rows=30)
            )
    with db.connect() as conn:
        assert snapshot(conn) == before
