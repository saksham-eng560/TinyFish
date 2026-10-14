"""The public landing page and its SEO files (docs/HIREFLOW_PLAN.md §6)."""

from __future__ import annotations

import struct

from conftest import BASE
from playwright.sync_api import Page, expect

SECTIONS = ["Find → Swipe → Apply", "HireFlow in 60 seconds", "Everything after “I want this one”",
            "An agent with brakes", "How it's built", "Questions"]


def _get(page: Page, path: str) -> tuple[int, str, bytes]:
    r = page.request.get(f"{BASE}{path}")
    return r.status, r.headers.get("content-type", ""), r.body()


def test_every_section_is_there_and_the_buttons_work(page: Page) -> None:
    page.goto(BASE)
    for title in SECTIONS:
        expect(page.get_by_role("heading", name=title)).to_be_visible()
    hero = page.get_by_role("link", name="View on GitHub")
    expect(hero).to_have_attribute("href", "https://github.com/saksham-eng560/HireFlow")
    expect(page.get_by_role("img", name="Swipe Review: a job card", exact=False)).to_be_visible()
    # Every in-page link lands on a section that exists
    for anchor in page.locator('nav[aria-label="Main"] a[href^="#"]').all():
        target = anchor.get_attribute("href")
        assert page.locator(target).count() == 1, target
    # The FAQ opens with the keyboard
    question = page.get_by_text("Will it spam employers in my name?")
    question.focus()
    page.keyboard.press("Enter")
    expect(page.get_by_text("waits 10 minutes in", exact=False)).to_be_visible()
    # People sign up and use it for real: no demo shortcut, even when the server runs the demo
    expect(page.get_by_role("button", name="Try the demo")).to_have_count(0)
    page.get_by_role("link", name="Get started").first.click()
    page.wait_for_url("**/register")


def test_it_fits_a_phone(phone: Page) -> None:
    phone.goto(BASE)
    expect(phone.get_by_role("heading", name="Find → Swipe → Apply")).to_be_visible()
    assert phone.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0


def test_search_engines_and_link_previews(page: Page) -> None:
    page.goto(BASE)
    assert page.title().startswith("HireFlow")
    og = page.locator('meta[property="og:image"]').get_attribute("content")
    assert page.locator('meta[name="description"]').get_attribute("content")
    assert page.locator('meta[name="twitter:card"]').get_attribute("content") == "summary_large_image"
    status, kind, png = _get(page, "/" + og.split("/", 3)[3])
    assert status == 200 and kind == "image/png"
    assert struct.unpack(">II", png[16:24]) == (1200, 630)

    status, _, robots = _get(page, "/robots.txt")
    assert status == 200 and b"Disallow: /dashboard" in robots and b"Sitemap:" in robots
    status, _, sitemap = _get(page, "/sitemap.xml")
    assert status == 200 and b"/responsible-use</loc>" in sitemap and b"/dashboard" not in sitemap
