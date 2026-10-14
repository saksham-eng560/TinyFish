"""The agent "brain": discovery -> matching -> preparation -> staging -> (approval) -> submission.

Behavioural rules implemented here (PLAN.md §8):
 1. Never process a job that already has an application for this user.
 2. Never apply to the same company + role twice.
 3. Respect max_applications_per_day and per-platform limits (rate_limiter).
 4. Unexpected form fields -> flag for manual review.
 5. Screenshot at every critical step (staged form, confirmation, failures).
 6. Every run is logged to ``agent_runs``.
 7. Low-confidence answers -> needs_user_review.
 8. Prioritise by match_score DESC, deadline ASC.
 9. Salary questions use the bottom of the user's range (question_answerer).
10. A failed form is retried once before alerting the user.
And DIRECTIVE 2: nothing is ever submitted without explicit user approval.

Swipe Review (``review_mode="swipe"``, the default): scans never skip a job for a low score.
Every job that passes your hard filters waits in Swipe Review; keeping one is your approval to
prepare it and, with ``auto_submit_kept``, to submit it once the form is filled with every required
answer. Anything the agent is unsure about still stops in "Needs approval".
"""

from __future__ import annotations

import dataclasses
import logging
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, inspect, or_, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import checkpoint
from app.core.storage import get_storage, user_prefix
from app.models.agent_run import AgentRun
from app.models.application import Application, ApplicationStatusHistory
from app.models.enums import STATUS_RANK, ApplicationStatus, ATSPlatform
from app.models.job import Job
from app.models.resume import Resume
from app.models.user import User, UserFieldMapping
from app.schemas.resume_content import ResumeContent
from app.scrapers import SCRAPERS, ScrapedJob, ScraperError, SearchQuery, detect_ats_platform, fetch_job_from_url
from app.services import guardrails, llm_usage, sources
from app.services.application_service import set_status
from app.services.company_verifier import SUSPICIOUS, UNVERIFIED, CompanyCheck, check_job, is_trusted, verify_with_llm
from app.services.cover_letter import generate_cover_letter
from app.services.embeddings import cosine_similarity, embed_text, embed_texts
from app.services.intern_level import drop_ineligible, student, with_resume
from app.services.job_matcher import evaluate_match, filter_reasons, job_text, prefilter, priority_key
from app.services.llm import get_llm, llm_budget
from app.services.location_focus import balance_by_location, get_focus, get_season, location_tier, season_status
from app.services.notifier import notify, push_update
from app.services.pdf_generator import render_resume_pdf
from app.services.question_answerer import answer_questions, learnable_key, mappings_dict, profile_links
from app.services.rate_limiter import rate_limiter
from app.services.resume_tailor import light_tailor, tailor_resume
from app.services.role_focus import drop_off_focus
from app.services.scan_progress import ScanCancelled, ScanProgress
from app.services.source_mix import cap_internshala
from app.services.text_utils import dedupe_key, extract_skills

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- run logging
class RunLog:
    def __init__(self, db: Session, user: User, run_type: str, trigger: str = "user", existing: AgentRun | None = None) -> None:
        self.db = db
        if existing is not None:
            self.run = existing
            self.run.status = "running"
        else:
            self.run = AgentRun(user_id=user.id, run_type=run_type, trigger=trigger, status="running", log=[])
            db.add(self.run)
        db.flush()
        self._t0 = time.monotonic()

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        entry = {"ts": datetime.now(UTC).isoformat(), "level": level, "message": message}
        if data:
            entry["data"] = data
        self.run.log = [*(self.run.log or []), entry][-500:]
        if level == "error":
            self.run.errors_count = (self.run.errors_count or 0) + 1
        getattr(logger, "warning" if level == "error" else "info")("[run %s] %s", self.run.id, message)

    def finish(self, status: str = "completed") -> AgentRun:
        self.run.status = status
        self.run.completed_at = datetime.now(UTC)
        self.run.duration_seconds = int(time.monotonic() - self._t0)
        self.db.flush()
        push_update(str(self.run.user_id), "agent_run_updated", {"id": str(self.run.id), "status": status})
        return self.run


# --------------------------------------------------------------------------- helpers
def get_master_resume(db: Session, user: User) -> Resume | None:
    return db.scalar(
        select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True), Resume.is_active.is_(True))
        .order_by(Resume.updated_at.desc())
    )


def resume_embedding(db: Session, resume: Resume) -> list[float]:
    if not resume.skills_embedding:
        rc = ResumeContent.model_validate(resume.parsed_content)
        resume.skills_embedding = embed_text(f"{rc.skills_text()}\n{rc.full_text()}")
        db.flush()
    return resume.skills_embedding


def upsert_job(db: Session, scraped: ScrapedJob) -> tuple[Job, bool]:
    """Insert or refresh a job. Returns (job, created)."""
    job = db.scalar(select(Job).where(Job.source_url == scraped.source_url))
    now = datetime.now(UTC)
    if job is not None:
        job.last_checked = now
        job.is_active = True
        if scraped.description and len(scraped.description) > len(job.description or ""):
            job.description = scraped.description
        if job.company_verdict is None:
            apply_company_check(job)
        return job, False
    key = dedupe_key(scraped.company_name, scraped.role_title, scraped.location)
    duplicate = db.scalar(select(Job).where(Job.dedupe_key == key, Job.is_active.is_(True)))
    if duplicate is not None:
        duplicate.last_checked = now
        return duplicate, False
    job = Job(
        company_name=scraped.company_name,
        company_logo_url=scraped.company_logo_url,
        company_domain=scraped.company_domain,
        role_title=scraped.role_title,
        description=scraped.description or scraped.role_title,
        requirements=scraped.requirements,
        job_type=scraped.job_type,
        experience_level=scraped.experience_level,
        location=scraped.location,
        is_remote=scraped.is_remote,
        salary_min=scraped.salary_min,
        salary_max=scraped.salary_max,
        salary_currency=scraped.salary_currency or "USD",
        source_url=scraped.source_url,
        source_platform=scraped.source_platform,
        application_url=scraped.application_url or scraped.source_url,
        external_id=scraped.external_id,
        easy_apply=scraped.easy_apply,
        dedupe_key=key,
        extracted_skills=extract_skills(f"{scraped.role_title}\n{scraped.description}"),
        raw_data=scraped.raw,
        posted_date=scraped.posted_date,
        deadline_date=scraped.deadline_date,
    )
    apply_company_check(job)
    db.add(job)
    db.flush()
    return job, True


# --------------------------------------------------------------------------- the company check
COMPANY_AI_CHECKS_PER_SCAN = 8  # distinct unknown companies the AI looks at per scan (local models are slow)


def apply_company_check(job: Job, check: CompanyCheck | None = None) -> None:
    """Store the company-check agent's verdict on the job (services/company_verifier.py)."""
    check = check or check_job(job)
    job.company_verdict = check.verdict
    job.company_tier = check.tier or (job.raw_data or {}).get("company_tier")
    job.company_check = check.as_dict()


def backfill_company_checks(db: Session, limit: int = 2000) -> int:
    """Postings saved before the company check existed get their verdict (the rules: instant)."""
    jobs = db.scalars(select(Job).where(Job.company_verdict.is_(None)).limit(limit)).all()
    for job in jobs:
        apply_company_check(job)
    if jobs:
        db.flush()  # sessions don't autoflush: later queries in this request must see the verdicts
    return len(jobs)


def skip_suspicious_waiting(db: Session, user: User) -> int:
    """Jobs waiting in Swipe Review whose company looks like a scam are skipped (unless you marked it legit)."""
    if not user.prefs.get("skip_suspicious_companies", True):
        return 0
    waiting = db.scalars(
        select(Application).join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user.id, Job.company_verdict == SUSPICIOUS,
               Application.status.in_((ApplicationStatus.DISCOVERED, ApplicationStatus.MATCHED)),
               Application.review_decision.is_(None))).all()
    skipped = 0
    for app in waiting:
        if is_trusted(app.job, user.prefs):
            continue
        reasons = [r.removeprefix("⚠ ") for r in (app.job.company_check or {}).get("reasons") or [] if r.startswith("⚠")]
        app.match_reasoning = "Possible fraud: " + ("; ".join(reasons[:2]) or "the company check flagged this posting")
        set_status(db, app, ApplicationStatus.SKIPPED, "agent", app.match_reasoning)
        skipped += 1
    return skipped


def skip_ineligible_waiting(db: Session, user: User) -> int:
    """Jobs waiting in Swipe Review that your filters now rule out (not an internship for you, a Java role when you
    skip Java, outside your tech focus, a company you avoid...) are skipped, with the reason."""
    prefs = user.prefs
    waiting = db.scalars(
        select(Application).join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user.id,
               Application.status.in_((ApplicationStatus.DISCOVERED, ApplicationStatus.MATCHED)),
               Application.review_decision.is_(None))).all()
    if not waiting:
        return 0
    master = get_master_resume(db, user)
    prefs = with_resume(prefs, master.parsed_content if master else None)
    skipped = 0
    for app in waiting:
        reasons = filter_reasons(app.job, prefs)[0]  # the hard ones: the same a scan applies
        if reasons:
            app.match_reasoning = reasons[0]
            set_status(db, app, ApplicationStatus.SKIPPED, "agent", reasons[0])
            skipped += 1
    return skipped


def student_label(prefs: dict[str, Any]) -> str:
    year = student(prefs).year
    return f"a {('1st', '2nd', '3rd', '4th', '5th')[year - 1]}-year student" if year else "a student"


