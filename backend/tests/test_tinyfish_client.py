"""TinyFish client tests: every call is mocked with respx, so nothing here can reach the network."""

import json
import logging
import threading
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from app.config import settings
from app.scrapers.base import RateLimited, ScraperError
from app.services import tinyfish_client as tf
from app.services.rate_limiter import rate_limiter
from app.services.tinyfish_client import (
    NOT_CONFIGURED_HINT,
    TinyFishAuthError,
    TinyFishClient,
    TinyFishError,
    TinyFishNotConfigured,
    TinyFishQuotaError,
    TinyFishRateLimited,
    TinyFishUnavailable,
    get_client,
    tinyfish_available,
    usage_today,
)
from app.services.tinyfish_types import AgentEvent, AgentRunResult, FetchedPage, FetchFailure, FetchResult, UsageCounters
from tests.conftest import FIXTURES

FX = FIXTURES / "tinyfish"
KEY = "tf-test-key-0123456789abcdef"
SEARCH = "https://api.search.tinyfish.ai/"
FETCH = "https://api.fetch.tinyfish.ai/"
AGENT = "https://agent.tinyfish.ai"
SSE = f"{AGENT}/v1/automation/run-sse"
SYNC = f"{AGENT}/v1/automation/run"

mocked = respx.mock


def fx(name: str) -> str:
    return (FX / name).read_text()


def fxj(name: str) -> dict:
    return json.loads(fx(name))


def sse_response(text: str | None = None, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=(text if text is not None else fx("api_agent_sse.txt")).encode(), headers={"content-type": "text/event-stream"})


def sse_lines(*events: dict) -> str:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


def error_body(code: str, message: str = "nope", details: object = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}, "request_id": "req_1"}


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:
    """Backoff and polling sleeps are recorded instead of slept."""
    slept: list[float] = []
    monkeypatch.setattr(tf, "_sleep", slept.append)
    yield slept


@pytest.fixture
def sleeps(_no_waiting: list[float]) -> list[float]:
    return _no_waiting


def make(*, usage: UsageCounters | None = None, user_id: str | None = None) -> TinyFishClient:
    return TinyFishClient(KEY, usage=usage, user_id=user_id)


# ------------------------------------------------------------------------------------------------ config
def test_enabled_truth_table(monkeypatch: pytest.MonkeyPatch) -> None:
    assert settings.TINYFISH_API_KEY in (None, "") and settings.tinyfish_enabled is False  # conftest blanks the key
    assert tinyfish_available() is False
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", KEY)
    assert settings.tinyfish_enabled is True and tinyfish_available() is True
    assert settings.tinyfish_agent_allowed is True
    monkeypatch.setattr(settings, "TINYFISH_ENABLED", False)
    assert settings.tinyfish_enabled is False and settings.tinyfish_agent_allowed is False
    monkeypatch.setattr(settings, "TINYFISH_ENABLED", True)
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", "   ")
    assert settings.tinyfish_enabled is False
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", KEY)
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    assert settings.tinyfish_enabled is True and settings.tinyfish_agent_allowed is False


def test_get_client_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(TinyFishNotConfigured) as exc:  # no key (conftest sets TINYFISH_API_KEY="")
        get_client()
    assert "TINYFISH_API_KEY" in str(exc.value) and str(exc.value) == NOT_CONFIGURED_HINT
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", KEY)
    monkeypatch.setattr(settings, "TINYFISH_ENABLED", False)
    with pytest.raises(TinyFishNotConfigured, match="TINYFISH_ENABLED"):
        get_client()
    monkeypatch.setattr(settings, "TINYFISH_ENABLED", True)
    client = get_client("user-1")
    assert client.configured and client.user_id == "user-1"
    assert KEY not in repr(client)


def test_unconfigured_client_never_calls_out() -> None:
    client = TinyFishClient("")
    assert not client.configured
    with respx.mock() as router:  # any request would fail the test: nothing is mocked
        for call in (
            lambda: client.search("backend engineer"),
            lambda: client.fetch(["https://a.example/"]),
            lambda: client.run_agent("https://a.example/", "goal"),
            lambda: client.get_run("run_1"),
        ):
            with pytest.raises(TinyFishNotConfigured):
                call()
        assert not router.calls


def test_error_types() -> None:
    assert issubclass(TinyFishError, ScraperError)
    assert issubclass(TinyFishRateLimited, RateLimited) and issubclass(TinyFishRateLimited, TinyFishError)
    for cls in (TinyFishNotConfigured, TinyFishAuthError, TinyFishQuotaError, TinyFishUnavailable):
        assert issubclass(cls, TinyFishError)


