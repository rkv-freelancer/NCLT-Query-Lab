-- Q1: utime alone. Sessions never overlap in time (utime is an absolute
-- timestamp), so a utime range already lands inside one session. Is the
-- session_id column worth its space? Also compare q1_utime_only with q1_btree's
-- Postgres 18 skip scan on (session_id, utime).
--   make bench LABEL=q1_utime SETUP=q1_utime QUERIES=q1,q1_utime_only
CREATE INDEX IF NOT EXISTS bench_imu_utime ON imu (utime);