def verify_companies(db: Session, jobs: list[Job], run: RunLog | None = None, progress: ScanProgress | None = None) -> int:
    """The company-check agent's second look: the AI on companies the rules couldn't verify (cached per
    company: a verdict the AI gave in the last 30 days is reused)."""
    if not get_llm().available:
        return 0
    seen: dict[str, CompanyCheck | None] = {}
    asked = 0
    since = datetime.now(UTC) - timedelta(days=30)
    for job in jobs:
        if job.company_verdict != UNVERIFIED or (job.company_check or {}).get("method") != "rules":
            continue
        key = job.company_name.strip().lower()
        if key not in seen:
            earlier = db.scalars(select(Job).where(func.lower(Job.company_name) == key, Job.id != job.id,
                                                   Job.discovered_at >= since).limit(20)).all()
            done = next((j for j in earlier if (j.company_check or {}).get("method") == "ai"), None)
            if done is not None:
                seen[key] = CompanyCheck(done.company_verdict or UNVERIFIED, int(done.company_check.get("score") or 50),
                                         list(done.company_check.get("reasons") or []), done.company_tier, "ai")
            elif asked < COMPANY_AI_CHECKS_PER_SCAN:
                checkpoint(db)  # no write lock held while the AI thinks
                seen[key] = verify_with_llm(job, check_job(job))
                asked += 1
                if progress is not None:
                    progress.tick()
            else:
                seen[key] = None
        if seen[key] is not None:
            apply_company_check(job, seen[key])
    if run and asked:
        run.log(f"Company check: the AI looked at {asked} unknown compan{'y' if asked == 1 else 'ies'}")
    return asked


def _consent_needed(db: Session, user: User, skipped: list[str], run: RunLog) -> None:
    """Say why a source was left out; the first time, also tell you how to turn it on."""
    names = ", ".join(sources.GATED_SOURCES[p]["name"] for p in skipped)
    if settings.DEMO_MODE:
        run.log(f"Skipped {names}: not available in the demo")
        return
    run.log(f"Skipped {names}: their terms forbid automation, so they stay off until you agree to the risk "
            "in Settings › Job sources")
    from app.models.user import Notification

    asked = db.scalar(select(Notification.id).where(Notification.user_id == user.id,
                                                    Notification.event_type == "source_needs_consent").limit(1))
    if asked is None:
        notify(db, user, "source_needs_consent", f"{names}: now opt-in",
               f"{names} don't allow automated access, so HireFlow now uses them only after you agree to the "
               "risk. Turn them back on in Settings › Job sources if you want them.",
               link="/dashboard/settings?tab=sources")


def scan_platforms(prefs: dict[str, Any], requested: list[str] | None = None) -> list[str]:
    """The sources a scan searches: the ones you asked for, or your saved ones plus the top companies."""
    if settings.DEMO_MODE:  # the public demo searches only the bundled demo careers site
        return ["demo"]
    chosen = list(requested or prefs.get("platforms") or list(SCRAPERS))
    if not requested and prefs.get("scan_top_companies", True) and "top_companies" in SCRAPERS \
            and "top_companies" not in chosen:
        chosen.insert(0, "top_companies")
    return chosen


def suspicious_message(job: Job) -> str:
    flags = [r.removeprefix("⚠ ") for r in (job.company_check or {}).get("reasons") or [] if r.startswith("⚠")]
    why = f" ({'; '.join(flags[:2])})" if flags else ""
    return (f"⚠ {job.company_name} looks like a possible fraud{why}, so nothing is sent to it. If you're sure "
            "it's real, mark it legit first.")


def company_hold_reason(job: Job) -> str:
    reasons = [r.removeprefix("⚠ ") for r in (job.company_check or {}).get("reasons") or []]
    why = f" ({reasons[0]})" if reasons else ""
    return (f"Not sent automatically: {job.company_name} isn't a verified company{why}. Check it, then submit it "
            "yourself — or mark it legit so the agent applies to it automatically.")


def embed_jobs(db: Session, jobs: list[Job]) -> None:
    missing = [j for j in jobs if not j.description_embedding]
    for start in range(0, len(missing), 64):
        batch = missing[start : start + 64]
        vectors = embed_texts([job_text(j)[:8000] for j in batch])
        for job, vec in zip(batch, vectors, strict=True):
            job.description_embedding = vec
    db.flush()


def already_applied_elsewhere(db: Session, user: User, job: Job) -> bool:
    """Rule #2: same company + role already in the pipeline for this user."""
    rows = db.execute(
        select(Application.status, Job.id)
        .join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user.id, Job.dedupe_key == job.dedupe_key, Job.id != job.id)
    ).all()
    return any(STATUS_RANK.get(status, 0) >= STATUS_RANK[ApplicationStatus.PREPARING] for status, _ in rows)


def application_platform(job: Job) -> ATSPlatform:
    url = job.application_url or job.source_url
    if job.source_platform == ATSPlatform.LINKEDIN:
        external = (job.raw_data or {}).get("external_apply_url")
        if external:
            return detect_ats_platform(external)
        return ATSPlatform.LINKEDIN if job.easy_apply else detect_ats_platform(url)
    platform = detect_ats_platform(url)
    if platform in (ATSPlatform.INDEED, ATSPlatform.GLASSDOOR, ATSPlatform.WELLFOUND):
        return ATSPlatform.CUSTOM
    return platform


# --------------------------------------------------------------------------- discovery
def discover_jobs(query: SearchQuery, platforms: list[str], run: RunLog | None = None,
                  progress: ScanProgress | None = None, tick: Callable[[], None] | None = None) -> list[ScrapedJob]:
    """Search every platform at once (up to SCAN_SOURCE_CONCURRENCY). Sources are asked to wrap up
    with what they have at 80% of SCAN_SOURCE_TIMEOUT_SECONDS; one still running at the limit is left
    out of this scan so a single slow site never holds up the rest."""
    results: list[ScrapedJob] = []
    usable = [p for p in platforms if p in SCRAPERS]
    if not usable:
        return []

    started = time.monotonic()
    # Sources wrap up at 80% of the limit and return what they have; the hard limit is the backstop.
    query = dataclasses.replace(query, deadline=started + settings.SCAN_SOURCE_TIMEOUT_SECONDS * 0.8)

    def search(platform: str) -> list[ScrapedJob]:
        if progress is None:
            return SCRAPERS[platform]().search(query)
        progress.source_started(platform)
        scoped = dataclasses.replace(query, progress=lambda done, total: progress.source_step(platform, done, total))
        return SCRAPERS[platform]().search(scoped)

    pool = ThreadPoolExecutor(max_workers=max(1, min(settings.SCAN_SOURCE_CONCURRENCY, len(usable))),
                              thread_name_prefix="scan-source")
    futures = {pool.submit(search, p): p for p in usable}
    pending = set(futures)
    deadline = started + settings.SCAN_SOURCE_TIMEOUT_SECONDS
    try:
        while pending:
            done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            for future in done:
                platform = futures[future]
                try:
                    found = future.result()
                    results.extend(found)
                    if run:
                        run.log(f"{platform}: found {len(found)} jobs")
                    if progress:
                        progress.source_finished(platform, len(found))
                except ScraperError as exc:
                    if run:
                        run.log(f"{platform}: {exc}", level="error")
                    if progress:
                        progress.source_finished(platform, 0, "failed", str(exc))
                except Exception as exc:
                    logger.exception("Scraper %s crashed", platform)
                    if run:
                        run.log(f"{platform}: unexpected error {type(exc).__name__}: {exc}", level="error")
                    if progress:
                        progress.source_finished(platform, 0, "failed", f"{type(exc).__name__}: {exc}")
            if progress:
                progress.found = len({j.source_url for j in results})
            if tick:
                tick()
            if pending and time.monotonic() > deadline:
                for future in pending:
                    platform = futures[future]
                    if run:
                        run.log(f"{platform}: still searching after {settings.SCAN_SOURCE_TIMEOUT_SECONDS}s, "
                                "left out of this scan", level="warning")
                    if progress:
                        progress.source_finished(platform, 0, "timeout")
                break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # a timed-out source finishes on its own
    # Deduplicate by URL
    unique: dict[str, ScrapedJob] = {}
    for job in results:
        unique.setdefault(job.source_url, job)
    return list(unique.values())


def user_source_urls(db: Session, user: User, contains: str) -> frozenset[str]:
    """Source URLs (containing ``contains``) of the postings you already have."""
    rows = db.scalars(select(Job.source_url).join(Application, Application.job_id == Job.id)
                      .where(Application.user_id == user.id, Job.source_url.contains(contains))).all()
    return frozenset(rows)


