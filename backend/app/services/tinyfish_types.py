"""Plain data shared by the TinyFish client, the page parsers and the scraper (no network, no app imports)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, fields


@dataclass(frozen=True)
class SearchHit:
    position: int
    title: str
    snippet: str
    url: str
    site_name: str
    date: str | None = None  # free text from the search engine ("2 days ago", "Oct 5, 2026"): a hint, never parse it as ISO


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    title: str
    text: str
    format: str  # "markdown" | "html"
    links: list[str] = field(default_factory=list)
    latency_ms: float | None = None


@dataclass(frozen=True)
class FetchFailure:
    url: str
    code: str  # bot_blocked, empty_content, login_required, timeout, page_not_found, ...
    status: int | None = None


def _same_url(a: str, b: str) -> bool:
    return a == b or a.rstrip("/") == b.rstrip("/")


@dataclass
class FetchResult:
    pages: list[FetchedPage]
    failures: list[FetchFailure]

    def page_for(self, url: str) -> FetchedPage | None:
        """The page fetched for ``url`` (matched on the requested URL, then on where it ended up)."""
        for page in self.pages:
            if _same_url(page.url, url):
                return page
        for page in self.pages:
            if _same_url(page.final_url, url):
                return page
        return None

    def failure_for(self, url: str) -> FetchFailure | None:
        for failure in self.failures:
            if _same_url(failure.url, url):
                return failure
        return None


@dataclass(frozen=True)
class AgentEvent:
    type: str  # STARTED | STREAMING_URL | PROGRESS | HEARTBEAT | COMPLETE
    run_id: str | None = None
    purpose: str | None = None
    streaming_url: str | None = None
    status: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class AgentRunResult:
    run_id: str | None
    status: str  # COMPLETED | FAILED | CANCELLED | TIMEOUT (PENDING / RUNNING only while a run is still going)
    steps: int = 0
    result: dict | None = None
    error_code: str | None = None
    error_message: str | None = None
    streaming_url: str | None = None

    @property
    def ok(self) -> bool:
        """A finished run that produced a result object. COMPLETED alone doesn't mean the goal succeeded."""
        return self.status == "COMPLETED" and not self.error_code and isinstance(self.result, dict)


_USAGE_FIELDS = ("search_calls", "fetch_calls", "fetch_urls", "agent_runs", "agent_steps", "errors")


@dataclass
class UsageCounters:
    """What one scan / Finder run spent on TinyFish. Safe to share between threads."""

    search_calls: int = 0
    fetch_calls: int = 0
    fetch_urls: int = 0
    agent_runs: int = 0
    agent_steps: int = 0
    errors: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def add(self, **deltas: int) -> None:
        unknown = set(deltas) - set(_USAGE_FIELDS)
        if unknown:
            raise ValueError(f"Unknown usage counter(s): {', '.join(sorted(unknown))}")
        with self._lock:
            for name, delta in deltas.items():
                setattr(self, name, getattr(self, name) + int(delta))

    def as_dict(self) -> dict[str, int]:
        with self._lock:
            return {f.name: int(getattr(self, f.name)) for f in fields(self) if f.name in _USAGE_FIELDS}

    def endpoints_used(self) -> list[str]:
        used = self.as_dict()
        out = []
        if used["search_calls"]:
            out.append("search")
        if used["fetch_calls"] or used["fetch_urls"]:
            out.append("fetch")
        if used["agent_runs"]:
            out.append("agent")
        return out
