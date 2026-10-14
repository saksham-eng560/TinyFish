"""Task dispatch that works with or without a Celery worker.

* With Redis + a running worker: ``apply_async`` on the named Celery task.
* ``CELERY_TASK_ALWAYS_EAGER=true`` (or no broker reachable): run in a local background thread
  so API requests never block on long jobs.

``after_commit=session`` defers dispatch until the surrounding DB transaction commits, so the
worker always sees the rows the task depends on.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hireflow-task")
_registry: dict[str, Callable[..., Any]] = {}
_inline = {"enabled": False}
_running: set[Future[None]] = set()  # local background tasks not finished yet


def register(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _registry[name] = fn
        return fn

    return decorator


def _load_registry() -> None:
    if not _registry:
        from app.worker import tasks_apply, tasks_calendar, tasks_demo, tasks_email, tasks_scan, tasks_sync  # noqa: F401


def use_local_execution() -> bool:
    return settings.CELERY_TASK_ALWAYS_EAGER or get_redis() is None


class run_inline:  # noqa: N801 - context manager used by tests / scripts
    """Execute dispatched tasks synchronously in the calling thread."""

    def __enter__(self) -> None:
        _inline["enabled"] = True

    def __exit__(self, *exc: Any) -> None:
        _inline["enabled"] = False


def _run_local(name: str, args: tuple[Any, ...], countdown: int | None) -> None:
    _load_registry()
    fn = _registry[name]

    def job() -> None:
        try:
            fn(*args)
        except Exception:
            logger.exception("Local task %s failed", name)

    if _inline["enabled"]:
        if not countdown:  # delayed retries are dropped in inline mode
            fn(*args)
        return
    if countdown:  # wait on a timer, not in a worker thread: a long wait never blocks other tasks
        timer = threading.Timer(countdown, lambda: _track(_executor.submit(job)))
        timer.daemon = True
        timer.start()
        return
    _track(_executor.submit(job))


def _track(future: Future[None]) -> None:
    _running.add(future)
    future.add_done_callback(_running.discard)


def wait_for_local_tasks(timeout: float = 60) -> None:
    """Wait until the local background tasks started so far have finished (tests: so a task an API call
    started finishes inside that test, not during the next one)."""
    wait(list(_running), timeout=timeout)


def _send(name: str, args: tuple[Any, ...], countdown: int | None) -> None:
    if _inline["enabled"] or use_local_execution():
        _run_local(name, args, countdown)
        return
    from app.worker.celery_app import celery_app

    celery_app.send_task(f"hireflow.{name}", args=list(args), countdown=countdown)


def enqueue(name: str, *args: Any, countdown: int | None = None, after_commit: Session | None = None) -> None:
    if after_commit is not None and after_commit.in_transaction():
        event.listen(after_commit, "after_commit", lambda _s: _send(name, args, countdown), once=True)
        return
    _send(name, args, countdown)
