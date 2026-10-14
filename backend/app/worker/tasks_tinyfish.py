"""TinyFish Finder search task."""

from __future__ import annotations

import uuid

from app.core.database import session_scope
from app.models.agent_run import AgentRun
from app.models.user import User
from app.services import llm_usage, tinyfish_finder
from app.worker.celery_app import celery_app
from app.worker.dispatch import register


@register("tinyfish_search")
def tinyfish_search(user_id: str, run_id: str) -> str | None:
    with llm_usage.for_user(user_id), session_scope() as db:
        user = db.get(User, uuid.UUID(user_id))
        run = db.get(AgentRun, uuid.UUID(run_id))
        if user is None or not user.is_active or run is None or run.user_id != user.id or run.run_type != tinyfish_finder.RUN_TYPE:
            return None
        tinyfish_finder.execute(db, user, run)
        return str(run.id)


@celery_app.task(name="hireflow.tinyfish_search")
def tinyfish_search_task(user_id: str, run_id: str) -> str | None:
    return tinyfish_search(user_id, run_id)
