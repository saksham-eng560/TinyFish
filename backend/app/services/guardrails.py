"""Applying safely (docs/HIREFLOW_PLAN.md §5.1): limits and checks that hold whatever the UI sends.

* **Daily cap**: your ``max_applications_per_day``, never above ``MAX_APPLICATIONS_PER_DAY_CEILING``.
* **Per company**: at most ``MAX_APPLICATIONS_PER_COMPANY_PER_WEEK`` applications to one company in 7 days.
* **Duplicates**: never twice to the same job URL, or to the same company + title.
* **Pause everything**: while paused nothing is scanned, prepared or sent.
* **Dry run**: forms are filled and screenshotted, but Submit is never clicked.
* **Undo window**: an automatic submit waits ``SEND_DELAY`` in "Sending soon", so you can stop it.

Held applications stay APPROVED with ``send_after`` set; :func:`send_due` (every minute) sends them
once they're due.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models.application import Application, ApplicationStatusHistory
from app.models.enums import SUBMITTED_STATUSES, ApplicationStatus
from app.models.job import Job
from app.models.user import User
from app.services.rate_limiter import rate_limiter
from app.services.text_utils import normalize_company, normalize_title

logger = logging.getLogger(__name__)

SEND_DELAY = timedelta(minutes=10)
COMPANY_WINDOW = timedelta(days=7)


class Paused(Exception):
    """HireFlow is paused for this user."""


# --------------------------------------------------------------------------- pause & dry run
def is_paused(user: User) -> bool:
    return user.automation_paused_at is not None


def paused_now(db: Session, user: User) -> bool:
    """Re-read the switch: a long task notices a pause flipped while it was running."""
    db.refresh(user, attribute_names=["automation_paused_at"])
    return is_paused(user)


def dry_run(user: User) -> bool:
    return settings.SUBMISSION_DRY_RUN or bool(user.prefs.get("dry_run"))


# --------------------------------------------------------------------------- daily cap
def daily_cap(user: User) -> int:
    try:
        wanted = int(user.prefs.get("max_applications_per_day") or 10)
    except (TypeError, ValueError):
        wanted = 10
    return max(1, min(wanted, settings.MAX_APPLICATIONS_PER_DAY_CEILING))


def day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def sent_by_agent_since(db: Session, user: User, since: datetime) -> int:
    """Applications HireFlow submitted for you since ``since`` (from the status history, so it survives restarts)."""
    return db.scalar(
        select(func.count(func.distinct(ApplicationStatusHistory.application_id)))
        .join(Application, Application.id == ApplicationStatusHistory.application_id)
        .where(Application.user_id == user.id, ApplicationStatusHistory.new_status == ApplicationStatus.APPLIED,
               ApplicationStatusHistory.changed_by == "agent", ApplicationStatusHistory.created_at >= since)
    ) or 0


def applied_today(db: Session, user: User) -> int:
    return max(rate_limiter.applications_today(str(user.id)), sent_by_agent_since(db, user, day_start()))


def daily_cap_wait(db: Session, user: User, now: datetime | None = None) -> datetime | None:
    """When today's cap is used up: the time sending can resume (just after midnight UTC)."""
    now = now or datetime.now(UTC)
    if applied_today(db, user) < daily_cap(user):
        return None
    return day_start(now) + timedelta(days=1, minutes=5)


# --------------------------------------------------------------------------- per company
def company_cap() -> int:
    return max(1, settings.MAX_APPLICATIONS_PER_COMPANY_PER_WEEK)


def company_cap_wait(db: Session, user: User, app: Application, now: datetime | None = None) -> datetime | None:
    """When you've applied to this company ``company_cap()`` times in 7 days: when the oldest drops out."""
    now = now or datetime.now(UTC)
    company = normalize_company(app.job.company_name)
    if not company:
        return None
    rows = db.execute(
        select(Job.company_name, Application.submitted_at)
        .join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user.id, Application.id != app.id,
               Application.submitted_at.is_not(None), Application.submitted_at >= now - COMPANY_WINDOW,
               Application.status.in_(SUBMITTED_STATUSES))
    ).all()
    sent = sorted(_aware(at) for name, at in rows if normalize_company(name) == company)
    if len(sent) < company_cap():
        return None
    return sent[-company_cap()] + COMPANY_WINDOW + timedelta(minutes=1)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- duplicates
def normalize_url(url: str | None) -> str:
    """Scheme, query and fragment don't make a different job: https://Jobs.x.com/a/?src=li -> jobs.x.com/a"""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}" if host else ""


def duplicate_of(db: Session, user: User, app: Application) -> Application | None:
    """Another application of yours to the same job URL or the same company + title that was already sent
    (by HireFlow or by you) or is about to be."""
    job = app.job
    urls = {u for u in (normalize_url(job.application_url), normalize_url(job.source_url)) if u}
    role = (normalize_company(job.company_name), normalize_title(job.role_title))
    others = db.scalars(
        select(Application).join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user.id, Application.id != app.id,
               Application.status.in_([*SUBMITTED_STATUSES, ApplicationStatus.APPROVED]))
    ).all()
    for other in others:
        if other.status == ApplicationStatus.APPROVED and not _ahead(other, app):
            continue  # two copies approved together: the first approved goes, the other one is the duplicate
        o = other.job
        if urls & {normalize_url(o.application_url), normalize_url(o.source_url)} - {""}:
            return other
        if role[0] and role[1] and (normalize_company(o.company_name), normalize_title(o.role_title)) == role:
            return other
    return None


def _ahead(other: Application, app: Application) -> bool:
    mine, theirs = app.approved_at, other.approved_at
    if theirs is None or mine is None:
        return theirs is not None
    return (_aware(theirs), str(other.id)) < (_aware(mine), str(app.id))


def duplicate_message(other: Application) -> str:
    when = f" on {other.submitted_at:%d %b %Y}" if other.submitted_at else ""
    return (f"You already applied to {other.job.role_title} at {other.job.company_name}{when}. "
            "HireFlow never applies twice to the same role.")


# --------------------------------------------------------------------------- held sends
def hold(app: Application, until: datetime, note: str) -> None:
    app.send_after = until
    app.notes = note


def due(app: Application, now: datetime | None = None) -> bool:
    return app.send_after is None or _aware(app.send_after) <= (now or datetime.now(UTC))


def claim_due(db: Session, now: datetime | None = None, limit: int = 200) -> list[str]:
    """Held applications whose time has come, claimed atomically (so two sweeps never send one twice)."""
    now = now or datetime.now(UTC)
    paused = select(User.id).where(User.automation_paused_at.is_not(None))
    candidates = db.scalars(
        select(Application.id).where(Application.status == ApplicationStatus.APPROVED,
                                     Application.send_after.is_not(None), Application.send_after <= now,
                                     Application.user_id.not_in(paused))
        .order_by(Application.send_after).limit(limit)
    ).all()
    claimed = []
    for app_id in candidates:
        result = db.execute(
            update(Application).where(Application.id == app_id, Application.status == ApplicationStatus.APPROVED,
                                      Application.send_after.is_not(None), Application.send_after <= now)
            .values(send_after=None).execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            claimed.append(str(app_id))
    return claimed


def release(db: Session, app: Application) -> bool:
    """Take a held application out of the queue (stop it, or send it now). False if it isn't held any more:
    the sweep already claimed it, or it was never waiting."""
    result = db.execute(
        update(Application).where(Application.id == app.id, Application.status == ApplicationStatus.APPROVED,
                                  Application.send_after.is_not(None))
        .values(send_after=None).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return False
    db.refresh(app, attribute_names=["send_after"])
    return True