def test_rate_limiter_additions() -> None:
    from app.services.rate_limiter import limit_for

    assert limit_for("tinyfish").requests_per_hour == 1500
    assert rate_limiter.incr_counter("tf:test", 60) == 1
    assert rate_limiter.incr_counter("tf:test", 60, 4) == 5
    assert rate_limiter.get_counter("tf:test") == 5
    assert rate_limiter.get_counter("tf:missing") == 0


# ------------------------------------------------------------------------------------------------ types
def test_types() -> None:
    usage = UsageCounters()
    assert usage.endpoints_used() == []
    usage.add(search_calls=2, fetch_urls=5, fetch_calls=1)
    usage.add(agent_runs=1)
    assert usage.as_dict() == {"search_calls": 2, "fetch_calls": 1, "fetch_urls": 5, "agent_runs": 1, "agent_steps": 0, "errors": 0}
    assert usage.endpoints_used() == ["search", "fetch", "agent"]
    with pytest.raises(ValueError):
        usage.add(bogus=1)

    page = FetchedPage(url="https://a.example/x", final_url="https://www.a.example/y/", title="t", text="x", format="markdown")
    failure = FetchFailure(url="https://b.example/", code="bot_blocked", status=403)
    result = FetchResult(pages=[page], failures=[failure])
    assert result.page_for("https://a.example/x/") is page and result.page_for("https://www.a.example/y") is page
    assert result.page_for("https://nope.example/") is None
    assert result.failure_for("https://b.example") is failure and result.failure_for("https://a.example/x") is None

    assert AgentRunResult("r", "COMPLETED", result={"jobs": []}).ok
    assert not AgentRunResult("r", "COMPLETED").ok
    assert not AgentRunResult("r", "COMPLETED", result={}, error_code="TASK_FAILED").ok
    assert not AgentRunResult("r", "FAILED", result={"jobs": []}).ok


# ------------------------------------------------------------------------------------------------ search
@mocked
def test_search_params_and_parsing() -> None:
    route = respx.get(SEARCH).mock(return_value=httpx.Response(200, json=fxj("api_search.json")))
    client = make()
    hits = client.search(
        "backend engineer",
        purpose="Find openings",
        location="US",
        include_domains=["boards.greenhouse.io", "jobs.lever.co"],
        exclude_domains=["linkedin.com", "indeed.com"],
        after_date=date(2026, 9, 14),
    )
    request = route.calls.last.request
    assert request.headers["X-API-Key"] == KEY
    params = dict(request.url.params)
    assert params == {
        "query": "backend engineer",
        "purpose": "Find openings",
        "location": "US",
        "include_domains": "boards.greenhouse.io,jobs.lever.co",
        "exclude_domains": "linkedin.com,indeed.com",
        "after_date": "2026-09-14",
    }
    assert len(hits) == 5 and hits[0].position == 1
    assert hits[0].url == "https://job-boards.greenhouse.io/stripe/jobs/8031833" and hits[0].site_name == "job-boards.greenhouse.io"
    assert hits[0].date is None
    # `date` is free text from the search engine: kept as it came, never parsed
    assert hits[2].date == "2 days ago" and hits[4].date == "Oct 5, 2026"
    assert client.usage.as_dict()["search_calls"] == 1


@mocked
def test_search_page_param_and_tolerant_parsing() -> None:
    body = {"results": [{"url": "https://a.example/1", "date": 20261005}, {"title": "no url"}, "junk", {"url": "https://a.example/2", "position": "x"}]}
    route = respx.get(SEARCH).mock(return_value=httpx.Response(200, json=body))
    hits = make().search("x" * 3000, page=2)
    assert len(route.calls.last.request.url.params["query"]) == 2000 and route.calls.last.request.url.params["page"] == "2"
    assert [h.url for h in hits] == ["https://a.example/1", "https://a.example/2"]
    assert hits[0].date == "20261005" and hits[0].position == 1 and hits[1].position == 4  # a bad position falls back to the row number
    assert hits[0].title == "" and hits[0].snippet == ""


@mocked
def test_search_empty_results_and_blank_query() -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"query": "q", "results": [], "total_results": 0}))
    assert make().search("q") == []
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"results": None}))
    assert make().search("q") == []
    with pytest.raises(TinyFishError, match="query"):
        make().search("   ")


