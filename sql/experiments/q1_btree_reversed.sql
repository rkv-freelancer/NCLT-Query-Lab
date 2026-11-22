-- Q1: the same two columns in the other order, (utime, session_id).
-- Lesson: equality column first, range column last. With utime first, the index
-- can't jump straight to one session; compare pages read with q1_btree in plans.csv.
--   make bench LABEL=q1_btree_reversed SETUP=q1_btree_reversed QUERIES=q1
CREATE INDEX IF NOT EXISTS bench_imu_utime_sid ON imu (utime, session_id);
