-- Q4: a B-tree that supplies (session_id, utime) order, so the window function
-- can read rows already sorted. Expect the Sort node to disappear.
CREATE INDEX IF NOT EXISTS bench_gps_btree ON gps (session_id, utime);