# ------------------------------------------------------------------------------------------------ fetch
@mocked
def test_fetch_batches_23_urls_into_10_10_3() -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sizes.append(len(body["urls"]))
        assert body["format"] == "html" and body["ttl"] == 0 and body["per_url_timeout_ms"] == 30000
        assert body["links"] is True and request.headers["X-API-Key"] == KEY
        return httpx.Response(200, json={"results": [{"url": u, "final_url": u, "title": "T", "text": "<html></html>", "links": []} for u in body["urls"]], "errors": []})

    respx.post(FETCH).mock(side_effect=handler)
    urls = [f"https://site{i}.example/jobs" for i in range(23)]
    client = make()
    result = client.fetch(urls, format="html", per_url_timeout_ms=30000)
    assert sizes == [10, 10, 3]
    assert [p.url for p in result.pages] == urls and not result.failures
    assert result.pages[0].format == "html"
    assert client.usage.as_dict()["fetch_calls"] == 3 and client.usage.as_dict()["fetch_urls"] == 23


@mocked
def test_fetch_mixed_results_and_failures_never_raise() -> None:
    route = respx.post(FETCH).mock(return_value=httpx.Response(200, json=fxj("api_fetch.json")))
    result = make().fetch(
        ["https://acme.example/careers", "https://globex.example/jobs/1", "https://blocked.example/careers", "https://empty.example/jobs"],
        purpose="Read careers pages",
        ttl=None,
        include_selectors=["main"],
        exclude_selectors=["footer"],
    )
    body = json.loads(route.calls.last.request.content)
    assert "ttl" not in body and body["purpose"] == "Read careers pages"
    assert body["include_selectors"] == ["main"] and body["exclude_selectors"] == ["footer"] and body["format"] == "markdown"
    assert len(result.pages) == 2 and len(result.failures) == 2
    acme = result.page_for("https://acme.example/careers")
    assert acme and acme.format == "markdown" and acme.latency_ms == 812.0 and len(acme.links) == 2
    globex = result.page_for("https://globex.example/jobs/1")
    assert globex and globex.final_url == "https://www.globex.example/jobs/1/"
    blocked = result.failure_for("https://blocked.example/careers")
    assert blocked and blocked.code == "bot_blocked" and blocked.status == 403
    empty = result.failure_for("https://empty.example/jobs")
    assert empty and empty.code == "empty_content"


@mocked
def test_fetch_all_urls_failed_is_a_normal_result() -> None:
    respx.post(FETCH).mock(return_value=httpx.Response(200, json={"results": [], "errors": [{"url": "https://x.example/", "error": "timeout", "status": None}]}))
    result = make().fetch(["https://x.example/", "https://x.example/"])  # duplicates are sent once
    assert result.pages == [] and [f.code for f in result.failures] == ["timeout"]
    assert make().fetch([]).pages == []


@mocked
def test_fetch_http_422_is_an_error() -> None:
    respx.post(FETCH).mock(return_value=httpx.Response(422, json={"detail": [{"msg": "invalid selector"}]}))
    with pytest.raises(TinyFishError, match="HTTP 422") as exc:
        make().fetch(["https://x.example/"], include_selectors=["<<"])
    assert not isinstance(exc.value, TinyFishAuthError | TinyFishQuotaError | TinyFishRateLimited | TinyFishUnavailable)
    assert "invalid selector" in str(exc.value)


# ------------------------------------------------------------------------------------------------ agent: SSE
@mocked
def test_agent_sse_events_and_result() -> None:
    route = respx.post(SSE).mock(return_value=sse_response())
    events: list[AgentEvent] = []
    schema = {"type": "object", "properties": {"jobs": {"type": "array", "items": {"type": "object"}}}}
    client = make()
    result = client.run_agent(
        "https://acme.example/careers", "List jobs", output_schema=schema, stealth=True, proxy_country="US",
        max_duration_seconds=120, on_event=events.append,
    )
    assert [e.type for e in events] == ["STARTED", "STREAMING_URL", "PROGRESS", "HEARTBEAT", "PROGRESS", "COMPLETE"]
    assert events[1].streaming_url == "https://stream.tinyfish.example/run_123" and events[2].purpose == "Opening the careers page"
    assert events[0].run_id == "run_123" and events[-1].status == "COMPLETED"
    assert result.ok and result.run_id == "run_123" and result.steps == 6
    assert result.streaming_url == "https://stream.tinyfish.example/run_123"
    assert result.result and result.result["jobs"][0]["title"] == "Backend Engineer"
    request = route.calls.last.request
    assert request.headers["X-API-Key"] == KEY
    body = json.loads(request.content)
    assert body == {
        "url": "https://acme.example/careers",
        "goal": "List jobs",
        "browser_profile": "stealth",
        "agent_config": {"max_duration_seconds": 120},
        "proxy_config": {"enabled": True, "country_code": "US"},
        "output_schema": schema,
    }
    assert client.usage.as_dict()["agent_runs"] == 1 and client.usage.as_dict()["agent_steps"] == 6