def known_source_urls(db: Session, days: int = 30) -> frozenset[str]:
    """Postings from sites with slow detail pages that are already saved (with a full description)."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.scalars(select(Job.source_url).where(
        Job.is_active.is_(True), Job.last_checked >= since, func.length(Job.description) >= 200,
        Job.source_platform.in_([ATSPlatform.LINKEDIN, ATSPlatform.INDEED, ATSPlatform.GLASSDOOR]),
    )).all()
    return frozenset(rows)


def ensure_application(db: Session, user: User, job: Job) -> tuple[Application, bool]:
    app = db.scalar(select(Application).where(Application.user_id == user.id, Application.job_id == job.id))
    if app is not None:
        return app, False
    app = Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.DISCOVERED,
                      ats_platform=application_platform(job))
    db.add(app)
    db.flush()
    return app, True


def is_swipe_mode(prefs: dict[str, Any]) -> bool:
    return (prefs.get("review_mode") or "swipe") == "swipe"


def _precheck(db: Session, user: User, app: Application, master: Resume | None) -> tuple[bool, list[str]]:
    """Cheap checks before scoring. Returns (needs_scoring, heads_up)."""
    job = app.job
    prefs = with_resume(user.prefs, master.parsed_content if master else None)  # your graduation year
    swipe = is_swipe_mode(prefs)
    keep, reason = prefilter(job, prefs, strict=not swipe)
    if not keep:
        app.match_score = 0
        app.match_reasoning = reason
        set_status(db, app, ApplicationStatus.SKIPPED, "agent", reason)
        return False, []
    if already_applied_elsewhere(db, user, job):
        app.match_reasoning = "Already applied to the same role at this company"
        set_status(db, app, ApplicationStatus.SKIPPED, "agent", app.match_reasoning)
        return False, []
    heads_up = filter_reasons(job, prefs)[1] if swipe else []
    if master is None:
        app.match_reasoning = "Upload a master resume to enable matching"
        if heads_up:
            app.match_details = {"heads_up": heads_up}
        return False, heads_up
    return True, heads_up


def _apply_evaluation(db: Session, user: User, app: Application, evaluation: dict[str, Any], heads_up: list[str],
                      resume_vec: list[float] | None) -> None:
    prefs = user.prefs
    threshold = int(prefs.get("auto_apply_threshold") or 80)
    if resume_vec is not None and app.job.description_embedding:
        app.similarity_score = round(cosine_similarity(resume_vec, app.job.description_embedding), 4)
    app.match_score = evaluation["match_score"]
    app.match_reasoning = evaluation["reasoning"]
    app.match_details = {**evaluation, "heads_up": heads_up}
    if is_swipe_mode(prefs):
        # Never skipped for a low score: you decide in Swipe Review.
        set_status(db, app, ApplicationStatus.MATCHED, "agent", f"Match score {app.match_score} — waiting for your swipe")
    elif evaluation["proceed_with_application"] and evaluation["match_score"] >= threshold:
        set_status(db, app, ApplicationStatus.MATCHED, "agent", f"Match score {app.match_score}")
    else:
        set_status(db, app, ApplicationStatus.SKIPPED, "agent", f"Match score {app.match_score} below {threshold}")


def evaluate_application(
    db: Session, user: User, app: Application, master: Resume | None, use_llm: bool = True, resume_vec: list[float] | None = None
) -> Application:
    needs_scoring, heads_up = _precheck(db, user, app, master)
    if not needs_scoring or master is None:
        return app
    threshold = int(user.prefs.get("auto_apply_threshold") or 80)
    evaluation = evaluate_match(master.parsed_content, app.job, user.prefs, threshold, use_llm=use_llm)
    _apply_evaluation(db, user, app, evaluation, heads_up, resume_vec)
    return app


_SNAPSHOT_SKIP = {"description_embedding"}


def job_snapshot(job: Job) -> Any:
    """A plain, session-free copy of a job, safe to read from another thread."""
    return SimpleNamespace(**{attr.key: getattr(job, attr.key) for attr in inspect(Job).column_attrs
                              if attr.key not in _SNAPSHOT_SKIP})


def _swiped(db: Session, app: Application) -> bool:
    """Re-read the decision: you may have swiped this card while the scan was still working."""
    db.refresh(app, attribute_names=["status", "review_decision"])
    return app.review_decision is not None


def run_scan(db: Session, user: User, trigger: str = "user", platforms: list[str] | None = None,
             auto_prepare: bool = True, run: RunLog | None = None) -> AgentRun:
    run = run or RunLog(db, user, "scan", trigger)
    if guardrails.is_paused(user):
        run.log("Paused: no scan until you resume")
        return run.finish("cancelled")
    prefs = user.prefs
    chosen = scan_platforms(prefs, platforms)
    skipped = sources.missing_consent(user, chosen)  # sites that forbid automation, without your OK
    if skipped:
        chosen = [p for p in chosen if p not in skipped]
        _consent_needed(db, user, skipped, run)
    progress = ScanProgress(db, run.run, [p for p in chosen if p in SCRAPERS])
    try:
        master = get_master_resume(db, user)
        query = SearchQuery.from_preferences(prefs, limit=int(prefs.get("max_jobs_per_source") or settings.MAX_JOBS_PER_SOURCE))
        query = dataclasses.replace(query, known_urls=known_source_urls(db),
                                    allowed_gated=frozenset(p for p in sources.GATED_SOURCES if sources.has_consent(user, p)))
        run.log(f"Scanning {', '.join(chosen)} for {', '.join(query.keywords) or 'all roles'}"
                + (" (internships only)" if query.internships_only else ""))
        progress.set_phase("discovering", f"Searching {len(progress.sources)} job sources at once")
        scraped = discover_jobs(query, chosen, run, progress=progress, tick=progress.tick)
        scraped, not_for_you = drop_ineligible(scraped, with_resume(prefs, master.parsed_content if master else None))
        if not_for_you:
            run.log(f"Interns only: left out {not_for_you} posting{'s' if not_for_you != 1 else ''} that aren't "
                    f"internships for {student_label(prefs)} (full-time roles, PhD / MBA-only, final-year-only...)")
        scraped, off_focus = drop_off_focus(scraped, prefs)
        if off_focus:
            run.log(f"Tech focus: left out {off_focus} posting{'s' if off_focus != 1 else ''} outside "
                    f"{', '.join(prefs.get('focus_skills') or []) or 'your focus'}"
                    + (f" or in {', '.join(prefs.get('avoid_skills'))}" if prefs.get("avoid_skills") else ""))
        if query.focus is not None:
            scraped, dropped = balance_by_location(scraped, query.focus)
            if dropped:
                run.log(f"Location focus: kept ~{query.focus.share}% of postings in {query.focus.country.title()} "
                        f"({dropped} from elsewhere left out this scan)")
        scraped, capped = cap_internshala(scraped, prefs.get("internshala_share"), query.focus,
                                          prefs.get("internshala_per_scan"), user_source_urls(db, user, "internshala.com/"))
        if capped:
            run.log(f"Internshala: kept the best {prefs.get('internshala_per_scan', 10)} new postings at most "
                    f"(and at most {prefs.get('internshala_share', 25)}% of the scan); {capped} more left out")
        progress.found = len(scraped)
        progress.to_save = len(scraped)
        progress.set_phase("saving", f"Saving {len(scraped)} postings")
        new_apps: list[Application] = []
        jobs: list[Job] = []
        for idx, sj in enumerate(scraped):
            try:
                with db.begin_nested():
                    job, _created = upsert_job(db, sj)
                    app, created = ensure_application(db, user, job)
            except Exception as exc:  # noqa: BLE001
                run.log(f"Could not store job {sj.source_url}: {exc}", level="error")
                continue
            jobs.append(job)
            if created:
                new_apps.append(app)
            progress.saved = idx + 1
            progress.new = len(new_apps)
            if idx % 25 == 24:
                progress.tick()
        run.run.jobs_discovered = len(new_apps)
        run.log(f"Discovered {len(scraped)} postings, {len(new_apps)} new for you")
        backfill_company_checks(db)
        progress.set_phase("saving", "Checking the companies behind the new postings")
        verify_companies(db, [a.job for a in new_apps], run, progress)
        flagged = sum(1 for a in new_apps if a.job.company_verdict == SUSPICIOUS)
        if flagged:
            run.log(f"Company check: {flagged} posting{'s' if flagged != 1 else ''} with scam signs (skipped)")
        progress.set_phase("saving", f"Reading {len(new_apps)} new postings")  # embedding may call an API
        embed_jobs(db, jobs)
        checkpoint(db)

        resume_vec = resume_embedding(db, master) if master else None
        if master is None:
            run.log("No master resume uploaded — skipping matching", level="warning")
        # Rank by vector similarity so the most promising jobs get the (costly) LLM evaluation
        if resume_vec is not None:
            new_apps.sort(key=lambda a: cosine_similarity(resume_vec, a.job.description_embedding), reverse=True)
        progress.to_score = len(new_apps)
        progress.set_phase("scoring", f"Scoring {len(new_apps)} new jobs against your resume")
        score_applications(db, user, new_apps, master, resume_vec, run, progress)
        matched = [a for a in new_apps if a.status == ApplicationStatus.MATCHED]
        run.run.jobs_matched = len(matched)
        user.last_scan_at = datetime.now(UTC)
        progress.set_phase("finishing", "Wrapping up")

        if is_swipe_mode(prefs):
            auto_keep = prefs.get("auto_keep_min_score")
            kept = 0
            if auto_keep is not None and master is not None:
                for app in sorted(matched, key=lambda a: priority_key(a.match_score, a.job.deadline_date)):
                    if (app.match_score or 0) >= int(auto_keep) and not _swiped(db, app):
                        keep_application(db, user, app, decided_by="agent",
                                         note=f"Kept automatically (score {app.match_score} ≥ {auto_keep})")
                        kept += 1
            waiting = len(matched) - kept
            run.log(f"{waiting} jobs are waiting for you in Swipe Review"
                    + (f"; {kept} kept automatically (score ≥ {auto_keep})" if kept else ""))
            progress.finish("completed", f"{run.run.jobs_discovered} new jobs, {waiting} waiting for your swipe")
            run.finish("completed")
            notify(db, user, "scan_completed", "Job scan finished",
                   f"{run.run.jobs_discovered} new jobs — {waiting} waiting for your swipe.", link="/dashboard/review",
                   data={"run_id": str(run.run.id)})
            return run.run

        run.log(f"{len(matched)} jobs matched your threshold ({prefs.get('auto_apply_threshold')})")
        if auto_prepare and master is not None:
            queued = queue_preparations(db, user, matched, run)
            run.log(f"Queued {queued} applications for preparation")
        progress.finish("completed", f"{run.run.jobs_discovered} new jobs, {run.run.jobs_matched} matches")
        run.finish("completed")
        notify(db, user, "scan_completed", "Job scan finished",
               f"{run.run.jobs_discovered} new jobs, {run.run.jobs_matched} matches.", link="/dashboard/jobs",
               data={"run_id": str(run.run.id)})
    except ScanCancelled:
        run.log("Scan stopped by you; jobs already scored stay in Swipe Review")
        progress.finish("cancelled", "Stopped")
        run.finish("cancelled")
    except Exception as exc:
        logger.exception("Scan failed for user %s", user.id)
        if not db.is_active:  # a failed flush: roll back so the failure itself can be recorded
            db.rollback()
        run.log(f"Scan failed: {exc}", level="error")
        progress.finish("failed", f"Scan failed: {exc}"[:200])
        run.finish("failed")
        notify(db, user, "agent_error", "Job scan failed", str(exc)[:300], link="/dashboard/logs")
    return run.run


def score_applications(db: Session, user: User, apps: list[Application], master: Resume | None,
                       resume_vec: list[float] | None, run: RunLog, progress: ScanProgress | None = None) -> None:
    """Score new jobs. The best MAX_LLM_EVALUATIONS_PER_SCAN (by resume similarity) go to the LLM,
    SCAN_LLM_CONCURRENCY at a time; the rest use the instant heuristic meanwhile. Cards reach Swipe
    Review as they're scored, and a card you swipe mid-scan is left alone."""

    def advance() -> None:
        if progress is not None:
            progress.scored += 1
            progress.tick()

    # With a local model (Ollama) first, llm_budget() lowers both numbers. Even one call at a time goes
    # through the pool below, so the long tail gets its instant score while the LLM works.
    concurrency, llm_count = llm_budget()
    parallel = master is not None and get_llm().available
    if not parallel:
        for idx, app in enumerate(apps):
            use_llm = idx < llm_count
            if use_llm or idx % 10 == 0:
                checkpoint(db)  # cards reach Swipe Review as they're scored; no write lock held during an LLM call
            if not _swiped(db, app):  # you kept / skipped it before the scan got to it
                try:
                    evaluate_application(db, user, app, master, use_llm=use_llm, resume_vec=resume_vec)
                except Exception as exc:  # noqa: BLE001
                    run.log(f"Evaluation failed for {app.job.company_name}: {exc}", level="error")
            advance()
        return

    assert master is not None
    prefs = user.prefs
    threshold = int(prefs.get("auto_apply_threshold") or 80)
    to_llm: list[tuple[Application, list[str], Any]] = []
    for app in apps[:llm_count]:
        try:
            needs, heads_up = (False, []) if _swiped(db, app) else _precheck(db, user, app, master)
        except Exception as exc:  # noqa: BLE001
            run.log(f"Evaluation failed for {app.job.company_name}: {exc}", level="error")
            needs, heads_up = False, []
        if needs:
            to_llm.append((app, heads_up, job_snapshot(app.job)))
        else:
            advance()
    checkpoint(db)  # no write lock is held while the LLM works
    pool = ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(to_llm))),
                              thread_name_prefix="scan-score")
    futures = {pool.submit(llm_usage.in_context(evaluate_match), master.parsed_content, snap, prefs, threshold, use_llm=True): (app, heads_up)
               for app, heads_up, snap in to_llm}
    try:
        for idx, app in enumerate(apps[llm_count:]):  # the long tail: instant heuristic while the LLM works
            if idx % 10 == 0:
                checkpoint(db)
            if not _swiped(db, app):
                try:
                    evaluate_application(db, user, app, master, use_llm=False, resume_vec=resume_vec)
                except Exception as exc:  # noqa: BLE001
                    run.log(f"Evaluation failed for {app.job.company_name}: {exc}", level="error")
            advance()
        checkpoint(db)
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            for future in done:
                app, heads_up = futures[future]
                try:
                    evaluation = future.result()
                    if not _swiped(db, app):
                        _apply_evaluation(db, user, app, evaluation, heads_up, resume_vec)
                except Exception as exc:  # noqa: BLE001
                    run.log(f"Evaluation failed for {app.job.company_name}: {exc}", level="error")
                advance()
            if done:
                checkpoint(db)  # each scored card reaches Swipe Review right away
            elif progress is not None:
                progress.tick()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def focus_rank(job: Job, prefs: dict[str, Any]) -> tuple[int, int]:
    """(location tier, season rank): prime city before the rest of the country, the target season first."""
    focus, season = get_focus(prefs), get_season(prefs)
    tier = location_tier(job.location, job.is_remote, focus) if focus else 0
    if season is None:
        return tier, 0
    state, _ = season_status(job.role_title, job.description, (job.raw_data or {}).get("terms"), season)
    return tier, 0 if state == "match" else 1


