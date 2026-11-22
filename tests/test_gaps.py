"""Step 6 integration tests: sensor_gaps is correct, idempotent, and reconciles."""

import csv

import pytest
from conftest import BASE_UTIME, write_session

from nclt_lab import db, gaps
from nclt_lab.loader import SessionLoader
from nclt_lab.sensors import SENSORS

pytestmark = pytest.mark.integration
SESSION = "2012-01-08"
GPS = next(s for s in SENSORS if s.table == "gps")


def load_fixture(tmp_path, **kwargs):
    folder = write_session(tmp_path / SESSION, rows=100, **kwargs)
    with db.connect() as conn:
        SessionLoader(conn).load(SESSION, folder)


def test_one_injected_gap_is_found_with_exact_times(fresh_db, tmp_path):
    load_fixture(tmp_path, gap_after_row=49, gap_s=5.0)
    period_us = int(1e6 / GPS.expected_hz)
    with db.connect() as conn:
        assert gaps.rebuild(conn, GPS) == 1
        start, end, gap_s = conn.execute(
            "SELECT gap_start_utime, gap_end_utime, gap_s FROM sensor_gaps WHERE sensor = 'gps'"
        ).fetchone()
    assert start == BASE_UTIME + 49 * period_us  # last reading before the gap
    assert end == BASE_UTIME + 50 * period_us + 5_000_000  # first reading after it
    assert gap_s == pytest.approx(5.0 + period_us / 1e6)


def test_no_gap_means_no_rows(fresh_db, tmp_path):
    load_fixture(tmp_path)
    with db.connect() as conn:
        assert gaps.rebuild(conn, GPS) == 0


def test_rebuild_is_idempotent(fresh_db, tmp_path):
    load_fixture(tmp_path, gap_after_row=10)
    with db.connect() as conn:
        for spec in SENSORS:
            gaps.rebuild(conn, spec)
        first = conn.execute("SELECT * FROM sensor_gaps ORDER BY 1, 2, 3").fetchall()
        for spec in SENSORS:
            gaps.rebuild(conn, spec)
        second = conn.execute("SELECT * FROM sensor_gaps ORDER BY 1, 2, 3").fetchall()
    assert first == second
    assert len(first) == len(SENSORS)


def test_reconcile_flags_a_mismatch(fresh_db, tmp_path):
    load_fixture(tmp_path, gap_after_row=49, gap_s=5.0)
    with db.connect() as conn:
        gaps.rebuild(conn, GPS)
        (stored,) = conn.execute("SELECT max(gap_s) FROM sensor_gaps").fetchone()

        profile = tmp_path / "profile.csv"
        with profile.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["session", "file", "max_gap_s"])
            w.writeheader()
            w.writerow({"session": SESSION, "file": "gps.csv", "max_gap_s": stored})
            w.writerow({"session": SESSION, "file": "kvh.csv", "max_gap_s": 99})  # not loaded
        assert gaps.reconcile_with_profile(conn, profile) == []

        with profile.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["session", "file", "max_gap_s"])
            w.writeheader()
            w.writerow({"session": SESSION, "file": "gps.csv", "max_gap_s": stored + 1})
        assert len(gaps.reconcile_with_profile(conn, profile)) == 1
