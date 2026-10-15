"""Reservation policy applies to new actions while preserving historical records."""

from typing import assert_never
from urllib.parse import urlsplit

from .comments import validate_comment
from .identity import article_urls
from .models import Action, Ledger, Request, RunRecord
from .sessions import require_session
from .storage import BlockedError
from .workflow import ArticleRecord


def _article(ledger: Ledger, run: RunRecord, request: Request) -> ArticleRecord:
    if request.connection.profile_name != "티스토리 확산봇":
        message = "Only the Tistory expansion Chrome profile is allowed"
        raise BlockedError(message)
    article = next(
        (
            item
            for item in ledger.articles
            if item.run_id == run.id and item.url == request.url
        ),
        None,
    )
    if article is None or not article.body_read or not article.comments_checked:
        message = "Record the article body and complete comment inspection first"
        raise BlockedError(message)
    if article.skip_reason:
        message = "This candidate was excluded: " + article.skip_reason
        raise BlockedError(message)
    own_host = urlsplit(request.connection.account_blog).netloc
    if any(
        urlsplit(url).netloc == own_host for url in article_urls(ledger, article.url)
    ):
        message = "Own blog aliases are excluded"
        raise BlockedError(message)
    if run.targets is None:
        message = "Run targets are missing"
        raise BlockedError(message)
    if run.targets.unsubscribed_only and not article.unsubscribed:
        message = "This request requires an unsubscribed blog"
        raise BlockedError(message)
    if run.targets.source and run.targets.source != article.source:
        message = "Candidate source does not match the requested source"
        raise BlockedError(message)
    if (request.source, request.source_evidence) != (
        article.source,
        article.source_evidence,
    ):
        message = "Use the recorded discovery source and evidence"
        raise BlockedError(message)
    return article


def enforce(ledger: Ledger, run: RunRecord, request: Request) -> None:
    """Require the exclusive owner and confirmed predecessors for each new action."""
    _ = require_session(ledger, run.id, request.session_id)
    article = _article(ledger, run, request)
    confirmed = {
        item.request.action
        for item in ledger.attempts
        if item.request.run_id == run.id
        and item.request.url == request.url
        and item.receipt
        and item.receipt.outcome == "confirmed"
    }
    targets = run.targets
    if targets is None:
        message = "Run targets are missing"
        raise BlockedError(message)
    needs_comment = targets.comments > 0 and not article.own_comment
    needs_subscription = targets.subscriptions > 0 and article.unsubscribed
    if (
        request.action != Action.COMMENT
        and needs_comment
        and Action.COMMENT not in confirmed
    ):
        message = "Confirm the comment before subscribing or liking"
        raise BlockedError(message)
    match request.action:
        case Action.COMMENT:
            if article.own_comment:
                message = "An own comment already exists"
                raise BlockedError(message)
            validate_comment(request.content, unsubscribed=article.unsubscribed)
        case Action.SUBSCRIBE:
            if not article.unsubscribed:
                message = "The blog is already subscribed"
                raise BlockedError(message)
        case Action.LIKE:
            if article.liked:
                message = "The like was already active"
                raise BlockedError(message)
            if needs_subscription and Action.SUBSCRIBE not in confirmed:
                message = "Confirm the subscription before liking"
                raise BlockedError(message)
        case unreachable:
            assert_never(unreachable)
