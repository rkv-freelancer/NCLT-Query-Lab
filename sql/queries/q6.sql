-- Q6 (step 8): every GPS fix within radius_m of a point, across all sessions.
-- Without an index this checks every fix; experiments/q6_gist.sql adds a GiST index.
-- Params: lng_deg, lat_deg (WGS 84 degrees), radius_m
SELECT session_id, utime, lat, lng
FROM gps
WHERE ST_DWithin(
    geom,
    ST_Transform(ST_SetSRID(ST_MakePoint(%(lng_deg)s, %(lat_deg)s), 4326), 32617),
    %(radius_m)s
)
