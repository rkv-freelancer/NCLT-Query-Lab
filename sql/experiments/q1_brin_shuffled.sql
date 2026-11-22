-- Breaking BRIN on purpose: the same real rows, in random physical order.
-- Needs ~0.7 GB extra disk for all 27 sessions. Drop it afterwards:
--   DROP TABLE imu_shuffled;
CREATE TABLE IF NOT EXISTS imu_shuffled AS SELECT * FROM imu ORDER BY random();
ANALYZE imu_shuffled;
CREATE INDEX IF NOT EXISTS bench_imu_shuffled_brin ON imu_shuffled USING brin (session_id, utime);
