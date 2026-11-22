"""
[OVERVIEW]
Profile each sensor CSV in one NCLT session: rows, duration, actual rate, gaps.

[USAGE]
python3 -m nclt_lab.profile 2012-01-08


[PROJECT OVERVIEW]

"""

import argparse
import csv
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import duckdb

# (header, dict key, alignment, width, number format)
COLUMNS = [
    ("session", "session", "<", 10, ""),
    ("file", "file", "<", 24, ""),
    ("rows", "rows", ">", 10, ","),
    ("duration_s", "duration_s", ">", 11, ",.0f"),
    ("actual_hz", "actual_hz", ">", 10, ".1f"),
    ("median_ms", "median_dt_ms", ">", 10, ".1f"),
    ("gaps", "gaps", ">", 8, ","),
    ("max_gap_s", "max_gap_s", ">", 10, ".2f"),
    ("fields", "fields", ">", 7, ""),
]

LOADED_FILES = {
    "ms25.csv": "imu",
    "gps.csv": "gps",
    "gps_rtk.csv": "gps_rtk",
    "gps_rtk_err.csv": "gps_rtk_err",
    "odometry_mu_100hz.csv": "odometry",
}

DATA = Path("data/sensor_data")

QUERY = """
    -- one row per sensor reading: just its timestamp
    WITH readings AS (
        SELECT CAST(utime_text AS BIGINT) AS utime
        FROM read_csv('{path}', header = false, all_varchar = true) AS raw(utime_text)
    ),
    -- time since the previous reading, in microseconds
    intervals AS (
        SELECT utime - lag(utime) OVER (ORDER BY utime) AS interval_us
        FROM readings
    ),
    -- the normal spacing between readings
    typical AS (
        SELECT MEDIAN(interval_us) AS median_interval_us
        FROM intervals
    )
    SELECT
        (SELECT COUNT(*) FROM readings)                                 AS row_count,
        (SELECT (MAX(utime) - MIN(utime)) / 1e6 FROM readings)          AS duration_s,
        (SELECT median_interval_us FROM typical)                        AS median_interval_us,
        
        (SELECT COUNT(*)
        FROM intervals, typical
        WHERE intervals.interval_us > 3 * typical.median_interval_us)   AS gap_count,
        
        (SELECT MAX(interval_us) / 1e6 FROM intervals)                  AS max_gap_s,
        (SELECT MIN(utime) FROM readings)                               AS start_utime,
        (SELECT MAX(utime) FROM readings)                               AS end_utime,
"""

# --------------------------------------------------------------------------------------------
# UTILITY FUNCTIONS
# --------------------------------------------------------------------------------------------


def human_bytes(n: float) -> str:
    """1536 -> '1.5 KiB', 498_073_600 -> '475.0 MiB'."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} PiB"


def utime_to_utc(utime: int) -> str:
    return datetime.fromtimestamp(utime / 1e6, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------------------------
# END OF UTILITY FUNCTIONS
# --------------------------------------------------------------------------------------------


# Provide compute table
def profile(csv_path: Path) -> dict:
    """Facts about one CSV file: size, rows, timing, gaps."""
    with csv_path.open() as f:
        first_line = f.readline().strip()

    result = duckdb.connect().execute(QUERY.format(path=csv_path)).fetchone()
    if result is None:
        raise RuntimeError(f"[ERROR] No profile result returned for {csv_path}")

    rows, duration_s, median_interval_us, gaps, max_gap_s, start_utime, end_utime = result

    file_bytes = csv_path.stat().st_size

    return {
        "file": csv_path.name,
        "rows": rows,
        "file_bytes": file_bytes,
        "bytes_per_row": file_bytes / rows if rows else 0.0,
        "duration_s": duration_s,
        "actual_hz": rows / duration_s if duration_s else 0.0,
        "median_dt_ms": median_interval_us / 1000,
        "gaps": gaps,
        "max_gap_s": max_gap_s,
        "start_utime": start_utime,
        "end_utime": end_utime,
        "fields": len(first_line.split(",")),
        "first_line": first_line,
    }


def group_by(results: list[dict], key: str) -> dict[str, list[dict]]:
    """Bucket base rows by one field, e.g. group_by(results, "file")."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        groups[r[key]].append(r)
    return dict(groups)


def rollup_by_sensor(results: list[dict]) -> list[dict]:
    """Rollup A: one row per file across all sessions (Postgres table sizing)."""
    summary = []
    for file, rows in sorted(group_by(results, "file").items()):
        total_rows = sum(r["rows"] for r in rows)
        total_bytes = sum(r["file_bytes"] for r in rows)
        total_duration_s = sum(r["duration_s"] for r in rows)
        hz_values = [r["actual_hz"] for r in rows]
        summary.append(
            {
                "file": file,
                "sessions": len(rows),
                "rows": total_rows,
                "file_bytes": total_bytes,
                "bytes_per_row": total_bytes / total_rows if total_rows else 0.0,
                "actual_hz": total_rows / total_duration_s if total_duration_s else 0.0,
                "hz_min": min(hz_values),
                "hz_max": max(hz_values),
                "gaps": sum(r["gaps"] for r in rows),
                "max_gap_s": max(r["max_gap_s"] for r in rows),
            }
        )
    return summary


