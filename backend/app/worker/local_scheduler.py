"""Celery-Beat stand-in when there's no Redis: runs the periodic agent jobs in this process.

``scripts/local_scheduler.py`` runs it next to the API (``./start.sh`` starts it when Redis isn't installed).
The tasks the jobs enqueue (preparing kept jobs, submitting) run in this process's background threads.
"""

from __future__ import annotations

import logging
import threading
import time

from app.config import settings
from app.worker import dispatch

log = logging.getLogger("scheduler")

# (task name, interval in seconds, run on start-up?)
SCHEDULE: list[tuple[str, int, bool]] = [
    ("scan_due_users", 60 * 60, True),
    ("send_due_applications", 60, True),  # "Sending soon" and limit-held applications, once they're due
    ("reset_demo", 24 * 3600, False),  # the public demo's nightly reset (does nothing unless DEMO_MODE)
    ("check_all_emails", max(60, settings.EMAIL_POLL_MINUTES * 60), False),
    ("send_interview_reminders", 10 * 60, False),
    ("renew_gmail_watches", 24 * 3600, False),
    ("linkedin_sync_all", 24 * 3600, False),
    ("expire_stale_jobs", 24 * 3600, False),
    ("retention_cleanup", 24 * 3600, False),
    ("weekly_summary", 7 * 24 * 3600, False),
    ("progress_digest", 60 * 60, False),  # sends at ~20:00 in your time zone
]

FIRST_RUN_DELAY = 20.0  # seconds after start-up for the jobs marked "run on start-up"
TICK = 5.0


def run(stop: threading.Event) -> None:
    """Run the schedule until ``stop`` is set. One failing job never stops the others."""
    dispatch._load_registry()
    now = time.monotonic()
    next_run = {name: (now + FIRST_RUN_DELAY if on_start else now + interval) for name, interval, on_start in SCHEDULE}
    log.info("Local scheduler started: %s", ", ".join(name for name, _, _ in SCHEDULE))
    while not stop.is_set():
        now = time.monotonic()
        for name, interval, _ in SCHEDULE:
            if now >= next_run[name]:
                next_run[name] = now + interval
                try:
                    result = dispatch._registry[name]()
                    log.info("%s -> %s", name, result)
                except Exception:  # keep the loop alive whatever a job does
                    log.exception("%s failed", name)
        stop.wait(TICK)
    log.info("Local scheduler stopped")

