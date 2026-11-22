-- Q2 rewrite: filter gps_rtk_err first, then join.
-- The planner usually pushes the filter down already, so this may change
-- nothing. If so, that's a finding for the README's "What failed" section.
-- Params: min_error_m
WITH bad AS (
    SELECT session_id, utime, error_m
    FROM gps_rtk_err
    WHERE error_m > %(min_error_m)s
)
SELECT r.session_id, r.utime, r.lat, r.lng, bad.error_m
FROM bad
JOIN gps_rtk AS r USING (session_id, utime)
