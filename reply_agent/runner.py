"""One complete, fail-closed outreach cycle."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from . import store
from .models import (
    RUN_TARGET,
    Action,
    Candidate,
    Connection,
    Receipt,
    Request,
    RunOutcome,
    RunReceipt,
    RunTermination,
)

GREETING = "앞으로 친하게 지네요~!"


class BrowserError(Exception):
    """Base class for failures that must close a run without retrying."""


class ProfileMismatchError(BrowserError):
    """The connected browser is not the configured outreach profile."""


class BrowserDisconnectedError(BrowserError):
    """The approved browser connection disappeared during a cycle."""


@dataclass(frozen=True, slots=True)
class PageState:
    """Facts read from the opened article before any action."""

    unsubscribed: bool
    own_comment: bool


@dataclass(frozen=True, slots=True)
class Observation:
    """DOM-backed result of one browser action."""

    outcome: str
    evidence: str


class BrowserAdapter(Protocol):
    """The only browser capability accepted by the runner."""

    def verify_connection(self, connection: Connection) -> None:
        """Verify profile name, extension identity, and account blog."""
        ...

    def open(self, url: str) -> PageState:
        """Open an already-recorded canonical article URL."""
        ...

    def perform(self, action: Action, content: str) -> Observation:
        """Perform one action and return only DOM-backed evidence."""
        ...


def _request(
    candidate: Candidate,
    connection: Connection,
    action: Action,
    run_id: UUID,
    content: str = "",
) -> Request:
    return Request(
        connection=connection,
        action=action,
        url=candidate.url,
        source=candidate.source,
        source_evidence=candidate.source_evidence,
        content=content,
        run_id=run_id,
    )


def _comment(state: PageState) -> str:
    if state.own_comment:
        return ""
    suffix = f" {GREETING}" if state.unsubscribed else ""
    return f"글 잘 읽었습니다. 유익한 내용 공유해주셔서 감사합니다.{suffix}"


@dataclass(frozen=True, slots=True)
class ActionContext:
    directory: Path
    candidate: Candidate
    connection: Connection
    run_id: UUID
    adapter: BrowserAdapter


def _record_action(context: ActionContext, action: Action, content: str) -> bool:
    directory = context.directory
    candidate = context.candidate
    connection = context.connection
    run_id = context.run_id
    request = _request(candidate, connection, action, run_id, content)
    attempt = store.reserve(directory, request)
    observation = context.adapter.perform(action, content)
    store.finish(
        directory,
        Receipt(
            attempt_id=attempt.id,
            outcome=observation.outcome,
            evidence=observation.evidence,
        ),
    )
    return observation.outcome == "confirmed"


def execute(
    directory: Path,
    connection: Connection,
    slot: datetime,
    candidates: list[Candidate],
    adapter: BrowserAdapter,
) -> RunReceipt:
    """Run candidates until all targets, exhaustion, or a hard stop is proven."""
    run = store.start_run(directory, slot)
    counts = {Action.COMMENT: 0, Action.LIKE: 0, Action.SUBSCRIBE: 0}
    termination = RunTermination.CANDIDATES_EXHAUSTED
    outcome = RunOutcome.EXHAUSTED
    reason = "All approved candidates were visited without reaching every target"
    try:
        adapter.verify_connection(connection)
        for candidate in candidates:
            if min(counts.values()) >= RUN_TARGET:
                termination, outcome, reason = (
                    RunTermination.TARGET_REACHED,
                    RunOutcome.COMPLETED,
                    "All three action targets reached",
                )
                break
            if store.visited_today(directory, candidate.url):
                continue
            _ = store.mark_visit(directory, candidate.url)
            page = adapter.open(candidate.url)
            actions = (
                (Action.SUBSCRIBE, ""),
                (Action.LIKE, ""),
                (Action.COMMENT, _comment(page)),
            )
            for action, content in actions:
                if counts[action] >= RUN_TARGET or (
                    action is Action.COMMENT and not content
                ):
                    continue
                context = ActionContext(
                    directory, candidate, connection, run.id, adapter
                )
                if _record_action(context, action, content):
                    counts[action] += 1
        else:
            if min(counts.values()) >= RUN_TARGET:
                termination, outcome, reason = (
                    RunTermination.TARGET_REACHED,
                    RunOutcome.COMPLETED,
                    "All three action targets reached",
                )
            else:
                termination, outcome = (
                    RunTermination.CANDIDATES_EXHAUSTED,
                    RunOutcome.EXHAUSTED,
                )
    except ProfileMismatchError:
        termination, outcome, reason = (
            RunTermination.PROFILE_MISMATCH,
            RunOutcome.BLOCKED,
            "Connected browser profile does not match the configured outreach profile",
        )
    except BrowserDisconnectedError:
        termination, outcome, reason = (
            RunTermination.BROWSER_DISCONNECTED,
            RunOutcome.BLOCKED,
            "Approved browser connection disconnected",
        )
    except BrowserError as error:
        termination, outcome, reason = (
            RunTermination.TOOL_ERROR,
            RunOutcome.BLOCKED,
            str(error),
        )
    except store.BlockedError as error:
        if error.reason == "Daily action budget reached":
            termination, outcome, reason = (
                RunTermination.DAILY_LIMIT,
                RunOutcome.EXHAUSTED,
                error.reason,
            )
        else:
            termination, outcome, reason = (
                RunTermination.TOOL_ERROR,
                RunOutcome.BLOCKED,
                str(error),
            )
    receipt = RunReceipt(
        run_id=run.id,
        outcome=outcome,
        confirmed_comments=counts[Action.COMMENT],
        confirmed_likes=counts[Action.LIKE],
        confirmed_subscriptions=counts[Action.SUBSCRIBE],
        stop_reason=reason,
        termination=termination,
    )
    store.finish_run(directory, receipt)
    return receipt
