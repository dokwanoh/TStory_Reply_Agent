"""Canonical aliases observed in the browser, never guessed URL variants."""

from typing import assert_never
from urllib.parse import unquote, urlsplit

from .models import Action, Ledger, Request


def article_urls(ledger: Ledger, url: str) -> frozenset[str]:
    """Expand the explicit alias graph for one article."""
    urls = {url}
    while True:
        expanded = set(urls)
        for article in ledger.articles:
            aliases = {article.url, *article.aliases}
            if aliases & urls:
                expanded.update(aliases)
        if expanded == urls:
            return frozenset(urls)
        urls = expanded


def action_keys(ledger: Ledger, request: Request) -> frozenset[str]:
    """Preserve legacy keys while joining verified custom-domain aliases."""
    urls = article_urls(ledger, request.url)
    match request.action:
        case Action.COMMENT | Action.LIKE:
            return frozenset(
                f"{request.action}:{urlsplit(url).netloc.lower()}{unquote(urlsplit(url).path)}"
                for url in urls
            )
        case Action.SUBSCRIBE:
            hosts = {urlsplit(url).netloc.lower() for url in urls}
            while True:
                expanded = set(hosts)
                for article in ledger.articles:
                    aliases = {
                        urlsplit(url).netloc.lower()
                        for url in (article.url, *article.aliases)
                    }
                    if aliases & hosts:
                        expanded.update(aliases)
                if expanded == hosts:
                    return frozenset(f"subscribe:{host}" for host in hosts)
                hosts = expanded
        case unreachable:
            assert_never(unreachable)
