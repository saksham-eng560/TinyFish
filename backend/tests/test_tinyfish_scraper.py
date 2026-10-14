"""TinyFish source tests (Search -> Fetch -> Agent): every call is mocked with respx, so nothing here can reach the network."""

import copy
import json
import threading
import time
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.enums import ATSPlatform
from app.models.user import User
from app.scrapers import SCRAPERS, ScrapedJob, ScraperError, SearchQuery, fetch_job_from_url
from app.scrapers.base import RateLimited
from app.scrapers.generic import GenericScraper
from app.scrapers.tinyfish import DiscoveryHooks, TinyFishScraper, catalog_gap_companies
from app.services import agent_orchestrator as orch
from app.services import tinyfish_client as tf
from app.services.rate_limiter import rate_limiter
from app.services.scan_progress import ScanProgress
from tests.conftest import FIXTURES

FX = FIXTURES / "tinyfish"
SCRAPER_FX = FIXTURES / "scrapers"
KEY = "tf-test-key-0123456789abcdef"
SEARCH = "https://api.search.tinyfish.ai/"
FETCH = "https://api.fetch.tinyfish.ai/"
SSE = "https://agent.tinyfish.ai/v1/automation/run-sse"
GH = "https://boards-api.greenhouse.io/v1/boards"
LEVER_ID = "a1b2c3d4-0000-4000-8000-000000000001"
ROLES = ["Backend Engineer", "Software Engineer", "Platform Engineer"]


def fxj(name: str, base: Any = FX) -> Any:
    return json.loads((base / name).read_text())


def query(**kw: Any) -> SearchQuery:
    kw.setdefault("keywords", ROLES)
    kw.setdefault("limit", 50)
    return SearchQuery(posted_within_days=3650, **kw)


@pytest.fixture(autouse=True)
def _tinyfish_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A key for every test (conftest blanks it); backoff sleeps are skipped."""
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", KEY)
    monkeypatch.setattr(tf, "_sleep", lambda seconds: None)
    yield


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


# --------------------------------------------------------------------------- mock helpers
def route_search(mock: respx.MockRouter, body: Any, log: list[dict[str, str]] | None = None) -> respx.Route:
    def respond(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(dict(request.url.params))
        return httpx.Response(200, json=body)

    return mock.get(SEARCH).mock(side_effect=respond)


def route_fetch(mock: respx.MockRouter, fixtures: list[str] | None = None, log: list[dict[str, Any]] | None = None,
                failures: dict[str, str] | None = None, default_failure: str = "page_not_found",
                rows: list[dict[str, Any]] | None = None) -> respx.Route:
    """Fetch answers from the fixture pages; every URL it doesn't know fails with ``failures[url]`` / ``default_failure``."""
    pool = {row["url"]: row for name in fixtures or [] for row in fxj(name)["results"]}
    pool.update({row["url"]: row for row in rows or []})

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if log is not None:
            log.append(body)
        found = [pool[u] for u in body["urls"] if u in pool]
        errors = [{"url": u, "error": (failures or {}).get(u, default_failure), "status": None} for u in body["urls"] if u not in pool]
        return httpx.Response(200, json={"results": found, "errors": errors})

    return mock.post(FETCH).mock(side_effect=respond)


def page_row(url: str, text: str, fmt: str = "html") -> dict[str, Any]:
    return {"url": url, "final_url": url, "title": "", "format": fmt, "text": text, "links": [], "latency_ms": 100.0}


def sse(*events: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content="".join(f"data: {json.dumps(e)}\n\n" for e in events).encode(),
                          headers={"content-type": "text/event-stream"})


def agent_events(jobs: list[dict[str, Any]] | None, run_id: str = "run_1", progress: tuple[str, ...] = ("Opening the page",),
                 steps: int = 4, error: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"type": "STARTED", "run_id": run_id},
                                 {"type": "STREAMING_URL", "run_id": run_id, "streaming_url": f"https://stream.tinyfish.example/{run_id}"}]
    out += [{"type": "PROGRESS", "run_id": run_id, "purpose": text} for text in progress]
    out.append({"type": "COMPLETE", "run_id": run_id, "status": "FAILED" if error else "COMPLETED", "num_of_steps": steps,
                "result": None if error else {"company": "Acme", "jobs": jobs or []}, "error": error})
    return out


def jobs_for(url: str, n: int = 1) -> list[dict[str, Any]]:
    """Agent job rows whose apply URLs are on the page's own site (the only kind agent_jobs_to_scraped accepts)."""
    host = httpx.URL(url).host
    return [{"title": f"Backend Engineer {host.split('.')[0]} {i}", "apply_url": f"https://{host}/careers/jobs/backend-engineer-{i}-{i}0",
             "location": "Remote", "visa_sponsorship": "unknown"} for i in range(1, n + 1)]


def route_agent(mock: respx.MockRouter, log: list[dict[str, Any]], respond: Callable[[dict[str, Any]], httpx.Response] | None = None) -> respx.Route:
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        log.append(payload)
        return respond(payload) if respond else sse(*agent_events(jobs_for(payload["url"]), run_id=f"run_{len(log)}"))

    return mock.post(SSE).mock(side_effect=handle)


def route_ats(mock: respx.MockRouter) -> None:
    """Greenhouse (stripe board + posting 7001), Lever (acme posting) and Ashby (acme-labs board), from the scraper fixtures."""
    mock.get(f"{GH}/stripe/jobs").mock(return_value=httpx.Response(200, json=fxj("greenhouse_jobs.json", SCRAPER_FX)))
    mock.get(f"{GH}/stripe").mock(return_value=httpx.Response(200, json=fxj("greenhouse_board.json", SCRAPER_FX)))
    mock.get(f"{GH}/stripe/jobs/7001").mock(return_value=httpx.Response(200, json=fxj("greenhouse_job_questions.json", SCRAPER_FX)))
    mock.get(f"https://api.lever.co/v0/postings/acme/{LEVER_ID}").mock(
        return_value=httpx.Response(200, json=fxj("lever_postings.json", SCRAPER_FX)[0]))
    mock.get("https://api.ashbyhq.com/posting-api/job-board/acme-labs").mock(
        return_value=httpx.Response(200, json=fxj("ashby_board.json", SCRAPER_FX)))


def agent_only_query(n: int, **kw: Any) -> SearchQuery:
    """n career pages of the user's own, no Search: whatever Fetch can't read goes to the Agent."""
    return SearchQuery(keywords=[], posted_within_days=3650, sources={"career_pages": [f"https://site{i}.example/careers" for i in range(n)]}, **kw)


# =========================================================================== the pipeline
def mocked_pipeline(mock: respx.MockRouter) -> SimpleNamespace:
    net = SimpleNamespace(searches=[], fetches=[], search=None, fetch=None)
    net.search = route_search(mock, fxj("pipeline_search.json"), net.searches)
    net.fetch = route_fetch(mock, ["pipeline_fetch_careers.json", "pipeline_fetch_postings.json"], net.fetches)
    route_ats(mock)
    return net


