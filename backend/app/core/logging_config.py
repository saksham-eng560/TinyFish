"""Logging + optional Sentry setup."""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.core import log_privacy

_configured = False


def _scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Sentry: mask personal data in the message and exception values before an event leaves the server."""
    logentry = event.get("logentry") or {}
    for key in ("message", "formatted"):
        if isinstance(logentry.get(key), str):
            logentry[key] = log_privacy.mask_pii(logentry[key])
    for exc in (event.get("exception") or {}).get("values") or []:
        if isinstance(exc.get("value"), str):
            exc["value"] = log_privacy.mask_pii(exc["value"])
    return event


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "httpx2", "httpcore", "googleapiclient.discovery_cache", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # No e-mail addresses, phone numbers or tokens in the logs (uvicorn's loggers keep their own handlers)
    log_privacy.install("uvicorn", "uvicorn.access", "uvicorn.error", "celery", "celery.task")
    if settings.SENTRY_DSN:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.SENTRY_DSN, environment=settings.ENVIRONMENT,
                        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
                        release=settings.SENTRY_RELEASE or None,
                        send_default_pii=False, before_send=_scrub_event)
    _configured = True
