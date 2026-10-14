"""Pure helpers for the TinyFish source: URL rules, search plans, page parsers, Agent goal / result handling.

No network and no database in here: the scraper (scrapers/tinyfish.py) feeds it what TinyFish returned.
Page text and Agent output are untrusted, so every URL is re-checked (https, not a consent-gated site,
the employer's own site or a known ATS).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from app.models.enums import ATSPlatform, ExperienceLevel, JobType
from app.scrapers.ats_detect import detect_ats_platform, parse_ats_url
from app.scrapers.base import BLOCK_MARKERS, ScrapedJob, SearchQuery, parse_date
from app.scrapers.generic import _iter_jsonld, company_from_host, company_from_page, find_ats_boards, jobposting_to_job
from app.services.text_utils import html_to_text, normalize_text

if TYPE_CHECKING:  # the types live in services/tinyfish_types.py (kept out of the runtime import graph)
    from app.services.tinyfish_types import FetchedPage, SearchHit

# --------------------------------------------------------------------------- hosts
# LinkedIn, Internshala, Indeed and Glassdoor need the user's consent (services/sources.py). TinyFish is a third-party
# crawler, so these hosts are never sent to it, whatever the user agreed to.
GATED_HOST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|\.)linkedin\.(com|cn)$"),
    re.compile(r"(^|\.)lnkd\.in$"),
    re.compile(r"(^|\.)indeed\.(com|co\.[a-z]{2}|com\.[a-z]{2}|[a-z]{2})$"),
    re.compile(r"(^|\.)glassdoor\.(com|co\.[a-z]{2}|com\.[a-z]{2}|[a-z]{2})$"),
    re.compile(r"(^|\.)internshala\.(com|in)$"),
)
ATS_SEARCH_DOMAINS: tuple[str, ...] = ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co",
                                       "jobs.ashbyhq.com", "myworkdayjobs.com")
# Sent as Search ``exclude_domains`` (the regional sites are listed because subdomains aren't guaranteed to match)
GATED_EXCLUDE_DOMAINS: tuple[str, ...] = (
    "linkedin.com", "indeed.com", "in.indeed.com", "uk.indeed.com", "indeed.co.in", "indeed.co.uk",
    "glassdoor.com", "glassdoor.co.in", "glassdoor.co.uk", "glassdoor.ca", "internshala.com",
)
# Job aggregators, social sites and publishers: never a posting we can apply through
NON_JOB_HOSTS: tuple[str, ...] = (
    "ziprecruiter.com", "monster.com", "simplyhired.com", "jooble.org", "careerbuilder.com", "talent.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "reddit.com", "quora.com",
    "medium.com", "wikipedia.org", "pinterest.com", "tiktok.com", "substack.com",
)
AGGREGATOR_EXCLUDE_DOMAINS: tuple[str, ...] = ("ziprecruiter.com", "monster.com", "simplyhired.com", "jooble.org", "careerbuilder.com",
                                                "talent.com")

_TRACKING_PARAMS = re.compile(r"^(utm_.*|gh_src|lever-source(\[\])?|lever-origin|ref|refid|source|src|fbclid|gclid|trk|trkid|mc_[a-z]+)$", re.I)
_BLOG_SEGMENTS = frozenset({"blog", "blogs", "news", "newsroom", "press", "article", "articles", "story", "stories", "insights",
                            "media", "podcast", "events", "learn"})
_JOB_SEGMENTS = frozenset({"job", "jobs", "career", "careers", "position", "positions", "opening", "openings", "vacancy",
                           "vacancies", "join-us", "joinus", "work-with-us", "opportunities", "internship", "internships",
                           "apply", "life-at"})
_POSTING_LEADS = frozenset({"job", "jobs", "position", "positions", "opening", "openings", "vacancy", "vacancies", "posting",
                            "postings", "role", "roles"})
_LISTING_WORDS = frozenset({"search", "results", "all", "open-positions", "teams", "team", "locations", "location", "life",
                            "students", "early-careers", "university", "en", "en-us", "us", "in", "uk", "intern", "list"})
_JOB_QUERY = re.compile(r"(^|&)(job_?id|jobid|jid|gh_jid|req_?id|requisition_?id|posting_?id)=", re.I)
_ATS_PLATFORMS = frozenset({ATSPlatform.GREENHOUSE, ATSPlatform.LEVER, ATSPlatform.ASHBY, ATSPlatform.WORKDAY,
                            ATSPlatform.BAMBOOHR, ATSPlatform.ICIMS, ATSPlatform.TALEO, ATSPlatform.SMARTRECRUITERS,
                            ATSPlatform.JOBVITE})


def _host(url: str) -> str:
    try:
        return (urlparse(url if "//" in url else f"//{url}").hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def is_gated_url(url: str) -> bool:
    host = _host(url)
    return bool(host) and any(p.search(host) for p in GATED_HOST_PATTERNS)


def _host_in(host: str, domains: Sequence[str]) -> bool:
    return any(host == d or host.endswith(f".{d}") for d in domains)


def canonical_url(url: str) -> str:
    """Stable form of a URL for deduping: no tracking params, fragment or trailing slash (``gh_jid`` is kept)."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if not parts.netloc:
        return url.strip()
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


