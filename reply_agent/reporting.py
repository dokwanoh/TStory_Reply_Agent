"""Request-scoped accounting shared by completion gates and saved reports."""

from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

from .identity import article_urls
from .models import Action, Attempt, Ledger, RunRecord
from .workflow import ActionTotals, ArticleRecord, ArticleSummary, RunSummary


def _state(attempts: tuple[Attempt, ...], action: Action) -> str:
    matches = [item for item in attempts if item.request.action == action]
    if not matches:
        return "not_attempted"
    if any(
        item.receipt is not None and item.receipt.outcome == "confirmed"
        for item in matches
    ):
        return "confirmed"
    if any(item.receipt is None for item in matches):
        return "pending"
    return matches[-1].receipt.outcome if matches[-1].receipt else "pending"


def _eligible(observed: ArticleRecord | None, run: RunRecord) -> bool:
    if observed is None or observed.skip_reason:
        return False
    targets = run.targets
    if targets is None:
        return False
    return (not targets.unsubscribed_only or observed.unsubscribed) and (
        targets.source is None or targets.source == observed.source
    )


def _articles(ledger: Ledger, run: RunRecord) -> tuple[ArticleSummary, ...]:
    attempts = tuple(item for item in ledger.attempts if item.request.run_id == run.id)
    records = tuple(item for item in ledger.articles if item.run_id == run.id)
    visits = tuple(item for item in ledger.visits if item.run_id == run.id)
    urls = dict.fromkeys(
        [item.url for item in records]
        + [item.request.url for item in attempts]
        + [item.url for item in visits]
    )
    summaries: list[ArticleSummary] = []
    seen: set[str] = set()
    complete_blogs: set[str] = set()
    for url in urls:
        aliases = article_urls(ledger, url)
        if aliases & seen:
            continue
        seen.update(aliases)
        observed = next((item for item in records if item.url in aliases), None)
        related = tuple(item for item in attempts if item.request.url in aliases)
        states = tuple(_state(related, action) for action in Action)
        hosts = {urlsplit(alias).netloc.lower() for alias in aliases}
        complete = (
            _eligible(observed, run)
            and all(state == "confirmed" for state in states)
            and not hosts & complete_blogs
        )
        if complete:
            complete_blogs.update(hosts)
        visit_reason = next(
            (item.reason for item in visits if item.url in aliases and item.reason), ""
        )
        reason = (
            observed.skip_reason or ""
            if observed
            else visit_reason or "Initial article observation unavailable"
        )
        summaries.append(
            ArticleSummary(
                url=observed.url if observed else url,
                title=observed.title if observed else urlsplit(url).netloc,
                comment=states[0],
                like=states[1],
                subscribe=states[2],
                comment_text=next(
                    (
                        item.request.content
                        for item in related
                        if item.request.action == Action.COMMENT
                    ),
                    "",
                ),
                evidence=tuple(
                    item.receipt.evidence for item in related if item.receipt
                ),
                complete=complete,
                reason=reason,
            )
        )
    return tuple(summaries)


def summarize(ledger: Ledger, run: RunRecord) -> RunSummary:
    """Count three confirmed actions on one eligible article as one loop."""
    attempts = tuple(item for item in ledger.attempts if item.request.run_id == run.id)
    articles = _articles(ledger, run)
    counts = tuple(
        sum(
            item.request.action == action
            and item.receipt is not None
            and item.receipt.outcome == "confirmed"
            for item in attempts
        )
        for action in Action
    )
    loops = run.targets.loops if run.targets else None
    completed = sum(item.complete for item in articles)
    sessions = tuple(item for item in ledger.sessions if item.run_id == run.id)
    last = sessions[-1] if sessions else None
    return RunSummary(
        run_id=run.id,
        outcome=run.receipt.outcome if run.receipt else "open",
        target_loops=loops,
        complete_loops=completed,
        remaining_loops=max(0, loops - completed) if loops is not None else None,
        confirmed=ActionTotals(
            comments=counts[0], likes=counts[1], subscriptions=counts[2]
        ),
        uncertain=sum(
            item.receipt is not None and item.receipt.outcome == "uncertain"
            for item in attempts
        ),
        not_submitted=sum(
            item.receipt is not None and item.receipt.outcome == "not_submitted"
            for item in attempts
        ),
        pending=sum(item.receipt is None for item in attempts),
        partial_articles=sum(
            not item.complete
            and "confirmed" in (item.comment, item.subscribe, item.like)
            for item in articles
        ),
        skipped_articles=sum(
            bool(item.reason)
            and item.reason != "Initial article observation unavailable"
            and all(
                state == "not_attempted"
                for state in (item.comment, item.subscribe, item.like)
            )
            for item in articles
        ),
        active_sessions=sum(item.released_at is None for item in sessions),
        handoff_required=last.handoff_required if last else False,
        last_operation=last.operation if last else "",
        consecutive_failures=last.consecutive_failures if last else 0,
        last_error=last.last_error if last else "",
        next_action=last.next_action if last else "",
        stop_reason=run.receipt.stop_reason if run.receipt else "",
        articles=articles,
    )


