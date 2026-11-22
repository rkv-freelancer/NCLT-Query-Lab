-- Q8 (step 8): where was the robot when each GPS dropout began?
-- sensor_gaps stores the last reading's own timestamp (gap_start_utime), so this is
-- an EQUALITY join, not a "latest fix before t" search.
-- Duplicate timestamps would return extra rows here: that depends on your
-- duplicate policy (step 4).
SELECT g.session_id,
       g.gap_start_utime,
       g.gap_s,
       ST_X(p.geom) AS easting_m,
       ST_Y(p.geom) AS northing_m
FROM sensor_gaps AS g
JOIN gps AS p
  ON p.session_id = g.session_id
 AND p.utime = g.gap_start_utime
WHERE g.sensor = 'gps'
ORDER BY g.gap_s DESC
