"""Demo mode (docs/HIREFLOW_PLAN.md §5.5): the bundled demo careers site, demo-only scans, nothing real sent or
connected, the demo sign-in (used by the e2e tests), and the nightly reset."""

from __future__ import annotations

import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.models.interview import Interview
from app.models.job import Job
from app.models.user import User
from app.services import agent_orchestrator as orch
from app.services import demo_site
from app.services.demo_seed import demo_account, reset_demo
from app.submitters.base import SubmissionResult
from app.worker.dispatch import run_inline


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    demo_site.SUBMISSIONS.clear()


def _register(client: TestClient, email: str = "visitor@example.com") -> None:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "visitor-pass-1", "full_name": "Visitor"})
    assert r.status_code == 201, r.text


# --------------------------------------------------------------------------- the demo careers site
def test_the_careers_site_exists_only_in_the_demo(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get("/api/v1/demo-careers").status_code == 404
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    page = client.get("/api/v1/demo-careers")
    assert page.status_code == 200 and "fictional" in page.text
    graph = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page.text, re.S).group(1).replace("<\\/", "</"))
    assert len(graph["@graph"]) == len(demo_site.POSTINGS) and graph["@graph"][0]["@type"] == "JobPosting"


def test_its_forms_record_what_was_sent(client: TestClient, demo: None) -> None:
    form = client.get("/api/v1/demo-careers/jobs/acme-backend")
    assert form.status_code == 200 and 'id="application-form"' in form.text and "Acme Robotics" in form.text
    assert client.get("/api/v1/demo-careers/jobs/nope").status_code == 404
    sent = client.post("/api/v1/demo-careers/jobs/acme-backend/submit",
                       data={"first_name": "Sam", "last_name": "Rivera", "email": "sam@example.com", "auth": "yes"},
                       files={"resume": ("cv.pdf", b"%PDF-1.4 demo", "application/pdf")})
    assert sent.status_code == 200 and "Confirmation number: DEMO-1001" in sent.text
    assert client.get("/api/v1/demo-careers/submissions").json() == {"count": 1}
    assert demo_site.SUBMISSIONS[0]["resume"].startswith("cv.pdf")


# --------------------------------------------------------------------------- a visitor's run through
def test_a_visitor_goes_from_sign_up_to_a_deck_from_the_demo_site(client: TestClient, demo: None) -> None:
    _register(client)
    me = client.get("/api/v1/auth/me").json()
    assert me["preferences"]["dry_run"] is True and me["onboarding_completed_at"] is None
    assert client.get("/api/v1/auth/config").json()["demo_mode"] is True
    assert client.post("/api/v1/users/me/onboarding/sample").status_code == 200
    with run_inline():
        done = client.post("/api/v1/users/me/onboarding/complete", json={"start_scan": True})
    assert done.status_code == 200, done.text
    run = client.get(f"/api/v1/agent/runs/{done.json()['run']['id']}").json()
    assert run["status"] == "completed", run["log"]
    deck = client.get("/api/v1/review/queue").json()["items"]
    assert len(deck) >= 8
    assert all(card["job"]["source_url"].startswith(settings.demo_site_url) for card in deck)
    assert {card["job"]["company"]["verdict"] for card in deck} == {"verified"}
    status = client.get("/api/v1/agent/status").json()
    assert status["dry_run"] is True


def test_nothing_outside_the_demo_site_is_opened_or_sent(demo: None, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _register(client)  # signed up in the demo: dry run is on
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(orch, "_run_submitter", lambda db, user, app, submit: calls.append((app.job.source_url, submit))
                        or SubmissionResult(True, "staged", form_screenshot=b"png", screenshot=b"png"))
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == "visitor@example.com").one()  # the demo account exists too
        ids = []
        for url in ("https://real-company.example/jobs/1", f"{settings.demo_site_url}/jobs/test-posting"):
            job = Job(company_name="X", role_title=f"Intern {len(ids)}", description="Python", source_url=url,
                      application_url=url, source_platform="custom", company_verdict="verified")
            db.add(job)
            db.flush()
            app = Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.APPROVED, review_decision="keep")
            db.add(app)
            db.flush()
            ids.append(str(app.id))
        db.commit()
    for app_id in ids:
        with SessionLocal() as db:
            orch.submit_application(db, app_id)
            db.commit()
    with SessionLocal() as db:
        real, demo_app = (db.get(Application, uuid.UUID(i)) for i in ids)
        assert real.status == ApplicationStatus.PENDING_APPROVAL and real.manual_review_reason.startswith("Demo:")
        assert demo_app.manual_review_reason.startswith("Dry run")
    assert calls == [(f"{settings.demo_site_url}/jobs/test-posting", False)]  # only the demo site, and only filled


