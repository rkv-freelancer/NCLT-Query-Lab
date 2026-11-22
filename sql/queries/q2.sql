-- Q2: RTK fixes whose error is above a threshold (join on time + filter).
-- gps_rtk and gps_rtk_err share timestamps exactly, so this is an equality join.
-- Params: min_error_m (chosen from the data by `benchmarks params`)
SELECT r.session_id, r.utime, r.lat, r.lng, e.error_m
FROM gps_rtk AS r
JOIN gps_rtk_err AS e USING (session_id, utime)
WHERE e.error_m > %(min_error_m)s
