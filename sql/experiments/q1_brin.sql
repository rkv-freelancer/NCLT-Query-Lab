-- Q1: BRIN on the same columns. Tiny, because it stores only min/max per block
-- range (128 pages by default). Works only while rows are physically time-ordered.
-- Check first: SELECT attname, correlation FROM pg_stats WHERE tablename = 'imu';
CREATE INDEX IF NOT EXISTS bench_imu_brin ON imu USING brin (session_id, utime);
-- TODO (optional): try WITH (pages_per_range = 32) as a separate experiment.
