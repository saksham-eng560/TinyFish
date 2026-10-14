"""First-run onboarding wizard (docs/HIREFLOW_PLAN.md §4): progress, per-step saves and completion.

Every step saves as you go, so a refresh never loses work. Steps 3 (profiles & links) and 7 (connect) can be
skipped; the others can't. Finishing checks the required pieces and can start your first scan.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.agent import queue_scan, running_scan
from app.api.deps import DB, CurrentUser, limiter
from app.api.resumes import create_resume_from_text
from app.api.serializers import run_out
from app.api.users import validate_preferences
from app.config import settings
from app.models.user import User, UserFieldMapping, merge_preferences
from app.schemas.onboarding import (
    ANSWER_KEYS,
    LAST_STEP,
    SKIPPABLE_STEPS,
    STEP_MODELS,
    STEPS,
    AnswersStep,
    ApplyStep,
    ConnectStep,
    OnboardingComplete,
    OnboardingPatch,
    ProfilesStep,
    TargetsStep,
    WelcomeStep,
)
from app.services import demo, sources
from app.services.agent_orchestrator import get_master_resume
from app.services.presets import PRESETS, apply_preset, clamp_daily_cap

router = APIRouter(prefix="/users/me/onboarding", tags=["onboarding"])

# Sources whose terms don't allow automation: opt-in, with the consent recorded (docs/HIREFLOW_PLAN.md §5.4)
CONSENT_SOURCES = {"linkedin_consent": ("linkedin", "linkedin_automation"),
                   "internshala_consent": ("internshala", "internshala_automation")}


def _mappings(user: User) -> dict[str, str]:
    return {m.field_name: m.field_value for m in user.field_mappings}


def _set_mapping(db: Session, user: User, name: str, value: str | None, field_type: str = "text") -> None:
    existing = next((m for m in user.field_mappings if m.field_name == name), None)
    if value in (None, ""):
        if existing is not None:
            user.field_mappings.remove(existing)
        return
    if existing is not None:
        existing.field_value, existing.field_type = str(value), field_type
    else:
        user.field_mappings.append(UserFieldMapping(field_name=name, field_value=str(value), field_type=field_type))


def _done(db: Session, user: User) -> dict[int, bool]:
    prefs = user.prefs
    return {
        1: bool((user.full_name or "").strip()),
        2: get_master_resume(db, user) is not None,
        3: bool(user.linkedin_url or user.github_url or user.portfolio_url or user.profile_links),
        4: bool(prefs.get("target_roles")) and bool(prefs.get("target_locations")),
        5: bool(_mappings(user).get("work_authorization")),
        6: user.onboarding_step > 6 or user.onboarding_completed_at is not None,
        7: user.onboarding_step > 7 or user.onboarding_completed_at is not None,
        8: user.onboarding_completed_at is not None,
    }


def _missing(done: dict[int, bool], user: User) -> list[str]:
    """What blocks finishing: the required steps' required fields."""
    missing = []
    if not done[1]:
        missing.append("your name")
    if not done[2]:
        missing.append("your resume")
    prefs = user.prefs
    if not prefs.get("target_roles"):
        missing.append("target roles")
    if not prefs.get("target_locations"):
        missing.append("locations")
    if not done[5]:
        missing.append("work authorization")
    return missing


def _recommended(user: User) -> list[str]:
    """Skipped recommended steps, shown as a dashboard banner."""
    out = []
    if not user.linkedin_url:
        out.append("linkedin")
    if not user.github_url:
        out.append("github")
    return out


def onboarding_state(db: Session, user: User) -> dict[str, Any]:
    prefs = user.prefs
    mappings = _mappings(user)
    master = get_master_resume(db, user)
    info = ((master.parsed_content or {}).get("personal_info") or {}) if master else {}
    consents = user.consents or {}
    done = _done(db, user)
    return {
        "step": min(max(user.onboarding_step or 1, 1), LAST_STEP),
        "completed": user.onboarding_completed_at is not None,
        "completed_at": user.onboarding_completed_at.isoformat() if user.onboarding_completed_at else None,
        "steps": [{**s, "done": done[s["id"]], "skippable": s["id"] in SKIPPABLE_STEPS} for s in STEPS],
        "missing": _missing(done, user),
        "recommended": _recommended(user),
        "demo_mode": settings.DEMO_MODE,
        "daily_cap_ceiling": settings.MAX_APPLICATIONS_PER_DAY_CEILING,
        "presets": list(PRESETS),
        "data": {
            "welcome": {"full_name": user.full_name, "email": user.email, "phone": user.phone,
                        "location": user.location, "timezone": prefs.get("timezone")},
            "resume": {"resume_id": str(master.id) if master else None,
                       "filename": master.original_filename if master else None},
            "profiles": {"linkedin_url": user.linkedin_url or info.get("linkedin") or None,
                         "github_url": user.github_url or info.get("github") or None,
                         "portfolio_url": user.portfolio_url or info.get("portfolio") or None,
                         "profile_links": user.profile_links or []},
            "targets": {key: prefs.get(key) for key in ("target_roles", "target_locations", "remote_preference",
                                                        "location_focus", "internship_season", "internships_only",
                                                        "year_of_study", "graduation_year", "focus_skills",
                                                        "avoid_skills")}
                       | {"expected_stipend": int(mappings["expected_stipend"]) if (mappings.get("expected_stipend") or "").isdigit() else None},
            "answers": {key: mappings.get(key) for key in ANSWER_KEYS},
            "apply": {key: prefs.get(key) for key in ApplyStep.model_fields},
            "connect": {"linkedin_consent": "linkedin_automation" in consents,
                        "internshala_consent": "internshala_automation" in consents,
                        "google_connected": user.google_connected,
                        "linkedin_connected": bool(user.linkedin_session_cookie),
                        "internshala_connected": bool(user.internshala_session_valid)},
        },
    }


