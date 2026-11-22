"""
[OVERVIEW]
Step 6: build the sensor_gaps table (one row per dropout, per sensor, per session).

    python -m nclt_lab.main gaps                 # rebuild for every sensor, then reconcile
    python -m nclt_lab.main gaps --percentiles   # print interval percentiles to choose thresholds
    python -m nclt_lab.main gaps --sensor gps    # one sensor only

How it works:
- Each sensor is rebuilt in ONE transaction: delete its rows, insert Q4's result.
  Running it again gives identical rows instead of duplicates.
- A gap is an interval between consecutive readings longer than the sensor's
  `gap_threshold_s` in sensors.py. The interval is computed per session
  (PARTITION BY session_id), so two sessions are never compared.
- `gap_start_utime` is the last reading BEFORE the gap (its real timestamp), and
  `gap_end_utime` is the first reading AFTER it. Step 8 joins on that.
- Afterwards it checks the results against reports/profile.csv, which was computed
  from the raw files before loading. Two independent paths agreeing is the evidence.
"""

import argparse
import csv
import logging
from pathlib import Path

import psycopg
from psycopg import sql

from nclt_lab import db
from nclt_lab.config import PROFILE_CSV, session_id
from nclt_lab.sensors import SENSORS, SensorSpec

log = logging.getLogger(__name__)
COVERAGE_CSV = Path("reports/gap_coverage.csv")

REBUILD_SQL = """
INSERT INTO sensor_gaps (sensor, session_id, gap_start_utime, gap_end_utime, threshold_s)
SELECT %(sensor)s, session_id, prev_utime, utime, %(threshold_s)s
FROM (
    SELECT session_id,
           utime,
           lag(utime) OVER (PARTITION BY session_id ORDER BY utime) AS prev_utime
    FROM {table}
) AS readings
WHERE utime - prev_utime > %(threshold_us)s
"""

PERCENTILES_SQL = """
SELECT session_id,
       count(*)                                                       AS intervals,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY interval_us) / 1e3 AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY interval_us) / 1e3 AS p95_ms,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY interval_us) / 1e3 AS p99_ms,
       max(interval_us) / 1e3                                           AS max_ms
FROM (
    SELECT session_id,
           utime - lag(utime) OVER (PARTITION BY session_id ORDER BY utime) AS interval_us
    FROM {table}
) AS t
WHERE interval_us IS NOT NULL
GROUP BY session_id
ORDER BY session_id
"""


# --- core functions ----------------------------------------------------------------


def interval_percentiles(conn: psycopg.Connection, spec: SensorSpec) -> list[tuple]:
    """Per-session spacing between readings. Use this to choose gap_threshold_s.

    A good threshold sits clearly above the sensor's NORMAL pauses (p99) and
    below the dropouts you care about. For bursty sensors (RTK) the p99 can be
    far above the median, so the median alone would flag normal pauses as gaps.
    """
    query = sql.SQL(PERCENTILES_SQL).format(table=sql.Identifier(spec.table))
    return conn.execute(query).fetchall()


def rebuild(conn: psycopg.Connection, spec: SensorSpec) -> int:
    """Replace this sensor's rows in sensor_gaps. Returns the number of gaps found."""
    query = sql.SQL(REBUILD_SQL).format(table=sql.Identifier(spec.table))

    params = {
        "sensor": spec.table,
        "threshold_s": spec.gap_threshold_s,
        "threshold_us": int(spec.gap_threshold_s * 1e6),
    }

    with conn.transaction():
        conn.execute("DELETE FROM sensor_gaps WHERE sensor = %s", (spec.table,))
        cur = conn.execute(query, params)
        return cur.rowcount


