"""Applying safely (docs/HIREFLOW_PLAN.md §5.1): the server enforces these whatever the UI sends.

Daily cap and its ceiling, at most 3 applications per company a week, never the same role twice, "Pause
everything", dry run, a screenshot of the filled form on every application, and the 10-minute undo
window on automatic submits.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.config import settings
from app.core.database import SessionLocal
from app.models.agent_run import AgentRun
from app.models.application import Application, ApplicationStatusHistory
from app.models.enums import ApplicationStatus, ATSPlatform, JobType
from app.models.job import Job
from app.models.user import Notification, User
from app.services import agent_orchestrator as orch
from app.services import guardrails
from app.submitters.base import SubmissionResult
from app.worker.dispatch import run_inline

EMAIL = "jane@example.com"
FORM_PNG = b"\x89PNG form"
DONE_PNG = b"\x89PNG confirmation"


class Submitter:
    """Stands in for the browser: records each run (submit=True / False) and returns what a real one would."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, bool]] = []
        monkeypatch.setattr(orch, "_run_submitter", self)

    def __call__(self, db: Any, user: User, app: Application, submit: bool) -> SubmissionResult:
        self.calls.append((str(app.id), submit))
        if submit:
            return SubmissionResult(True, "submitted", screenshot=DONE_PNG, form_screenshot=FORM_PNG, confirmation_number="C-1")
        return SubmissionResult(True, "staged", screenshot=FORM_PNG, form_screenshot=FORM_PNG,
                                fields=[{"label": "Email", "status": "filled"}])


def _user(db: Any) -> User:
    return db.query(User).filter(User.email == EMAIL).one()


def _prefs(**prefs: Any) -> None:
    with SessionLocal() as db:
        user = _user(db)
        user.preferences = {**user.preferences, **prefs}
        db.commit()


def _app(company: str = "Acme", title: str = "Software Engineer Intern", *, url: str | None = None,
         status: ApplicationStatus = ApplicationStatus.APPROVED, submitted_days_ago: float | None = None,
         sent_by: str | None = None) -> str:
    url = url or f"https://boards.greenhouse.io/{company.lower()}/jobs/{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        user = _user(db)
        job = Job(company_name=company, role_title=title, description="Python internship. " * 20, source_url=url,
                  application_url=url, source_platform=ATSPlatform.GREENHOUSE, job_type=JobType.INTERNSHIP,
                  dedupe_key=uuid.uuid4().hex, company_verdict="verified")
        db.add(job)
        db.flush()
        app = Application(user_id=user.id, job_id=job.id, status=status, ats_platform=ATSPlatform.GREENHOUSE,
                          review_decision="keep", approved_at=datetime.now(UTC) - timedelta(minutes=5),
                          tailored_resume_pdf_url="resume.pdf")
        if submitted_days_ago is not None:
            app.submitted_at = datetime.now(UTC) - timedelta(days=submitted_days_ago)
        db.add(app)
        db.flush()
        if sent_by:  # how the daily count knows HireFlow sent it
            db.add(ApplicationStatusHistory(application_id=app.id, old_status=ApplicationStatus.APPROVED,
                                            new_status=ApplicationStatus.APPLIED, changed_by=sent_by,
                                            created_at=app.submitted_at or datetime.now(UTC)))
        db.commit()
        return str(app.id)


def _get(app_id: str) -> Application:
    with SessionLocal() as db:
        app = db.get(Application, uuid.UUID(app_id))
        _ = app.job, app.history
        db.expunge_all()
    return app


def _submit(app_id: str) -> Application:
    with run_inline(), SessionLocal() as db:
        orch.submit_application(db, app_id)
        db.commit()
    return _get(app_id)


# --------------------------------------------------------------------------- daily cap
def test_daily_cap_never_goes_above_the_ceiling(auth_client: TestClient) -> None:
    with SessionLocal() as db:
        user = _user(db)
        user.preferences = {**user.preferences, "max_applications_per_day": 500}  # e.g. saved before the ceiling
        assert guardrails.daily_cap(user) == settings.MAX_APPLICATIONS_PER_DAY_CEILING
        user.preferences = {**user.preferences, "max_applications_per_day": 3}
        assert guardrails.daily_cap(user) == 3
    r = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"max_applications_per_day": 26}})
    assert r.status_code == 422 and "1-25" in r.json()["detail"]
    status = auth_client.get("/api/v1/agent/status").json()
    assert status["daily_limit"] == 10 and status["applied_today"] == 0  # new users start at 10