@mocked
def test_agent_default_payload_uses_lite_profile_and_configured_duration() -> None:
    route = respx.post(SSE).mock(return_value=sse_response())
    make().run_agent("https://a.example/", "goal")
    body = json.loads(route.calls.last.request.content)
    assert body["browser_profile"] == "lite" and body["agent_config"] == {"max_duration_seconds": settings.TINYFISH_AGENT_MAX_DURATION_SECONDS}
    assert "output_schema" not in body and "proxy_config" not in body


@mocked
def test_agent_failed_run_and_run_level_errors_are_results() -> None:
    text = sse_lines(
        {"type": "STARTED", "run_id": "run_9"},
        {"type": "COMPLETE", "run_id": "run_9", "status": "FAILED", "error": {"code": "BILLING_REJECTED", "message": "Out of credits", "category": "billing"}},
    )
    respx.post(SSE).mock(return_value=sse_response(text))
    client = make()
    result = client.run_agent("https://a.example/", "goal")
    assert not result.ok and result.status == "FAILED"
    assert result.error_code == "BILLING_REJECTED" and result.error_message == "Out of credits"
    assert client.usage.as_dict()["errors"] == 1


@mocked
def test_agent_completed_without_result_is_not_ok() -> None:
    text = sse_lines({"type": "STARTED", "run_id": "r"}, {"type": "COMPLETE", "run_id": "r", "status": "COMPLETED", "result": None, "error": None})
    respx.post(SSE).mock(return_value=sse_response(text))
    result = make().run_agent("https://a.example/", "goal")
    assert result.status == "COMPLETED" and result.result is None and not result.ok


@mocked
def test_agent_ignores_garbage_lines_and_callback_errors() -> None:
    text = ": keep-alive\n\nevent: ping\ndata: not json\n\ndata:[1,2]\n\n" + sse_lines(
        {"type": "STARTED", "run_id": "r"}, {"type": "COMPLETE", "run_id": "r", "status": "COMPLETED", "result": {"jobs": []}}
    )
    respx.post(SSE).mock(return_value=sse_response(text))

    def boom(_event: AgentEvent) -> None:
        raise RuntimeError("ui exploded")

    assert make().run_agent("https://a.example/", "goal", on_event=boom).ok


@mocked
def test_agent_stream_cut_recovers_through_get_run(sleeps: list[float]) -> None:
    cut = sse_lines({"type": "STARTED", "run_id": "run_123"}, {"type": "STREAMING_URL", "run_id": "run_123", "streaming_url": "https://s.example/1"},
                    {"type": "PROGRESS", "run_id": "run_123", "purpose": "Opening"})
    respx.post(SSE).mock(return_value=sse_response(cut))
    running = {"run_id": "run_123", "status": "RUNNING", "num_of_steps": 2}
    poll = respx.get(f"{AGENT}/v1/runs/run_123").mock(side_effect=[httpx.Response(200, json=running), httpx.Response(200, json=running), httpx.Response(200, json=fxj("api_run_status.json"))])
    result = make().run_agent("https://a.example/", "goal")
    assert poll.call_count == 3 and sleeps == [tf.AGENT_POLL_SECONDS] * 2
    assert result.ok and result.run_id == "run_123" and result.steps == 6
    assert result.streaming_url == "https://stream.tinyfish.example/run_123"


@mocked
def test_agent_transport_error_mid_stream_recovers_through_get_run() -> None:
    def chunks() -> Iterator[bytes]:
        yield sse_lines({"type": "STARTED", "run_id": "run_5"}).encode()
        raise httpx.ReadTimeout("no data for 60s")

    respx.post(SSE).mock(return_value=httpx.Response(200, content=chunks(), headers={"content-type": "text/event-stream"}))
    respx.get(f"{AGENT}/v1/runs/run_5").mock(return_value=httpx.Response(200, json={"run_id": "run_5", "status": "FAILED", "error": {"code": "SITE_BLOCKED", "message": "blocked"}}))
    result = make().run_agent("https://a.example/", "goal")
    assert result.status == "FAILED" and result.error_code == "SITE_BLOCKED" and not result.ok


@mocked
def test_agent_stream_ends_before_started() -> None:
    respx.post(SSE).mock(return_value=sse_response(": nothing\n\n"))
    result = make().run_agent("https://a.example/", "goal")
    assert result.status == "FAILED" and result.error_code == "STREAM_ENDED" and result.run_id is None


