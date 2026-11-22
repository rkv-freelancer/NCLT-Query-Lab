import psycopg

from nclt_lab.profile import rollup_by_sensor


def test_postgres_reachable():
    with psycopg.connect("") as conn:  # empty string = use the PG* env vars
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_pg_stat_statements_installed():
    with psycopg.connect("") as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'"
        ).fetchone()
        assert row is not None


def test_sensor_hz_is_recomputed_not_averaged():
    results = [
        {
            "file": "imu.csv",
            "rows": 100,
            "file_bytes": 1000,
            "duration_s": 10.0,
            "actual_hz": 10.0,
            "gaps": 0,
            "max_gap_s": 0.1,
        },
        {
            "file": "imu.csv",
            "rows": 10,
            "file_bytes": 100,
            "duration_s": 100.0,
            "actual_hz": 0.1,
            "gaps": 0,
            "max_gap_s": 0.1,
        },
    ]
    summary = rollup_by_sensor(results)[0]
    assert summary["rows"] == 110
    assert summary["actual_hz"] == 1.0  # 110 rows / 110 s, not (10 + 0.1) / 2
