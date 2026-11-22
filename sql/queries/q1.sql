-- Q1: IMU readings in one session between t1 and t2 (time-range scan).
-- Lesson: selectivity decides whether an index helps.
-- Params: session_id, t1, t2 (utime, microseconds)
SELECT *
FROM imu
WHERE session_id = %(session_id)s
  AND utime BETWEEN %(t1)s AND %(t2)s
