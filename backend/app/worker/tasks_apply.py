"""Application preparation / staging / submission tasks (browser queue)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from app.core.database import session_scope
from app.models.application import Application
from app.services import agent_orchestrator as orch
from app.services import guardrails, llm_usage
from app.worker.celery_app import celery_app
from app.worker.dispatch import enqueue, register

logger = logging.getLogger(__name__)


def _owner(application_id: str) -> uuid.UUID | None:
    """Whose application it is: AI calls made while working on it count against that user's budget."""
    with session_scope() as db:
        return db.scalar(select(Application.user_id).where(Application.id == uuid.UUID(str(application_id))))


@register("prepare_application")
def prepare_application(application_id: str) -> None:
    with llm_usage.for_user(_owner(application_id)), session_scope() as db:
        orch.prepare_application(db, application_id)


@register("stage_application")
def stage_application(application_id: str) -> None:
    with llm_usage.for_user(_owner(application_id)), session_scope() as db:
        orch.stage_application(db, application_id)


@register("submit_application")
def submit_application(application_id: str) -> None:
    with llm_usage.for_user(_owner(application_id)), session_scope() as db:
        orch.submit_application(db, application_id)


@register("send_due_applications")
def send_due_applications() -> int:
    """Every minute: send held applications whose time has come ("Sending soon", daily / company limits), and
    restart preparations a restart interrupted. Returns how many were sent."""
    with session_scope() as db:
        claimed = guardrails.claim_due(db)
        stalled = orch.requeue_stalled_preparations(db)
    for application_id in claimed:  # claimed and committed first, so no other sweep sends them too
        enqueue("submit_application", application_id)
    for application_id in stalled:
        enqueue("prepare_application", application_id)
    return len(claimed)


@celery_app.task(name="hireflow.prepare_application", soft_time_limit=900)
def prepare_application_task(application_id: str) -> None:
    prepare_application(application_id)


@celery_app.task(name="hireflow.stage_application", soft_time_limit=600)
def stage_application_task(application_id: str) -> None:
    stage_application(application_id)


@celery_app.task(name="hireflow.submit_application", soft_time_limit=600)
def submit_application_task(application_id: str) -> None:
    submit_application(application_id)


@celery_app.task(name="hireflow.send_due_applications")
def send_due_applications_task() -> int:
    return send_due_applications()
