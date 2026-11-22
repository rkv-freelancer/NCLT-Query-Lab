"""Step 9: turn the sweep's runs into the P2 Explorer page.

    python -m nclt_lab.explorer

runs.csv -> reports/explorer/cells.json -> docs/explorer/index.html

Needs Altair:  uv add altair
Publish: commit docs/explorer/index.html, then in the GitHub repo settings enable
Pages from the main branch, /docs folder. Link the page from the README.

The page only ever shows measured cells. A control combination that wasn't
measured shows no line, never an estimate.
"""

import json
import logging
from pathlib import Path

import duckdb

from nclt_lab.benchmarks import RUNS_CSV

log = logging.getLogger(__name__)
CELLS_JSON = Path("reports/explorer/cells.json")
PAGE = Path("docs/explorer/index.html")
WINDOW_LABELS = {1: "1 s", 60: "1 min", 600: "10 min", 0: "whole session"}


def aggregate(runs_csv: Path = RUNS_CSV, label_prefix: str = "sweep-") -> list[dict]:
    """One row per cell x cache state: p50, p95, spread, run count, and the winner."""
    rows = duckdb.execute(
        """
        WITH runs AS (
            SELECT * FROM read_csv($path, header = true)
            WHERE starts_with(label, $prefix)
              AND NOT ends_with(label, '-check')
              AND latency_ms IS NOT NULL
        ),
        cells AS (
            SELECT query, technique, mem_limit_mb, window_s, cache_state,
                   count(*)                                AS runs,
                   quantile_cont(latency_ms, 0.50)         AS p50_ms,
                   quantile_cont(latency_ms, 0.95)         AS p95_ms,
                   quantile_cont(latency_ms, 0.75)
                     - quantile_cont(latency_ms, 0.25)     AS iqr_ms,
                   any_value(rows_returned)                AS rows_returned
            FROM runs
            GROUP BY ALL
        )
        SELECT *,
               p95_ms = min(p95_ms) OVER (PARTITION BY query, mem_limit_mb, window_s, cache_state)
                 AS is_winner
        FROM cells
        ORDER BY query, cache_state, mem_limit_mb, window_s, technique
        """,
        {"path": str(runs_csv), "prefix": label_prefix},
    ).fetchall()
    columns = [
        "query",
        "technique",
        "mem_limit_mb",
        "window_s",
        "cache_state",
        "runs",
        "p50_ms",
        "p95_ms",
        "iqr_ms",
        "rows_returned",
        "is_winner",
    ]
    cells = [dict(zip(columns, r, strict=True)) for r in rows]
    for c in cells:
        c["window"] = WINDOW_LABELS.get(int(c["window_s"]), f"{c['window_s']} s")
    return cells


def build_page(cells: list[dict], path: Path = PAGE) -> None:
    import altair as alt  # only this function needs it

    memory_levels = sorted({c["mem_limit_mb"] for c in cells})
    memory = alt.param(
        name="memory",
        value=512 if 512 in memory_levels else memory_levels[0],
        bind=alt.binding_radio(options=memory_levels, name="Postgres memory limit (MB): "),
    )
    cache = alt.param(
        name="cache",
        value="cold",
        bind=alt.binding_radio(options=["cold", "warm"], name="Cache: "),
    )
    window_order = [WINDOW_LABELS[k] for k in (1, 60, 600, 0)]

    crossover = (
        alt.Chart(alt.Data(values=cells))
        .mark_line(point=True)
        .encode(
            x=alt.X(
                "window:O", sort=window_order, title="Q1 time window", axis=alt.Axis(labelAngle=0)
            ),
            y=alt.Y("p95_ms:Q", scale=alt.Scale(type="log"), title="p95 latency (ms, log scale)"),
            color=alt.Color("technique:N", title="Technique"),
            tooltip=[
                "technique:N",
                "window:O",
                "mem_limit_mb:Q",
                "cache_state:N",
                alt.Tooltip("p50_ms:Q", format=",.1f"),
                alt.Tooltip("p95_ms:Q", format=",.1f"),
                alt.Tooltip("iqr_ms:Q", format=",.1f", title="spread (IQR, ms)"),
                "runs:Q",
                "rows_returned:Q",
            ],
        )
        .add_params(memory, cache)
        .transform_filter((alt.datum.mem_limit_mb == memory) & (alt.datum.cache_state == cache))
        .properties(
            width=640,
            height=360,
            title="Which technique wins depends on how much of the table the query reads",
        )
    )
    # TODO (v1): add the other views from the plan: hit ratio vs memory, planner
    # choices per cell, predicted vs measured sizes, and the noise view.

    path.parent.mkdir(parents=True, exist_ok=True)
    crossover.save(str(path))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    cells = aggregate()
    if not cells:
        raise SystemExit("no sweep runs in runs.csv yet: run `python -m nclt_lab.sweep` first")
    CELLS_JSON.parent.mkdir(parents=True, exist_ok=True)
    CELLS_JSON.write_text(json.dumps(cells, indent=2, default=float) + "\n")
    build_page(cells)
    log.info("%d cells -> %s and %s", len(cells), CELLS_JSON, PAGE)


if __name__ == "__main__":
    main()
