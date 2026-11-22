"""Step 8 tests: PostGIS geometry is correct, and the checks catch a swapped lat/lng.

Skipped where PostGIS isn't installed (for example CI's plain postgres:18 image).
"""

import pytest
from conftest import write_session

from nclt_lab import db, spatial
from nclt_lab.loader import SessionLoader

pytestmark = pytest.mark.integration

# A real North Campus fix, in radians as NCLT stores it.
LAT_RAD, LNG_RAD = 0.7381566, -1.4610097


@pytest.fixture
def campus_db(fresh_db, tmp_path):
    with db.connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'"
        ).fetchone():
            pytest.skip("PostGIS not installed on this server")
        SessionLoader(conn).load("2012-01-08", write_session(tmp_path / "s", rows=200))
        # Walk ~0.15 m per fix from a real campus position.
        for table in ("gps", "gps_rtk"):
            conn.execute(
                f"UPDATE {table} SET lat = %s + (utime - 1326030975000000) * 1e-14, "
                f"lng = %s + (utime - 1326030975000000) * 1e-14",
                (LAT_RAD, LNG_RAD),
            )
        spatial.apply(conn)
        yield conn


def test_campus_fixes_pass_every_check(campus_db):
    assert spatial.check(campus_db, "gps") == []
    assert spatial.check(campus_db, "gps_rtk") == []


def test_swapped_lat_lng_is_caught(campus_db):
    campus_db.execute("UPDATE gps SET lat = lng, lng = lat")  # the classic bug
    failures = spatial.check(campus_db, "gps")
    assert any("not North Campus" in f for f in failures)


def test_q6_points_are_real_fixes(campus_db):
    points = spatial.point_params(campus_db, n=3, seed=1)
    assert len(points) == 3
    assert all(42.25 < p["lat_deg"] < 42.33 and p["radius_m"] == 50.0 for p in points)
