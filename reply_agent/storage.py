import fcntl
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Final, override
from zoneinfo import ZoneInfo

from .models import Ledger

SEOUL: Final = ZoneInfo("Asia/Seoul")


def now() -> datetime:
    """Use the same Korea-time clock for action and session records."""
    return datetime.now(SEOUL)


class BlockedError(Exception):
    """Keep exception traceback mutable for contextmanager propagation."""

    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason


@contextmanager
def locked(directory: Path) -> Generator[None]:
    """Serialize all ledger mutations across processes."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "ledger.lock").open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read(directory: Path) -> Ledger:
    """Read existing state without recovering corrupt records silently."""
    path = directory / "ledger.json"
    return Ledger.model_validate_json(path.read_text()) if path.exists() else Ledger()


def save(directory: Path, ledger: Ledger) -> None:
    """Persist before browser actions; atomic replacement survives interruption."""
    temporary = directory / "ledger.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        _ = handle.write(ledger.model_dump_json(indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    _ = temporary.replace(directory / "ledger.json")
