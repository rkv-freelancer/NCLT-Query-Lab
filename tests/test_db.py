import psycopg


def test_postgres_reachable():
    with psycopg.connect("") as conn:  # empty string = use the PG* env vars
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_pg_stat_statements_installed():
    with psycopg.connect("") as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'"
        ).fetchone()
        assert row is not None
