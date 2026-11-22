"""Pipeline entry point.

python -m nclt_lab.main ingest 2012-01-08 2012-05-11
python -m nclt_lab.main ingest --all
python -m nclt_lab.main ingest --all --force        # reload everything
python -m nclt_lab.main gaps                        # step 6

Steps 7-10 have their own entry points (see the Makefile):
python -m nclt_lab.benchmarks | spatial | sweep | explorer | export | report
"""

import argparse
import logging
import sys

from nclt_lab import gaps
from nclt_lab.config import ALL_SESSIONS
from nclt_lab.ingest import ingest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nclt_lab", description="NCLT Query Lab pipeline")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest_cmd = commands.add_parser(
        "ingest", help="steps 1-5: fetch, extract, profile, load, clean"
    )
    ingest_cmd.add_argument("sessions", nargs="*", help="session dates, e.g. 2012-01-08")
    ingest_cmd.add_argument("--all", action="store_true", help="all 27 sessions")
    ingest_cmd.add_argument("--force", action="store_true", help="reload sessions already loaded")
    ingest_cmd.add_argument("--keep-files", action="store_true", help="keep CSVs after loading")

    gaps_cmd = commands.add_parser("gaps", help="step 6: build sensor_gaps and reconcile")
    gaps.add_arguments(gaps_cmd)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if args.command == "ingest":
        sessions: list[str] = list(ALL_SESSIONS) if args.all else args.sessions
        if not sessions:
            parser.error("[ERROR] give session dates, or --all")

        unknown = sorted(set(sessions) - set(ALL_SESSIONS))
        if unknown:
            parser.error(f"[ERROR] not NCLT sessions: {', '.join(unknown)}")

        sessions = sorted(set(sessions), key=ALL_SESSIONS.index)  # time order, no repeats

        ingest(sessions, force=args.force, keep_files=args.keep_files)

    elif args.command == "gaps":
        return gaps.run(args.sensor, args.percentiles)

    return 0


if __name__ == "__main__":
    sys.exit(main())