@mocked
def test_agent_poll_gives_up_with_timeout_and_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.post(SSE).mock(return_value=sse_response(sse_lines({"type": "STARTED", "run_id": "run_7"})))
    respx.get(f"{AGENT}/v1/runs/run_7").mock(return_value=httpx.Response(200, json={"run_id": "run_7", "status": "RUNNING"}))
    cancel = respx.post(f"{AGENT}/v1/runs/run_7/cancel").mock(return_value=httpx.Response(200, json={}))
    ticks = iter(range(0, 10_000, 50))
    monkeypatch.setattr(tf, "_clock", lambda: float(next(ticks)))
    result = make().run_agent("https://a.example/", "goal", max_duration_seconds=60)
    assert result.status == "TIMEOUT" and result.error_code == "TIMEOUT" and result.run_id == "run_7" and not result.ok
    assert cancel.called


@mocked
def test_agent_404_on_sse_falls_back_to_sync_run() -> None:
    sse = respx.post(SSE).mock(return_value=httpx.Response(404, json=error_body("NOT_FOUND")))
    sync = respx.post(SYNC).mock(return_value=httpx.Response(200, json=fxj("api_run_status.json")))
    client = make()
    result = client.run_agent("https://a.example/", "goal", max_duration_seconds=90)
    assert sse.call_count == 1 and sync.call_count == 1
    body = json.loads(sync.calls.last.request.content)
    assert body["goal"] == "goal" and body["agent_config"] == {"max_duration_seconds": 90}
    assert result.ok and result.run_id == "run_123" and result.steps == 6
    assert client.usage.as_dict()["agent_runs"] == 1


@mocked
def test_agent_connection_error_on_sse_falls_back_to_sync_run() -> None:
    respx.post(SSE).mock(side_effect=httpx.ConnectError("refused"))
    sync = respx.post(SYNC).mock(return_value=httpx.Response(200, json={"run_id": "r", "status": "FAILED", "error": {"code": "TIMEOUT", "message": "slow"}}))
    result = make().run_agent("https://a.example/", "goal")
    assert sync.call_count == 1 and result.error_code == "TIMEOUT" and not result.ok


@mocked
def test_agent_should_cancel_stops_the_run() -> None:
    respx.post(SSE).mock(return_value=sse_response())
    cancel = respx.post(f"{AGENT}/v1/runs/run_123/cancel").mock(return_value=httpx.Response(200, json={}))
    seen: list[str] = []
    result = make().run_agent(
        "https://a.example/", "goal", on_event=lambda e: seen.append(e.type), should_cancel=lambda: len(seen) >= 3
    )
    assert result.status == "CANCELLED" and result.run_id == "run_123" and not result.ok
    assert seen == ["STARTED", "STREAMING_URL", "PROGRESS"] and cancel.call_count == 1
    assert result.streaming_url == "https://stream.tinyfish.example/run_123"


@mocked
def test_agent_cancelled_before_start_makes_no_request() -> None:
    sse = respx.post(SSE).mock(return_value=sse_response())
    result = make().run_agent("https://a.example/", "goal", should_cancel=lambda: True)
    assert result.status == "CANCELLED" and result.run_id is None and not sse.called


@mocked
def test_get_run_and_cancel_run() -> None:
    respx.get(f"{AGENT}/v1/runs/run_123").mock(return_value=httpx.Response(200, json=fxj("api_run_status.json")))
    cancel = respx.post(f"{AGENT}/v1/runs/run_123/cancel").mock(return_value=httpx.Response(200, json={}))
    client = make()
    run = client.get_run("run_123")
    assert run.ok and run.steps == 6 and run.streaming_url
    client.cancel_run("run_123")
    assert cancel.calls.last.request.headers["X-API-Key"] == KEY
    respx.get(f"{AGENT}/v1/runs/missing").mock(return_value=httpx.Response(404, json=error_body("NOT_FOUND", "no such run")))
    with pytest.raises(TinyFishError, match="404"):
        client.get_run("missing")


@mocked
def test_agent_sse_rate_limited_then_ok_retries(sleeps: list[float]) -> None:
    respx.post(SSE).mock(side_effect=[httpx.Response(429, json=error_body("RATE_LIMIT_EXCEEDED")), sse_response()])
    assert make().run_agent("https://a.example/", "goal").ok
    assert sleeps == [tf.BACKOFF_SECONDS[0]]


@mocked
def test_agent_sse_quota_error_pauses() -> None:
    respx.post(SSE).mock(return_value=httpx.Response(402, json=error_body("INSUFFICIENT_CREDITS", "Out of credits")))
    with pytest.raises(TinyFishQuotaError):
        make().run_agent("https://a.example/", "goal")
    assert rate_limiter.is_paused("tinyfish")


