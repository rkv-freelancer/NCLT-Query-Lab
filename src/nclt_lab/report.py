"""README charts, regenerated from data (never edited by hand).

    python -m nclt_lab.report                              # every chart whose inputs exist
    python -m nclt_lab.report --before baseline --after final

Charts written to reports/:
- p2_before_after_p95.png    from reports/bench/runs.csv   (step 7: baseline vs final)
- p2_before_after_p95_spatial_base.png                     (step 8: spatial_base vs final)
- p2_q1_index_types.png      from runs.csv + setups.csv     (Q1 per index type)
- p2_sensor_gaps_heatmap.png from reports/gap_coverage.csv (step 6)
- p2_rtk_error_by_season.png TODO (step 8, from gps_grid_stats)
- p2_grid_error_by_season.png TODO (step 8)

Style: colors and ink from a validated reference palette (blue = before,
orange = after); recessive grid; one y-axis per chart; NCLT attribution under each.
"""

import argparse
import csv
import logging
from pathlib import Path

import duckdb
import matplotlib

matplotlib.use("Agg")  # headless: no display in the container
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap

from nclt_lab.benchmarks import RUNS_CSV, SETUPS_CSV
from nclt_lab.gaps import COVERAGE_CSV

log = logging.getLogger(__name__)
OUT = Path("reports")
ATTRIBUTION = "Data: NCLT (Carlevaris-Bianco et al., IJRR 2016), ODbL."

# "before" query -> the query that replaced it in the "after" run. A rewrite is a
# different file, so without this map it would never share a bar group with the
# query it replaced, and the chart would silently leave it out.
REWRITES = {"q3": "q3_summary", "q5_row_number": "q5_lateral", "q7": "q7_view"}

SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BEFORE, AFTER = "#2a78d6", "#eb6834"  # categorical slots 1 and 2
BLUE_RAMP = [
    "#cde2fb",
    "#9ec5f4",
    "#6da7ec",
    "#3987e5",
    "#256abf",
    "#184f95",
    "#0d366b",
]


def style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK_2)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def finish(fig, path: Path, caption: str) -> None:
    fig.text(0.01, 0.01, f"{caption}  {ATTRIBUTION}", fontsize=8, color=INK_2, ha="left")
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    log.info("wrote %s", path)


def before_after_p95(before: str, after: str, cache: str = "cold") -> Path | None:
    if not RUNS_CSV.exists():
        return None
    rows = duckdb.execute(
        """
        SELECT label, query, quantile_cont(latency_ms, 0.95) AS p95
        FROM read_csv($path, header = true)
        WHERE label IN ($before, $after) AND cache_state = $cache AND latency_ms IS NOT NULL
        GROUP BY ALL
        """,
        {"path": str(RUNS_CSV), "before": before, "after": after, "cache": cache},
    ).fetchall()
    p95 = {(label, q): v for label, q, v in rows}

    # (before query, after query) pairs; a rewrite pairs with the query it replaced.
    # A rewrite the "before" run also measured on its own (spatial_base ran q7 and
    # q7_view) is shown once, as the replacement of the original.
    measured_before = {q for (label, q) in p95 if label == before}
    replaced = {REWRITES[q] for q in measured_before if q in REWRITES}
    pairs = [
        (q, REWRITES.get(q, q))
        for q in sorted(measured_before - replaced)
        if (after, REWRITES.get(q, q)) in p95
    ]
    if not pairs:
        log.warning("no query measured in both %r and %r (%s cache)", before, after, cache)
        return None
    names = [b if a == b else f"{b} \u2192 {a}" for b, a in pairs]

    fig, ax = plt.subplots(figsize=(max(7.5, 1.1 * len(names) + 2), 4.4), facecolor=SURFACE)
    style(ax)
    x = range(len(names))
    width = 0.38

    for offset, label, color, side in (
        (-width / 2, before, BEFORE, 0),
        (width / 2, after, AFTER, 1),
    ):
        values = [p95[(label, pair[side])] for pair in pairs]
        bars = ax.bar(
            [i + offset for i in x],
            values,
            width=width - 0.02,
            color=color,
            label=label,
        )
        for bar, v in zip(bars, values, strict=True):
            ax.annotate(
                f"{v:,.0f}" if v >= 10 else f"{v:.1f}",
                (bar.get_x() + bar.get_width() / 2, v),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                color=INK,
            )

    ax.set_yscale("log")
    ax.set_xticks(list(x), names, color=INK, fontsize=8)
    ax.set_ylabel(f"p95 latency, {cache} cache (ms, log scale)", color=INK_2, fontsize=9)
    ax.set_title(
        f"p95 latency per query: {before} vs. {after}",
        color=INK,
        fontsize=11,
        loc="left",
    )
    ax.legend(frameon=False, fontsize=9, labelcolor=INK)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    # one file per comparison, so a second run doesn't overwrite the first
    name = (
        "p2_before_after_p95.png" if before == "baseline" else f"p2_before_after_p95_{before}.png"
    )
    path = OUT / name
    finish(
        fig,
        path,
        f"{cache.capitalize()} cache, 27 sessions, Postgres in a 512 MB container.",
    )
    return path