def queue_preparations(db: Session, user: User, matched: list[Application], run: RunLog | None = None) -> int:
    """Queue preparation for the best matches, respecting the daily application budget."""
    from app.worker.dispatch import enqueue

    prefs = user.prefs
    if guardrails.is_paused(user):
        if run and matched:
            run.log(f"Paused: {len(matched)} matches wait until you resume")
        return 0
    budget = guardrails.daily_cap(user)  # your daily limit, never above the server ceiling
    in_flight = db.scalar(
        select(func.count()).select_from(Application).where(
            Application.user_id == user.id,
            Application.status.in_([ApplicationStatus.PREPARING, ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.APPROVED]),
        )
    ) or 0
    remaining = max(0, budget - guardrails.applied_today(db, user) - int(in_flight))
    ordered = sorted(matched, key=lambda a: (*focus_rank(a.job, prefs), *priority_key(a.match_score, a.job.deadline_date)))
    count = 0
    for app in ordered[:remaining]:
        set_status(db, app, ApplicationStatus.PREPARING, "agent", "Queued for preparation")
        db.flush()
        enqueue("prepare_application", str(app.id), after_commit=db)
        count += 1
    if run and len(ordered) > remaining:
        run.log(f"{len(ordered) - remaining} matches deferred (daily budget of {budget} reached)")
    return count


# --------------------------------------------------------------------------- pause everything
def pause_everything(db: Session, user: User) -> None:
    """Header switch: stop scans now; preparation stops at its next step; nothing is sent until you resume.
    (A form already being submitted in the browser at this very moment finishes.)"""
    now = datetime.now(UTC)
    if user.automation_paused_at is None:
        user.automation_paused_at = now
    for run in db.scalars(select(AgentRun).where(AgentRun.user_id == user.id, AgentRun.run_type == "scan",
                                                 AgentRun.status == "running")):
        run.status = "cancelled"  # the scan notices within a second or two and stops
        run.completed_at = now
        push_update(str(user.id), "agent_run_updated", {"id": str(run.id), "status": "cancelled"})


def resume_everything(db: Session, user: User) -> None:
    """Pick up where you paused. Anything due to be sent gets a fresh undo window first."""
    from app.worker.dispatch import enqueue

    user.automation_paused_at = None
    send_at = datetime.now(UTC) + guardrails.SEND_DELAY
    for app in db.scalars(select(Application).where(Application.user_id == user.id,
                                                    Application.status == ApplicationStatus.APPROVED)):
        if app.send_after is None or guardrails.due(app, send_at):
            guardrails.hold(app, send_at, f"Sending soon: goes out at {send_at:%H:%M} UTC unless you stop it")
    for app in db.scalars(select(Application).where(Application.user_id == user.id, Application.notes == PAUSED_NOTE,
                                                    Application.status == ApplicationStatus.PREPARING)):
        app.notes = None
        enqueue("prepare_application", str(app.id), after_commit=db)


# --------------------------------------------------------------------------- stalled preparations
# A kept job is "preparing" while its task runs. If the worker or the server restarts mid-way (in local mode
# the tasks run in the API's own threads) the task is gone and the job would wait forever. Every step of a
# preparation saves progress (and so moves updated_at), and no single step takes this long:
STALLED_AFTER = timedelta(minutes=45)
MAX_PREPARE_RESTARTS = 2
RESTART_NOTE = "Preparation restarted after an interruption"


def requeue_stalled_preparations(db: Session, now: datetime | None = None) -> list[str]:
    """Kept jobs stuck in "preparing": start their preparation again (at most twice, then they're marked
    failed with a reason). Each is claimed atomically, so two sweeps never restart the same one. Returns the
    application ids to enqueue."""
    now = now or datetime.now(UTC)
    cutoff = now - STALLED_AFTER
    paused = select(User.id).where(User.automation_paused_at.is_not(None))
    stalled = db.scalars(
        select(Application).where(Application.status == ApplicationStatus.PREPARING, Application.updated_at < cutoff,
                                  Application.user_id.not_in(paused),
                                  or_(Application.notes.is_(None), Application.notes != PAUSED_NOTE))
        .order_by(Application.updated_at).limit(100)
    ).all()
    restart = []
    for app in stalled:
        claimed = db.execute(
            update(Application).where(Application.id == app.id, Application.status == ApplicationStatus.PREPARING,
                                      Application.updated_at < cutoff)
            .values(updated_at=now).execution_options(synchronize_session=False)
        ).rowcount == 1
        if not claimed:
            continue
        db.refresh(app)
        restarts = db.scalar(select(func.count()).select_from(ApplicationStatusHistory).where(
            ApplicationStatusHistory.application_id == app.id, ApplicationStatusHistory.notes == RESTART_NOTE)) or 0
        if restarts >= MAX_PREPARE_RESTARTS:
            app.error_log = f"Preparation was interrupted {restarts + 1} times and didn't finish."
            app.needs_manual_review = True
            app.manual_review_reason = ("Preparation kept getting interrupted. Press “Re-fill form” to try again, or "
                                        "apply yourself and click “I Applied”.")
            set_status(db, app, ApplicationStatus.FAILED, "agent", "Preparation kept getting interrupted")
            notify(db, db.get(User, app.user_id), "agent_error", f"Could not prepare {app.job.company_name} application",
                   app.manual_review_reason, link=f"/dashboard/applications/{app.id}")
            continue
        db.add(ApplicationStatusHistory(application_id=app.id, old_status=ApplicationStatus.PREPARING,
                                        new_status=ApplicationStatus.PREPARING, changed_by="agent", notes=RESTART_NOTE))
        restart.append(str(app.id))
    if restart:
        logger.info("Restarting %d stalled preparation(s)", len(restart))
    return restart


# --------------------------------------------------------------------------- swipe review
REVIEWABLE = (ApplicationStatus.DISCOVERED, ApplicationStatus.MATCHED, ApplicationStatus.SKIPPED)


def keep_application(db: Session, user: User, app: Application, decided_by: str = "user",
                     note: str = "Kept in Swipe Review") -> Application:
    """Swipe right: prepare this job (tailor, cover letter, fill) and, with auto_submit_kept, submit it."""
    from app.worker.dispatch import enqueue

    if app.status not in REVIEWABLE:
        raise ValueError(f"This job is already {app.status.value.replace('_', ' ')}")
    app.review_decision = "keep"
    app.reviewed_at = datetime.now(UTC)
    app.auto_submit = bool(user.prefs.get("auto_submit_kept", True))
    set_status(db, app, ApplicationStatus.PREPARING, decided_by, note)
    enqueue("prepare_application", str(app.id), after_commit=db)
    return app


def skip_application(db: Session, app: Application, note: str = "Skipped in Swipe Review") -> Application:
    if app.status not in REVIEWABLE:
        raise ValueError(f"This job is already {app.status.value.replace('_', ' ')}")
    app.review_decision = "skip"
    app.reviewed_at = datetime.now(UTC)
    set_status(db, app, ApplicationStatus.SKIPPED, "user", note)
    return app


def undo_review(db: Session, app: Application) -> Application:
    """Put a swiped job back on the deck (only while nothing has been prepared yet)."""
    if app.review_decision is None:
        raise ValueError("This job has not been swiped")
    if app.review_decision == "skip" and app.status != ApplicationStatus.SKIPPED:
        raise ValueError(f"This job is already {app.status.value.replace('_', ' ')}")
    if app.review_decision == "keep" and (app.status != ApplicationStatus.PREPARING or app.tailored_resume_id):
        raise ValueError("Preparation has already started — withdraw it from Applications instead")
    app.review_decision = None
    app.auto_submit = False
    set_status(db, app, ApplicationStatus.MATCHED, "user", "Swipe undone")
    return app


