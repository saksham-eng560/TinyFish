"""GDPR / CCPA: full data export and one-click deletion (PLAN.md §15)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import get_storage, user_prefix
from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.communication import Communication
from app.models.interview import Interview
from app.models.resume import Resume
from app.models.user import Notification, User
from app.services.google_oauth import revoke

logger = logging.getLogger(__name__)


def _row(obj: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    exclude = exclude or set()
    out: dict[str, Any] = {}
    for column in obj.__table__.columns:
        if column.key in exclude:
            continue
        value = getattr(obj, column.key)
        if hasattr(value, "value"):
            value = value.value
        elif isinstance(value, datetime):
            value = value.isoformat()
        elif value is not None and not isinstance(value, (str, int, float, bool, list, dict)):
            value = str(value)
        out[column.key] = value
    return out


SECRET_USER_FIELDS = {"hashed_password", "google_access_token", "google_refresh_token", "linkedin_session_cookie",
                      "internshala_session",
                      "ats_credentials"}


def export_user_data(db: Session, user: User) -> dict[str, Any]:
    apps = db.scalars(select(Application).where(Application.user_id == user.id)).all()
    app_ids = [a.id for a in apps]
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "user": _row(user, SECRET_USER_FIELDS),
        "field_mappings": [
            {"field_name": m.field_name, "field_value": "********" if "password" in m.field_name else m.field_value,
             "field_type": m.field_type}
            for m in user.field_mappings
        ],
        "resumes": [_row(r, {"skills_embedding"}) for r in db.scalars(select(Resume).where(Resume.user_id == user.id)).all()],
        "applications": [
            {**_row(a), "job": _row(a.job, {"description_embedding"}),
             "history": [_row(h) for h in a.history]}
            for a in apps
        ],
        "communications": [_row(c) for c in db.scalars(select(Communication).where(Communication.user_id == user.id)).all()],
        "interviews": [_row(i) for i in db.scalars(select(Interview).where(Interview.application_id.in_(app_ids))).all()] if app_ids else [],
        "agent_runs": [_row(r) for r in db.scalars(select(AgentRun).where(AgentRun.user_id == user.id)).all()],
        "notifications": [_row(n) for n in db.scalars(select(Notification).where(Notification.user_id == user.id)).all()],
    }


def export_zip(db: Session, user: User) -> bytes:
    """Everything: the data as JSON plus every file stored for you (resumes, tailored PDFs, form screenshots)."""
    import io
    import json
    import zipfile

    storage = get_storage()
    prefix = user_prefix(user.id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("hireflow-data.json", json.dumps(export_user_data(db, user), indent=2, default=str))
        for key in storage.list_prefix(prefix):
            try:
                archive.writestr(f"files/{key[len(prefix) + 1:]}", storage.read(key))
            except Exception as exc:  # noqa: BLE001 - one unreadable file doesn't stop the export
                logger.warning("Export: could not read %s: %s", key, exc)
        archive.writestr("README.txt", "Your HireFlow data.\n\nhireflow-data.json: your account, resumes, applications, "
                         "e-mails, interviews, activity and notifications.\nfiles/: every file stored for you.\n"
                         "Passwords and access tokens are never exported.\n")
    return buffer.getvalue()


def delete_user_data(db: Session, user: User) -> None:
    """Permanently wipe resumes (DB + storage), applications, communications, tokens and the account."""
    try:
        revoke(user)
    except Exception:  # noqa: BLE001
        logger.warning("Token revoke failed during account deletion for %s", user.id)
    try:
        get_storage().delete_prefix(user_prefix(user.id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Storage cleanup failed for %s: %s", user.id, exc)
    app_ids = [a for a in db.scalars(select(Application.id).where(Application.user_id == user.id)).all()]
    if app_ids:
        for interview in db.scalars(select(Interview).where(Interview.application_id.in_(app_ids))).all():
            db.delete(interview)
    for comm in db.scalars(select(Communication).where(Communication.user_id == user.id)).all():
        db.delete(comm)
    db.flush()
    # Applications reference tailored resumes; delete them before resumes.
    for app in db.scalars(select(Application).where(Application.user_id == user.id)).all():
        db.delete(app)
    db.flush()
    for resume in db.scalars(select(Resume).where(Resume.user_id == user.id, Resume.parent_resume_id.is_not(None))).all():
        db.delete(resume)
    db.flush()
    db.delete(user)
    db.flush()
