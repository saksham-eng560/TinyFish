"""When the API can't be reached (not started, still starting, stopped), people get a clear message, not a status code."""

from __future__ import annotations

import re

import pytest
from conftest import BASE
from playwright.sync_api import Page, Route, expect

MESSAGE = re.compile(r"Can't reach the HireFlow server right now")


def _proxy_404(route: Route) -> None:
    # What a proxy in front of the API answers when nothing runs behind it (plain text, not JSON)
    route.fulfill(status=404, content_type="text/plain; charset=utf-8", body="The page could not be found\n\nNOT_FOUND\n")


def _server_starting(route: Route) -> None:
    route.fulfill(status=503, content_type="text/html", body="<html><body>Service unavailable</body></html>")


@pytest.mark.parametrize("answer", [_proxy_404, _server_starting, lambda route: route.abort("connectionrefused")])
def test_signing_in_without_a_server_says_so(page: Page, answer: object) -> None:
    page.goto(f"{BASE}/login")
    page.route("**/api/v1/**", answer)
    page.get_by_label("Email").fill("someone@example.com")
    page.get_by_label("Password").fill("a-password-123")
    page.get_by_role("button", name="Sign in", exact=False).last.click()
    expect(page.get_by_text(MESSAGE)).to_be_visible()
    expect(page.get_by_text("Request failed")).to_have_count(0)


def test_creating_an_account_without_a_server_says_so(page: Page) -> None:
    page.goto(f"{BASE}/register")
    page.route("**/api/v1/auth/register", _proxy_404)
    page.get_by_label("Full name").fill("Someone")
    page.get_by_label("Email").fill("someone@example.com")
    page.get_by_label("Password").fill("a-password-123")
    page.get_by_role("button", name="Create account").last.click()
    expect(page.get_by_text(MESSAGE)).to_be_visible()


def test_real_api_errors_keep_their_own_message(page: Page) -> None:
    """A wrong password is the API's own JSON answer, so it's shown as is."""
    page.goto(f"{BASE}/login")
    page.get_by_label("Email").fill("nobody-here@example.com")
    page.get_by_label("Password").fill("wrong-password-123")
    page.get_by_role("button", name="Sign in", exact=False).last.click()
    expect(page.get_by_text("Invalid email or password")).to_be_visible()
    expect(page.get_by_text(MESSAGE)).to_have_count(0)

