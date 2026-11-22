-- Q1/Q5: composite B-tree on the time key. Also serves Q5's LATERAL rewrite.
CREATE INDEX IF NOT EXISTS bench_imu_btree ON imu (session_id, utime);
