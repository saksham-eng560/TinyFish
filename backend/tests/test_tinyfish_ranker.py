"""Preference-only ranker for TinyFish listings."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.enums import ATSPlatform, ExperienceLevel
from app.scrapers.base import ScrapedJob, SearchQuery
from app.services.job_matcher import filter_reasons
from app.services.tinyfish_ranker import (
    WEIGHTS,
    Ranked,
    attach_match,
    detect_sponsorship,
    rank_jobs,
    score_job,
)

TODAY = date(2026, 10, 14)


def job(title: str = "Backend Engineer", *, location: str | None = "Bengaluru, India", description: str = "", posted: date | None = TODAY,
        level: ExperienceLevel | None = ExperienceLevel.ENTRY, remote: bool = False, raw: dict[str, Any] | None = None) -> ScrapedJob:
    return ScrapedJob(company_name="Acme", role_title=title, description=description, source_url=f"https://acme.com/jobs/{abs(hash(title)) % 999}",
                      source_platform=ATSPlatform.CUSTOM, location=location, posted_date=posted, experience_level=level, is_remote=remote, raw=raw)


def query(**kw: Any) -> SearchQuery:
    base: dict[str, Any] = {"keywords": ["Backend Engineer"], "locations": ["Bengaluru"], "seniority": ["entry"], "skills": ["python", "fastapi"]}
    return SearchQuery(**{**base, **kw})


def points(ranked: Ranked) -> dict[str, int]:
    return {r.factor: r.points for r in ranked.reasons}


def test_weights_sum_to_100() -> None:
    assert sum(WEIGHTS.values()) == 100


def test_perfect_job_scores_100() -> None:
    j = job(description="Python and FastAPI services. We offer visa sponsorship for this role.")
    ranked = score_job(j, query(needs_sponsorship=True), TODAY)
    assert ranked.score == 100
    assert points(ranked) == {"title": 35, "location": 20, "seniority": 15, "keywords": 15, "recency": 10, "visa": 5}
    assert ranked.visa_status == "sponsors" and ranked.visa_evidence and "visa sponsorship" in ranked.visa_evidence
    assert all(r.max_points == WEIGHTS[r.factor] and r.note for r in ranked.reasons)


def test_title_mismatch_caps_at_65() -> None:
    j = job("Marketing Manager", description="python fastapi visa sponsorship available")
    ranked = score_job(j, query(needs_sponsorship=True), TODAY)
    assert points(ranked)["title"] == 0
    assert ranked.score <= 65


@pytest.mark.parametrize(("title", "expected"), [
    ("Senior Backend Engineer II", 35),          # substring
    ("Backend Developer", 28),                    # synonym: 2 of 2 tokens
    ("Backend Engineer", 35),
    ("Frontend Engineer", 14),                    # 1 of 2 tokens
    ("Backend Systems Architect", 14),
    ("Chief Marketing Officer", 0),
])
def test_title_factor(title: str, expected: int) -> None:
    assert points(score_job(job(title), query(), TODAY))["title"] == expected


def test_title_token_ratio_boundaries() -> None:
    four = query(keywords=["Senior Backend Software Engineer"])
    assert points(score_job(job("Backend Software Engineer"), four, TODAY))["title"] == 28   # 3 of 4 = 0.75
    assert points(score_job(job("Backend Software Intern"), four, TODAY))["title"] == 14     # 2 of 4 = 0.5
    three = query(keywords=["Senior Backend Engineer"])
    assert points(score_job(job("Backend Engineer"), three, TODAY))["title"] == 14           # 2 of 3 = 0.667 < 0.67
    assert points(score_job(job("Backend Analyst"), three, TODAY))["title"] == 0             # 1 of 3 = 0.333 < 0.34
    assert points(score_job(job("Florist"), three, TODAY))["title"] == 0


def test_title_best_of_several_roles_and_no_roles() -> None:
    q = query(keywords=["Data Scientist", "Backend Engineer"])
    assert points(score_job(job("Backend Engineer Intern"), q, TODAY))["title"] == 35
    ranked = score_job(job("Whatever"), query(keywords=[]), TODAY)
    assert points(ranked)["title"] == 20 and next(r.note for r in ranked.reasons if r.factor == "title") == "no role filter"


@pytest.mark.parametrize(("kw", "job_kw", "expected"), [
    ({}, {}, 20),                                                    # substring match
    ({}, {"location": "Pune, India"}, 2),                            # mismatch
    ({}, {"location": None}, 10),                                    # unknown location
    ({"locations": []}, {"location": "Anywhere else"}, 14),          # no locations given
    ({"remote": True}, {"remote": True, "location": "Berlin"}, 20),  # remote friendly
    ({"locations": ["Remote"]}, {"location": "Remote - US"}, 20),
    ({"remote": True}, {"remote": False, "location": "Pune, India"}, 2),
    ({"locations": ["Delhi, India", "Bengaluru, India"]}, {"location": "Bengaluru, Karnataka"}, 20),
])
def test_location_factor(kw: dict[str, Any], job_kw: dict[str, Any], expected: int) -> None:
    assert points(score_job(job(**job_kw), query(**kw), TODAY))["location"] == expected


@pytest.mark.parametrize(("seniority", "level", "expected"), [
    (["entry"], ExperienceLevel.ENTRY, 15),
    (["internship", "entry"], ExperienceLevel.ENTRY, 15),
    (["entry"], ExperienceLevel.INTERNSHIP, 8),   # adjacent
    (["entry"], ExperienceLevel.MID, 8),
    (["senior"], ExperienceLevel.LEAD, 8),
    (["entry"], ExperienceLevel.SENIOR, 0),
    (["internship"], ExperienceLevel.LEAD, 0),
    ([], ExperienceLevel.EXECUTIVE, 10),          # nothing requested
])
def test_seniority_factor(seniority: list[str], level: ExperienceLevel, expected: int) -> None:
    assert points(score_job(job(level=level), query(seniority=seniority), TODAY))["seniority"] == expected


def test_seniority_inferred_from_title() -> None:
    j = job("Senior Backend Engineer", level=None)
    assert points(score_job(j, query(seniority=["senior"]), TODAY))["seniority"] == 15


@pytest.mark.parametrize(("description", "skills", "expected"), [
    ("python and fastapi", ["python", "fastapi"], 15),
    ("python only", ["python", "fastapi"], 8),         # round(7.5) = 8
    ("java", ["python", "fastapi"], 0),
    ("anything", [], 8),                                # nothing given
    ("we use Go daily", ["go"], 15),
    ("we use django", ["go"], 0),                       # whole words only
    ("c++ and c#", ["c++", "c#"], 15),
])
def test_keyword_factor(description: str, skills: list[str], expected: int) -> None:
    assert points(score_job(job(description=description), query(skills=skills), TODAY))["keywords"] == expected


def test_keywords_only_look_at_title_and_first_4000_chars() -> None:
    late = "x " * 2500 + "python"
    assert points(score_job(job(description=late), query(skills=["python"]), TODAY))["keywords"] == 0
    assert points(score_job(job("Python Engineer"), query(skills=["python"]), TODAY))["keywords"] == 15


@pytest.mark.parametrize(("days", "expected"), [(-1, 10), (0, 10), (3, 10), (4, 8), (7, 8), (8, 6), (14, 6), (15, 4), (30, 4), (31, 2),
                                                (90, 2), (91, 0), (400, 0)])
def test_recency_factor(days: int, expected: int) -> None:
    assert points(score_job(job(posted=TODAY - timedelta(days=days)), query(), TODAY))["recency"] == expected


def test_recency_unknown() -> None:
    assert points(score_job(job(posted=None), query(), TODAY))["recency"] == 4


@pytest.mark.parametrize(("text", "status"), [
    ("We do not sponsor visas for this role.", "no_sponsorship"),
    ("Unfortunately we cannot sponsor work permits", "no_sponsorship"),
    ("Candidates must be a U.S. citizen.", "no_sponsorship"),
    ("There is no visa sponsorship available.", "no_sponsorship"),
    ("Visa sponsorship is not available", "no_sponsorship"),
    ("We offer visa sponsorship and relocation.", "sponsors"),
    ("Visa sponsorship available for the right candidate", "sponsors"),
    ("We will sponsor H-1B visas", "sponsors"),
    ("Build great things with Python.", "unknown"),
    ("", "unknown"),
    ("We sponsor visas but do not sponsor remote hires", "no_sponsorship"),  # a refusal anywhere wins
])
def test_detect_sponsorship(text: str, status: str) -> None:
    got, evidence = detect_sponsorship(text)
    assert got == status
    assert (evidence is None) == (status == "unknown")


def test_visa_factor() -> None:
    sponsors = job(description="We offer visa sponsorship.")
    refuses = job(description="We do not sponsor visas.")
    silent = job(description="Nothing about it.")
    need = query(needs_sponsorship=True)
    assert [points(score_job(j, need, TODAY))["visa"] for j in (sponsors, silent, refuses)] == [5, 2, 0]
    assert [points(score_job(j, query(), TODAY))["visa"] for j in (sponsors, silent, refuses)] == [5, 5, 5]


def test_visa_reads_raw_sponsorship_from_agent() -> None:
    j = job(raw={"sponsorship": "No visa sponsorship"})
    assert score_job(j, query(needs_sponsorship=True), TODAY).visa_status == "no_sponsorship"


def test_rank_jobs_drops_refusals_only_when_needed() -> None:
    jobs = [job("Backend Engineer", description="We do not sponsor visas."), job("Backend Engineer Intern", description="Python")]
    assert len(rank_jobs(jobs, query(needs_sponsorship=True), TODAY)) == 1
    assert len(rank_jobs(jobs, query(needs_sponsorship=False), TODAY)) == 2
    assert len(rank_jobs(jobs, query(needs_sponsorship=True), TODAY, drop_no_sponsorship=False)) == 2


def test_rank_jobs_orders_by_score_then_recency_then_title() -> None:
    best = job("Backend Engineer", description="python fastapi")
    older = job("Backend Engineer II", description="python fastapi", posted=TODAY - timedelta(days=5))
    mid = job("Backend Developer", description="python")
    worst = job("Chef", description="cooking", location="Paris")
    tie_b = job("Backend Engineer B", description="python fastapi", posted=TODAY - timedelta(days=5))
    tie_a = job("Backend Engineer A", description="python fastapi", posted=TODAY - timedelta(days=5))
    ranked = rank_jobs([worst, tie_b, mid, older, tie_a, best], query(), TODAY)
    assert [r.job.role_title for r in ranked] == ["Backend Engineer", "Backend Engineer A", "Backend Engineer B",
                                                  "Backend Engineer II", "Backend Developer", "Chef"]
    scores = [r.score for r in ranked]
    assert scores == sorted(scores, reverse=True)
    again = rank_jobs(list(reversed([worst, tie_b, mid, older, tie_a, best])), query(), TODAY)
    assert [r.job.role_title for r in again] == [r.job.role_title for r in ranked]  # input order does not matter
    assert rank_jobs([], query(), TODAY) == []


def test_attach_match_writes_raw() -> None:
    j = job(description="We offer visa sponsorship.", raw={"board_token": "acme"})
    ranked = score_job(j, query(needs_sponsorship=True), TODAY)
    attach_match(ranked)
    assert j.raw is not None
    assert j.raw["board_token"] == "acme"
    assert j.raw["match"]["score"] == ranked.score
    assert j.raw["match"]["reasons"][0] == {"factor": "title", "points": 35, "max": 35, "note": ranked.reasons[0].note}
    assert [r["factor"] for r in j.raw["match"]["reasons"]] == ["title", "location", "seniority", "keywords", "recency", "visa"]
    assert "visa sponsorship" in j.raw["sponsorship"]


def test_attach_match_without_evidence_leaves_sponsorship_alone() -> None:
    j = job(description="Nothing.")
    attach_match(score_job(j, query(), TODAY))
    assert j.raw is not None and "sponsorship" not in j.raw and "match" in j.raw


def test_attach_match_sponsorship_is_read_by_filter_reasons() -> None:
    """A refusal found by the ranker must block the job in job_matcher.filter_reasons, even when its wording is new to it."""
    j = job(description="We do not sponsor visas for this role.")
    attach_match(score_job(j, query(needs_sponsorship=True), TODAY))
    assert j.raw is not None and j.raw["sponsorship"].lower().startswith("no visa sponsorship")
    stored = SimpleNamespace(company_name=j.company_name, role_title=j.role_title, description="", raw_data=j.raw, job_type=None,
                             deadline_date=None, company_verdict=None, company_check=None, location=j.location, source_platform=j.source_platform)
    prefs = {"exclude_no_sponsorship": True, "internships_only": False, "job_types": [], "skip_suspicious_companies": False,
             "internship_season": ""}
    hard, _ = filter_reasons(stored, prefs)  # type: ignore[arg-type]
    assert any("sponsor" in reason for reason in hard)
    hard_off, _ = filter_reasons(stored, {**prefs, "exclude_no_sponsorship": False})  # type: ignore[arg-type]
    assert not any("sponsor" in reason for reason in hard_off)
