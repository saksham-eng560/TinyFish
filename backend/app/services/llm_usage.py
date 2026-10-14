"""Per-user daily AI budget and usage meter (docs/HIREFLOW_PLAN.md §5.2).

Every LLM call made for a user counts against ``LLM_CALLS_PER_USER_PER_DAY`` (``DEMO_LLM_CALLS_PER_USER_PER_DAY``
in the demo). Past it, calls fail with :class:`LLMBudgetExceeded`, an ``LLMUnavailable``, so every feature
falls back to its rule-based version until the next day (UTC) instead of breaking.

Calls are attributed with :func:`for_user`: the API sets it for each signed-in request, the worker for
each task, and it's copied into the scan's scoring threads.
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, TypeVar

from app.config import settings
from app.services.rate_limiter import rate_limiter

T = TypeVar("T")
_user: contextvars.ContextVar[str | None] = contextvars.ContextVar("llm_user", default=None)


@contextmanager
def for_user(user_id: uuid.UUID | str | None) -> Iterator[None]:
    token = _user.set(str(user_id) if user_id else None)
    try:
        yield
    finally:
        _user.reset(token)


def current_user() -> str | None:
    return _user.get()


def in_context(fn: Callable[..., T]) -> Callable[..., T]:
    """Run ``fn`` (e.g. in a thread pool) with the caller's user attribution."""
    ctx = contextvars.copy_context()

    def run(*args: Any, **kwargs: Any) -> T:
        return ctx.run(fn, *args, **kwargs)

    return run


def daily_limit() -> int:
    """0 = no limit."""
    return settings.DEMO_LLM_CALLS_PER_USER_PER_DAY if settings.DEMO_MODE else settings.LLM_CALLS_PER_USER_PER_DAY


def _key(user_id: str) -> str:
    return f"llm_calls:{user_id}:{datetime.now(UTC).date().isoformat()}"


def used_today(user_id: uuid.UUID | str) -> int:
    return int(rate_limiter._get(_key(str(user_id))))


def charge() -> None:
    """Count one LLM call for the current user; raise once today's budget is used up."""
    from app.services.llm import LLMBudgetExceeded

    user_id = _user.get()
    limit = daily_limit()
    if not user_id or limit <= 0:
        return
    if used_today(user_id) >= limit:
        raise LLMBudgetExceeded(f"Today's AI limit is used up ({limit} calls): rule-based answers until tomorrow (UTC)")
    rate_limiter._incr(_key(user_id), ttl=2 * 24 * 3600)
