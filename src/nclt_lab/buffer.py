"""The work buffer: a folder that holds at most `capacity` sessions' files at once.

Layout:  data/buffer/<session>/{five CSVs, .complete}

A session folder exists from the moment fetch starts until clean deletes it, which
happens only after its load commits. If anything fails in between, the folder
stays, and the next run resumes from it instead of downloading again.
"""

import shutil
from pathlib import Path

from nclt_lab.config import BUFFER_CAPACITY, BUFFER_DIR


class BufferFull(RuntimeError):
    """Raised when claiming a new session would exceed the buffer's capacity."""


class WorkBuffer:
    MARKER = ".complete"  # written after extraction succeeds

    def __init__(self, root: Path = BUFFER_DIR, capacity: int = BUFFER_CAPACITY) -> None:
        self.root = root
        self.capacity = capacity
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, session: str) -> Path:
        return self.root / session

    def sessions(self) -> list[str]:
        """Sessions currently holding a folder in the buffer."""
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def claim(self, session: str) -> Path:
        """Reserve a folder for a session; reuse it if a previous run left one."""
        folder = self.path(session)
        if folder.exists():
            return folder
        occupied = self.sessions()
        if len(occupied) >= self.capacity:
            raise BufferFull(
                f"Buffer holds {occupied} (capacity {self.capacity}). "
                "Rerun those sessions to finish them, or delete their folders."
            )
        folder.mkdir()
        return folder

    def is_complete(self, session: str) -> bool:
        return (self.path(session) / self.MARKER).exists()

    def mark_complete(self, session: str) -> None:
        (self.path(session) / self.MARKER).touch()

    def release(self, session: str) -> None:
        """Step 5 (clean): delete the session's files. Call only after its load commits."""
        folder = self.path(session)
        if folder.exists():
            shutil.rmtree(folder)

    def bytes_used(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
