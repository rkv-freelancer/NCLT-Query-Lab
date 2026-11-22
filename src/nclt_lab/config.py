"""Project-wide settings.

The database connection is not configured here: psycopg reads PGHOST, PGUSER,
PGPASSWORD, PGDATABASE and PGPORT from the environment (set in docker-compose.yml).
"""

from pathlib import Path

DATA_DIR = Path("data")
BUFFER_DIR = DATA_DIR / "buffer"  # working area: at most BUFFER_CAPACITY sessions on disk
BUFFER_CAPACITY = 2
PROFILE_CSV = Path("reports/profile.csv")

BASE_URL = "https://s3.us-east-2.amazonaws.com/nclt.perl.engin.umich.edu/sensor_data"

# All 27 NCLT sessions in date order.
# session_id = position in this tuple + 1, so an ID never changes once assigned.
ALL_SESSIONS = (
    "2012-01-08",
    "2012-01-15",
    "2012-01-22",
    "2012-02-02",
    "2012-02-04",
    "2012-02-05",
    "2012-02-12",
    "2012-02-18",
    "2012-02-19",
    "2012-03-17",
    "2012-03-25",
    "2012-03-31",
    "2012-04-29",
    "2012-05-11",
    "2012-05-26",
    "2012-06-15",
    "2012-08-04",
    "2012-08-20",
    "2012-09-28",
    "2012-10-28",
    "2012-11-04",
    "2012-11-16",
    "2012-11-17",
    "2012-12-01",
    "2013-01-10",
    "2013-02-23",
    "2013-04-05",
)


def session_id(session: str) -> int:
    """Stable small integer ID for a session date, e.g. '2012-01-08' -> 1."""
    return ALL_SESSIONS.index(session) + 1