def test_over_the_daily_cap_it_waits_for_tomorrow(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch,
                                                  send_held: Any) -> None:
    submitter = Submitter(monkeypatch)
    _prefs(max_applications_per_day=1)
    _app("Earlier Co", status=ApplicationStatus.APPLIED, submitted_days_ago=0, sent_by="agent")
    app_id = _app("Next Co")
    app = _submit(app_id)
    assert submitter.calls == [] and app.status == ApplicationStatus.APPROVED
    assert app.send_after.date() == (datetime.now(UTC) + timedelta(days=1)).date()
    assert app.notes.startswith("Daily limit of 1 reached")
    assert auth_client.get("/api/v1/agent/status").json()["applied_today"] == 1
    # The sweep picking it up early changes nothing: the limit is checked again when it's sent
    send_held()
    assert submitter.calls == [] and _get(app_id).send_after is not None


def test_applications_you_sent_yourself_dont_use_up_the_daily_cap(auth_client: TestClient,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = Submitter(monkeypatch)
    _prefs(max_applications_per_day=1)
    _app("Mine Co", status=ApplicationStatus.APPLIED, submitted_days_ago=0, sent_by="user")  # "I Applied"
    app_id = _app("Next Co")
    assert _submit(app_id).status == ApplicationStatus.APPLIED and len(submitter.calls) == 1


# --------------------------------------------------------------------------- per company
def test_at_most_three_applications_per_company_a_week(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = Submitter(monkeypatch)
    _prefs(max_applications_per_day=25)
    for days, title in ((6, "Backend Intern"), (3, "Data Intern"), (1, "ML Intern")):
        _app("Acme Inc.", title, status=ApplicationStatus.APPLIED, submitted_days_ago=days)
    _app("Acme", "Old Intern", status=ApplicationStatus.APPLIED, submitted_days_ago=9)  # outside the 7 days
    fourth = _submit(_app("ACME", "Frontend Intern"))
    assert submitter.calls == [] and fourth.status == ApplicationStatus.APPROVED
    assert fourth.notes.startswith("Already 3 applications to ACME this week")
    # It goes once the oldest of the three is more than 7 days old
    expected = datetime.now(UTC) - timedelta(days=6) + timedelta(days=7)
    assert abs((fourth.send_after.replace(tzinfo=UTC) - expected).total_seconds()) < 120
    # Another company isn't affected
    assert _submit(_app("Other Co", "Frontend Intern")).status == ApplicationStatus.APPLIED


# --------------------------------------------------------------------------- duplicates
@pytest.mark.parametrize("same", ["url", "company_and_title"])
def test_never_applies_twice_to_the_same_role(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch, same: str) -> None:
    submitter = Submitter(monkeypatch)
    first = _app("Stripe", "Software Engineer Intern", url="https://boards.greenhouse.io/stripe/jobs/123",
                 status=ApplicationStatus.APPLIED, submitted_days_ago=30)
    if same == "url":  # the same posting found again through another source (tracking parameters and all)
        second = _app("Stripe, Inc.", "SWE Intern (Summer)", url="https://BOARDS.greenhouse.io/stripe/jobs/123/?gh_src=li")
    else:  # the same role at a different URL
        second = _app("Stripe Inc", "Software Engineer Intern (2027)", url="https://stripe.com/jobs/listing/swe-intern/9")
    app = _submit(second)
    assert submitter.calls == [] and app.status == ApplicationStatus.SKIPPED
    assert "already applied to Software Engineer Intern at Stripe" in app.history[-1].notes
    with SessionLocal() as db:
        note = db.query(Notification).filter(Notification.event_type == "application_duplicate").one()
        assert note.link == f"/dashboard/applications/{first}"
    # Approving a copy by hand is refused too
    third = _app("Stripe", "Software Engineer Intern", status=ApplicationStatus.PENDING_APPROVAL)
    r = auth_client.post(f"/api/v1/applications/{third}/approve", json={})
    assert r.status_code == 409 and "already applied" in r.json()["detail"]


def test_a_different_role_at_the_same_company_is_fine(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    Submitter(monkeypatch)
    _app("Stripe", "Software Engineer Intern", status=ApplicationStatus.APPLIED, submitted_days_ago=30)
    assert _submit(_app("Stripe", "Data Science Intern")).status == ApplicationStatus.APPLIED


def test_two_copies_approved_together_send_only_the_first(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = Submitter(monkeypatch)
    first = _app("Notion", "Product Engineer Intern")
    second = _app("Notion", "Product Engineer Intern")
    with SessionLocal() as db:  # approved a minute apart
        db.get(Application, uuid.UUID(first)).approved_at = datetime.now(UTC) - timedelta(minutes=2)
        db.get(Application, uuid.UUID(second)).approved_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    assert _submit(second).status == ApplicationStatus.SKIPPED  # the first one is ahead of it
    assert _submit(first).status == ApplicationStatus.APPLIED
    assert submitter.calls == [(first, True)]


# --------------------------------------------------------------------------- pause everything
def test_pause_stops_scans_preparation_and_sending(auth_client: TestClient, master_resume: dict,
                                                   monkeypatch: pytest.MonkeyPatch, send_held: Any) -> None:
    submitter = Submitter(monkeypatch)
    queued: list[tuple] = []
    monkeypatch.setattr("app.worker.dispatch.enqueue", lambda *a, **k: queued.append(a))
    with SessionLocal() as db:  # a scan already running
        user = _user(db)
        db.add(AgentRun(user_id=user.id, run_type="scan", trigger="user", status="running"))
        db.commit()
    approved = _app("Linear", "Engineer Intern")
    preparing = _app("Vercel", "Engineer Intern", status=ApplicationStatus.PREPARING)

    status = auth_client.post("/api/v1/agent/pause").json()
    assert status["paused"] is True and status["paused_at"]
    with SessionLocal() as db:
        assert db.query(AgentRun).one().status == "cancelled"
    assert auth_client.post("/api/v1/agent/start-scan", json={}).status_code == 409
    from app.worker.tasks_scan import scan_due_users

    assert scan_due_users() == 0
    with SessionLocal() as db:
        orch.prepare_application(db, preparing)
        db.commit()
    assert _get(preparing).notes == orch.PAUSED_NOTE and _get(preparing).status == ApplicationStatus.PREPARING
    assert _submit(approved).status == ApplicationStatus.APPROVED and submitter.calls == []
    assert send_held() == 0  # nothing of yours is sent while paused
    pending = _app("Ramp", "Engineer Intern", status=ApplicationStatus.PENDING_APPROVAL)
    r = auth_client.post(f"/api/v1/applications/{pending}/approve", json={})
    assert r.status_code == 409 and "paused" in r.json()["detail"]

    status = auth_client.post("/api/v1/agent/resume").json()
    assert status["paused"] is False
    # What was due gets a fresh undo window; preparation picks up again
    held = _get(approved)
    assert held.send_after.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(minutes=9)
    assert ("prepare_application", preparing) in queued and _get(preparing).notes is None
    assert send_held() == 1 and submitter.calls == [(approved, True)]


# --------------------------------------------------------------------------- dry run & screenshots
def test_dry_run_fills_and_screenshots_but_never_clicks_submit(auth_client: TestClient,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = Submitter(monkeypatch)
    r = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"dry_run": True}})
    assert r.status_code == 200 and auth_client.get("/api/v1/agent/status").json()["dry_run"] is True
    app = _submit(_app("Figma", "Design Engineer Intern"))
    assert submitter.calls == [(str(app.id), False)]  # the fill-only path: no Submit click anywhere
    assert app.status == ApplicationStatus.PENDING_APPROVAL and app.manual_review_reason.startswith("Dry run")
    assert app.form_screenshot_url and app.confirmation_screenshot_url is None and app.submitted_at is None
    assert app.history[-1].notes == "Dry run completed: nothing was sent"
    assert auth_client.get("/api/v1/agent/status").json()["applied_today"] == 0
    assert auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"dry_run": "yes"}}).status_code == 422


