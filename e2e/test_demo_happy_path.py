"""End-to-end, in demo mode, through the real dashboard and API (docs/HIREFLOW_PLAN.md §4 and §5.5).

    scripts/e2e.sh          # starts a demo-mode API and dashboard, then runs these tests

A new visitor signs up, loads the sample profile in onboarding, runs the first scan, lands on a Swipe Review
deck from the bundled demo careers site, keeps a job, and finds its form filled (dry run) in Ready to submit.
The shared demo account opens with its deck ready.
"""

from __future__ import annotations

import time
import uuid

from conftest import BASE, open_demo_account
from playwright.sync_api import Page, expect


def test_sign_up_onboard_scan_swipe_and_fill(page: Page) -> None:
    page.goto(f"{BASE}/register")
    page.get_by_label("Full name").fill("Eve Tester")
    page.get_by_label("Email").fill(f"e2e-{uuid.uuid4().hex[:10]}@example.com")
    page.get_by_label("Password").fill("e2e-password-123")
    page.get_by_role("button", name="Create account").click()

    # Onboarding opens; the demo's sample profile fills every step
    page.wait_for_url("**/onboarding", timeout=20_000)
    expect(page.get_by_text("Step 1 of 8")).to_be_visible()
    page.get_by_role("button", name="Load sample profile").click()
    expect(page.get_by_text("Step 8 of 8")).to_be_visible(timeout=30_000)
    expect(page.get_by_role("heading", name="All set")).to_be_visible()

    # The first scan (the bundled demo careers site) fills the first Swipe Review deck
    started = time.monotonic()
    page.get_by_role("button", name="Run my first scan").click()
    page.wait_for_url("**/dashboard/review", timeout=120_000)
    print(f"sign-up to first deck: scan took {time.monotonic() - started:.1f}s")
    expect(page.get_by_text("Demo — nothing is really sent.")).to_be_visible()
    keep = page.get_by_role("button", name="Keep (right arrow)")
    expect(keep).to_be_enabled(timeout=30_000)

    # Keep one: it's tailored and its form filled on the demo site, then waits in Ready to submit (dry run)
    with page.expect_response(lambda r: "/api/v1/review/" in r.url and r.request.method == "POST", timeout=30_000) as kept:
        keep.click()  # the card flies off, then the decision is sent
    assert kept.value.ok, kept.value.text()
    deadline = time.monotonic() + 150
    while True:
        page.goto(f"{BASE}/dashboard/submit", wait_until="networkidle")
        if page.get_by_role("button", name="Submit application").count():
            break
        assert time.monotonic() < deadline, "the kept job never reached Ready to submit"
        page.wait_for_timeout(3000)
    waiting = page.request.get(f"{BASE}/api/v1/applications?status=pending_approval").json()["items"]
    assert len(waiting) == 1 and "/demo-careers/jobs/" in waiting[0]["job"]["source_url"]
    detail = page.request.get(f"{BASE}/api/v1/applications/{waiting[0]['id']}").json()
    assert detail["form_screenshot_url"] and detail["staged_at"]  # filled on the demo site, screenshot kept
    assert page.request.get(f"{BASE}/api/v1/agent/status").json()["dry_run"] is True  # and nothing will be sent


def test_the_demo_account_opens_with_its_deck(page: Page) -> None:
    open_demo_account(page)
    expect(page.get_by_text("Demo — nothing is really sent.")).to_be_visible()
    expect(page.get_by_role("heading", name="Overview")).to_be_visible()
    page.goto(f"{BASE}/dashboard/review")
    expect(page.get_by_role("button", name="Keep (right arrow)")).to_be_enabled(timeout=30_000)