def import_job_url(db: Session, user: User, url: str) -> Application:
    scraped = fetch_job_from_url(url)
    if scraped is None:
        raise ScraperError("Could not read a job posting at that URL")
    job, _ = upsert_job(db, scraped)
    embed_jobs(db, [job])
    app, _ = ensure_application(db, user, job)
    master = get_master_resume(db, user)
    if app.status == ApplicationStatus.DISCOVERED:
        evaluate_application(db, user, app, master, use_llm=True,
                             resume_vec=resume_embedding(db, master) if master else None)
    return app


# --------------------------------------------------------------------------- preparation
def _store(user_id: uuid.UUID, folder: str, data: bytes, ext: str, content_type: str) -> str:
    key = f"{user_prefix(user_id)}/{folder}/{uuid.uuid4().hex}.{ext}"
    return get_storage().save(key, data, content_type)


RESUME_STRATEGIES = ("original", "light", "full")


def resume_strategy(user: User, master: Resume) -> str:
    """original: send your uploaded file untouched · light: reorder only · full: AI rewrite (truth-guarded).

    A resume pasted as text has no original file, so it falls back to light tweaks.
    """
    strategy = user.prefs.get("resume_strategy") or "original"
    if strategy not in RESUME_STRATEGIES:
        strategy = "original"
    if strategy == "original" and not master.original_file_url:
        return "light"
    return strategy


def render_tailored_pdf(user: User, resume: Resume) -> str:
    pdf = render_resume_pdf(resume.parsed_content, template=(user.prefs.get("resume_template") or "classic"))
    key = _store(user.id, "resumes", pdf, "pdf", "application/pdf")
    resume.pdf_url = key
    return key


def prepare_application(db: Session, application_id: str, stage: bool | None = None) -> Application:
    app = db.get(Application, uuid.UUID(str(application_id)))
    if app is None:
        raise ValueError("Application not found")
    user = db.get(User, app.user_id)
    if app.status != ApplicationStatus.PREPARING:
        # Stale task: the swipe was undone, the job was skipped, or a duplicate task already prepared it.
        logger.info("Skipping preparation of %s: status is %s", app.id, app.status.value)
        return app
    if guardrails.is_paused(user):
        app.notes = PAUSED_NOTE  # resuming picks it up again
        return app
    app.updated_at = datetime.now(UTC)  # work started: a long wait in the queue isn't mistaken for a stall
    run = RunLog(db, user, "prepare", "system")
    run.log(f"Preparing {app.job.role_title} @ {app.job.company_name}")
    try:
        master = get_master_resume(db, user)
        if master is None:
            raise ValueError("No master resume uploaded")
        job = app.job
        checkpoint(db)
        if enrich_job(db, job):
            run.log("Fetched the full job description from the posting")
        if app.match_score is None:  # e.g. imported before a resume existed; score it for the reviewer
            evaluation = evaluate_match(master.parsed_content, job, user.prefs,
                                        int(user.prefs.get("auto_apply_threshold") or 80), use_llm=True)
            app.match_score, app.match_reasoning, app.match_details = (
                evaluation["match_score"], evaluation["reasoning"], evaluation)

        strategy = resume_strategy(user, master)
        if strategy == "original":
            # Your own file, exactly as uploaded: your design, your words, nothing re-parsed or re-rendered.
            resume = master
            app.tailored_resume_id = None
            app.tailored_resume_pdf_url = master.original_file_url
            run.log("Using your original resume file, unchanged")
        else:
            checkpoint(db)  # never hold the write lock while waiting on the LLM
            tailored = (light_tailor if strategy == "light" else tailor_resume)(master.parsed_content, job)
            resume = Resume(
                user_id=user.id,
                label=f"{job.company_name} — {job.role_title}"[:255],
                parsed_content=tailored["tailored_resume"],
                is_master=False,
                parent_resume_id=master.id,
                tailored_for_job_id=job.id,
                changes_made=tailored["changes_made"] + [f"[guard] {v}" for v in tailored["violations"]],
                version=1,
            )
            db.add(resume)
            db.flush()
            app.tailored_resume_id = resume.id
            app.tailored_resume_pdf_url = render_tailored_pdf(user, resume)
            run.log(f"Tailored resume ({tailored['method']}), {len(tailored['changes_made'])} changes, "
                    f"{len(tailored['violations'])} truthfulness corrections")

        checkpoint(db)
        if _undone(db, app, run) or _paused(db, user, app, run):
            return app
        if user.prefs.get("cover_letter_enabled", True):
            letter = generate_cover_letter(resume.parsed_content, job)
            app.cover_letter = letter["cover_letter"]
            run.log(f"Cover letter generated ({letter['method']}, tone={letter['tone']})")

        # Pre-answer questions when the ATS publishes them (Greenhouse API)
        if app.ats_platform == ATSPlatform.GREENHOUSE:
            try:
                from app.scrapers.ats_detect import parse_ats_url
                from app.scrapers.greenhouse import GreenhouseScraper

                ref = parse_ats_url(job.application_url or job.source_url)
                if ref.board and ref.job_id:
                    questions = [q for q in GreenhouseScraper().application_questions(ref.board, ref.job_id)
                                 if q["field_type"] != "file" and not q["question"].lower().startswith(("first name", "last name", "email", "phone"))]
                    app.custom_answers = answer_questions(questions, resume.parsed_content, user.prefs,
                                                          mappings_dict(user.field_mappings), job,
                                                          links=profile_links(user))
                    run.log(f"Pre-answered {len(questions)} Greenhouse questions")
            except Exception as exc:  # noqa: BLE001
                run.log(f"Could not pre-fetch Greenhouse questions: {exc}", level="warning")

        run.run.applications_prepared = 1
        checkpoint(db)
        if _undone(db, app, run) or _paused(db, user, app, run):
            return app
        should_stage = settings.AUTO_STAGE_APPLICATIONS if stage is None else stage
        if should_stage:
            stage_application(db, str(app.id), run=run)
        else:
            _ready_or_submit(db, user, app, run)
        run.finish("completed")
    except Exception as exc:
        logger.exception("Preparation failed for %s", application_id)
        run.log(f"Preparation failed: {exc}", level="error")
        app.error_log = f"{datetime.now(UTC).isoformat()} prepare: {exc}"
        set_status(db, app, ApplicationStatus.FAILED, "agent", f"Preparation failed: {exc}")
        run.finish("failed")
        notify(db, user, "agent_error", f"Could not prepare {app.job.company_name} application", str(exc)[:300],
               link=f"/dashboard/applications/{app.id}")
    return app


def _undone(db: Session, app: Application, run: RunLog) -> bool:
    """True (and the run is closed) when you undid or skipped the job while it was being prepared."""
    db.refresh(app, attribute_names=["status", "review_decision"])
    if app.status == ApplicationStatus.PREPARING:
        return False
    run.log(f"Preparation stopped: the job is now {app.status.value.replace('_', ' ')}")
    run.finish("cancelled")
    return True


PAUSED_NOTE = "Paused: preparation continues when you resume"


def _paused(db: Session, user: User, app: Application, run: RunLog) -> bool:
    """True (and the run is closed) when you pressed "Pause everything" while this was being prepared."""
    if not guardrails.paused_now(db, user):
        return False
    app.notes = PAUSED_NOTE
    run.log("Paused: preparation stopped here and continues when you resume")
    run.finish("cancelled")
    return True


def enrich_job(db: Session, job: Job) -> bool:
    """Postings from curated lists carry only a title; fetch the real description when we can."""
    if not (job.raw_data or {}).get("listing_source") or len(job.description or "") >= 400:
        return False
    try:
        scraped = fetch_job_from_url(job.application_url or job.source_url)
    except Exception as exc:  # noqa: BLE001 - best effort, the application still works without it
        logger.info("Could not enrich job %s: %s", job.id, exc)
        return False
    if scraped is None or len(scraped.description or "") <= len(job.description or ""):
        return False
    job.description = scraped.description
    job.requirements = job.requirements or scraped.requirements
    if scraped.salary_min or scraped.salary_max:
        job.salary_min, job.salary_max = scraped.salary_min, scraped.salary_max
        job.salary_currency = scraped.salary_currency or job.salary_currency
    job.extracted_skills = extract_skills(f"{job.role_title}\n{job.description}")
    job.description_embedding = None
    embed_jobs(db, [job])
    return True


def unanswered_questions(app: Application, trust_generated: bool = False) -> list[str]:
    """Questions that must be answered by you before a submission.

    Blank required answers and every eligibility question the agent is unsure about (visa, work
    authorization, record checks...) always block. With ``trust_generated`` the agent's own answers
    to open-ended questions ("Why do you want to work here?") don't.
    """
    from app.services.question_answerer import FACTUAL
    from app.services.text_utils import normalize_text

    blocking = []
    for a in app.custom_answers or []:
        question = str(a.get("question") or "")
        answer = str(a.get("answer") or "").strip()
        factual = bool(FACTUAL.search(normalize_text(question)))
        guessed = a.get("source") in ("llm", "fallback")  # eligibility answers count only when they're yours
        if (a.get("required") and not answer) or (factual and (not answer or a.get("needs_user_review") or guessed)):
            blocking.append(question)
        elif a.get("needs_user_review") and not trust_generated:
            blocking.append(question)
    return blocking


def ready_or_submit(db: Session, user: User, app: Application, run: RunLog | None = None) -> None:
    _ready_or_submit(db, user, app, run)


