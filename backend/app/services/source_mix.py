"""Keep Internshala from crowding out everything else.

Internshala returns far more postings than any other source, and many come from small or unknown companies,
so left alone they fill the whole deck. Each scan adds at most ``internshala_per_scan`` (default 10) new
Internshala postings, and no more than ``internshala_share`` % (default 25 %) of its new postings: one for
every three from elsewhere. The ones kept are the best: renowned companies first, then postings with no
warning signs, then the prime city. Postings you already have don't count (they're only refreshed).
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from app.scrapers.base import ScrapedJob
from app.services.company_verifier import SUSPICIOUS, VERIFIED, check_job
from app.services.location_focus import location_tier

DEFAULT_SHARE = 25
DEFAULT_PER_SCAN = 10
MIN_KEPT = 3  # a scan where only Internshala answered still shows a few


def is_internshala(job: ScrapedJob) -> bool:
    return (job.raw or {}).get("listing_source") == "internshala" or "internshala.com/" in (job.source_url or "")


def _setting(value: Any, default: int, high: int) -> int:
    try:
        return default if value is None else max(0, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def internshala_allowance(others: int, share: Any = None, per_scan: Any = None) -> int:
    """How many new Internshala postings a scan with ``others`` new postings from elsewhere may add."""
    share, per_scan = _setting(share, DEFAULT_SHARE, 100), _setting(per_scan, DEFAULT_PER_SCAN, 50)
    if share == 0 or per_scan == 0:
        return 0
    by_share = per_scan if share >= 100 else max(MIN_KEPT, others * share // (100 - share))
    return min(per_scan, by_share)


def cap_internshala(jobs: list[ScrapedJob], share: Any = None, focus: Any = None, per_scan: Any = None,
                    known: Collection[str] = ()) -> tuple[list[ScrapedJob], int]:
    """Return (jobs to keep in their original order, how many new Internshala postings were left out).

    ``known``: source URLs you already have; those are kept and don't count toward the limit.
    """
    new = [j for j in jobs if is_internshala(j) and j.source_url not in known]
    if not new:
        return jobs, 0
    others = sum(1 for j in jobs if not is_internshala(j) and j.source_url not in known)
    allowed = internshala_allowance(others, share, per_scan)
    if len(new) <= allowed:
        return jobs, 0

    def rank(job: ScrapedJob) -> tuple[int, int, int]:
        check = check_job(job)
        trust = 0 if check.verdict == VERIFIED else 2 if check.verdict == SUSPICIOUS else 1
        place = location_tier(job.location, job.is_remote, focus) if focus is not None else 0
        return (trust, -check.score, place)

    keep = {id(j) for j in sorted(new, key=rank)[:allowed]}
    kept = [j for j in jobs if not is_internshala(j) or j.source_url in known or id(j) in keep]
    return kept, len(new) - allowed
