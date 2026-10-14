#!/usr/bin/env python3
"""Celery-Beat stand-in for local mode (no Redis): runs the periodic agent jobs in-process.

    python scripts/local_scheduler.py        # started for you by ./start.sh

Scheduled scans, e-mail polling, interview reminders and clean-ups run here; the tasks they
enqueue (preparing kept jobs, submitting) run in this process's background threads.
The schedule itself lives in app/worker/local_scheduler.py.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "backend" if (_ROOT / "backend" / "app").is_dir() else _ROOT))

from app.config import settings
from app.worker.local_scheduler import run

logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s [scheduler] %(levelname)s %(message)s")


def main() -> None:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run(stop)


if __name__ == "__main__":
    main()
