"""Shared fixtures for the end-to-end tests (run them with scripts/e2e.sh)."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:3000").rstrip("/")


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


def _page(browser: Browser, viewport: dict[str, int], **options: object) -> Iterator[Page]:
    context = browser.new_context(viewport=viewport, reduced_motion="reduce", **options)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    yield page
    context.close()
    assert errors == [], f"errors in the page: {errors}"


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    yield from _page(browser, {"width": 1440, "height": 900})


@pytest.fixture
def phone(browser: Browser) -> Iterator[Page]:
    """A phone-sized touch screen."""
    yield from _page(browser, {"width": 390, "height": 844}, has_touch=True, is_mobile=True)


def open_demo_account(page: Page) -> None:
    """Sign in to the demo's shared, pre-filled account (demo mode only; the site has no button for it), then open
    the dashboard."""
    response = page.request.post(f"{BASE}/api/v1/auth/demo")
    assert response.ok, response.text()
    page.goto(f"{BASE}/dashboard")
    page.wait_for_url("**/dashboard", timeout=60_000)
