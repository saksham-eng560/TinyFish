"""TinyFish Finder API tests: every TinyFish call is mocked with respx, so nothing here can reach the network."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import limiter
from app.config import settings
from app.core.database import SessionLocal
from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.services import tinyfish_client as tf
from app.worker.dispatch import wait_for_local_tasks
from tests.test_tinyfish_scraper import KEY, mocked_pipeline, route_agent, route_fetch, route_search

API = "/api/v1"
BODY: dict[str, Any] = {"roles": ["Backend Engineer", "Software Engineer", "Platform Engineer"], "locations": [], "keywords": ["python"],
                        "seniority": [], "posted_within_days": 365, "limit": 50, "min_score": 0, "agent_runs": 2}


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", KEY)
    monkeypatch.setattr(tf, "_sleep", lambda seconds: None)


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        router.route(host="testserver").pass_through()
        yield router


def run_search(c: TestClient, body: dict[str, Any] | None = None) -> dict[str, Any]:
    r = c.post(f"{API}/tinyfish/search", json={**BODY, **(body or {})})
    assert r.status_code == 202, r.text
    wait_for_local_tasks()
    state = c.get(f"{API}/tinyfish/search/{r.json()['search_id']}")
    assert state.status_code == 200, state.text
    return state.json()


def test_status_without_and_with_a_key(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    s = auth_client.get(f"{API}/tinyfish/status").json()
    assert s["configured"] is False and s["enabled"] is False and s["endpoints"] == [] and "TINYFISH_API_KEY" in s["hint"]
    monkeypatch.setattr(settings, "TINYFISH_API_KEY", KEY)
    s = auth_client.get(f"{API}/tinyfish/status").json()
    assert s["configured"] and s["enabled"] and s["agent_enabled"] and s["endpoints"] == ["search", "fetch", "agent"] and s["hint"] is None
    assert s["agent_runs_max"] == settings.TINYFISH_AGENT_MAX_RUNS_PER_SCAN and KEY not in json.dumps(s)


def test_search_without_a_key_is_503_and_creates_no_run(auth_client: TestClient) -> None:
    r = auth_client.post(f"{API}/tinyfish/search", json=BODY)
    assert r.status_code == 503 and "TINYFISH_API_KEY" in r.json()["detail"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.run_type == "tinyfish_search")) == 0


@pytest.mark.parametrize("body", [
    {"roles": [], "keywords": [], "companies": [], "career_pages": []},
    {"seniority": ["wizard"]},
    {"career_pages": ["ftp://acme.com/jobs"]},
    {"career_pages": ["https://www.linkedin.com/company/acme/jobs"]},
    {"limit": 1},
    {"min_score": 101},
    {"roles": ["x"] * 11},
])
def test_validation(auth_client: TestClient, keyed: None, body: dict[str, Any]) -> None:
    assert auth_client.post(f"{API}/tinyfish/search", json={**BODY, **body}).status_code == 422


def test_full_search_ranks_dedupes_and_reports_usage(auth_client: TestClient, keyed: None, mock: respx.MockRouter) -> None:
    mocked_pipeline(mock)
    state = run_search(auth_client)
    assert state["status"] == "completed" and state["phase"] == "done" and state["percent"] == 100 and state["error"] is None
    results = state["results"]
    assert results and len({x["id"] for x in results}) == len(results) and len({x["apply_url"] for x in results}) == len(results)
    scores = [x["score"] for x in results]
    assert scores == sorted(scores, reverse=True)
    for x in results:
        assert x["apply_url"].startswith("http") and 0 <= x["score"] <= 100 and x["reasons"] and x["discovered_by"] and "job" not in x
        assert set(x["visa"]) == {"status", "evidence"} and x["source_host"] and x["saved"] is False
    assert state["usage"]["search_calls"] >= 1 and state["usage"]["fetch_urls"] == 5
    assert state["endpoints_used"] == ["search", "fetch"]
    assert [s["name"] for s in state["stages"]] == ["search", "fetch", "agent", "rank"]
    assert state["counts"]["returned"] == len(results) and state["request"]["agent_runs"] == 2
    assert KEY not in json.dumps(state)


def test_agent_is_used_and_demo_mode_forces_it_off(auth_client: TestClient, keyed: None, mock: respx.MockRouter,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    route_search(mock, {"results": [{"position": 1, "title": "Backend Engineer", "url": "https://shell.example/careers", "snippet": "", "site_name": ""}]})
    route_fetch(mock, failures={"https://shell.example/careers": "empty_content"})
    calls: list[dict[str, Any]] = []
    route_agent(mock, calls)
    state = run_search(auth_client)
    assert state["status"] == "completed" and len(calls) == 1
    assert state["endpoints_used"] == ["search", "fetch", "agent"] and state["usage"]["agent_runs"] == 1
    assert state["agent"]["runs"][0]["status"] == "completed" and state["agent"]["runs"][0]["streaming_url"].startswith("https://")
    assert state["results"]

    monkeypatch.setattr(settings, "DEMO_MODE", True)
    with SessionLocal() as db:
        for run in db.scalars(select(AgentRun)).all():
            run.status = "completed"
        db.commit()
    state = run_search(auth_client)
    assert len(calls) == 1 and state["request"]["agent_runs"] == 0 and any("DEMO_MODE" in n for n in state["notes"])


def test_a_second_search_while_one_runs_is_409(auth_client: TestClient, keyed: None, mock: respx.MockRouter) -> None:
    route_search(mock, {"results": []})
    route_fetch(mock)
    with SessionLocal() as db:
        from app.models.user import User

        uid = db.scalar(select(User.id))
        db.add(AgentRun(user_id=uid, run_type="tinyfish_search", status="running", progress={}))
        db.commit()
    assert auth_client.post(f"{API}/tinyfish/search", json=BODY).status_code == 409


def test_cancel_stops_a_running_search_and_keeps_partial_results(auth_client: TestClient, keyed: None, mock: respx.MockRouter) -> None:
    mocked_pipeline(mock)
    # the cancel arrives as soon as the first Search call is made
    from app.api import deps  # noqa: F401

    original = tf.TinyFishClient.search

    def cancelling(self: Any, *a: Any, **kw: Any) -> Any:
        hits = original(self, *a, **kw)
        with SessionLocal() as db:
            for run in db.scalars(select(AgentRun).where(AgentRun.run_type == "tinyfish_search")).all():
                run.status = "cancelled"
            db.commit()
        return hits

    mock_patch = pytest.MonkeyPatch()
    mock_patch.setattr(tf.TinyFishClient, "search", cancelling)
    try:
        r = auth_client.post(f"{API}/tinyfish/search", json=BODY)
        wait_for_local_tasks()
    finally:
        mock_patch.undo()
    sid = r.json()["search_id"]
    state = auth_client.get(f"{API}/tinyfish/search/{sid}").json()
    assert state["status"] == "cancelled" and isinstance(state["results"], list)
    assert auth_client.post(f"{API}/agent/runs/{sid}/cancel").status_code == 200


def test_save_without_a_resume_then_again_and_other_user(auth_client: TestClient, keyed: None, mock: respx.MockRouter) -> None:
    mocked_pipeline(mock)
    state = run_search(auth_client)
    sid, ids = state["search_id"], [x["id"] for x in state["results"][:2]]
    r = auth_client.post(f"{API}/tinyfish/save", json={"search_id": sid, "listing_ids": ids})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] == 2 and body["already_in_deck"] == 0 and body["review_url"] == "/dashboard/review"
    assert all(i["created"] for i in body["items"])
    with SessionLocal() as db:
        apps = db.scalars(select(Application)).all()
        assert len(apps) == 2 and all(a.status == ApplicationStatus.MATCHED and a.match_score is not None for a in apps)
        assert all(a.match_details["method"] == "tinyfish_preferences" for a in apps)
    queue = auth_client.get(f"{API}/review/queue")
    assert queue.status_code == 200 and len(queue.json()["items"]) == 2
    again = auth_client.post(f"{API}/tinyfish/save", json={"search_id": sid, "listing_ids": ids}).json()
    assert again["saved"] == 0 and again["already_in_deck"] == 2
    assert all(x["saved"] for x in auth_client.get(f"{API}/tinyfish/search/{sid}").json()["results"] if x["id"] in ids)
    assert auth_client.post(f"{API}/tinyfish/save", json={"search_id": sid, "listing_ids": ["nope"]}).status_code == 404
    assert auth_client.post(f"{API}/tinyfish/save", json={"search_id": sid, "listing_ids": ["a"] * 101}).status_code == 422

    other = TestClient(auth_client.app)
    assert other.post(f"{API}/auth/register", json={"email": "bob@example.com", "password": "supersecret1", "full_name": "Bob"}).status_code == 201
    assert other.get(f"{API}/tinyfish/search/{sid}").status_code == 404
    assert other.post(f"{API}/tinyfish/save", json={"search_id": sid, "listing_ids": ids}).status_code == 404


def test_runs_list_has_request_and_status_but_no_results(auth_client: TestClient, keyed: None, mock: respx.MockRouter) -> None:
    mocked_pipeline(mock)
    run_search(auth_client)
    items = auth_client.get(f"{API}/agent/runs?run_type=tinyfish_search&page_size=5").json()["items"]
    assert len(items) == 1 and items[0]["progress"]["status"] == "completed" and items[0]["progress"]["request"]["roles"]
    assert "results" not in items[0]["progress"]


def test_progress_is_pushed_over_the_websocket(auth_client: TestClient, keyed: None, mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import tinyfish_finder

    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(tinyfish_finder, "push_update", lambda uid, kind, data: events.append((kind, data)))
    mocked_pipeline(mock)
    state = run_search(auth_client)
    pushed = [d for kind, d in events if kind == "tinyfish_progress"]
    assert pushed and pushed[-1]["search_id"] == state["search_id"] and pushed[-1]["state"]["status"] == "completed"
    assert all("results" not in d["state"] for d in pushed)


def test_integrations_section_and_test_endpoint(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch, mock: respx.MockRouter) -> None:
    section = auth_client.get(f"{API}/users/me/integrations").json()["tinyfish"]
    assert section["configured"] is False and section["agent_runs_per_scan"] == 2 and section["agent_runs_max"] == 5
    off = auth_client.post(f"{API}/users/me/integrations/tinyfish/test").json()
    assert off["ok"] is False and off["error"] == "not configured" and "TINYFISH_API_KEY" in off["hint"]

    monkeypatch.setattr(settings, "TINYFISH_API_KEY", KEY)
    route_search(mock, {"results": [{"position": 1, "title": "t", "url": "https://acme.example/jobs", "snippet": "", "site_name": ""}]})
    resp = auth_client.get(f"{API}/users/me/integrations")
    assert resp.json()["tinyfish"]["configured"] is True and set(resp.json()["tinyfish"]) == {
        "configured", "enabled", "agent_available", "agent_runs_per_scan", "agent_runs_max", "usage_today"}
    assert KEY not in resp.text
    ok = auth_client.post(f"{API}/users/me/integrations/tinyfish/test").json()
    assert ok["ok"] is True and ok["results"] == 1 and ok["error"] is None and KEY not in json.dumps(ok)


def test_preference_validation(auth_client: TestClient) -> None:
    put = lambda prefs: auth_client.put(f"{API}/users/me/preferences", json={"preferences": prefs})  # noqa: E731
    assert put({"tinyfish_agent_runs": 9}).status_code == 422
    assert put({"tinyfish_agent_runs": -1}).status_code == 422
    assert put({"tinyfish_enabled": "yes"}).status_code == 422
    assert put({"tinyfish_enabled": False, "tinyfish_agent_runs": 3}).status_code == 200


def test_search_endpoint_is_rate_limited() -> None:
    assert "app.api.tinyfish.start_search" in limiter._route_limits