def reconcile_with_profile(conn: psycopg.Connection, profile_csv: Path = PROFILE_CSV) -> list[str]:
    """Compare the longest stored gap per sensor and session with profile.csv.

    profile.csv's `max_gap_s` came from the raw CSV files (DuckDB), before loading.
    Whenever it is above the sensor's threshold, the longest gap in sensor_gaps
    for that session must be the same interval. Returns a list of mismatches.
    """
    if not profile_csv.exists():
        return [f"{profile_csv} not found; run profiling first"]

    by_file = {spec.file: spec for spec in SENSORS}
    stored = {
        (sensor, sid): max_gap
        for sensor, sid, max_gap in conn.execute(
            "SELECT sensor, session_id, max(gap_s) FROM sensor_gaps GROUP BY sensor, session_id"
        )
    }

    problems = []
    with profile_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            spec = by_file.get(row["file"])
            # a file the pipeline doesn't load (kvh.csv, wheels.csv, ...)
            if spec is None:
                continue

            expected = float(row["max_gap_s"])
            if expected <= spec.gap_threshold_s:
                # no gap above the threshold, so nothing should be stored
                continue

            actual = stored.get((spec.table, session_id(row["session"])))
            if actual is None or abs(actual - expected) > 1e-6:
                problems.append(
                    f"{row['session']} {spec.table}: profile max gap {expected:.6f} s, "
                    f"sensor_gaps has {actual}"
                )
    return problems


def coverage(conn: psycopg.Connection) -> list[tuple]:
    """Share of each session's duration lost to dropouts, per sensor.
    This feeds the gaps heatmap chart (report.py)."""
    return conn.execute(
        """
        SELECT s.session_date::text,
               g.sensor,
               count(*)                         AS gaps,
               sum(g.gap_s)                     AS gap_s_total,
               max(g.gap_s)                     AS gap_s_max,
               100 * sum(g.gap_s) / s.duration_s AS pct_of_session
        FROM sensor_gaps g
        JOIN dim_session s USING (session_id)
        GROUP BY s.session_date, s.duration_s, g.sensor
        ORDER BY s.session_date, g.sensor
        """
    ).fetchall()


def save_coverage(rows: list[tuple], path: Path = COVERAGE_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["session", "sensor", "gaps", "gap_s_total", "gap_s_max", "pct_of_session"])
        writer.writerows(rows)


# --- command line ------------------------------------------------------------------


def print_percentiles(conn: psycopg.Connection, specs: list[SensorSpec]) -> None:
    for spec in specs:
        print(f"\n{spec.table}  (threshold now {spec.gap_threshold_s} s)")
        print(
            f"{'session_id':>10} {'intervals':>10} {'p50_ms':>9} {'p95_ms':>9} "
            f"{'p99_ms':>9} {'max_ms':>11}"
        )

        for sid, n, p50, p95, p99, mx in interval_percentiles(conn, spec):
            print(f"{sid:>10} {n:>10,} {p50:>9.1f} {p95:>9.1f} {p99:>9.1f} {mx:>11.1f}")

    # TODO: decide gps_rtk's threshold from this output (it reports in bursts),
    #       update sensors.py, and write the reasoning in data/README.md.


def run(sensor: str | None = None, percentiles: bool = False) -> int:
    specs = [s for s in SENSORS if sensor in (None, s.table)]
    with db.connect() as conn:
        db.apply_schema(conn)

        if percentiles:
            print_percentiles(conn, specs)
            return 0

        for spec in specs:
            found = rebuild(conn, spec)
            log.info(
                "[INFO] %s: %s gaps above %.3f s", spec.table, f"{found:,}", spec.gap_threshold_s
            )

        problems = reconcile_with_profile(conn)
        for problem in problems:
            log.error("[ERROR] reconcile: %s", problem)

        if not problems:
            log.info("[INFO] reconcile: every profiled max gap matches sensor_gaps")

        rows = coverage(conn)
        save_coverage(rows)
        log.info("[INFO] coverage for %d sensor-sessions written to %s", len(rows), COVERAGE_CSV)

        # TODO (spot check): the 634s GPS gap on 2012-08-04 should be the longest
        # GPS gap in that session. Write a query that confirms it.
        return 1 if problems else 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sensor", choices=[s.table for s in SENSORS], help="one sensor only")
    parser.add_argument("--percentiles", action="store_true", help="print interval percentiles")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    p = argparse.ArgumentParser(description="Step 6: build sensor_gaps")
    add_arguments(p)

    a = p.parse_args()

    raise SystemExit(run(a.sensor, a.percentiles))
