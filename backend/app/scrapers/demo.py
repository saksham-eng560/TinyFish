"""The demo job source (DEMO_MODE): the bundled demo careers site's internships, read directly (no network),
so demo scans are fast and nothing outside HireFlow is touched."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import settings
from app.models.enums import ATSPlatform, JobType
from app.scrapers.base import BaseScraper, ScrapedJob, SearchQuery
from app.services import demo_site


class DemoScraper(BaseScraper):
    platform = ATSPlatform.CUSTOM
    rate_key = "demo"

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        base = settings.demo_site_url
        jobs = []
        for post in demo_site.POSTINGS:
            city, country = post["location"]
            url = f"{base}/jobs/{post['id']}"
            jobs.append(ScrapedJob(
                company_name=post["company"], role_title=post["title"], description=post["description"],
                source_url=url, application_url=url, source_platform=ATSPlatform.CUSTOM,
                location=f"{city}, {country}" if city != "Remote" else f"Remote, {country}", is_remote=post["remote"],
                job_type=JobType.INTERNSHIP, salary_min=post["stipend"], salary_max=post["stipend"], salary_currency="INR",
                posted_date=(datetime.now(UTC) - timedelta(days=post["days_ago"])).date(), external_id=post["id"],
                raw={"listing_source": "demo", "demo": True, "terms": ["Summer 2027"]},
            ).finalize())
        return jobs[: max(query.limit, len(jobs))]

    def fetch_job(self, url: str) -> ScrapedJob | None:
        return None
