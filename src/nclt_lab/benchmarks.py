"""Step 7: the query benchmark harness.

    python -m nclt_lab.benchmarks params                        # once: fixed query parameters
    python -m nclt_lab.benchmarks run --label baseline          # no indexes, all core queries
    python -m nclt_lab.benchmarks run --label q1_btree --setup q1_btree --queries q1,q5_lateral
    python -m nclt_lab.benchmarks summary --label baseline

What one `run` does:
1. Drops every `bench_*` index (unless --keep), then runs the --setup files from
   sql/experiments/, recording build time and index size in reports/bench/setups.csv.
2. Warm runs: one unmeasured warm-up, then --warm timed runs per query.
3. Cold runs: --cold cycles of "restart Postgres, run every query once, in random order".
4. Plans: one EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) per query and cache state, saved
   to plans/<label>/ and summarized in reports/bench/plans.csv. Plans are captured
   in separate runs because EXPLAIN ANALYZE's instrumentation slows queries down.
5. Every timed run is appended to reports/bench/runs.csv (one row per run).

Design choices worth knowing for interviews:
- prepare_threshold=None: psycopg normally turns a statement into a server-side
  prepared statement after 5 executions, and Postgres may then switch to a
  *generic* plan. That would change plans in the middle of a benchmark and make
  them differ from EXPLAIN's. Disabling it keeps one plan type throughout.
- Parameters come from bench/params.json, generated once with a fixed seed, so
  every label measures exactly the same inputs.
- Latency is measured around execute + fetchall: what a client actually waits for.
  EXPLAIN's "Execution Time" is server-side only; the README can show both.
- "Cold" means Postgres was restarted (shared_buffers empty). The OS page cache can
  survive a restart; plans.csv records shared reads and I/O read time so you can
  tell a truly cold read (I/O time > 0) from one served by the page cache.
"""

import argparse
import csv
import json
import logging
import os
import platform
import random
import shlex
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from nclt_lab.sensors import SENSORS

log = logging.getLogger(__name__)

QUERIES_DIR = Path("sql/queries")
EXPERIMENTS_DIR = Path("sql/experiments")
PARAMS_JSON = Path("bench/params.json")
RUNS_CSV = Path("reports/bench/runs.csv")
SETUPS_CSV = Path("reports/bench/setups.csv")
PLANS_CSV = Path("reports/bench/plans.csv")
PLANS_DIR = Path("plans")

DEFAULT_QUERIES = ("q1", "q2", "q3", "q4", "q5_row_number")
STATEMENT_TIMEOUT = "10min"  # a pathological baseline can't hang the run
# How to restart Postgres for cold runs. Needs the Docker CLI in the dev container
# (devcontainer feature docker-outside-of-docker). Override for other setups.
RESTART_CMD = os.environ.get("NCLT_RESTART_CMD", "docker restart nclt_pg")
MACHINE = os.environ.get("NCLT_MACHINE", f"{platform.machine()} x{os.cpu_count()}")

RUN_COLUMNS = [
    "started_at",
    "label",
    "cell_hash",
    "query",
    "param_index",
    "cache_state",
    "repetition",
    "latency_ms",
    "rows_returned",
    "error",
    "mem_limit_mb",
    "shared_buffers",
    "work_mem",
    "jit",
    "parallel_workers",
    "technique",
    "window_s",
    "sessions_loaded",
    "machine",
    "pg_version",
    "git_sha",
]


# --- small helpers ---------------------------------------------------------------


def connect(timeout_s: int = 10) -> psycopg.Connection:
    """Benchmark connection: autocommit, no auto-prepare, statement timeout."""
    conn = psycopg.connect("", autocommit=True, prepare_threshold=None, connect_timeout=timeout_s)
    conn.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
    return conn


def load_query(name: str) -> str:
    return (QUERIES_DIR / f"{name}.sql").read_text()


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def server_conditions(conn: psycopg.Connection) -> dict:
    """The settings that change results; recorded on every row."""
    show = {
        name: conn.execute(f"SHOW {name}").fetchone()[0]
        for name in ("shared_buffers", "work_mem", "jit", "max_parallel_workers_per_gather")
    }
    (sessions,) = conn.execute("SELECT count(*) FROM dim_session").fetchone()
    return {
        "shared_buffers": show["shared_buffers"],
        "work_mem": show["work_mem"],
        "jit": show["jit"],
        "parallel_workers": show["max_parallel_workers_per_gather"],
        "sessions_loaded": sessions,
        "pg_version": conn.info.server_version,
        "machine": MACHINE,
        "git_sha": git_sha(),
    }