class UrlKind(str, Enum):
    GATED = "gated"
    ATS_BOARD = "ats_board"
    ATS_POSTING = "ats_posting"
    POSTING = "posting"
    CAREERS_PAGE = "careers_page"
    OTHER = "other"


def _looks_like_slug(segment: str) -> bool:
    return bool(re.search(r"\d", segment)) or segment.count("-") >= 3


def classify_url(url: str) -> UrlKind:
    if is_gated_url(url):
        return UrlKind.GATED
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host or _host_in(host, NON_JOB_HOSTS):
        return UrlKind.OTHER
    segments = [s.lower() for s in parsed.path.split("/") if s]

    platform = ATSPlatform.UNKNOWN
    if _host_in(host, ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkdaysite.com", "workday.com")):
        platform = detect_ats_platform(f"https://{host}")
    if platform in (ATSPlatform.GREENHOUSE, ATSPlatform.LEVER, ATSPlatform.ASHBY, ATSPlatform.WORKDAY):
        ref = parse_ats_url(url)
        if platform == ATSPlatform.WORKDAY:
            if ref.job_id:
                return UrlKind.ATS_POSTING
            return UrlKind.ATS_BOARD if ref.site and host.count(".") >= 2 else UrlKind.OTHER
        if not ref.board or ref.board in ("embed", "v1"):
            return UrlKind.OTHER
        return UrlKind.ATS_POSTING if ref.job_id else UrlKind.ATS_BOARD

    if any(s in _BLOG_SEGMENTS for s in segments):
        return UrlKind.OTHER
    if _JOB_QUERY.search(parsed.query) and (segments or host.split(".")[0] in ("careers", "jobs")):
        return UrlKind.POSTING  # e.g. acme.com/careers?gh_jid=123
    careers_host = host.split(".")[0] in ("careers", "career", "jobs", "join")
    marker = next((i for i, s in enumerate(segments) if s in _JOB_SEGMENTS), None)
    if marker is None and not careers_host:
        return UrlKind.OTHER
    rest = segments[(marker + 1 if marker is not None else 0):]
    rest = [s for s in rest if s not in _JOB_SEGMENTS and s not in _LISTING_WORDS]
    if not rest:
        return UrlKind.CAREERS_PAGE
    after_lead = marker is not None and any(s in _POSTING_LEADS for s in segments[marker:-1])
    if after_lead or _looks_like_slug(segments[-1]):
        return UrlKind.POSTING
    return UrlKind.CAREERS_PAGE


# --------------------------------------------------------------------------- search plans
@dataclass(frozen=True)
class SearchPlan:
    query: str
    purpose: str
    kind: Literal["ats", "open", "company"]
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()
    location: str | None = None
    after_date: date | None = None
    company: str | None = None


_COUNTRY_NAMES = {
    "india": "IN", "united states": "US", "usa": "US", "us": "US", "u.s.": "US", "america": "US", "united kingdom": "GB",
    "uk": "GB", "england": "GB", "great britain": "GB", "canada": "CA", "germany": "DE", "france": "FR", "netherlands": "NL",
    "ireland": "IE", "spain": "ES", "italy": "IT", "poland": "PL", "sweden": "SE", "switzerland": "CH", "singapore": "SG",
    "australia": "AU", "new zealand": "NZ", "japan": "JP", "united arab emirates": "AE", "uae": "AE", "brazil": "BR",
    "israel": "IL", "mexico": "MX", "south africa": "ZA", "nigeria": "NG", "indonesia": "ID", "philippines": "PH",
}
_CITY_COUNTRY = {
    "delhi": "IN", "new delhi": "IN", "delhi ncr": "IN", "gurugram": "IN", "gurgaon": "IN", "noida": "IN", "bengaluru": "IN",
    "bangalore": "IN", "mumbai": "IN", "pune": "IN", "hyderabad": "IN", "chennai": "IN", "kolkata": "IN", "ahmedabad": "IN",
    "san francisco": "US", "new york": "US", "seattle": "US", "austin": "US", "boston": "US", "los angeles": "US",
    "london": "GB", "manchester": "GB", "toronto": "CA", "vancouver": "CA", "berlin": "DE", "munich": "DE", "paris": "FR",
    "amsterdam": "NL", "dublin": "IE", "sydney": "AU", "melbourne": "AU", "tokyo": "JP", "dubai": "AE",
}


def country_code(location: str | None) -> str | None:
    """ISO country code for Search's ``location`` parameter, or None when unknown / remote (Search then defaults to US)."""
    if not location:
        return None
    parts = [normalize_text(p) for p in location.split(",") if p.strip()]
    for part in reversed(parts):
        if part in _COUNTRY_NAMES:
            return _COUNTRY_NAMES[part]
    for part in parts:
        if part in _CITY_COUNTRY:
            return _CITY_COUNTRY[part]
    return None


_SENIORITY_TERMS = {
    ExperienceLevel.INTERNSHIP.value: "internship", ExperienceLevel.ENTRY.value: "entry level new grad",
    ExperienceLevel.MID.value: "mid level", ExperienceLevel.SENIOR.value: "senior", ExperienceLevel.LEAD.value: "lead staff",
    ExperienceLevel.EXECUTIVE.value: "director",
}


def _round_robin(*lists: Sequence[SearchPlan]) -> list[SearchPlan]:
    out: list[SearchPlan] = []
    for i in range(max((len(x) for x in lists), default=0)):
        out.extend(x[i] for x in lists if i < len(x))
    return out


def build_search_plans(query: SearchQuery, max_queries: int, *, catalog_gap_companies: Sequence[str] = ()) -> list[SearchPlan]:
    """Search calls for a scan: an ATS-domain pass, an open pass and one per company, interleaved and capped."""
    roles = [r.strip() for r in query.keywords if r and r.strip()] or ([" ".join(query.skills[:2])] if query.skills else [])
    locations: list[str | None] = [loc for loc in query.locations if loc and loc.strip()] or [None]
    levels = [lv for lv in query.seniority if lv in _SENIORITY_TERMS] or (["internship"] if query.internships_only else [])
    term = _SENIORITY_TERMS[levels[0]] if levels else ""
    after = None
    if query.posted_within_days:
        after = datetime.now(UTC).date() - timedelta(days=query.posted_within_days)
    visa = "visa sponsorship" if query.needs_sponsorship else ""
    skills = " ".join(query.skills[:2])

    def words(*parts: str | None) -> str:
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()

    ats: list[SearchPlan] = []
    opened: list[SearchPlan] = []
    for i in range(max(len(roles), 1) * len(locations)):  # roles x locations, one step of each per round
        role = roles[i % len(roles)] if roles else None
        loc = locations[(i // max(len(roles), 1)) % len(locations)]
        level_term = "" if term and role and term.split()[0][:5] in normalize_text(role) else term
        base = words(role, level_term, loc or ("remote" if query.remote else None))
        if not base:
            continue
        code = country_code(loc)
        what = words(role, level_term) or "jobs"
        ats.append(SearchPlan(query=words(base, skills, visa), purpose=f"Find open {what} postings on company job boards",
                              kind="ats", include_domains=ATS_SEARCH_DOMAINS, location=code, after_date=after))
        opened.append(SearchPlan(query=words(base, "careers apply", visa), purpose=f"Find {what} openings on company careers pages",
                                 kind="open", exclude_domains=(*GATED_EXCLUDE_DOMAINS, *AGGREGATOR_EXCLUDE_DOMAINS),
                                 location=code, after_date=after))

    named: list[str] = []
    for name in query.companies:
        if name and name.strip() and normalize_text(name) not in {normalize_text(n) for n in named}:
            named.append(name.strip())
    gaps = [g.strip() for g in catalog_gap_companies if g and g.strip() and normalize_text(g) not in {normalize_text(n) for n in named}]
    named.extend(dict.fromkeys(gaps[:3]))  # up to 3 catalog companies that have no public board of their own
    first_code = country_code(next((loc for loc in locations if loc), None))
    companies = [SearchPlan(query=words(name, "careers", roles[0] if roles else None), kind="company", company=name,
                            purpose=f"Find the {name} careers page and its open roles", exclude_domains=GATED_EXCLUDE_DOMAINS,
                            location=first_code, after_date=None) for name in named]

    plans: list[SearchPlan] = []
    seen: set[tuple[str, str]] = set()
    for plan in _round_robin(ats, opened, companies):
        key = (plan.kind, normalize_text(plan.query))
        if key not in seen:
            seen.add(key)
            plans.append(plan)
    return plans[: max(max_queries, 0)]


# --------------------------------------------------------------------------- page parsing
_MD_LINK = re.compile(r"(?<!!)\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def markdown_links(text: str, base_url: str | None = None) -> list[tuple[str, str]]:
    """``[anchor](url)`` pairs of a markdown page; relative URLs are made absolute when ``base_url`` is given."""
    out: list[tuple[str, str]] = []
    for m in _MD_LINK.finditer(text or ""):
        href = m.group(2).strip()
        if base_url:
            href = urljoin(base_url, href)
        out.append((re.sub(r"\s+", " ", m.group(1)).strip(), href))
    return out


def _slug_text(url: str) -> str:
    segments = [s for s in urlparse(url).path.split("/") if s]
    return re.sub(r"[-_]+", " ", segments[-1]) if segments else ""


def job_links_from_page(page: FetchedPage, query: SearchQuery, limit: int = 15) -> list[str]:
    """Posting links on a careers page whose anchor (or URL slug) matches the target roles."""
    base = page.final_url or page.url
    candidates: list[tuple[str, str]] = []
    if page.format == "html":
        soup = BeautifulSoup(page.text or "", "lxml")
        candidates.extend((a.get_text(" ", strip=True), urljoin(base, a["href"])) for a in soup.find_all("a", href=True))
    else:
        candidates.extend(markdown_links(page.text, base))
    candidates.extend(("", urljoin(base, link)) for link in page.links or [])
    here = canonical_url(base)
    out: list[str] = []
    seen: set[str] = {here}
    for anchor, href in candidates:
        if not href.startswith(("http://", "https://")):
            continue
        canon = canonical_url(href)
        if canon in seen or classify_url(canon) not in (UrlKind.POSTING, UrlKind.ATS_POSTING):
            continue
        if not (anchor and query.matches_title(anchor)) and not query.matches_title(_slug_text(canon)):
            continue
        seen.add(canon)
        out.append(canon)
        if len(out) >= limit:
            break
    return out


def ats_boards_in_page(page: FetchedPage) -> dict[str, set[str]]:
    """Greenhouse / Lever / Ashby / Workday boards embedded in or linked from a fetched page."""
    return find_ats_boards("\n".join([page.text or "", *(page.links or [])]), page.final_url or page.url)


_LOCATION_LINE = re.compile(r"(?im)^[\s*_>#-]*(?:locations?|office)[\s*_]*[:|-][\s*_]*(.{2,120})$")
_POSTED = re.compile(r"(?i)posted\s*(?:on|:|-)?\s*(\d{4}-\d{2}-\d{2}|\d+\+?\s*(?:day|week|month)s?\s+ago|today|yesterday|just now)")
_TITLE_AT = re.compile(r"^(.{3,120}?)\s+(?:at|@)\s+([^|\-–]{2,60})$")


def _posted_from_text(text: str) -> date | None:
    m = _POSTED.search(text[:6000])
    return parse_date(m.group(1)) if m else None


def _platform_of(url: str) -> ATSPlatform:
    platform = parse_ats_url(url).platform
    return platform if platform in _ATS_PLATFORMS else ATSPlatform.CUSTOM


def _split_title_company(title: str) -> tuple[str, str | None]:
    m = _TITLE_AT.match(title.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else (title.strip(), None)


def parse_posting_html(html: str, url: str, hit: SearchHit | None = None) -> ScrapedJob | None:
    """One posting from a page: schema.org JobPosting first, else the ``h1`` and the ``main`` text."""
    if not html or not html.strip():
        return None
    soup = BeautifulSoup(html, "lxml")
    site = company_from_page(soup, url)
    postings = _iter_jsonld(soup)
    here = canonical_url(url)
    chosen = next((p for p in postings if canonical_url(str(p.get("url") or "")) == here), None)
    if chosen is None and len(postings) == 1:
        chosen = postings[0]
    if chosen is not None:
        job = jobposting_to_job(chosen, url, site)
        if job:
            job.source_url = url
            job.application_url = job.application_url or url
            return job
    if len(postings) > 1:
        return None  # a listing of several postings, not one posting
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    heading = soup.find("h1")
    og = soup.find("meta", attrs={"property": "og:title"})
    raw_title = (heading.get_text(" ", strip=True) if heading else "") or ((og.get("content") or "").strip() if og else "")
    raw_title = raw_title or (soup.title.get_text(" ", strip=True) if soup.title else "") or (hit.title if hit else "")
    main = soup.find("main") or soup.find("article") or soup.body
    body = main.get_text("\n", strip=True) if main else ""
    if not raw_title or len(body) < 40:
        return None
    title, title_company = _split_title_company(raw_title)
    loc = _LOCATION_LINE.search(body)
    return ScrapedJob(
        company_name=title_company or site,
        role_title=title,
        description=body[:20000],
        source_url=url,
        application_url=url,
        source_platform=_platform_of(url),
        location=loc.group(1).strip() if loc else None,
        posted_date=_posted_from_text(body),
    ).finalize()


def _strip_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = _MD_LINK.sub(lambda m: m.group(1), text)
    text = re.sub(r"(?m)^[\s>]*#{1,6}\s*", "", text)
    text = re.sub(r"[*_`]{1,3}", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_posting_markdown(text: str, url: str, hit: SearchHit | None = None) -> ScrapedJob | None:
    """One posting from a Fetch markdown page: the first heading is the title; an "Apply" link is the apply URL."""
    if not text or not text.strip():
        return None
    heading = re.search(r"(?m)^\s*#{1,2}\s+(.+?)\s*#*\s*$", text)
    raw_title = (heading.group(1).strip() if heading else "") or (hit.title if hit else "")
    raw_title = _MD_LINK.sub(lambda m: m.group(1), raw_title).strip(" *_")
    body = _strip_markdown(text[heading.end():] if heading else text)
    if not raw_title or len(body) < 40:
        return None
    title, title_company = _split_title_company(raw_title)
    apply_url = next((href for anchor, href in markdown_links(text, url)
                      if re.search(r"\bapply\b", anchor, re.I) and href.startswith(("http://", "https://")) and not is_gated_url(href)), None)
    loc = _LOCATION_LINE.search(text)
    return ScrapedJob(
        company_name=title_company or company_from_host(url),
        role_title=title,
        description=body[:20000],
        source_url=url,
        application_url=apply_url or url,
        source_platform=_platform_of(url),
        location=_strip_markdown(loc.group(1)) if loc else None,
        posted_date=_posted_from_text(text),
    ).finalize()


_JS_MARKERS = re.compile(r"enable javascript|javascript (is )?(required|disabled)|requires javascript|you need to enable js|"
                         r"<noscript[^>]*>[^<]*(enable|requires?)", re.I)


def looks_js_shell(content: str) -> bool:
    """A page with (almost) no readable text: an empty app shell, a "please enable JavaScript" or a bot-check page."""
    if not content or not content.strip():
        return True
    if "<" in content and re.search(r"<(html|body|div|script)\b", content, re.I):
        soup = BeautifulSoup(content, "lxml")
        scripts = len([t for t in soup.find_all("script") if "json" not in str(t.get("type") or "").lower()])  # data isn't an app
        for tag in soup(["script", "style", "noscript", "template"]):
            tag.decompose()
        visible = len(re.sub(r"\s+", " ", soup.get_text(" ", strip=True)))
    else:
        scripts, visible = 0, len(re.sub(r"\s+", " ", content.strip()))
    if visible < 400 and scripts:
        return True
    return visible < 800 and bool(_JS_MARKERS.search(content) or any(marker in content for marker in BLOCK_MARKERS))


# --------------------------------------------------------------------------- Agent
# Only keywords TinyFish accepts: type, properties, items, maxItems, required, enum, nullable
# (additionalProperties, const, oneOf and ``type: [.., "null"]`` are rejected with HTTP 400).
JOBS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company": {"type": "string", "nullable": True},
        "blocked": {"type": "boolean", "nullable": True},
        "jobs": {
            "type": "array",
            "maxItems": 25,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "apply_url": {"type": "string"},
                    "location": {"type": "string", "nullable": True},
                    "posted": {"type": "string", "nullable": True},
                    "employment_type": {"type": "string", "nullable": True},
                    "seniority": {"type": "string", "nullable": True},
                    "summary": {"type": "string", "nullable": True},
                    "visa_sponsorship": {"type": "string", "enum": ["yes", "no", "unknown"]},
                },
                "required": ["title", "apply_url"],
            },
        },
    },
    "required": ["jobs"],
}
MAX_AGENT_JOBS = 25
MAX_SUMMARY_CHARS = 4000


def build_agent_goal(query: SearchQuery | None, url: str, *, single_posting: bool = False) -> str:
    roles = ", ".join(r for r in (query.keywords if query else []) if r) or "any role"
    places = ", ".join(loc for loc in (query.locations if query else []) if loc) or "any location"
    levels = ", ".join(query.seniority) if query and query.seniority else "any seniority"
    if single_posting:
        find = ("This URL is a single job posting. Read it and return exactly that one posting in the jobs list "
                "(apply_url = the page's apply link, or this page's URL).")
    else:
        find = (f"Find the open job listings that match these roles: {roles}; locations: {places}; seniority: {levels}. "
                "Skip listings that clearly do not match.")
    steps = [
        f"Open {url} and dismiss any cookie or consent banner.",
        'If the listings are paginated or behind a "Load more" / "Show more" button, press it at most 3 times.',
        find,
        "Extract at most 25 listings with exactly these fields: title, apply_url, location, posted (date or text as shown), "
        "employment_type, seniority, summary (1-3 sentences), visa_sponsorship (yes, no or unknown).",
        "apply_url must be the absolute URL of the posting or its apply page. Use null for anything you cannot see, "
        "do not guess, and leave a listing out when its URL is unknown.",
        "Never log in, fill in forms, upload files or submit anything. Do not leave the employer's site, except to follow a "
        "link to its job board (Greenhouse, Lever, Ashby, Workday).",
        'If the page is blocked or behind a login wall, return {"jobs": [], "blocked": true}.',
    ]
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))


