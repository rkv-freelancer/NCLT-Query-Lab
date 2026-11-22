-- Q1 against imu_shuffled: the same real rows in random physical order
-- (created by experiments/q1_brin_shuffled.sql). Shows BRIN degrading.
-- Params: session_id, t1, t2
SELECT *
FROM imu_shuffled
WHERE session_id = %(session_id)s
  AND utime BETWEEN %(t1)s AND %(t2)s