def test_the_filled_form_is_kept_as_proof_of_what_was_sent(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.storage import get_storage

    Submitter(monkeypatch)
    app = _submit(_app("Supabase", "Backend Intern"))
    assert app.status == ApplicationStatus.APPLIED
    storage = get_storage()
    assert storage.read(app.form_screenshot_url) == FORM_PNG  # just before Submit was clicked
    assert storage.read(app.confirmation_screenshot_url) == DONE_PNG
    detail = auth_client.get(f"/api/v1/applications/{app.id}").json()
    assert detail["form_screenshot_url"] and detail["confirmation_screenshot_url"]


# --------------------------------------------------------------------------- undo window
def _sending_soon() -> str:
    app_id = _app("Ashby", "Platform Intern")
    with SessionLocal() as db:
        guardrails.hold(db.get(Application, uuid.UUID(app_id)), datetime.now(UTC) + guardrails.SEND_DELAY, "Sending soon")
        db.commit()
    return app_id


def test_stop_it_in_the_undo_window(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch, send_held: Any) -> None:
    submitter = Submitter(monkeypatch)
    app_id = _sending_soon()
    assert auth_client.get("/api/v1/agent/status").json()["sending_soon"] == 1
    listed = auth_client.get("/api/v1/applications", params={"status": "approved"}).json()["items"][0]
    assert listed["send_after"] and listed["hold_reason"] == "Sending soon"
    r = auth_client.post(f"/api/v1/applications/{app_id}/cancel-send")
    assert r.status_code == 200 and r.json()["status"] == "pending_approval" and r.json()["send_after"] is None
    assert send_held() == 0 and submitter.calls == []
    assert auth_client.post(f"/api/v1/applications/{app_id}/cancel-send").status_code == 409


def test_send_now_skips_the_rest_of_the_window(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = Submitter(monkeypatch)
    app_id = _sending_soon()
    with run_inline():
        r = auth_client.post(f"/api/v1/applications/{app_id}/send-now")
    assert r.status_code == 202, r.text
    assert submitter.calls == [(app_id, True)] and _get(app_id).status == ApplicationStatus.APPLIED


def test_a_due_application_is_claimed_once(auth_client: TestClient) -> None:
    app_id = _sending_soon()
    later = datetime.now(UTC) + timedelta(minutes=11)
    with SessionLocal() as db:
        assert guardrails.claim_due(db, now=datetime.now(UTC)) == []  # not due yet
        assert guardrails.claim_due(db, now=later) == [app_id]
        assert guardrails.claim_due(db, now=later) == []  # a second sweep finds nothing
        db.commit()
    # Too late to stop it once it's claimed
    assert auth_client.post(f"/api/v1/applications/{app_id}/cancel-send").status_code == 409


def test_a_task_that_runs_early_does_not_send(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    submitter = Submitter(monkeypatch)
    app_id = _sending_soon()
    assert _submit(app_id).status == ApplicationStatus.APPROVED and submitter.calls == []


def test_normalize_url() -> None:
    assert guardrails.normalize_url("https://Jobs.Lever.co/acme/123/?utm_source=x#apply") == "jobs.lever.co/acme/123"
    assert guardrails.normalize_url("http://www.acme.com/careers/9") == "acme.com/careers/9"
    assert guardrails.normalize_url(None) == "" and guardrails.normalize_url("not a url") == ""


# --------------------------------------------------------------------------- interrupted preparations
def _stall(app_id: str, minutes: int = 50) -> None:
    """As if the task preparing it died with the server `minutes` ago (nothing has touched it since)."""
    with SessionLocal() as db:
        db.execute(update(Application).where(Application.id == uuid.UUID(app_id))
                   .values(updated_at=datetime.now(UTC) - timedelta(minutes=minutes)))
        db.commit()


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    tasks: list[tuple[str, ...]] = []
    monkeypatch.setattr("app.worker.tasks_apply.enqueue", lambda name, *args, **_: tasks.append((name, *args)))
    return tasks


def test_a_preparation_cut_off_by_a_restart_starts_again(auth_client: TestClient, queued: list) -> None:
    from app.worker.tasks_apply import send_due_applications

    stalled = _app("Stalled Co", status=ApplicationStatus.PREPARING)
    busy = _app("Busy Co", status=ApplicationStatus.PREPARING)  # still being prepared: recently touched
    _stall(stalled)
    _stall(busy, minutes=10)
    send_due_applications()
    assert queued == [("prepare_application", stalled)]
    send_due_applications()  # the next sweep, a minute later: already restarted, left alone
    assert queued == [("prepare_application", stalled)]
    assert [h.notes for h in _get(stalled).history].count(orch.RESTART_NOTE) == 1
    assert _get(stalled).status == ApplicationStatus.PREPARING


def test_paused_preparations_wait_for_resume_instead(auth_client: TestClient, queued: list) -> None:
    from app.worker.tasks_apply import send_due_applications

    app_id = _app("Paused Co", status=ApplicationStatus.PREPARING)
    with SessionLocal() as db:
        db.get(Application, uuid.UUID(app_id)).notes = orch.PAUSED_NOTE
        db.commit()
    _stall(app_id)
    send_due_applications()
    assert queued == []
    other = _app("Other Co", status=ApplicationStatus.PREPARING)
    _stall(other)
    assert auth_client.post("/api/v1/agent/pause").status_code == 200
    send_due_applications()
    assert queued == []  # nothing restarts while you've paused everything


def test_after_two_restarts_it_stops_and_tells_you(auth_client: TestClient, queued: list,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker.tasks_apply import send_due_applications

    app_id = _app("Flaky Co", status=ApplicationStatus.PREPARING)
    for _ in range(orch.MAX_PREPARE_RESTARTS + 1):
        _stall(app_id)
        send_due_applications()
    assert queued == [("prepare_application", app_id)] * orch.MAX_PREPARE_RESTARTS
    app = _get(app_id)
    assert app.status == ApplicationStatus.FAILED and app.needs_manual_review
    assert "Re-fill form" in app.manual_review_reason
    with SessionLocal() as db:
        assert db.query(Notification).filter(Notification.title.contains("Flaky Co")).count() == 1
    # "Re-fill form" on a job that was never prepared runs the whole preparation, not just the form
    api_tasks: list[tuple[str, ...]] = []
    monkeypatch.setattr("app.api.applications.enqueue", lambda name, *args, **_: api_tasks.append((name, *args)))
    assert auth_client.post(f"/api/v1/applications/{app_id}/restage").status_code == 202
    assert api_tasks == [("prepare_application", app_id)]
