-- Q1 variant for Postgres 18 skip scan: filter on utime only.
-- With a B-tree on (session_id, utime), Postgres 18 can "skip" through the 27
-- session_id values instead of scanning the whole index. Look for an
-- "Index Searches" count in the plan.
-- Params: t1, t2
SELECT *
FROM imu
WHERE utime BETWEEN %(t1)s AND %(t2)s
