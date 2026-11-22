-- Q2: a covering index on the filter column. INCLUDE (session_id, utime) carries
-- the join keys, so the error_m filter can run as an index-only scan instead of
-- visiting gps_rtk_err's heap. Same join-side index as q2_indexes, so the only
-- difference from that run is plain error_m index vs covering error_m index.
--   make bench LABEL=q2_covering SETUP=q2_covering QUERIES=q2
CREATE INDEX IF NOT EXISTS bench_gps_rtk_btree     ON gps_rtk (session_id, utime);
CREATE INDEX IF NOT EXISTS bench_gps_rtk_err_cover ON gps_rtk_err (error_m) INCLUDE (session_id, utime);
