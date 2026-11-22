"""Steps 1-2 (fetch + extract): get one session's five sensor CSVs into the buffer.

Contract: when fetch_session() returns, buffer/<session>/ holds exactly the files
in SENSOR_FILES plus a .complete marker. Anything less raises an exception.

- Resumable: an interrupted download continues from its .part file.
- Idempotent: a session already complete in the buffer is not downloaded again.
- Safe extraction: files are written by their base name only, so a malicious or
  odd archive path (e.g. "../../etc/x") can't write outside the session folder.
"""

import hashlib
import logging
import shutil
import tarfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from nclt_lab.buffer import WorkBuffer
from nclt_lab.config import BASE_URL
from nclt_lab.sensors import SENSOR_FILES

log = logging.getLogger(__name__)
# 1 MiB: memory use stays flat no matter how big the archive is
CHUNK = 1 << 20


def archive_url(session: str) -> str:
    return f"{BASE_URL}/{session}_sen.tar.gz"


def sha256_of(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def download(url: str, dest: Path, expected_sha256: str = "") -> Path:
    """Stream `url` into `dest`, resuming a partial `dest.part` if one exists."""

    part = dest.with_name(dest.name + ".part")
    start = part.stat().st_size if part.exists() else 0

    headers = {"Range": f"bytes={start}-"} if start else {}

    try:
        # Read and update hash in chunks to handle large files efficiently
        response = urlopen(Request(url, headers=headers), timeout=60)

    except HTTPError as err:
        # range starts at or past the end: the .part is already complete
        if err.code == 416:
            return part.replace(dest)
        raise

    with response, part.open("ab") as out:
        # server ignored Range: start over
        if start and response.status == 200:
            out.truncate(0)

        while chunk := response.read(CHUNK):
            out.write(chunk)

    return part.replace(dest)


def extract_needed(archive: Path, folder: Path) -> list[Path]:
    """Copy only the SENSOR_FILES out of the archive into `folder`."""
    wanted = set(SENSOR_FILES)
    extracted: list[Path] = []

    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            name = Path(member.name).name

            if not (member.isfile() and name in wanted):
                continue

            # base name only, no archive paths
            target = folder / name

            source = tar.extractfile(member)
            if source is None:
                continue

            with source, target.open("wb") as out:
                shutil.copyfileobj(source, out, CHUNK)

            extracted.append(target)

    missing = wanted - {p.name for p in extracted}
    if missing:
        raise FileNotFoundError(f"{archive.name} is missing {sorted(missing)}")

    return extracted


def fetch_session(session: str, buffer: WorkBuffer) -> Path:
    """Steps 1-2 for one session. Returns the folder holding its CSVs."""
    folder = buffer.claim(session)

    if buffer.is_complete(session):
        log.info("fetch %s: already in buffer, skipping download", session)
        return folder

    started = time.perf_counter()
    archive = folder / f"{session}_sen.tar.gz"
    download(archive_url(session), archive)
    archive_bytes = archive.stat().st_size

    try:
        extract_needed(archive, folder)
    except (tarfile.TarError, EOFError):
        # corrupt or truncated: force a fresh download next run
        archive.unlink(missing_ok=True)
        raise

    # the archive is never needed again
    archive.unlink()

    buffer.mark_complete(session)

    log.info(
        "fetch %s: %.1f MiB archive in %.1f s",
        session,
        archive_bytes / 2**20,
        time.perf_counter() - started,
    )

    return folder
