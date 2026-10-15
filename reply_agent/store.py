"""Locked, atomic persistence and duplicate suppression."""

from datetime import datetime
from pathlib import Path
from typing import Final, assert_never
from uuid import UUID, uuid4

from .identity import action_keys, article_urls
from .models import (
    Action,
    Attempt,
    Connection,
    Receipt,
    Request,
    RunOutcome,
    RunReceipt,
    RunRecord,
    RunTargets,
    Visit,
)
from .policy import enforce
from .reporting import summarize, write_report
from .sessions import require_session
from .storage import SEOUL, BlockedError, locked, now, read, save
from .workflow import ArticleObservation, ArticleRecord

DAILY_LIMIT: Final = 100
CURRENT_POLICY: Final = 2
__all__ = [
    "BlockedError",
    "finish",
    "finish_run",
    "locked",
    "mark_visit",
    "note_visit",
    "now",
    "read",
    "record_article",
    "reserve",
    "save",
    "start_run",
    "visited_today",
]


def mark_visit(directory: Path, url: str, run_id: UUID | None = None) -> Visit:
    """Record a candidate URL before the browser opens it."""
    with locked(directory):
        ledger = read(directory)
        now = datetime.now(SEOUL)
        today = now.date()
        if any(
            item.url in article_urls(ledger, url)
            and item.visited_at.astimezone(SEOUL).date() == today
            for item in ledger.visits
        ):
            raise BlockedError("Article was already visited today")
        visit = Visit(url=url, visited_at=now, run_id=run_id)
        save(directory, ledger.model_copy(update={"visits": (*ledger.visits, visit)}))
        return visit


def visited_today(directory: Path, url: str) -> bool:
    """Check today's visit ledger without opening the article."""
    now = datetime.now(SEOUL).date()
    with locked(directory):
        ledger = read(directory)
        return any(
            item.url in article_urls(ledger, url)
            and item.visited_at.astimezone(SEOUL).date() == now
            for item in ledger.visits
        )


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
        run = next((item for item in ledger.runs if item.id == request.run_id), None)
        if run is None or run.receipt is not None:
            raise BlockedError("Scheduled run is missing or already closed")
        if run.targets is None:
            raise BlockedError("Legacy run has no declared targets")
        if any(item.receipt is None for item in ledger.attempts):
            raise BlockedError("Pending action: inspect the browser before continuing")
        enforce(ledger, run, request)
        if any(
            action_keys(ledger, item.request) & action_keys(ledger, request)
            for item in ledger.attempts
        ):
            raise BlockedError("Action already reserved; automatic retry is forbidden")
        confirmed = sum(
            item.request.run_id == run.id
            and item.request.action == request.action
            and item.receipt is not None
            and item.receipt.outcome == "confirmed"
            for item in ledger.attempts
        )
        loop_target = run.targets.loops
        if (
            run.targets.for_action(request.action) == 0
            or (
                loop_target is None
                and confirmed >= run.targets.for_action(request.action)
            )
            or (
                loop_target is not None
                and summarize(ledger, run).complete_loops >= loop_target
            )
        ):
            raise BlockedError("Run action target already reached or excluded")
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


def start_run(directory: Path, slot: datetime, targets: RunTargets) -> RunRecord:
    """Persist explicit goals before any browser action is reserved."""
    with locked(directory):
        if (directory / "STOP").exists():
            raise BlockedError("STOP file is present")
        ledger = read(directory)
        if any(item.receipt is None for item in ledger.runs):
            raise BlockedError("Previous scheduled run is still open")
        if any(item.slot == slot for item in ledger.runs):
            raise BlockedError("Scheduled slot already recorded")
        run = RunRecord(
            id=uuid4(),
            slot=slot,
            started_at=datetime.now(SEOUL),
            targets=targets,
            policy_version=CURRENT_POLICY,
        )
        save(directory, ledger.model_copy(update={"runs": (*ledger.runs, run)}))
        return run