def test_pipeline_finds_dedupes_and_reads_each_kind_of_page(mock: respx.MockRouter) -> None:
    net = mocked_pipeline(mock)
    result = TinyFishScraper().discover(query(), include_catalog_gaps=True)

    by_title = {j.role_title: j for j in result.jobs}
    assert {"Backend Engineer, Payments", "Senior Software Engineer, Platform", "Backend Engineer Intern", "Software Engineer, Backend",
            "Platform Engineer"} <= set(by_title)
    # The same posting reached three ways (the board, its own address, a custom-site copy) is one job: the ATS API's copy
    payments = [j for j in result.jobs if j.role_title == "Backend Engineer, Payments"]
    assert len(payments) == 1 and payments[0].source_platform == ATSPlatform.GREENHOUSE and payments[0].raw["board_token"] == "stripe"
    assert payments[0].raw["tinyfish"]["via"] == ["search", "ats_api"]
    assert len({j.source_url for j in result.jobs}) == len(result.jobs)
    assert result.counts["duplicates"] == 2  # by address (board vs posting) and by company + title + place (custom-site copy)
    # Board root -> Greenhouse API; careers page with an Ashby link -> Ashby API; posting -> Fetch html (JSON-LD); job link -> Fetch html
    assert result.counts["boards_delegated"] == 2
    assert by_title["Software Engineer, Backend"].raw["tinyfish"]["via"] == ["search", "fetch", "ats_api"]
    intern = by_title["Backend Engineer Intern"]
    assert intern.company_name == "Initech" and intern.source_url == "https://initech.example/careers/jobs/backend-engineer-intern-123"
    assert intern.raw["tinyfish"]["via"] == ["search", "fetch"] and intern.raw["tinyfish"]["query"]
    assert by_title["Platform Engineer"].company_name == "Globex" and by_title["Platform Engineer"].raw["tinyfish"]["via"] == ["search", "fetch"]
    assert by_title["Senior Software Engineer, Platform"].raw["tinyfish"]["via"] == ["search", "ats_api"]
    assert all(j.raw["discovered_by"] == "tinyfish" for j in result.jobs)
    assert "Account Executive" not in by_title  # the board's other jobs are filtered by title
    # Usage: free ATS APIs cost no Fetch credit
    fetched = [u for body in net.fetches for u in body["urls"]]
    assert not any("greenhouse" in u or "lever.co" in u or "ashbyhq" in u for u in fetched)
    assert result.usage["search_calls"] == len(net.searches) and result.usage["fetch_urls"] == len(fetched) == 5
    assert result.usage["agent_runs"] == 0 and result.agent_runs == []
    assert all(body["ttl"] == 0 for body in net.fetches)
    assert len([c for c in mock.calls if c.request.url.path.endswith("/stripe/jobs/7001")]) == 1  # each ATS posting is read once


def test_a_gated_hit_is_never_requested(mock: respx.MockRouter) -> None:
    net = mocked_pipeline(mock)
    result = TinyFishScraper().discover(query(), include_catalog_gaps=True)
    assert result.counts["gated_dropped"] == 1  # the LinkedIn hit
    requests = [call.request for call in mock.calls]
    assert not any(r.url.host.endswith("linkedin.com") for r in requests)
    assert not any("linkedin" in u for body in net.fetches for u in body["urls"])
    assert not any("linkedin" in j.source_url or "linkedin" in (j.application_url or "") for j in result.jobs)
    # ...and the Search itself keeps those sites out
    open_pass = [p for p in net.searches if "exclude_domains" in p]
    assert open_pass and all("linkedin.com" in p["exclude_domains"] for p in open_pass)


def test_every_gated_host_family_is_dropped_before_any_fetch_or_agent(mock: respx.MockRouter) -> None:
    gated = ["https://www.linkedin.com/jobs/view/1", "https://in.indeed.com/viewjob?jk=abc", "https://www.glassdoor.co.in/job-listing/x-JV_1.htm",
             "https://internshala.com/internship/detail/backend-intern", "https://lnkd.in/abc"]
    route_search(mock, {"results": [{"position": i, "title": "Backend Engineer", "url": u, "snippet": "", "site_name": ""} for i, u in enumerate(gated, 1)]})
    fetch, agent_log = route_fetch(mock), []
    route_agent(mock, agent_log)
    q = query(sources={"career_pages": ["https://www.linkedin.com/company/acme/jobs", "https://glassdoor.com/Overview/x"]})
    result = TinyFishScraper().discover(q)
    assert result.jobs == [] and result.counts["gated_dropped"] == 7 and result.counts["raw_hits"] > 0
    assert not fetch.called and agent_log == []


def test_search_ranks_and_filters_and_annotates_every_job(mock: respx.MockRouter) -> None:
    mocked_pipeline(mock)
    notes: list[tuple[str | None, dict[str, Any]]] = []
    jobs = TinyFishScraper().search(query(detail=lambda text, extra: notes.append((text, extra))))
    assert jobs and all(j.raw["discovered_by"] == "tinyfish" and 0 <= j.raw["match"]["score"] <= 100 for j in jobs)
    assert all(r["factor"] and "max" in r for j in jobs for r in j.raw["match"]["reasons"])
    scores = [j.raw["match"]["score"] for j in jobs]
    assert scores == sorted(scores, reverse=True)
    assert "Account Executive" not in {j.role_title for j in jobs}
    usage = next(extra["usage"] for _text, extra in reversed(notes) if "usage" in extra)
    assert usage["search_calls"] > 0 and usage["fetch_urls"] == 5


def test_search_applies_the_query_filters(mock: respx.MockRouter) -> None:
    mocked_pipeline(mock)
    assert len(TinyFishScraper().search(query(limit=2))) == 2
    interns = TinyFishScraper().search(query(internships_only=True))
    assert interns and all("intern" in j.role_title.lower() for j in interns)


def test_search_plans_include_catalog_gap_companies_only_for_scans(mock: respx.MockRouter) -> None:
    searches: list[dict[str, str]] = []
    route_search(mock, {"results": []}, searches)
    TinyFishScraper().discover(query())
    assert not any("careers" in p["query"] and any(g in p["query"] for g in catalog_gap_companies()) for p in searches)
    searches.clear()
    TinyFishScraper().discover(query(), include_catalog_gaps=True)
    gaps = catalog_gap_companies()
    assert len(gaps) == 3 and all(g.lower() not in "linkedin" for g in gaps)
    assert any(g in p["query"] for p in searches for g in gaps)
    assert len(searches) <= settings.TINYFISH_SEARCH_QUERIES_PER_SCAN


