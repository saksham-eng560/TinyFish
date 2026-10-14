"""The demo's shared, pre-filled account (the end-to-end tests sign in to it) and the nightly reset (docs/HIREFLOW_PLAN.md §5.5).

The account is the sample candidate from onboarding, with a few past applications at fictional companies
(so every dashboard page has something to show) and a Swipe Review deck from the bundled demo careers
site. Every night everything is wiped, every visitor's account included, and the account is re-created.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import hash_password
from app.models.application import Application, ApplicationStatusHistory
from app.models.communication import Communication
from app.models.enums import ApplicationStatus, ATSPlatform, EmailDirection, EmailIntent, InterviewType, JobType
from app.models.interview import Interview
from app.models.job import Job
from app.models.user import User, default_preferences
from app.services import demo_site
from app.services.privacy import delete_user_data
from app.services.text_utils import dedupe_key, extract_skills

logger = logging.getLogger(__name__)

# Past applications at fictional companies: (company, title, location, final status, days ago, what came back)
HISTORY: list[tuple[str, str, str, ApplicationStatus, int, EmailIntent | None]] = [
    ("Litware Security", "Python Developer Intern", "Remote, India", ApplicationStatus.OFFER, 21, EmailIntent.OFFER),
    ("Tailspin Mobility", "Backend Intern", "Pune, India", ApplicationStatus.INTERVIEW, 12, EmailIntent.INTERVIEW_INVITE),
    ("Woodgrove Health", "Software Engineering Intern", "Gurugram, India", ApplicationStatus.SCREENING, 9,
     EmailIntent.INTERVIEW_INVITE),
    ("Northwind Analytics", "Analytics Engineering Intern", "Hyderabad, India", ApplicationStatus.ACKNOWLEDGED, 6,
     EmailIntent.ACKNOWLEDGMENT),
    ("Proseware Labs", "Software Intern", "Delhi, India", ApplicationStatus.REJECTED, 15, EmailIntent.REJECTION),
    ("Contoso Cloud", "Platform Intern", "Remote, India", ApplicationStatus.APPLIED, 2, None),
    ("Lucerne Software", "Python Tooling Intern", "Remote, India", ApplicationStatus.APPLIED, 1, None),
]
_PIPELINE = [ApplicationStatus.MATCHED, ApplicationStatus.PREPARING, ApplicationStatus.PENDING_APPROVAL,
             ApplicationStatus.APPROVED, ApplicationStatus.APPLIED]


def demo_account(db: Session) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == settings.DEMO_ACCOUNT_EMAIL.lower()))


def is_demo_account(user: User) -> bool:
    return settings.DEMO_MODE and user.email.lower() == settings.DEMO_ACCOUNT_EMAIL.lower()


def _history(db: Session, user: User, now: datetime) -> None:
    for idx, (company, title, location, status, days, intent) in enumerate(HISTORY):
        url = f"{settings.demo_site_url}/archive/{idx}"
        description = (f"{title} at {company} (fictional company). Summer 2027 internship working with Python, SQL and "
                       "Docker alongside a mentor.")
        job = Job(company_name=company, role_title=title, description=description, location=location,
                  is_remote=location.startswith("Remote"), source_platform=ATSPlatform.CUSTOM, job_type=JobType.INTERNSHIP,
                  source_url=url, application_url=url, dedupe_key=dedupe_key(company, title, location),
                  extracted_skills=extract_skills(description), company_verdict="verified",
                  posted_date=(now - timedelta(days=days + 3)).date(),
                  raw_data={"listing_source": "demo", "demo": True, "terms": ["Summer 2027"]})
        db.add(job)
        db.flush()
        sent = now - timedelta(days=days)
        app = Application(user_id=user.id, job_id=job.id, status=status, match_score=78 - idx * 3,
                          match_reasoning="Strong overlap with your Python and FastAPI experience.",
                          ats_platform=ATSPlatform.CUSTOM, review_decision="keep", reviewed_at=sent - timedelta(hours=3),
                          approved_at=sent - timedelta(hours=1), submitted_at=sent, created_at=sent - timedelta(days=1),
                          updated_at=sent + timedelta(days=1), confirmation_number=f"DEMO-{900 + idx}",
                          cover_letter=f"Dear {company} team,\n\nI'd love to join you as a {title}...\n\nSincerely,\nSam Rivera",
                          custom_answers=[{"question": f"Why do you want to work at {company}?",
                                           "answer": f"{company}'s product and the chance to ship real features excite me.",
                                           "confidence": 0.8, "needs_user_review": False, "source": "llm"}])
        db.add(app)
        db.flush()
        previous = None
        for step in _PIPELINE:
            db.add(ApplicationStatusHistory(application_id=app.id, old_status=previous, new_status=step,
                                            changed_by="agent" if step == ApplicationStatus.APPLIED else "user",
                                            created_at=sent - timedelta(hours=3)))
            previous = step
        if intent is None:
            continue
        reply_at = sent + timedelta(days=2)
        app.first_response_at = reply_at
        db.add(ApplicationStatusHistory(application_id=app.id, old_status=previous, new_status=status,
                                        changed_by="email_parser", notes=f"Detected '{intent.value}' e-mail", created_at=reply_at))
        db.add(Communication(
            user_id=user.id, application_id=app.id, gmail_message_id=f"demo-{uuid.uuid4().hex[:10]}",
            direction=EmailDirection.INBOUND, sender_email=f"careers@{company.split()[0].lower()}.example",
            sender_name=f"{company} Recruiting", subject=f"{title}: next steps",
            body_text=f"Hi Sam,\n\nThanks for applying to {company}. (Demo e-mail: {intent.value.replace('_', ' ')}.)\n\n"
                      f"Best,\n{company} Recruiting",
            detected_intent=intent, intent_confidence=0.9, received_at=reply_at,
            urgency="high" if intent in (EmailIntent.INTERVIEW_INVITE, EmailIntent.OFFER) else "low",
            is_action_required=intent in (EmailIntent.INTERVIEW_INVITE, EmailIntent.OFFER)))
        if status in (ApplicationStatus.INTERVIEW, ApplicationStatus.SCREENING):
            db.add(Interview(application_id=app.id, scheduled_at=now + timedelta(days=2 + idx, hours=5),
                             interview_type=InterviewType.TECHNICAL if status == ApplicationStatus.INTERVIEW
                             else InterviewType.PHONE_SCREEN, duration_minutes=45, timezone="Asia/Kolkata",
                             meeting_platform="google_meet", meeting_link="https://meet.example/demo", outcome="pending",
                             prep_notes=f"{title} at {company}: lead with the FastAPI ticket classifier and StudyBuddy."))


def seed_demo_account(db: Session) -> User:
    """(Re)create the shared demo account: the sample candidate, a short history and a full Swipe Review deck."""
    from app.api.onboarding import apply_sample_profile
    from app.services.agent_orchestrator import run_scan

    existing = demo_account(db)
    if existing is not None:
        delete_user_data(db, existing)
        db.flush()
    now = datetime.now(UTC)
    prefs = {**default_preferences(), "dry_run": True}
    user = User(email=settings.DEMO_ACCOUNT_EMAIL, full_name="Sam Rivera",
                hashed_password=hash_password(secrets.token_urlsafe(32)),  # nobody signs in with a password here
                preferences=prefs, onboarding_step=8, onboarding_completed_at=now)
    db.add(user)
    db.flush()
    apply_sample_profile(db, user)
    user.onboarding_completed_at = now
    _history(db, user, now)
    run_scan(db, user, trigger="schedule")  # the deck, from the demo careers site (no network)
    db.flush()
    return user


def ensure_demo_account(db: Session) -> User:
    return demo_account(db) or seed_demo_account(db)


def reset_demo(db: Session) -> dict[str, Any]:
    """Nightly, in the demo only: delete every account (visitors' too) and every job, then re-create the demo."""
    if not settings.DEMO_MODE:
        return {"reset": False}
    users = db.scalars(select(User)).all()
    for user in users:
        delete_user_data(db, user)
    db.flush()
    db.execute(delete(Job))
    demo_site.SUBMISSIONS.clear()
    seed_demo_account(db)
    logger.info("Demo reset: %d accounts removed, demo account re-created", len(users))
    return {"reset": True, "accounts_removed": len(users)}