def finish_run(directory: Path, receipt: RunReceipt) -> None:
    """Close a scheduled run with its target counts and stop reason."""
    if receipt.termination is None:
        raise BlockedError("A machine-readable run termination is required")
    with locked(directory):
        ledger = read(directory)
        target = next((item for item in ledger.runs if item.id == receipt.run_id), None)
        if target is None:
            raise BlockedError("Unknown scheduled run ID")
        if target.receipt is not None:
            raise BlockedError("Scheduled run already closed")
        attempts = [x for x in ledger.attempts if x.request.run_id == target.id]
        counts = tuple(
            sum(
                x.request.action == action
                and x.receipt is not None
                and x.receipt.outcome == "confirmed"
                for x in attempts
            )
            for action in Action
        )
        if counts != (
            receipt.confirmed_comments,
            receipt.confirmed_likes,
            receipt.confirmed_subscriptions,
        ):
            raise BlockedError("Run counts do not match confirmed action receipts")
        match receipt.outcome:
            case RunOutcome.COMPLETED:
                if target.targets is None:
                    raise BlockedError("Legacy run has no declared targets")
                goals_met = (
                    summarize(ledger, target).complete_loops >= target.targets.loops
                    if target.targets.loops is not None
                    else all(
                        count >= target.targets.for_action(action)
                        for action, count in zip(Action, counts, strict=True)
                    )
                )
                if any(x.receipt is None for x in attempts) or not goals_met:
                    raise BlockedError("Declared run targets have not been confirmed")
            case RunOutcome.EXHAUSTED | RunOutcome.BLOCKED | RunOutcome.CANCELLED:
                pass
            case unreachable:
                assert_never(unreachable)
        completed = target.model_copy(update={"receipt": receipt})
        closed = ledger.model_copy(
            update={
                "runs": tuple(
                    completed if item.id == target.id else item for item in ledger.runs
                ),
                "sessions": tuple(
                    item.model_copy(update={"released_at": now()})
                    if item.run_id == target.id and item.released_at is None
                    else item
                    for item in ledger.sessions
                ),
            }
        )
        save(directory, closed)
        _ = write_report(directory, summarize(closed, completed))


def record_article(
    directory: Path, run_id: UUID, session_id: UUID, observation: ArticleObservation
) -> ArticleRecord:
    """Freeze initial subscription facts and verified aliases before acting."""
    with locked(directory):
        ledger = read(directory)
        _ = require_session(ledger, run_id, session_id)
        run = next((item for item in ledger.runs if item.id == run_id), None)
        if run is None or run.receipt is not None:
            raise BlockedError("Run unavailable")
        aliases = {observation.url, *observation.aliases}
        if any(
            item.run_id == run_id and aliases & {item.url, *item.aliases}
            for item in ledger.articles
        ):
            raise BlockedError("Initial article observation is already recorded")
        if not any(
            item.url in aliases
            and item.visited_at.astimezone(SEOUL).date() == now().date()
            for item in ledger.visits
        ):
            raise BlockedError("Record the visit before opening the article")
        reason = observation.skip_reason
        if (
            run.targets
            and run.targets.loops is not None
            and (observation.own_comment or observation.liked)
        ):
            reason = (
                reason
                or "Existing comment or like: exclude from new three-action loops"
            )
        if (
            run.targets
            and run.targets.unsubscribed_only
            and not observation.unsubscribed
        ):
            reason = reason or "Already subscribed"
        if (
            run.targets
            and run.targets.source
            and observation.source != run.targets.source
        ):
            reason = reason or "Outside requested discovery source"
        record = ArticleRecord(
            url=observation.url,
            aliases=observation.aliases,
            title=observation.title,
            source=observation.source,
            source_evidence=observation.source_evidence,
            unsubscribed=observation.unsubscribed,
            own_comment=observation.own_comment,
            liked=observation.liked,
            body_read=observation.body_read,
            comments_checked=observation.comments_checked,
            evidence=observation.evidence,
            skip_reason=reason,
            run_id=run_id,
            observed_at=now(),
        )
        visits = tuple(
            item.model_copy(update={"run_id": run_id, "reason": reason or ""})
            if item.url in aliases
            and item.run_id in (None, run_id)
            and item.visited_at.astimezone(SEOUL).date() == now().date()
            else item
            for item in ledger.visits
        )
        missing = tuple(
            Visit(url=url, visited_at=now(), run_id=run_id, reason=reason or "")
            for url in aliases
            if not any(
                item.url == url
                and item.visited_at.astimezone(SEOUL).date() == now().date()
                for item in visits
            )
        )
        save(
            directory,
            ledger.model_copy(
                update={
                    "articles": (*ledger.articles, record),
                    "visits": (*visits, *missing),
                }
            ),
        )
        return record


def note_visit(directory: Path, url: str, run_id: UUID, reason: str) -> None:
    """Retain an exclusion or access failure when article inspection is incomplete."""
    with locked(directory):
        ledger = read(directory)
        if not any(
            item.url == url and item.visited_at.astimezone(SEOUL).date() == now().date()
            for item in ledger.visits
        ):
            raise BlockedError("Visit not found for today")
        save(
            directory,
            ledger.model_copy(
                update={
                    "visits": tuple(
                        item.model_copy(update={"run_id": run_id, "reason": reason})
                        if item.url == url
                        and item.visited_at.astimezone(SEOUL).date() == now().date()
                        else item
                        for item in ledger.visits
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
        session_id = target.request.session_id
        sessions = ledger.sessions
        if receipt.outcome == "confirmed" and session_id is not None:
            sessions = tuple(
                item.model_copy(
                    update={
                        "operation": "",
                        "consecutive_failures": 0,
                        "last_error": "",
                        "next_action": "",
                    }
                )
                if item.id == session_id
                else item
                for item in sessions
            )
        save(
            directory,
            ledger.model_copy(
                update={
                    "attempts": tuple(
                        completed if item.id == target.id else item
                        for item in ledger.attempts
                    ),
                    "sessions": sessions,
                }
            ),
        )
