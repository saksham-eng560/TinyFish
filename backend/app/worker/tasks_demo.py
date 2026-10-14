"""The public demo's nightly reset (DEMO_MODE only): every account and job is wiped and the demo re-created."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.core.database import session_scope
from app.services.demo_seed import reset_demo
from app.worker.celery_app import celery_app
from app.worker.dispatch import register


@register("reset_demo")
def reset_demo_data() -> dict[str, Any]:
    if not settings.DEMO_MODE:
        return {"reset": False}
    with session_scope() as db:
        return reset_demo(db)


@celery_app.task(name="hireflow.reset_demo")
def reset_demo_task() -> dict[str, Any]:
    return reset_demo_data()
