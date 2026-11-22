-- Q7 (step 8): median and p95 RTK error per 25 m cell and season, computed directly.
-- Compare with q7_view.sql (the materialized view). Same lesson as Q3.
WITH fixes AS (
    SELECT floor(ST_X(r.geom) / 25)::int AS cell_x,
           floor(ST_Y(r.geom) / 25)::int AS cell_y,
           CASE WHEN extract(month FROM s.session_date) IN (12, 1, 2) THEN 'winter'
                WHEN extract(month FROM s.session_date) IN (3, 4, 5)  THEN 'spring'
                WHEN extract(month FROM s.session_date) IN (6, 7, 8)  THEN 'summer'
                ELSE 'fall' END            AS season,
           e.error_m
    FROM gps_rtk AS r
    JOIN gps_rtk_err AS e USING (session_id, utime)
    JOIN dim_session AS s USING (session_id)
    WHERE r.geom IS NOT NULL AND e.error_m IS NOT NULL
)
SELECT cell_x, cell_y, season,
       count(*)                                              AS fixes,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY error_m) AS median_error_m,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY error_m) AS p95_error_m
FROM fixes
GROUP BY cell_x, cell_y, season