# (label in runs.csv, query, display name) for Q1's one-at-a-time index runs
Q1_RUNS = (
    ("baseline", "q1", "No index"),
    ("q1_btree", "q1", "B-tree (session_id, utime)"),
    ("q1_btree_reversed", "q1", "B-tree (utime, session_id)"),
    ("q1_utime", "q1", "B-tree (utime)"),
    ("q1_brin", "q1", "BRIN, rows in time order"),
    ("q1_brin_shuffled", "q1_shuffled", "BRIN, same rows shuffled"),
)


def q1_index_types(cache: str = "cold") -> Path | None:
    """Q1 latency per index type, with each index's size: the BRIN trade-off."""
    if not RUNS_CSV.exists():
        return None
    rows = duckdb.execute(
        """
        SELECT label, query,
               quantile_cont(latency_ms, 0.50) AS p50,
               quantile_cont(latency_ms, 0.95) AS p95
        FROM read_csv($path, header = true)
        WHERE cache_state = $cache AND latency_ms IS NOT NULL
        GROUP BY ALL
        """,
        {"path": str(RUNS_CSV), "cache": cache},
    ).fetchall()
    stats = {(label, q): (p50, p95) for label, q, p50, p95 in rows}
    runs = [r for r in Q1_RUNS if (r[0], r[1]) in stats]
    if len(runs) < 2:
        log.warning("fewer than two Q1 index runs in %s; skipping the Q1 chart", RUNS_CSV)
        return None

    sizes: dict[str, int] = {}  # label -> size of the bench_* index that run measured
    if SETUPS_CSV.exists():
        with SETUPS_CSV.open(newline="") as f:
            for row in csv.DictReader(f):
                if row["index_name"].startswith("bench_imu") and row["size_bytes"]:
                    sizes[row["label"]] = int(row["size_bytes"])

    fig, ax = plt.subplots(figsize=(7.5, 3.4), facecolor=SURFACE)
    style(ax)
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ys = list(range(len(runs)))[::-1]
    for y, (label, query, _name) in zip(ys, runs, strict=True):
        p50, p95 = stats[(label, query)]
        ax.plot([p50, p95], [y, y], color=GRID, linewidth=3, zorder=1)
        ax.scatter(
            [p50],
            [y],
            s=50,
            color=BEFORE,
            zorder=3,
            label="p50" if y == ys[0] else None,
        )
        ax.scatter(
            [p95],
            [y],
            s=50,
            facecolor=SURFACE,
            edgecolor=BEFORE,
            linewidth=2,
            zorder=3,
            label="p95" if y == ys[0] else None,
        )
        size = sizes.get(label)
        size_text = (
            ""
            if size is None
            else (
                f"   index {size / 2**10:,.0f} KiB"
                if size < 2**20
                else f"   index {size / 2**20:,.0f} MiB"
            )
        )
        ax.annotate(
            f"{p95:,.1f} ms{size_text}",
            (p95, y),
            xytext=(8, -3),
            textcoords="offset points",
            fontsize=8,
            color=INK_2,
        )
    ax.set_xscale("log")
    lo = min(stats[(r[0], r[1])][0] for r in runs)
    hi = max(stats[(r[0], r[1])][1] for r in runs)
    ax.set_xlim(lo / 2, hi * 20)
    ax.set_yticks(ys, [r[2] for r in runs], color=INK, fontsize=9)
    ax.set_xlabel(f"Q1 latency, {cache} cache (ms, log scale)", color=INK_2, fontsize=9)
    ax.set_title(
        "Q1: one session, one time window, per index type",
        color=INK,
        fontsize=11,
        loc="left",
    )
    ax.legend(frameon=False, fontsize=8, labelcolor=INK, loc="upper right")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    path = OUT / "p2_q1_index_types.png"
    finish(fig, path, "imu, 27 sessions, Postgres in a 512 MB container.")
    return path


