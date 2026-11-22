# Experiments

Each file sets up ONE change for a labeled benchmark run:

    python -m nclt_lab.benchmarks run --label q1_btree --setup q1_btree --queries q1

Rules the harness enforces:
- Every experiment index is named `bench_*`. Before each run, the harness drops all
  `bench_*` indexes (unless you pass `--keep`), so experiments never leak into each other.
- Build time and index sizes are recorded in `reports/bench/setups.csv`.

`final.sql` is yours to write: the combination you choose after the one-at-a-time runs.
