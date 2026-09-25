"""Locked, atomic persistence and duplicate suppression."""

import fcntl
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Final, override
from uuid import uuid4
from zoneinfo import ZoneInfo

from .models import (
    Attempt,
    Connection,
    Ledger,
    Receipt,
    Request,
    RunReceipt,
    RunRecord,
)

SEOUL: Final = ZoneInfo("Asia/Seoul")
DAILY_LIMIT: Final = 100


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


def reserve(directory: Path, request: Request) -> Attempt:
    """Reserve once, enforcing identity, action keys and daily budgets."""
    with locked(directory):
        if (directory / "STOP").exists():
            raise BlockedError("STOP file is present")
        expected = Connection.model_validate_json(
            (directory / "connection.json").read_text(),
        )
        if request.connection != expected:
            raise BlockedError("Browser/account identity does not match local config")
        ledger = read(directory)
        if request.run_id is None:
            raise BlockedError("Start a scheduled run before reserving actions")
        run = next((item for item in ledger.runs if item.id == request.run_id), None)
        if run is None or run.receipt is not None:
            raise BlockedError("Scheduled run is missing or already closed")
        if any(item.receipt is None for item in ledger.attempts):
            raise BlockedError("Pending action: inspect the browser before continuing")
        if any(item.request.key == request.key for item in ledger.attempts):
            raise BlockedError("Action already reserved; automatic retry is forbidden")
        now = datetime.now(SEOUL)
        today = [
            item
            for item in ledger.attempts
            if item.created_at.astimezone(SEOUL).date() == now.date()
        ]
        if sum(item.request.action == request.action for item in today) >= DAILY_LIMIT:
            raise BlockedError("Daily action budget reached")
        attempt = Attempt(id=uuid4(), created_at=now, request=request)
        save(
            directory,
            ledger.model_copy(update={"attempts": (*ledger.attempts, attempt)}),
        )
        return attempt


def start_run(directory: Path, slot: datetime) -> RunRecord:
    """Claim one scheduled slot before any browser action is reserved."""
    with locked(directory):
        if (directory / "STOP").exists():
            raise BlockedError("STOP file is present")
        ledger = read(directory)
        if any(item.receipt is None for item in ledger.runs):
            raise BlockedError("Previous scheduled run is still open")
        if any(item.slot == slot for item in ledger.runs):
            raise BlockedError("Scheduled slot already recorded")
        run = RunRecord(id=uuid4(), slot=slot, started_at=datetime.now(SEOUL))
        save(directory, ledger.model_copy(update={"runs": (*ledger.runs, run)}))
        return run


def finish_run(directory: Path, receipt: RunReceipt) -> None:
    """Close a scheduled run with its target counts and stop reason."""
    with locked(directory):
        ledger = read(directory)
        target = next((item for item in ledger.runs if item.id == receipt.run_id), None)
        if target is None:
            raise BlockedError("Unknown scheduled run ID")
        if target.receipt is not None:
            raise BlockedError("Scheduled run already closed")
        completed = target.model_copy(update={"receipt": receipt})
        save(
            directory,
            ledger.model_copy(
                update={
                    "runs": tuple(
                        completed if item.id == target.id else item
                        for item in ledger.runs
                    )
                }
            ),
        )


def finish(directory: Path, receipt: Receipt) -> None:
    """Record confirmation or uncertainty without enabling a second submission."""
    with locked(directory):
        ledger = read(directory)
        target = next(
            (item for item in ledger.attempts if item.id == receipt.attempt_id), None
        )
        if target is None:
            raise BlockedError("Unknown attempt ID")
        if target.receipt is not None:
            raise BlockedError("Attempt already closed")
        completed = target.model_copy(update={"receipt": receipt})
        save(
            directory,
            ledger.model_copy(
                update={
                    "attempts": tuple(
                        completed if item.id == target.id else item
                        for item in ledger.attempts
                    )
                }
            ),
        )
