from nclt_lab.profile import profile


def test_profile_handles_ten_plus_columns(tmp_path):
    # 10 columns, like ms25.csv: DuckDB would auto-name these column00..column09
    rows = [f"{t}," + ",".join(["1.0"] * 9) + "\n" for t in [0, 10_000, 20_000]]
    csv = tmp_path / "wide.csv"
    csv.write_text("".join(rows))
    assert profile(csv)["rows"] == 3


def test_profile_counts_one_gap(tmp_path):
    times = [0, 10_000, 20_000, 30_000, 130_000, 140_000]  # 10 ms steps, one 100 ms gap

    csv = tmp_path / "s.csv"
    csv.write_text("".join(f"{t},1.0\n" for t in times))

    r = profile(csv)

    assert r["rows"] == 6
    assert r["gaps"] == 1
    assert r["median_dt_ms"] == 10.0
