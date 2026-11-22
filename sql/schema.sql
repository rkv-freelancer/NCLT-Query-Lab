-- NCLT Query Lab schema. Every statement is idempotent (IF NOT EXISTS),
-- so `make schema` and the loader can run it any number of times.

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
-- CREATE EXTENSION IF NOT EXISTS postgis;   -- Phase 6, once db.Dockerfile installs it

-- One row per successfully loaded session. Written in the same transaction as
-- the session's sensor rows, so a row here means the whole session committed.
CREATE TABLE IF NOT EXISTS dim_session (
    session_id   smallint PRIMARY KEY,
    session_date date     NOT NULL UNIQUE,
    start_utime  bigint   NOT NULL,
    end_utime    bigint   NOT NULL,
    duration_s   double precision GENERATED ALWAYS AS ((end_utime - start_utime) / 1e6) STORED,
    rows_loaded  bigint   NOT NULL,
    status       text     NOT NULL DEFAULT 'loaded',
    loaded_at    timestamptz NOT NULL
);

-- Final sensor tables.
-- Deliberately NO primary keys, indexes or foreign keys: the Phase 3 baseline
-- measures queries without them, and Phase 4 adds them one at a time.
-- Values are stored exactly as delivered (lat/lng in radians); 'nan' becomes NULL.

CREATE TABLE IF NOT EXISTS imu (
    session_id smallint NOT NULL,
    utime      bigint   NOT NULL,
    mag_x float8, mag_y float8, mag_z float8,        -- Gauss
    accel_x float8, accel_y float8, accel_z float8,  -- m/s^2
    rot_r float8, rot_p float8, rot_h float8         -- rad/s
);

CREATE TABLE IF NOT EXISTS odometry (
    session_id smallint NOT NULL,
    utime      bigint   NOT NULL,
    x float8, y float8, z float8,                    -- verify units in nclt.pdf
    roll float8, pitch float8, heading float8
);

CREATE TABLE IF NOT EXISTS gps (
    session_id smallint NOT NULL,
    utime      bigint   NOT NULL,
    mode smallint, num_sats smallint,
    lat float8, lng float8, alt float8,              -- lat/lng in radians
    track float8, speed float8
);

CREATE TABLE IF NOT EXISTS gps_rtk (
    session_id smallint NOT NULL,
    utime      bigint   NOT NULL,
    mode smallint, num_sats smallint,
    lat float8, lng float8, alt float8,
    track float8, speed float8
);

CREATE TABLE IF NOT EXISTS gps_rtk_err (
    session_id smallint NOT NULL,
    utime      bigint   NOT NULL,
    error_m    float8                                -- verify units in nclt.pdf
);

-- Staging tables: same columns as the CSV files (no session_id; the swap adds it).
-- UNLOGGED skips the write-ahead log, so COPY into them is faster. The trade-off:
-- Postgres empties unlogged tables after a crash, which is fine for scratch space.

CREATE UNLOGGED TABLE IF NOT EXISTS staging_imu (
    utime bigint NOT NULL,
    mag_x float8, mag_y float8, mag_z float8,
    accel_x float8, accel_y float8, accel_z float8,
    rot_r float8, rot_p float8, rot_h float8
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_odometry (
    utime bigint NOT NULL,
    x float8, y float8, z float8,
    roll float8, pitch float8, heading float8
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_gps (
    utime bigint NOT NULL,
    mode smallint, num_sats smallint,
    lat float8, lng float8, alt float8,
    track float8, speed float8
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_gps_rtk (
    utime bigint NOT NULL,
    mode smallint, num_sats smallint,
    lat float8, lng float8, alt float8,
    track float8, speed float8
);

CREATE UNLOGGED TABLE IF NOT EXISTS staging_gps_rtk_err (
    utime   bigint NOT NULL,
    error_m float8
);


CREATE TABLE IF NOT EXISTS sensor_gaps (
    sensor          text     NOT NULL,     -- SensorSpec.table, e.g. 'gps'
    session_id      smallint NOT NULL,
    gap_start_utime bigint   NOT NULL,     -- last reading before the gap
    gap_end_utime   bigint   NOT NULL,     -- first reading after it
    gap_s double precision GENERATED ALWAYS AS ((gap_end_utime - gap_start_utime) / 1e6) STORED,
    threshold_s     double precision NOT NULL,
    PRIMARY KEY (sensor, session_id, gap_start_utime)
);


ALTER TABLE staging_imu         ADD COLUMN IF NOT EXISTS line_no bigint GENERATED ALWAYS AS IDENTITY;
ALTER TABLE staging_odometry    ADD COLUMN IF NOT EXISTS line_no bigint GENERATED ALWAYS AS IDENTITY;
ALTER TABLE staging_gps         ADD COLUMN IF NOT EXISTS line_no bigint GENERATED ALWAYS AS IDENTITY;
ALTER TABLE staging_gps_rtk     ADD COLUMN IF NOT EXISTS line_no bigint GENERATED ALWAYS AS IDENTITY;
ALTER TABLE staging_gps_rtk_err ADD COLUMN IF NOT EXISTS line_no bigint GENERATED ALWAYS AS IDENTITY;