"""Run with uv run python -m reply_agent; this CLI does not drive a browser."""

from pathlib import Path
from typing import Annotated, Final

import typer
from pydantic import ValidationError

from . import store
from .models import Receipt, Request

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
    """Save visible confirmation or an uncertain outcome."""
    store.finish(state, Receipt.model_validate_json(receipt_file.read_text()))
    typer.echo("Recorded")


@APP.command()
def status(state: StateOption = DEFAULT_STATE) -> None:
    """Print private action history. Never publish this output to Git."""
    typer.echo(store.read(state).model_dump_json(indent=2))


def main() -> None:
    """Turn expected boundary failures into concise CLI errors."""
    try:
        APP()
    except (store.BlockedError, ValidationError, OSError) as error:
        typer.echo(f"Blocked: {error}", err=True)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
