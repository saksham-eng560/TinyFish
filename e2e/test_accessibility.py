"""Accessibility (docs/HIREFLOW_PLAN.md §6): axe-core finds no serious or critical problems on the public pages and
the dashboard, in the light and the dark theme. axe-core comes from the dashboard's dev dependencies."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import BASE, open_demo_account
from playwright.sync_api import Browser, Page

AXE = Path(__file__).resolve().parents[1] / "frontend" / "node_modules" / "axe-core" / "axe.min.js"
PUBLIC = ["/", "/login", "/register", "/privacy", "/terms", "/responsible-use"]
DASHBOARD = ["/dashboard", "/dashboard/review", "/dashboard/submit", "/dashboard/applications", "/dashboard/applied",
             "/dashboard/jobs", "/dashboard/top-companies", "/dashboard/emails", "/dashboard/interviews",
             "/dashboard/analytics", "/dashboard/logs", "/dashboard/resume", "/dashboard/settings"]
SCAN = """async () => (await axe.run(document, {resultTypes: ["violations"]})).violations
  .filter(v => v.impact === "serious" || v.impact === "critical")
  .map(v => `${v.id} (${v.impact}): ${v.help} -> ${v.nodes.slice(0, 3).map(n => n.target.join(" ")).join(", ")}`)"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_no_serious_accessibility_problems(browser: Browser, theme: str) -> None:
    if not AXE.exists():
        pytest.skip("axe-core isn't installed: run npm ci in frontend/")
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.goto(BASE)
    page.evaluate("theme => localStorage.setItem('theme', theme)", theme)
    found: dict[str, list[str]] = {}
    for path in PUBLIC:
        page.goto(f"{BASE}{path}")
        found[path] = _scan(page)
    open_demo_account(page)
    assert page.evaluate("document.documentElement.classList.contains('dark')") is (theme == "dark")
    for path in DASHBOARD:
        page.goto(f"{BASE}{path}")
        page.wait_for_load_state("networkidle")
        found[path] = _scan(page)
    context.close()
    problems = {path: issues for path, issues in found.items() if issues}
    assert problems == {}, problems


def _scan(page: Page) -> list[str]:
    page.wait_for_timeout(500)
    page.add_script_tag(path=str(AXE))
    return page.evaluate(SCAN)