def append_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or list(rows[0])
    new_file = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- parameters --------------------------------------------------------------------


def make_params(
    spans: list[tuple[int, int, int]],
    min_error_m: float,
    n: int = 10,
    seed: int = 42,
    window_s: float = 60.0,
) -> dict[str, list[dict]]:
    """Deterministic parameter sets. `spans` = (session_id, start_utime, end_utime).

    window_s <= 0 means "the whole session" (used by the Explorer's widest window).
    Same seed + same spans -> identical output, which is what makes runs comparable.
    """
    rng = random.Random(seed)

    def window() -> dict:
        sid, lo, hi = rng.choice(spans)
        width_us = int(window_s * 1e6)
        if window_s <= 0 or hi - lo <= width_us:
            return {"session_id": sid, "t1": lo, "t2": hi}
        t1 = rng.randint(lo, hi - width_us)
        return {"session_id": sid, "t1": t1, "t2": t1 + width_us}

    q1 = [window() for _ in range(n)]
    return {
        "q1": q1,
        "q1_shuffled": q1,
        "q1_utime_only": [{"t1": p["t1"], "t2": p["t2"]} for p in q1],
        "q2": [{"min_error_m": min_error_m}] * n,
        "q2_filter_first": [{"min_error_m": min_error_m}] * n,
        "q4": [{"threshold_us": 2_000_000}] * n,
        # Queries not listed here take no parameters. q6 (a point + radius) is
        # added by write_params() once sql/spatial.sql has been applied.
    }


def write_params(n: int, seed: int, window_s: float) -> dict:
    with connect() as conn:
        spans = conn.execute(
            "SELECT session_id, start_utime, end_utime FROM dim_session ORDER BY session_id"
        ).fetchall()
        if not spans:
            raise SystemExit("dim_session is empty: ingest sessions before generating parameters")
        # Threshold from the data: the 99th percentile of RTK error, so Q2 returns ~1% of fixes.
        (p99,) = conn.execute(
            "SELECT percentile_cont(0.99) WITHIN GROUP (ORDER BY error_m) FROM gps_rtk_err"
        ).fetchone()
        params = make_params(spans, float(p99 or 0.0), n=n, seed=seed, window_s=window_s)

        from nclt_lab import spatial  # step 8: only once the geom columns exist

        if spatial.has_geom(conn):
            params["q6"] = spatial.point_params(conn, n=n, seed=seed)
    document = {
        "generated_at": now(),
        "seed": seed,
        "window_s": window_s,
        "n": n,
        "queries": params,
    }
    PARAMS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_JSON.write_text(json.dumps(document, indent=2) + "\n")
    log.info("wrote %s (%d sets per query, seed %d)", PARAMS_JSON, n, seed)
    return document


def load_params() -> dict[str, list[dict]]:
    if not PARAMS_JSON.exists():
        raise SystemExit(f"{PARAMS_JSON} missing: run `python -m nclt_lab.benchmarks params` first")
    return json.loads(PARAMS_JSON.read_text())["queries"]


def param_sets(params: dict[str, list[dict]], query: str) -> list[dict | None]:
    return params.get(query) or [None]


# --- setups (indexes, summary tables) -----------------------------------------------


def drop_bench_indexes(conn: psycopg.Connection) -> list[str]:
    names = [
        r[0]
        for r in conn.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND indexname LIKE 'bench\\_%'"
        )
    ]
    for name in names:
        conn.execute(f'DROP INDEX IF EXISTS "{name}"')
    return names


def sensor_table_indexes(conn: psycopg.Connection) -> list[tuple[str, str, int]]:
    tables = [s.table for s in SENSORS] + ["imu_shuffled", "session_odometry_summary"]
    return conn.execute(
        """
        SELECT c.relname AS table_name, i.relname AS index_name, pg_relation_size(i.oid)
        FROM pg_index x
        JOIN pg_class i ON i.oid = x.indexrelid
        JOIN pg_class c ON c.oid = x.indrelid
        WHERE c.relname = ANY(%s)
        ORDER BY 1, 2
        """,
        (tables,),
    ).fetchall()


