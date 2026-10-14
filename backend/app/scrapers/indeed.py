"""Indeed search via a stealth browser (parses the embedded mosaic job-card JSON)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from app.automation.browser import BrowserUnavailable
from app.models.enums import ATSPlatform
from app.scrapers.base import ScrapedJob, ScraperError, SearchQuery, parse_date, parse_salary
from app.scrapers.browser_scraper import BrowserScraper, extract_assigned_json
from app.services.location_focus import is_indian_location

logger = logging.getLogger(__name__)

BASE = "https://www.indeed.com"
INDIA_BASE = "https://in.indeed.com"


def base_for(location: str) -> str:
    """Indeed runs one site per country; Indian locations are searched on in.indeed.com."""
    return INDIA_BASE if is_indian_location(location) else BASE


def parse_job_cards(html: str) -> list[dict[str, Any]]:
    data = extract_assigned_json(html, 'window.mosaic.providerData["mosaic-provider-jobcards"]')
    if not data:
        return []
    model = (data.get("metaData") or {}).get("mosaicProviderJobCardsModel") or {}
    return model.get("results") or []


def parse_description(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one("#jobDescriptionText, [data-testid='jobsearch-JobComponent-description']")
    return node.decode_contents() if node else ""


class IndeedScraper(BrowserScraper):
    platform = ATSPlatform.INDEED
    rate_key = "indeed"

    def card_to_job(self, card: dict[str, Any], description: str, base: str = BASE) -> ScrapedJob:
        salary = (card.get("salarySnippet") or {}).get("text") or ""
        parsed_salary = parse_salary(salary)
        jk = card.get("jobkey")
        url = f"{base}/viewjob?jk={jk}"
        third_party = card.get("thirdPartyApplyUrl")
        return ScrapedJob(
            company_name=card.get("company") or "Unknown",
            role_title=card.get("displayTitle") or card.get("title") or "",
            description=description or card.get("snippet") or "",
            source_url=url,
            application_url=third_party or url,
            source_platform=ATSPlatform.INDEED,
            location=card.get("formattedLocation"),
            is_remote=bool(card.get("remoteLocation")),
            salary_min=parsed_salary[0] if parsed_salary else None,
            salary_max=parsed_salary[1] if parsed_salary else None,
            salary_currency=parsed_salary[2] if parsed_salary else None,
            posted_date=parse_date(card.get("pubDate")),
            external_id=jk,
            easy_apply=bool(card.get("indeedApplyEnabled")),
            raw={"third_party_apply_url": third_party},
        ).finalize()

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        try:
            with self.open_session() as session:
                for keyword in query.keywords or [""]:
                    for location in query.locations or ["remote" if query.remote else ""]:
                        base = base_for(location)
                        url = f"{base}/jobs?q={quote_plus(keyword)}&l={quote_plus(location)}&fromage={min(query.posted_within_days or 14, 14)}"
                        if query.remote:
                            url += "&sc=0kf%3Aattr%28DSQF7%29%3B"
                        cards = parse_job_cards(self.browser_get(session, url, "#mosaic-provider-jobcards"))
                        for card in cards:
                            if len(jobs) >= query.limit or query.time_up():
                                break
                            if not query.matches_title(card.get("displayTitle") or ""):
                                continue
                            detail_url = f"{base}/viewjob?jk={card.get('jobkey')}"
                            if query.is_known(detail_url):  # already saved: no need to open it again
                                description = ""
                            else:
                                try:
                                    description = parse_description(self.browser_get(session, detail_url, "#jobDescriptionText"))
                                except ScraperError:
                                    description = ""
                            jobs.append(self.card_to_job(card, description, base))
        except BrowserUnavailable as exc:
            raise ScraperError(str(exc)) from exc
        return self.filter(jobs, query)
