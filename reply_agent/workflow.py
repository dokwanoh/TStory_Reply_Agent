"""Additive workflow records; old action history retains its original rules."""

from datetime import datetime
from typing import ClassVar
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


class WorkflowModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class ArticleObservation(WorkflowModel):
    """Visible facts collected before the first action on an article."""

    url: str
    aliases: tuple[str, ...] = ()
    title: str = Field(min_length=1)
    source: str = Field(pattern=r"^(popular|feed|search)$")
    source_evidence: str = Field(min_length=10)
    unsubscribed: bool
    own_comment: bool
    liked: bool
    body_read: bool
    comments_checked: bool
    evidence: str = Field(min_length=10)
    skip_reason: str | None = None

    @model_validator(mode="after")
    def validate_urls(self) -> "ArticleObservation":
        """Keep aliases explicit and free of credentials and query strings."""
        for url in (self.url, *self.aliases):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or not parsed.path.strip("/")
                or parsed.username
                or parsed.password
                or parsed.port
                or parsed.query
                or parsed.fragment
                or url.endswith("/")
            ):
                msg = "article_url"
                raise PydanticCustomError(msg, "Use canonical article URLs")
        return self


class ArticleRecord(ArticleObservation):
    run_id: UUID
    observed_at: datetime


class ExecutionSession(WorkflowModel):
    """Durable owner and failure streak for one browser execution context."""

    id: UUID
    run_id: UUID
    owner: str = Field(min_length=1)
    started_at: datetime
    released_at: datetime | None = None
    handoff_required: bool = False
    operation: str = ""
    consecutive_failures: int = Field(default=0, ge=0)
    last_error: str = ""
    next_action: str = ""


class FailureEvent(WorkflowModel):
    session_id: UUID
    operation: str = Field(min_length=1)
    reason: str = Field(min_length=10)
    next_action: str = Field(min_length=10)
    occurred_at: datetime


class ActionTotals(WorkflowModel):
    comments: int = 0
    likes: int = 0
    subscriptions: int = 0


class ArticleSummary(WorkflowModel):
    url: str
    title: str
    comment: str
    subscribe: str
    like: str
    comment_text: str = ""
    evidence: tuple[str, ...] = ()
    complete: bool = False
    reason: str = ""


class RunSummary(WorkflowModel):
    run_id: UUID
    outcome: str
    target_loops: int | None
    complete_loops: int
    remaining_loops: int | None
    confirmed: ActionTotals
    uncertain: int
    not_submitted: int
    pending: int
    partial_articles: int
    skipped_articles: int
    active_sessions: int
    handoff_required: bool
    last_operation: str = ""
    consecutive_failures: int = 0
    last_error: str = ""
    next_action: str = ""
    stop_reason: str
    articles: tuple[ArticleSummary, ...]
