-- Q3: precompute the per-session summary once (27 rows) instead of reading
-- ~13.5M odometry rows on every query.
-- This is a FULL rebuild. TODO (better design): maintain it per session inside
-- the loader's transaction (SessionLoader.load), so it can never go stale.
DROP TABLE IF EXISTS session_odometry_summary;
CREATE TABLE session_odometry_summary AS
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
GROUP BY session_id;
ALTER TABLE session_odometry_summary ADD PRIMARY KEY (session_id);
