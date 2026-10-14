"""TinyFish source: Search finds postings and careers pages, Fetch reads them, the Agent handles the pages Fetch can't.

``discover`` is the pipeline (shared with the TinyFish Finder); ``search`` is the scan entry point that ranks and
filters what it found. The order of work, cheapest first:

1. Search: an ATS-domain pass, an open pass and one query per company.
2. Boards (Greenhouse / Lever / Ashby / Workday) go to the existing ATS scrapers; single ATS postings are read from the
   ATS's own free public JSON, so Fetch credits are spent only on pages that have no such API.
3. Fetch reads the other postings (schema.org JobPosting first) and careers pages (embedded boards, job links).
4. The Agent runs only on pages Fetch couldn't read, within a small per-scan budget (see ``discover``).

LinkedIn, Indeed, Glassdoor and Internshala are consent-gated and TinyFish is a third-party crawler, so a URL on those
hosts is never sent to it. Page text and Agent output are untrusted: tinyfish_parse re-checks every URL.

The TinyFish client and the ranker import ``app.scrapers.base``, so this module imports them inside functions: the
package ``app.scrapers`` imports this module, and a top-level import would be a cycle.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.models.enums import ATSPlatform
from app.scrapers.ashby import AshbyScraper
from app.scrapers.ats_detect import detect_ats_platform, parse_ats_url
from app.scrapers.base import BaseScraper, ScrapedJob, ScraperError, SearchQuery, parse_date
from app.scrapers.generic import _iter_jsonld, company_from_host, company_from_page, jobposting_to_job
from app.scrapers.greenhouse import GreenhouseScraper
from app.scrapers.lever import LeverScraper
from app.scrapers.tinyfish_parse import (
    _ATS_PLATFORMS,
    JOBS_OUTPUT_SCHEMA,
    UrlKind,
    agent_jobs_to_scraped,
    ats_boards_in_page,
    build_agent_goal,
    build_search_plans,
    canonical_url,
    classify_url,
    is_gated_url,
    job_links_from_page,
    looks_js_shell,
    parse_posting_html,
    parse_posting_markdown,
    tag_provenance,
)
from app.scrapers.workday import WorkdayScraper
from app.services.company_catalog import CATALOG
from app.services.text_utils import dedupe_key
from app.services.tinyfish_types import AgentEvent, FetchedPage, FetchFailure, FetchResult, SearchHit

if TYPE_CHECKING:
    from app.services.tinyfish_client import TinyFishClient

logger = logging.getLogger(__name__)

AGENT_MIN_SECONDS = 60  # no new Agent run with less than this left before the deadline
SEARCH_THREADS = 4
FETCH_THREADS = 3
FETCH_CHUNK = 10  # the client batches at 10 URLs per request; chunks let the stages report progress
LINKS_PER_PAGE = 15  # posting links followed from one careers page
BOARD_LIMIT = 150  # jobs read from one delegated board (the final filter trims the rest)
# Fetch errors that a real browser (the Agent) may get past; anything else (404, DNS...) is just dropped
AGENT_FETCH_CODES = frozenset({"empty_content", "bot_blocked", "login_required", "timeout"})
BILLING_CODES = frozenset({"BILLING_REJECTED", "INSUFFICIENT_CREDITS"})
SOURCE_RANK = {"ats_api": 0, "fetch": 1, "agent": 2, "snippet": 3}  # which copy of the same posting wins
STAGES = 4  # search, boards + reading, agent, tidy-up (the scan's progress bar)


# --------------------------------------------------------------------------- public types
@dataclass
class DiscoveryHooks:
    """What a caller (the Finder) wants to hear about while ``discover`` runs."""

    stage: Callable[[str, str, int, int], None] | None = None  # (stage "search|read|agent|rank", message, done, total)
    agent_event: Callable[[str, AgentEvent], None] | None = None  # (page url, event)
    should_cancel: Callable[[], bool] | None = None


@dataclass
class AgentRunInfo:
    url: str
    run_id: str | None
    status: str
    steps: int
    found: int
    streaming_url: str | None
    error: str | None


@dataclass
class DiscoveryResult:
    jobs: list[ScrapedJob]  # canonical, deduplicated, provenance-tagged, NOT yet filtered / ranked
    usage: dict[str, int]
    agent_runs: list[AgentRunInfo]
    counts: dict[str, int]  # raw_hits, gated_dropped, boards_delegated, pages_read, postings_parsed, agent_jobs, duplicates
    notes: list[str]  # human lines for the run log


def _new_counts() -> dict[str, int]:
    return dict.fromkeys(("raw_hits", "gated_dropped", "boards_delegated", "pages_read", "postings_parsed", "agent_jobs", "duplicates"), 0)


def catalog_gap_companies(limit: int = 3, today: datetime | None = None) -> list[str]:
    """Catalog companies that hire only through their own site (found on LinkedIn otherwise), a few per day, rotating."""
    gaps = [c.name for c in CATALOG if c.linkedin and not (c.greenhouse or c.lever or c.ashby or c.workday)]
    if not gaps:
        return []
    start = (today or datetime.now(UTC)).toordinal() % len(gaps)
    return [gaps[(start + i) % len(gaps)] for i in range(min(limit, len(gaps)))]


def _is_fatal(exc: Exception) -> bool:
    """A key / credit problem: every further TinyFish call would fail the same way."""
    from app.services import tinyfish_client as tfc

    return isinstance(exc, tfc.TinyFishAuthError | tfc.TinyFishQuotaError | tfc.TinyFishNotConfigured)


# --------------------------------------------------------------------------- ATS postings (free public JSON)
class _AtsReader:
    """One ATS posting through its board's public API; Ashby has no single-posting endpoint, so a board is loaded once."""

    def __init__(self, http: httpx.Client) -> None:
        self.http = http
        self._lock = threading.Lock()
        self._ashby: dict[str, list[ScrapedJob]] = {}

    def read(self, url: str) -> tuple[ScrapedJob | None, str]:
        """(job, "ok") | (None, "closed": the posting is gone) | (None, "error": unreadable here, try Fetch)."""
        ref = parse_ats_url(url)
        try:
            if ref.platform == ATSPlatform.ASHBY:
                job = self._ashby_job(ref.board, ref.job_id)
                return (job, "ok") if job else (None, "closed")
            scraper = {ATSPlatform.GREENHOUSE: GreenhouseScraper, ATSPlatform.LEVER: LeverScraper,
                       ATSPlatform.WORKDAY: WorkdayScraper}.get(ref.platform)
            job = scraper(self.http).fetch_job(url) if scraper else None
        except ScraperError as exc:
            # A Workday address without its location segment 404s although the posting is open: let Fetch try it
            closed = str(exc).startswith("Not found") and ref.platform != ATSPlatform.WORKDAY
            return None, "closed" if closed else "error"
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.debug("ATS posting %s unreadable: %s", url, exc)
            return None, "error"
        return (job, "ok") if job else (None, "error")

    def _ashby_job(self, board: str | None, job_id: str | None) -> ScrapedJob | None:
        if not board or not job_id:
            raise ScraperError("Not an Ashby posting address")
        with self._lock:
            if board not in self._ashby:
                self._ashby[board] = AshbyScraper(self.http).list_board(board)
            jobs = self._ashby[board]
        return next((j for j in jobs if j.external_id == job_id), None)


