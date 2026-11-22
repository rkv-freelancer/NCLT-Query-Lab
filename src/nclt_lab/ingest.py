"""
Steps 1-5 for many sessions, one at a time:

    fetch -> extract -> profile -> load -> clean

A failed session stops the run and keeps its files in the buffer; rerunning the
same command resumes it. Sessions already loaded are skipped unless force=True.
"""

import csv
import logging
import time
from pathlib import Path

from nclt_lab import db
from nclt_lab.buffer import WorkBuffer
from nclt_lab.config import PROFILE_CSV
from nclt_lab.fetch import fetch_session
from nclt_lab.loader import SessionLoader
from nclt_lab.profile import profile
from nclt_lab.sensors import SENSORS

log = logging.getLogger(__name__)


def profile_session(session: str, folder: Path) -> list[dict]:
    """Step 3: facts about each sensor file, saved before the data is loaded."""
    rows = [{"session": session, **profile(folder / spec.file)} for spec in SENSORS]
    upsert_profile(session, rows)
    return rows


def upsert_profile(session: str, rows: list[dict], path: Path = PROFILE_CSV) -> None:
    """Replace this session's rows in profile.csv, keeping every other session's.

    Written to a temporary file first, then renamed over the original: a crash
    mid-write can never leave a half-written profile.csv.
    """
    kept: list[dict] = []
    if path.exists():
        with path.open(newline="") as f:
            kept = [r for r in csv.DictReader(f) if r["session"] != session]

    fieldnames = [key for key in rows[0] if key != "first_line"]
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept + rows)

    temporary.replace(path)


def ingest(sessions: list[str], *, force: bool = False, keep_files: bool = False) -> None:
    buffer = WorkBuffer()
    with db.connect() as conn:
        db.apply_schema(conn)
        already_loaded = db.loaded_sessions(conn)
        loader = SessionLoader(conn)

        for session in sessions:
            if session in already_loaded and not force:
                log.info("[INFO] %s: already loaded, skipping (use --force to reload)", session)
                continue

            started = time.perf_counter()

            # Steps 1-2
            folder = fetch_session(session, buffer)

            # Step 3
            profile_session(session, folder)

            # Step 4 (commits)
            report = loader.load(session, folder)

            if not keep_files:
                # Step 5
                buffer.release(session)

            log.info(
                "[INFO] %s: %s rows loaded in %.1f s (%s rows/s), session total %.1f s",
                session,
                f"{report.total_rows:,}",
                report.seconds,
                f"{report.total_rows / report.seconds:,.0f}" if report.seconds else "n/a",
                time.perf_counter() - started,
            )

        db.vacuum_analyze(conn)
        log.info("[INFO] done; buffer now holds %.1f MiB", buffer.bytes_used() / 2**20)