def apply_setups(conn: psycopg.Connection, label: str, setups: list[str], keep: bool) -> None:
    if not keep:
        dropped = drop_bench_indexes(conn)
        if dropped:
            log.info("dropped %d bench_* indexes: %s", len(dropped), ", ".join(dropped))

    rows = []
    for setup in setups:
        statement = (EXPERIMENTS_DIR / f"{setup}.sql").read_text()
        started = time.perf_counter()
        conn.execute(statement)
        seconds = time.perf_counter() - started
        log.info("setup %s: %.1f s", setup, seconds)
        rows.append(
            {
                "measured_at": now(),
                "label": label,
                "setup": setup,
                "index_name": "",
                "size_bytes": "",
                "seconds": round(seconds, 3),
            }
        )

    for table, index, size in sensor_table_indexes(conn):
        rows.append(
            {
                "measured_at": now(),
                "label": label,
                "setup": f"present on {table}",
                "index_name": index,
                "size_bytes": size,
                "seconds": "",
            }
        )
        log.info("index present: %s.%s (%.1f MiB)", table, index, size / 2**20)
    append_csv(SETUPS_CSV, rows)


# --- timing ---------------------------------------------------------------------------


def timed(conn: psycopg.Connection, sql_text: str, params: dict | None) -> tuple[float, int]:
    started = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(sql_text, params)
        rows = len(cur.fetchall())
    return (time.perf_counter() - started) * 1000, rows


def run_row(
    base: dict,
    query: str,
    index: int,
    cache: str,
    rep: int,
    latency_ms: float | None,
    rows: int | None,
    error: str = "",
) -> dict:
    return {
        **base,
        "started_at": now(),
        "query": query,
        "param_index": index,
        "cache_state": cache,
        "repetition": rep,
        "latency_ms": "" if latency_ms is None else round(latency_ms, 3),
        "rows_returned": "" if rows is None else rows,
        "error": error,
    }


def run_warm(conn, base: dict, queries: list[str], params, n: int) -> Iterator[dict]:
    for query in queries:
        text, sets = load_query(query), param_sets(params, query)
        timed(conn, text, sets[0])  # warm-up: not recorded
        for rep in range(n):
            i = rep % len(sets)
            latency, rows = timed(conn, text, sets[i])
            yield run_row(base, query, i, "warm", rep + 1, latency, rows)
        log.info("warm %-16s done (%d runs)", query, n)


def restart_postgres(wait_s: float = 120.0) -> None:
    """Restart the server so shared_buffers is empty, then wait until it accepts connections."""
    result = subprocess.run(shlex.split(RESTART_CMD), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"restart failed ({RESTART_CMD!r}): {result.stderr.strip()}")
    deadline = time.monotonic() + wait_s
    while True:
        try:
            psycopg.connect("", connect_timeout=2).close()
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def run_cold(base: dict, queries: list[str], params, n: int, seed: int = 7) -> Iterator[dict]:
    rng = random.Random(seed)
    texts = {q: load_query(q) for q in queries}
    for cycle in range(n):
        restart_postgres()
        order = queries[:]
        rng.shuffle(order)  # no query always runs first after a restart
        with connect() as conn:
            for query in order:
                sets = param_sets(params, query)
                i = cycle % len(sets)
                try:
                    latency, rows = timed(conn, texts[query], sets[i])
                    yield run_row(base, query, i, "cold", cycle + 1, latency, rows)
                except psycopg.OperationalError as err:  # e.g. the server was OOM-killed
                    yield run_row(base, query, i, "cold", cycle + 1, None, None, str(err)[:200])
                    break
        log.info("cold cycle %d/%d done", cycle + 1, n)


# --- plans ------------------------------------------------------------------------


def plan_summary(plan: list[dict]) -> dict:
    """Condense EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) output into the numbers the
    README needs. Buffer counts on the root node include all its children."""
    root = plan[0]["Plan"]
    node_types: list[str] = []

    def walk(node: dict) -> None:
        node_types.append(node["Node Type"])
        for child in node.get("Plans", []):
            walk(child)

    walk(root)
    io_read_ms = root.get("Shared I/O Read Time", root.get("I/O Read Time", 0.0))  # PG17+ / PG16
    return {
        "execution_ms": plan[0].get("Execution Time"),
        "planning_ms": plan[0].get("Planning Time"),
        "root_node": root["Node Type"],
        "node_types": " > ".join(dict.fromkeys(node_types)),  # unique, in tree order
        "shared_hit": root.get("Shared Hit Blocks", 0),
        "shared_read": root.get("Shared Read Blocks", 0),
        "io_read_ms": io_read_ms,
        "temp_written": root.get("Temp Written Blocks", 0),
        "rows": root.get("Actual Rows"),
    }


def capture_plan(conn, label: str, query: str, params: dict | None, cache: str) -> dict:
    """EXPLAIN with the parameters inlined as literals (ClientCursor), which gives
    the same custom plan the timed runs get."""
    with psycopg.ClientCursor(conn) as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + load_query(query), params)
        plan = cur.fetchone()[0]
    folder = PLANS_DIR / label
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{query}_{cache}.json").write_text(json.dumps(plan, indent=2) + "\n")
    return {
        "measured_at": now(),
        "label": label,
        "query": query,
        "cache_state": cache,
        **plan_summary(plan),
    }


