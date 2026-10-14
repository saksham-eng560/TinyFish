"""Preference-only ranker for TinyFish listings: a 0-100 score with per-factor reasons, no resume needed.

The Finder works before anyone uploads a resume, so listings are scored against what the user typed
(roles, locations, seniority, skills, visa need) instead of against a resume (services/job_matcher.py).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from app.models.enums import ExperienceLevel
from app.scrapers.base import ScrapedJob, SearchQuery, infer_experience_level
from app.services.job_matcher import NO_SPONSORSHIP_PATTERNS, _role_matches
from app.services.text_utils import normalize_text

WEIGHTS = {"title": 35, "location": 20, "seniority": 15, "keywords": 15, "recency": 10, "visa": 5}  # sums to 100

VisaStatus = Literal["sponsors", "no_sponsorship", "unknown"]

# NO_SPONSORSHIP_PATTERNS misses the most common wording ("we do not sponsor visas"), so it is widened here
_NEGATIVE = re.compile(
    NO_SPONSORSHIP_PATTERNS.pattern
    + r"|(?:do(?:es)?\s+not|don'?t|doesn'?t|cannot|can'?t|won'?t|will\s+not)\s+(?:offer\s+|provide\s+)?(?:visa\s+)?sponsor"
    + r"|without\s+(?:visa\s+)?sponsorship|sponsorship\s+(?:is\s+)?not\s+(?:available|offered|provided)",
    re.IGNORECASE)
_POSITIVE = re.compile(
    r"visa\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered|possible)|(?:we|company)\s+(?:will\s+)?(?:offer\s+|provide\s+)?"
    r"(?:visa\s+)?sponsor|(?:offers?|provides?|providing|offering)\s+(?:visa\s+)?sponsorship|sponsorship\s+(?:is\s+)?"
    r"(?:available|provided|offered)|will\s+sponsor|able\s+to\s+sponsor|relocation\s+and\s+visa|visa\s+support|h-?1b\s+sponsor",
    re.IGNORECASE)

_LEVEL_ORDER = [ExperienceLevel.INTERNSHIP.value, ExperienceLevel.ENTRY.value, ExperienceLevel.MID.value,
                ExperienceLevel.SENIOR.value, ExperienceLevel.LEAD.value, ExperienceLevel.EXECUTIVE.value]
_SYNONYMS = {"engineer": {"developer", "engineering", "swe"}, "developer": {"engineer"}, "intern": {"internship", "co-op", "coop"}}
_FILLER = {"of", "and", "the", "i", "ii", "iii"}


@dataclass(frozen=True)
class Reason:
    factor: str
    points: int
    max_points: int
    note: str


@dataclass
class Ranked:
    job: ScrapedJob
    score: int
    reasons: list[Reason]
    visa_status: VisaStatus
    visa_evidence: str | None


def _snippet(text: str, m: re.Match[str]) -> str:
    start, end = max(0, m.start() - 60), min(len(text), m.end() + 80)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def detect_sponsorship(text: str) -> tuple[VisaStatus, str | None]:
    """What a posting says about visa sponsorship, with the sentence fragment that says it. "No" wins over "yes"."""
    text = text or ""
    if m := _NEGATIVE.search(text):
        return "no_sponsorship", _snippet(text, m)
    if m := _POSITIVE.search(text):
        return "sponsors", _snippet(text, m)
    return "unknown", None


def _token_ratio(target: str, title: str) -> float:
    tokens = [t for t in re.findall(r"[a-z0-9+#]+", target) if t not in _FILLER]
    title_tokens = set(re.findall(r"[a-z0-9+#]+", title))
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t in title_tokens or _SYNONYMS.get(t, set()) & title_tokens)
    return hits / len(tokens)


def _title_points(job: ScrapedJob, query: SearchQuery) -> Reason:
    cap = WEIGHTS["title"]
    roles = [r for r in query.keywords if r and r.strip()]
    if not roles:
        return Reason("title", 20, cap, "no role filter")
    title = normalize_text(job.role_title)
    best_ratio, best_role = 0.0, roles[0]
    for role in roles:
        target = normalize_text(role)
        if target and target in title:
            return Reason("title", cap, cap, f"Title contains '{role}'")
        ratio = _token_ratio(target, title)
        if _role_matches(target, title):
            ratio = max(ratio, 0.67)
        if ratio > best_ratio:
            best_ratio, best_role = ratio, role
    if best_ratio >= 0.67:
        return Reason("title", 28, cap, f"Title is close to '{best_role}'")
    if best_ratio >= 0.34:
        return Reason("title", 14, cap, f"Title partly matches '{best_role}'")
    return Reason("title", 0, cap, "Title does not match your roles")


def _location_points(job: ScrapedJob, query: SearchQuery) -> Reason:
    cap = WEIGHTS["location"]
    loc = normalize_text(job.location or "")
    wants_remote = query.remote or any(normalize_text(x) == "remote" for x in query.locations)
    if wants_remote and (job.is_remote or "remote" in loc):
        return Reason("location", cap, cap, "Remote")
    targets = [normalize_text(x) for x in query.locations if x and normalize_text(x) != "remote"]
    if not targets:
        return Reason("location", 14, cap, "no location filter")
    if not loc:
        return Reason("location", 10, cap, "Location not listed")
    for target in targets:
        head = target.split(",")[0].strip()
        if head and head in loc:
            return Reason("location", cap, cap, f"In {job.location}")
    return Reason("location", 2, cap, f"{job.location} is outside your locations")


def _seniority_points(job: ScrapedJob, query: SearchQuery) -> Reason:
    cap = WEIGHTS["seniority"]
    level = job.experience_level or infer_experience_level(job.role_title, job.description or "")
    value = level.value if level is not None else ""
    wanted = [s.lower() for s in query.seniority if s]
    if not wanted:
        return Reason("seniority", 10, cap, "no seniority filter")
    if value in wanted:
        return Reason("seniority", cap, cap, f"{value} level")
    if value in _LEVEL_ORDER:
        idx = _LEVEL_ORDER.index(value)
        if any(w in _LEVEL_ORDER and abs(_LEVEL_ORDER.index(w) - idx) == 1 for w in wanted):
            return Reason("seniority", 8, cap, f"{value} level is next to {', '.join(wanted)}")
    return Reason("seniority", 0, cap, f"{value or 'unknown'} level, you asked for {', '.join(wanted)}")


def _keyword_points(job: ScrapedJob, query: SearchQuery) -> Reason:
    cap = WEIGHTS["keywords"]
    skills = [s for s in query.skills if s and s.strip()]
    if not skills:
        return Reason("keywords", 8, cap, "no keywords given")
    hay = normalize_text(f"{job.role_title} {(job.description or '')[:4000]}")
    found = [s for s in skills if re.search(rf"(?<![a-z0-9+#]){re.escape(normalize_text(s))}(?![a-z0-9+#])", hay)]
    points = round(cap * len(found) / len(skills))
    return Reason("keywords", points, cap, f"{len(found)} of {len(skills)} keywords: {', '.join(found) or 'none'}")


def _recency_points(job: ScrapedJob, today: date) -> Reason:
    cap = WEIGHTS["recency"]
    if job.posted_date is None:
        return Reason("recency", 4, cap, "Posting date unknown")
    days = (today - job.posted_date).days
    for limit, points in ((3, 10), (7, 8), (14, 6), (30, 4), (90, 2)):
        if days <= limit:
            return Reason("recency", points, cap, "Posted today" if days <= 0 else f"Posted {days} day{'s' if days != 1 else ''} ago")
    return Reason("recency", 0, cap, f"Posted {days} days ago")


def score_job(job: ScrapedJob, query: SearchQuery, today: date | None = None) -> Ranked:
    today = today or datetime.now(UTC).date()
    raw = job.raw or {}
    status, evidence = detect_sponsorship(f"{raw.get('sponsorship') or ''} {job.role_title} {(job.description or '')[:6000]}")
    cap = WEIGHTS["visa"]
    if not query.needs_sponsorship:
        visa = Reason("visa", cap, cap, "Visa not needed")
    elif status == "sponsors":
        visa = Reason("visa", cap, cap, "Mentions visa sponsorship")
    elif status == "unknown":
        visa = Reason("visa", 2, cap, "Sponsorship not mentioned")
    else:
        visa = Reason("visa", 0, cap, "Says it does not sponsor visas")
    reasons = [_title_points(job, query), _location_points(job, query), _seniority_points(job, query),
               _keyword_points(job, query), _recency_points(job, today), visa]
    score = max(0, min(100, sum(r.points for r in reasons)))
    return Ranked(job=job, score=score, reasons=reasons, visa_status=status, visa_evidence=evidence)


def rank_jobs(jobs: Iterable[ScrapedJob], query: SearchQuery, today: date | None = None, *,
              drop_no_sponsorship: bool = True) -> list[Ranked]:
    """Score and sort (best first; ties: newest posting, then title). Jobs that refuse sponsorship are dropped when the user needs it."""
    ranked = [score_job(job, query, today) for job in jobs]
    if drop_no_sponsorship and query.needs_sponsorship:
        ranked = [r for r in ranked if r.visa_status != "no_sponsorship"]
    ranked.sort(key=lambda r: (-r.score, -(r.job.posted_date.toordinal() if r.job.posted_date else 0), normalize_text(r.job.role_title)))
    return ranked


def attach_match(ranked: Ranked) -> None:
    """Write the score, reasons and sponsorship evidence into ``job.raw`` (stored in ``Job.raw_data``)."""
    job = ranked.job
    raw = dict(job.raw or {})
    raw["match"] = {"score": ranked.score,
                    "reasons": [{"factor": r.factor, "points": r.points, "max": r.max_points, "note": r.note} for r in ranked.reasons]}
    if ranked.visa_evidence:
        evidence = ranked.visa_evidence
        if ranked.visa_status == "no_sponsorship" and not NO_SPONSORSHIP_PATTERNS.search(evidence):
            evidence = f"No visa sponsorship: {evidence}"  # so job_matcher.filter_reasons reads it as a refusal
        raw["sponsorship"] = evidence
    job.raw = raw