# ------------------------------------------------------------------------------------------------ errors
@mocked
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, error_body("INVALID_INPUT", "bad", [{"field": "query"}]), TinyFishError),
        (401, error_body("INVALID_API_KEY"), TinyFishAuthError),
        (403, error_body("FORBIDDEN"), TinyFishError),
        (404, error_body("FEATURE_NOT_AVAILABLE"), TinyFishError),
    ],
)
def test_plain_errors_are_not_retried(status: int, body: dict, expected: type[Exception], sleeps: list[float]) -> None:
    route = respx.get(SEARCH).mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(expected) as exc:
        make().search("q")
    assert type(exc.value) is expected and route.call_count == 1 and not sleeps
    assert not rate_limiter.is_paused("tinyfish")
    if status == 400:
        assert "INVALID_INPUT" in str(exc.value) and "field" in str(exc.value)
    if status == 401:
        assert "TINYFISH_API_KEY" in str(exc.value)


@mocked
def test_402_is_quota_and_pauses_the_platform() -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(402, json=error_body("FREE_ALLOWANCE_EXHAUSTED", "used up")))
    with pytest.raises(TinyFishQuotaError):
        make().search("q")
    assert rate_limiter.is_paused("tinyfish")


@mocked
def test_429_with_details_is_quota_and_pauses_without_retry(sleeps: list[float]) -> None:
    details = {"limit": 12000, "unit": "requests", "window_seconds": 86400, "upgrade_url": "https://tinyfish.ai/pricing"}
    route = respx.get(SEARCH).mock(return_value=httpx.Response(429, json=error_body("RATE_LIMIT_EXCEEDED", "limit", details)))
    with pytest.raises(TinyFishQuotaError):
        make().search("q")
    assert route.call_count == 1 and not sleeps and rate_limiter.is_paused("tinyfish")


@mocked
def test_daily_limit_exceeded_pauses_until_midnight() -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(429, json=error_body("DAILY_LIMIT_EXCEEDED")))
    with pytest.raises(TinyFishQuotaError, match="Paused"):
        make().search("q")
    assert rate_limiter.is_paused("tinyfish")
    until = rate_limiter._get("rl:pause:tinyfish") - datetime.now(UTC).timestamp()
    assert 55 <= until <= 86400 + 10  # to UTC midnight (never less than a minute)


@mocked
def test_429_without_details_retries_then_rate_limited(sleeps: list[float]) -> None:
    route = respx.get(SEARCH).mock(return_value=httpx.Response(429, headers={"Retry-After": "7"}, json=error_body("RATE_LIMIT_EXCEEDED")))
    with pytest.raises(TinyFishRateLimited) as exc:
        make().search("q")
    assert route.call_count == 3 and sleeps == [7.0, 7.0]
    assert isinstance(exc.value, RateLimited)
    assert not rate_limiter.is_paused("tinyfish")


@mocked
def test_retry_after_is_capped(sleeps: list[float]) -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(429, headers={"Retry-After": "600"}, json=error_body("RATE_LIMIT_EXCEEDED")))
    with pytest.raises(TinyFishRateLimited):
        make().search("q")
    assert sleeps == [tf.MAX_RETRY_WAIT_SECONDS] * 2


@mocked
def test_409_retry_required_retries_then_rate_limited(sleeps: list[float]) -> None:
    route = respx.get(SEARCH).mock(return_value=httpx.Response(409, json=error_body("RETRY_REQUIRED")))
    with pytest.raises(TinyFishRateLimited):
        make().search("q")
    assert route.call_count == 3 and len(sleeps) == 2


@mocked
def test_503_retries_then_unavailable_and_recovers(sleeps: list[float]) -> None:
    route = respx.get(SEARCH).mock(return_value=httpx.Response(503, json=error_body("BACKEND_UNAVAILABLE")))
    with pytest.raises(TinyFishUnavailable):
        make().search("q")
    assert route.call_count == 3 and len(sleeps) == 2

    route.mock(side_effect=[httpx.Response(502), httpx.Response(200, json={"results": [{"url": "https://a.example/"}]})])
    assert len(make().search("q")) == 1


@mocked
def test_500_is_unavailable_without_retry(sleeps: list[float]) -> None:
    route = respx.post(FETCH).mock(return_value=httpx.Response(500, json=error_body("INTERNAL_ERROR")))
    with pytest.raises(TinyFishUnavailable):
        make().fetch(["https://a.example/"])
    assert route.call_count == 1 and not sleeps