def gaps_heatmap() -> Path | None:
    if not COVERAGE_CSV.exists():
        return None
    with COVERAGE_CSV.open(newline="") as f:
        rows = list(csv.DictReader(f))
    sessions = sorted({r["session"] for r in rows})
    sensors = ["imu", "odometry", "gps", "gps_rtk", "gps_rtk_err"]
    pct = {(r["sensor"], r["session"]): float(r["pct_of_session"]) for r in rows}
    grid = [[pct.get((s, d), 0.0) for d in sessions] for s in sensors]  # no row = no gaps

    fig, ax = plt.subplots(figsize=(max(6, 0.32 * len(sessions) + 2), 3.2), facecolor=SURFACE)
    image = ax.imshow(grid, aspect="auto", cmap=ListedColormap(BLUE_RAMP), vmin=0)
    ax.set_yticks(range(len(sensors)), sensors, color=INK, fontsize=9)
    ax.set_xticks(range(len(sessions)), sessions, rotation=60, ha="right", color=INK_2, fontsize=7)

    for spine in ax.spines.values():
        spine.set_visible(False)
    bar = fig.colorbar(image, ax=ax, fraction=0.03, pad=0.02)
    bar.set_label("% of session lost to dropouts", color=INK_2, fontsize=8)
    bar.ax.tick_params(colors=INK_2, labelsize=8)
    ax.set_title("Sensor dropouts per session", color=INK, fontsize=11, loc="left")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    path = OUT / "p2_sensor_gaps_heatmap.png"
    finish(fig, path, "Gaps above each sensor's threshold (sensors.py).")

    return path


def rtk_error_by_season() -> Path | None:
    # TODO (step 8): read gps_grid_stats (or Q7's result) and plot p95 RTK error
    # per season. Decide the form first: four values -> a simple bar chart, or the
    # distribution per season -> box plots. Same style() and finish() as above.
    return None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    parser = argparse.ArgumentParser(description="Regenerate README charts")
    parser.add_argument("--before", default="baseline")
    parser.add_argument("--after", default="final")
    parser.add_argument("--cache", default="cold", choices=["cold", "warm"])
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    made = [
        p
        for p in (
            before_after_p95(args.before, args.after, args.cache),
            # spatial queries (Q6-Q8) have their own "before": the spatial_base run
            before_after_p95("spatial_base", args.after, args.cache),
            q1_index_types(args.cache),
            gaps_heatmap(),
            rtk_error_by_season(),
        )
        if p
    ]
    log.info("%d charts written", len(made))


if __name__ == "__main__":
    main()
