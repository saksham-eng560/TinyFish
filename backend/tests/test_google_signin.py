"""Sign in with Google: the whole round trip, with Google itself faked."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.auth import OAUTH_COOKIE
from app.config import settings
from app.models.user import User
from app.services import google_oauth

FRONTEND = settings.FRONTEND_URL.rstrip("/")


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Google configured, with the token exchange and profile answered locally. Change ``profile`` per test."""
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-secret")
    fake: dict[str, Any] = {
        "profile": {"email": "Priya@Example.com", "email_verified": True, "name": "Priya Sharma"},
        "exchanged": [],
    }

    def exchange_code(code: str) -> dict[str, Any]:
        fake["exchanged"].append(code)
        return {"access_token": "google-access", "expires_in": 3600, "scope": "openid email profile"}

    monkeypatch.setattr(google_oauth, "exchange_code", exchange_code)
    monkeypatch.setattr(google_oauth, "fetch_userinfo", lambda _token: dict(fake["profile"]))
    return fake


def _start(client: TestClient, path: str = "/api/v1/auth/google/login?next=/dashboard") -> str:
    """Press "Continue with Google": returns the state Google would hand back."""
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 307, r.text
    url = urlparse(r.headers["location"])
    assert url.netloc == "accounts.google.com"
    query = parse_qs(url.query)
    assert query["scope"] == ["openid email profile"]
    assert query["redirect_uri"] == [settings.google_redirect_uri]
    assert client.cookies.get(OAUTH_COOKIE)
    return query["state"][0]


def _finish(client: TestClient, state: str, code: str = "code-from-google") -> Any:
    return client.get("/api/v1/auth/google/callback", params={"code": code, "state": state}, follow_redirects=False)


def test_new_google_account_is_created_and_signed_in(client: TestClient, google: dict[str, Any], db: Any) -> None:
    r = _finish(client, _start(client))
    assert r.status_code == 307
    assert r.headers["location"] == f"{FRONTEND}/dashboard"  # the dashboard sends first-timers to onboarding
    me = client.get("/api/v1/auth/me").json()
    assert me["email"] == "priya@example.com"
    assert me["full_name"] == "Priya Sharma"
    user = db.scalar(select(User).where(User.email == "priya@example.com"))
    assert user.hashed_password is None  # a Google-only account has no password to guess
    assert user.google_email == "priya@example.com"
    assert not client.cookies.get(OAUTH_COOKIE)  # the one-time nonce is cleared


def test_google_signs_in_to_an_existing_account_with_the_same_email(client: TestClient, google: dict[str, Any]) -> None:
    r = client.post("/api/v1/auth/register", json={"email": "priya@example.com", "password": "supersecret1", "full_name": "Priya"})
    user_id = r.json()["user"]["id"]
    client.post("/api/v1/auth/logout")
    client.cookies.clear()

    _finish(client, _start(client))
    assert client.get("/api/v1/auth/me").json()["id"] == user_id
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login", json={"email": "priya@example.com", "password": "supersecret1"}).status_code == 200


def test_unverified_google_email_is_refused(client: TestClient, google: dict[str, Any], db: Any) -> None:
    google["profile"] = {"email": "someone@example.com", "email_verified": False}
    r = _finish(client, _start(client))
    assert r.headers["location"] == f"{FRONTEND}/login?error=google_email_unverified"
    assert db.scalar(select(User)) is None
    assert client.get("/api/v1/auth/me").status_code == 401


def test_callback_from_a_browser_that_did_not_start_is_refused(client: TestClient, google: dict[str, Any]) -> None:
    """Someone else's state (or a link someone sends you) can't sign you in to their account."""
    state = _start(client)
    client.cookies.clear()
    r = _finish(client, state)
    assert r.headers["location"] == f"{FRONTEND}/login?error=google_oauth_failed"
    assert google["exchanged"] == []  # Google's code is never even redeemed

    first = _start(client)
    _start(client)  # a second sign-in replaces the nonce: the first state no longer matches
    assert _finish(client, first).headers["location"] == f"{FRONTEND}/login?error=google_oauth_failed"


def test_cancel_and_bad_answers_go_back_to_sign_in(client: TestClient, google: dict[str, Any]) -> None:
    r = client.get("/api/v1/auth/google/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r.headers["location"] == f"{FRONTEND}/login?error=access_denied"
    r = client.get("/api/v1/auth/google/callback", params={"error": "<script>"}, follow_redirects=False)
    assert r.headers["location"] == f"{FRONTEND}/login?error=google_oauth_failed"  # never echoes Google's text
    r = client.get("/api/v1/auth/google/callback", params={"code": "x", "state": "not-a-token"}, follow_redirects=False)
    assert r.headers["location"] == f"{FRONTEND}/login?error=google_oauth_failed"


def test_disabled_account_and_closed_registration(client: TestClient, google: dict[str, Any], db: Any,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ALLOW_REGISTRATION", False)
    r = _finish(client, _start(client))
    assert r.headers["location"] == f"{FRONTEND}/login?error=registration_disabled"

    db.add(User(email="priya@example.com", full_name="Priya", is_active=False))
    db.commit()
    r = _finish(client, _start(client))
    assert r.headers["location"] == f"{FRONTEND}/login?error=account_disabled"
    assert client.get("/api/v1/auth/me").status_code == 401


def test_without_google_set_up_the_button_explains_instead_of_erroring(client: TestClient) -> None:
    assert client.get("/api/v1/auth/config").json()["google_enabled"] is False
    r = client.get("/api/v1/auth/google/login", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == f"{FRONTEND}/login?error=google_not_configured"


def test_connecting_gmail_also_needs_the_same_browser(auth_client: TestClient, google: dict[str, Any]) -> None:
    url = auth_client.get("/api/v1/auth/google/connect").json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    nonce = auth_client.cookies.get(OAUTH_COOKIE)
    auth_client.cookies.delete(OAUTH_COOKIE, path="/api/v1/auth/google")
    assert _finish(auth_client, state).headers["location"] == f"{FRONTEND}/login?error=google_oauth_failed"

    auth_client.cookies.set(OAUTH_COOKIE, nonce, path="/api/v1/auth/google")
    r = _finish(auth_client, state)
    assert r.headers["location"] == f"{FRONTEND}/dashboard/settings?google=connected"
