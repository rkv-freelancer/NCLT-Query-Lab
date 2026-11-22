"""
[OVERVIEW]
One description per sensor. Every pipeline step reads this list, so adding a
sensor later is a one-line change.

Column names follow NCLT's sample scripts (read_ms25.py, read_gps.py, ...).
Verify each one against nclt.pdf before the first real load.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SensorSpec:
    file: str  # file name inside the session archive
    table: str  # final Postgres table
    columns: tuple[str, ...]  # CSV columns, in file order, utime first
    expected_hz: float  # measured in profiling (5 sessions)
    gap_threshold_s: float  # an interval longer than this is a dropout (step 6)

    @property
    def staging_table(self) -> str:
        return f"staging_{self.table}"


SENSORS: tuple[SensorSpec, ...] = (
    SensorSpec(
        file="ms25.csv",
        table="imu",
        columns=(
            "utime",
            "mag_x",
            "mag_y",
            "mag_z",
            "accel_x",
            "accel_y",
            "accel_z",
            "rot_r",
            "rot_p",
            "rot_h",
        ),
        expected_hz=47.3,
        gap_threshold_s=0.15,  # ~5x the 21 ms period
    ),
    SensorSpec(
        file="odometry_mu_100hz.csv",
        table="odometry",
        columns=("utime", "x", "y", "z", "roll", "pitch", "heading"),
        expected_hz=107.7,
        gap_threshold_s=0.05,  # ~5x the 9.3 ms period
    ),
    SensorSpec(
        file="gps.csv",
        table="gps",
        columns=("utime", "mode", "num_sats", "lat", "lng", "alt", "track", "speed"),
        expected_hz=8.1,
        gap_threshold_s=2.0,  # matches Q4's definition
    ),
    SensorSpec(
        file="gps_rtk.csv",
        table="gps_rtk",
        columns=("utime", "mode", "num_sats", "lat", "lng", "alt", "track", "speed"),
        expected_hz=2.5,
        # RTK is bursty: revisit after looking at its intervals
        gap_threshold_s=5.0,
    ),
    SensorSpec(
        file="gps_rtk_err.csv",
        table="gps_rtk_err",
        columns=("utime", "error_m"),
        expected_hz=2.5,
        gap_threshold_s=5.0,
    ),
)

SENSOR_FILES: tuple[str, ...] = tuple(s.file for s in SENSORS)
