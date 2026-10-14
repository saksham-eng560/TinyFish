"""Runtime settings: database and Redis URLs from hosted providers, error reporting, Redis being optional,
and the scheduler that stands in for Celery beat when there's no Redis (./start.sh without Redis)."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from celery import Celery

from app.config import Settings


@pytest.mark.parametrize("url", ["postgres://u:p@ep-x.aws.neon.tech/hireflow?sslmode=require",
                                 "postgresql://u:p@ep-x.aws.neon.tech/hireflow?sslmode=require"])
def test_a_plain_postgres_url_opens_with_psycopg_3(url: str) -> None:
    assert Settings(DATABASE_URL=url).DATABASE_URL == "postgresql+psycopg://u:p@ep-x.aws.neon.tech/hireflow?sslmode=require"


def test_other_database_urls_are_left_alone() -> None:
    for url in ("postgresql+psycopg://u:p@db/hireflow", "sqlite:///data/hireflow.db"):
        assert Settings(DATABASE_URL=url).DATABASE_URL == url


def test_a_tls_redis_url_works_for_celery() -> None:
    url = "rediss://default:secret@eu1-x.upstash.io:6379"
    settings = Settings(REDIS_URL=url)
    assert settings.celery_broker == settings.celery_backend == f"{url}?ssl_cert_reqs=required"
    with pytest.raises(ValueError, match="ssl_cert_reqs"):  # what Celery does with the URL as copied
        _ = Celery("raw", broker=url, backend=url).backend
    backend = Celery("fixed", broker=settings.celery_broker, backend=settings.celery_backend).backend
    assert backend.connparams["ssl_cert_reqs"] == 2  # ssl.CERT_REQUIRED: the certificate is verified


def test_a_tls_url_that_already_says_how_to_verify_is_kept() -> None:
    url = "rediss://x:6379/0?ssl_cert_reqs=none"
    assert Settings(REDIS_URL=url).celery_broker == url
    assert Settings(REDIS_URL="redis://localhost:6379/0").celery_broker == "redis://localhost:6379/0"


def test_sentry_is_private_and_names_the_release(monkeypatch: pytest.MonkeyPatch) -> None:
    import sentry_sdk

    from app.config import settings
    from app.core import logging_config

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://key@o0.ingest.sentry.io/1")
    monkeypatch.setattr(settings, "SENTRY_RELEASE", "abc1234")
    monkeypatch.setattr(logging_config, "_configured", False)
    logging_config.configure_logging()
    assert calls and calls[0]["release"] == "abc1234" and calls[0]["send_default_pii"] is False


def test_readiness_says_redis_is_not_used_rather_than_down(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """./start.sh runs without Redis when it isn't installed: that's a supported setup, not an outage."""
    from app.config import settings

    monkeypatch.setattr(settings, "REDIS_URL", "")
    checks = client.get("/health/ready").json()["checks"]
    assert checks["redis"] == "not used (in-process tasks)"


def test_the_local_scheduler_runs_every_job_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """One failing job never stops the others, and the loop ends when asked (Ctrl-C in ./start.sh)."""
    from app.worker import dispatch, local_scheduler

    ran: list[str] = []

    def boom() -> None:
        ran.append("boom")
        raise RuntimeError("a job failed")

    monkeypatch.setattr(local_scheduler, "SCHEDULE", [("boom", 3600, True), ("ok", 3600, True)])
    monkeypatch.setattr(local_scheduler, "FIRST_RUN_DELAY", 0.0)
    monkeypatch.setattr(local_scheduler, "TICK", 0.05)
    monkeypatch.setattr(dispatch, "_load_registry", lambda: None)
    monkeypatch.setitem(dispatch._registry, "boom", boom)
    monkeypatch.setitem(dispatch._registry, "ok", lambda: ran.append("ok"))
    stop = threading.Event()
    thread = threading.Thread(target=local_scheduler.run, args=(stop,), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while len(ran) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    stop.set()
    thread.join(timeout=2)
    assert ran == ["boom", "ok"] and not thread.is_alive()
