-- Step 8: PostGIS geometry for GPS fixes, and the grid statistics view.
-- Apply it in step 7.1 (schema freeze), BEFORE the baseline, so every benchmark
-- runs against the same tables:   python -m nclt_lab.spatial apply
-- Not part of schema.sql on purpose: CI's plain postgres:18 has no PostGIS.

CREATE EXTENSION IF NOT EXISTS postgis;

-- Each fix as a point in UTM zone 17N (EPSG:32617), so distances are metres.
-- lat/lng are stored in radians, so convert to degrees first; ST_MakePoint takes
-- (x = longitude, y = latitude). A NULL lat/lng (from 'nan') gives a NULL geom.
-- STORED, not VIRTUAL: Postgres can't index virtual generated columns (step 8's GiST).
ALTER TABLE gps ADD COLUMN IF NOT EXISTS geom geometry(Point, 32617)
    GENERATED ALWAYS AS (
        CASE WHEN mode >= 2 AND lat <> 0 AND lng <> 0   -- no fix or (0,0) "Null Island" -> NULL geom
             THEN ST_Transform(ST_SetSRID(ST_MakePoint(degrees(lng), degrees(lat)), 4326), 32617)
        END
    ) STORED;

ALTER TABLE gps_rtk ADD COLUMN IF NOT EXISTS geom geometry(Point, 32617)
    GENERATED ALWAYS AS (
        CASE WHEN mode >= 2 AND lat <> 0 AND lng <> 0   -- no fix or (0,0) "Null Island" -> NULL geom
             THEN ST_Transform(ST_SetSRID(ST_MakePoint(degrees(lng), degrees(lat)), 4326), 32617)
        END
    ) STORED;

-- Q7's precomputed answer: RTK error per 25 m cell and season.
-- Seasons by month: Dec-Feb winter, Mar-May spring, Jun-Aug summer, Sep-Nov fall.
-- Refresh after every ingest:   python -m nclt_lab.spatial refresh
CREATE MATERIALIZED VIEW IF NOT EXISTS gps_grid_stats AS
WITH fixes AS (
    SELECT floor(ST_X(r.geom) / 25)::int AS cell_x,
           floor(ST_Y(r.geom) / 25)::int AS cell_y,
           CASE WHEN extract(month FROM s.session_date) IN (12, 1, 2) THEN 'winter'
                WHEN extract(month FROM s.session_date) IN (3, 4, 5)  THEN 'spring'
                WHEN extract(month FROM s.session_date) IN (6, 7, 8)  THEN 'summer'
                ELSE 'fall' END            AS season,
           e.error_m
    FROM gps_rtk AS r
    JOIN gps_rtk_err AS e USING (session_id, utime)
    JOIN dim_session AS s USING (session_id)
    WHERE r.geom IS NOT NULL
      AND e.error_m IS NOT NULL
      -- TODO: also exclude fixes whose `mode` means "no fix" (check nclt.pdf)
)
SELECT cell_x, cell_y, season,
       count(*)                                              AS fixes,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY error_m) AS median_error_m,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY error_m) AS p95_error_m
FROM fixes
GROUP BY cell_x, cell_y, season
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS gps_grid_stats_key ON gps_grid_stats (cell_x, cell_y, season);