def _ready_or_submit(db: Session, user: User, app: Application, run: RunLog | None = None) -> None:
    """Kept jobs go straight to submission when nothing needs a human; everything else waits for review."""
    from app.worker.dispatch import enqueue

    if app.auto_submit and app.review_decision == "keep" and _may_auto_submit(user, app):
        blockers = unanswered_questions(app, trust_generated=bool(user.prefs.get("trust_generated_answers", True)))
        # Only verified companies are applied to on their own; your "Apply with the bot" click is your OK for this one
        if not app.needs_manual_review and not blockers and not is_trusted(app.job, user.prefs) \
                and not bot_apply_requested(app):
            app.needs_manual_review = True
            app.manual_review_reason = company_hold_reason(app.job)
            if run:
                run.log(f"Waiting for you: {app.job.company_name} isn't a verified company")
        if not app.needs_manual_review and not blockers:
            set_status(db, app, ApplicationStatus.APPROVED, "user", "Approved when you kept it in Swipe Review")
            if bot_apply_requested(app):  # your "Apply with the bot" click on this one: send it now
                if run:
                    run.log("Everything answered — submitting (you asked the bot to apply)")
                enqueue("submit_application", str(app.id), after_commit=db)
                return
            # Automatic: it waits in "Sending soon" first, so you can stop it
            send_at = datetime.now(UTC) + guardrails.SEND_DELAY
            guardrails.hold(app, send_at, f"Sending soon: goes out at {send_at:%H:%M} UTC unless you stop it")
            if run:
                run.log(f"Everything answered — sending in {int(guardrails.SEND_DELAY.total_seconds() // 60)} minutes "
                        "unless you stop it (you kept this job in Swipe Review)")
            return
        if run:
            run.log("Needs your review before submitting: "
                    + (app.manual_review_reason or f"{len(blockers)} question(s) need an answer"))
    _mark_ready(db, user, app)


INTERNSHALA_RECHECK_SECONDS = 50 * 60  # over the daily limit: look again later (it goes out the next day)


def is_internshala_job(job: Job) -> bool:
    return ((job.raw_data or {}).get("listing_source") == "internshala"
            or "internshala.com/" in (job.application_url or job.source_url or ""))


def internshala_ready(user: User) -> bool:
    """The opt-in Internshala bot is on and holds a working Internshala login."""
    return bool(user.prefs.get("internshala_bot_enabled") and user.internshala_session and user.internshala_session_valid)


def _may_auto_submit(user: User, app: Application) -> bool:
    """Sites that take applications only from your own account are never sent without your click,
    except Internshala with the bot ready and either "Submit automatically" on or your
    "Apply with the bot" click on this application."""
    if is_internshala_job(app.job):
        return internshala_ready(user) and bool(user.prefs.get("internshala_auto_submit") or bot_apply_requested(app))
    return not (app.job.raw_data or {}).get("apply_on_site")


def bot_apply_requested(app: Application) -> bool:
    return bool((app.match_details or {}).get("bot_apply_requested_at"))


def internshala_missing(user: User) -> str | None:
    """What the Internshala bot still needs: "bot_off", "not_synced", "expired" — or None when ready."""
    if not user.prefs.get("internshala_bot_enabled"):
        return "bot_off"
    if not user.internshala_session:
        return "not_synced"
    if not user.internshala_session_valid:
        return "expired"
    return None


INTERNSHALA_MISSING = {
    "bot_off": "The Internshala bot is off: turn on “Let the agent apply on Internshala” in Settings › Integrations "
               "— or apply yourself and click “I Applied”.",
    "not_synced": "The Internshala bot is on, but your Internshala login isn't synced yet: log into internshala.com in "
                  "Chrome, open the HireFlow extension and click “Sync Internshala session” — or apply yourself and "
                  "click “I Applied”.",
    "expired": "Your synced Internshala login has expired: log into internshala.com in Chrome again and click “Sync "
               "Internshala session” in the extension — or apply yourself and click “I Applied”.",
}


def bot_apply(db: Session, user: User, app: Application) -> Application:
    """Your "Apply with the bot" click: the bot fills this Internshala form and submits it by itself.

    The click is your approval for this one application. If a required question has no answer the
    bot can stand behind, it stops and the application comes back to Ready to submit with the reason.
    """
    from app.worker.dispatch import enqueue

    if not is_internshala_job(app.job):
        raise ValueError("“Apply with the bot” is for Internshala postings; use Submit for this one")
    if guardrails.is_paused(user):
        raise ValueError("HireFlow is paused. Resume it to submit applications.")
    other = guardrails.duplicate_of(db, user, app)
    if other is not None:
        raise ValueError(guardrails.duplicate_message(other))
    if app.job.company_verdict == SUSPICIOUS and not is_trusted(app.job, user.prefs):
        raise ValueError(suspicious_message(app.job))
    missing = internshala_missing(user)
    if missing:
        raise ValueError(INTERNSHALA_MISSING[missing])
    if app.status not in (ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.FAILED, ApplicationStatus.MATCHED,
                          ApplicationStatus.DISCOVERED):
        raise ValueError(f"This application is {app.status.value.replace('_', ' ')}")
    app.match_details = {**(app.match_details or {}), "bot_apply_requested_at": datetime.now(UTC).isoformat()}
    app.review_decision = "keep"
    app.auto_submit = True
    app.needs_manual_review = False
    app.manual_review_reason = None
    app.retry_count = 0
    set_status(db, app, ApplicationStatus.PREPARING, "user", "You asked the bot to fill and submit this on Internshala")
    enqueue("stage_application", str(app.id), after_commit=db)
    return app


def internshala_submitted_today(db: Session, user: User) -> int:
    """Internshala applications the agent sent today (UTC), for ``internshala_daily_limit``."""
    from app.models.application import ApplicationStatusHistory as History

    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(db.scalar(
        select(func.count()).select_from(History)
        .join(Application, Application.id == History.application_id).join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user.id, History.new_status == ApplicationStatus.APPLIED,
               History.changed_by == "agent", History.created_at >= start,
               or_(Job.source_url.like("%internshala.com/%"), Job.application_url.like("%internshala.com/%")))
    ) or 0)


def _internshala_over_limit(db: Session, user: User) -> str | None:
    limit = max(1, min(25, int(user.prefs.get("internshala_daily_limit") or 15)))
    done = internshala_submitted_today(db, user)
    return f"Daily Internshala limit reached ({done}/{limit}); it goes out tomorrow" if done >= limit else None


# One browser at a time on your Internshala account (a person never applies from four windows at once), and
# once Internshala has refused the synced login, nothing else tries it until you sync again. Per process: the
# local app runs every job in one process.
_internshala_guard = threading.Lock()
_internshala_locks: dict[uuid.UUID, threading.Lock] = {}
_internshala_refused: dict[uuid.UUID, float] = {}   # when the bot last saw Internshala refuse the login
_internshala_notified: dict[uuid.UUID, float] = {}  # the sync we already told you about


def _synced_at(user: User) -> float:
    return user.internshala_session_updated_at.timestamp() if user.internshala_session_updated_at else 0.0


def _internshala_lock(user_id: uuid.UUID) -> threading.Lock:
    with _internshala_guard:
        return _internshala_locks.setdefault(user_id, threading.Lock())


def internshala_refused(user: User) -> bool:
    """Internshala refused the login you synced most recently (your next sync clears this)."""
    return _internshala_refused.get(user.id, -1.0) >= _synced_at(user)


def _internshala_session_expired(db: Session, user: User) -> None:
    user.internshala_session_valid = False
    if _internshala_notified.get(user.id) == _synced_at(user):
        return  # one notification per synced login, not one per waiting application
    _internshala_notified[user.id] = _synced_at(user)
    notify(db, user, "session_expired", "Internshala session expired",
           "Internshala showed the bot its sign-up / login page, so the synced login no longer works there. Log into "
           "internshala.com in Chrome (log out and back in if you already are), then click “Sync Internshala "
           "session” in the extension: the waiting applications are filled again automatically.",
           link="/dashboard/settings?tab=integrations")


def _internshala_already_applied(db: Session, user: User, app: Application, run: RunLog | None, by_agent: bool) -> None:
    """Internshala shows "Already Applied": track it as applied instead of failing."""
    note = "Internshala shows this internship as already applied"
    app.needs_manual_review = False
    app.manual_review_reason = None
    if by_agent:  # an earlier attempt of ours went through without a confirmation
        set_status(db, app, ApplicationStatus.APPLIED, "agent", note)
        notify(db, user, "application_submitted", f"✅ Applied: {app.job.role_title} @ {app.job.company_name}",
               "Internshala confirms your application was sent.", link=f"/dashboard/applications/{app.id}")
    else:
        mark_self_applied(db, user, app, note=f"{note}; tracking it from now on")
    if run:
        run.log(note)


def direct_submit_blocker(user: User, app: Application) -> str | None:
    """Why the agent can't submit this application itself (None = your one click submits it).

    Boards like Internshala only take applications from your own logged-in account; Internshala
    can, once you turn on its bot and sync your login.
    """
    if app.job.company_verdict == SUSPICIOUS and not is_trusted(app.job, user.prefs):
        return suspicious_message(app.job)
    if is_internshala_job(app.job):
        missing = internshala_missing(user)
        if missing:
            return INTERNSHALA_MISSING[missing]
        if not app.form_fields:  # prepared while the bot was off: the real form hasn't been read yet
            return INTERNSHALA_FILLING
        return None
    site = (app.job.raw_data or {}).get("apply_on_site")
    if site:
        return f"{site} needs your own {site} login: apply there, then click “I Applied”."
    return None


INTERNSHALA_FILLING = ("The bot hasn't filled this Internshala form yet: click “Apply with the bot” and it fills the "
                      "form and submits it for you — or apply yourself and click “I Applied”.")


def restage_internshala_waiting(db: Session, user: User) -> int:
    """Internshala applications prepared while the bot was off get their real form filled once it's on,
    so you review what will actually be sent (never submit a form nobody has seen)."""
    from app.worker.dispatch import enqueue

    if not internshala_ready(user):
        return 0
    waiting = db.scalars(select(Application).where(Application.user_id == user.id,
                                                   Application.status == ApplicationStatus.PENDING_APPROVAL)).all()
    count = 0
    for app in waiting:
        if is_internshala_job(app.job) and not app.form_fields:
            app.needs_manual_review = False
            app.manual_review_reason = None
            if app.review_decision == "keep":  # prepared while the bot couldn't apply: your keep counts again
                app.auto_submit = bool(user.prefs.get("auto_submit_kept", True))
            set_status(db, app, ApplicationStatus.PREPARING, "agent", "Filling the Internshala form with the bot")
            enqueue("stage_application", str(app.id), after_commit=db)
            count += 1
    return count


