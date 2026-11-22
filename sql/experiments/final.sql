-- Final index set: the combination chosen after the one-at-a-time runs.
--   make bench LABEL=final SETUP=final QUERIES=q1,q2,q2_filter_first,q3_summary,q4,q5_lateral,q6,q7_view,q8
--
-- Primary keys on (session_id, utime). Their index IS the B-tree, so no separate one.
-- Safe because the loader keeps one row per utime (last row wins, data/README.md).
-- NOT named bench_*: the harness drops bench_* with DROP INDEX, which a constraint's
-- index refuses. Undo with final_undo.sql before any later baseline or sweep run.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['imu', 'odometry', 'gps', 'gps_rtk', 'gps_rtk_err'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conrelid = t::regclass AND contype = 'p'
        ) THEN
            EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I PRIMARY KEY (session_id, utime)',
                           t, t || '_pkey');
        END IF;
    END LOOP;
END $$;

-- The other winners, as bench_* indexes (keep only the ones that won):
CREATE INDEX IF NOT EXISTS bench_gps_rtk_err_error ON gps_rtk_err (error_m);
CREATE INDEX IF NOT EXISTS bench_gps_geom_gist     ON gps USING gist (geom);

ANALYZE;  -- fresh statistics; VACUUM can't run inside this multi-statement script