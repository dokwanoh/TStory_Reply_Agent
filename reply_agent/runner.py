"""Orchestrate one article at a time using observed browser results."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from pydantic_core import PydanticCustomError

from . import sessions, store
from .comments import compose
from .identity import article_urls
from .models import (
    Action,
    Candidate,
    Connection,
    Receipt,
    Request,
    RunOutcome,
    RunReceipt,
    RunRecord,
    RunTargets,
    RunTermination,
)
from .reporting import summarize, write_report
from .workflow import ArticleObservation, ArticleRecord, RunSummary


class BrowserError(Exception):
    """A dispatched action may have succeeded despite the reported failure."""


class ReadError(BrowserError):
    """The adapter guarantees this read dispatched no external action."""


@dataclass(frozen=True, slots=True)
class ReceiptObservation:
    outcome: str
    evidence: str


class BrowserAdapter(Protocol):
    def verify_connection(self, connection: Connection) -> None:
        """Verify the live Chrome profile, extension and logged-in blog."""
        ...

    def inspect(self, candidate: Candidate) -> ArticleObservation:
        """Read article and reaction states without submitting any reaction."""
        ...

    def draft_reaction(self, article: ArticleRecord) -> str:
        """Return one contextual impression without the conditional greeting."""
        ...

    def perform(self, action: Action, content: str) -> ReceiptObservation:
        """Act once on the inspected article and return visible outcome evidence."""
        ...


@dataclass(frozen=True, slots=True)
class RunPlan:
    connection: Connection
    slot: datetime
    candidates: tuple[Candidate, ...]
    targets: RunTargets
    owner: str
    run_id: UUID | None = None


def _inspect(
    directory: Path, candidate: Candidate, session_id: UUID, adapter: BrowserAdapter
) -> ArticleObservation | None:
    for _ in range(sessions.FAILURE_LIMIT):
        try:
            observation = adapter.inspect(candidate)
        except ReadError as error:
            session = sessions.record_failure(
                directory,
                session_id,
                f"inspect:{candidate.url}",
                f"Article inspection failed: {error}",
                "Stop this worker and resume the same run in a new execution session",
            )
            if session.handoff_required:
                return None
        else:
            sessions.record_success(directory, session_id)
            return observation
    return None


def _perform(directory: Path, request: Request, adapter: BrowserAdapter) -> bool:
    attempt = store.reserve(directory, request)
    try:
        result = adapter.perform(request.action, request.content)
    except BrowserError as error:
        store.finish(
            directory,
            Receipt(
                attempt_id=attempt.id,
                outcome="uncertain",
                evidence=f"Dispatched operation failed: {error}. Do not resend.",
            ),
        )
        raise
    store.finish(
        directory,
        Receipt(
            attempt_id=attempt.id, outcome=result.outcome, evidence=result.evidence
        ),
    )
    return result.outcome == "confirmed"


def _reconcile(
    directory: Path, article: ArticleRecord, observation: ArticleObservation
) -> None:
    ledger = store.read(directory)
    aliases = article_urls(ledger, article.url)
    if observation.url not in aliases:
        message = "Resumed browser article does not match the recorded article"
        raise BrowserError(message)
    confirmed = {
        item.request.action
        for item in ledger.attempts
        if item.request.run_id == article.run_id
        and item.request.url in aliases
        and item.receipt
        and item.receipt.outcome == "confirmed"
    }
    expected = (
        article.own_comment or Action.COMMENT in confirmed,
        not article.unsubscribed or Action.SUBSCRIBE in confirmed,
        article.liked or Action.LIKE in confirmed,
    )
    actual = (observation.own_comment, not observation.unsubscribed, observation.liked)
    if (
        actual != expected
        or not observation.body_read
        or not observation.comments_checked
    ):
        message = (
            "Live reactions differ from receipts; reconcile without clicking again"
        )
        raise BrowserError(message)


def _process_article(
    directory: Path,
    plan: RunPlan,
    session_id: UUID,
    article: ArticleRecord,
    adapter: BrowserAdapter,
) -> None:
    run = next(item for item in store.read(directory).runs if item.id == article.run_id)
    content = ""
    for action in (Action.COMMENT, Action.SUBSCRIBE, Action.LIKE):
        if plan.targets.for_action(action) == 0:
            continue
        if (
            (action == Action.COMMENT and article.own_comment)
            or (action == Action.SUBSCRIBE and not article.unsubscribed)
            or (action == Action.LIKE and article.liked)
        ):
            continue
        ledger = store.read(directory)
        aliases = article_urls(ledger, article.url)
        prior = next(
            (
                item
                for item in ledger.attempts
                if item.request.run_id == run.id
                and item.request.url in aliases
                and item.request.action == action
            ),
            None,
        )
        if prior is not None:
            if prior.receipt is None or prior.receipt.outcome != "confirmed":
                break
            continue
        if plan.targets.loops is None:
            summary = summarize(ledger, run)
            counts = {
                Action.COMMENT: summary.confirmed.comments,
                Action.SUBSCRIBE: summary.confirmed.subscriptions,
                Action.LIKE: summary.confirmed.likes,
            }
            if counts[action] >= plan.targets.for_action(action):
                continue
        if action == Action.COMMENT:
            content = compose(
                adapter.draft_reaction(article), unsubscribed=article.unsubscribed
            )
        request = Request(
            connection=plan.connection,
            action=action,
            url=article.url,
            source=article.source,
            source_evidence=article.source_evidence,
            content=content if action == Action.COMMENT else "",
            run_id=run.id,
            session_id=session_id,
        )
        if not _perform(directory, request, adapter):
            break


def _finish_run(directory: Path, run: RunRecord, summary: RunSummary) -> RunSummary:
    targets = run.targets
    if targets is None:
        message = "Run targets are missing"
        raise store.BlockedError(message)
    done = _goals_met(summary, targets)
    store.finish_run(
        directory,
        RunReceipt(
            run_id=run.id,
            outcome=RunOutcome.COMPLETED if done else RunOutcome.EXHAUSTED,
            confirmed_comments=summary.confirmed.comments,
            confirmed_likes=summary.confirmed.likes,
            confirmed_subscriptions=summary.confirmed.subscriptions,
            termination=(
                RunTermination.TARGET_REACHED
                if done
                else RunTermination.CANDIDATES_EXHAUSTED
            ),
            stop_reason=(
                "Requested targets confirmed"
                if done
                else "All candidates in this approved selection have been inspected"
            ),
        ),
    )
    ledger = store.read(directory)
    return summarize(ledger, next(item for item in ledger.runs if item.id == run.id))


def execute(directory: Path, plan: RunPlan, adapter: BrowserAdapter) -> RunSummary:
    """Preserve the run ID across handoffs and uncertain dispatches."""
    if plan.connection.profile_name != "티스토리 확산봇":
        message = "Only the Tistory expansion Chrome profile is allowed"
        raise store.BlockedError(message)
    adapter.verify_connection(plan.connection)
    run = next(
        (item for item in store.read(directory).runs if item.id == plan.run_id), None
    )
    if plan.run_id is not None and (
        run is None or run.receipt is not None or run.targets != plan.targets
    ):
        message = "Resume the original open run with its original targets"
        raise store.BlockedError(message)
    if run is None:
        run = store.start_run(directory, plan.slot, plan.targets)
    session = sessions.start_session(directory, run.id, plan.owner)
    handoff = False
    error_reason = ""
    try:
        for candidate in plan.candidates:
            ledger = store.read(directory)
            if _goals_met(summarize(ledger, run), plan.targets):
                break
            aliases = article_urls(ledger, candidate.url)
            existing = next(
                (
                    item
                    for item in ledger.articles
                    if item.run_id == run.id and item.url in aliases
                ),
                None,
            )
            if existing is None:
                visited = store.visited_today(directory, candidate.url)
                own_unfinished_visit = any(
                    item.run_id == run.id and item.url in aliases and not item.reason
                    for item in ledger.visits
                ) and not any(item.request.url in aliases for item in ledger.attempts)
                if visited and not own_unfinished_visit:
                    continue
                if not visited:
                    _ = store.mark_visit(directory, candidate.url, run.id)
            observation = _inspect(directory, candidate, session.id, adapter)
            if observation is None:
                handoff = True
                break
            if existing is not None:
                _reconcile(directory, existing, observation)
            article = existing or store.record_article(
                directory, run.id, session.id, observation
            )
            if not article.skip_reason:
                _process_article(directory, plan, session.id, article, adapter)
    except (
        BrowserError,
        store.BlockedError,
        ValidationError,
        PydanticCustomError,
    ) as error:
        error_reason = str(error)
    if not handoff:
        sessions.release_session(
            directory, session.id, error_reason or "Candidate processing stopped"
        )
    summary = summarize(store.read(directory), run)
    if not handoff and not error_reason and summary.pending == 0:
        summary = _finish_run(directory, run, summary)
    _ = write_report(directory, summary)
    return summary


def _goals_met(summary: RunSummary, targets: RunTargets) -> bool:
    if targets.loops is not None:
        return summary.complete_loops >= targets.loops
    return (
        summary.confirmed.comments >= targets.comments
        and summary.confirmed.likes >= targets.likes
        and summary.confirmed.subscriptions >= targets.subscriptions
    )
