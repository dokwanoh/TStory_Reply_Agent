"""Validated action records at the file boundary."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Final, Self, assert_never
from urllib.parse import unquote, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

MIN_COMMENT: Final = 20
MAX_COMMENT: Final = 400
RUN_TARGET: Final = 10


class Action(StrEnum):
    COMMENT = "comment"
    LIKE = "like"
    SUBSCRIBE = "subscribe"


class RunOutcome(StrEnum):
    """Terminal state for one scheduled browser run."""

    COMPLETED = "completed"
    EXHAUSTED = "exhausted"
    BLOCKED = "blocked"


class RunTermination(StrEnum):
    """Machine-readable reason for closing a scheduled run."""

    TARGET_REACHED = "target_reached"
    CANDIDATES_EXHAUSTED = "candidates_exhausted"
    DAILY_LIMIT = "daily_limit"
    RESERVE_UNAVAILABLE = "reserve_unavailable"
    PROFILE_MISMATCH = "profile_mismatch"
    BROWSER_DISCONNECTED = "browser_disconnected"
    CAPTCHA_OR_BLOCK = "captcha_or_block"
    TOOL_ERROR = "tool_error"


class FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class Connection(FrozenModel):
    profile_name: str = Field(min_length=1)
    extension_id: UUID
    account_blog: str = Field(pattern=r"^https://[a-z0-9-]+\.tistory\.com/$")


class Request(FrozenModel):
    """Observed article plus the intended action, before any click."""

    connection: Connection
    action: Action
    url: str = Field(pattern=r"^https://[^/?#]+/.+")
    source: str = Field(pattern=r"^(popular|feed)$")
    source_evidence: str = Field(min_length=10)
    content: str = ""
    run_id: UUID | None = None

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        """Reject unsafe targets and malformed comment payloads."""
        parsed = urlsplit(self.url)
        if parsed.username or parsed.password or parsed.port:
            raise PydanticCustomError("target", "URL credentials/ports are forbidden")
        if parsed.query or parsed.fragment or self.url.endswith("/"):
            raise PydanticCustomError("target", "Use the canonical article URL")
        if self.url.startswith(self.connection.account_blog):
            raise PydanticCustomError("target", "Own blog is excluded")
        match self.action:
            case Action.COMMENT:
                if not MIN_COMMENT <= len(self.content.strip()) <= MAX_COMMENT:
                    raise PydanticCustomError("comment", "Comment must be 20-400 chars")
                if "http" in self.content or "맞구독" in self.content:
                    raise PydanticCustomError(
                        "comment", "Promotional links are excluded"
                    )
            case Action.LIKE | Action.SUBSCRIBE:
                if self.content:
                    raise PydanticCustomError("content", "Only comments have content")
            case unreachable:
                assert_never(unreachable)
        return self

    @property
    def blog(self) -> str:
        """Blog key from the verified canonical article URL."""
        return urlsplit(self.url).netloc.lower()

    @property
    def key(self) -> str:
        """Subscription scope is a blog; other actions use the canonical post."""
        match self.action:
            case Action.SUBSCRIBE:
                return f"{self.action}:{self.blog}"
            case Action.COMMENT | Action.LIKE:
                return f"{self.action}:{self.blog}{unquote(urlsplit(self.url).path)}"
            case unreachable:
                assert_never(unreachable)


class Receipt(FrozenModel):
    """Visible result used to close an attempted action."""

    attempt_id: UUID
    outcome: str = Field(pattern=r"^(confirmed|uncertain)$")
    evidence: str = Field(min_length=10)


class Attempt(FrozenModel):
    """A persisted reservation cannot be silently retried."""

    id: UUID
    created_at: datetime
    request: Request
    receipt: Receipt | None = None


class Ledger(FrozenModel):
    version: int = Field(default=1, ge=1, le=1)
    attempts: tuple[Attempt, ...] = ()
    runs: tuple["RunRecord", ...] = ()


class RunRecord(FrozenModel):
    """One claimed scheduler slot, closed only with a durable receipt."""

    id: UUID
    slot: datetime
    started_at: datetime
    receipt: "RunReceipt | None" = None


class RunReceipt(FrozenModel):
    """Required end-of-run evidence that prevents silent early exits."""

    run_id: UUID
    outcome: RunOutcome
    confirmed_comments: int = Field(ge=0, le=10)
    confirmed_likes: int = Field(ge=0, le=10)
    confirmed_subscriptions: int = Field(ge=0, le=10)
    stop_reason: str = Field(min_length=10)
    termination: RunTermination | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        """Require all targets unless the run explicitly records exhaustion."""
        if self.outcome is RunOutcome.COMPLETED and (
            self.termination is not RunTermination.TARGET_REACHED
            or min(
                self.confirmed_comments,
                self.confirmed_likes,
                self.confirmed_subscriptions,
            ) < RUN_TARGET
        ):
            raise PydanticCustomError(
                "run_receipt",
                "completed runs must reach all three targets",
            )
        return self
