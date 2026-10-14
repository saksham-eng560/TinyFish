"""Platform rules and responsible use (docs/HIREFLOW_PLAN.md §5.4): sites that forbid automation are off by
default and used only after a recorded OK (never in the demo), and sites that say "slow down" are left alone."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.user import Notification, User
from app.scrapers import SCRAPERS, ScrapedJob, SearchQuery
from app.scrapers.base import BaseScraper, RateLimited, retry_after_seconds
from app.services import agent_orchestrator as orch
from app.services import sources
from app.services.rate_limiter import MAX_BACKOFF_SECONDS, rate_limiter

GATED = ["linkedin", "internshala", "indeed", "glassdoor"]


def _user(db: Any) -> User:
    return db.query(User).filter(User.email == "jane@example.com").one()


# --------------------------------------------------------------------------- off by default, on with consent
def test_new_users_start_without_sites_that_forbid_automation(auth_client: TestClient) -> None:
    platforms = auth_client.get("/api/v1/auth/me").json()["preferences"]["platforms"]
    assert not set(GATED) & set(platforms) and "greenhouse" in platforms
    listed = auth_client.get("/api/v1/users/me/sources").json()["sources"]
    assert [s["platform"] for s in listed] == GATED
    assert all(not s["enabled"] and not s["consented"] and "terms" in s["warning"] for s in listed)


def test_turning_one_on_needs_your_ok(auth_client: TestClient) -> None:
    r = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"platforms": ["greenhouse", "linkedin"]}})
    assert r.status_code == 422 and "LinkedIn needs your OK" in r.json()["detail"]
    r = auth_client.put("/api/v1/users/me/sources/linkedin", json={"enabled": True})
    assert r.status_code == 422 and "I understand the risk" in r.json()["detail"]
    r = auth_client.put("/api/v1/users/me/sources/linkedin", json={"enabled": True, "agree": True})
    assert r.status_code == 200 and "linkedin" in r.json()["platforms"]
    assert next(s for s in r.json()["sources"] if s["platform"] == "linkedin")["enabled"] is True
    with SessionLocal() as db:
        assert _user(db).consents["linkedin_automation"]  # the OK is recorded, with its time
    # Off again: the OK is withdrawn too
    r = auth_client.put("/api/v1/users/me/sources/linkedin", json={"enabled": False})
    assert "linkedin" not in r.json()["platforms"]
    with SessionLocal() as db:
        assert "linkedin_automation" not in (_user(db).consents or {})
    assert auth_client.put("/api/v1/users/me/sources/greenhouse", json={"enabled": True, "agree": True}).status_code == 404


def test_a_source_you_already_had_doesnt_block_other_settings(auth_client: TestClient) -> None:
    with SessionLocal() as db:  # e.g. an account from before these rules: LinkedIn on, no recorded OK
        user = _user(db)
        user.preferences = {**user.preferences, "platforms": ["greenhouse", "linkedin"]}
        db.commit()
    r = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"platforms": ["greenhouse", "linkedin", "lever"]}})
    assert r.status_code == 200  # nothing new and gated was turned on


def test_presets_never_turn_them_on(auth_client: TestClient) -> None:
    prefs = auth_client.post("/api/v1/users/me/preferences/preset/internships").json()
    assert not set(GATED) & set(prefs["platforms"]) and "internships" in prefs["platforms"]
    auth_client.put("/api/v1/users/me/sources/indeed", json={"enabled": True, "agree": True})
    auth_client.put("/api/v1/users/me/sources/indeed", json={"enabled": False})
    with SessionLocal() as db:  # agreed again, but turned off: a preset may add it back now
        sources.give(_user(db), "indeed")
        db.commit()
    assert "indeed" in auth_client.post("/api/v1/users/me/preferences/preset/internships").json()["platforms"]


def test_an_earlier_explicit_ok_still_counts(auth_client: TestClient) -> None:
    with SessionLocal() as db:
        user = _user(db)
        user.consents = {"linkedin": "2026-01-01T00:00:00+00:00", "internshala_bot": "2026-01-01T00:00:00+00:00"}
        assert sources.has_consent(user, "linkedin") and sources.has_consent(user, "internshala")
        assert not sources.has_consent(user, "indeed") and sources.has_consent(user, "greenhouse")


# --------------------------------------------------------------------------- scans
class _Board:
    name = "board"
    calls: list[str] = []

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        self.calls.append(self.name)
        return []


def test_a_scan_skips_them_without_your_ok(auth_client: TestClient, master_resume: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    for name in ("linkedin", "board_ok"):
        monkeypatch.setitem(SCRAPERS, name, type(name, (_Board,), {"name": name, "calls": calls}))
    with SessionLocal() as db:
        user = _user(db)
        for _ in range(2):
            run = orch.run_scan(db, user, platforms=["linkedin", "board_ok"])
            db.commit()
        assert calls == ["board_ok", "board_ok"]
        assert any("Skipped LinkedIn" in entry["message"] for entry in run.log)
        assert db.query(Notification).filter(Notification.event_type == "source_needs_consent").count() == 1  # told once
        sources.give(user, "linkedin")
        orch.run_scan(db, user, platforms=["linkedin", "board_ok"])
        db.commit()
    assert sorted(calls[2:]) == ["board_ok", "linkedin"]


def test_the_demo_never_uses_them(auth_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    with SessionLocal() as db:
        user = _user(db)
        sources.give(user, "linkedin")
        assert not sources.has_consent(user, "linkedin")
    r = auth_client.put("/api/v1/users/me/sources/linkedin", json={"enabled": True, "agree": True})
    assert r.status_code == 403
    assert all(not s["available"] for s in auth_client.get("/api/v1/users/me/sources").json()["sources"])


# --------------------------------------------------------------------------- backing off
def test_back_off_doubles_and_respects_retry_after() -> None:
    assert rate_limiter.back_off("site-a") == 15 * 60
    assert rate_limiter.back_off("site-a") == 30 * 60
    assert rate_limiter.back_off("site-a") == 60 * 60
    assert rate_limiter.is_paused("site-a")
    assert rate_limiter.back_off("site-b", retry_after=7200) == 7200  # longer than the 15 min it would wait
    assert rate_limiter.back_off("site-c", retry_after=10 ** 7) == MAX_BACKOFF_SECONDS  # capped
    for _ in range(10):
        last = rate_limiter.back_off("site-d")
    assert last == MAX_BACKOFF_SECONDS
    assert retry_after_seconds("120") == 120 and retry_after_seconds(None) is None and retry_after_seconds("soon") is None
    assert 0 < retry_after_seconds("Wed, 21 Oct 2099 07:28:00 GMT")


class _Site(BaseScraper):
    rate_key = "test-site"

    def search(self, query: SearchQuery) -> list[ScrapedJob]:
        return []


@respx.mock
def test_a_429_pauses_the_site() -> None:
    respx.get("https://site.example/jobs").mock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(RateLimited, match="paused for 60 min"):
        _Site().request("GET", "https://site.example/jobs")
    with pytest.raises(RateLimited, match="cooling down"):  # not even tried while paused
        _Site().request("GET", "https://site.example/jobs")
    assert respx.calls.call_count == 1


@respx.mock
def test_a_captcha_pauses_the_site_but_a_browser_check_does_not() -> None:
    respx.get("https://site.example/captcha").mock(return_value=httpx.Response(
        403, text="<html><div class='g-recaptcha'></div> Please verify you are human</html>"))
    with pytest.raises(RateLimited, match="captcha"):
        _Site().request("GET", "https://site.example/captcha")
    rate_limiter._mem.clear()
    # A JavaScript challenge (a real browser passes it): returned as is, so a scraper can fall back to a browser
    respx.get("https://site.example/challenge").mock(return_value=httpx.Response(503, text="<title>Just a moment...</title>"))
    assert _Site().request("GET", "https://site.example/challenge").status_code == 503
    assert not rate_limiter.is_paused("test-site")