def rollup_by_session(results: list[dict]) -> list[dict]:
    """Rollup B: one row per session across files (dim_session, D1 memory cap)."""
    summary = []
    for session, rows in sorted(group_by(results, "session").items()):
        start = min(r["start_utime"] for r in rows)
        end = max(r["end_utime"] for r in rows)
        summary.append(
            {
                "session": session,
                "files": len(rows),
                "rows": sum(r["rows"] for r in rows),
                "file_bytes": sum(r["file_bytes"] for r in rows),  # Mbs ->
                "start_utc": utime_to_utc(start),
                "end_utc": utime_to_utc(end),
                "duration_s": (end - start) / 1e6,
            }
        )
    return summary


def grand_total(results: list[dict]) -> dict:
    """Rollup C: totals for the tables P2 actually loads (cache-ratio input)."""
    loaded = [r for r in results if r["file"] in LOADED_FILES]
    return {
        "sessions": len({r["session"] for r in loaded}),
        "tables": len({r["file"] for r in loaded}),
        "rows": sum(r["rows"] for r in loaded),
        "file_bytes": sum(r["file_bytes"] for r in loaded),
    }


def save_results(results: list[dict], path: Path = Path("reports/profile.csv")) -> None:
    """Save the base table so later steps read it instead of re-profiling."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [key for key in results[0] if key != "first_line"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


def print_rows(title: str, rows: list[dict]) -> None:
    """Quick readable dump: ints with commas, floats with 2 decimals."""
    print(f"\n{title}")
    for r in rows:
        parts = []
        for key, value in r.items():
            if key.endswith("_bytes"):
                parts.append(f"{key}={human_bytes(value)}")
            elif isinstance(value, bool) or not isinstance(value, int | float):
                parts.append(f"{key}={value}")
            elif isinstance(value, int):
                parts.append(f"{key}={value:,}")
            else:
                parts.append(f"{key}={value:,.2f}")
        print("  " + "  ".join(parts))


def find_csvs(sessions: list[str]) -> list[tuple[str, Path]]:
    """Return (session, csv_path) pairs, sorted by session, then file name."""
    sessions = sorted(set(sessions))
    missing = [s for s in sessions if not any((DATA / s).glob("*.csv"))]

    if missing:
        sys.exit(
            f"No CSVs for: {', '.join(missing)}\n"
            f'Download first: make download DATE="{" ".join(missing)}"'
        )
    return [(s, csv) for s in sessions for csv in sorted((DATA / s).glob("*.csv"))]


def print_session_info_table(results: list[dict]) -> None:
    """"""
    header = "  ".join(f"{name:{align}{width}}" for name, _, align, width, _ in COLUMNS)
    print(header)
    print("-" * len(header))
    previous_session = None

    for r in results:
        if previous_session and r["session"] != previous_session:
            print()  # blank line between sessions
        previous_session = r["session"]
        print("  ".join(f"{r[key]:{align}{width}{fmt}}" for _, key, align, width, fmt in COLUMNS))


def check_consistency(results: list[dict]) -> None:
    """Warn when a file is missing from some sessions or changes column COUNT."""
    all_sessions = {r["session"] for r in results}

    for file in sorted({r["file"] for r in results}):
        rows = [r for r in results if r["file"] == file]
        absent = all_sessions - {r["session"] for r in rows}
        field_counts = sorted({r["fields"] for r in rows})

        if absent:
            print(f"[WARNING] {file} is missing from {', '.join(sorted(absent))}")
        if len(field_counts) > 1:
            print(f"[WARNING] {file} has different column counts across sessions: {field_counts}")


# --------------------------------------------------------------------------------------------
# POINT OF ENTRY
# --------------------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile NCLT sessions' sensor CSVs.")
    parser.add_argument(
        "sessions",
        nargs="*",
        default=["2012-01-08 2012-05-11 2012-08-04 2012-11-17 2013-02-23"],
        help="session dates, e.g. 2012-01-08 2012-05-11 (default: 2012-01-08)",
    )
    parser.add_argument("--first-lines", action="store_true", help="show each file's first line")
    args = parser.parse_args()

    results = [{"session": s, **profile(csv)} for s, csv in find_csvs(args.sessions)]
    save_results(results)

    print_session_info_table(results)
    print()
    check_consistency(results)

    loaded = [r for r in results if r["file"] in LOADED_FILES]
    print_rows("A. Per sensor (loaded tables)", rollup_by_sensor(loaded))
    print_rows("B. Per session (loaded tables)", rollup_by_session(loaded))
    print_rows("C. Grand total", [grand_total(results)])

    duckdb.sql("""
        SELECT file,
            sum(rows)                  AS rows,
            sum(rows) / sum(duration_s) AS actual_hz,
            min(actual_hz)             AS hz_min,
            max(actual_hz)             AS hz_max
        FROM read_csv('reports/profile.csv')
        GROUP BY file
        ORDER BY file
    """).show()