def capture_plans(label: str, queries: list[str], params, cold: bool) -> None:
    rows = []
    with connect() as conn:
        for query in queries:
            rows.append(capture_plan(conn, label, query, param_sets(params, query)[0], "warm"))
    if cold:
        for query in queries:
            restart_postgres()  # one restart per query, so no plan inherits another's cache
            with connect() as conn:
                rows.append(capture_plan(conn, label, query, param_sets(params, query)[0], "cold"))
    append_csv(PLANS_CSV, rows)
    for r in rows:
        log.info(
            "plan %-16s %-4s %9.1f ms  read=%-7s io=%.1f ms  %s",
            r["query"],
            r["cache_state"],
            r["execution_ms"],
            r["shared_read"],
            r["io_read_ms"],
            r["root_node"],
        )


# --- entry points ------------------------------------------------------------------


def run(
    label: str,
    queries: list[str],
    setups: list[str],
    keep: bool,
    warm: int,
    cold: int,
    plans: bool = True,
    extra: dict | None = None,
) -> None:
    params = load_params()
    with connect() as conn:
        apply_setups(conn, label, setups, keep)
        base = {**server_conditions(conn), **(extra or {}), "label": label}
        rows = list(run_warm(conn, base, queries, params, warm))
    append_csv(RUNS_CSV, rows, RUN_COLUMNS)

    if cold:
        rows = list(run_cold(base, queries, params, cold))
        append_csv(RUNS_CSV, rows, RUN_COLUMNS)

    if plans:
        capture_plans(label, queries, params, cold=cold > 0)
    summary(label)


def summary(label: str | None = None) -> None:
    import duckdb  # imported here: only the summary needs it

    if not RUNS_CSV.exists():
        raise SystemExit(f"{RUNS_CSV} not found")
    where = "WHERE label = $label" if label else ""
    result = duckdb.execute(
        f"""
        SELECT label, query, cache_state,
               count(latency_ms)                               AS runs,
               round(quantile_cont(latency_ms, 0.50), 2)       AS p50_ms,
               round(quantile_cont(latency_ms, 0.95), 2)       AS p95_ms,
               count(*) - count(latency_ms)                    AS errors
        FROM read_csv($path, header = true, all_varchar = false)
        {where}
        GROUP BY ALL
        ORDER BY label, query, cache_state DESC
        """,
        {"path": str(RUNS_CSV), **({"label": label} if label else {})},
    ).fetchall()
    print(
        f"\n{'label':<18}{'query':<18}{'cache':<6}{'runs':>6}{'p50_ms':>12}{'p95_ms':>12}{'errors':>8}"
    )
    for lab, q, cache, n, p50, p95, errors in result:
        print(f"{lab:<18}{q:<18}{cache:<6}{n:>6}{p50:>12,.2f}{p95:>12,.2f}{errors:>8}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    parser = argparse.ArgumentParser(description="Step 7: query benchmarks")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("params", help="generate bench/params.json once")
    p.add_argument("--n", type=int, default=10, help="parameter sets per query")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--window-s", type=float, default=60.0, help="Q1 time window width")

    r = commands.add_parser("run", help="one labeled benchmark run")
    r.add_argument("--label", required=True, help="e.g. baseline, q1_btree, final")
    r.add_argument("--queries", default=",".join(DEFAULT_QUERIES))
    r.add_argument("--setup", default="", help="comma-separated files in sql/experiments/")
    r.add_argument("--keep", action="store_true", help="don't drop existing bench_* indexes")
    r.add_argument("--warm", type=int, default=30)
    r.add_argument("--cold", type=int, default=30, help="0 skips cold runs (no restarts)")
    r.add_argument("--no-plans", action="store_true")

    s = commands.add_parser("summary", help="p50/p95 per query from runs.csv")
    s.add_argument("--label")

    args = parser.parse_args()
    if args.command == "params":
        write_params(args.n, args.seed, args.window_s)
    elif args.command == "run":
        run(
            args.label,
            [q for q in args.queries.split(",") if q],
            [x for x in args.setup.split(",") if x],
            args.keep,
            args.warm,
            args.cold,
            plans=not args.no_plans,
        )
    else:
        summary(args.label)


def stream_csv(file_path: Path):
    with open(file_path, encoding="utf-8") as csv_file:
        reader = csv.reader(csv_file)
        yield from reader


if __name__ == "__main__":
    main()
