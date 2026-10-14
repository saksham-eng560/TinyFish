"""First-run onboarding: one Pydantic model per wizard step (docs/HIREFLOW_PLAN.md §4).

Steps: 1 welcome · 2 resume · 3 profiles & links · 4 what you're looking for · 5 your answers ·
6 how HireFlow applies · 7 connect (optional) · 8 all set (``POST /users/me/onboarding/complete``).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import settings
from app.schemas.user import ProfileLink, normalize_url

STEPS: list[dict[str, Any]] = [
    {"id": 1, "key": "welcome", "title": "Welcome", "required": True},
    {"id": 2, "key": "resume", "title": "Resume", "required": True},
    {"id": 3, "key": "profiles", "title": "Profiles & links", "required": False},
    {"id": 4, "key": "targets", "title": "What you're looking for", "required": True},
    {"id": 5, "key": "answers", "title": "Your answers", "required": True},
    {"id": 6, "key": "apply", "title": "How HireFlow applies", "required": True},
    {"id": 7, "key": "connect", "title": "Connect", "required": False},
    {"id": 8, "key": "done", "title": "All set", "required": True},
]
LAST_STEP = len(STEPS)
SKIPPABLE_STEPS = {s["id"] for s in STEPS if not s["required"]}

YesNo = Literal["Yes", "No"]
YesNoPreferNot = Literal["Yes", "No", "Prefer not to say"]
_PHONE = re.compile(r"^\+?[0-9 ()\-.]{7,25}$")


def _clean_list(values: list[str], max_len: int) -> list[str]:
    out: list[str] = []
    for v in values:
        v = (v or "").strip()
        if not v:
            continue
        if len(v) > max_len:
            raise ValueError(f"each entry must be at most {max_len} characters")
        if v.lower() not in {o.lower() for o in out}:
            out.append(v)
    return out


class _Step(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WelcomeStep(_Step):
    full_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=255)
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if not v:
            return None
        if not _PHONE.match(v) or sum(c.isdigit() for c in v) < 7:
            raise ValueError("enter a phone number with at least 7 digits, e.g. +91 98765 43210")
        return v

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, v: str | None) -> str | None:
        if not v:
            return None
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown time zone; use a name like Asia/Kolkata or America/New_York") from exc
        return v


class ResumeStep(_Step):
    """The resume itself is uploaded with ``POST /resumes/upload`` (or ``/from-text``) and corrected with
    ``PUT /resumes/{id}``; this step only confirms that the parsed content was checked."""


class ProfilesStep(_Step):
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    profile_links: list[ProfileLink] = Field(default_factory=list, max_length=10)

    @field_validator("linkedin_url")
    @classmethod
    def _linkedin(cls, v: str | None) -> str | None:
        return normalize_url(v, host="linkedin.com")

    @field_validator("github_url")
    @classmethod
    def _github(cls, v: str | None) -> str | None:
        return normalize_url(v, host="github.com")

    @field_validator("portfolio_url")
    @classmethod
    def _portfolio(cls, v: str | None) -> str | None:
        return normalize_url(v)


class TargetsStep(_Step):
    preset: Literal["ai-engineer", "internships", "startups", "new-grad", "india-internships"] | None = None
    target_roles: list[str] = Field(min_length=1, max_length=20)
    target_locations: list[str] = Field(min_length=1, max_length=20)
    remote_preference: Literal["remote", "hybrid", "onsite", "any"] = "any"
    location_focus: dict[str, Any] | None = None
    internship_season: str | None = Field(default=None, max_length=40)
    internships_only: bool | None = None
    year_of_study: int | None = Field(default=None, ge=1, le=5)
    graduation_year: int | None = Field(default=None, ge=2000, le=2100)
    focus_skills: list[str] = Field(default_factory=list, max_length=50)
    avoid_skills: list[str] = Field(default_factory=list, max_length=50)
    expected_stipend: int | None = Field(default=None, ge=0, le=10_000_000)

    @field_validator("target_roles", "target_locations")
    @classmethod
    def _required_list(cls, v: list[str]) -> list[str]:
        cleaned = _clean_list(v, 100)
        if not cleaned:
            raise ValueError("add at least one")
        return cleaned

    @field_validator("focus_skills", "avoid_skills")
    @classmethod
    def _skills(cls, v: list[str]) -> list[str]:
        return _clean_list(v, 60)


class AnswersStep(_Step):
    """Questions only you can answer. Each is saved as a field mapping, so forms use it; nothing here is guessed."""

    work_authorization: YesNo
    over_18: YesNo | None = None
    requires_sponsorship: YesNoPreferNot | None = None
    earliest_start_date: dt.date | None = None
    availability_months: int | None = Field(default=None, ge=1, le=24)
    willing_to_relocate: YesNoPreferNot | None = None
    notice_period: str | None = Field(default=None, max_length=100)
    pronouns: str | None = Field(default=None, max_length=60)
    gender: str | None = Field(default=None, max_length=100)
    race_ethnicity: str | None = Field(default=None, max_length=100)
    hispanic_latino: str | None = Field(default=None, max_length=100)
    veteran_status: str | None = Field(default=None, max_length=100)
    disability_status: str | None = Field(default=None, max_length=100)


ANSWER_KEYS = tuple(AnswersStep.model_fields)


class ApplyStep(_Step):
    review_mode: Literal["swipe", "auto"] = "swipe"
    auto_submit_kept: bool = False
    max_applications_per_day: int = Field(default=10, ge=1)
    resume_strategy: Literal["original", "light", "full"] = "original"
    cover_letter_enabled: bool = True

    @field_validator("max_applications_per_day")
    @classmethod
    def _ceiling(cls, v: int) -> int:
        if v > settings.MAX_APPLICATIONS_PER_DAY_CEILING:
            raise ValueError(f"at most {settings.MAX_APPLICATIONS_PER_DAY_CEILING} a day")
        return v


class ConnectStep(_Step):
    """Opt-in sources whose terms don't allow automation: each needs an explicit, recorded consent."""

    linkedin_consent: bool = False
    internshala_consent: bool = False


STEP_MODELS: dict[int, type[_Step]] = {1: WelcomeStep, 2: ResumeStep, 3: ProfilesStep, 4: TargetsStep, 5: AnswersStep,
                                       6: ApplyStep, 7: ConnectStep}


class OnboardingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int = Field(ge=1, le=LAST_STEP - 1)
    data: dict[str, Any] = Field(default_factory=dict)
    skip: bool = False


class OnboardingComplete(BaseModel):
    start_scan: bool = True
