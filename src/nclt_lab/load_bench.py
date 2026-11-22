"""Load benchmark (outcome O2): row-by-row INSERT vs executemany vs COPY.

    python -m nclt_lab.loadbench 2012-01-08
    python -m nclt_lab.loadbench 2012-01-08 --table gps --repeat 5

Fair comparison rules:
- Same session file, same target (the sensor's staging table), same connection.
- Each run happens inside ONE transaction that is rolled back afterwards, so you
  compare loading methods, not commit costs, and the staging table stays empty.
- INSERT and executemany pay for parsing the CSV in Python, because that's part
  of their real cost. COPY sends the file's bytes and lets Postgres parse them.
- Method order rotates between repeats, so no method always runs first.
- Every run checks that the table holds exactly as many rows as the file.
"""

import argparse
import csv
import logging
import os
import platform
import statistics
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql

from nclt_lab import db
from nclt_lab.buffer import WorkBuffer
from nclt_lab.fetch import fetch_session
from nclt_lab.sensors import SENSORS, SensorSpec

log = logging.getLogger(__name__)
RESULTS_CSV = Path("reports/load_bench.csv")
CHUNK = 1 << 20


# --- helpers -------------------------------------------------------------------


def parse_rows(path: Path) -> Iterator[tuple]:
    """CSV lines -> typed tuples: utime as int, 'nan' as None (matching COPY's NULL 'nan')."""
    with path.open(newline="") as f:
        for fields in csv.reader(f):
            yield (int(fields[0]), *(None if v.strip() == "nan" else float(v) for v in fields[1:]))


def insert_statement(spec: SensorSpec) -> sql.Composed:
    return sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(spec.staging_table),
        sql.SQL(", ").join(map(sql.Identifier, spec.columns)),
        sql.SQL(", ").join(sql.Placeholder() * len(spec.columns)),
    )


def count_lines(path: Path) -> int:
    with path.open("rb") as f:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(CHUNK), b""))


# --- the three methods -----------------------------------------------------------


def load_row_by_row(cur: psycopg.Cursor, spec: SensorSpec, path: Path) -> None:
    """One INSERT statement and one network round trip per row."""
    statement = insert_statement(spec)
    for row in parse_rows(path):
        cur.execute(statement, row)


def load_executemany(cur: psycopg.Cursor, spec: SensorSpec, path: Path) -> None:
    """One call for all rows. psycopg 3 pipelines the statements, so it doesn't
    wait for each reply before sending the next row."""
    cur.executemany(insert_statement(spec), parse_rows(path))


def load_copy(cur: psycopg.Cursor, spec: SensorSpec, path: Path) -> None:
    """Stream the raw file; Postgres parses it. Same statement as the loader."""
    statement = sql.SQL("COPY {} ({}) FROM STDIN (FORMAT csv, NULL 'nan')").format(
        sql.Identifier(spec.staging_table),
        sql.SQL(", ").join(map(sql.Identifier, spec.columns)),
    )
    with path.open("rb") as f, cur.copy(statement) as copy:
        while chunk := f.read(CHUNK):
            copy.write(chunk)


METHODS: dict[str, Callable[[psycopg.Cursor, SensorSpec, Path], None]] = {
    "insert_row_by_row": load_row_by_row,
    "executemany": load_executemany,
    "copy": load_copy,
}


# --- runner ---------------------------------------------------------------------


def time_once(
    conn: psycopg.Connection, method: str, spec: SensorSpec, path: Path, expected: int
) -> float:
    """Load the file once inside a transaction that is always rolled back."""
    with conn.transaction(force_rollback=True), conn.cursor() as cur:
        cur.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(spec.staging_table)))

        started = time.perf_counter()
        METHODS[method](cur, spec, path)
        seconds = time.perf_counter() - started

        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(spec.staging_table)))

        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"{method}: count query returned no row")
        loaded = row[0]
    if loaded != expected:
        raise RuntimeError(f"{method}: loaded {loaded:,} rows, file has {expected:,}")
    return seconds


def run(session: str, spec: SensorSpec, repeat: int) -> list[dict]:
    # downloads only if not already in the buffer
    folder = fetch_session(session, WorkBuffer())
    path = folder / spec.file
    rows = count_lines(path)
    log.info(
        "%s / %s: %s rows, %.1f MiB", session, spec.file, f"{rows:,}", path.stat().st_size / 2**20
    )

    results = []
    names = list(METHODS)

    with db.connect() as conn:
        db.apply_schema(conn)
        for r in range(repeat):
            order = names[r % len(names) :] + names[: r % len(names)]  # rotate the order
            for method in order:
                seconds = time_once(conn, method, spec, path, rows)
                log.info(
                    "repeat %d  %-18s %8.2f s  %10s rows/s",
                    r + 1,
                    method,
                    seconds,
                    f"{rows / seconds:,.0f}",
                )
                results.append(
                    {
                        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
                        "session": session,
                        "table": spec.table,
                        "method": method,
                        "repeat": r + 1,
                        "rows": rows,
                        "seconds": round(seconds, 4),
                        "rows_per_s": round(rows / seconds),
                        "machine": f"{platform.machine()} x{os.cpu_count()}",
                    }
                )
    return results


def save(results: list[dict], path: Path = RESULTS_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()

    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))

        if new_file:
            writer.writeheader()
        writer.writerows(results)


def summarize(results: list[dict]) -> None:
    medians = {
        m: statistics.median(r["rows_per_s"] for r in results if r["method"] == m) for m in METHODS
    }
    baseline = medians["insert_row_by_row"]
    print(f"\n{'method':<20}{'median rows/s':>15}{'vs INSERT':>12}")
    print("-" * 47)
    for method, value in sorted(medians.items(), key=lambda kv: kv[1]):
        print(f"{method:<20}{value:>15,.0f}{value / baseline:>11.1f}x")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare INSERT, executemany and COPY load speed.")
    parser.add_argument("session", help="e.g. 2012-01-08")
    parser.add_argument("--table", default="imu", choices=[s.table for s in SENSORS])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--cleanup", action="store_true", help="delete the session's files afterwards"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    spec = next(s for s in SENSORS if s.table == args.table)
    results = run(args.session, spec, args.repeat)
    save(results)
    summarize(results)
    print(f"\nAll runs appended to {RESULTS_CSV}")
    if args.cleanup:
        WorkBuffer().release(args.session)


if __name__ == "__main__":
    main()
