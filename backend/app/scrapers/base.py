"""Scraper interface + shared helpers (HTTP with retries, classification, salary parsing)."""

from __future__ import annotations

import logging
import random
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from app.automation.browser import USER_AGENTS
from app.config import settings
from app.models.enums import ATSPlatform, ExperienceLevel, JobType
from app.services.intern_level import internships_only, is_internship
from app.services.location_focus import LocationFocus, get_focus, location_tier
from app.services.rate_limiter import rate_limiter
from app.services.text_utils import html_to_text, normalize_text

logger = logging.getLogger(__name__)


class ScraperError(Exception):
    pass


class RateLimited(ScraperError):
    pass


@dataclass
class SearchQuery:
    keywords: list[str] = field(default_factory=list)  # target roles
    locations: list[str] = field(default_factory=list)
    remote: bool = False
    job_types: list[str] = field(default_factory=list)
    posted_within_days: int = 14
    limit: int = 50
    sources: dict[str, Any] = field(default_factory=dict)
    # What to type into a site's search box when it differs from the target roles (e.g. "Google intern");
    # titles are still matched against ``keywords``.
    search_terms: list[str] | None = None
    focus: LocationFocus | None = None  # e.g. India with Delhi NCR first (see services/location_focus.py)
    internships_only: bool = False  # drop every posting that isn't an internship (services/intern_level.py)
    # Sites that forbid automation (services/sources.py) this scan may touch: only the ones you agreed to
    allowed_gated: frozenset[str] = field(default_factory=frozenset)
    # Postings already in the database: scrapers skip re-downloading their detail pages.
    known_urls: frozenset[str] = field(default_factory=frozenset, repr=False, compare=False)
    # Called as progress(done, total) while a scraper works through boards / pages (scan progress bar).
    progress: Callable[[int, int], None] | None = field(default=None, repr=False, compare=False)
    # time.monotonic() after which a scraper should wrap up and return what it has so far.
    deadline: float | None = field(default=None, repr=False, compare=False)

    def time_up(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline

    def report(self, done: int, total: int) -> None:
        if self.progress is not None:
            try:
                self.progress(done, total)
            except Exception:  # progress reporting must never break a scan
                logger.debug("progress callback failed", exc_info=True)

    def is_known(self, url: str | None) -> bool:
        return bool(url) and url in self.known_urls

    @classmethod
    def from_preferences(cls, prefs: dict[str, Any], limit: int = 50) -> SearchQuery:
        focus = get_focus(prefs)
        locations = [loc for loc in prefs.get("target_locations") or [] if loc]
        if focus is not None and not locations:  # search the prime city and the whole country
            prime = (prefs.get("location_focus") or {}).get("prime_cities") or []
            country = str((prefs.get("location_focus") or {}).get("country") or "").strip()
            locations = [f"{prime[0]}, {country}", country] if prime else [country]
        only_interns = internships_only(prefs)
        return cls(
            keywords=[k for k in prefs.get("target_roles") or [] if k],
            locations=locations,
            remote=(prefs.get("remote_preference") == "remote"),
            job_types=["internship"] if only_interns else prefs.get("job_types") or [],
            internships_only=only_interns,
            posted_within_days=int(prefs.get("posted_within_days") or 14),
            limit=limit,
            sources=prefs.get("sources") or {},
            focus=focus,
        )

    def matches_title(self, title: str) -> bool:
        if not self.keywords:
            return True
        from app.services.job_matcher import _role_matches

        norm = normalize_text(title)
        return any(_role_matches(normalize_text(k), norm) for k in self.keywords)

    def matches_location(self, location: str | None, remote: bool) -> bool:
        if self.focus is not None:
            # Everything is kept here; the scan keeps ~country_share % in the focus country afterwards.
            return True
        if not self.locations:
            return True
        if remote:
            return True
        loc = normalize_text(location or "")
        if not loc:
            return True
        return any(normalize_text(target).split(",")[0].strip() in loc for target in self.locations) or "remote" in loc


@dataclass
class ScrapedJob:
    company_name: str
    role_title: str
    description: str
    source_url: str
    source_platform: ATSPlatform
    application_url: str | None = None
    location: str | None = None
    is_remote: bool = False
    job_type: JobType | None = None
    experience_level: ExperienceLevel | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    posted_date: date | None = None
    deadline_date: date | None = None
    external_id: str | None = None
    easy_apply: bool = False
    company_logo_url: str | None = None
    company_domain: str | None = None
    requirements: str | None = None
    raw: dict[str, Any] | None = None

    def finalize(self) -> ScrapedJob:
        self.description = html_to_text(self.description) if self.description else ""
        text = f"{self.role_title} {self.location or ''} {self.description[:2000]}"
        if not self.is_remote:
            self.is_remote = detect_remote(self.location, self.description)
        if self.job_type is None:
            self.job_type = infer_job_type(self.role_title, text)
        if self.experience_level is None:
            self.experience_level = infer_experience_level(self.role_title, self.description)
        if self.salary_min is None and self.salary_max is None:
            parsed = parse_salary(self.description)
            if parsed:
                self.salary_min, self.salary_max, self.salary_currency = parsed
        self.role_title = self.role_title.strip()[:255]
        self.company_name = (self.company_name or "Unknown").strip()[:255]
        if self.location:
            self.location = self.location.strip()[:255]
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- classification helpers
def detect_remote(location: str | None, description: str | None = None) -> bool:
    loc = normalize_text(location or "")
    if re.search(r"\bremote\b|anywhere|work from home|wfh", loc):
        return True
    desc = normalize_text((description or "")[:1500])
    return bool(re.search(r"\b(fully remote|100% remote|remote-first|remote first|this (role|position) is remote)\b", desc))


def infer_job_type(title: str, text: str = "") -> JobType | None:
    t = normalize_text(f"{title} {text[:600]}")
    title_n = normalize_text(title)
    if re.search(r"\bintern(ship)?\b|co-?op\b|\bsummer (analyst|associate)\b", title_n):
        return JobType.INTERNSHIP
    if re.search(r"\bcontract(or)?\b|\bcontract-to-hire\b|\btemporary\b|\btemp\b", title_n):
        return JobType.CONTRACT
    if re.search(r"\bfreelance\b", title_n):
        return JobType.FREELANCE
    if re.search(r"\bpart[- ]time\b", t):
        return JobType.PART_TIME
    if re.search(r"\bintern(ship)?\b", t) and "intern" in title_n:
        return JobType.INTERNSHIP
    return JobType.FULL_TIME


def infer_experience_level(title: str, description: str = "") -> ExperienceLevel | None:
    t = normalize_text(title)
    if re.search(r"\bintern(ship)?\b|co-?op", t):
        return ExperienceLevel.INTERNSHIP
    if re.search(r"\b(vp|vice president|chief|cto|ceo|cfo|head of|director)\b", t):
        return ExperienceLevel.EXECUTIVE
    if re.search(r"\b(lead|principal|staff|manager|architect)\b", t):
        return ExperienceLevel.LEAD
    if re.search(r"\b(senior|sr\.?|iii|iv)\b", t):
        return ExperienceLevel.SENIOR
    if re.search(r"\b(junior|jr\.?|entry|graduate|new grad|associate|i)\b", t):
        return ExperienceLevel.ENTRY
    return ExperienceLevel.MID


_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "c$": "CAD", "a$": "AUD"}