# --------------------------------------------------------------------------- one discover() call
@dataclass
class _Target:
    url: str  # canonical
    kind: UrlKind
    order: int
    hit: SearchHit | None = None
    query: str | None = None  # the Search query that found it
    seed: bool = False  # a career page the user gave (these come first)
    parent: str | None = None  # the careers page this posting link was taken from
    base_via: tuple[str, ...] = ()


@dataclass
class _AgentCandidate:
    target: _Target
    stealth: bool = False
    single_posting: bool = False


@dataclass
class _Found:
    job: ScrapedJob
    source: str  # key of SOURCE_RANK
    order: int


class _Discovery:
    def __init__(self, scraper: TinyFishScraper, client: TinyFishClient, query: SearchQuery, hooks: DiscoveryHooks,
                 agent_budget: int | None, include_catalog_gaps: bool, deadline: float | None) -> None:
        self.scraper = scraper
        self.client = client
        self.query = query
        self.hooks = hooks
        self.include_gaps = include_catalog_gaps
        deadlines = [d for d in (deadline, query.deadline) if d is not None]
        self.deadline = min(deadlines) if deadlines else None
        asked = agent_budget if agent_budget is not None else query.agent_runs
        try:
            budget = 2 if asked is None else int(asked)
        except (TypeError, ValueError):
            budget = 2
        self.agent_budget = max(0, min(budget, settings.TINYFISH_AGENT_MAX_RUNS_PER_SCAN))
        self.counts = _new_counts()
        self.notes: list[str] = []
        self.agent_infos: list[AgentRunInfo] = []
        self.targets: dict[str, _Target] = {}
        self._found: list[_Found] = []
        self._agent: dict[str, _AgentCandidate] = {}
        self._snippets: dict[str, _Target] = {}  # postings we have only a Search result for
        self._resolved: set[str] = set()  # target urls that produced a job
        self._delegated: dict[str, set[str]] = {}
        self._gated: set[str] = set()
        self._fetch_left = max(0, settings.TINYFISH_FETCH_URLS_PER_SCAN)
        self._halt: str | None = None  # set after a key / credit error: no more TinyFish calls
        self._ats = _AtsReader(scraper.client)
        self._usage_before = client.usage.as_dict()

    # ------------------------------------------------------------------ helpers
    def _remaining(self) -> float | None:
        return None if self.deadline is None else self.deadline - time.monotonic()

    def _stopped(self) -> bool:
        if self.query.time_up() or (self.deadline is not None and time.monotonic() > self.deadline):
            return True
        return bool(self.hooks.should_cancel and self.hooks.should_cancel())

    def _usable(self) -> bool:
        return self._halt is None and not self._stopped()

    def usage(self) -> dict[str, int]:
        now = self.client.usage.as_dict()
        return {k: v - self._usage_before.get(k, 0) for k, v in now.items()}

    def _stage(self, stage: str, message: str, done: int = 0, total: int = 0) -> None:
        self.query.note(message, usage=self.usage())
        if self.hooks.stage:
            try:
                self.hooks.stage(stage, message, done, total)
            except Exception:
                logger.debug("discovery stage callback failed", exc_info=True)

    def _drop_gated(self, url: str) -> None:
        self._gated.add(url)
        self.counts["gated_dropped"] = len(self._gated)  # distinct addresses, however many searches turned them up

    def _note(self, text: str) -> None:
        self.notes.append(text)
        logger.info("tinyfish: %s", text)

    def _fail(self, exc: Exception, what: str) -> None:
        self._note(f"{what}: {exc}")
        if _is_fatal(exc):
            self._halt = str(exc)

    def _add(self, job: ScrapedJob, source: str, t: _Target, *, run_id: str | None = None) -> None:
        via = [*t.base_via, *([] if source == "snippet" else [source])] or ["search"]
        tag_provenance(job, via=via, query=t.query, hit_url=t.hit.url if t.hit else (t.parent or t.url),
                       hit_title=t.hit.title if t.hit else None, agent_run_id=run_id)
        self._found.append(_Found(job, source, len(self._found)))
        self._resolved.add(t.url)

    def _target(self, url: str, kind: UrlKind, **kw: Any) -> _Target | None:
        if url in self.targets:
            return None
        t = _Target(url=url, kind=kind, order=len(self.targets), **kw)
        self.targets[url] = t
        return t

    # ------------------------------------------------------------------ pipeline
    def run(self) -> DiscoveryResult:
        self.query.report(0, STAGES)
        self._search()
        self.query.report(1, STAGES)
        self._boards([t for t in self.targets.values() if t.kind == UrlKind.ATS_BOARD])
        self._read_pages([t for t in self.targets.values() if t.kind == UrlKind.CAREERS_PAGE])  # (adds the job links it finds to the targets)
        postings = [t for t in self.targets.values() if t.kind in (UrlKind.POSTING, UrlKind.ATS_POSTING)]
        self._read_postings(sorted(postings, key=lambda t: (not t.seed, t.order)))
        self.query.report(2, STAGES)
        self._agent_stage()
        self.query.report(3, STAGES)
        jobs = self._tidy()
        self.query.report(4, STAGES)
        return DiscoveryResult(jobs=jobs, usage=self.usage(), agent_runs=self.agent_infos, counts=self.counts, notes=self.notes)

    # ------------------------------------------------------------------ 1. Search
    def _search(self) -> None:
        q = self.query
        for raw in q.sources.get("career_pages") or []:  # the user's own pages go first
            url = str(raw).strip()
            if not url.startswith(("http://", "https://")):
                continue
            if is_gated_url(url):
                self._drop_gated(url)
                continue
            canon = canonical_url(url)
            kind = classify_url(canon)
            self._target(canon, UrlKind.CAREERS_PAGE if kind == UrlKind.OTHER else kind, seed=True)
        plans = build_search_plans(q, settings.TINYFISH_SEARCH_QUERIES_PER_SCAN,
                                   catalog_gap_companies=catalog_gap_companies() if self.include_gaps else ())
        if not plans:
            self._note("Search: nothing to search for (no roles, skills or companies)")
            return
        self._stage("search", f"Searching with TinyFish Search ({len(plans)} queries)", 0, len(plans))

        def one(plan: Any) -> list[SearchHit]:
            if not self._usable():
                return []
            return self.client.search(plan.query, purpose=plan.purpose, location=plan.location,
                                      include_domains=plan.include_domains or None, exclude_domains=plan.exclude_domains or None,
                                      after_date=plan.after_date)

        results: dict[int, list[SearchHit]] = {}
        errors: list[Exception] = []
        with ThreadPoolExecutor(max_workers=min(SEARCH_THREADS, len(plans)), thread_name_prefix="tinyfish-search") as pool:
            futures = {pool.submit(one, plan): i for i, plan in enumerate(plans)}
            for done, future in enumerate(as_completed(futures), start=1):
                try:
                    results[futures[future]] = future.result()
                except ScraperError as exc:
                    errors.append(exc)
                self._stage("search", f"Searching with TinyFish Search ({len(plans)} queries)", done, len(plans))
        hits_total = sum(len(h) for h in results.values())
        fatal = next((e for e in errors if _is_fatal(e)), None)
        if fatal is not None:
            self._halt = str(fatal)
        if errors and not hits_total and (fatal is not None or not any(t.seed for t in self.targets.values())):
            raise fatal or errors[0]  # nothing came back and nothing else to read: the source failed
        for exc in errors:
            self._note(f"Search: {exc}")
        for i, plan in enumerate(plans):
            for hit in results.get(i, []):
                self.counts["raw_hits"] += 1
                if is_gated_url(hit.url):
                    self._drop_gated(hit.url)
                    continue
                url = canonical_url(hit.url)
                kind = classify_url(url)
                if kind in (UrlKind.GATED, UrlKind.OTHER):
                    continue
                self._target(url, kind, hit=hit, query=plan.query, base_via=("search",))
        self._note(f"Search: {len(plans)} queries, {hits_total} results, {len(self.targets)} candidate pages")

    # ------------------------------------------------------------------ 2. ATS boards
    def _boards(self, board_targets: list[_Target]) -> None:
        sites: dict[str, set[str]] = {"greenhouse_boards": set(), "lever_companies": set(), "ashby_boards": set(), "workday_sites": set()}
        for t in board_targets:
            ref = parse_ats_url(t.url)
            if ref.platform == ATSPlatform.GREENHOUSE and ref.board:
                sites["greenhouse_boards"].add(ref.board)
            elif ref.platform == ATSPlatform.LEVER and ref.board:
                sites["lever_companies"].add(ref.board)
            elif ref.platform == ATSPlatform.ASHBY and ref.board:
                sites["ashby_boards"].add(ref.board)
            elif ref.platform == ATSPlatform.WORKDAY and ref.host and ref.site:
                sites["workday_sites"].add(f"https://{ref.host}/{ref.site}")
        self._delegate(sites, ("search",), None)

    def _delegate(self, sites: dict[str, set[str]], base_via: tuple[str, ...], found_on: str | None) -> None:
        """Read whole boards with the existing ATS scrapers; they keep their own ``raw`` (the board_token company check needs it)."""
        for key, scraper_cls in (("greenhouse_boards", GreenhouseScraper), ("lever_companies", LeverScraper),
                                 ("ashby_boards", AshbyScraper), ("workday_sites", WorkdayScraper)):
            names = sorted(sites.get(key, set()) - self._delegated.setdefault(key, set()))
            if not names or self._stopped():  # (the ATS APIs are free and public: a TinyFish credit error doesn't stop them)
                continue
            self._delegated[key].update(names)
            self._stage("read", f"Reading {len(names)} job board{'s' if len(names) != 1 else ''} through their public API")
            sub = dataclasses.replace(self.query, sources={key: names}, progress=None, detail=None, deadline=self.deadline,
                                      limit=max(self.query.limit, BOARD_LIMIT))
            try:
                jobs = scraper_cls(self.scraper.client).search(sub)
            except ScraperError as exc:
                self._note(f"{key.split('_')[0].title()} boards skipped: {exc}")
                continue
            self.counts["boards_delegated"] += len(names)
            for job in jobs:
                tag_provenance(job, via=[*base_via, *(("fetch",) if found_on else ()), "ats_api"], hit_url=found_on)
                self._found.append(_Found(job, "ats_api", len(self._found)))

    # ------------------------------------------------------------------ 3. Fetch
    def _fetch(self, targets: list[_Target], fmt: Literal["markdown", "html"], links: bool, purpose: str,
               what: str) -> tuple[FetchResult, list[_Target]]:
        """Fetch what the budget allows (in chunks, a few at a time). Returns the result and the targets it covers."""
        room = self._fetch_left if self._usable() else 0
        take, left = targets[:room], targets[room:]
        merged = FetchResult(pages=[], failures=[])
        if left:
            self._note(f"{what}: {len(left)} page{'s' if len(left) != 1 else ''} left unread "
                       + ("(Fetch budget used up)" if self._usable() else "(out of time or stopped)"))
        if not take:
            return merged, []
        self._fetch_left -= len(take)
        chunks = [take[i:i + FETCH_CHUNK] for i in range(0, len(take), FETCH_CHUNK)]

        def one(chunk: list[_Target]) -> FetchResult | None:
            if not self._usable():
                return None
            return self.client.fetch([t.url for t in chunk], format=fmt, links=links, ttl=0, purpose=purpose)

        read = 0
        self._stage("read", f"{what} with TinyFish Fetch ({len(take)})", 0, len(take))
        with ThreadPoolExecutor(max_workers=min(FETCH_THREADS, len(chunks)), thread_name_prefix="tinyfish-fetch") as pool:
            futures = {pool.submit(one, c): c for c in chunks}
            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    res = future.result()
                except ScraperError as exc:
                    self._fail(exc, "Fetch")
                    res = None
                if res is None:  # not attempted, or the call failed
                    merged.failures.extend(FetchFailure(t.url, "not_fetched") for t in chunk)
                else:
                    merged.pages.extend(res.pages)
                    merged.failures.extend(res.failures)
                read += len(chunk)
                self._stage("read", f"{what} with TinyFish Fetch ({len(take)})", read, len(take))
        self.counts["pages_read"] += len(merged.pages)
        return merged, take

    def _want_agent(self, t: _Target, *, stealth: bool = False, single_posting: bool = False) -> None:
        cand = self._agent.get(t.url)
        if cand is None:
            self._agent[t.url] = _AgentCandidate(t, stealth, single_posting)
        else:
            cand.stealth = cand.stealth or stealth

    def _failed(self, t: _Target, failure: FetchFailure | None, *, single_posting: bool) -> None:
        code = failure.code if failure else "missing"
        if code in AGENT_FETCH_CODES:
            self._want_agent(t, stealth=code == "bot_blocked", single_posting=single_posting)
        if single_posting and t.hit is not None:
            self._snippets[t.url] = t  # kept from Search's title and snippet unless the Agent reads it after all

    def _read_pages(self, pages: list[_Target]) -> None:
        """Careers pages: embedded boards are delegated, matching job links become postings to read; the rest go to the Agent."""
        if not pages:
            return
        result, fetched = self._fetch(pages, "markdown", True, "Read a careers page to find its open roles", "Reading careers pages")
        boards: dict[tuple[str, ...], dict[str, set[str]]] = {}
        for t in fetched:
            page = result.page_for(t.url)
            if page is None:
                self._failed(t, result.failure_for(t.url), single_posting=False)
                continue
            found = ats_boards_in_page(page)
            if any(found.values()):
                merged = boards.setdefault(t.base_via, {k: set() for k in found})
                for key, names in found.items():
                    merged[key] |= names
                self._resolved.add(t.url)
                continue
            if looks_js_shell(page.text):
                self._want_agent(t)
                continue
            links = job_links_from_page(page, self.query, LINKS_PER_PAGE)
            for link in links:
                self._target(link, classify_url(link), query=t.query, seed=t.seed, parent=t.url, base_via=t.base_via)
            if links:
                self._resolved.add(t.url)
            else:
                self._want_agent(t)
        for base_via, sites in boards.items():
            self._delegate(sites, base_via, found_on="careers page")

    def _read_postings(self, postings: list[_Target]) -> None:
        """ATS postings through the ATS's public API, everything else (and what the API couldn't give) through Fetch."""
        postings = [t for t in postings if not self.query.is_known(t.url)]  # already saved: no need to read again
        need_fetch: dict[str, _Target] = {t.url: t for t in postings if t.kind == UrlKind.POSTING}
        ats = [t for t in postings if t.kind == UrlKind.ATS_POSTING]
        if ats and not self._stopped():
            self._stage("read", f"Reading {len(ats)} ATS postings through their public API", 0, len(ats))
            with ThreadPoolExecutor(max_workers=max(1, min(settings.SCRAPER_BOARD_CONCURRENCY, len(ats))), thread_name_prefix="tinyfish-ats") as pool:
                outcomes = list(pool.map(lambda t: self._ats.read(t.url), ats))
            for t, (job, status) in zip(ats, outcomes, strict=True):
                if job is not None:
                    self._add(job, "ats_api", t)
                elif status == "error":
                    need_fetch[t.url] = t
        todo = sorted(need_fetch.values(), key=lambda t: (not t.seed, t.order))
        if not todo:
            return
        result, fetched = self._fetch(todo, "html", False, "Read a job posting", "Reading job postings")
        for t in fetched:
            page = result.page_for(t.url)
            if page is None:
                self._failed(t, result.failure_for(t.url), single_posting=True)
                continue
            job = self._parse(page, t)
            jobs = [job] if job else self._jsonld_jobs(page, t)
            for j in jobs:
                self.counts["postings_parsed"] += 1
                self._add(j, "fetch", t)
            if not jobs:
                if looks_js_shell(page.text):
                    self._want_agent(t, single_posting=True)
                if t.hit is not None:
                    self._snippets[t.url] = t

    @staticmethod
    def _parse(page: FetchedPage, t: _Target) -> ScrapedJob | None:
        parse = parse_posting_markdown if page.format == "markdown" else parse_posting_html
        return parse(page.text, t.url, t.hit)

    @staticmethod
    def _jsonld_jobs(page: FetchedPage, t: _Target) -> list[ScrapedJob]:
        """A page that lists several schema.org postings: every one of them is a job."""
        if page.format == "markdown" or not page.text:
            return []
        soup = BeautifulSoup(page.text, "lxml")
        site = company_from_page(soup, t.url)
        return [j for p in _iter_jsonld(soup)[:25] if (j := jobposting_to_job(p, t.url, site))]

    # ------------------------------------------------------------------ 4. Agent
    def _agent_event(self, url: str, event: AgentEvent) -> None:
        if event.type == "PROGRESS" and event.purpose:
            self.query.note(event.purpose)
        elif event.type == "STREAMING_URL" and event.streaming_url:
            self.query.note(None, streaming_url=event.streaming_url)
        if self.hooks.agent_event:
            try:
                self.hooks.agent_event(url, event)
            except Exception:
                logger.debug("agent event callback failed", exc_info=True)

    def _agent_stage(self) -> None:
        cands = sorted(self._agent.values(), key=lambda c: (not c.target.seed, c.target.order))
        if not cands:
            return
        if not settings.tinyfish_agent_allowed:
            self._note(f"TinyFish Agent skipped for {len(cands)} page{'s' if len(cands) != 1 else ''}: "
                       + ("not available in the demo" if settings.DEMO_MODE else "TinyFish is off"))
            return
        if self.agent_budget <= 0:
            self._note(f"TinyFish Agent is set to 0 runs: {len(cands)} page{'s' if len(cands) != 1 else ''} left unread")
            return
        remaining = self._remaining()
        if not self._usable() or (remaining is not None and remaining < AGENT_MIN_SECONDS):
            self._note("TinyFish Agent skipped: not enough time left in this scan")
            return
        chosen = cands[: self.agent_budget]
        if len(cands) > len(chosen):
            self._note(f"TinyFish Agent: {len(cands) - len(chosen)} more page(s) could use it, over the budget of {self.agent_budget}")
        billing = threading.Event()  # set when TinyFish refuses to bill: no further runs
        self._stage("agent", f"Agent reading {len(chosen)} page{'s' if len(chosen) != 1 else ''} that Fetch could not", 0, len(chosen))
        workers = max(1, min(2, settings.TINYFISH_AGENT_CONCURRENCY, len(chosen)))  # TinyFish allows 2 concurrent runs
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tinyfish-agent") as pool:
            futures = [pool.submit(self._run_agent, c, billing) for c in chosen]
            for done, future in enumerate(as_completed(futures), start=1):
                outcome = future.result()
                if outcome is not None:
                    info, jobs, cand = outcome
                    self.agent_infos.append(info)
                    for job in jobs:
                        self.counts["agent_jobs"] += 1
                        self._add(job, "agent", cand.target, run_id=info.run_id)
                self._stage("agent", f"Agent reading {len(chosen)} pages that Fetch could not", done, len(chosen))

    def _run_agent(self, cand: _AgentCandidate, billing: threading.Event) -> tuple[AgentRunInfo, list[ScrapedJob], _AgentCandidate] | None:
        remaining = self._remaining()
        if billing.is_set() or self._halt or self._stopped() or (remaining is not None and remaining < AGENT_MIN_SECONDS):
            return None
        url = cand.target.url
        duration = settings.TINYFISH_AGENT_MAX_DURATION_SECONDS
        if remaining is not None:
            duration = min(duration, int(remaining))  # never run past the scan's own deadline
        try:
            run = self.client.run_agent(
                url, build_agent_goal(self.query, url, single_posting=cand.single_posting), output_schema=JOBS_OUTPUT_SCHEMA,
                stealth=cand.stealth, max_duration_seconds=duration, on_event=lambda e: self._agent_event(url, e),
                should_cancel=self.hooks.should_cancel)
        except ScraperError as exc:
            if _is_fatal(exc):
                billing.set()
            self._note(f"Agent on {url}: {exc}")
            return AgentRunInfo(url, None, "FAILED", 0, 0, None, str(exc)[:200]), [], cand
        if run.error_code in BILLING_CODES:
            billing.set()
            self._note("TinyFish Agent stopped: out of credits")
        jobs = agent_jobs_to_scraped(run.result, url, run.run_id) if run.ok else []
        error = None if run.ok else (run.error_message or run.error_code or run.status)
        return AgentRunInfo(url, run.run_id, run.status, run.steps, len(jobs), run.streaming_url, error and str(error)[:200]), jobs, cand

    # ------------------------------------------------------------------ 5. tidy up
    def _tidy(self) -> list[ScrapedJob]:
        self._stage("rank", "Removing duplicates", 0, len(self._found))
        for t in self._snippets.values():
            if t.url not in self._resolved:  # Fetch / the Agent got nothing out of it: Search's title and snippet will do
                job = _snippet_job(t)
                if job is not None:
                    self._add(job, "snippet", t)
        by_url: dict[str, _Found] = {}
        for f in self._found:
            job = f.job
            if is_gated_url(job.source_url) or (job.application_url and is_gated_url(job.application_url)):
                self._drop_gated(job.source_url)
                continue
            key = canonical_url(job.source_url)
            if f.source != "ats_api":  # the ATS scrapers' own URLs stay as they are, so they match what a board scan stores
                job.source_url = key
                job.application_url = canonical_url(job.application_url) if job.application_url else key
            best = by_url.get(key)
            if best is not None:
                self.counts["duplicates"] += 1
            if best is None or (SOURCE_RANK[f.source], f.order) < (SOURCE_RANK[best.source], best.order):
                by_url[key] = f
        by_title: dict[str, _Found] = {}
        for f in by_url.values():
            key = dedupe_key(f.job.company_name, f.job.role_title, f.job.location)
            best = by_title.get(key)
            if best is not None:
                self.counts["duplicates"] += 1
            if best is None or (SOURCE_RANK[f.source], f.order) < (SOURCE_RANK[best.source], best.order):
                by_title[key] = f
        return [f.job for f in sorted(by_title.values(), key=lambda f: f.order)]


