""""Continue with Google" on the sign-in and sign-up pages, and no demo shortcut anywhere."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from conftest import BASE
from playwright.sync_api import Page, Route, expect

SETUP_NOTE = "Google sign-in isn't set up on this computer yet."


def _config(google_enabled: bool) -> Callable[[Route], None]:
    """The server's /auth/config as a normal (not demo) install would answer it."""
    body = {"google_enabled": google_enabled, "registration_enabled": True, "demo_mode": False}

    def answer(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    return answer


@pytest.mark.parametrize("path", ["/login", "/register"])
def test_google_button_explains_the_setup_until_google_is_configured(page: Page, path: str) -> None:
    page.route("**/api/v1/auth/config", _config(google_enabled=False))
    page.goto(f"{BASE}{path}")
    google = page.get_by_role("button", name="Continue with Google")
    expect(google).to_be_visible()
    expect(page.get_by_text(SETUP_NOTE)).to_have_count(0)
    google.click()
    expect(page.get_by_text(SETUP_NOTE)).to_be_visible()
    steps = page.get_by_role("link", name="See the steps")
    expect(steps).to_have_attribute("href", "https://github.com/saksham-eng560/HireFlow/blob/main/docs/USER_GUIDE.md#sign-in-with-google")
    # Email and password still work right below it
    expect(page.get_by_label("Email")).to_be_visible()
    expect(page.get_by_role("button", name="Try the demo", exact=False)).to_have_count(0)


def test_google_button_starts_google_sign_in_when_configured(page: Page) -> None:
    page.route("**/api/v1/auth/config", _config(google_enabled=True))
    page.goto(f"{BASE}/login?next=/dashboard/review")
    google = page.get_by_role("link", name="Continue with Google")
    expect(google).to_have_attribute("href", "/api/v1/auth/google/login?next=%2Fdashboard%2Freview")


def test_google_answers_come_back_as_plain_messages(page: Page) -> None:
    page.route("**/api/v1/auth/config", _config(google_enabled=False))
    page.goto(f"{BASE}/login?error=google_not_configured")
    expect(page.get_by_text(SETUP_NOTE)).to_be_visible()
    page.goto(f"{BASE}/login?error=access_denied")
    expect(page.get_by_role("alert").filter(has_text="Google sign-in")).to_have_text("Google sign-in was cancelled.")
    page.goto(f"{BASE}/login?error=<b>anything-else</b>")
    expect(page.get_by_role("alert").filter(has_text="Google sign-in")).to_have_text("Google sign-in failed. Please try again.")


def test_no_google_button_in_the_demo(page: Page) -> None:
    """The e2e server runs the demo, where real-world sign-ins are off."""
    page.goto(f"{BASE}/login")
    expect(page.get_by_label("Email")).to_be_visible()
    expect(page.get_by_role("link", name="Continue with Google")).to_have_count(0)
    expect(page.get_by_role("button", name="Continue with Google")).to_have_count(0)
