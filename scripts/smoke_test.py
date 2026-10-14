#!/usr/bin/env python3
"""Smoke-test a running HireFlow the way a visitor uses it. Python standard library only.

    python3 scripts/smoke_test.py --site http://localhost:3000 --api http://localhost:8000 --demo

--site is the dashboard; --api is the API's own URL, optional. --demo (with ./start.sh --demo) also checks the
demo: the demo sign-in works, the deck is full, dry run is on, and the demo careers site answers. Every check
prints a line; the exit code is 1 if any failed. --timeout is per request.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from typing import Any


class Client:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, method: str, url: str, body: Any = None, headers: dict[str, str] | None = None
                ) -> tuple[int, dict[str, str], bytes]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "User-Agent": "hireflow-smoke-test", **({"Content-Type": "application/json"} if data else {}), **(headers or {})})
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:  # unreachable: status 0, the check fails
            return 0, {}, str(getattr(exc, "reason", exc)).encode()


class Run:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        print(f"  {'✓' if ok else '✗'} {name}" + (f" ({detail})" if detail and not ok else ""))
        self.failed += not ok
        return ok


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", required=True, help="the dashboard's URL")
    parser.add_argument("--api", help="the API's own URL (optional)")
    parser.add_argument("--demo", action="store_true", help="also check the public demo")
    parser.add_argument("--timeout", type=float, default=60, help="seconds per request (default 60)")
    args = parser.parse_args()
    site, api = args.site.rstrip("/"), (args.api or "").rstrip("/")
    http = Client(args.timeout)
    run = Run()
    started = time.monotonic()

    print(f"Dashboard {site}")
    status, headers, body = http.request("GET", f"{site}/")
    run.check("home page", status == 200 and b"HireFlow" in body, f"HTTP {status}")
    run.check("security headers", headers.get("x-content-type-options") == "nosniff", "no X-Content-Type-Options")
    status, _, body = http.request("GET", f"{site}/api/health")
    run.check("dashboard health", status == 200, f"HTTP {status}")
    status, _, body = http.request("GET", f"{site}/robots.txt")
    run.check("robots.txt", status == 200 and b"Sitemap:" in body, f"HTTP {status}")
    status, _, body = http.request("GET", f"{site}/sitemap.xml")
    run.check("sitemap.xml", status == 200 and b"<urlset" in body, f"HTTP {status}")
    status, headers, body = http.request("GET", f"{site}/opengraph-image")
    size = struct.unpack(">II", body[16:24]) if status == 200 and body[:8] == b"\x89PNG\r\n\x1a\n" else None
    run.check("social preview image 1200×630", size == (1200, 630), f"HTTP {status}, size {size}")
    status, _, body = http.request("GET", f"{site}/api/v1/auth/config")
    config = _json(body) or {}
    run.check("API through the dashboard's /api proxy", status == 200 and "registration_enabled" in config, f"HTTP {status}")

    if api:
        print(f"API {api}")
        status, _, _ = http.request("GET", f"{api}/health")
        run.check("API health", status == 200, f"HTTP {status}")
        status, _, body = http.request("GET", f"{api}/health/ready")
        checks = (_json(body) or {}).get("checks", {})
        run.check("database reachable", status == 200 and checks.get("database") == "ok", json.dumps(checks))
        if api.startswith("https://"):
            # Redis is optional (./start.sh runs without it when it isn't installed)
            redis = str(checks.get("redis"))
            run.check("Redis connected (or not used)", redis == "ok" or redis.startswith("not used"), redis)
            status, headers, _ = http.request("GET", f"{api}/health")
            run.check("HSTS", "strict-transport-security" in headers, "no Strict-Transport-Security header")

    if args.demo:
        print("Demo")
        run.check("demo mode is on", config.get("demo_mode") is True, f"demo_mode={config.get('demo_mode')}")
        status, _, body = http.request("POST", f"{site}/api/v1/auth/demo", body={}, headers={"Origin": site})
        run.check("the demo sign-in works", status == 200, f"HTTP {status}: {body[:200]!r}")
        status, _, body = http.request("GET", f"{site}/api/v1/auth/me")
        me = _json(body) or {}
        run.check("signed in as the demo account", status == 200 and (me.get("preferences") or {}).get("dry_run") is True,
                  f"HTTP {status}")
        status, _, body = http.request("GET", f"{site}/api/v1/review/queue")
        cards = (_json(body) or {}).get("items") or []
        run.check("Swipe Review deck has jobs", status == 200 and len(cards) > 0, f"HTTP {status}, {len(cards)} cards")
        status, _, body = http.request("GET", f"{site}/api/v1/agent/status")
        run.check("dry run is on", status == 200 and (_json(body) or {}).get("dry_run") is True, f"HTTP {status}")
        careers = f"{api or site}/api/v1/demo-careers"
        status, _, body = http.request("GET", careers)
        run.check("demo careers site", status == 200 and b"fictional" in body, f"HTTP {status} at {careers}")
        status, _, body = http.request("POST", f"{site}/api/v1/auth/password", body={
            "current_password": "x", "new_password": "smoke-test-password"}, headers={"Origin": site})
        run.check("real-world actions are off (password change refused)", status == 403, f"HTTP {status}")

    verdict = "all checks passed" if not run.failed else f"{run.failed} check(s) failed"
    print(f"\n{verdict} in {time.monotonic() - started:.1f} s")
    return 1 if run.failed else 0


if __name__ == "__main__":
    sys.exit(main())