def _mark_ready(db: Session, user: User, app: Application) -> None:
    set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent", "Ready for your review")
    warn = f" ⚠️ {app.manual_review_reason}" if app.needs_manual_review and app.manual_review_reason else ""
    notify(
        db, user, "application_ready",
        f"Review application: {app.job.role_title} @ {app.job.company_name}",
        f"Match score {app.match_score or '—'}. Tailored resume, cover letter and answers are ready.{warn}",
        link=f"/dashboard/applications/{app.id}",
        data={"application_id": str(app.id), "match_score": app.match_score},
    )


@contextmanager
def _temp_file(data: bytes, suffix: str, name: str | None = None):  # type: ignore[no-untyped-def]
    directory = tempfile.mkdtemp(prefix="hireflow-")
    path = os.path.join(directory, name or f"file{suffix}")
    with open(path, "wb") as fh:
        fh.write(data)
    try:
        yield path
    finally:
        try:
            os.remove(path)
            os.rmdir(directory)
        except OSError:
            pass


def build_packet(db: Session, user: User, app: Application, resume_path: str | None, cover_path: str | None) -> Any:
    from app.submitters import CandidatePacket

    resume = app.tailored_resume or get_master_resume(db, user)
    rc = ResumeContent.model_validate(resume.parsed_content if resume else {})
    info = rc.personal_info
    name = (info.name or user.full_name or "").split(" ")
    mappings = mappings_dict(user.field_mappings)
    job = app.job

    links = profile_links(user)

    def resolver(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return answer_questions(questions, rc.to_dict(), user.prefs, mappings, job, links=links)

    current = rc.experience[0] if rc.experience else None
    credentials = dict(user.ats_credentials or {})
    if mappings.get("workday_password") and "workday_password" not in credentials:
        credentials["workday_password"] = mappings["workday_password"]
    return CandidatePacket(
        first_name=name[0] if name else "",
        last_name=" ".join(name[1:]) if len(name) > 1 else "",
        email=info.email or user.email,
        phone=info.phone or user.phone or "",
        location=info.location or user.location or "",
        # Your profile links (onboarding / Settings) first, then the ones on the resume
        linkedin=user.linkedin_url or info.linkedin or "",
        github=user.github_url or info.github or "",
        portfolio=user.portfolio_url or info.portfolio or mappings.get("website", ""),
        current_company=current.company if current else "",
        current_title=current.title if current else "",
        resume_path=resume_path,
        cover_letter_text=app.cover_letter,
        cover_letter_path=cover_path,
        answers=list(app.custom_answers or []),
        resolve_answers=resolver,
        ats_credentials=credentials,
        linkedin_cookie=user.linkedin_session_cookie,
        internshala_session=user.internshala_session if is_internshala_job(job) else None,
        internshala_user_agent=user.internshala_user_agent if is_internshala_job(job) else None,
        application_url=job.application_url or job.source_url,
        company_name=job.company_name,
        role_title=job.role_title,
        # Your corrections from the "Ready to submit" queue beat everything the agent worked out
        overrides={str(k): "" if v is None else str(v) for k, v in (app.field_overrides or {}).items()},
    )


def _resume_filename(user: User, ext: str = ".pdf") -> str:
    safe = "".join(c for c in (user.full_name or "Resume") if c.isalnum() or c in " -_").strip().replace(" ", "_")
    return f"{safe or 'Resume'}_Resume{ext}"


def _run_submitter(db: Session, user: User, app: Application, submit: bool) -> Any:
    if not is_internshala_job(app.job):
        return _drive_submitter(db, user, app, submit)
    from app.submitters.base import SubmissionResult
    from app.submitters.internshala_apply import EXPIRED

    with _internshala_lock(user.id):  # queued runs wait here, then see what the run before them found
        if internshala_refused(user):
            return SubmissionResult(False, "failed", error=EXPIRED, session_expired=True)  # no browser opened
        result = _drive_submitter(db, user, app, submit)
        if result.session_expired:
            _internshala_refused[user.id] = time.time()
        elif result.session_cookies:  # Internshala renewed your login while the bot used it: keep the fresh copy
            user.internshala_session = result.session_cookies
        return result


def _drive_submitter(db: Session, user: User, app: Application, submit: bool) -> Any:
    from app.services.pdf_generator import render_cover_letter_pdf
    from app.submitters import InternshalaSubmitter, get_submitter

    storage = get_storage()
    if not app.tailored_resume_pdf_url:
        resume = app.tailored_resume or get_master_resume(db, user)
        if resume is None:
            raise ValueError("No resume available")
        app.tailored_resume_pdf_url = render_tailored_pdf(user, resume)
    pdf = storage.read(app.tailored_resume_pdf_url)
    ext = os.path.splitext(app.tailored_resume_pdf_url)[1].lower() or ".pdf"  # your original may be a .docx
    cover_pdf = render_cover_letter_pdf(app.cover_letter, {"name": user.full_name, "email": user.email}) if app.cover_letter else None
    submitter = (InternshalaSubmitter() if is_internshala_job(app.job)
                 else get_submitter(app.ats_platform or application_platform(app.job)))
    with _temp_file(pdf, ext, _resume_filename(user, ext)) as resume_path:
        if cover_pdf:
            with _temp_file(cover_pdf, ".pdf", "Cover_Letter.pdf") as cover_path:
                packet = build_packet(db, user, app, resume_path, cover_path)
                return submitter.submit(packet) if submit else submitter.stage(packet)
        packet = build_packet(db, user, app, resume_path, None)
        return submitter.submit(packet) if submit else submitter.stage(packet)


def _merge_answers(existing: list[dict[str, Any]] | None, new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for answer in (existing or []) + new:
        key = (answer.get("question") or "").strip().lower()
        if key:
            merged[key] = {**merged.get(key, {}), **answer}
    return list(merged.values())


def stage_application(db: Session, application_id: str, run: RunLog | None = None) -> Application:
    """Fill the form and take a screenshot WITHOUT submitting, then wait for approval."""
    app = db.get(Application, uuid.UUID(str(application_id)))
    user = db.get(User, app.user_id)
    if guardrails.is_paused(user):  # no browser opens while you've paused everything
        if app.status == ApplicationStatus.PREPARING:
            app.notes = PAUSED_NOTE
        return app
    if (reason := demo_blocker(app)) is not None:
        app.needs_manual_review, app.manual_review_reason = True, reason
        app.auto_submit = False
        _mark_ready(db, user, app)
        return app
    internshala = is_internshala_job(app.job)
    site = (app.job.raw_data or {}).get("apply_on_site") or ("Internshala" if internshala else None)
    # Boards like Internshala only take applications from your own logged-in account (the opt-in
    # Internshala bot can, with the login the extension syncs: then the form is filled like any other).
    # Any Internshala posting counts, however it was added (e.g. "Add job by URL"), so the bot never
    # opens your account while it's off.
    if site and not (internshala and internshala_ready(user)):
        missing = internshala_missing(user) if internshala else None
        if missing in (None, "bot_off"):  # you apply there yourself (turning the bot on restores auto-submit)
            app.auto_submit = False
        app.needs_manual_review = True
        app.manual_review_reason = (INTERNSHALA_MISSING[missing] if missing and missing != "bot_off" else
                                    f"{site} needs your own {site} login: open the application form, apply there, "
                                    "then click “I Applied” so the agent keeps tracking it.")
        app.staged_at = datetime.now(UTC)
        if run:
            run.log(f"{site} posting: your resume and answers are ready; apply on {site} yourself")
        if missing == "expired":  # the one "Internshala session expired" notice covers every waiting application
            set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent", "Waiting for a working Internshala login")
        else:
            _mark_ready(db, user, app)
        return app
    result = None
    checkpoint(db)
    for attempt in (1, 2):  # rule #10: retry once
        result = _run_submitter(db, user, app, submit=False)
        if result.success or result.session_expired or result.stage != "failed":  # e.g. already applied: final
            break
        if run:
            run.log(f"Staging attempt {attempt} failed: {result.error}", level="warning")
    db.refresh(app, attribute_names=["status"])
    if app.status != ApplicationStatus.PREPARING:  # e.g. you clicked "I Applied" while the form was being filled
        if run:
            run.log(f"Form filled, but the job is now {app.status.value.replace('_', ' ')}; leaving it as is")
        return app
    if result.stage == "already_applied":
        _internshala_already_applied(db, user, app, run, by_agent=False)
        return app
    if result.screenshot and not result.session_expired:  # a login / sign-up page is not your filled form
        _replace_file(app, "form_screenshot_url", _store(user.id, "screenshots", result.screenshot, "png", "image/png"))
    app.form_fields = result.fields
    if result.answers:
        app.custom_answers = _merge_answers(app.custom_answers, result.answers)
    app.staged_at = datetime.now(UTC)
    if result.session_expired and is_internshala_job(app.job):
        _internshala_session_expired(db, user)
    elif result.session_expired:
        user.linkedin_session_valid = False
        notify(db, user, "session_expired", "LinkedIn session expired",
               "Re-sync your LinkedIn session from the browser extension to use Easy Apply.", link="/dashboard/settings")
    if not result.success:
        app.needs_manual_review = True
        app.manual_review_reason = (result.error if result.stage == "unavailable"  # closed / external / profile gate
                                    else f"Automatic form filling failed: {result.error}. You can still apply manually.")
        app.error_log = f"{datetime.now(UTC).isoformat()} stage: {result.error}"
    else:
        app.needs_manual_review = result.needs_manual_review
        app.manual_review_reason = result.review_reason
    if result.session_expired and internshala:  # waits for a fresh login; one "session expired" notice covers all
        set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent", "Waiting for a working Internshala login")
        return app
    if run:
        run.log(f"Staged form on {app.ats_platform.value if app.ats_platform else 'unknown'}: "
                f"{len([f for f in result.fields if f['status'] == 'filled'])} fields filled"
                + (f", review needed: {app.manual_review_reason}" if app.needs_manual_review else ""))
    _ready_or_submit(db, user, app, run)
    return app


# --------------------------------------------------------------------------- approval & submission
def approve_application(db: Session, app: Application, cover_letter: str | None = None,
                        custom_answers: list[dict[str, Any]] | None = None, note: str = "Approved by user") -> Application:
    if app.status not in (ApplicationStatus.PENDING_APPROVAL, ApplicationStatus.FAILED, ApplicationStatus.MATCHED):
        raise ValueError(f"Cannot approve an application in status '{app.status.value}'")
    if guardrails.is_paused(app.user):
        raise ValueError("HireFlow is paused. Resume it to submit applications.")
    other = guardrails.duplicate_of(db, app.user, app)
    if other is not None:
        raise ValueError(guardrails.duplicate_message(other))
    if cover_letter is not None:
        app.cover_letter = cover_letter
    if custom_answers is not None:
        app.custom_answers = [{**a, "needs_user_review": False, "source": a.get("source") or "user"} for a in custom_answers]
        remember_answers(db, app.user_id, custom_answers)
    app.retry_count = 0
    set_status(db, app, ApplicationStatus.APPROVED, "user", note)
    from app.worker.dispatch import enqueue

    db.flush()
    enqueue("submit_application", str(app.id), after_commit=db)
    return app


def mark_self_applied(db: Session, user: User, app: Application, applied_on: date | None = None,
                      note: str | None = None) -> bool:
    """"I Applied": you applied on your own. The agent stops working on it and tracks it from now on.

    Returns False when the application is already at "applied" or further along (nothing moves backwards).
    """
    if STATUS_RANK.get(app.status, 0) >= STATUS_RANK[ApplicationStatus.APPLIED] and app.status != ApplicationStatus.WITHDRAWN:
        return False
    app.auto_submit = False
    app.needs_manual_review = False
    app.manual_review_reason = None
    set_status(db, app, ApplicationStatus.APPLIED, "user", note or "You applied on your own")
    if applied_on is not None and applied_on <= datetime.now(UTC).date():
        app.submitted_at = datetime(applied_on.year, applied_on.month, applied_on.day, 12, tzinfo=UTC)
    job = app.job
    notify(
        db, user, "self_applied", f"📌 Tracking: {job.role_title} @ {job.company_name}",
        "You applied on your own. HireFlow now watches your inbox for replies from "
        f"{job.company_name} and will tell you about every update (acknowledgement, test, interview, offer).",
        link=f"/dashboard/applications/{app.id}",
        data={"application_id": str(app.id), "company": job.company_name, "role": job.role_title},
    )
    return True


def demo_blocker(app: Application) -> str | None:
    """The public demo only ever opens or sends to the bundled demo careers site."""
    if not settings.DEMO_MODE:
        return None
    url = app.job.application_url or app.job.source_url or ""
    if url.startswith(settings.demo_site_url + "/"):
        return None
    return "Demo: HireFlow only applies to the bundled demo careers site here, so this one isn't opened or sent."


def _replace_file(app: Application, attr: str, key: str) -> None:
    """Point ``attr`` at a newly stored file and delete the one it replaces."""
    old = getattr(app, attr)
    setattr(app, attr, key)
    if old and old != key:
        try:
            get_storage().delete(old)
        except Exception:  # noqa: BLE001 - a leftover file is harmless
            logger.warning("Could not delete replaced file %s", old)


def _within_limits(db: Session, user: User, app: Application) -> bool:
    """Duplicates, the daily cap and the per-company cap, checked on the server right before sending.
    A duplicate is dropped; over a limit, it waits (still approved) and the sweep sends it later."""
    other = guardrails.duplicate_of(db, user, app)
    if other is not None:
        message = guardrails.duplicate_message(other)
        app.send_after = None
        app.auto_submit = False
        set_status(db, app, ApplicationStatus.SKIPPED, "agent", f"Not sent: {message}")
        notify(db, user, "application_duplicate", f"Not sent twice: {app.job.role_title} @ {app.job.company_name}",
               message, link=f"/dashboard/applications/{other.id}")
        return False
    until = guardrails.daily_cap_wait(db, user)
    if until is not None:
        guardrails.hold(app, until, f"Daily limit of {guardrails.daily_cap(user)} reached: sends after {until:%d %b %H:%M} UTC")
        return False
    until = guardrails.company_cap_wait(db, user, app)
    if until is not None:
        guardrails.hold(app, until, f"Already {guardrails.company_cap()} applications to {app.job.company_name} this week: "
                                    f"sends on {until:%d %b %H:%M} UTC")
        return False
    return True


def remember_answers(db: Session, user_id: uuid.UUID, answers: list[dict[str, Any]]) -> None:
    """Save approved answers to standard questions (sponsorship, work authorization...) as field mappings."""
    known = set(db.scalars(select(UserFieldMapping.field_name).where(UserFieldMapping.user_id == user_id)))
    for answer in answers:
        value = str(answer.get("answer") or "").strip()
        key = learnable_key(str(answer.get("question") or ""))
        if key and value and key not in known:
            db.add(UserFieldMapping(user_id=user_id, field_name=key, field_value=value, field_type="text"))
            known.add(key)


def submit_application(db: Session, application_id: str) -> Application:
    """Submit an APPROVED application. Never called for unapproved applications (DIRECTIVE 2)."""
    app = db.get(Application, uuid.UUID(str(application_id)))
    if app is None:
        raise ValueError("Application not found")
    if app.status != ApplicationStatus.APPROVED:
        logger.warning("Refusing to submit application %s in status %s", app.id, app.status)
        return app
    user = db.get(User, app.user_id)
    if guardrails.is_paused(user):  # sent once you resume (with a fresh undo window)
        guardrails.hold(app, datetime.now(UTC), "Paused: this is sent when you resume")
        return app
    if not guardrails.due(app):  # in "Sending soon" or held by a limit: the sweep sends it when it's due
        return app
    if (reason := demo_blocker(app)) is not None:
        app.needs_manual_review, app.manual_review_reason = True, reason
        set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "system", "Demo: not sent")
        return app
    if not _within_limits(db, user, app):
        return app
    blocker = direct_submit_blocker(user, app)
    if blocker:  # e.g. the Internshala bot was turned off, or its login expired, after you approved
        app.needs_manual_review = True
        app.manual_review_reason = blocker
        set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent", blocker)
        return app
    internshala = is_internshala_job(app.job)
    platform = "internshala" if internshala else (app.ats_platform or ATSPlatform.UNKNOWN).value
    allowed, reason = rate_limiter.can_apply(str(user.id), platform, guardrails.daily_cap(user))
    wait = int(rate_limiter.cooldown_seconds(platform)) + 30
    if allowed and internshala and (reason := _internshala_over_limit(db, user)):
        allowed, wait = False, INTERNSHALA_RECHECK_SECONDS
    if not allowed:
        app.notes = f"Waiting: {reason}"
        from app.worker.dispatch import enqueue

        enqueue("submit_application", str(app.id), countdown=wait, after_commit=db)
        return app

    dry = guardrails.dry_run(user)
    run = RunLog(db, user, "apply", "user")
    run.log(f"{'Dry run (nothing is sent)' if dry else 'Submitting'}: {app.job.role_title} @ {app.job.company_name} via {platform}")
    checkpoint(db)
    # Dry run fills the form and takes the screenshot through the same path as staging, so no submitter
    # ever reaches its Submit click
    result = _run_submitter(db, user, app, submit=not dry)
    if result.form_screenshot and not result.session_expired:  # proof of what was (or would have been) sent
        _replace_file(app, "form_screenshot_url", _store(user.id, "screenshots", result.form_screenshot, "png", "image/png"))
    if result.screenshot and not dry and result.screenshot is not result.form_screenshot:
        key = _store(user.id, "screenshots", result.screenshot, "png", "image/png")
        if result.success:
            app.confirmation_screenshot_url = key
    if result.answers:
        app.custom_answers = _merge_answers(app.custom_answers, result.answers)
    if dry and result.success:
        app.form_fields = result.fields
        app.needs_manual_review = True
        app.manual_review_reason = ("Dry run: the form was filled and screenshotted, but Submit was not clicked. "
                                    "Turn off Dry run in Settings to send it for real."
                                    + (f" Also: {result.review_reason}" if result.review_reason else ""))
        app.staged_at = datetime.now(UTC)
        set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "system", "Dry run completed: nothing was sent")
        run.log("Dry run — form filled, Submit not clicked")
        run.finish("completed")
        return app

    if result.success and result.stage == "submitted":
        rate_limiter.record_application(str(user.id), platform)
        app.confirmation_number = result.confirmation_number
        set_status(db, app, ApplicationStatus.APPLIED, "agent", "Submitted successfully")
        run.run.applications_submitted = 1
        run.log("Application submitted ✅")
        run.finish("completed")
        notify(db, user, "application_submitted", f"✅ Applied: {app.job.role_title} @ {app.job.company_name}",
               "Your application was submitted.", link=f"/dashboard/applications/{app.id}")
        return app
    if result.success and result.stage == "dry_run":
        app.needs_manual_review = True
        app.manual_review_reason = "Dry-run mode (SUBMISSION_DRY_RUN=true): the final Submit click was skipped."
        set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "system", "Dry run completed")
        run.log("Dry run — submit click skipped")
        run.finish("completed")
        return app

    if result.stage == "already_applied":
        _internshala_already_applied(db, user, app, run, by_agent=bool(app.retry_count))
        run.finish("completed")
        return app
    app.retry_count = (app.retry_count or 0) + 1
    app.error_log = f"{datetime.now(UTC).isoformat()} submit: {result.error}"
    run.log(f"Submission failed: {result.error}", level="error")
    if result.session_expired and internshala:  # back to your review queue; Submit works again after a re-sync
        _internshala_session_expired(db, user)
        app.needs_manual_review = True
        app.manual_review_reason = "Internshala session expired — open Internshala in Chrome, click Sync in the extension, then submit again."
        set_status(db, app, ApplicationStatus.PENDING_APPROVAL, "agent", "Internshala session expired")
        run.finish("failed")
        return app
    if result.session_expired:
        notify(db, user, "session_expired", "LinkedIn session expired",
               "Re-sync your LinkedIn session to finish this Easy Apply.", link="/dashboard/settings")
    if app.retry_count <= 1 and not result.session_expired and result.stage == "failed":
        run.log("Retrying once")
        run.finish("failed")
        from app.worker.dispatch import enqueue

        enqueue("submit_application", str(app.id), countdown=30, after_commit=db)
        return app
    set_status(db, app, ApplicationStatus.FAILED, "agent", f"Submission failed: {result.error}")
    run.finish("failed")
    notify(db, user, "application_failed", f"Could not submit {app.job.company_name} application",
           f"{result.error}. Open the application to apply manually.", link=f"/dashboard/applications/{app.id}")
    return app
