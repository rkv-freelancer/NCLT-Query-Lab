"""Step 10: export every loaded session to Parquet, D1's input and a compact backup.

    python -m nclt_lab.export                 # all loaded sessions (skips files that exist)
    python -m nclt_lab.export --force         # rewrite everything
    python -m nclt_lab.export --check         # verify counts only, and that D1 can read it

Layout (Hive-style partitions, so DuckDB/Polars read a session by its folder name):

    data/export/<table>/session=<date>/data.parquet     one per table per session
    data/export/sensor_gaps.parquet
    data/export/dim_session.parquet

How one file is written, with memory staying flat:
1. psycopg streams `COPY (SELECT ... ORDER BY utime) TO STDOUT` into a temporary CSV.
2. DuckDB converts that CSV to Parquet (zstd), with explicit column types.
3. The Parquet file is written under a temporary name, then renamed into place,
   so a crash never leaves a half-written file that looks complete.
4. Its row count is compared with Postgres before the temporary CSV is deleted.

Sorting by utime matters: Parquet stores min/max per row group, so D1's
time-range reads can skip row groups. It's the same idea as BRIN.

Alternative to try later: DuckDB's `postgres` extension can read Postgres
directly (ATTACH ... TYPE postgres) without the temporary CSV. Measure both.
"""

import argparse
import csv
import logging
import os
from pathlib import Path

import duckdb
import psycopg
from psycopg import sql

from nclt_lab import db
from nclt_lab.config import PROFILE_CSV
from nclt_lab.sensors import SENSORS

log = logging.getLogger(__name__)
EXPORT_DIR = Path("data/export")
SIZES_CSV = Path("reports/export_sizes.csv")
DUCKDB_MEMORY = "256MB"
CHUNK = 1 << 20

# Postgres type -> DuckDB type, for the explicit read_csv schema.
PG_TO_DUCKDB = {
    "smallint": "SMALLINT",
    "integer": "INTEGER",
    "bigint": "BIGINT",
    "double precision": "DOUBLE",
    "real": "FLOAT",
    "text": "VARCHAR",
    "date": "DATE",
    "timestamp with time zone": "TIMESTAMPTZ",
    "boolean": "BOOLEAN",
}


def table_columns(conn: psycopg.Connection, table: str) -> list[tuple[str, str]]:
    """(column, DuckDB type) in table order. PostGIS geometry is left out on purpose:
    D1 doesn't need it, and the raw lat/lng are exported anyway."""
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return [(name, PG_TO_DUCKDB[pg_type]) for name, pg_type in rows if pg_type in PG_TO_DUCKDB]


