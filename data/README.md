# Data notes: NCLT sensor data

What the raw data is, how it was loaded, and every data-quality decision the
pipeline makes. Each rule cites the evidence behind it. Facts marked
**(5 sessions)** were checked on 2012-01-08, 2012-05-11, 2012-08-04, 2012-11-17
and 2013-02-23 only; rerun those checks on all 27 sessions before quoting them.

## Provenance

- **Dataset:** The University of Michigan North Campus Long-Term Vision and Lidar
  Dataset (NCLT). Carlevaris-Bianco, Ushani and Eustice, *International Journal of
  Robotics Research*, 2016. <https://robots.engin.umich.edu/nclt>
- **License:** Open Database License (ODbL) 1.0 for the database, Database Contents
  License (DbCL) 1.0 for its contents. Credit the work and share adapted public
  versions under the same license.
- **Source files:** `<date>_sen.tar.gz` per session from
  `s3.us-east-2.amazonaws.com/nclt.perl.engin.umich.edu/sensor_data/`.
- **Scope:** all 27 sessions, sensor data only (no lidar, no images). No synthetic
  rows anywhere in the pipeline.

## Sessions

`session_id` is the position in `config.ALL_SESSIONS` (starting at 1), so an ID
never changes.

| id | date | id | date | id | date |
| --- | --- | --- | --- | --- | --- |
| 1 | 2012-01-08 | 10 | 2012-03-17 | 19 | 2012-09-28 |
| 2 | 2012-01-15 | 11 | 2012-03-25 | 20 | 2012-10-28 |
| 3 | 2012-01-22 | 12 | 2012-03-31 | 21 | 2012-11-04 |
| 4 | 2012-02-02 | 13 | 2012-04-29 | 22 | 2012-11-16 |
| 5 | 2012-02-04 | 14 | 2012-05-11 | 23 | 2012-11-17 |
| 6 | 2012-02-05 | 15 | 2012-05-26 | 24 | 2012-12-01 |
| 7 | 2012-02-12 | 16 | 2012-06-15 | 25 | 2013-01-10 |
| 8 | 2012-02-18 | 17 | 2012-08-04 | 26 | 2013-02-23 |
| 9 | 2012-02-19 | 18 | 2012-08-20 | 27 | 2013-04-05 |

## Files, tables and units

All files are headerless CSV with `utime` first. `utime` is microseconds since
the Unix epoch (UTC).

| File | Table | Columns after utime | Units |
| --- | --- | --- | --- |
| `ms25.csv` | `imu` | mag_x/y/z, accel_x/y/z, rot_r/p/h | Gauss, m/s^2, rad/s |
| `odometry_mu_100hz.csv` | `odometry` | x, y, z, roll, pitch, heading | x, y in metres (evidence below); angles to confirm in nclt.pdf |
| `gps.csv` | `gps` | mode, num_sats, lat, lng, alt, track, speed | lat/lng in radians, alt in metres |
| `gps_rtk.csv` | `gps_rtk` | same as gps | same as gps |
| `gps_rtk_err.csv` | `gps_rtk_err` | error_m | metres (to confirm in nclt.pdf) |

**Odometry x/y are metres (5 sessions).** Per-session odometry path length was
5.4-6.5 km against 6.0-8.1 km of RTK distance (haversine on mode-3 fixes), and the
x/y extents (about 400-930 m) match the RTK extents (about 410-1,000 m). In feet
they would be about 3.3x larger. The RTK path is longer because GPS jitter while
the robot stands still adds distance that odometry doesn't.

**`num_sats` is not a usable quality signal.** It is 0 in every `gps` row
(5 sessions), and often 0 in `gps_rtk` even for 3D fixes.

## Sampling rates and gap thresholds

A gap is an interval between consecutive readings, within one session, longer than
the sensor's `gap_threshold_s` in `sensors.py`. Thresholds were chosen from
`python -m nclt_lab.main gaps --percentiles` on all 27 sessions:

| Sensor | Median interval | p99 (range over sessions) | Longest interval | Threshold | Why |
| --- | --- | --- | --- | --- | --- |
| imu | 20.0 ms | 41-51 ms | 72-100 ms | 0.15 s | No IMU dropouts in 27 sessions. At 0.1 s the only "gap" was one 100.4 ms interval (session 20), 0.4 ms over the line. |
| odometry | 9.6-9.7 ms | 20-21 ms | 50-75 ms | 0.05 s | 2.5x the worst p99. Catches real stalls of 5-7 missed samples. |
| gps | 110-200 ms | 0.27-0.60 s | 0.9 s to 739 s | 2.0 s | Clean split: normal pauses stay under 0.6 s, real dropouts run from about 39 s to 12 min. |
| gps_rtk | 31-129 ms | 1.0-1.94 s | 2.0-4.07 s | 5.0 s | See below. |
| gps_rtk_err | same timestamps as gps_rtk | | | 5.0 s | Paired with gps_rtk, so the same threshold. |

