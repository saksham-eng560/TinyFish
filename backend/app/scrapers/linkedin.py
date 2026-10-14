"""LinkedIn Jobs via the public guest endpoints (no login needed for discovery).

Easy Apply submissions use the user's synced ``li_at`` session cookie (see submitters/linkedin_easy_apply.py).
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from app.models.enums import ATSPlatform, ExperienceLevel, JobType
from app.scrapers.ats_detect import parse_ats_url
from app.scrapers.base import BaseScraper, RateLimited, ScrapedJob, ScraperError, SearchQuery, parse_date

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
EMPLOYMENT = {"full-time": JobType.FULL_TIME, "part-time": JobType.PART_TIME, "internship": JobType.INTERNSHIP,
              "contract": JobType.CONTRACT, "temporary": JobType.CONTRACT}
SENIORITY = {"internship": ExperienceLevel.INTERNSHIP, "entry level": ExperienceLevel.ENTRY,
             "associate": ExperienceLevel.ENTRY, "mid-senior level": ExperienceLevel.SENIOR,
             "director": ExperienceLevel.LEAD, "executive": ExperienceLevel.EXECUTIVE}
JOB_TYPE_FILTER = {"full-time": "F", "part-time": "P", "contract": "C", "internship": "I", "freelance": "T"}


def parse_search_results(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    results = []
    for card in soup.select("div.base-card, div.job-search-card, li > div[data-entity-urn]"):
        urn = card.get("data-entity-urn") or ""
        m = re.search(r"(\d+)$", urn)
        link = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        href = (link.get("href") if link else "") or ""
        if not m:
            m = re.search(r"/jobs/view/(?:[^/?]*-)?(\d+)", href)
        if not m:
            continue
        title = card.select_one(".base-search-card__title, h3")
        company = card.select_one(".base-search-card__subtitle, h4")
        location = card.select_one(".job-search-card__location")
        posted = card.select_one("time")
        logo = card.select_one("img[data-delayed-url], img.artdeco-entity-image")
        results.append(
            {
                "id": m.group(1),
                "title": title.get_text(strip=True) if title else "",
                "company": company.get_text(strip=True) if company else "",
                "location": location.get_text(strip=True) if location else "",
                "posted": (posted.get("datetime") or posted.get_text(strip=True)) if posted else None,
                "url": href.split("?")[0] if href else f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
                "logo": (logo.get("data-delayed-url") or logo.get("src")) if logo else None,
            }
        )
    return results


def parse_job_detail(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    desc = soup.select_one(".show-more-less-html__markup, .description__text")
    criteria: dict[str, str] = {}
    for item in soup.select(".description__job-criteria-item"):
        header = item.select_one(".description__job-criteria-subheader")
        value = item.select_one(".description__job-criteria-text")
        if header and value:
            criteria[header.get_text(strip=True).lower()] = value.get_text(strip=True).lower()
    apply_url = None
    code = soup.select_one("code#applyUrl")
    if code:
        text = code.decode_contents()
        m = re.search(r'"(https?://[^"]+)"', text)
        if m:
            apply_url = m.group(1).replace("\\u0026", "&")
            m2 = re.search(r"[?&]url=([^&]+)", apply_url)
            if m2 and "linkedin.com" in apply_url:
                from urllib.parse import unquote

                apply_url = unquote(m2.group(1))
    easy_apply = apply_url is None and bool(
        soup.select_one("[data-tracking-control-name*='apply'], button.sign-up-modal__outlet, .apply-button")
    )
    title = soup.select_one(".top-card-layout__title, h2")
    company = soup.select_one(".topcard__org-name-link, .top-card-layout__second-subline a")
    location = soup.select_one(".topcard__flavor--bullet")
    return {
        "description": desc.decode_contents() if desc else "",
        "criteria": criteria,
        "apply_url": apply_url,
        "easy_apply": easy_apply,
        "title": title.get_text(strip=True) if title else None,
        "company": company.get_text(strip=True) if company else None,
        "location": location.get_text(strip=True) if location else None,
    }


class LinkedInScraper(BaseScraper):
    platform = ATSPlatform.LINKEDIN
    rate_key = "linkedin"

    def _build_job(self, summary: dict[str, Any], detail: dict[str, Any]) -> ScrapedJob:
        criteria = detail.get("criteria") or {}
        employment = criteria.get("employment type", "")
        seniority = criteria.get("seniority level", "")
        apply_url = detail.get("apply_url")
        source_url = f"https://www.linkedin.com/jobs/view/{summary['id']}/"
        return ScrapedJob(
            company_name=summary.get("company") or detail.get("company") or "Unknown",
            role_title=summary.get("title") or detail.get("title") or "",
            description=detail.get("description") or "",
            source_url=source_url,
            application_url=apply_url or source_url,
            source_platform=ATSPlatform.LINKEDIN,
            location=summary.get("location") or detail.get("location"),
            job_type=next((v for k, v in EMPLOYMENT.items() if k in employment), None),
            experience_level=next((v for k, v in SENIORITY.items() if k in seniority), None),
            posted_date=parse_date(summary.get("posted")),
            external_id=summary["id"],
            easy_apply=bool(detail.get("easy_apply")),
            company_logo_url=summary.get("logo"),
            raw={"criteria": criteria, "external_apply_url": apply_url,
                 "external_platform": parse_ats_url(apply_url).platform.value if apply_url else None},
        ).finalize()

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        seen: set[str] = set()
        keywords = query.search_terms or query.keywords or [""]
        locations = query.locations or ([""] if not query.remote else ["United States"])
        days = max(1, min(query.posted_within_days or 7, 30))
        for keyword in keywords:
            for location in locations:
                params = f"keywords={quote_plus(keyword)}&location={quote_plus(location)}&f_TPR=r{days * 86400}"
                if query.remote:
                    params += "&f_WT=2"
                types = [JOB_TYPE_FILTER[t] for t in query.job_types if t in JOB_TYPE_FILTER]
                if types:
                    params += "&f_JT=" + "%2C".join(types)
                for start in range(0, min(query.limit, 100), 25):
                    if query.time_up():
                        return self.filter(jobs, query)
                    try:
                        resp = self.request("GET", f"{SEARCH_URL}?{params}&start={start}")
                    except RateLimited:
                        return self.filter(jobs, query)
                    if resp.status_code != 200 or not resp.text.strip():
                        break
                    summaries = parse_search_results(resp.text)
                    if not summaries:
                        break
                    fresh = []
                    for summary in summaries:
                        if summary["id"] in seen or not query.matches_title(summary["title"]):
                            continue
                        seen.add(summary["id"])
                        fresh.append(summary)
                    fresh = fresh[: max(0, query.limit - len(jobs))]
                    details, limited = self._details(fresh, query)
                    for summary in fresh:
                        detail = details.get(summary["id"]) or {}
                        if detail.get("description") or detail.get("known"):
                            jobs.append(self._build_job(summary, detail))
                    if limited or len(jobs) >= query.limit:
                        return self.filter(jobs, query)
        return self.filter(jobs, query)

    def _details(self, summaries: list[dict[str, Any]], query: SearchQuery) -> tuple[dict[str, dict[str, Any]], bool]:
        """Job details for a page of results, three at a time. Postings already saved aren't downloaded again."""
        details: dict[str, dict[str, Any]] = {}
        todo = []
        for summary in summaries:
            if query.is_known(f"https://www.linkedin.com/jobs/view/{summary['id']}/"):
                details[summary["id"]] = {"known": True}
            else:
                todo.append(summary)
        limited = False

        def fetch(job_id: str) -> dict[str, Any]:
            resp = self.request("GET", DETAIL_URL.format(job_id=job_id))
            return parse_job_detail(resp.text) if resp.status_code == 200 else {}

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="scrape-linkedin") as pool:
            futures = {pool.submit(fetch, s["id"]): s["id"] for s in todo}
            for future in as_completed(futures):
                try:
                    details[futures[future]] = future.result()
                except RateLimited:
                    limited = True
                except ScraperError:
                    details[futures[future]] = {}
        return details, limited

    def fetch_job(self, url: str) -> ScrapedJob | None:
        ref = parse_ats_url(url)
        if not ref.job_id:
            return None
        resp = self.request("GET", DETAIL_URL.format(job_id=ref.job_id))
        if resp.status_code != 200:
            return None
        detail = parse_job_detail(resp.text)
        return self._build_job({"id": ref.job_id, "title": detail.get("title"), "company": detail.get("company"),
                                "location": detail.get("location")}, detail)