@mocked
def test_network_errors_retry_then_unavailable(sleeps: list[float]) -> None:
    route = respx.get(SEARCH).mock(side_effect=httpx.ConnectTimeout("slow"))
    client = make()
    with pytest.raises(TinyFishUnavailable, match="ConnectTimeout"):
        client.search("q")
    assert route.call_count == 3 and len(sleeps) == 2 and client.usage.as_dict()["errors"] == 1


@mocked
def test_paused_platform_blocks_every_call() -> None:
    rate_limiter.pause_platform("tinyfish", 600)
    for call in (
        lambda c: c.search("q"),
        lambda c: c.fetch(["https://a.example/"]),
        lambda c: c.run_agent("https://a.example/", "goal"),
        lambda c: c.get_run("r"),
    ):
        with pytest.raises(TinyFishRateLimited, match="paused"):
            call(make())
    assert not respx.calls


@mocked
def test_hourly_budget_blocks_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import rate_limiter as rl

    monkeypatch.setitem(rl.PLATFORM_LIMITS, "tinyfish", rl.PlatformLimit(2, 0, (0, 0)))
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"results": []}))
    client = make()
    client.search("a")
    client.search("b")
    with pytest.raises(TinyFishRateLimited, match="budget"):
        client.search("c")


# ------------------------------------------------------------------------------------------------ throttles
def test_token_bucket_with_fake_clock() -> None:
    now = [100.0]
    waited: list[float] = []
    bucket = tf.TokenBucket(30, 60, clock=lambda: now[0], sleep=waited.append)
    for _ in range(30):
        bucket.acquire(1, max_wait=20)
    assert waited == []
    bucket.acquire(1, max_wait=20)  # the 31st within the minute waits for a token (2 s at 30/min)
    assert waited == [pytest.approx(2.0)]
    now[0] += 60  # a minute later the bucket is full again
    waited.clear()
    for _ in range(29):
        bucket.acquire(1, max_wait=20)
    assert waited == []  # 29 tokens came back (the 31st request had reserved one ahead)
    with pytest.raises(TinyFishRateLimited):
        bucket.acquire(100, max_wait=20)


@mocked
def test_search_throttle_blocks_past_30_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    waited: list[float] = []
    monkeypatch.setattr(tf, "_search_bucket", tf.TokenBucket(30, 60, clock=lambda: now[0], sleep=waited.append))
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"results": []}))
    client = make()
    for i in range(30):
        client.search(f"q{i}")
    assert waited == []
    client.search("q30")
    assert waited == [pytest.approx(2.0)]

    monkeypatch.setattr(tf, "_search_bucket", tf.TokenBucket(1, 60, clock=lambda: now[0], sleep=waited.append))
    client.search("one")
    with pytest.raises(TinyFishRateLimited, match="Search"):  # would wait 60 s: longer than the 20 s we allow
        client.search("two")


@mocked
def test_fetch_throttle_counts_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    waited: list[float] = []
    monkeypatch.setattr(tf, "_fetch_bucket", tf.TokenBucket(150, 60, clock=lambda: now[0], sleep=waited.append))
    respx.post(FETCH).mock(return_value=httpx.Response(200, json={"results": [], "errors": []}))
    client = make()
    client.fetch([f"https://s{i}.example/" for i in range(150)])
    assert waited == []
    client.fetch([f"https://t{i}.example/" for i in range(10)])  # 10 more URLs = 4 s at 2.5 URLs/s
    assert waited == [pytest.approx(4.0)]


# ------------------------------------------------------------------------------------------------ concurrency
@mocked
def test_agent_runs_are_capped_at_two_under_five_threads() -> None:
    lock = threading.Lock()
    state = {"now": 0, "peak": 0, "calls": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        with lock:
            state["now"] += 1
            state["calls"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.08)
        with lock:
            state["now"] -= 1
        return sse_response()

    respx.post(SSE).mock(side_effect=handler)
    results: list[AgentRunResult] = []

    def worker() -> None:
        results.append(make().run_agent("https://a.example/", "goal"))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert state["calls"] == 5 and len(results) == 5 and all(r.ok for r in results)
    assert state["peak"] == 2


def test_agent_slots_busy_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tf, "AGENT_SLOT_WAIT_SECONDS", 0.05)
    slots = settings.TINYFISH_AGENT_CONCURRENCY
    for _ in range(slots):
        assert tf._agent_slots.acquire(timeout=1)
    try:
        with respx.mock() as router, pytest.raises(TinyFishRateLimited, match="agent slots are busy"):
            make().run_agent("https://a.example/", "goal")
        assert not router.calls
    finally:
        for _ in range(slots):
            tf._agent_slots.release()


