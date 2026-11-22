-- Q3: per-session odometry summary (full aggregate).
-- Lesson: it must read every row, so an index can't skip anything. The fix is a
-- summary table (q3_summary.sql), not an index.
-- TODO: confirm odometry x/y are metres in nclt.pdf before trusting distance_m.
SELECT session_id,
       sum(sqrt((x - prev_x) ^ 2 + (y - prev_y) ^ 2))                    AS distance_m,
       (max(utime) - min(utime)) / 1e6                                   AS duration_s,
       max(sqrt((x - prev_x) ^ 2 + (y - prev_y) ^ 2)
           / nullif((utime - prev_utime) / 1e6, 0))                      AS max_speed_m_s
FROM (
    SELECT session_id, utime, x, y,
           lag(x)     OVER w AS prev_x,
           lag(y)     OVER w AS prev_y,
           lag(utime) OVER w AS prev_utime
    FROM odometry
    WINDOW w AS (PARTITION BY session_id ORDER BY utime)
) AS steps
GROUP BY session_id
ORDER BY session_id
