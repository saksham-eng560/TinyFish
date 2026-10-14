"""Request models of the TinyFish Finder API (POST /tinyfish/search, POST /tinyfish/save)."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.models.enums import ExperienceLevel
from app.scrapers.tinyfish_parse import is_gated_url

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
SENIORITY = {level.value for level in ExperienceLevel}


class FinderRequest(BaseModel):
    roles: list[Text] = Field(default_factory=list, max_length=10)
    locations: list[Text] = Field(default_factory=list, max_length=10)
    keywords: list[Text] = Field(default_factory=list, max_length=15)
    seniority: list[Text] = Field(default_factory=list, max_length=6)
    visa_sponsorship: bool = False
    remote: bool = False
    companies: list[Text] = Field(default_factory=list, max_length=10)
    career_pages: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=5)
    posted_within_days: int = Field(default=30, ge=1, le=365)
    limit: int = Field(default=30, ge=5, le=100)
    min_score: int = Field(default=20, ge=0, le=100)
    use_agent: bool = True
    agent_runs: int = Field(default=2, ge=0, le=5)

    @field_validator("seniority")
    @classmethod
    def _seniority(cls, values: list[str]) -> list[str]:
        bad = [v for v in values if v.lower() not in SENIORITY]
        if bad:
            raise ValueError(f"unknown seniority {bad}; use one of {sorted(SENIORITY)}")
        return [v.lower() for v in values]

    @field_validator("career_pages")
    @classmethod
    def _pages(cls, values: list[str]) -> list[str]:
        for url in values:
            parts = urlparse(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError(f"career page must be an http(s) URL: {url[:80]}")
            if is_gated_url(url):
                raise ValueError("LinkedIn, Indeed, Glassdoor and Internshala pages are never sent to TinyFish")
        return values

    @model_validator(mode="after")
    def _something_to_search(self) -> FinderRequest:
        if not (self.roles or self.keywords or self.companies or self.career_pages):
            raise ValueError("give at least one role, keyword, company or careers page")
        return self


class SaveRequest(BaseModel):
    search_id: str = Field(min_length=1, max_length=64)
    listing_ids: list[Annotated[str, StringConstraints(min_length=1, max_length=64)]] = Field(min_length=1, max_length=100)
