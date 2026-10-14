"""Swipe Review on a phone: a real touch swipe and the arrow keys decide, and the page fits the screen
(docs/HIREFLOW_PLAN.md §6, dashboard polish)."""

from __future__ import annotations

from conftest import BASE, open_demo_account
from playwright.sync_api import Page, Response, expect


def _decision(response: Response) -> str:
    return response.request.post_data_json["decision"]


def _is_decision(response: Response) -> bool:
    path = response.url.split("/api/v1/review/", 1)[-1]
    return "/api/v1/review/" in response.url and response.request.method == "POST" and "/" not in path


def test_swipe_review_on_a_phone(phone: Page) -> None:
    open_demo_account(phone)
    phone.goto(f"{BASE}/dashboard/review")
    keep = phone.get_by_role("button", name="Keep (right arrow)")
    expect(keep).to_be_enabled(timeout=30_000)
    assert phone.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0

    # A finger swipe to the right keeps the top card (on the card's body, which scrolls on its own)
    card = phone.locator('section[aria-label="Job deck"] article').last
    card.scroll_into_view_if_needed()
    box = card.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    touch = phone.context.new_cdp_session(phone)
    with phone.expect_response(_is_decision, timeout=30_000) as kept:
        touch.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        for step in range(1, 15):
            touch.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + step * 18, "y": y}]})
        touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    assert kept.value.ok and _decision(kept.value) == "keep"

    # The left arrow skips the next one
    expect(keep).to_be_enabled(timeout=30_000)
    with phone.expect_response(_is_decision, timeout=30_000) as skipped:
        phone.keyboard.press("ArrowLeft")
    assert skipped.value.ok and _decision(skipped.value) == "skip"