def _article_lines(article: ArticleSummary) -> list[str]:
    lines = [
        f"## {article.title.replace(chr(10), ' ')}",
        "",
        f"[글 열기]({quote(article.url, safe=':/%~-._')})",
        "",
        f"댓글: {article.comment} / 구독: {article.subscribe} / 공감: {article.like}",
        f"3종 완료: {'예' if article.complete else '아니오'}",
        "",
    ]
    if article.comment_text:
        label = (
            "등록 확인 댓글:"
            if article.comment == "confirmed"
            else "입력/시도문 (등록 확인 아님):"
        )
        lines.extend([label, "", "> " + article.comment_text.replace("\n", "\n> "), ""])
    if article.reason:
        lines.extend([f"사유: {article.reason}", ""])
    lines.extend(f"- 확인 근거: {evidence}" for evidence in article.evidence)
    lines.append("")
    return lines


def write_report(directory: Path, summary: RunSummary) -> Path:
    """Save private Markdown and JSON summaries without compacting the ledger."""
    destination = directory / "reports"
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = summary.target_loops
    confirmed = summary.confirmed
    target_label = target if target is not None else "바퀴 목표 없음"
    counts = " · ".join(
        (
            f"댓글 {confirmed.comments}",
            f"공감 {confirmed.likes}",
            f"신규 구독 {confirmed.subscriptions}",
        )
    )
    exceptions = " · ".join(
        (
            f"부분 완료 {summary.partial_articles}",
            f"미확인 {summary.uncertain}",
            f"미제출 {summary.not_submitted}",
            f"건너뜀 {summary.skipped_articles}",
        )
    )
    lines = [
        "# 티스토리 확산 원장 요약",
        "",
        f"- 작업 ID: `{summary.run_id}`",
        f"- 실행 상태: {summary.outcome}",
        f"- 3종 완료: {summary.complete_loops} / {target_label}",
        f"- 확정: {counts}",
        f"- {exceptions}",
        f"- 남은 바퀴: {summary.remaining_loops}",
        f"- 미처리 예약 {summary.pending} · 활성 작업 세션 {summary.active_sessions}",
        f"- 새 작업기 인계 필요: {summary.handoff_required}",
        f"- 마지막 실패 작업: {summary.last_operation}",
        f"- 연속 실패: {summary.consecutive_failures}",
        f"- 마지막 오류: {summary.last_error}",
        f"- 다음 행동: {summary.next_action}",
        f"- 종료 사유: {summary.stop_reason or '진행 중'}",
        "",
        "과거 회차와 미확인·미제출을 확정 수에 합산하지 않았습니다.",
        "초기 관찰이 없는 과거 기록의 3종 완료 수는 재구성하지 않습니다.",
        "",
    ]
    for article in summary.articles:
        lines.extend(_article_lines(article))
    path = destination / f"{summary.run_id}.md"
    outputs = (
        (path, "\n".join(lines)),
        (path.with_suffix(".json"), summary.model_dump_json(indent=2)),
    )
    for target_path, content in outputs:
        temporary = target_path.with_suffix(f".{uuid4()}.tmp")
        _ = temporary.write_text(content, encoding="utf-8")
        temporary.chmod(0o600)
        _ = temporary.replace(target_path)
    return path
