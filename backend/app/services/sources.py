"""Sites that don't allow automation (docs/HIREFLOW_PLAN.md §5.4).

LinkedIn, Internshala, Indeed and Glassdoor forbid automated access in their terms. They are off by
default, and each is turned on only after you've seen its warning and agreed: the agreement is stored
in ``user.consents``. A scan skips any of them you haven't agreed to, and the public demo never uses them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.models.user import User

GATED_SOURCES: dict[str, dict[str, Any]] = {
    "linkedin": {
        "name": "LinkedIn",
        "warning": "LinkedIn's terms don't allow automated access. Searching and applying with your session may get "
                   "your account restricted.",
        # An explicit OK you gave before this switch existed: syncing your LinkedIn session from the extension
        "earlier_consents": ("linkedin",),
    },
    "internshala": {
        "name": "Internshala",
        "warning": "Internshala's terms don't allow automated access. The bot searches and applies with your own "
                   "login, at most your daily limit.",
        "earlier_consents": ("internshala", "internshala_bot"),
    },
    "indeed": {
        "name": "Indeed",
        "warning": "Indeed's terms don't allow automated access. Searches may be blocked, and your IP or account "
                   "flagged.",
        "earlier_consents": (),
    },
    "glassdoor": {
        "name": "Glassdoor",
        "warning": "Glassdoor's terms don't allow automated access. Searches may be blocked, and your IP or account "
                   "flagged.",
        "earlier_consents": (),
    },
}


class ConsentRequired(ValueError):
    """A consent-gated source was turned on without its consent."""


def consent_key(platform: str) -> str:
    return f"{platform}_automation"


def has_consent(user: User, platform: str) -> bool:
    if platform not in GATED_SOURCES:
        return True
    if settings.DEMO_MODE:
        return False  # the public demo never uses them
    consents = user.consents or {}
    keys = (consent_key(platform), *GATED_SOURCES[platform]["earlier_consents"])
    return any(consents.get(k) for k in keys)


def missing_consent(user: User, platforms: list[str]) -> list[str]:
    """The gated sources in ``platforms`` you haven't agreed to."""
    return [p for p in platforms if p in GATED_SOURCES and not has_consent(user, p)]


def allowed(user: User, platforms: list[str]) -> list[str]:
    return [p for p in platforms if has_consent(user, p)]


def require_consent(user: User, platforms: list[str]) -> None:
    """For a settings change: refuse to turn on a gated source you haven't agreed to."""
    missing = missing_consent(user, platforms)
    if missing:
        names = ", ".join(GATED_SOURCES[p]["name"] for p in missing)
        if settings.DEMO_MODE:
            raise ConsentRequired(f"{names}: not available in the demo")
        raise ConsentRequired(f"{names} needs your OK to its terms first: turn it on in Settings › Job sources, "
                              "where the risks are explained")


def give(user: User, platform: str) -> None:
    consents = dict(user.consents or {})
    consents[consent_key(platform)] = datetime.now(UTC).isoformat()
    user.consents = consents


def withdraw(user: User, platform: str) -> None:
    consents = dict(user.consents or {})
    for key in (consent_key(platform), *GATED_SOURCES[platform]["earlier_consents"]):
        consents.pop(key, None)
    user.consents = consents


def describe(user: User) -> list[dict[str, Any]]:
    """For Settings › Job sources: each gated source, its warning, and whether it's agreed to and on."""
    platforms = user.prefs.get("platforms") or []
    return [{"platform": p, "name": info["name"], "warning": info["warning"], "consented": has_consent(user, p),
             "enabled": p in platforms and has_consent(user, p), "available": not settings.DEMO_MODE}
            for p, info in GATED_SOURCES.items()]


def keep_consented(user: User, before: list[str], after: list[str]) -> list[str]:
    """``after`` without the gated sources it would newly turn on without your OK (e.g. from a preset)."""
    return [p for p in after if p in before or has_consent(user, p)]
