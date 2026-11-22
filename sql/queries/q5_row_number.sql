-- Q5 (slow version): the last 100 IMU readings of every session.
-- Numbers all ~6M rows to keep ~2,700 of them.
SELECT *
FROM (
    SELECT imu.*,
           row_number() OVER (PARTITION BY session_id ORDER BY utime DESC) AS rn
    FROM imu
) AS ranked
WHERE rn <= 100
