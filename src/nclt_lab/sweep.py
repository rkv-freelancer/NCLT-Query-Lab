"""Step 9: run the Explorer sweep defined in bench/sweep.toml.

    python -m nclt_lab.sweep             # resumable: cells already in runs.csv are skipped
    python -m nclt_lab.sweep --dry-run   # list cells and the estimated run count

Each cell = one combination of technique x memory limit x Q1 window width. Its
runs go to the same reports/bench/runs.csv as step 7, with label "sweep-<name>",
the conditions in their own columns, and a `cell_hash`.

Resumability: a cell's hash is computed from its conditions. Before running a
cell, the sweep checks whether that hash is already in runs.csv; if so, it skips
it. Killing the sweep loses at most the cell in progress.

Memory changes use `docker update` on the running container (no restart needed).
Override NCLT_SET_MEMORY_CMD for other environments; "{mb}" is replaced.
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import shlex
import statistics
import subprocess
import tomllib
from pathlib import Path

import psycopg

from nclt_lab import benchmarks as bench

log = logging.getLogger(__name__)
SWEEP_TOML = Path("bench/sweep.toml")
SET_MEMORY_CMD = os.environ.get(
    "NCLT_SET_MEMORY_CMD", "docker update --memory {mb}m --memory-swap {mb}m nclt_pg"
)


def cell_hash(conditions: dict) -> str:
    """Stable 12-character ID for a set of conditions (key order doesn't matter)."""
    return hashlib.sha1(json.dumps(conditions, sort_keys=True).encode()).hexdigest()[:12]


def done_hashes(path: Path = bench.RUNS_CSV) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="") as f:
        return {row["cell_hash"] for row in csv.DictReader(f) if row.get("cell_hash")}


def set_memory(mb: int) -> None:
    command = shlex.split(SET_MEMORY_CMD.format(mb=mb))
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"setting memory to {mb} MB failed: {result.stderr.strip()}")


def plan_cells(cfg: dict) -> list[dict]:
    """All cells, in loop order: technique (outer), memory, window (inner)."""
    s = cfg["sweep"]
    cells = []
    for technique in s["techniques"]:
        for mb in s["mem_limit_mb"]:
            for window in s["windows_s"]:
                cells.append(
                    {
                        "sweep": s["name"],
                        "query": s["query"],
                        "technique": technique,
                        "mem_limit_mb": mb,
                        "window_s": window,
                        "warm": s["warm"],
                        "cold": s["cold"],
                    }
                )
    return cells


def window_params(spans, window_s: float, cfg: dict) -> dict[str, list[dict]]:
    s = cfg["sweep"]
    q1 = bench.make_params(spans, 0.0, n=s["n_params"], seed=s["seed"], window_s=window_s)["q1"]
    return {s["query"]: q1}


def measure_cell(cell: dict, spans, cfg: dict, label: str) -> list[dict]:
    """Warm and cold runs for one cell. An OOM kill is recorded as a result."""
    params = window_params(spans, cell["window_s"], cfg)
    extra = {k: cell[k] for k in ("mem_limit_mb", "technique", "window_s")}
    extra["cell_hash"] = cell_hash(cell)
    rows = []
    try:
        with bench.connect() as conn:
            base = {**bench.server_conditions(conn), **extra, "label": label}
            rows += list(bench.run_warm(conn, base, [cell["query"]], params, cell["warm"]))
        rows += list(bench.run_cold(base, [cell["query"]], params, cell["cold"]))
    except psycopg.OperationalError as err:  # most likely the container hit its memory cap
        rows.append(
            bench.run_row(
                {**extra, "label": label}, cell["query"], 0, "warm", 0, None, None, f"{err}"[:200]
            )
        )
        bench.restart_postgres()
    return rows


def warm_p95(rows: list[dict]) -> float:
    values = [r["latency_ms"] for r in rows if r["cache_state"] == "warm" and r["latency_ms"] != ""]
    return statistics.quantiles(values, n=20)[-1] if len(values) >= 2 else float("nan")


def run(dry_run: bool = False) -> None:
    cfg = tomllib.loads(SWEEP_TOML.read_text())
    label = f"sweep-{cfg['sweep']['name']}"
    cells = plan_cells(cfg)
    done = done_hashes()
    todo = [c for c in cells if cell_hash(c) not in done]
    per_cell = cfg["sweep"]["warm"] + cfg["sweep"]["cold"]
    log.info(
        "%d cells, %d already done, %d to run (%d timed runs each)",
        len(cells),
        len(cells) - len(todo),
        len(todo),
        per_cell,
    )
    if dry_run:
        for c in todo:
            print(cell_hash(c), c)
        return

    with bench.connect() as conn:
        spans = conn.execute(
            "SELECT session_id, start_utime, end_utime FROM dim_session ORDER BY session_id"
        ).fetchall()

    b = cfg["baseline"]
    check = {
        "sweep": cfg["sweep"]["name"],
        "query": cfg["sweep"]["query"],
        "technique": b["technique"],
        "mem_limit_mb": b["mem_limit_mb"],
        "window_s": b["window_s"],
        "warm": cfg["sweep"]["warm"],
        "cold": 0,
    }

    def baseline(phase: str) -> float:
        with bench.connect() as conn:
            bench.apply_setups(
                conn, f"{label}-check", cfg["techniques"][b["technique"]], keep=False
            )
        set_memory(b["mem_limit_mb"])
        rows = measure_cell({**check, "phase": phase}, spans, cfg, f"{label}-check")
        bench.append_csv(bench.RUNS_CSV, rows, bench.RUN_COLUMNS)
        return warm_p95(rows)

    start_p95 = baseline("start")
    current_technique = None
    for i, cell in enumerate(todo, 1):
        if cell["technique"] != current_technique:  # index builds: once per technique
            with bench.connect() as conn:
                bench.apply_setups(conn, label, cfg["techniques"][cell["technique"]], keep=False)
            current_technique = cell["technique"]
        set_memory(cell["mem_limit_mb"])
        rows = measure_cell(cell, spans, cfg, label)
        bench.append_csv(bench.RUNS_CSV, rows, bench.RUN_COLUMNS)  # written per cell: resumable
        log.info(
            "cell %d/%d %s done: %s",
            i,
            len(todo),
            cell_hash(cell),
            {k: cell[k] for k in ("technique", "mem_limit_mb", "window_s")},
        )

    end_p95 = baseline("end")
    drift = abs(end_p95 - start_p95) / start_p95 if start_p95 else float("nan")
    log.info(
        "baseline warm p95: start %.2f ms, end %.2f ms (drift %.0f%%)",
        start_p95,
        end_p95,
        100 * drift,
    )
    if drift > b["drift_limit"]:
        log.warning(
            "drift above %.0f%%: the machine changed during the sweep; rerun it",
            100 * b["drift_limit"],
        )
    set_memory(b["mem_limit_mb"])  # leave the container at the baseline setting


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    parser = argparse.ArgumentParser(description="Step 9: Explorer sweep")
    parser.add_argument("--dry-run", action="store_true")
    run(parser.parse_args().dry_run)


if __name__ == "__main__":
    main()