def parse_salary(text: str | None) -> tuple[int, int, str] | None:
    if not text:
        return None
    m = re.search(
        r"(c\$|a\$|\$|€|£|₹)\s?(\d{2,3}(?:[,.]\d{3})*|\d{2,3})\s?(k)?\s*(?:-|–|to)\s*(?:c\$|a\$|\$|€|£|₹)?\s?(\d{2,3}(?:[,.]\d{3})*|\d{2,3})\s?(k)?",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None

    tail = text[m.end(): m.end() + 25].lower()
    if re.match(r"\s*(/|per|an?)\s*(hr|hour)", tail):
        return None  # hourly pay

    def to_int(value: str, k: str | None) -> int:
        number = int(re.sub(r"[,.]", "", value))
        return number * 1000 if k else number

    low, high = to_int(m.group(2), m.group(3) or m.group(5)), to_int(m.group(4), m.group(5))
    if low < 10000 or high < low:  # hourly or noise
        return None
    return low, high, _CURRENCY.get(m.group(1).lower(), "USD")


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(ts, tz=UTC).date()
    text = str(value).strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return date.fromisoformat(m.group(1))
    low = text.lower()
    today = datetime.now(UTC).date()
    if "today" in low or "just" in low or "hour" in low:
        return today
    if "yesterday" in low:
        return today - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*(day|week|month)", low) or re.fullmatch(r"(\d+)\+?\s*([dwm])", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)[0]
        return today - timedelta(days=n * {"d": 1, "w": 7, "m": 30}[unit])
    if re.fullmatch(r"\d+\+?\s*h", low):
        return today
    try:
        from dateutil import parser as dateparser

        return dateparser.parse(text).date()
    except (ValueError, OverflowError):
        return None


# --------------------------------------------------------------------------- base class
# Pages a site shows instead of content: a captcha wants a person; a challenge page (a JavaScript check)
# is passed by a real browser, which is why some scrapers fall back to one
CAPTCHA_MARKERS = ("px-captcha", "verify you are human", "unusual traffic", "g-recaptcha", "hcaptcha")
BLOCK_MARKERS = (*CAPTCHA_MARKERS, "cf-challenge", "challenge-platform", "Just a moment...", "Access Denied")


def looks_blocked(html: str) -> bool:
    return any(marker in html for marker in BLOCK_MARKERS)


def wants_a_person(html: str) -> bool:
    return any(marker in html for marker in CAPTCHA_MARKERS)


def retry_after_seconds(value: str | None) -> float | None:
    """``Retry-After`` as seconds: a number of seconds, or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        from email.utils import parsedate_to_datetime

        return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


class BaseScraper(ABC):
    platform: ATSPlatform = ATSPlatform.UNKNOWN
    rate_key: str = "unknown"
    requires_browser: bool = False

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            timeout=25,
            follow_redirects=True,
            headers={"User-Agent": random.choice(USER_AGENTS), "Accept-Language": "en-US,en;q=0.9"},
        )

    # Retry policy (PLAN.md §13): network timeout -> 3 attempts (2s, 4s, 8s); 429 -> pause platform 15 min.
    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if not rate_limiter.allow_request(self.rate_key):
            raise RateLimited(f"{self.rate_key} request budget exhausted or cooling down")
        delays = [2, 4, 8]
        for attempt in range(len(delays) + 1):
            try:
                response = self.client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == len(delays):
                    raise ScraperError(f"Network error for {url}: {exc}") from exc
                time.sleep(delays[attempt])
                continue
            if response.status_code == 429:
                paused = rate_limiter.back_off(self.rate_key, retry_after_seconds(response.headers.get("retry-after")))
                raise RateLimited(f"{self.rate_key} returned HTTP 429: paused for {paused // 60} min")
            if response.status_code in (403, 429, 503) and wants_a_person(response.text[:20000]):
                paused = rate_limiter.back_off(self.rate_key)  # a captcha: stop, never try to get around it
                raise RateLimited(f"{self.rate_key} asked for a captcha: paused for {paused // 60} min")
            if response.status_code >= 500 and attempt < len(delays):
                time.sleep(delays[attempt])
                continue
            return response
        raise ScraperError(f"Request failed: {url}")  # pragma: no cover

    def get_json(self, url: str, **kwargs: Any) -> Any:
        response = self.request("GET", url, **kwargs)
        if response.status_code == 404:
            raise ScraperError(f"Not found: {url}")
        response.raise_for_status()
        return response.json()

    def map_sources(self, items: Iterable[str], fetch: Callable[[str], list[ScrapedJob]], query: SearchQuery,
                    label: str) -> list[ScrapedJob]:
        """Fetch several boards / pages at once and return their jobs in the original order.

        One failing board never stops the others; it is logged and skipped.
        """
        names = [str(i).strip() for i in items if str(i).strip()]
        if not names:
            return []
        results: dict[int, list[ScrapedJob]] = {}
        workers = max(1, min(settings.SCRAPER_BOARD_CONCURRENCY, len(names)))
        done = 0
        query.report(0, len(names))
        def fetch_in_time(name: str) -> list[ScrapedJob]:
            return [] if query.time_up() else fetch(name)  # out of time: skip the boards not started yet

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"scrape-{self.rate_key}") as pool:
            futures = {pool.submit(fetch_in_time, name): idx for idx, name in enumerate(names)}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except RateLimited as exc:
                    logger.warning("%s %s skipped: %s", label, names[idx], exc)
                except Exception as exc:  # noqa: BLE001 - e.g. HTTP 403 on one board
                    logger.warning("%s %s failed: %s", label, names[idx], exc)
                done += 1
                query.report(done, len(names))
        return [job for idx in sorted(results) for job in results[idx]]

    @abstractmethod
    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        """Return jobs matching the query (already filtered by title / location)."""

    def fetch_job(self, url: str) -> ScrapedJob | None:  # pragma: no cover - optional
        return None

    def filter(self, jobs: list[ScrapedJob], query: SearchQuery) -> list[ScrapedJob]:
        cutoff = datetime.now(UTC).date() - timedelta(days=query.posted_within_days or 3650)
        out = []
        for job in jobs:
            if query.internships_only and not is_internship(job.role_title, job.job_type):
                continue  # a full-time / new-grad / contract job
            if not query.matches_title(job.role_title):
                continue
            if query.remote and not job.is_remote:
                continue
            if not query.matches_location(job.location, job.is_remote):
                continue
            if job.posted_date and job.posted_date < cutoff:
                continue
            out.append(job)
        if query.focus is not None:  # prime city, then the rest of the country, first in line for the limit
            out.sort(key=lambda j: location_tier(j.location, j.is_remote, query.focus))
        return out[: query.limit]
