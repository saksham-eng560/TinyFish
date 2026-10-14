"""Wellfound (AngelList Talent) startup jobs via a stealth browser (__NEXT_DATA__ Apollo state)."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.automation.browser import BrowserUnavailable
from app.models.enums import ATSPlatform, JobType
from app.scrapers.base import ScrapedJob, ScraperError, SearchQuery, parse_date, parse_salary
from app.scrapers.browser_scraper import BrowserScraper, next_data

logger = logging.getLogger(__name__)

BASE = "https://wellfound.com"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_apollo_jobs(data: Any) -> list[dict[str, Any]]:
    """Walk the Apollo cache and join JobListing nodes with their Startup."""
    state = (((data or {}).get("props") or {}).get("pageProps") or {}).get("apolloState") or {}
    graph = state.get("data") or state
    jobs = []
    for key, node in graph.items():
        if not isinstance(node, dict) or "JobListing" not in str(node.get("__typename") or key):
            continue
        startup_ref = (node.get("startup") or {}).get("__ref") if isinstance(node.get("startup"), dict) else None
        startup = graph.get(startup_ref, {}) if startup_ref else {}
        jobs.append(
            {
                "id": node.get("id"),
                "title": node.get("title") or "",
                "slug": node.get("slug") or _slug(node.get("title") or ""),
                "description": node.get("description") or node.get("descriptionSnippet") or "",
                "locations": node.get("locationNames") or [],
                "remote": bool(node.get("remote")),
                "compensation": node.get("compensation") or "",
                "job_type": node.get("jobType") or "",
                "posted": node.get("liveStartAt") or node.get("postedAt"),
                "company": startup.get("name") or "",
                "company_slug": startup.get("slug") or "",
                "logo": startup.get("logoUrl"),
            }
        )
    return jobs


class WellfoundScraper(BrowserScraper):
    platform = ATSPlatform.WELLFOUND
    rate_key = "wellfound"

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        try:
            with self.open_session() as session:
                for keyword in query.keywords or ["software engineer"]:
                    locations = query.locations or ["remote" if query.remote else ""]
                    for location in locations:
                        url = f"{BASE}/role/r/{_slug(keyword)}" if not location else f"{BASE}/role/l/{_slug(keyword)}/{_slug(location)}"
                        html = self.browser_get(session, url, "[data-test='StartupResult']")
                        for item in parse_apollo_jobs(next_data(html)):
                            if len(jobs) >= query.limit or query.time_up():
                                break
                            if not query.matches_title(item["title"]):
                                continue
                            sal = parse_salary(item["compensation"])
                            job_url = f"{BASE}/jobs/{item['id']}-{item['slug']}"
                            jobs.append(
                                ScrapedJob(
                                    company_name=item["company"] or "Startup",
                                    role_title=item["title"],
                                    description=item["description"] or item["title"],
                                    source_url=job_url,
                                    application_url=job_url,
                                    source_platform=ATSPlatform.WELLFOUND,
                                    location=", ".join(item["locations"]) or None,
                                    is_remote=item["remote"],
                                    job_type=JobType.INTERNSHIP if "intern" in item["job_type"].lower() else None,
                                    salary_min=sal[0] if sal else None,
                                    salary_max=sal[1] if sal else None,
                                    salary_currency=sal[2] if sal else None,
                                    posted_date=parse_date(item["posted"]),
                                    external_id=str(item["id"]),
                                    company_logo_url=item["logo"],
                                    easy_apply=True,
                                ).finalize()
                            )
        except BrowserUnavailable as exc:
            raise ScraperError(str(exc)) from exc
        return self.filter(jobs, query)
