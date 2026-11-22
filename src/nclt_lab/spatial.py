"""Step 8: where does GPS fail on campus?

    python -m nclt_lab.spatial apply     # PostGIS, geom columns, grid view (step 7.1)
    python -m nclt_lab.spatial check     # correctness checks; exits 1 if any fails
    python -m nclt_lab.spatial refresh   # recompute gps_grid_stats after an ingest

Then benchmark the spatial queries with the same harness as step 7:

    python -m nclt_lab.benchmarks params          # adds q6 points once geom exists
    python -m nclt_lab.benchmarks run --label spatial_base --queries q6,q7,q7_view,q8
    python -m nclt_lab.benchmarks run --label q6_gist --setup q6_gist --queries q6

Run `check` BEFORE trusting any map. A swapped longitude/latitude order or a
missed radians-to-degrees conversion produces plausible-looking numbers that put
North Campus somewhere in the ocean; these checks catch that immediately.
"""

import argparse
import logging
import random
from pathlib import Path

import psycopg

from nclt_lab import db

log = logging.getLogger(__name__)
SPATIAL_SQL = Path("sql/spatial.sql")

# Rough bounds for the University of Michigan North Campus area.
LAT_DEG = (42.25, 42.33)
LNG_DEG = (-83.75, -83.68)
EASTING_M = (270_000, 285_000)  # UTM zone 17N
NORTHING_M = (4_680_000, 4_690_000)
MAX_MEDIAN_STEP_M = 10.0  # consecutive fixes ~0.1-0.4 s apart at walking speed


def apply(conn: psycopg.Connection) -> None:
    conn.execute(SPATIAL_SQL.read_text())
    refresh(conn)


def refresh(conn: psycopg.Connection) -> None:
    conn.execute("REFRESH MATERIALIZED VIEW gps_grid_stats")


def check(conn: psycopg.Connection, table: str = "gps") -> list[str]:
    """Return a list of failed checks (empty = all good)."""
    failures = []
    lat, lng, east, north, total, no_geom = conn.execute(
        f"""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY degrees(lat)),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY degrees(lng)),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY ST_X(geom)),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY ST_Y(geom)),
               count(*),
               count(*) FILTER (WHERE geom IS NULL)
        FROM {table}
        """
    ).fetchone()
    log.info(
        "%s median fix: %.5f N, %.5f E -> easting %.0f m, northing %.0f m",
        table,
        lat,
        lng,
        east,
        north,
    )
    log.info("%s: %s fixes, %s without a position (NULL geom)", table, f"{total:,}", f"{no_geom:,}")

    if not LAT_DEG[0] <= lat <= LAT_DEG[1] or not LNG_DEG[0] <= lng <= LNG_DEG[1]:
        failures.append(f"median position {lat:.4f}, {lng:.4f} is not North Campus")
    if not EASTING_M[0] <= east <= EASTING_M[1] or not NORTHING_M[0] <= north <= NORTHING_M[1]:
        failures.append(f"UTM {east:.0f}, {north:.0f} m is outside the expected zone 17N box")

    (median_step,) = conn.execute(
        f"""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY step_m)
        FROM (
            SELECT ST_Distance(
                       geom, lag(geom) OVER (PARTITION BY session_id ORDER BY utime)
                   ) AS step_m
            FROM {table}
            WHERE geom IS NOT NULL
        ) AS steps
        WHERE step_m IS NOT NULL
        """
    ).fetchone()
    log.info("%s median distance between consecutive fixes: %.2f m", table, median_step)
    if median_step > MAX_MEDIAN_STEP_M:
        failures.append(
            f"consecutive fixes are {median_step:.1f} m apart (expected metres, not km)"
        )

    # TODO: count fixes outside the campus box per `mode` value. If one mode
    # explains most of them, that mode probably means "no fix": exclude it in
    # spatial.sql's view and in Q6-Q8, and write the rule in data/README.md.
    return failures


def point_params(conn: psycopg.Connection, n: int, seed: int, radius_m: float = 50.0) -> list[dict]:
    """Q6 parameters: n real fix positions (degrees), chosen deterministically."""
    (count,) = conn.execute("SELECT count(*) FROM gps WHERE geom IS NOT NULL").fetchone()
    if not count:
        return []
    rng = random.Random(seed)
    points = []
    for _ in range(n):
        lng, lat = conn.execute(
            "SELECT degrees(lng), degrees(lat) FROM gps WHERE geom IS NOT NULL "
            "ORDER BY session_id, utime OFFSET %s LIMIT 1",
            (rng.randrange(count),),
        ).fetchone()
        points.append({"lng_deg": lng, "lat_deg": lat, "radius_m": radius_m})
    return points


def has_geom(conn: psycopg.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'gps' AND column_name = 'geom'"
        ).fetchone()
        is not None
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    parser = argparse.ArgumentParser(description="Step 8: spatial phase")
    parser.add_argument("command", choices=["apply", "check", "refresh"])
    args = parser.parse_args()

    with db.connect() as conn:
        if args.command == "apply":
            apply(conn)
            log.info("PostGIS columns and gps_grid_stats are in place")
        elif args.command == "refresh":
            refresh(conn)
            log.info("gps_grid_stats refreshed")
        else:
            failures = check(conn, "gps") + check(conn, "gps_rtk")
            for failure in failures:
                log.error("check failed: %s", failure)
            raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
