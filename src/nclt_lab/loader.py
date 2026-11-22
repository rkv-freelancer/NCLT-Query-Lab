"""Step 4 (load): stream a session's CSVs into staging, validate, swap into the
final tables, all in ONE transaction.

If anything fails or the process dies at any point, Postgres rolls the whole
transaction back: the final tables and dim_session are exactly as they were.
That's the crash-safety guarantee (outcome O1).
"""

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg import sql

from nclt_lab.config import session_id
from nclt_lab.sensors import SENSORS, SensorSpec

log = logging.getLogger(__name__)
CHUNK = 1 << 20


class ValidationError(RuntimeError):
    """The staged data doesn't match the source file; the transaction rolls back."""


@dataclass
class LoadReport:
    session: str
    rows: dict[str, int] = field(default_factory=dict)
    duplicate_utimes: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())


def maybe_crash(stage: str) -> None:
    """Crash-test hook. Run with NCLT_CRASH_AT=copy:imu (or swap) to kill the
    process at that point, then check that the final tables didn't change."""
    if os.environ.get("NCLT_CRASH_AT") == stage:
        log.warning("NCLT_CRASH_AT=%s: exiting abruptly", stage)
        os._exit(137)  # no cleanup, no commit: like an OOM kill


class SessionLoader:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def load(self, session: str, folder: Path) -> LoadReport:
        sid = session_id(session)
        report = LoadReport(session)
        started = time.perf_counter()

        with self.conn.transaction(), self.conn.cursor() as cur:
            for spec in SENSORS:
                cur.execute(
                    sql.SQL("TRUNCATE {} RESTART IDENTITY").format(
                        sql.Identifier(spec.staging_table)
                    )
                )

                file_lines = self._copy(cur, spec, folder / spec.file)
                maybe_crash(f"copy:{spec.table}")

                staged, duplicates = self._validate(cur, spec, file_lines)
                # rows that will be in the final table after the last-row-wins rule
                report.rows[spec.table] = staged - duplicates
                report.duplicate_utimes[spec.table] = duplicates

            start_utime, end_utime = self._time_span(cur)
            self._swap(cur, sid, report.rows, report.duplicate_utimes)
            maybe_crash("swap")
            self._record_session(cur, sid, session, start_utime, end_utime, report.total_rows)
        # leaving the `with` block commits; an exception rolls everything back

        report.seconds = time.perf_counter() - started

        return report

    # --- steps inside the transaction -------------------------------------

    def _copy(self, cur: psycopg.Cursor, spec: SensorSpec, csv_path: Path) -> int:
        """Stream the file into its staging table; return its line count."""
        statement = sql.SQL("COPY {} ({}) FROM STDIN (FORMAT csv, NULL 'nan')").format(
            sql.Identifier(spec.staging_table),
            sql.SQL(", ").join(map(sql.Identifier, spec.columns)),
        )
        lines, last_byte = 0, b"\n"

        with csv_path.open("rb") as f, cur.copy(statement) as copy:
            while chunk := f.read(CHUNK):
                copy.write(chunk)
                lines += chunk.count(b"\n")
                last_byte = chunk[-1:]

        # last line had no trailing newline
        if last_byte != b"\n":
            lines += 1

        return lines

    def _validate(self, cur: psycopg.Cursor, spec: SensorSpec, file_lines: int) -> tuple[int, int]:
        cur.execute(
            sql.SQL("SELECT count(*), count(*) - count(DISTINCT utime) FROM {}").format(
                sql.Identifier(spec.staging_table)
            )
        )

        result = cur.fetchone()
        if result is None:
            raise ValidationError(f"{spec.table}: validation query returned no result")

        staged, duplicates = result

        if staged != file_lines:
            raise ValidationError(f"{spec.table}: {file_lines:,} lines in file, {staged:,} staged")

        if duplicates:
            # Policy: last row wins. A later reading for the same utime replaces the
            # earlier ones in _swap; values are never averaged (an average is a state
            # the robot was never in).
            log.warning(
                "%s: %d rows share a utime with a later row; keeping the last",
                spec.table,
                duplicates,
            )

        return staged, duplicates

    def _time_span(self, cur: psycopg.Cursor) -> tuple[int, int]:
        """Earliest and latest utime across all staged sensors."""
        parts = [
            sql.SQL("SELECT min(utime) AS lo, max(utime) AS hi FROM {}").format(
                sql.Identifier(spec.staging_table)
            )
            for spec in SENSORS
        ]
        cur.execute(
            sql.SQL("SELECT min(lo), max(hi) FROM ({}) AS spans").format(
                sql.SQL(" UNION ALL ").join(parts)
            )
        )
        result = cur.fetchone()

        if result is None or result[0] is None or result[1] is None:
            raise ValidationError("cannot determine time span from empty staged data")

        return int(result[0]), int(result[1])

    def _record_session(
        self,
        cur: psycopg.Cursor,
        sid: int,
        session: str,
        start_utime: int,
        end_utime: int,
        rows: int,
    ) -> None:
        cur.execute(
            """
            INSERT INTO dim_session
                (session_id, session_date, start_utime, end_utime, rows_loaded, status, loaded_at)
            VALUES (%s, %s, %s, %s, %s, 'loaded', now())
            ON CONFLICT (session_id) DO UPDATE SET
                start_utime = EXCLUDED.start_utime,
                end_utime   = EXCLUDED.end_utime,
                rows_loaded = EXCLUDED.rows_loaded,
                status      = EXCLUDED.status,
                loaded_at   = EXCLUDED.loaded_at
            """,
            (sid, session, start_utime, end_utime, rows),
        )

    def _swap(
        self, cur: psycopg.Cursor, sid: int, expected: dict[str, int], duplicates: dict[str, int]
    ) -> None:
        for spec in SENSORS:
            table, staging = sql.Identifier(spec.table), sql.Identifier(spec.staging_table)
            columns = sql.SQL(", ").join(map(sql.Identifier, spec.columns))

            if duplicates.get(spec.table):
                # last row in the file wins for each utime; never averaged
                source = sql.SQL(
                    "SELECT DISTINCT ON (utime) {c} FROM {s} ORDER BY utime, line_no DESC"
                ).format(c=columns, s=staging)
            else:
                source = sql.SQL("SELECT {c} FROM {s}").format(c=columns, s=staging)

            cur.execute(sql.SQL("DELETE FROM {} WHERE session_id = %s").format(table), (sid,))
            cur.execute(
                sql.SQL(
                    "INSERT INTO {t} (session_id, {c}) SELECT %s, {c} FROM ({src}) AS kept"
                ).format(t=table, c=columns, src=source),
                (sid,),
            )

            inserted = cur.rowcount
            if inserted != expected[spec.table]:
                raise ValidationError(
                    f"{spec.table}: expected {expected[spec.table]:,} rows after removing "
                    f"{duplicates.get(spec.table, 0):,} duplicates, inserted {inserted:,}"
                )