def _snippet_job(t: _Target) -> ScrapedJob | None:
    """A posting we know only from its Search result (title + snippet): better than losing a real apply link."""
    hit = t.hit
    if hit is None or not hit.title.strip():
        return None
    ref = parse_ats_url(t.url)
    platform = detect_ats_platform(t.url)
    company = re.sub(r"[-_]+", " ", ref.board).title() if platform in _ATS_PLATFORMS and ref.board else company_from_host(t.url)
    try:
        posted = parse_date(hit.date)
    except (ValueError, OverflowError, TypeError):
        posted = None
    return ScrapedJob(company_name=company, role_title=hit.title.strip(), description=hit.snippet, source_url=t.url, application_url=t.url,
                      source_platform=platform if platform in _ATS_PLATFORMS else ATSPlatform.CUSTOM, posted_date=posted).finalize()


# --------------------------------------------------------------------------- the scraper
class TinyFishScraper(BaseScraper):
    platform = ATSPlatform.CUSTOM
    rate_key = "tinyfish"

    def __init__(self, client: TinyFishClient | None = None, http: httpx.Client | None = None) -> None:
        super().__init__(http)  # BaseScraper.client is the plain HTTP client (ATS APIs); the TinyFish client is kept apart
        self._tf = client

    def _client(self) -> TinyFishClient:
        if self._tf is not None:
            return self._tf
        from app.services.tinyfish_client import get_client

        return get_client()  # raises TinyFishNotConfigured (a ScraperError with the "add TINYFISH_API_KEY" hint)

    def discover(self, query: SearchQuery, *, hooks: DiscoveryHooks | None = None, agent_budget: int | None = None,
                 include_catalog_gaps: bool = False, deadline: float | None = None) -> DiscoveryResult:
        """Search -> Fetch -> Agent for one query. Returns deduplicated jobs that are not yet filtered or ranked.

        ``agent_budget`` (default: ``query.agent_runs``, else 2) is clamped to TINYFISH_AGENT_MAX_RUNS_PER_SCAN; the Agent never
        runs in DEMO_MODE, with fewer than 60 s left before ``deadline`` (a time.monotonic() value), or after a billing error.
        Raises a ScraperError when TinyFish is not configured or rejects the key / credits before anything was found.
        """
        return _Discovery(self, self._client(), query, hooks or DiscoveryHooks(), agent_budget, include_catalog_gaps, deadline).run()

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        from app.services.tinyfish_ranker import attach_match, rank_jobs

        result = self.discover(query, include_catalog_gaps=True)
        ranked = rank_jobs(result.jobs, query)
        for r in ranked:
            attach_match(r)
        query.note(None, usage=result.usage)
        return self.filter([r.job for r in ranked], query)  # the focus sort is stable: score order is kept within a tier

    def fetch_job(self, url: str, allow_agent: bool = True) -> ScrapedJob | None:
        """One posting: the ATS's public API when it has one, else Fetch, else (for JS-only or blocked pages) one Agent run."""
        if is_gated_url(url):
            return None
        kind = classify_url(url)
        if kind in (UrlKind.GATED, UrlKind.ATS_BOARD):
            return None
        client = self._client()
        if kind == UrlKind.ATS_POSTING:
            job, status = _AtsReader(self.client).read(url)
            if job is not None:
                return tag_provenance(job, via=["ats_api"], hit_url=url)
            if status == "closed":
                return None
        result = client.fetch([url], format="html", links=False, ttl=0, purpose="Read a job posting")
        page = result.page_for(url)
        failure = result.failure_for(url)
        if page is not None:
            job = parse_posting_markdown(page.text, url) if page.format == "markdown" else parse_posting_html(page.text, url)
            if job is not None:
                return tag_provenance(job, via=["fetch"], hit_url=url)
        wants_agent = (failure is not None and failure.code in AGENT_FETCH_CODES) or (page is not None and looks_js_shell(page.text))
        if not (allow_agent and wants_agent and settings.tinyfish_agent_allowed):
            return None
        run = client.run_agent(url, build_agent_goal(None, url, single_posting=True), output_schema=JOBS_OUTPUT_SCHEMA,
                               stealth=failure is not None and failure.code == "bot_blocked",
                               max_duration_seconds=settings.TINYFISH_AGENT_MAX_DURATION_SECONDS)
        jobs = agent_jobs_to_scraped(run.result, url, run.run_id) if run.ok else []
        if not jobs:
            return None
        jobs[0].source_url = url  # the address the user gave, like the other scrapers
        return tag_provenance(jobs[0], via=["agent"], hit_url=url, agent_run_id=run.run_id)

    def read_html(self, url: str) -> str | None:
        """The page as TinyFish Fetch reads it (HTML), for GenericScraper when plain HTTP fails. None when it can't."""
        if is_gated_url(url) or not settings.tinyfish_enabled:
            return None
        try:
            result = self._client().fetch([url], format="html", links=False, ttl=0, purpose="Read a page that plain HTTP could not load")
        except ScraperError as exc:
            logger.info("TinyFish could not read %s: %s", url, exc)
            return None
        page = result.page_for(url)
        return page.text if page is not None and page.text.strip() else None
