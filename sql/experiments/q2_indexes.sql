-- Q2: join-key indexes on both tables, plus one on the filter column.
CREATE INDEX IF NOT EXISTS bench_gps_rtk_btree     ON gps_rtk (session_id, utime);
CREATE INDEX IF NOT EXISTS bench_gps_rtk_err_btree ON gps_rtk_err (session_id, utime);
CREATE INDEX IF NOT EXISTS bench_gps_rtk_err_error ON gps_rtk_err (error_m);