_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}


def _registrable(host: str) -> str:
    labels = host.lower().split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _safe_apply_url(value: Any, page_url: str) -> str | None:
    """The apply URL when it is safe to keep: https, no credentials, not gated, the employer's site or a known ATS."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or is_gated_url(value):
        return None
    page_host = _host(page_url)
    same_site = bool(page_host) and _registrable(host) == _registrable(page_host)
    if same_site or detect_ats_platform(f"https://{host}") in _ATS_PLATFORMS:
        return value
    return None


def _job_type(value: Any) -> JobType | None:
    text = normalize_text(str(value or ""))
    for needle, kind in (("intern", JobType.INTERNSHIP), ("part", JobType.PART_TIME), ("contract", JobType.CONTRACT),
                         ("freelance", JobType.FREELANCE), ("temp", JobType.CONTRACT), ("full", JobType.FULL_TIME)):
        if needle in text:
            return kind
    return None


def _experience(value: Any) -> ExperienceLevel | None:
    text = normalize_text(str(value or ""))
    if not text:
        return None
    for level in ExperienceLevel:
        if level.value == text:
            return level
    for needle, level in (("intern", ExperienceLevel.INTERNSHIP), ("entry", ExperienceLevel.ENTRY), ("junior", ExperienceLevel.ENTRY),
                          ("graduate", ExperienceLevel.ENTRY), ("mid", ExperienceLevel.MID), ("senior", ExperienceLevel.SENIOR),
                          ("lead", ExperienceLevel.LEAD), ("staff", ExperienceLevel.LEAD), ("principal", ExperienceLevel.LEAD),
                          ("director", ExperienceLevel.EXECUTIVE), ("executive", ExperienceLevel.EXECUTIVE)):
        if needle in text:
            return level
    return None


def _company_for(page_url: str) -> str:
    ref = parse_ats_url(page_url)
    if ref.platform in _ATS_PLATFORMS and ref.board and ref.board != "embed":
        return re.sub(r"[-_]+", " ", ref.board).title()
    return company_from_host(page_url)


def agent_jobs_to_scraped(result: dict | None, page_url: str, run_id: str | None = None) -> list[ScrapedJob]:
    """Turn an Agent run's ``result`` into jobs, dropping anything that isn't a safe, apply-able posting."""
    if not isinstance(result, dict) or not isinstance(result.get("jobs"), list):
        return []
    company = result.get("company") if isinstance(result.get("company"), str) and result["company"].strip() else _company_for(page_url)
    jobs: list[ScrapedJob] = []
    seen: set[str] = set()
    for item in result["jobs"][:MAX_AGENT_JOBS]:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        apply_url = _safe_apply_url(item.get("apply_url"), page_url)
        if not isinstance(title, str) or not title.strip() or not apply_url:
            continue
        url = canonical_url(apply_url)
        if url in seen:
            continue
        seen.add(url)
        location = item.get("location") if isinstance(item.get("location"), str) else None
        summary = html_to_text(item["summary"]) if isinstance(item.get("summary"), str) else ""
        platform = detect_ats_platform(url)
        try:
            posted = parse_date(item.get("posted"))
        except (TypeError, ValueError, OverflowError):
            posted = None
        job = ScrapedJob(
            company_name=company.strip(),
            role_title=title.strip(),
            description=summary[:MAX_SUMMARY_CHARS],
            source_url=url,
            application_url=url,
            source_platform=platform if platform in _ATS_PLATFORMS else ATSPlatform.CUSTOM,
            location=location.strip() if location and location.strip() else None,
            job_type=_job_type(item.get("employment_type")),
            experience_level=_experience(item.get("seniority")),
            posted_date=posted,
            raw={},
        ).finalize()
        visa = str(item.get("visa_sponsorship") or "").lower()
        if visa in ("yes", "no"):  # worded so job_matcher.NO_SPONSORSHIP_PATTERNS reads the "no"
            job.raw["sponsorship"] = "Visa sponsorship offered" if visa == "yes" else "No visa sponsorship"
        tag_provenance(job, via=["agent"], hit_url=page_url, agent_run_id=run_id)
        jobs.append(job)
    return jobs


def tag_provenance(job: ScrapedJob, *, via: Sequence[str], query: str | None = None, hit_url: str | None = None,
                   hit_title: str | None = None, agent_run_id: str | None = None) -> ScrapedJob:
    """Record how TinyFish found a job in ``job.raw`` (kept in ``Job.raw_data``); other raw keys are untouched."""
    raw = dict(job.raw or {})
    raw["discovered_by"] = "tinyfish"
    raw["tinyfish"] = {
        "via": list(via),
        "query": query,
        "hit_url": hit_url,
        "hit_title": hit_title,
        "agent_run_id": agent_run_id,
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    job.raw = raw
    return job