# --------------------------------------------------------------------------- nothing real is connected
def test_real_world_actions_are_off(client: TestClient, demo: None) -> None:
    _register(client)
    blocked = [
        ("POST", "/api/v1/auth/password", {"current_password": "visitor-pass-1", "new_password": "another-pass-2"}),
        ("POST", "/api/v1/auth/extension-token", None),
        ("GET", "/api/v1/auth/google/login", None),
        ("GET", "/api/v1/auth/google/connect", None),
        ("PUT", "/api/v1/users/me/ats-credentials", {"credentials": {"workday_password": "x"}}),
        ("POST", "/api/v1/users/me/integrations/linkedin-cookie", {"li_at": "abc"}),
        ("POST", "/api/v1/users/me/integrations/internshala-session", {"cookies": []}),
        ("POST", "/api/v1/users/me/progress-report", None),
        ("POST", f"/api/v1/communications/{uuid.uuid4()}/send", {"text": "hi"}),
        ("PUT", "/api/v1/users/me/preferences", {"preferences": {"internshala_bot_enabled": True}}),
        ("PUT", "/api/v1/users/me/preferences", {"preferences": {"slack_webhook_url": "https://hooks.example/x"}}),
    ]
    for method, path, body in blocked:
        r = client.request(method, path, json=body, follow_redirects=False)
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert "demo" in r.json()["detail"]


# --------------------------------------------------------------------------- the demo sign-in
def test_try_the_demo_signs_you_in_to_the_shared_account(client: TestClient, demo: None) -> None:
    r = client.post("/api/v1/auth/demo")
    assert r.status_code == 200 and r.json()["user"]["email"] == settings.DEMO_ACCOUNT_EMAIL
    me = client.get("/api/v1/auth/me").json()
    assert me["onboarding_completed_at"] and me["preferences"]["dry_run"] is True
    apps = client.get("/api/v1/applications", params={"page_size": 100}).json()
    statuses = {a["status"] for a in apps["items"]}
    assert {"offer", "interview", "screening", "applied", "rejected"} <= statuses
    assert len(client.get("/api/v1/review/queue").json()["items"]) >= 8
    with SessionLocal() as db:
        assert db.query(Interview).count() == 2
        first_id = demo_account(db).id
    client.cookies.clear()
    assert client.post("/api/v1/auth/demo").json()["user"]["id"] == str(first_id)  # the same account, not a new one
    r = client.request("DELETE", "/api/v1/users/me", json={"confirm": "DELETE"})
    assert r.status_code == 403
    assert client.post("/api/v1/auth/demo", headers={}).status_code == 200


def test_try_the_demo_is_off_outside_the_demo(client: TestClient) -> None:
    assert client.post("/api/v1/auth/demo").status_code == 404


def test_the_nightly_reset_wipes_everything_and_recreates_the_demo(client: TestClient, demo: None) -> None:
    _register(client, "visitor-1@example.com")
    client.post("/api/v1/users/me/onboarding/sample")
    client.post("/api/v1/auth/demo")
    with SessionLocal() as db:
        before = demo_account(db).id
        assert db.query(User).count() == 2
    with SessionLocal() as db:
        result = reset_demo(db)
        db.commit()
    assert result == {"reset": True, "accounts_removed": 2}
    with SessionLocal() as db:
        users = db.query(User).all()
        assert [u.email for u in users] == [settings.DEMO_ACCOUNT_EMAIL] and users[0].id != before
        assert all(job.source_url.startswith(settings.demo_site_url) for job in db.query(Job).all())


def test_the_reset_does_nothing_outside_the_demo(auth_client: TestClient) -> None:
    from app.worker.tasks_demo import reset_demo_data

    assert reset_demo_data() == {"reset": False}
    with SessionLocal() as db:
        assert db.query(User).count() == 1


def test_the_demo_fills_its_own_site_without_human_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typing and mouse pacing protect real accounts on real sites; the demo's own careers site needs none, and a
    visitor shouldn't wait a minute for a form (README: "a few seconds later the kept job is in Ready to submit")."""
    from app.automation import human

    monkeypatch.setattr(settings, "HUMAN_EMULATION", True)
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    assert human._enabled()
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    assert not human._enabled()


def test_a_room_sharing_one_ip_can_all_try_the_demo(client: TestClient, demo: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """It signs in to the shared account and creates nothing, so it isn't limited like sign-up (5 a minute): a class
    or an office behind one address, or the e2e suite, gets in; a flood still doesn't."""
    from app.api.deps import limiter

    monkeypatch.setattr(limiter, "enabled", True)
    codes = []
    try:
        for _ in range(21):
            client.cookies.clear()  # a different visitor each time: a fresh browser, same address
            codes.append(client.post("/api/v1/auth/demo").status_code)
        assert codes == [200] * 20 + [429]
    finally:
        limiter.reset()