**RTK never stops reporting.** Its longest interval in 27 sessions is 4.07 s, and
long intervals cluster near 2, 3 and 4 s (1-3 missed epochs of a once-per-second
rhythm). During the 634 s GPS dropout on 2012-08-04, RTK kept logging 919 readings:
474 in mode 2 (mean error 37.3 m, max 111.6 m) and 445 in mode 3 (mean 2.7 m).
So RTK outages show up as fix quality (`mode`, `error_m`), not as missing rows.
The 5 s threshold, above the 4.07 s maximum, gives no RTK timestamp gaps by design;
RTK quality is analyzed in the spatial phase instead.

**Spot checks.** The longest GPS dropout in 2012-08-04 (session 17) is 634.3 s.
The longest GPS dropout overall is 739 s in 2012-02-18 (session 8).

**Mid-year changes in the sensors' behavior.** IMU p99 is about 42 ms for sessions
1-14 and about 50 ms from session 15 (2012-05-26) on. The RTK median interval drops
from about 120 ms to 50-60 ms from session 19 (2012-09-28) on, and also in sessions
13 and 16. Likely a logging or configuration change; thresholds are set above both
regimes.

## Duplicate timestamps

**Rule: last row wins.** When several rows in one file share a `utime`, the loader
keeps the last one in file order and drops the earlier ones (`SessionLoader._swap`).
Values are never averaged: an average is a state the robot was never in. The number
of dropped rows is logged per session and table, and `dim_session.rows_loaded`
counts rows after the rule. **(5 sessions)** No duplicates were found, so the rule
exists for correctness, not because the data needed it.

## Missing values

- The CSVs write a missing value as `nan`. COPY loads it as SQL NULL
  (`NULL 'nan'`).
- **Exception (5 sessions):** `gps_rtk_err.csv` writes one missing value as
  `     NaN` (padded and capitalised). COPY doesn't match it, so Postgres stores a
  float NaN, not NULL. `IS NOT NULL` does not filter it, and `avg()` over it
  returns NaN. It was the single mode-0 reading. Count on all 27 sessions:
  `SELECT count(*) FROM gps_rtk_err WHERE error_m = 'NaN'::float8;`

## GPS fix mode and position validity

`mode` is the receiver's fix type (confirm the codes in nclt.pdf):

| mode | Meaning | Evidence (5 sessions) |
| --- | --- | --- |
| 0 | No fix | 2 gps rows, 1 gps_rtk row; no altitude |
| 2 | 2D fix: lat/lng, no altitude | `alt` is NULL in every mode-2 row (104,559 gps, 9,057 gps_rtk) |
| 3 | 3D fix | `alt` present in every mode-3 row (101,691 gps, 55,220 gps_rtk) |

**Rule: a position counts only if `mode >= 2` and it isn't (0, 0).** Otherwise the
generated `geom` column is NULL (`sql/spatial.sql`), so Q6-Q8, `gps_grid_stats` and
the spatial checks never see it. `make spatial-check` reports how many fixes have
no position.

**Why the (0, 0) check is needed in addition to mode.** One `gps_rtk` reading in
27 sessions (session 19, 2012-09-28) reports latitude 0, longitude 0 and altitude 0
while labeled mode 3. Its `error_m` is 6,356,586.5 m, close to the Earth's polar
radius (about 6,357 km), which suggests the receiver emitted an empty position.
It is an isolated glitch: the readings 2 s before and 3 s after sit at the same
spot on campus (42.29395 N, 83.71580 W) with errors of 1.1-5.0 m. Without the
check, `ST_Transform` to UTM fails on it ("Point outside of projection domain") and
`make spatial` aborts. Lesson: a quality flag the device sets itself isn't
enough on its own.

**Out-of-area fixes (5 sessions).** 304 gps fixes fall outside the campus box, all
mode 2, all from 2013-02-23, in the last ~65 s of the session, at about 42.346 N
(roughly 6 km north). They keep a `geom` under the rule above; filter them in
analyses that need positions on campus.

**RTK error by fix mode (5 sessions).** Mode 3: mean 2.6 m, p95 6.9 m. Mode 2:
mean 29.4 m, p95 120.6 m. The error column reports metres-level accuracy, not the
centimetre level RTK can reach in ideal conditions.

## Open checks

- [ ] Confirm `mode` codes, odometry angle units and `error_m` units in nclt.pdf.
- [ ] Count `NaN` values in `gps_rtk_err` (and any other float column) on all 27 sessions.
- [ ] Rerun the out-of-area and duplicate checks on all 27 sessions.
