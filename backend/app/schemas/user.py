from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    full_name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def normalize_url(value: str | None, host: str | None = None) -> str | None:
    """A profile link: http(s) only (``https://`` added when missing); ``host`` restricts the site, e.g. github.com."""
    value = (value or "").strip()
    if not value:
        return None
    if len(value) > 500:
        raise ValueError("link is too long (max 500 characters)")
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    parts = urlsplit(value)
    netloc = parts.hostname or ""
    if parts.scheme not in ("http", "https") or "." not in netloc or any(c.isspace() for c in value):
        raise ValueError("enter a valid link, e.g. https://example.com/you")
    if host and not (netloc == host or netloc.endswith("." + host)):
        raise ValueError(f"enter a {host} link")
    return value


class ProfileLink(BaseModel):
    """Any other profile, e.g. LeetCode, Codeforces, Kaggle or a blog."""

    model_config = ConfigDict(str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=40)
    url: str

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        url = normalize_url(v)
        if url is None:
            raise ValueError("enter a link")
        return url


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=255)
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    profile_links: list[ProfileLink] | None = Field(default=None, max_length=10)

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str | None) -> str:
        if v is None or not v.strip():
            raise ValueError("your name can't be empty")
        return v.strip()

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


class PasswordChange(BaseModel):
    current_password: str | None = None
    new_password: str = Field(min_length=8, max_length=256)


class PreferencesUpdate(BaseModel):
    preferences: dict[str, Any]


class SourceConsentIn(BaseModel):
    enabled: bool
    agree: bool = False  # "I understand the risk": required to turn a site that forbids automation on


class FieldMappingIn(BaseModel):
    field_name: str = Field(min_length=1, max_length=255)
    field_value: str
    field_type: str | None = "text"


class FieldMappingsUpdate(BaseModel):
    mappings: list[FieldMappingIn]


class ATSCredentialsUpdate(BaseModel):
    credentials: dict[str, str]


class LinkedInCookieIn(BaseModel):
    li_at: str = Field(min_length=10, max_length=4000)
    profile_url: str | None = None


class InternshalaCookieIn(BaseModel):
    """One cookie as Chrome's ``chrome.cookies`` API reports it (field names kept as Chrome spells them)."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=256)
    value: str = Field(default="", max_length=4096)
    domain: str = Field(min_length=1, max_length=255)
    path: str = Field(default="/", max_length=1024)
    secure: bool = False
    http_only: bool = Field(default=False, alias="httpOnly")
    same_site: str | None = Field(default=None, alias="sameSite", max_length=32)
    expiration_date: float | None = Field(default=None, alias="expirationDate")
    host_only: bool | None = Field(default=None, alias="hostOnly")


class InternshalaSessionIn(BaseModel):
    cookies: list[InternshalaCookieIn] = Field(min_length=1, max_length=60)
    reason: str = Field(default="manual", max_length=20)  # manual | scheduled | cookie-changed (extension)
    user_agent: str | None = Field(default=None, max_length=512)  # the browser the login belongs to


class DeleteAccountRequest(BaseModel):
    confirm: str  # must equal "DELETE"
