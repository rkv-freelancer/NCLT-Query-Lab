-- Q5 rewrite: for each session, walk the (session_id, utime) index backwards
-- 100 steps. Expect one "Index Scan Backward" per session in the plan.
-- Requires experiments/q1_btree.sql (the imu B-tree).
SELECT i.*
FROM dim_session AS s
CROSS JOIN LATERAL (
    SELECT *
    FROM imu
    WHERE imu.session_id = s.session_id
    ORDER BY utime DESC
    LIMIT 100
) AS i
