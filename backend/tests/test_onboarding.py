"""First-run onboarding (docs/HIREFLOW_PLAN.md §4): per-step validation, skipping, completion, saved answers,
profile links in form filling, and existing users not being sent through it."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.schemas.resume_content import ResumeContent
from app.services.question_answerer import answer_questions

URL = "/api/v1/users/me/onboarding"

WELCOME = {"full_name": "Jane Doe", "phone": "+1 (415) 555-0100", "location": "San Francisco, CA", "timezone": "America/Los_Angeles"}
TARGETS = {"target_roles": ["Software Engineer Intern"], "target_locations": ["San Francisco, CA", "Remote"],
           "remote_preference": "any", "year_of_study": 2, "focus_skills": ["Python"], "expected_stipend": 6000}
ANSWERS = {"work_authorization": "Yes", "requires_sponsorship": "No", "earliest_start_date": "2027-05-15",
           "availability_months": 3, "willing_to_relocate": "Prefer not to say", "over_18": "Yes", "gender": "Prefer not to say"}


def patch(client: TestClient, step: int, data: dict[str, Any] | None = None, skip: bool = False):  # type: ignore[no-untyped-def]
    return client.patch(URL, json={"step": step, "data": data or {}, "skip": skip})


def _queued(monkeypatch) -> list[tuple]:  # type: ignore[no-untyped-def]
    queued: list[tuple] = []
    monkeypatch.setattr("app.api.agent.enqueue", lambda *a, **k: queued.append(a))
    return queued


def test_new_user_starts_onboarding_with_safe_defaults(auth_client: TestClient) -> None:
    state = auth_client.get(URL).json()
    assert state["step"] == 1 and not state["completed"] and state["completed_at"] is None
    assert state["missing"] == ["your resume", "target roles", "work authorization"]  # name came with sign-up
    assert [s["skippable"] for s in state["steps"]] == [False, False, True, False, False, False, True, False]
    me = auth_client.get("/api/v1/auth/me").json()
    assert me["onboarding_completed_at"] is None and me["onboarding_step"] == 1
    prefs = auth_client.get("/api/v1/users/me/preferences").json()
    assert prefs["auto_submit_kept"] is False and prefs["max_applications_per_day"] == 10


@pytest.mark.parametrize(("step", "data", "fragment"), [
    (1, {"full_name": ""}, "full_name"),
    (1, {"full_name": "Jane", "phone": "call me"}, "phone"),
    (1, {"full_name": "Jane", "timezone": "Mars/Olympus"}, "time zone"),
    (1, {"full_name": "Jane", "unexpected": 1}, "unexpected"),
    (3, {"github_url": "https://gitlab.com/jane"}, "github.com"),
    (3, {"linkedin_url": "not a link"}, "link"),
    (3, {"profile_links": [{"label": "LeetCode", "url": "ftp://leetcode.com/jane"}]}, "link"),
    (4, {**TARGETS, "target_roles": []}, "target_roles"),
    (4, {**TARGETS, "target_locations": ["  "]}, "target_locations"),
    (4, {**TARGETS, "year_of_study": 9}, "year_of_study"),
    (5, {"work_authorization": "Maybe"}, "work_authorization"),
    (5, {"requires_sponsorship": "No"}, "work_authorization"),  # required
    (5, {"work_authorization": "Yes", "earliest_start_date": "next spring"}, "earliest_start_date"),
    (6, {"max_applications_per_day": 26}, "at most 25"),
    (6, {"review_mode": "yolo"}, "review_mode"),
])
def test_each_step_is_validated(auth_client: TestClient, step: int, data: dict, fragment: str) -> None:
    r = patch(auth_client, step, data)
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"] if isinstance(r.json()["detail"], str) else fragment in r.text
    assert auth_client.get(URL).json()["step"] == 1  # a rejected step doesn't advance


def test_required_steps_cannot_be_skipped(auth_client: TestClient) -> None:
    for step in (1, 2, 4, 5, 6):
        r = patch(auth_client, step, skip=True)
        assert r.status_code == 400 and "can't be skipped" in r.json()["detail"]
    r = patch(auth_client, 3, skip=True)  # profiles & links are optional
    assert r.status_code == 200 and r.json()["step"] == 4
    assert patch(auth_client, 7, skip=True).json()["step"] == 8


def test_resume_step_needs_an_uploaded_resume(auth_client: TestClient) -> None:
    r = patch(auth_client, 2)
    assert r.status_code == 400 and r.json()["detail"] == "Upload your resume first"


def test_complete_requires_resume_and_answers(auth_client: TestClient, monkeypatch) -> None:
    _queued(monkeypatch)
    assert patch(auth_client, 1, WELCOME).status_code == 200
    assert patch(auth_client, 4, TARGETS).status_code == 200
    assert patch(auth_client, 5, ANSWERS).status_code == 200
    r = auth_client.post(f"{URL}/complete", json={"start_scan": False})
    assert r.status_code == 400 and r.json()["detail"] == "Finish these first: your resume"
    assert auth_client.get("/api/v1/auth/me").json()["onboarding_completed_at"] is None


def test_full_walkthrough_saves_everything_and_starts_the_first_scan(auth_client: TestClient, master_resume: dict,
                                                                     monkeypatch) -> None:
    queued = _queued(monkeypatch)
    assert patch(auth_client, 1, WELCOME).json()["step"] == 2
    assert patch(auth_client, 2).json()["step"] == 3
    profiles = {"linkedin_url": "linkedin.com/in/janedoe", "github_url": "https://github.com/janedoe",
                "portfolio_url": "janedoe.dev", "profile_links": [{"label": "LeetCode", "url": "leetcode.com/u/jane"}]}
    state = patch(auth_client, 3, profiles).json()
    assert state["data"]["profiles"]["linkedin_url"] == "https://linkedin.com/in/janedoe"  # https:// added
    assert state["data"]["profiles"]["profile_links"] == [{"label": "LeetCode", "url": "https://leetcode.com/u/jane"}]
    state = patch(auth_client, 4, {**TARGETS, "preset": "ai-engineer"}).json()
    assert state["data"]["targets"]["target_roles"] == ["Software Engineer Intern"]  # your roles beat the preset's
    assert state["data"]["targets"]["expected_stipend"] == 6000
    patch(auth_client, 5, ANSWERS)
    patch(auth_client, 6, {"review_mode": "swipe", "auto_submit_kept": False, "max_applications_per_day": 12,
                           "resume_strategy": "light", "cover_letter_enabled": False})
    state = patch(auth_client, 7, {"linkedin_consent": True, "internshala_consent": False}).json()
    assert state["step"] == 8 and state["missing"] == [] and all(s["done"] for s in state["steps"][:7])

    r = auth_client.post(f"{URL}/complete", json={"start_scan": True})
    assert r.status_code == 200 and r.json()["completed"] and r.json()["run"]["run_type"] == "scan"
    assert queued and queued[-1][0] == "scan_user"

    me = auth_client.get("/api/v1/auth/me").json()
    assert me["onboarding_completed_at"] and me["onboarding_step"] == 8
    assert me["full_name"] == "Jane Doe" and me["github_url"] == "https://github.com/janedoe"
    prefs = me["preferences"]
    assert prefs["timezone"] == "America/Los_Angeles" and prefs["max_applications_per_day"] == 12
    assert prefs["resume_strategy"] == "light" and prefs["cover_letter_enabled"] is False
    assert "linkedin" in prefs["platforms"] and "internshala" not in prefs["platforms"]
    # Finishing twice is fine and doesn't queue a second scan while one runs
    assert auth_client.post(f"{URL}/complete", json={"start_scan": True}).status_code == 200
    assert sum(1 for q in queued if q[0] == "scan_user") == 1


def test_saved_answers_become_field_mappings(auth_client: TestClient) -> None:
    patch(auth_client, 5, ANSWERS)
    mappings = {m["field_name"]: m["field_value"] for m in auth_client.get("/api/v1/users/me/field-mappings").json()["mappings"]}
    assert mappings["work_authorization"] == "Yes" and mappings["requires_sponsorship"] == "No"
    assert mappings["earliest_start_date"] == "2027-05-15" and mappings["availability_months"] == "3"
    assert mappings["willing_to_relocate"] == "Prefer not to say" and mappings["over_18"] == "Yes"
    # Sending an answer as null clears it; answers you didn't send stay
    patch(auth_client, 5, {"work_authorization": "Yes", "requires_sponsorship": None})
    mappings = {m["field_name"]: m["field_value"] for m in auth_client.get("/api/v1/users/me/field-mappings").json()["mappings"]}
    assert "requires_sponsorship" not in mappings and mappings["earliest_start_date"] == "2027-05-15"


def test_consent_is_recorded_and_withdrawn(auth_client: TestClient) -> None:
    patch(auth_client, 7, {"linkedin_consent": True, "internshala_consent": True})
    connect = auth_client.get(URL).json()["data"]["connect"]
    assert connect["linkedin_consent"] and connect["internshala_consent"]
    patch(auth_client, 7, {"linkedin_consent": False, "internshala_consent": True})
    state = auth_client.get(URL).json()
    assert not state["data"]["connect"]["linkedin_consent"] and state["data"]["connect"]["internshala_consent"]
    prefs = auth_client.get("/api/v1/users/me/preferences").json()
    assert "linkedin" not in prefs["platforms"] and "internshala" in prefs["platforms"]


def test_form_filling_uses_profile_links() -> None:
    resume = ResumeContent.model_validate({"personal_info": {"name": "Jane", "github": "https://github.com/old-handle"}}).to_dict()
    links = {"github": "https://github.com/janedoe", "linkedin": "https://linkedin.com/in/janedoe",
             "portfolio": "https://janedoe.dev", "leetcode": "https://leetcode.com/u/jane"}
    questions = [{"question": "GitHub profile", "field_type": "text"},
                 {"question": "Portfolio website", "field_type": "text"},
                 {"question": "LeetCode profile URL", "field_type": "text"},
                 {"question": "Do you have a LinkedIn profile?", "field_type": "radio", "options": ["Yes", "No"]},
                 {"question": "Earliest start date", "field_type": "text"},
                 {"question": "How many months are you available for this internship?", "field_type": "number"}]
    out = answer_questions(questions, resume, {}, {"earliest_start_date": "2027-05-15", "availability_months": "3"},
                           use_llm=False, links=links)
    by_q = {a["question"]: a["answer"] for a in out}
    assert by_q["GitHub profile"] == "https://github.com/janedoe"  # your profile link beats the resume's
    assert by_q["Portfolio website"] == "https://janedoe.dev"
    assert by_q["LeetCode profile URL"] == "https://leetcode.com/u/jane"
    assert by_q["Do you have a LinkedIn profile?"] in ("", "Yes", "No")  # never a URL in a Yes / No field
    assert by_q["Earliest start date"] == "2027-05-15"
    assert by_q["How many months are you available for this internship?"] == "3"
    # Without profile links the resume's link is still used
    out = answer_questions(questions[:1], resume, {}, {}, use_llm=False)
    assert out[0]["answer"] == "https://github.com/old-handle"


def test_profile_and_links_in_settings(auth_client: TestClient) -> None:
    r = auth_client.patch("/api/v1/users/me", json={"github_url": "github.com/janedoe",
                                                    "profile_links": [{"label": "Kaggle", "url": "https://kaggle.com/jane"}]})
    assert r.status_code == 200 and r.json()["github_url"] == "https://github.com/janedoe"
    assert r.json()["profile_links"] == [{"label": "Kaggle", "url": "https://kaggle.com/jane"}]
    assert auth_client.patch("/api/v1/users/me", json={"github_url": "https://bitbucket.org/jane"}).status_code == 422
    # A null name used to be a 500; now it's a clear 422
    assert auth_client.patch("/api/v1/users/me", json={"full_name": None}).status_code == 422


def test_daily_cap_has_a_server_ceiling(auth_client: TestClient, db) -> None:
    put = lambda prefs: auth_client.put("/api/v1/users/me/preferences", json={"preferences": prefs})  # noqa: E731
    assert put({"max_applications_per_day": settings.MAX_APPLICATIONS_PER_DAY_CEILING}).status_code == 200
    r = put({"max_applications_per_day": settings.MAX_APPLICATIONS_PER_DAY_CEILING + 1})
    assert r.status_code == 422 and str(settings.MAX_APPLICATIONS_PER_DAY_CEILING) in r.json()["detail"]
    assert auth_client.post("/api/v1/users/me/preferences/preset/internships").json()["max_applications_per_day"] == 25
    # A cap saved before the ceiling existed doesn't block other changes; it's brought down to the ceiling
    from app.models.user import User

    user = db.query(User).one()
    user.preferences = {**user.prefs, "max_applications_per_day": 100}
    db.commit()
    r = put({"posted_within_days": 7})
    assert r.status_code == 200 and r.json()["max_applications_per_day"] == 25


def test_sample_profile_only_in_demo_mode(auth_client: TestClient, monkeypatch) -> None:
    assert auth_client.post(f"{URL}/sample").status_code == 404
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    state = auth_client.post(f"{URL}/sample").json()
    assert state["demo_mode"] and state["missing"] == [] and state["step"] == 7
    assert state["data"]["resume"]["resume_id"] and state["data"]["answers"]["work_authorization"] == "Yes"
    assert auth_client.post(f"{URL}/complete", json={"start_scan": False}).status_code == 200


def test_existing_users_skip_onboarding_after_upgrade(db) -> None:
    """A local SQLite database from before onboarding: its users are marked done, so they aren't redirected."""
    from sqlalchemy import text

    from app.core.database import create_all, engine
    from app.models.user import User

    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite only (PostgreSQL gets the same backfill from migration 0002)")
    db.add(User(email="old@example.com", full_name="Old User", preferences={}))
    db.commit()
    with engine.begin() as conn:
        for column in ("onboarding_completed_at", "onboarding_step", "profile_links", "portfolio_url", "github_url"):
            conn.execute(text(f"ALTER TABLE users DROP COLUMN {column}"))
    create_all()
    db.expire_all()
    user = db.query(User).filter_by(email="old@example.com").one()
    assert user.onboarding_completed_at is not None and user.onboarding_step == 8


def test_an_index_the_old_rows_cannot_take_never_blocks_start_up(db, caplog) -> None:
    """Upgrading an old local database adds new indexes; one its rows can't satisfy is logged, not fatal."""
    from sqlalchemy import Index

    from app.core.database import create_all, engine
    from app.models.user import User

    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite only (PostgreSQL is upgraded by Alembic)")
    db.add_all([User(email="a@example.com", full_name="Same Name", preferences={}),
                User(email="b@example.com", full_name="Same Name", preferences={})])
    db.commit()
    index = Index("ux_test_unique_names", User.__table__.c.full_name, unique=True)
    try:
        with caplog.at_level("WARNING", logger="app.core.database"):
            create_all()
        assert "ux_test_unique_names not added" in caplog.text
    finally:
        User.__table__.indexes.discard(index)
    assert db.query(User).count() == 2
