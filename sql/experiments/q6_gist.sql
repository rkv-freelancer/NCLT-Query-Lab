-- Q6 (step 8): spatial index for radius searches. Requires sql/spatial.sql.
CREATE INDEX IF NOT EXISTS bench_gps_geom_gist ON gps USING gist (geom);