# =========================================================================== ATS delegation and postings
def test_ats_boards_are_expanded_by_their_own_scrapers(mock: respx.MockRouter) -> None:
    hits = ["https://boards.greenhouse.io/stripe", "https://jobs.lever.co/acme", "https://jobs.ashbyhq.com/openai",
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"]
    route_search(mock, {"results": [{"position": i, "title": "Jobs", "url": u, "snippet": "", "site_name": ""} for i, u in enumerate(hits, 1)]})
    fetch = route_fetch(mock)
    route_ats(mock)
    mock.get("https://api.lever.co/v0/postings/acme").mock(return_value=httpx.Response(200, json=fxj("lever_postings.json", SCRAPER_FX)))
    mock.get("https://api.ashbyhq.com/posting-api/job-board/openai").mock(return_value=httpx.Response(200, json=fxj("ashby_board.json", SCRAPER_FX)))
    base = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite"
    mock.post(f"{base}/jobs").mock(return_value=httpx.Response(200, json=fxj("workday_jobs.json", SCRAPER_FX)))
    mock.get(f"{base}/job/US-CA-Santa-Clara/Software-Engineer---Backend_JR100").mock(
        return_value=httpx.Response(200, json=fxj("workday_detail.json", SCRAPER_FX)))
    result = TinyFishScraper().discover(query())
    platforms = {j.source_platform for j in result.jobs}
    assert platforms == {ATSPlatform.GREENHOUSE, ATSPlatform.LEVER, ATSPlatform.ASHBY, ATSPlatform.WORKDAY}
    assert result.counts["boards_delegated"] == 4 and not fetch.called
    assert all(j.raw["tinyfish"]["via"] == ["search", "ats_api"] for j in result.jobs)
    assert any(j.raw.get("board_token") == "stripe" for j in result.jobs)  # native raw is kept for the company check


def test_ats_postings_use_the_public_api_not_fetch(mock: respx.MockRouter) -> None:
    hits = [f"https://jobs.lever.co/acme/{LEVER_ID}", "https://job-boards.greenhouse.io/stripe/jobs/7001",
            "https://jobs.ashbyhq.com/openai/f00d-1", "https://jobs.ashbyhq.com/openai/f00d-2",
            "https://medtronic.wd1.myworkdayjobs.com/en-US/MedtronicCareers/job/Software-Engineering-Intern---Summer-2027_R73630-1"]
    route_search(mock, {"results": [{"position": i, "title": "Engineer", "url": u, "snippet": "", "site_name": ""} for i, u in enumerate(hits, 1)]})
    fetch = route_fetch(mock)
    route_ats(mock)
    ashby = mock.get("https://api.ashbyhq.com/posting-api/job-board/openai").mock(return_value=httpx.Response(200, json=fxj("ashby_board.json", SCRAPER_FX)))
    detail = copy.deepcopy(fxj("workday_detail.json", SCRAPER_FX))
    detail["jobPostingInfo"]["title"] = "Software Engineering Intern - Summer 2027"
    mock.get("https://medtronic.wd1.myworkdayjobs.com/wday/cxs/medtronic/MedtronicCareers/job/Software-Engineering-Intern---Summer-2027_R73630-1").mock(
        return_value=httpx.Response(200, json=detail))
    result = TinyFishScraper().discover(query())
    titles = {j.role_title for j in result.jobs}
    assert titles == {"Senior Software Engineer, Platform", "Backend Engineer, Payments", "Software Engineer, Backend", "Research Scientist",
                      "Software Engineering Intern - Summer 2027"}
    assert not fetch.called and ashby.call_count == 1  # two Ashby postings, the board is read once
    assert result.usage["fetch_urls"] == 0


def test_a_closed_ats_posting_is_dropped_and_an_unreadable_one_falls_back_to_fetch(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.scrapers.base.time.sleep", lambda seconds: None)  # (the scrapers retry a 500 after 2, 4, 8 s)
    closed = f"https://jobs.lever.co/acme/{LEVER_ID}"
    broken = "https://job-boards.greenhouse.io/stripe/jobs/7001"
    route_search(mock, {"results": [{"position": i, "title": "Backend Engineer", "url": u, "snippet": "", "site_name": ""}
                                    for i, u in enumerate([closed, broken], 1)]})
    log: list[dict[str, Any]] = []
    html = fxj("pipeline_fetch_postings.json")["results"][1]["text"].replace("stripe.example/careers/jobs/backend-engineer-payments-7001", "x")
    route_fetch(mock, rows=[page_row(broken, html)], log=log)
    mock.get(f"https://api.lever.co/v0/postings/acme/{LEVER_ID}").mock(return_value=httpx.Response(404))
    mock.get(f"{GH}/stripe/jobs/7001").mock(return_value=httpx.Response(500))
    mock.get(f"{GH}/stripe").mock(return_value=httpx.Response(500))
    result = TinyFishScraper().discover(query())
    assert [u for body in log for u in body["urls"]] == [broken]  # only the one the API couldn't give
    assert [j.role_title for j in result.jobs] == ["Backend Engineer, Payments"] and result.jobs[0].raw["tinyfish"]["via"] == ["search", "fetch"]


def test_a_page_listing_several_postings_yields_each_of_them(mock: respx.MockRouter) -> None:
    ld = [{"@type": "JobPosting", "title": f"Backend Engineer {n}", "description": "<p>Python</p>", "datePosted": "2026-10-10",
           "url": f"https://listing.example/jobs/backend-engineer-{n}-9{n}", "hiringOrganization": {"name": "Listing Co"}} for n in (1, 2)]
    html = f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body><main><h1>Jobs</h1></main></body></html>'
    route_search(mock, {"results": [{"position": 1, "title": "Jobs at Listing Co", "url": "https://listing.example/careers/jobs/all-open-roles-2026",
                                     "snippet": "", "site_name": ""}]})
    route_fetch(mock, rows=[page_row("https://listing.example/careers/jobs/all-open-roles-2026", html)])
    result = TinyFishScraper().discover(query())
    assert sorted(j.role_title for j in result.jobs) == ["Backend Engineer 1", "Backend Engineer 2"]


def test_a_markdown_posting_is_parsed_too(mock: respx.MockRouter) -> None:
    url = "https://md.example/careers/jobs/backend-engineer-5150"
    route_search(mock, {"results": [{"position": 1, "title": "Backend Engineer - MD", "url": url, "snippet": "", "site_name": ""}]})
    text = "# Backend Engineer\n\nLocation: Remote\n\nBuild and run Python services for millions of users. [Apply now](https://md.example/apply/5150)\n"
    route_fetch(mock, rows=[page_row(url, text, "markdown")])
    result = TinyFishScraper().discover(query())
    assert [j.role_title for j in result.jobs] == ["Backend Engineer"] and result.jobs[0].application_url == "https://md.example/apply/5150"


def test_a_posting_nothing_can_read_is_kept_from_its_search_result(mock: respx.MockRouter) -> None:
    url = "https://acme.example/careers/jobs/backend-engineer-1"
    hit = {"position": 1, "title": "Backend Engineer", "url": url, "snippet": "Build Python services", "site_name": "acme.example", "date": "2 days ago"}
    route_search(mock, {"results": [hit]})
    route_fetch(mock, default_failure="page_not_found")
    result = TinyFishScraper().discover(query())
    assert [(j.role_title, j.source_url) for j in result.jobs] == [("Backend Engineer", url)]
    assert result.jobs[0].raw["tinyfish"]["via"] == ["search"] and result.jobs[0].description == "Build Python services"
    assert result.jobs[0].posted_date is not None


def test_the_agents_copy_beats_the_search_snippet(mock: respx.MockRouter) -> None:
    url = "https://acme.example/careers/jobs/backend-engineer-1-10"
    route_search(mock, {"results": [{"position": 1, "title": "Backend Engineer 1", "url": url, "snippet": "snippet", "site_name": ""}]})
    route_fetch(mock, default_failure="timeout")
    log: list[dict[str, Any]] = []
    route_agent(mock, log)
    result = TinyFishScraper().discover(query(), agent_budget=1)
    assert len(log) == 1 and [j.raw["tinyfish"]["via"] for j in result.jobs] == [["search", "agent"]]
    assert result.jobs[0].description != "snippet"
    # Without an Agent budget the snippet is all there is
    log.clear()
    result = TinyFishScraper().discover(query(), agent_budget=0)
    assert not log and [j.raw["tinyfish"]["via"] for j in result.jobs] == [["search"]]


def test_user_career_pages_are_read_first_and_a_page_with_job_links_is_followed(mock: respx.MockRouter) -> None:
    route_search(mock, {"results": []})
    log: list[dict[str, Any]] = []
    route_fetch(mock, ["pipeline_fetch_careers.json", "pipeline_fetch_postings.json"], log)
    q = query(sources={"career_pages": ["https://globex.example/careers/"]})
    result = TinyFishScraper().discover(q)
    assert [u for body in log for u in body["urls"]] == ["https://globex.example/careers", "https://globex.example/careers/jobs/platform-engineer-77"]
    assert [body["format"] for body in log] == ["markdown", "html"]
    assert [(j.role_title, j.raw["tinyfish"]["via"]) for j in result.jobs] == [("Platform Engineer", ["fetch"])]


def test_the_fetch_budget_is_respected(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TINYFISH_FETCH_URLS_PER_SCAN", 2)
    route_search(mock, {"results": []})
    log: list[dict[str, Any]] = []
    route_fetch(mock, log=log)
    result = TinyFishScraper().discover(agent_only_query(5), agent_budget=0)
    assert sum(len(b["urls"]) for b in log) == 2 and result.usage["fetch_urls"] == 2
    assert any("left unread" in n for n in result.notes)


# =========================================================================== the Agent
def test_a_bot_blocked_page_gets_one_stealth_agent_run(mock: respx.MockRouter) -> None:
    pages = ["https://site0.example/careers", "https://site1.example/careers"]
    route_fetch(mock, failures={pages[0]: "bot_blocked", pages[1]: "empty_content"})
    log: list[dict[str, Any]] = []
    route_agent(mock, log)
    result = TinyFishScraper().discover(agent_only_query(2))
    profiles = {p["url"]: p["browser_profile"] for p in log}
    assert profiles == {pages[0]: "stealth", pages[1]: "lite"}
    assert all(p["output_schema"]["required"] == ["jobs"] and "Never log in" in p["goal"] for p in log)
    assert len(result.agent_runs) == 2 and all(r.status == "COMPLETED" and r.found == 1 and r.steps == 4 for r in result.agent_runs)
    assert result.counts["agent_jobs"] == 2 and result.usage["agent_runs"] == 2 and result.usage["agent_steps"] == 8
    assert all(j.raw["tinyfish"]["via"] == ["agent"] and j.raw["tinyfish"]["agent_run_id"] for j in result.jobs)
    assert {r.streaming_url for r in result.agent_runs} == {"https://stream.tinyfish.example/run_1", "https://stream.tinyfish.example/run_2"}


def test_a_javascript_shell_or_a_page_with_no_jobs_goes_to_the_agent(mock: respx.MockRouter) -> None:
    shell = "<html><body><div id='root'></div><script src='/app.js'></script><noscript>Please enable JavaScript</noscript></body></html>"
    empty = "# Careers\n\nWe are not hiring right now, but we love to hear from you."
    route_fetch(mock, rows=[page_row("https://site0.example/careers", shell, "markdown"), page_row("https://site1.example/careers", empty, "markdown")])
    log: list[dict[str, Any]] = []
    route_agent(mock, log)
    TinyFishScraper().discover(agent_only_query(2))
    assert sorted(p["url"] for p in log) == ["https://site0.example/careers", "https://site1.example/careers"]


def test_a_page_that_is_just_gone_is_not_worth_an_agent_run(mock: respx.MockRouter) -> None:
    route_fetch(mock, default_failure="page_not_found")
    log: list[dict[str, Any]] = []
    route_agent(mock, log)
    assert TinyFishScraper().discover(agent_only_query(2)).jobs == [] and log == []


def test_agent_runs_never_exceed_the_budget_or_the_hard_cap(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    route_fetch(mock, default_failure="empty_content")
    log: list[dict[str, Any]] = []
    route_agent(mock, log)
    TinyFishScraper().discover(agent_only_query(5), agent_budget=3)
    assert len(log) == 3
    log.clear()
    TinyFishScraper().discover(agent_only_query(5, agent_runs=99))  # the preference is unclamped: the cap applies (5)
    assert len(log) == 5
    log.clear()
    monkeypatch.setattr(settings, "TINYFISH_AGENT_MAX_RUNS_PER_SCAN", 2)
    TinyFishScraper().discover(agent_only_query(5, agent_runs=99))
    assert len(log) == 2
    log.clear()
    TinyFishScraper().discover(agent_only_query(5), agent_budget=99)
    assert len(log) == 2
    log.clear()
    result = TinyFishScraper().discover(agent_only_query(5))  # nothing set: the default of 2
    assert len(log) == 2 and any("could use it" in n for n in result.notes)
    log.clear()
    result = TinyFishScraper().discover(agent_only_query(5, agent_runs=0))
    assert log == [] and result.agent_runs == [] and any("0 runs" in n for n in result.notes)


def test_at_most_two_agent_runs_are_in_flight(mock: respx.MockRouter) -> None:
    route_fetch(mock, default_failure="empty_content")
    gate = threading.Lock()
    state = {"now": 0, "peak": 0}

    def respond(payload: dict[str, Any]) -> httpx.Response:
        with gate:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.15)
        with gate:
            state["now"] -= 1
        return sse(*agent_events(jobs_for(payload["url"])))

    route_agent(mock, [], respond)
    TinyFishScraper().discover(agent_only_query(5), agent_budget=5)
    assert state["peak"] == 2


def test_two_agent_runs_really_run_at_the_same_time(mock: respx.MockRouter) -> None:
    route_fetch(mock, default_failure="empty_content")
    barrier = threading.Barrier(2)

    def respond(payload: dict[str, Any]) -> httpx.Response:
        barrier.wait(timeout=5)  # only passes when the second run starts while the first is still going
        return sse(*agent_events(jobs_for(payload["url"])))

    route_agent(mock, [], respond)
    result = TinyFishScraper().discover(agent_only_query(2), agent_budget=2)
    assert len(result.jobs) == 2


def test_every_agent_run_carries_max_duration_seconds(mock: respx.MockRouter) -> None:
    route_fetch(mock, default_failure="empty_content")
    log: list[dict[str, Any]] = []
    route_agent(mock, log)
    TinyFishScraper().discover(agent_only_query(1))
    assert log[0]["agent_config"]["max_duration_seconds"] == settings.TINYFISH_AGENT_MAX_DURATION_SECONDS == 180
    log.clear()
    TinyFishScraper().discover(agent_only_query(1), deadline=time.monotonic() + 100)  # never past the scan's own deadline
    assert 90 <= log[0]["agent_config"]["max_duration_seconds"] <= 100


def test_no_agent_run_starts_with_under_a_minute_left(mock: respx.MockRouter) -> None:
    fetch = route_fetch(mock, default_failure="empty_content")
    log: list[dict[str, Any]] = []
    route_agent(mock, log)
    result = TinyFishScraper().discover(agent_only_query(2), deadline=time.monotonic() + 30)
    assert fetch.called and log == [] and result.agent_runs == [] and any("not enough time" in n for n in result.notes)
    result = TinyFishScraper().discover(agent_only_query(2, deadline=time.monotonic() + 45))  # the query's own deadline counts too
    assert log == [] and any("not enough time" in n for n in result.notes)


def test_a_billing_error_stops_further_agent_runs(mock: respx.MockRouter) -> None:
    route_fetch(mock, default_failure="empty_content")
    log: list[dict[str, Any]] = []
    broke = {"code": "BILLING_REJECTED", "message": "Out of credits", "category": "billing"}
    route_agent(mock, log, lambda payload: sse(*agent_events(None, error=broke)))
    result = TinyFishScraper().discover(agent_only_query(5), agent_budget=5)
    assert 1 <= len(log) <= 2 and len(result.agent_runs) == len(log)  # the two already in flight; nothing queued after them
    assert all(r.error == "Out of credits" and r.status == "FAILED" for r in result.agent_runs)
    assert any("out of credits" in n for n in result.notes) and result.jobs == []


def test_the_agent_never_runs_in_demo_mode(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    route_fetch(mock, default_failure="empty_content")
    log: list[dict[str, Any]] = []
    route_agent(mock, log)
    result = TinyFishScraper().discover(agent_only_query(2), agent_budget=2)
    assert log == [] and result.usage["agent_runs"] == 0 and any("demo" in n for n in result.notes)


def test_agent_progress_reaches_the_scan_detail_and_the_hooks(mock: respx.MockRouter) -> None:
    route_fetch(mock, default_failure="empty_content")
    route_agent(mock, [], lambda payload: sse(*agent_events(jobs_for(payload["url"]), progress=("Opening the page", "Clicking Load more"))))
    details: list[tuple[str | None, dict[str, Any]]] = []
    events: list[tuple[str, str]] = []
    stages: list[tuple[str, int, int]] = []
    hooks = DiscoveryHooks(stage=lambda stage, message, done, total: stages.append((stage, done, total)),
                           agent_event=lambda url, event: events.append((url, event.type)))
    TinyFishScraper().discover(agent_only_query(1, detail=lambda text, extra: details.append((text, extra))), hooks=hooks)
    texts = [t for t, _ in details]
    assert "Opening the page" in texts and "Clicking Load more" in texts
    assert any(extra.get("streaming_url") == "https://stream.tinyfish.example/run_1" for _t, extra in details)
    assert any(extra.get("usage", {}).get("agent_runs") == 1 for _t, extra in details)
    assert ("https://site0.example/careers", "PROGRESS") in events and ("https://site0.example/careers", "COMPLETE") in events
    assert {s for s, _d, _t in stages} >= {"read", "agent", "rank"}


def test_agent_output_is_validated(mock: respx.MockRouter) -> None:
    route_fetch(mock, default_failure="empty_content")
    rows = [{"title": "Backend Engineer", "apply_url": "https://site0.example/careers/jobs/be-1-10", "visa_sponsorship": "no"},
            {"title": "Plain http", "apply_url": "http://site0.example/careers/jobs/be-2-20"},
            {"title": "Elsewhere", "apply_url": "https://evil.example.net/jobs/1"},
            {"title": "LinkedIn copy", "apply_url": "https://www.linkedin.com/jobs/view/3999999"}]
    route_agent(mock, [], lambda payload: sse(*agent_events(rows)))
    result = TinyFishScraper().discover(agent_only_query(1))
    assert [j.role_title for j in result.jobs] == ["Backend Engineer"] and result.jobs[0].raw["sponsorship"] == "No visa sponsorship"


# =========================================================================== deadlines, cancel, failures
def test_out_of_time_or_cancelled_means_no_new_work(mock: respx.MockRouter) -> None:
    search = route_search(mock, {"results": []})
    fetch = route_fetch(mock)
    result = TinyFishScraper().discover(agent_only_query(2, deadline=time.monotonic() - 1))
    assert result.jobs == [] and not search.called and not fetch.called
    result = TinyFishScraper().discover(query(), deadline=time.monotonic() - 1)
    assert result.jobs == [] and not search.called
    result = TinyFishScraper().discover(query(), hooks=DiscoveryHooks(should_cancel=lambda: True))
    assert result.jobs == [] and not search.called and not fetch.called


def test_progress_is_reported_for_the_scan_bar(mock: respx.MockRouter) -> None:
    mocked_pipeline(mock)
    steps: list[tuple[int, int]] = []
    TinyFishScraper().discover(query(progress=lambda done, total: steps.append((done, total))))
    assert steps[0] == (0, 4) and steps[-1] == (4, 4) and [d for d, _t in steps] == sorted(d for d, _t in steps)


def test_search_without_a_key_says_how_to_add_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", "")
    with pytest.raises(ScraperError, match="TINYFISH_API_KEY"):
        TinyFishScraper().search(query())
    with pytest.raises(ScraperError, match="TINYFISH_API_KEY"):
        TinyFishScraper().discover(query())


class _Board:
    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        return [ScrapedJob(company_name="Board Co", role_title="Backend Engineer", description="x", source_url="https://board.example/jobs/1",
                           source_platform=ATSPlatform.CUSTOM)]


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        self.lines.append((level, message))


def _scan_sources(monkeypatch: pytest.MonkeyPatch, names: list[str]) -> tuple[list[ScrapedJob], _Log, ScanProgress]:
    monkeypatch.setitem(SCRAPERS, "board", _Board)
    log = _Log()
    progress = ScanProgress(None, None, names)
    return orch.discover_jobs(query(), names, log, progress=progress), log, progress


def test_a_scan_without_a_key_marks_tinyfish_failed_and_carries_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", "")
    jobs, log, progress = _scan_sources(monkeypatch, ["board", "tinyfish"])
    assert [j.company_name for j in jobs] == ["Board Co"]
    assert progress.sources["tinyfish"]["status"] == "failed" and "TINYFISH_API_KEY" in progress.sources["tinyfish"]["error"]
    assert progress.sources["board"]["status"] == "done"
    assert any(level == "error" and "tinyfish" in message and "TINYFISH_API_KEY" in message for level, message in log.lines)


@pytest.mark.parametrize(("status", "body", "words"), [
    (401, {"error": {"code": "UNAUTHORIZED", "message": "bad key"}}, "invalid"),
    (402, {"error": {"code": "PAYMENT_REQUIRED", "message": "no credits"}}, ""),
    (429, {"error": {"code": "RATE_LIMITED", "message": "slow down", "details": {"limit": "daily"}}}, ""),
])
def test_a_tinyfish_account_error_fails_only_that_source(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, status: int,
                                                         body: dict[str, Any], words: str) -> None:
    mock.get(SEARCH).mock(return_value=httpx.Response(status, json=body))
    fetch = route_fetch(mock)
    jobs, log, progress = _scan_sources(monkeypatch, ["board", "tinyfish"])
    assert [j.company_name for j in jobs] == ["Board Co"] and not fetch.called
    assert progress.sources["tinyfish"]["status"] == "failed" and progress.sources["board"]["status"] == "done"
    assert any(level == "error" and message.startswith("tinyfish:") for level, message in log.lines)


def test_a_search_outage_still_reads_the_users_career_pages(mock: respx.MockRouter) -> None:
    mock.get(SEARCH).mock(return_value=httpx.Response(500, json={"error": {"code": "INTERNAL", "message": "boom"}}))
    route_fetch(mock, ["pipeline_fetch_careers.json", "pipeline_fetch_postings.json"])
    result = TinyFishScraper().discover(query(sources={"career_pages": ["https://globex.example/careers"]}))
    assert [j.role_title for j in result.jobs] == ["Platform Engineer"] and any(n.startswith("Search:") for n in result.notes)


def test_a_credit_error_while_fetching_keeps_what_was_found(mock: respx.MockRouter) -> None:
    route_search(mock, fxj("pipeline_search.json"))
    mock.post(FETCH).mock(return_value=httpx.Response(402, json={"error": {"code": "PAYMENT_REQUIRED", "message": "no credits"}}))
    route_ats(mock)
    result = TinyFishScraper().discover(query())
    assert {j.role_title for j in result.jobs} >= {"Backend Engineer, Payments", "Senior Software Engineer, Platform"}
    assert any(n.startswith("Fetch:") for n in result.notes)


# =========================================================================== fetch_job / read_html
def test_fetch_job_reads_an_ats_posting_through_its_api_and_a_page_through_fetch(mock: respx.MockRouter) -> None:
    fetch = route_fetch(mock, ["pipeline_fetch_postings.json"])
    route_ats(mock)
    job = TinyFishScraper().fetch_job("https://job-boards.greenhouse.io/stripe/jobs/7001?gh_src=x")
    assert job and job.role_title == "Backend Engineer, Payments" and job.raw["tinyfish"]["via"] == ["ats_api"] and not fetch.called
    url = "https://initech.example/careers/jobs/backend-engineer-intern-123"
    job = TinyFishScraper().fetch_job(url)
    assert job and job.company_name == "Initech" and job.source_url == url and job.raw["discovered_by"] == "tinyfish"
    assert job.raw["tinyfish"]["via"] == ["fetch"]


def test_fetch_job_gives_up_on_closed_boards_and_gated_urls(mock: respx.MockRouter) -> None:
    fetch = route_fetch(mock)
    mock.get(f"https://api.lever.co/v0/postings/acme/{LEVER_ID}").mock(return_value=httpx.Response(404))
    scraper = TinyFishScraper()
    assert scraper.fetch_job(f"https://jobs.lever.co/acme/{LEVER_ID}") is None
    assert scraper.fetch_job("https://boards.greenhouse.io/stripe") is None
    for url in ("https://www.linkedin.com/jobs/view/1", "https://in.indeed.com/viewjob?jk=1", "https://www.glassdoor.com/job-listing/x",
                "https://internshala.com/internship/detail/x"):
        assert scraper.fetch_job(url) is None
    assert not fetch.called


def test_fetch_job_uses_one_agent_run_for_a_blocked_or_javascript_only_page(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://app.example/careers/jobs/backend-engineer-42"
    shell = "<html><body><div id='root'></div><script src='/app.js'></script></body></html>"
    route_fetch(mock, rows=[page_row(url, shell)])
    log: list[dict[str, Any]] = []
    route_agent(mock, log, lambda payload: sse(*agent_events([{"title": "Backend Engineer", "apply_url": url, "visa_sponsorship": "yes"}])))
    job = TinyFishScraper().fetch_job(url)
    assert job and job.role_title == "Backend Engineer" and job.source_url == url and job.raw["tinyfish"]["via"] == ["agent"]
    assert len(log) == 1 and "single job posting" in log[0]["goal"] and log[0]["agent_config"]["max_duration_seconds"] == 180
    log.clear()
    assert TinyFishScraper().fetch_job(url, allow_agent=False) is None and log == []
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    assert TinyFishScraper().fetch_job(url) is None and log == []


def test_fetch_job_blocked_page_uses_stealth(mock: respx.MockRouter) -> None:
    url = "https://app.example/careers/jobs/backend-engineer-42"
    route_fetch(mock, failures={url: "bot_blocked"})
    log: list[dict[str, Any]] = []
    route_agent(mock, log, lambda payload: sse(*agent_events([{"title": "Backend Engineer", "apply_url": url}])))
    assert TinyFishScraper().fetch_job(url) is not None and log[0]["browser_profile"] == "stealth"


def test_read_html(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://initech.example/careers/jobs/backend-engineer-intern-123"
    fetch = route_fetch(mock, ["pipeline_fetch_postings.json"])
    html = TinyFishScraper().read_html(url)
    assert html and "JobPosting" in html and json.loads(fetch.calls[0].request.content)["format"] == "html"
    assert TinyFishScraper().read_html("https://nowhere.example/x") is None
    calls = fetch.call_count
    assert TinyFishScraper().read_html("https://www.linkedin.com/jobs/view/1") is None and fetch.call_count == calls
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", "")
    assert TinyFishScraper().read_html(url) is None and fetch.call_count == calls


# =========================================================================== wiring: scans
@pytest.mark.parametrize(("key", "pref", "demo", "requested", "expected"), [
    (KEY, None, False, None, ["greenhouse", "tinyfish"]),
    (KEY, True, False, None, ["greenhouse", "tinyfish"]),
    (KEY, False, False, None, ["greenhouse"]),
    ("", None, False, None, ["greenhouse"]),
    (KEY, None, True, None, ["demo"]),
    (KEY, None, False, ["greenhouse"], ["greenhouse"]),
    (KEY, None, False, ["tinyfish"], ["tinyfish"]),
])
def test_scan_platforms_matrix(monkeypatch: pytest.MonkeyPatch, key: str, pref: bool | None, demo: bool, requested: list[str] | None,
                               expected: list[str]) -> None:
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", key)
    monkeypatch.setattr(settings, "DEMO_MODE", demo)
    prefs: dict[str, Any] = {"platforms": ["greenhouse"], "scan_top_companies": False}
    if pref is not None:
        prefs["tinyfish_enabled"] = pref
    assert orch.scan_platforms(prefs, requested) == expected


def test_scan_platforms_kill_switch_and_no_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    prefs = {"platforms": ["greenhouse", "tinyfish"], "scan_top_companies": False}
    assert orch.scan_platforms(prefs) == ["greenhouse", "tinyfish"]
    monkeypatch.setattr(settings, "TINYFISH_ENABLED", False)
    assert orch.scan_platforms({"platforms": ["greenhouse"], "scan_top_companies": False}) == ["greenhouse"]


class _FakeTinyFish:
    queries: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        _FakeTinyFish.queries.append(query)
        query.report(1, 2)
        query.note("Searching with TinyFish Search")
        query.note("Clicking Load more", streaming_url="https://stream.tinyfish.example/run_1")
        query.note(None, usage={"search_calls": 6, "fetch_calls": 2, "fetch_urls": 14, "agent_runs": 2, "agent_steps": 11, "errors": 0})
        return []


def _run_scan(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch, trigger: str = "user", runs: int | None = None) -> Any:
    _FakeTinyFish.queries = []
    monkeypatch.setitem(SCRAPERS, "tinyfish", _FakeTinyFish)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "jane@example.com").one()
        if runs is not None:
            user.preferences = {**user.preferences, "tinyfish_agent_runs": runs}
            db.commit()
        run = orch.run_scan(db, user, trigger=trigger, platforms=["tinyfish"])
        db.commit()
        assert run.status == "completed", run.log
        return run


def test_scan_progress_carries_the_detail_and_usage_and_the_run_log_has_the_usage_line(auth_client: TestClient, master_resume: dict,
                                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run_scan(auth_client, monkeypatch)
    source = next(s for s in run.progress["sources"] if s["name"] == "tinyfish")
    assert source["detail"] == "Clicking Load more" and source["streaming_url"] == "https://stream.tinyfish.example/run_1"
    assert source["usage"]["fetch_urls"] == 14 and source["status"] == "done"
    assert "tinyfish: Search 6 · Fetch 14 URLs · Agent 2 runs (11 steps)" in [entry["message"] for entry in run.log]


def test_the_agent_budget_is_clamped_and_scheduled_scans_spend_at_most_one(auth_client: TestClient, master_resume: dict,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _run_scan(auth_client, monkeypatch, runs=99)
    assert _FakeTinyFish.queries[0].agent_runs == settings.TINYFISH_AGENT_MAX_RUNS_PER_SCAN == 5
    _run_scan(auth_client, monkeypatch, trigger="schedule", runs=3)
    assert _FakeTinyFish.queries[0].agent_runs == 1
    _run_scan(auth_client, monkeypatch, trigger="schedule", runs=0)
    assert _FakeTinyFish.queries[0].agent_runs == 0
    _run_scan(auth_client, monkeypatch, runs=2)
    assert _FakeTinyFish.queries[0].agent_runs == 2


def test_without_a_key_the_scan_query_is_left_as_it_was(auth_client: TestClient, master_resume: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", "")
    _run_scan(auth_client, monkeypatch, runs=99)
    assert _FakeTinyFish.queries[0].agent_runs == 99


def test_usage_line() -> None:
    assert orch.tinyfish_usage_line({"search_calls": 1, "fetch_urls": 1, "agent_runs": 1, "agent_steps": 3}) == "Search 1 · Fetch 1 URL · Agent 1 run (3 steps)"
    assert orch.tinyfish_usage_line({"search_calls": 4}) == "Search 4"
    assert orch.tinyfish_usage_line({}) == ""
    assert [orch.clamp_agent_runs(v) for v in (None, "x", -3, 2, "4", 99)] == [2, 2, 0, 2, 4, 5]


def test_source_detail_whitelist() -> None:
    progress = ScanProgress(None, None, ["tinyfish", "greenhouse"])
    progress.source_step("tinyfish", 1, 4)
    progress.source_detail("tinyfish", "x" * 300, {"usage": {"search_calls": 2, "bogus": "text"}, "streaming_url": "https://s.example/1",
                                                   "status": "done", "found": 99, "evil": "<script>"})
    src = progress.snapshot()["sources"][0]
    assert src["detail"] == "x" * 120 and src["usage"] == {"search_calls": 2} and src["streaming_url"] == "https://s.example/1"
    assert src["status"] == "pending" and src["found"] == 0 and (src["done"], src["total"]) == (1, 4) and "evil" not in src
    progress.source_detail("tinyfish", None, {"streaming_url": "http://insecure.example/1"})  # http is not kept, the old value stays
    progress.source_detail("tinyfish", "", None)
    progress.source_detail("nobody", "ignored", {"usage": {"a": 1}})
    snap = progress.snapshot()
    assert snap["sources"][0]["detail"] == "x" * 120 and snap["sources"][0]["streaming_url"] == "https://s.example/1"
    assert "detail" not in snap["sources"][1] and len(snap["sources"]) == 2


# =========================================================================== wiring: Add job by URL and career pages
JSON_LD_PAGE = fxj("pipeline_fetch_postings.json")["results"][0]["text"].replace("Build Python and FastAPI services.", "Build Python and FastAPI services. " + "More. " * 60)
INITECH = "https://initech.example/careers/jobs/backend-engineer-intern-123"


def test_add_by_url_falls_back_to_tinyfish_when_the_site_blocks_us(mock: respx.MockRouter) -> None:
    mock.get(INITECH).mock(return_value=httpx.Response(403, text="Forbidden"))
    fetch = route_fetch(mock, rows=[page_row(INITECH, JSON_LD_PAGE)])
    job = fetch_job_from_url(INITECH)
    assert job and job.role_title == "Backend Engineer Intern" and fetch.call_count == 1  # the long description needs no second look


def test_add_by_url_falls_back_to_tinyfish_for_a_javascript_shell(mock: respx.MockRouter) -> None:
    shell = "<html><head><title>Loading</title></head><body><div id='root'></div><script src='/app.js'></script></body></html>"
    mock.get(INITECH).mock(return_value=httpx.Response(200, text=shell))
    route_fetch(mock, rows=[page_row(INITECH, JSON_LD_PAGE)])
    job = fetch_job_from_url(INITECH)
    assert job and job.role_title == "Backend Engineer Intern" and job.company_name == "Initech"


def test_add_by_url_keeps_the_better_of_a_thin_page_and_tinyfishs_copy(mock: respx.MockRouter) -> None:
    thin = ("<html><head><meta property='og:site_name' content='Initech'></head><body><main><h1>Backend Engineer Intern</h1>"
            "<p>Apply today for this great role.</p></main></body></html>")
    mock.get(INITECH).mock(return_value=httpx.Response(200, text=thin))
    route_fetch(mock, rows=[page_row(INITECH, JSON_LD_PAGE)])
    job = fetch_job_from_url(INITECH)
    assert job and len(job.description) > 200 and job.raw["discovered_by"] == "tinyfish"


def test_add_by_url_leaves_a_full_page_alone(mock: respx.MockRouter) -> None:
    mock.get(INITECH).mock(return_value=httpx.Response(200, text=JSON_LD_PAGE))
    fetch = route_fetch(mock, rows=[])
    job = fetch_job_from_url(INITECH)
    assert job and not fetch.called and "discovered_by" not in (job.raw or {})


@pytest.mark.parametrize("url", ["https://www.linkedin.com/jobs/view/1", "https://in.indeed.com/viewjob?jk=1",
                                 "https://www.glassdoor.com/job-listing/x-JV_1.htm", "https://internshala.com/internship/detail/x"])
def test_add_by_url_never_sends_gated_sites_to_tinyfish(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    fetch = route_fetch(mock)
    agent_log: list[dict[str, Any]] = []
    route_agent(mock, agent_log)
    monkeypatch.setattr("app.scrapers._fetch_job_plain", lambda u: None)
    assert fetch_job_from_url(url) is None
    assert not fetch.called and agent_log == []
    assert not any(call.request.url.host.endswith(("tinyfish.ai",)) for call in mock.calls)


def test_add_by_url_reraises_the_original_error_when_tinyfish_cannot_help(mock: respx.MockRouter) -> None:
    mock.get(INITECH).mock(return_value=httpx.Response(403, text="Forbidden"))
    route_fetch(mock, default_failure="page_not_found")
    with pytest.raises(ScraperError, match="HTTP 403"):
        fetch_job_from_url(INITECH)


def test_add_by_url_does_not_go_around_a_rate_limit(mock: respx.MockRouter) -> None:
    mock.get(INITECH).mock(return_value=httpx.Response(429, text="slow down"))
    fetch = route_fetch(mock, rows=[page_row(INITECH, JSON_LD_PAGE)])
    with pytest.raises(RateLimited):
        fetch_job_from_url(INITECH)
    assert not fetch.called


def test_add_by_url_without_a_key_is_exactly_what_it_was(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", "")
    mock.get(INITECH).mock(return_value=httpx.Response(403, text="Forbidden"))
    with pytest.raises(ScraperError, match="HTTP 403 for"):
        fetch_job_from_url(INITECH)
    assert not [c for c in mock.calls if c.request.url.host.endswith("tinyfish.ai")]
    shell = "<html><body><div id='root'></div><script src='/app.js'></script></body></html>"
    mock.get(INITECH).mock(return_value=httpx.Response(200, text=shell))
    assert fetch_job_from_url(INITECH) is None
    assert not [c for c in mock.calls if c.request.url.host.endswith("tinyfish.ai")]


CAREERS = "https://careers.initech.example/jobs"


def test_generic_fetch_html_falls_back_to_tinyfish(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.scrapers.base.time.sleep", lambda seconds: None)
    fetch = route_fetch(mock, rows=[page_row(CAREERS, "<html><body>from tinyfish</body></html>")])
    scraper = GenericScraper()
    mock.get(CAREERS).mock(return_value=httpx.Response(403, text="Forbidden"))
    assert scraper.fetch_html(CAREERS) == "<html><body>from tinyfish</body></html>"
    mock.get(CAREERS).mock(return_value=httpx.Response(200, text="<html><body>Just a moment...<script>x</script></body></html>"))
    assert "from tinyfish" in scraper.fetch_html(CAREERS)
    mock.get(CAREERS).mock(return_value=httpx.Response(200, text="<html><body><div id='root'></div><script src='/a.js'></script></body></html>"))
    assert "from tinyfish" in scraper.fetch_html(CAREERS)
    mock.get(CAREERS).mock(side_effect=httpx.ConnectError("down"))
    assert "from tinyfish" in scraper.fetch_html(CAREERS)
    assert fetch.call_count == 4
    # a normal page is returned as it is, with no TinyFish call
    mock.get(CAREERS).mock(return_value=httpx.Response(200, text="<html><body>" + "Real content. " * 60 + "</body></html>"))
    assert "Real content" in GenericScraper().fetch_html(CAREERS) and fetch.call_count == 4


def test_generic_fetch_html_never_goes_around_a_stop_sign(mock: respx.MockRouter) -> None:
    fetch = route_fetch(mock, rows=[page_row(CAREERS, "<html>from tinyfish</html>")])
    mock.get(CAREERS).mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(RateLimited, match="HTTP 429"):
        GenericScraper().fetch_html(CAREERS)
    rate_limiter._mem.clear()  # (the 429 paused the platform)
    mock.get(CAREERS).mock(return_value=httpx.Response(403, text="<div class='g-recaptcha'></div>"))
    with pytest.raises(RateLimited, match="captcha"):
        GenericScraper().fetch_html(CAREERS)
    rate_limiter._mem.clear()
    mock.get("https://www.linkedin.com/company/acme/jobs").mock(return_value=httpx.Response(403, text="no"))
    with pytest.raises(ScraperError, match="HTTP 403"):
        GenericScraper().fetch_html("https://www.linkedin.com/company/acme/jobs")
    assert not fetch.called


def test_generic_fetch_html_when_tinyfish_has_nothing_either(mock: respx.MockRouter) -> None:
    fetch = route_fetch(mock, default_failure="page_not_found")
    mock.get(CAREERS).mock(return_value=httpx.Response(403, text="Forbidden"))
    with pytest.raises(ScraperError, match="HTTP 403 for"):
        GenericScraper().fetch_html(CAREERS)
    assert fetch.call_count == 1
    shell = "<html><body><div id='root'></div><script src='/a.js'></script></body></html>"
    mock.get(CAREERS).mock(return_value=httpx.Response(200, text=shell))
    assert GenericScraper().fetch_html(CAREERS) == shell  # TinyFish had nothing: the original page is returned


def test_generic_fetch_html_without_a_key_never_calls_tinyfish(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", "")
    fetch = route_fetch(mock, rows=[page_row(CAREERS, "<html>from tinyfish</html>")])
    mock.get(CAREERS).mock(return_value=httpx.Response(403, text="Forbidden"))
    with pytest.raises(ScraperError, match="HTTP 403 for"):
        GenericScraper().fetch_html(CAREERS)
    shell = "<html><body><div id='root'></div><script src='/a.js'></script></body></html>"
    mock.get(CAREERS).mock(return_value=httpx.Response(200, text=shell))
    assert GenericScraper().fetch_html(CAREERS) == shell
    assert not fetch.called


def test_generic_scraper_caps_how_many_pages_it_reads_through_tinyfish(mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.scrapers.generic.MAX_TINYFISH_READS", 2)
    fetch = route_fetch(mock, rows=[page_row(f"https://careers.initech.example/jobs/{i}", "<html>ok</html>") for i in range(3)])
    scraper = GenericScraper()
    for i in range(3):
        mock.get(f"https://careers.initech.example/jobs/{i}").mock(return_value=httpx.Response(403, text="no"))
    assert scraper.fetch_html("https://careers.initech.example/jobs/0") and scraper.fetch_html("https://careers.initech.example/jobs/1")
    with pytest.raises(ScraperError):
        scraper.fetch_html("https://careers.initech.example/jobs/2")
    assert fetch.call_count == 2