def export_query(conn, select: sql.Composable, columns: list[tuple[str, str]], target: Path) -> int:
    """Stream one SELECT to a Parquet file. Returns the number of rows written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_csv = target.with_suffix(".csv.tmp")
    tmp_parquet = target.with_suffix(".parquet.tmp")

    with tmp_csv.open("wb") as out, conn.cursor() as cur:
        with cur.copy(sql.SQL("COPY ({}) TO STDOUT (FORMAT csv)").format(select)) as copy:
            for chunk in copy:
                out.write(chunk)

    schema = "{" + ", ".join(f"'{name}': '{kind}'" for name, kind in columns) + "}"
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{DUCKDB_MEMORY}'")
    con.execute(
        f"COPY (SELECT * FROM read_csv('{tmp_csv}', header = false, columns = {schema})) "
        f"TO '{tmp_parquet}' (FORMAT parquet, COMPRESSION zstd)"
    )
    (written,) = con.execute(f"SELECT count(*) FROM read_parquet('{tmp_parquet}')").fetchone()
    con.close()

    os.replace(tmp_parquet, target)  # atomic: the file appears complete or not at all
    tmp_csv.unlink()
    return written


def export_session(
    conn, table: str, session_id: int, session: str, force: bool
) -> tuple[int, bool]:
    target = EXPORT_DIR / table / f"session={session}" / "data.parquet"
    if target.exists() and not force:
        return 0, False
    columns = table_columns(conn, table)
    select = sql.SQL("SELECT {} FROM {} WHERE session_id = {} ORDER BY utime").format(
        sql.SQL(", ").join(sql.Identifier(c) for c, _ in columns),
        sql.Identifier(table),
        sql.Literal(session_id),
    )
    written = export_query(conn, select, columns, target)
    (expected,) = conn.execute(
        sql.SQL("SELECT count(*) FROM {} WHERE session_id = %s").format(sql.Identifier(table)),
        (session_id,),
    ).fetchone()
    if written != expected:
        target.unlink()
        raise RuntimeError(
            f"{table} {session}: Postgres has {expected:,} rows, Parquet {written:,}"
        )
    return written, True


def export_small_table(conn, table: str) -> int:
    columns = table_columns(conn, table)
    select = sql.SQL("SELECT {} FROM {}").format(
        sql.SQL(", ").join(sql.Identifier(c) for c, _ in columns), sql.Identifier(table)
    )
    return export_query(conn, select, columns, EXPORT_DIR / f"{table}.parquet")


def check(conn) -> list[str]:
    """Per table: Postgres total vs the total D1 will see through DuckDB."""
    problems = []
    for spec in SENSORS:
        (pg_rows,) = conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(spec.table))
        ).fetchone()
        pattern = EXPORT_DIR / spec.table / "*" / "data.parquet"
        try:
            (pq_rows,) = duckdb.execute(
                f"SELECT count(*) FROM read_parquet('{pattern}', hive_partitioning = true)"
            ).fetchone()
        except duckdb.IOException:
            pq_rows = 0
        status = "ok" if pq_rows == pg_rows else "MISMATCH"
        log.info(
            "%-12s Postgres %12s  Parquet %12s  %s",
            spec.table,
            f"{pg_rows:,}",
            f"{pq_rows:,}",
            status,
        )
        if pq_rows != pg_rows:
            problems.append(spec.table)
    return problems


def write_sizes(conn) -> None:
    """CSV vs Postgres heap vs Parquet, per table: the README's storage comparison."""
    csv_bytes: dict[str, int] = {}
    if PROFILE_CSV.exists():
        with PROFILE_CSV.open(newline="") as f:
            for row in csv.DictReader(f):
                csv_bytes[row["file"]] = csv_bytes.get(row["file"], 0) + int(row["file_bytes"])
    rows = []
    for spec in SENSORS:
        (heap,) = conn.execute("SELECT pg_relation_size(%s)", (spec.table,)).fetchone()
        parquet = sum(p.stat().st_size for p in (EXPORT_DIR / spec.table).rglob("*.parquet"))
        rows.append(
            {
                "table": spec.table,
                "csv_bytes": csv_bytes.get(spec.file, ""),
                "heap_bytes": heap,
                "parquet_bytes": parquet,
            }
        )
    SIZES_CSV.parent.mkdir(parents=True, exist_ok=True)
    with SIZES_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    # Note: profile.csv covers only the sessions you profiled, and the gps tables'
    # heap includes the geom column. Say both next to the numbers in the README.
    # TODO: add a derived "Parquet / CSV" ratio column for the README table.


def run(force: bool = False, check_only: bool = False) -> int:
    with db.connect() as conn:
        if not check_only:
            sessions = conn.execute(
                "SELECT session_id, session_date::text FROM dim_session ORDER BY session_id"
            ).fetchall()
            for sid, session in sessions:
                for spec in SENSORS:
                    written, did = export_session(conn, spec.table, sid, session, force)
                    if did:
                        log.info("%s %-12s %10s rows", session, spec.table, f"{written:,}")
            for table in ("sensor_gaps", "dim_session"):  # small: always rewritten
                log.info("%-24s %10s rows", table, f"{export_small_table(conn, table):,}")
            write_sizes(conn)
        problems = check(conn)
    return 1 if problems else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    parser = argparse.ArgumentParser(description="Step 10: export to Parquet for D1")
    parser.add_argument("--force", action="store_true", help="rewrite files that already exist")
    parser.add_argument("--check", action="store_true", help="only compare counts")
    args = parser.parse_args()
    raise SystemExit(run(args.force, args.check))


if __name__ == "__main__":
    main()
