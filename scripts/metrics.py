#!/usr/bin/env python3
"""Measure HireFlow's numbers instead of guessing them (docs/HIREFLOW_PLAN.md §6).

    backend/.venv/bin/python scripts/metrics.py                  # demo benchmark + test count (about a minute)
    backend/.venv/bin/python scripts/metrics.py --coverage       # + the backend suite with coverage (a few minutes)
    backend/.venv/bin/python scripts/metrics.py --from-db "$DATABASE_URL"   # + numbers from your own usage
    backend/.venv/bin/python scripts/metrics.py --json metrics.json         # also write them as JSON

Demo benchmark, in a throwaway SQLite database in demo mode (no AI keys: built-in heuristics, so it's
reproducible): a visitor signs up, loads the sample profile and runs the first scan against the bundled demo
careers site; then every job in the deck is kept, tailored and its form filled in Chromium. Dry run: nothing is
submitted. The demo forms are served locally, so no network is needed.

--from-db reads a real HireFlow database (read-only): jobs found per scan, scan time, and how often the form was
filled per ATS (Greenhouse, Lever, Ashby, Workday, ...) across the applications the agent prepared.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


# --------------------------------------------------------------------------- the demo careers site, served locally
class _DemoSite(BaseHTTPRequestHandler):
    """The bundled demo careers site (app.services.demo_site) over plain HTTP, for the browser to fill."""

    base = ""

    def _send(self, status: int, body: str) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        from app.services import demo_site

        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "":
            return self._send(200, demo_site.careers_html(self.base))
        match = re.fullmatch(r"/jobs/([\w-]+)", path)
        post = demo_site.by_id(match.group(1)) if match else None
        return self._send(200, demo_site.form_html(post, self.base)) if post else self._send(404, "Not found")

    def do_POST(self) -> None:
        from app.services import demo_site

        match = re.fullmatch(r"/jobs/([\w-]+)/submit", self.path.split("?", 1)[0])
        post = demo_site.by_id(match.group(1)) if match else None
        if post is None:
            return self._send(404, "Not found")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        fields = {k: v[0] for k, v in parse_qs(body).items()} if "urlencoded" in self.headers.get("Content-Type", "") else {}
        return self._send(200, demo_site.confirmation_html(demo_site.record(post["id"], fields), post))

    def log_message(self, *_: Any) -> None:
        pass


def _serve_demo_site() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DemoSite)
    _DemoSite.base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return _DemoSite.base


# --------------------------------------------------------------------------- demo benchmark
def demo_benchmark(with_llm: bool) -> dict[str, Any]:
    tmp = tempfile.mkdtemp(prefix="hireflow-metrics-")
    env = {
        "ENVIRONMENT": "development", "DEMO_MODE": "true", "DATABASE_URL": f"sqlite:///{tmp}/metrics.db",
        "REDIS_URL": "", "LOCAL_STORAGE_PATH": f"{tmp}/storage", "SECRET_KEY": "metrics-secret-key-0123456789abcdef",
        "HUMAN_EMULATION": "false", "SUBMISSION_DRY_RUN": "true", "GOOGLE_CLIENT_ID": "", "SMTP_HOST": "",
    }
    if not with_llm:
        env |= {"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "OLLAMA_MODEL": "", "OLLAMA_API_KEY": "", "LLM_PROVIDER": "auto"}
    os.environ.update(env)
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    os.environ["DEMO_SITE_URL"] = _serve_demo_site()

    from fastapi.testclient import TestClient

    from app.core.database import SessionLocal
    from app.main import app
    from app.models.agent_run import AgentRun
    from app.models.application import Application
    from app.worker.dispatch import run_inline

    out: dict[str, Any] = {"mode": "AI model from .env" if with_llm else "built-in heuristics, no AI keys"}
    with TestClient(app) as client, run_inline():
        started = time.perf_counter()
        r = client.post("/api/v1/auth/register", json={"email": "metrics@example.com", "password": "metrics-pass-123",
                                                         "full_name": "Metrics Visitor"})
        r.raise_for_status()
        client.post("/api/v1/users/me/onboarding/sample").raise_for_status()
        done = client.post("/api/v1/users/me/onboarding/complete", json={"start_scan": True})
        done.raise_for_status()
        deck = client.get("/api/v1/review/queue", params={"limit": 100}).json()["items"]
        out["signup_to_first_deck_seconds"] = round(time.perf_counter() - started, 2)
        run_id = done.json()["run"]["id"]
        with SessionLocal() as db:
            run = db.get(AgentRun, uuid.UUID(run_id))
            out["scan"] = {"jobs_found": run.jobs_discovered, "jobs_in_deck": len(deck),
                           "seconds": round((run.completed_at - run.started_at).total_seconds(), 2)}

        # Keep every job: each is tailored and its form filled in Chromium (dry run, nothing is submitted)
        fills: list[dict[str, Any]] = []
        for card in deck:
            t0 = time.perf_counter()
            client.post(f"/api/v1/review/{card['application_id']}", json={"decision": "keep"}).raise_for_status()
            with SessionLocal() as db:
                application = db.get(Application, uuid.UUID(card["application_id"]))
                fields = application.form_fields or []
                fills.append({
                    "company": card["job"]["company_name"], "status": application.status.value,
                    "ats": application.ats_platform.value if application.ats_platform else "custom",
                    "filled": sum(f.get("status") == "filled" for f in fields),
                    "unmapped": sum(f.get("status") == "unmapped" for f in fields),
                    "required": sum(bool(f.get("required")) for f in fields),
                    "required_filled": sum(bool(f.get("required")) and f.get("status") == "filled" for f in fields),
                    "screenshot": bool(application.form_screenshot_url),
                    "seconds": round(time.perf_counter() - t0, 2),
                })
    staged = [f for f in fills if f["screenshot"]]
    filled = sum(f["filled"] for f in fills)
    fillable = filled + sum(f["unmapped"] for f in fills)
    out["forms"] = {
        "kept": len(fills),
        "filled_in_browser": len(staged),
        "fields_filled_pct": round(100 * filled / fillable, 1) if fillable else None,
        "required_fields_filled_pct": round(100 * sum(f["required_filled"] for f in fills)
                                            / max(1, sum(f["required"] for f in fills)), 1),
        "forms_with_every_required_field_pct": round(100 * sum(f["required"] == f["required_filled"] and f["screenshot"]
                                                               for f in fills) / max(1, len(fills)), 1),
        "median_seconds_per_job": round(statistics.median(f["seconds"] for f in fills), 2) if fills else None,
        "per_job": fills,
    }
    return out


# --------------------------------------------------------------------------- tests and coverage
def test_counts(coverage: bool) -> dict[str, Any]:
    python = sys.executable
    out: dict[str, Any] = {}
    collected = subprocess.run([python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"], cwd=BACKEND,
                               capture_output=True, text=True, check=False)
    match = re.search(r"(\d+) tests? collected", collected.stdout)
    out["backend_tests"] = int(match.group(1)) if match else None
    e2e = subprocess.run([python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", str(ROOT / "e2e"),
                          "--rootdir", str(ROOT / "e2e")], cwd=ROOT, capture_output=True, text=True, check=False)
    match = re.search(r"(\d+) tests? collected", e2e.stdout)
    out["e2e_tests"] = int(match.group(1)) if match else None
    if coverage:
        run = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--cov=app", "--cov-report=term",
                              "-rfE"], cwd=BACKEND, capture_output=True, text=True, check=False)
        total = re.search(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", run.stdout, re.M)
        passed = re.search(r"(\d+) passed", run.stdout)
        out["backend_coverage_pct"] = int(total.group(1)) if total else None
        out["backend_passed"] = int(passed.group(1)) if passed else None
        out["backend_failed"] = re.findall(r"^(?:FAILED|ERROR) (\S+)", run.stdout, re.M)  # a partial run isn't a result
    return out


# --------------------------------------------------------------------------- your own usage
def from_database(url: str) -> dict[str, Any]:
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    out: dict[str, Any] = {}
    with engine.connect() as conn:
        scans = conn.execute(text(
            "SELECT jobs_discovered, started_at, completed_at FROM agent_runs "
            "WHERE run_type = 'scan' AND status = 'completed' AND completed_at IS NOT NULL")).all()
        if scans:
            seconds = [(c - s).total_seconds() for _, s, c in scans if c and s]
            found = [n or 0 for n, _, _ in scans]
            out["scans"] = {"runs": len(scans), "median_jobs_found": statistics.median(found), "max_jobs_found": max(found),
                            "median_seconds": round(statistics.median(seconds), 1) if seconds else None}
        rows = conn.execute(text(
            "SELECT COALESCE(ats_platform, 'unknown') AS ats, "
            "SUM(CASE WHEN staged_at IS NOT NULL THEN 1 ELSE 0 END) AS filled, COUNT(*) AS tried "
            "FROM applications WHERE review_decision = 'keep' AND (staged_at IS NOT NULL OR status = 'failed') "
            "GROUP BY COALESCE(ats_platform, 'unknown')")).all()
        out["fill_success_by_ats"] = {str(ats): {"filled": int(filled), "tried": int(tried),
                                                 "pct": round(100 * int(filled) / int(tried), 1)}
                                      for ats, filled, tried in rows if tried}
    return out


# --------------------------------------------------------------------------- report
def report(m: dict[str, Any]) -> str:
    lines = ["HireFlow metrics", "================"]
    demo = m.get("demo")
    if demo:
        forms = demo["forms"]
        lines += [
            f"Demo benchmark ({demo['mode']}):",
            f"  Sign-up to first Swipe Review deck: {demo['signup_to_first_deck_seconds']} s",
            f"  First scan: {demo['scan']['jobs_found']} jobs found, {demo['scan']['jobs_in_deck']} in the deck, "
            f"{demo['scan']['seconds']} s",
            f"  Kept {forms['kept']}: {forms['filled_in_browser']} forms filled in Chromium, "
            f"{forms['fields_filled_pct']}% of fields, {forms['required_fields_filled_pct']}% of required fields; "
            f"{forms['forms_with_every_required_field_pct']}% of forms had every required field filled",
            f"  Median time per kept job (tailor + cover letter + fill): {forms['median_seconds_per_job']} s",
        ]
    tests = m.get("tests")
    if tests:
        line = f"Tests: {tests['backend_tests']} backend, {tests['e2e_tests']} end-to-end (Playwright)"
        if tests.get("backend_coverage_pct") is not None:
            line += f"; backend coverage {tests['backend_coverage_pct']}% ({tests['backend_passed']} passed)"
        lines.append(line)
        for name in tests.get("backend_failed") or []:
            lines.append(f"  FAILED in the coverage run: {name}")
    usage = m.get("usage")
    if usage:
        scans = usage.get("scans")
        lines.append("Your database:")
        if scans:
            lines.append(f"  Scans: {scans['runs']} runs, median {scans['median_jobs_found']} jobs found "
                         f"(max {scans['max_jobs_found']}), median {scans['median_seconds']} s")
        for ats, row in sorted(usage.get("fill_success_by_ats", {}).items()):
            lines.append(f"  {ats}: form filled {row['filled']}/{row['tried']} ({row['pct']}%)")
        if not scans and not usage.get("fill_success_by_ats"):
            lines.append("  no scans or prepared applications yet")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--coverage", action="store_true", help="also run the backend suite with coverage")
    parser.add_argument("--from-db", metavar="DATABASE_URL", help="also read numbers from a real HireFlow database")
    parser.add_argument("--no-demo", action="store_true", help="skip the demo benchmark")
    parser.add_argument("--with-llm", action="store_true", help="use the AI keys in your environment for the benchmark")
    parser.add_argument("--json", metavar="FILE", help="also write the numbers to FILE as JSON")
    args = parser.parse_args()

    metrics: dict[str, Any] = {}
    if args.from_db:  # before the benchmark, which points the app at a throwaway database
        metrics["usage"] = from_database(args.from_db)
    metrics["tests"] = test_counts(args.coverage)
    if not args.no_demo:
        metrics["demo"] = demo_benchmark(args.with_llm)
    print(report(metrics))
    if args.json:
        Path(args.json).write_text(json.dumps(metrics, indent=2, default=str))
    if metrics["tests"].get("backend_failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
