-- Q4: GPS dropouts longer than a threshold (window function over ordered utime).
-- Lesson: LAG needs rows in time order, so without an index Postgres sorts the
-- whole table (watch for "Sort Method: external merge  Disk" in the plan).
-- Params: threshold_us
SELECT session_id,
       prev_utime                 AS gap_start_utime,
       utime                      AS gap_end_utime,
       (utime - prev_utime) / 1e6 AS gap_s
FROM (
    SELECT session_id, utime,
           lag(utime) OVER (PARTITION BY session_id ORDER BY utime) AS prev_utime
    FROM gps
) AS t
WHERE utime - prev_utime > %(threshold_us)s