@mocked
def test_agent_slot_is_released_after_errors() -> None:
    respx.post(SSE).mock(return_value=httpx.Response(401, json=error_body("INVALID_API_KEY")))
    for _ in range(4):  # more failures than slots: a leaked slot would make a later call block and time out
        with pytest.raises(TinyFishAuthError):
            make().run_agent("https://a.example/", "goal")


# ------------------------------------------------------------------------------------------------ usage
@mocked
def test_usage_counters_and_usage_today() -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"results": []}))
    respx.post(FETCH).mock(return_value=httpx.Response(200, json={"results": [], "errors": []}))
    respx.post(SSE).mock(return_value=sse_response())
    usage = UsageCounters()
    client = make(usage=usage, user_id="u-1")
    client.search("a")
    client.search("b")
    client.fetch([f"https://s{i}.example/" for i in range(12)])
    client.run_agent("https://a.example/", "goal")
    assert usage is client.usage
    assert usage.as_dict() == {"search_calls": 2, "fetch_calls": 2, "fetch_urls": 12, "agent_runs": 1, "agent_steps": 6, "errors": 0}
    assert usage.endpoints_used() == ["search", "fetch", "agent"]
    assert usage_today("u-1") == {"search_calls": 2, "fetch_urls": 12, "agent_runs": 1}
    assert usage_today("someone-else") == {"search_calls": 0, "fetch_urls": 0, "agent_runs": 0}
    make().search("anonymous")  # no user_id: only the shared counters
    assert usage_today("u-1")["search_calls"] == 2


@mocked
def test_failed_calls_count_as_errors() -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(403, json=error_body("FORBIDDEN")))
    client = make()
    with pytest.raises(TinyFishError):
        client.search("q")
    assert client.usage.as_dict()["errors"] == 1 and client.usage.as_dict()["search_calls"] == 1


# ------------------------------------------------------------------------------------------------ ping
def test_ping_unconfigured_returns_hint() -> None:
    with respx.mock() as router:
        out = TinyFishClient("").ping()
        assert not router.calls
    assert out["ok"] is False and out["hint"] == NOT_CONFIGURED_HINT and out["error"] and out["results"] == 0


@mocked
def test_ping_ok_and_failures() -> None:
    route = respx.get(SEARCH).mock(return_value=httpx.Response(200, json=fxj("api_search.json")))
    out = make().ping()
    assert out["ok"] is True and out["results"] == 5 and out["error"] is None and out["hint"] is None and isinstance(out["latency_ms"], int)
    assert route.call_count == 1

    route.mock(return_value=httpx.Response(401, json=error_body("INVALID_API_KEY")))
    out = make().ping()
    assert out["ok"] is False and "TINYFISH_API_KEY" in out["error"] and out["hint"]
    route.mock(return_value=httpx.Response(402, json=error_body("INSUFFICIENT_CREDITS")))
    rate_limiter._mem.clear()
    assert make().ping()["ok"] is False


# ------------------------------------------------------------------------------------------------ secrets
@mocked
def test_key_never_appears_in_logs_or_errors(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    echoing = error_body("INVALID_INPUT", f"bad request for key {KEY}")
    respx.get(SEARCH).mock(side_effect=[httpx.Response(200, json={"results": []}), httpx.Response(400, json=echoing), httpx.Response(401, json=error_body("INVALID_API_KEY")),
                                       httpx.Response(402, json=error_body("INSUFFICIENT_CREDITS", KEY)), httpx.Response(503), httpx.Response(503), httpx.Response(503)])
    client = make()
    client.search("fine")
    messages = []
    for _ in range(3):
        rate_limiter._mem.clear()
        with pytest.raises(TinyFishError) as exc:
            client.search("q")
        messages.append(str(exc.value))
    rate_limiter._mem.clear()
    with pytest.raises(TinyFishUnavailable) as exc:
        client.search("q")
    messages.append(str(exc.value))
    assert all(KEY not in m for m in messages) and "***" in messages[0]

    respx.post(SSE).mock(return_value=sse_response())
    client.run_agent("https://a.example/", "goal")
    assert KEY not in caplog.text and KEY not in repr(client)


@mocked
def test_key_comes_from_settings_when_not_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", f"  {KEY}  ")
    route = respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"results": []}))
    TinyFishClient().search("q")
    assert route.calls.last.request.headers["X-API-Key"] == KEY  # trimmed


@mocked
def test_hosts_come_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TINYFISH_SEARCH_URL", "https://search.proxy.example/tf/")
    route = respx.get("https://search.proxy.example/tf/").mock(return_value=httpx.Response(200, json={"results": []}))
    make().search("q")
    assert route.called
