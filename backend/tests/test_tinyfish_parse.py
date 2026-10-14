"""TinyFish parsing helpers + the preference plumbing they rely on (pure logic, no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.enums import ATSPlatform, ExperienceLevel, JobType
from app.models.user import default_preferences
from app.scrapers.base import ScrapedJob, SearchQuery
from app.scrapers.tinyfish_parse import (
    ATS_SEARCH_DOMAINS,
    GATED_EXCLUDE_DOMAINS,
    JOBS_OUTPUT_SCHEMA,
    UrlKind,
    agent_jobs_to_scraped,
    ats_boards_in_page,
    build_agent_goal,
    build_search_plans,
    canonical_url,
    classify_url,
    country_code,
    is_gated_url,
    job_links_from_page,
    looks_js_shell,
    markdown_links,
    parse_posting_html,
    parse_posting_markdown,
    tag_provenance,
)
from tests.conftest import FIXTURES

FX = FIXTURES / "tinyfish"


def fx(name: str) -> str:
    return (FX / name).read_text()


def page(text: str, fmt: str = "markdown", url: str = "https://acme.com/careers", links: list[str] | None = None) -> Any:
    return SimpleNamespace(url=url, final_url=url, title="", text=text, format=fmt, links=links or [], latency_ms=None)


# --------------------------------------------------------------------------- SearchQuery plumbing
def test_from_preferences_maps_tinyfish_fields() -> None:
    q = SearchQuery.from_preferences(default_preferences())
    assert q.seniority == ["internship"]
    assert q.needs_sponsorship is False and q.skills == [] and q.companies == []
    assert q.agent_runs == 2

    prefs = {**default_preferences(), "exclude_no_sponsorship": True, "focus_skills": ["Python", "FastAPI"],
             "companies_to_target": ["Stripe"], "experience_level": ["entry", "mid"], "tinyfish_agent_runs": 0}
    q = SearchQuery.from_preferences(prefs)
    assert q.needs_sponsorship is True
    assert q.skills == ["Python", "FastAPI"] and q.companies == ["Stripe"]
    assert q.seniority == ["entry", "mid"] and q.agent_runs == 0


def test_new_default_prefs() -> None:
    prefs = default_preferences()
    assert prefs["tinyfish_enabled"] is True and prefs["tinyfish_agent_runs"] == 2
    assert SearchQuery.from_preferences({}).agent_runs is None


def test_existing_query_copies_still_work() -> None:
    base = SearchQuery(keywords=["Engineer"], limit=5)
    copy = SearchQuery(**{**base.__dict__, "sources": {"career_pages": ["https://acme.com/careers"]}})
    assert copy.seniority == [] and copy.needs_sponsorship is False and copy.detail is None
    assert copy == SearchQuery(keywords=["Engineer"], limit=5, sources={"career_pages": ["https://acme.com/careers"]})


def test_note_calls_detail_and_swallows_errors() -> None:
    calls: list[tuple[str | None, dict[str, Any]]] = []
    q = SearchQuery(detail=lambda text, extra: calls.append((text, extra)))
    q.note("Clicking Load more", streaming_url="https://x")
    q.note(None, usage={"search_calls": 1})
    assert calls == [("Clicking Load more", {"streaming_url": "https://x"}), (None, {"usage": {"search_calls": 1}})]

    def boom(text: str | None, extra: dict[str, Any]) -> None:
        raise RuntimeError("nope")

    SearchQuery(detail=boom).note("x")  # must not raise
    SearchQuery().note("x")  # no callback


# --------------------------------------------------------------------------- URLs
@pytest.mark.parametrize(("raw", "expected"), [
    ("https://ACME.com/Careers/?utm_source=a&utm_medium=b#apply", "https://acme.com/Careers"),
    ("https://boards.greenhouse.io/stripe/jobs/7001?gh_src=abc", "https://boards.greenhouse.io/stripe/jobs/7001"),
    ("https://jobs.lever.co/acme/1111?lever-source=Twitter", "https://jobs.lever.co/acme/1111"),
    ("https://acme.com/careers?gh_jid=555&utm_campaign=x", "https://acme.com/careers?gh_jid=555"),
    ("https://acme.com/jobs/1?ref=x&source=y&foo=bar", "https://acme.com/jobs/1?foo=bar"),
    ("https://acme.com", "https://acme.com"),
    ("not a url", "not a url"),
])
def test_canonical_url(raw: str, expected: str) -> None:
    assert canonical_url(raw) == expected


@pytest.mark.parametrize("url", [
    "https://linkedin.com/jobs/view/1", "https://www.linkedin.com/jobs/view/1", "https://in.linkedin.com/jobs/x",
    "https://in.indeed.com/viewjob?jk=1", "https://www.indeed.com/jobs", "https://uk.indeed.co.uk/x", "https://indeed.co.in/x",
    "https://internshala.com/internship/detail/x", "https://www.internshala.com/x",
    "https://glassdoor.co.in/job-listing/x", "https://www.glassdoor.com/Job/x", "https://www.glassdoor.co.uk/x",
])
def test_gated_hosts(url: str) -> None:
    assert is_gated_url(url) and classify_url(url) == UrlKind.GATED


@pytest.mark.parametrize("url", [
    "https://acme.com/careers", "https://notlinkedin.com/jobs", "https://linkedin.com.evil.example/jobs/1",
    "https://indeed.acme.com/jobs", "https://boards.greenhouse.io/stripe",
])
def test_not_gated_hosts(url: str) -> None:
    assert not is_gated_url(url)


@pytest.mark.parametrize(("url", "kind"), [
    ("https://boards.greenhouse.io/stripe", UrlKind.ATS_BOARD),
    ("https://boards.greenhouse.io/stripe/jobs", UrlKind.ATS_BOARD),
    ("https://job-boards.greenhouse.io/stripe/jobs/7001", UrlKind.ATS_POSTING),
    ("https://boards.greenhouse.io/embed/job_board?for=stripe", UrlKind.ATS_BOARD),
    ("https://jobs.lever.co/acme", UrlKind.ATS_BOARD),
    ("https://jobs.lever.co/acme/5c9b0a9e-1111-2222-3333-444455556666", UrlKind.ATS_POSTING),
    ("https://jobs.ashbyhq.com/openai", UrlKind.ATS_BOARD),
    ("https://jobs.ashbyhq.com/openai/5c9b0a9e-1111-2222-3333-444455556666", UrlKind.ATS_POSTING),
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", UrlKind.ATS_BOARD),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA/Engineer_JR1", UrlKind.ATS_POSTING),
    ("https://linkedin.com/jobs/view/1", UrlKind.GATED),
    ("https://in.indeed.com/viewjob?jk=1", UrlKind.GATED),
    ("https://internshala.com/internship/detail/x", UrlKind.GATED),
    ("https://glassdoor.co.in/job-listing/x", UrlKind.GATED),
    ("https://acme.com/careers", UrlKind.CAREERS_PAGE),
    ("https://acme.com/careers/", UrlKind.CAREERS_PAGE),
    ("https://careers.acme.com", UrlKind.CAREERS_PAGE),
    ("https://acme.com/careers/teams", UrlKind.CAREERS_PAGE),
    ("https://acme.com/careers/jobs/backend-engineer-123", UrlKind.POSTING),
    ("https://acme.com/jobs/12345", UrlKind.POSTING),
    ("https://careers.acme.com/software-engineer-intern-bengaluru", UrlKind.POSTING),
    ("https://acme.com/careers?gh_jid=555", UrlKind.POSTING),
    ("https://acme.com/blog/we-are-hiring-engineers", UrlKind.OTHER),
    ("https://acme.com/careers/blog/life-at-acme", UrlKind.OTHER),
    ("https://medium.com/@someone/how-to-get-hired", UrlKind.OTHER),
    ("https://www.ziprecruiter.com/jobs/x", UrlKind.OTHER),
    ("https://news.example.org/2026/10/startup-raises-funds", UrlKind.OTHER),
    ("https://acme.com", UrlKind.OTHER),
    ("ftp://acme.com/careers", UrlKind.OTHER),
])
def test_classify_url(url: str, kind: UrlKind) -> None:
    assert classify_url(url) == kind


# --------------------------------------------------------------------------- search plans
def test_country_code() -> None:
    assert country_code("Bengaluru, Karnataka, India") == "IN"
    assert country_code("Delhi") == "IN"
    assert country_code("San Francisco, CA") == "US"
    assert country_code("United Kingdom") == "GB"
    assert country_code("Remote") is None and country_code(None) is None and country_code("Atlantis") is None


def test_search_plans_shape_and_domains() -> None:
    q = SearchQuery(keywords=["Backend Engineer", "Data Engineer"], locations=["Bengaluru, India", "London"], seniority=["entry"],
                    skills=["python", "fastapi", "aws"], companies=["Razorpay"], posted_within_days=30)
    plans = build_search_plans(q, 12, catalog_gap_companies=["Google", "Razorpay", "Meta", "Apple", "Netflix"])
    assert {p.kind for p in plans} == {"ats", "open", "company"}
    assert len(plans) == len({(p.kind, p.query) for p in plans})
    for p in plans:
        assert not any(is_gated_url(f"https://{d}/") for d in p.include_domains)
        if p.kind == "ats":
            assert p.include_domains == ATS_SEARCH_DOMAINS
            assert "python fastapi" in p.query and "aws" not in p.query  # first two skills only
        else:
            assert not p.include_domains
            assert set(GATED_EXCLUDE_DOMAINS) <= set(p.exclude_domains)
        expected_after = None if p.kind == "company" else datetime.now(UTC).date() - timedelta(days=30)
        assert p.after_date == expected_after  # a careers page has no posting date to filter on
    open_plan = next(p for p in plans if p.kind == "open")
    assert "careers apply" in open_plan.query and "entry level" in open_plan.query
    companies = [p.company for p in plans if p.kind == "company"]
    assert companies == ["Razorpay", "Google", "Meta", "Apple"]  # own list first, 3 catalog gaps, no duplicates
    assert plans[0].location in {"IN", "GB"}


@pytest.mark.parametrize("cap", [0, 1, 2, 5])
def test_search_plans_respect_cap(cap: int) -> None:
    q = SearchQuery(keywords=["Engineer"], locations=["Delhi", "Pune"], companies=["Stripe", "Acme"])
    assert len(build_search_plans(q, cap)) == min(cap, 6)


def test_search_plans_visa_and_seniority_terms() -> None:
    q = SearchQuery(keywords=["Backend Engineer"], locations=["Berlin"], seniority=["senior"], needs_sponsorship=True)
    plans = build_search_plans(q, 8)
    assert all("visa sponsorship" in p.query for p in plans if p.kind in ("ats", "open"))
    assert all("senior" in p.query for p in plans if p.kind == "ats")
    plain = build_search_plans(SearchQuery(keywords=["Backend Engineer"]), 8)
    assert not any("visa sponsorship" in p.query for p in plain)


def test_search_plans_intern_defaults_and_empty() -> None:
    q = SearchQuery(keywords=["Software Engineer Intern"], internships_only=True)
    assert "internship" not in build_search_plans(q, 4)[0].query  # the role already says intern
    q = SearchQuery(keywords=["Software Engineer"], internships_only=True)
    assert "internship" in build_search_plans(q, 4)[0].query
    assert build_search_plans(SearchQuery(), 8) == []
    only_company = build_search_plans(SearchQuery(companies=["Stripe"]), 8)
    assert [p.kind for p in only_company] == ["company"] and only_company[0].query == "Stripe careers"


# --------------------------------------------------------------------------- pages
def test_parse_posting_html_prefers_jsonld_graph() -> None:
    job = parse_posting_html(fx("page_posting.html"), "https://acme.com/careers/jobs/backend-engineer-intern-123")
    assert job is not None
    assert job.role_title == "Backend Engineer Intern" and job.company_name == "Acme Robotics"
    assert job.location == "Bengaluru, IN" and job.job_type == JobType.INTERNSHIP
    assert job.experience_level == ExperienceLevel.INTERNSHIP
    assert job.posted_date is not None and job.posted_date.isoformat() == "2026-10-10"
    assert "visa sponsorship" in job.description
    assert job.source_url == "https://acme.com/careers/jobs/backend-engineer-intern-123"
    assert job.application_url == job.source_url


def test_parse_posting_html_falls_back_to_h1_and_main() -> None:
    html = ("<html><head><title>x</title><meta property='og:site_name' content='Beta Labs'></head><body><nav>menu</nav>"
            "<main><h1>Senior Data Engineer</h1><p>Location: Pune, India</p>"
            "<p>You will build pipelines for our analytics platform. Posted 2026-10-09.</p></main>"
            "<script>var x = 1;</script></body></html>")
    job = parse_posting_html(html, "https://beta.io/jobs/12")
    assert job is not None
    assert job.company_name == "Beta Labs" and job.role_title == "Senior Data Engineer"
    assert job.location == "Pune, India" and job.experience_level == ExperienceLevel.SENIOR
    assert job.posted_date is not None and job.posted_date.isoformat() == "2026-10-09"
    assert "var x" not in job.description and "menu" not in job.description
    assert job.source_platform == ATSPlatform.CUSTOM


def test_parse_posting_html_title_at_company_and_hit() -> None:
    body = "<html><body><h1>Backend Engineer at Gamma</h1><main>" + "Build things. " * 10 + "</main></body></html>"
    job = parse_posting_html(body, "https://careers.gamma.dev/x1")
    assert job is not None and job.role_title == "Backend Engineer" and job.company_name == "Gamma"
    hit = SimpleNamespace(title="Platform Engineer - Delta", snippet="", url="https://delta.io/jobs/9", site_name="delta.io", date=None)
    no_title = "<html><body><main>" + "We are hiring a platform engineer to scale our infra. " * 3 + "</main></body></html>"
    job = parse_posting_html(no_title, "https://delta.io/jobs/9", hit)  # type: ignore[arg-type]
    assert job is not None and job.role_title == "Platform Engineer - Delta"


def test_parse_posting_html_empty_and_listing() -> None:
    assert parse_posting_html("", "https://acme.com/jobs/1") is None
    assert parse_posting_html("<html><body></body></html>", "https://acme.com/jobs/1") is None
    assert parse_posting_html("<html><body><h1>Hi</h1></body></html>", "https://acme.com/jobs/1") is None
    two = ("<html><head><script type='application/ld+json'>[{\"@type\":\"JobPosting\",\"title\":\"A\"},"
           "{\"@type\":\"JobPosting\",\"title\":\"B\"}]</script></head><body><h1>Jobs</h1>" + "x " * 50 + "</body></html>")
    assert parse_posting_html(two, "https://acme.com/careers") is None  # several postings: not one posting


def test_parse_posting_markdown() -> None:
    md = ("# Backend Engineer Intern\n\n**Location:** Bengaluru, India\n\nPosted 2 days ago\n\n"
          "Build [Python](https://python.org) services with FastAPI for our fleet of robots. Great mentors.\n\n"
          "[Apply now](https://apply.acme.com/j/123)\n[Share on LinkedIn](https://www.linkedin.com/share)")
    job = parse_posting_markdown(md, "https://acme.com/careers/jobs/backend-engineer-intern-123")
    assert job is not None
    assert job.role_title == "Backend Engineer Intern" and job.company_name == "Acme"
    assert job.location == "Bengaluru, India"
    assert job.application_url == "https://apply.acme.com/j/123"
    assert job.posted_date is not None and (datetime.now(UTC).date() - job.posted_date).days == 2
    assert "Python" in job.description and "](" not in job.description and "#" not in job.description
    assert parse_posting_markdown("", "https://acme.com/jobs/1") is None
    assert parse_posting_markdown("# Careers\nshort", "https://acme.com/jobs/1") is None
    gated_apply = parse_posting_markdown("# Role\n\n" + "text " * 20 + "\n[Apply](https://www.linkedin.com/jobs/view/1)", "https://acme.com/jobs/1")
    assert gated_apply is not None and gated_apply.application_url == "https://acme.com/jobs/1"


def test_markdown_links() -> None:
    text = '![logo](/x.png) [Jobs](/jobs "All jobs") and [A](https://a.com/x) [empty]() [Docs](docs/a)'
    assert markdown_links(text) == [("Jobs", "/jobs"), ("A", "https://a.com/x"), ("Docs", "docs/a")]
    assert markdown_links(text, "https://acme.com/careers/")[0] == ("Jobs", "https://acme.com/jobs")


def test_job_links_from_page_on_careers_markdown() -> None:
    q = SearchQuery(keywords=["Backend Engineer"])
    links = job_links_from_page(page(fx("page_careers.md")), q)
    assert links == ["https://acme.com/careers/jobs/backend-engineer-intern-123", "https://acme.com/careers/jobs/backend-engineer-456"]
    assert job_links_from_page(page(fx("page_careers.md")), q, limit=1) == links[:1]
    assert all(not is_gated_url(link) for link in job_links_from_page(page(fx("page_careers.md")), SearchQuery()))
    everything = job_links_from_page(page(fx("page_careers.md")), SearchQuery())
    assert "https://acme.com/careers/jobs/senior-marketing-manager-789" in everything


def test_job_links_from_page_html_and_bare_links() -> None:
    html = ("<a href='/careers/jobs/data-engineer-1'>Data Engineer</a><a href='/careers/jobs/chef-2'>Chef</a>"
            "<a href='/about'>About</a><a href='mailto:x@y.z'>Mail</a>")
    q = SearchQuery(keywords=["Data Engineer"])
    assert job_links_from_page(page(html, "html"), q) == ["https://acme.com/careers/jobs/data-engineer-1"]
    bare = page("", links=["https://acme.com/careers/jobs/data-engineer-77", "https://acme.com/careers/jobs/chef-78"])
    assert job_links_from_page(bare, q) == ["https://acme.com/careers/jobs/data-engineer-77"]


def test_ats_boards_in_page() -> None:
    boards = ats_boards_in_page(page(fx("page_careers.md"), links=["https://job-boards.greenhouse.io/other-co"]))
    assert boards["lever_companies"] == {"acme-robotics"}
    assert boards["greenhouse_boards"] == {"acmerobotics", "other-co"}
    assert boards["ashby_boards"] == {"acme-labs"}
    assert boards["workday_sites"] == set()


def test_looks_js_shell() -> None:
    assert looks_js_shell(fx("page_js_shell.html"))
    assert looks_js_shell("")
    assert looks_js_shell("<html><body><p>Please enable JavaScript to continue.</p></body></html>")
    assert looks_js_shell("<html><body>Just a moment...<script src='/cdn-cgi/challenge-platform/x.js'></script></body></html>")
    assert looks_js_shell("Please enable JavaScript to view this page")
    assert not looks_js_shell(fx("page_posting.html"))  # short, but it is JSON-LD data, not an app
    long_page = "<html><body><script>1</script><main>" + "A real careers page with plenty of readable text. " * 30 + "</main></body></html>"
    assert not looks_js_shell(long_page)
    assert not looks_js_shell(fx("page_careers.md") * 3)
    noscript_footer = long_page.replace("</main>", "</main><noscript>Enable JavaScript for the map</noscript>")
    assert not looks_js_shell(noscript_footer)
    assert not looks_js_shell("<html><body><h1>Not found</h1></body></html>")  # tiny, but no scripts or markers


# --------------------------------------------------------------------------- Agent
def _walk(node: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.append((k, v))
            out.extend(_walk(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_walk(v))
    return out


def test_output_schema_uses_only_accepted_keywords() -> None:
    allowed = {"type", "properties", "items", "maxItems", "required", "enum", "nullable"}
    pairs = _walk(JOBS_OUTPUT_SCHEMA)
    keys = {k for k, _ in pairs}
    for banned in ("additionalProperties", "const", "oneOf", "anyOf", "$ref", "format"):
        assert banned not in keys
    assert not any(k == "type" and isinstance(v, list) for k, v in pairs)
    # every schema node (not a property name) only uses accepted keywords
    def check(schema: dict[str, Any]) -> None:
        assert set(schema) <= allowed
        for sub in (schema.get("properties") or {}).values():
            check(sub)
        if "items" in schema:
            check(schema["items"])
    check(JOBS_OUTPUT_SCHEMA)
    assert JOBS_OUTPUT_SCHEMA["type"] == "object" and JOBS_OUTPUT_SCHEMA["required"] == ["jobs"]
    json.dumps(JOBS_OUTPUT_SCHEMA)


def test_build_agent_goal() -> None:
    q = SearchQuery(keywords=["Backend Engineer"], locations=["Bengaluru"], seniority=["entry"])
    goal = build_agent_goal(q, "https://acme.com/careers")
    assert goal.startswith("1. Open https://acme.com/careers")
    for needle in ("Backend Engineer", "Bengaluru", "entry", "at most 3 times", "at most 25", "apply_url", "do not guess",
                   "Never log in", '"blocked": true'):
        assert needle in goal
    assert [line[:2] for line in goal.splitlines()] == [f"{i}." for i in range(1, 8)]
    single = build_agent_goal(None, "https://acme.com/jobs/1", single_posting=True)
    assert "single job posting" in single and "Find the open job listings" not in single


def test_agent_jobs_to_scraped_filters_untrusted_output() -> None:
    result = json.loads(fx("page_agent_result.json"))
    jobs = agent_jobs_to_scraped(result, "https://acme.com/careers", run_id="run_1")
    assert [j.role_title for j in jobs] == ["Backend Engineer Intern", "Platform Engineer"]
    first, second = jobs
    assert first.source_url == "https://acme.com/careers/jobs/backend-engineer-intern-123"  # canonical
    assert first.company_name == "Acme Robotics" and first.location == "Bengaluru, India"
    assert first.job_type == JobType.INTERNSHIP and first.experience_level == ExperienceLevel.INTERNSHIP
    assert first.description == "Build Python services."
    assert first.raw is not None and first.raw["sponsorship"] == "Visa sponsorship offered"
    assert first.raw["tinyfish"]["via"] == ["agent"] and first.raw["tinyfish"]["agent_run_id"] == "run_1"
    assert second.source_platform == ATSPlatform.GREENHOUSE and second.experience_level == ExperienceLevel.SENIOR
    assert second.raw is not None and second.raw["sponsorship"] == "No visa sponsorship"
    assert second.posted_date is not None


def test_agent_jobs_to_scraped_rejections_and_caps() -> None:
    page_url = "https://careers.acme.co.uk/openings"

    def item(url: str, n: int = 0) -> dict[str, Any]:
        return {"title": f"Engineer {n}", "apply_url": url}

    assert agent_jobs_to_scraped({"jobs": [item("http://careers.acme.co.uk/j/1")]}, page_url) == []  # http
    assert agent_jobs_to_scraped({"jobs": [item("https://www.linkedin.com/jobs/view/1")]}, page_url) == []
    assert agent_jobs_to_scraped({"jobs": [item("https://in.indeed.com/viewjob?jk=1")]}, page_url) == []
    assert agent_jobs_to_scraped({"jobs": [item("https://evil.example.net/jobs/1")]}, page_url) == []
    assert agent_jobs_to_scraped({"jobs": [item("https://acme.co.uk.evil.net/jobs/1")]}, page_url) == []
    assert agent_jobs_to_scraped({"jobs": [item("https://user:pw@jobs.acme.co.uk/1")]}, page_url) == []
    assert agent_jobs_to_scraped({"jobs": [item("javascript:alert(1)")]}, page_url) == []
    assert len(agent_jobs_to_scraped({"jobs": [item("https://jobs.acme.co.uk/1"), item("https://jobs.lever.co/acme/abc")]}, page_url)) == 2
    assert agent_jobs_to_scraped({"jobs": [item("https://jobs.acme.co.uk/1"), item("https://jobs.acme.co.uk/1/?utm_source=x")]}, page_url).__len__() == 1
    many = {"jobs": [item(f"https://acme.co.uk/jobs/{i}", i) for i in range(40)]}
    assert len(agent_jobs_to_scraped(many, page_url)) == 25
    for bad in (None, {}, {"jobs": "nope"}, {"jobs": [None, 3, "x", {}]}, {"blocked": True, "jobs": []}):
        assert agent_jobs_to_scraped(bad, page_url) == []  # type: ignore[arg-type]
    long = {"jobs": [{**item("https://acme.co.uk/jobs/1"), "summary": "x" * 9000}]}
    assert len(agent_jobs_to_scraped(long, page_url)[0].description) == 4000


def test_agent_company_falls_back_to_ats_board_name() -> None:
    jobs = agent_jobs_to_scraped({"jobs": [{"title": "SWE", "apply_url": "https://boards.greenhouse.io/acme-robotics/jobs/1"}]},
                                 "https://boards.greenhouse.io/acme-robotics")
    assert jobs[0].company_name == "Acme Robotics"


def test_tag_provenance_shape_keeps_existing_raw() -> None:
    job = ScrapedJob(company_name="A", role_title="B", description="", source_url="https://a.com/1", source_platform=ATSPlatform.CUSTOM,
                     raw={"board_token": "a"})
    out = tag_provenance(job, via=["search", "fetch"], query="backend", hit_url="https://a.com/1", hit_title="B at A")
    assert out is job
    assert job.raw is not None
    assert job.raw["board_token"] == "a" and job.raw["discovered_by"] == "tinyfish"
    tf = job.raw["tinyfish"]
    assert tf["via"] == ["search", "fetch"] and tf["query"] == "backend" and tf["agent_run_id"] is None
    assert tf["hit_url"] == "https://a.com/1" and tf["hit_title"] == "B at A"
    assert datetime.strptime(tf["fetched_at"], "%Y-%m-%dT%H:%M:%SZ")
    bare = tag_provenance(ScrapedJob(company_name="A", role_title="B", description="", source_url="u", source_platform=ATSPlatform.CUSTOM),
                          via=["agent"], agent_run_id="r1")
    assert bare.raw is not None and bare.raw["tinyfish"]["agent_run_id"] == "r1"
