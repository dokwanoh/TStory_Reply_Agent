"""Private ledger commands; browser actions remain in the connected browser tool."""

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Final
from uuid import UUID

import typer
from pydantic import ValidationError
from pydantic_core import PydanticCustomError

from . import sessions, store
from .comments import compose
from .models import Receipt, Request, RunReceipt, RunTargets
from .reporting import summarize, write_report
from .workflow import ArticleObservation

APP: Final = typer.Typer(pretty_exceptions_enable=False)
DEFAULT_STATE: Final = Path(__file__).resolve().parents[1] / ".local" / "state"
StateOption = Annotated[Path, typer.Option(help="Private state directory")]


@APP.command()
def reserve(request_file: Path, state: StateOption = DEFAULT_STATE) -> None:
    """Persist a reservation before performing exactly one browser action."""
    request = Request.model_validate_json(request_file.read_text())
    typer.echo(store.reserve(state, request).model_dump_json(indent=2))


@APP.command()
def finish(receipt_file: Path, state: StateOption = DEFAULT_STATE) -> None:
    """Save confirmation, uncertainty, or evidence of no submission."""
    store.finish(state, Receipt.model_validate_json(receipt_file.read_text()))
    typer.echo("Recorded")


@APP.command("run-start")
def run_start(
    slot: datetime, targets_file: Path, state: StateOption = DEFAULT_STATE
) -> None:
    """Start one request with explicit action and optional three-action loop goals."""
    targets = RunTargets.model_validate_json(targets_file.read_text())
    typer.echo(store.start_run(state, slot, targets).model_dump_json(indent=2))


@APP.command("run-finish")
def run_finish(receipt_file: Path, state: StateOption = DEFAULT_STATE) -> None:
    """Close a run and automatically save its Markdown and JSON summary."""
    store.finish_run(state, RunReceipt.model_validate_json(receipt_file.read_text()))
    typer.echo("Run recorded; private summary saved")


@APP.command()
def visit(url: str, run_id: UUID, state: StateOption = DEFAULT_STATE) -> None:
    """Record a candidate before navigating to it."""
    typer.echo(store.mark_visit(state, url, run_id).model_dump_json(indent=2))


@APP.command("visit-note")
def visit_note(
    url: str, run_id: UUID, reason: str, state: StateOption = DEFAULT_STATE
) -> None:
    """Attach an exclusion or access failure to an existing visit."""
    store.note_visit(state, url, run_id, reason)
    typer.echo("Visit reason recorded")


@APP.command("article-observe")
def article_observe(
    run_id: UUID,
    session_id: UUID,
    observation_file: Path,
    state: StateOption = DEFAULT_STATE,
) -> None:
    """Freeze initial article facts and explicit aliases before any action."""
    observation = ArticleObservation.model_validate_json(observation_file.read_text())
    typer.echo(
        store.record_article(state, run_id, session_id, observation).model_dump_json(
            indent=2
        )
    )


@APP.command()
def comment(
    body: str,
    unsubscribed: Annotated[bool, typer.Option("--unsubscribed/--subscribed")],
) -> None:
    """Validate one reaction sentence and add the conditional greeting."""
    typer.echo(compose(body, unsubscribed=unsubscribed))


@APP.command("session-start")
def session_start(run_id: UUID, owner: str, state: StateOption = DEFAULT_STATE) -> None:
    """Claim exclusive execution ownership for an existing open run."""
    typer.echo(sessions.start_session(state, run_id, owner).model_dump_json(indent=2))


@APP.command("session-release")
def session_release(
    session_id: UUID, reason: str, state: StateOption = DEFAULT_STATE
) -> None:
    """Record that the corresponding browser worker has stopped."""
    sessions.release_session(state, session_id, reason)
    typer.echo("Execution session released")


@APP.command("session-failure")
def session_failure(
    session_id: UUID,
    operation: str,
    reason: str,
    next_action: str,
    state: StateOption = DEFAULT_STATE,
) -> None:
    """Count a failed read or a proven pre-dispatch failure."""
    typer.echo(
        sessions.record_failure(
            state, session_id, operation, reason, next_action
        ).model_dump_json(indent=2)
    )


@APP.command("session-success")
def session_success(session_id: UUID, state: StateOption = DEFAULT_STATE) -> None:
    """Reset the consecutive failure count after observed success."""
    sessions.record_success(state, session_id)
    typer.echo("Failure streak reset")


@APP.command()
def report(run_id: UUID, state: StateOption = DEFAULT_STATE) -> None:
    """Save a request-scoped private report without rewriting the ledger."""
    ledger = store.read(state)
    run = next((item for item in ledger.runs if item.id == run_id), None)
    if run is None:
        message = "Unknown run ID"
        raise store.BlockedError(message)
    typer.echo(write_report(state, summarize(ledger, run)))


@APP.command()
def status(
    *,
    state: StateOption = DEFAULT_STATE,
    run_id: Annotated[UUID | None, typer.Option()] = None,
    details: Annotated[bool, typer.Option()] = False,
    raw: Annotated[bool, typer.Option(help="Print the private full ledger")] = False,
) -> None:
    """Show current request totals and global pending work; details include articles."""
    ledger = store.read(state)
    if raw:
        typer.echo(ledger.model_dump_json(indent=2))
        return
    selected = (
        [item for item in ledger.runs if item.id == run_id]
        if run_id
        else [item for item in ledger.runs if item.receipt is None]
        or list(ledger.runs[-1:])
    )
    if run_id is not None and not selected:
        message = "Unknown run ID"
        raise store.BlockedError(message)
    typer.echo(
        json.dumps(
            {
                "open_runs": sum(item.receipt is None for item in ledger.runs),
                "pending_actions": sum(
                    item.receipt is None for item in ledger.attempts
                ),
                "active_sessions": sum(
                    item.released_at is None for item in ledger.sessions
                ),
                "runs": [
                    summarize(ledger, run).model_dump(
                        mode="json", exclude=set() if details else {"articles"}
                    )
                    for run in selected
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    """Turn expected file-boundary and policy failures into concise CLI errors."""
    try:
        APP()
    except (store.BlockedError, ValidationError, PydanticCustomError, OSError) as error:
        typer.echo(f"Blocked: {error}", err=True)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