def _validation_error(exc: ValidationError) -> HTTPException:
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first.get("loc", ()) if p != "__root__")
    message = str(first.get("msg", "invalid value")).removeprefix("Value error, ")
    return HTTPException(422, f"{where}: {message}" if where else message)


def _save_preferences(user: User, updates: dict[str, Any], base: dict[str, Any] | None = None) -> None:
    prefs = clamp_daily_cap(merge_preferences(base if base is not None else user.preferences, updates))
    validate_preferences(prefs)
    user.preferences = prefs


def _apply_step(db: Session, user: User, step: int, data: dict[str, Any]) -> None:
    model = STEP_MODELS[step]
    try:
        body = model.model_validate(data)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    if isinstance(body, WelcomeStep):
        user.full_name, user.phone, user.location = body.full_name, body.phone, body.location
        if body.timezone:
            _save_preferences(user, {"timezone": body.timezone})
    elif step == 2:
        if get_master_resume(db, user) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload your resume first")
    elif isinstance(body, ProfilesStep):
        user.linkedin_url, user.github_url, user.portfolio_url = body.linkedin_url, body.github_url, body.portfolio_url
        user.profile_links = [link.model_dump() for link in body.profile_links]
    elif isinstance(body, TargetsStep):
        base = apply_preset(user.preferences, body.preset) if body.preset else user.preferences
        base = {**base, "platforms": sources.keep_consented(user, user.prefs.get("platforms") or [], base.get("platforms") or [])}
        updates = body.model_dump(exclude={"preset", "expected_stipend"}, exclude_none=True)
        _save_preferences(user, updates, base=base)
        _set_mapping(db, user, "expected_stipend", str(body.expected_stipend) if body.expected_stipend is not None else None,
                     "number")
    elif isinstance(body, AnswersStep):
        for key in ANSWER_KEYS:
            if key in data or getattr(body, key) is not None:  # a key sent as null clears that answer
                value = getattr(body, key)
                _set_mapping(db, user, key, value.isoformat() if hasattr(value, "isoformat") else value,
                             "number" if key == "availability_months" else "text")
    elif isinstance(body, ApplyStep):
        _save_preferences(user, body.model_dump())
    elif isinstance(body, ConnectStep):
        if settings.DEMO_MODE and (body.linkedin_consent or body.internshala_consent):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "LinkedIn and Internshala aren't available in the demo")
        consents = dict(user.consents or {})
        platforms = list(user.prefs.get("platforms") or [])
        for field, (platform, consent_key) in CONSENT_SOURCES.items():
            if getattr(body, field):
                consents.setdefault(consent_key, datetime.now(UTC).isoformat())
                if platform not in platforms:
                    platforms.append(platform)
            else:
                consents.pop(consent_key, None)
                platforms = [p for p in platforms if p != platform]
        user.consents = consents
        _save_preferences(user, {"platforms": platforms})


@router.get("")
def get_onboarding(user: CurrentUser, db: DB) -> dict:
    """Current step, completed steps, what's missing, and the saved data to prefill each step."""
    return onboarding_state(db, user)


@router.patch("")
def save_step(body: OnboardingPatch, user: CurrentUser, db: DB) -> dict:
    """Save one step (validated for that step) and move on to the next one."""
    if body.skip:
        if body.step not in SKIPPABLE_STEPS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Step {body.step} is required and can't be skipped")
    else:
        _apply_step(db, user, body.step, body.data)
    user.onboarding_step = max(user.onboarding_step or 1, min(body.step + 1, LAST_STEP))
    db.flush()
    return onboarding_state(db, user)


@router.post("/complete")
@limiter.limit(settings.RATE_LIMIT_SCAN)  # it can start a scan
def complete_onboarding(request: Request, body: OnboardingComplete, user: CurrentUser, db: DB) -> dict:
    """Check the required steps, mark onboarding done and (by default) start the first scan."""
    missing = _missing(_done(db, user), user)
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Finish these first: {', '.join(missing)}")
    if user.onboarding_completed_at is None:
        user.onboarding_completed_at = datetime.now(UTC)
    user.onboarding_step = LAST_STEP
    run = None
    if body.start_scan:
        run = running_scan(db, user) or queue_scan(db, user)
    db.flush()
    return {**onboarding_state(db, user), "run": run_out(run) if run else None}


@router.post("/sample")
def load_sample_profile(user: CurrentUser, db: DB) -> dict:
    """Demo mode only: fill every step with a sample candidate so you can click straight through."""
    if not settings.DEMO_MODE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    apply_sample_profile(db, user)
    return onboarding_state(db, user)


def apply_sample_profile(db: Session, user: User) -> None:
    """Fill every onboarding step with the sample candidate (also used to seed the demo account)."""
    sample = demo.SAMPLE_PROFILE
    _apply_step(db, user, 1, {"full_name": user.full_name or "Sam Rivera", **sample["welcome"]})
    if get_master_resume(db, user) is None:
        create_resume_from_text(db, user, demo.SAMPLE_RESUME_TEXT, "Sample resume")
    for step, key in ((3, "profiles"), (4, "targets"), (5, "answers"), (6, "apply")):
        _apply_step(db, user, step, sample[key])
    user.onboarding_step = max(user.onboarding_step or 1, LAST_STEP - 1)
    db.flush()
