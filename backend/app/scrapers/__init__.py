"""Scraper registry."""

from __future__ import annotations

import logging

from app.config import settings
from app.models.enums import ATSPlatform
from app.scrapers.ashby import AshbyScraper
from app.scrapers.ats_detect import detect_ats_platform, parse_ats_url
from app.scrapers.base import BaseScraper, RateLimited, ScrapedJob, ScraperError, SearchQuery
from app.scrapers.demo import DemoScraper
from app.scrapers.generic import GenericScraper
from app.scrapers.glassdoor import GlassdoorScraper
from app.scrapers.greenhouse import GreenhouseScraper
from app.scrapers.indeed import IndeedScraper
from app.scrapers.internshala import InternshalaScraper
from app.scrapers.internships import InternshipListScraper
from app.scrapers.lever import LeverScraper
from app.scrapers.linkedin import LinkedInScraper
from app.scrapers.tinyfish import TinyFishScraper
from app.scrapers.tinyfish_parse import is_gated_url
from app.scrapers.top_companies import TopCompaniesScraper
from app.scrapers.wellfound import WellfoundScraper
from app.scrapers.workday import WorkdayScraper

logger = logging.getLogger(__name__)

SCRAPERS: dict[str, type[BaseScraper]] = {
    "top_companies": TopCompaniesScraper,
    "internshala": InternshalaScraper,
    "internships": InternshipListScraper,
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
    "ashby": AshbyScraper,
    "workday": WorkdayScraper,
    "linkedin": LinkedInScraper,
    "indeed": IndeedScraper,
    "glassdoor": GlassdoorScraper,
    "wellfound": WellfoundScraper,
    "generic": GenericScraper,
    "tinyfish": TinyFishScraper,  # Search -> Fetch -> Agent; added to scans only when TINYFISH_API_KEY is set
    "demo": DemoScraper,  # the bundled demo careers site (DEMO_MODE)
}

PLATFORM_TO_SCRAPER = {
    ATSPlatform.GREENHOUSE: "greenhouse",
    ATSPlatform.LEVER: "lever",
    ATSPlatform.ASHBY: "ashby",
    ATSPlatform.WORKDAY: "workday",
    ATSPlatform.LINKEDIN: "linkedin",
}


def get_scraper(name: str) -> BaseScraper:
    return SCRAPERS[name]()


def _scraper_name(url: str) -> str:
    name = PLATFORM_TO_SCRAPER.get(detect_ats_platform(url), "generic")
    return "internshala" if "internshala.com/" in url else name


def _fetch_job_plain(url: str) -> ScrapedJob | None:
    name = _scraper_name(url)
    job = get_scraper(name).fetch_job(url)
    if job is None and name != "generic":
        job = get_scraper("generic").fetch_job(url)
    return job


# A generic page this short is probably a login wall or an empty app shell
THIN_DESCRIPTION_CHARS = 200
TINYFISH_NEVER = (ATSPlatform.LINKEDIN, ATSPlatform.INDEED, ATSPlatform.GLASSDOOR)


def fetch_job_from_url(url: str) -> ScrapedJob | None:
    """Import a single posting from any supported URL (used by "Add job by URL").

    With a TinyFish key, a page the plain scrapers can't read (blocked, JavaScript-only, empty) is retried
    through TinyFish Fetch, then the Agent; the better of the two results is kept.
    """
    error: ScraperError | None = None
    try:
        job = _fetch_job_plain(url)
    except ScraperError as exc:
        job, error = None, exc
    thin = job is None or (_scraper_name(url) == "generic" and len(job.description or "") < THIN_DESCRIPTION_CHARS)
    # (a 429 / captcha is a stop sign, not something to route around through TinyFish)
    if thin and settings.tinyfish_enabled and not isinstance(error, RateLimited) and not is_gated_url(url) \
            and detect_ats_platform(url) not in TINYFISH_NEVER:
        try:
            better = TinyFishScraper().fetch_job(url)
        except ScraperError as exc:
            logger.info("TinyFish could not read %s: %s", url, exc)
            better = None
        if better is not None and (job is None or len(better.description or "") > len(job.description or "")):
            job = better
    if error is not None and job is None:
        raise error
    return job


__all__ = [
    "SCRAPERS",
    "BaseScraper",
    "RateLimited",
    "ScrapedJob",
    "ScraperError",
    "SearchQuery",
    "detect_ats_platform",
    "fetch_job_from_url",
    "get_scraper",
    "parse_ats_url",
]
