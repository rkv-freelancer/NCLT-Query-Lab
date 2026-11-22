"""Step 7 tests: deterministic parameters, plan parsing, and one small harness run."""

import csv
import json

import pytest
from conftest import write_session

from nclt_lab import benchmarks, db
from nclt_lab.loader import SessionLoader

SPANS = [(1, 1_000_000_000, 1_600_000_000), (2, 2_000_000_000, 2_600_000_000)]


def test_params_are_deterministic_and_inside_sessions():
    a = benchmarks.make_params(SPANS, min_error_m=1.5, n=8, seed=3)
    b = benchmarks.make_params(SPANS, min_error_m=1.5, n=8, seed=3)
    assert a == b
    for p in a["q1"]:
        sid, lo, hi = next(s for s in SPANS if s[0] == p["session_id"])
        assert lo <= p["t1"] < p["t2"] <= hi
        assert p["t2"] - p["t1"] == 60_000_000


def test_whole_session_window():
    p = benchmarks.make_params(SPANS, 1.0, n=3, window_s=0)["q1"][0]
    assert (p["t1"], p["t2"]) in {(lo, hi) for _, lo, hi in SPANS}


def test_plan_summary_reads_buffers_and_node_types():
    plan = [
        {
            "Plan": {
                "Node Type": "Hash Join",
                "Actual Rows": 5,
                "Shared Hit Blocks": 10,
                "Shared Read Blocks": 4,
                "Shared I/O Read Time": 1.5,
                "Temp Written Blocks": 0,
                "Plans": [
                    {"Node Type": "Seq Scan"},
                    {"Node Type": "Hash", "Plans": [{"Node Type": "Seq Scan"}]},
                ],
            },
            "Planning Time": 0.2,
            "Execution Time": 3.4,
        }
    ]
    s = benchmarks.plan_summary(plan)
    assert s["root_node"] == "Hash Join"
    assert s["node_types"] == "Hash Join > Seq Scan > Hash"
    assert (s["shared_hit"], s["shared_read"], s["io_read_ms"]) == (10, 4, 1.5)
    assert s["execution_ms"] == 3.4


@pytest.mark.integration
def test_small_run_writes_runs_and_plans(fresh_db, tmp_path, monkeypatch):
    with db.connect() as conn:
        SessionLoader(conn).load("2012-01-08", write_session(tmp_path / "s", rows=200))

    for name in ("PARAMS_JSON", "RUNS_CSV", "SETUPS_CSV", "PLANS_CSV"):
        monkeypatch.setattr(benchmarks, name, tmp_path / getattr(benchmarks, name).name)
    monkeypatch.setattr(benchmarks, "PLANS_DIR", tmp_path / "plans")

    benchmarks.write_params(n=3, seed=1, window_s=0.5)
    benchmarks.run("t_btree", ["q1", "q5_lateral"], ["q1_btree"], keep=False, warm=2, cold=0)

    with benchmarks.RUNS_CSV.open() as f:
        runs = list(csv.DictReader(f))
    assert len(runs) == 4 and {r["query"] for r in runs} == {"q1", "q5_lateral"}
    assert all(float(r["latency_ms"]) > 0 for r in runs)

    # On 200 rows the planner may still prefer a sequential scan, so only check
    # that a real plan was captured, not which plan it is.
    plan = json.loads((tmp_path / "plans" / "t_btree" / "q1_warm.json").read_text())
    assert benchmarks.plan_summary(plan)["execution_ms"] > 0

    with db.connect() as conn:  # the next run drops the previous run's bench_* indexes
        benchmarks.apply_setups(conn, "t_none", [], keep=False)
        assert all(
            not name.startswith("bench_") for _, name, _ in benchmarks.sensor_table_indexes(conn)
        )
