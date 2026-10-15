"""Session ownership and three consecutive pre-dispatch failures."""

from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

from . import storage as store
from .models import Ledger
from .workflow import ExecutionSession, FailureEvent

FAILURE_LIMIT: Final = 3


def require_session(
    ledger: Ledger, run_id: UUID, session_id: UUID | None
) -> ExecutionSession:
    """Reject released owners so an old worker cannot resume external actions."""
    session = next((item for item in ledger.sessions if item.id == session_id), None)
    if session is None or session.run_id != run_id or session.released_at is not None:
        msg = "Claim a new active execution session for this run"
        raise store.BlockedError(msg)
    return session


def start_session(directory: Path, run_id: UUID, owner: str) -> ExecutionSession:
    """Claim the same open run after the old browser worker has stopped."""
    with store.locked(directory):
        ledger = store.read(directory)
        run = next((item for item in ledger.runs if item.id == run_id), None)
        if run is None or run.receipt is not None or (directory / "STOP").exists():
            msg = "Run unavailable or STOP is present"
            raise store.BlockedError(msg)
        if any(item.released_at is None for item in ledger.sessions):
            msg = "Another execution session still owns the profile"
            raise store.BlockedError(msg)
        if any(item.receipt is None for item in ledger.attempts):
            msg = "Reconcile pending actions before taking ownership"
            raise store.BlockedError(msg)
        previous = [item for item in ledger.sessions if item.run_id == run_id]
        if previous and previous[-1].handoff_required and previous[-1].owner == owner:
            msg = "Three failures require a different worker owner"
            raise store.BlockedError(msg)
        session = ExecutionSession(
            id=uuid4(), run_id=run_id, owner=owner, started_at=store.now()
        )
        store.save(
            directory,
            ledger.model_copy(update={"sessions": (*ledger.sessions, session)}),
        )
        return session


def release_session(directory: Path, session_id: UUID, reason: str) -> None:
    """Release ownership only after stopping the corresponding browser worker."""
    with store.locked(directory):
        ledger = store.read(directory)
        session = next(
            (item for item in ledger.sessions if item.id == session_id), None
        )
        if session is None or session.released_at is not None:
            msg = "Execution session is missing or already released"
            raise store.BlockedError(msg)
        updated = session.model_copy(
            update={"released_at": store.now(), "next_action": reason}
        )
        store.save(
            directory,
            ledger.model_copy(
                update={
                    "sessions": tuple(
                        updated if item.id == session_id else item
                        for item in ledger.sessions
                    )
                }
            ),
        )


def record_failure(
    directory: Path, session_id: UUID, operation: str, reason: str, next_action: str
) -> ExecutionSession:
    """Record reads or proven pre-dispatch failures; never authorize a resend."""
    with store.locked(directory):
        ledger = store.read(directory)
        original = next(
            (item for item in ledger.sessions if item.id == session_id), None
        )
        if original is None:
            msg = "Unknown execution session"
            raise store.BlockedError(msg)
        session = require_session(ledger, original.run_id, session_id)
        if any(item.receipt is None for item in ledger.attempts):
            msg = "Pending dispatch requires reconciliation, not retry counting"
            raise store.BlockedError(msg)
        event = FailureEvent(
            session_id=session_id,
            operation=operation,
            reason=reason,
            next_action=next_action,
            occurred_at=store.now(),
        )
        count = (
            session.consecutive_failures + 1 if session.operation == operation else 1
        )
        handoff = count >= FAILURE_LIMIT
        updated = session.model_copy(
            update={
                "operation": operation,
                "consecutive_failures": count,
                "last_error": reason,
                "next_action": next_action,
                "handoff_required": handoff,
                "released_at": store.now() if handoff else None,
            }
        )
        store.save(
            directory,
            ledger.model_copy(
                update={
                    "sessions": tuple(
                        updated if item.id == session_id else item
                        for item in ledger.sessions
                    ),
                    "failures": (*ledger.failures, event),
                }
            ),
        )
        return updated


def record_success(directory: Path, session_id: UUID) -> None:
    """Reset the streak after an observed successful operation."""
    with store.locked(directory):
        ledger = store.read(directory)
        original = next(
            (item for item in ledger.sessions if item.id == session_id), None
        )
        if original is None:
            msg = "Unknown execution session"
            raise store.BlockedError(msg)
        session = require_session(ledger, original.run_id, session_id)
        updated = session.model_copy(
            update={
                "operation": "",
                "consecutive_failures": 0,
                "last_error": "",
                "next_action": "",
            }
        )
        store.save(
            directory,
            ledger.model_copy(
                update={
                    "sessions": tuple(
                        updated if item.id == session_id else item
                        for item in ledger.sessions
                    )
                }
            ),
        )
