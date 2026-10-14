"""The TinyFish Finder: a search by role / location / keywords that needs no resume.

A Finder search is an ``agent_runs`` row (``run_type="tinyfish_search"``). Its ``progress`` JSON holds the live state
(see :class:`FinderTracker`) and, once the search ends, the ranked ``results`` (each with a private ``job`` payload
that ``save_listings`` turns back into a job). ``run_out`` strips ``results`` so run lists stay small.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import threading
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import SessionLocal, checkpoint
from app.models.agent_run import AgentRun
from app.models.enums import ApplicationStatus, ATSPlatform, ExperienceLevel, JobType
from app.models.user import User
from app.scrapers.base import ScrapedJob, ScraperError, SearchQuery
from app.scrapers.tinyfish import DiscoveryHooks, TinyFishScraper
from app.scrapers.tinyfish_parse import canonical_url
from app.services import agent_orchestrator as orch
from app.services.application_service import set_status
from app.services.notifier import push_update
from app.services.tinyfish_client import NOT_CONFIGURED_HINT, get_client
from app.services.tinyfish_ranker import Ranked, rank_jobs
from app.services.tinyfish_types import AgentEvent

logger = logging.getLogger(__name__)

RUN_TYPE = "tinyfish_search"
FLUSH_INTERVAL = 0.8
CANCEL_CHECK_INTERVAL = 1.0
RAW_KEYS = ("discovered_by", "tinyfish", "sponsorship", "match", "board_token", "listing_source")
STAGES = (("search", "search"), ("read", "fetch"), ("agent", "agent"), ("rank", "rank"))  # (hook stage, stage name)
PHASES = {"search": "searching", "read": "reading", "agent": "agent", "rank": "ranking"}
RANGES = {"search": (2, 35), "read": (35, 75), "agent": (75, 92), "rank": (92, 99)}
MAX_DESCRIPTION = 6000
STATUS_LABELS = {"COMPLETED": "completed"}


def unavailable_hint() -> str | None:
    """Why TinyFish can't be used (None when it can)."""
    if settings.tinyfish_enabled:
        return None
    if (settings.TINYFISH_API_KEY or "").strip():
        return "TinyFish is switched off (TINYFISH_ENABLED=false)."
    return NOT_CONFIGURED_HINT


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# --------------------------------------------------------------------------- job <-> payload
def job_to_payload(job: ScrapedJob) -> dict[str, Any]:
    raw = {k: v for k, v in (job.raw or {}).items() if k in RAW_KEYS}
    return {
        "company_name": job.company_name, "role_title": job.role_title, "description": (job.description or "")[:MAX_DESCRIPTION],
        "source_url": job.source_url, "source_platform": job.source_platform.value, "application_url": job.application_url,
        "location": job.location, "is_remote": job.is_remote, "job_type": job.job_type.value if job.job_type else None,
        "experience_level": job.experience_level.value if job.experience_level else None,
        "salary_min": job.salary_min, "salary_max": job.salary_max, "salary_currency": job.salary_currency,
        "posted_date": job.posted_date.isoformat() if job.posted_date else None,
        "deadline_date": job.deadline_date.isoformat() if job.deadline_date else None,
        "external_id": job.external_id, "company_logo_url": job.company_logo_url, "company_domain": job.company_domain,
        "requirements": job.requirements, "raw": raw,
    }


def payload_to_job(p: dict[str, Any]) -> ScrapedJob:
    def day(value: Any) -> date | None:
        return date.fromisoformat(value) if value else None

    return ScrapedJob(
        company_name=p["company_name"], role_title=p["role_title"], description=p.get("description") or "",
        source_url=p["source_url"], source_platform=ATSPlatform(p.get("source_platform") or "custom"),
        application_url=p.get("application_url"), location=p.get("location"), is_remote=bool(p.get("is_remote")),
        job_type=JobType(p["job_type"]) if p.get("job_type") else None,
        experience_level=ExperienceLevel(p["experience_level"]) if p.get("experience_level") else None,
        salary_min=p.get("salary_min"), salary_max=p.get("salary_max"), salary_currency=p.get("salary_currency"),
        posted_date=day(p.get("posted_date")), deadline_date=day(p.get("deadline_date")), external_id=p.get("external_id"),
        company_logo_url=p.get("company_logo_url"), company_domain=p.get("company_domain"), requirements=p.get("requirements"),
        raw=dict(p.get("raw") or {}))


def listing_id(job: ScrapedJob) -> str:
    return hashlib.sha1(canonical_url(job.source_url).encode(), usedforsecurity=False).hexdigest()[:16]  # an id, not security


def to_listing(r: Ranked) -> dict[str, Any]:
    job = r.job
    apply_url = job.application_url or job.source_url
    via = ((job.raw or {}).get("tinyfish") or {}).get("via") or []
    return {
        "id": listing_id(job), "title": job.role_title, "company": job.company_name, "location": job.location,
        "remote": job.is_remote, "job_type": job.job_type.value if job.job_type else None,
        "seniority": job.experience_level.value if job.experience_level else None,
        "posted_date": job.posted_date.isoformat() if job.posted_date else None,
        "salary": {"min": job.salary_min, "max": job.salary_max, "currency": job.salary_currency},
        "apply_url": apply_url, "source_url": job.source_url, "source_host": (urlparse(apply_url).hostname or "").lower(),
        "ats": job.source_platform.value, "discovered_by": list(via), "snippet": (job.description or "")[:280],
        "visa": {"status": r.visa_status, "evidence": r.visa_evidence},
        "score": r.score,
        "reasons": [{"factor": x.factor, "points": x.points, "max": x.max_points, "note": x.note} for x in r.reasons],
        "saved": False, "application_id": None,
        "job": job_to_payload(job),
    }


def public_listing(listing: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in listing.items() if k != "job"}


# --------------------------------------------------------------------------- live state
class FinderTracker:
    """The state of one search. Thread-safe: Agent events arrive from worker threads."""

    def __init__(self, run: AgentRun, request: dict[str, Any], usage: Any = None) -> None:
        self.run_id = run.id
        self.user_id = str(run.user_id)
        self.request = request
        self.usage = usage
        self.started_at = run.started_at or datetime.now(UTC)
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.status = "running"
        self.stage: str | None = None
        self.percent = 1
        self.message = "Starting"
        self.stage_status = {name: "pending" for _, name in STAGES}
        self.stage_detail: dict[str, str | None] = {name: None for _, name in STAGES}
        self.agent_runs: dict[str, dict[str, Any]] = {}
        self.agent_purpose: str | None = None
        self.agent_streaming_url: str | None = None
        self.counts: dict[str, int] = {}
        self.notes: list[str] = []
        self.error: str | None = None
        self.completed_at: datetime | None = None
        self.duration: int | None = None
        self.cancelled = False
        self._last_flush = 0.0
        self._last_check = 0.0

    # ---- updates
    def stage_update(self, stage: str, message: str, done: int, total: int) -> None:
        with self.lock:
            if stage not in dict(STAGES):
                return
            self.stage = stage
            name = dict(STAGES)[stage]
            order = [s for s, _ in STAGES]
            for _s, n in STAGES[: order.index(stage)]:
                if self.stage_status[n] == "pending":
                    self.stage_status[n] = "skipped"
                elif self.stage_status[n] == "running":
                    self.stage_status[n] = "done"
            self.stage_status[name] = "running"
            self.stage_detail[name] = f"{done} / {total}" if total else message
            lo, hi = RANGES[stage]
            self.percent = max(self.percent, int(lo + (hi - lo) * (done / total if total else 0)))
            self.message = message
        self.flush()

    def agent_event(self, url: str, event: AgentEvent) -> None:
        with self.lock:
            run = self.agent_runs.setdefault(url, {"url": url, "status": "running", "steps": 0, "found": 0,
                                                   "streaming_url": None, "error": None})
            if event.type == "STREAMING_URL" and event.streaming_url and event.streaming_url.startswith("https://"):
                run["streaming_url"] = event.streaming_url[:500]
                self.agent_streaming_url = run["streaming_url"]
            elif event.type == "PROGRESS":
                run["steps"] += 1
                if event.purpose:
                    self.agent_purpose = event.purpose[:200]
            elif event.type == "COMPLETE":
                run["status"] = "completed" if (event.status or "").upper() == "COMPLETED" else "failed"
        self.flush()

    def note(self, text: str) -> None:
        with self.lock:
            if len(self.notes) < 30 and text not in self.notes:
                self.notes.append(text[:300])

    # ---- serialising
    def state(self) -> dict[str, Any]:
        with self.lock:
            runs = [dict(r) for r in self.agent_runs.values()]
            usage = self.usage.as_dict() if self.usage is not None else dict.fromkeys(
                ("search_calls", "fetch_calls", "fetch_urls", "agent_runs", "agent_steps", "errors"), 0)
            endpoints = self.usage.endpoints_used() if self.usage is not None else []
            active = any(r["status"] == "running" for r in runs) and self.status == "running"
            return {
                "search_id": str(self.run_id), "status": self.status,
                "phase": "done" if self.status != "running" else PHASES.get(self.stage or "", "searching"),
                "percent": 100 if self.status == "completed" else self.percent, "message": self.message,
                "started_at": iso(self.started_at), "completed_at": iso(self.completed_at), "duration_seconds": self.duration,
                "request": self.request, "usage": usage, "endpoints_used": endpoints,
                "agent": {"active": active, "purpose": self.agent_purpose if active else None,
                          "streaming_url": self.agent_streaming_url if active else None, "runs": runs},
                "stages": [{"name": n, "status": self.stage_status[n], "detail": self.stage_detail[n]} for _, n in STAGES],
                "counts": dict(self.counts), "notes": list(self.notes), "error": self.error,
            }

    def flush(self, force: bool = False) -> None:
        """Write the state to the run row (own short session: safe from any thread) and push it over the WebSocket."""
        now = time.monotonic()
        if not force and now - self._last_flush < FLUSH_INTERVAL:
            return
        with self.write_lock:
            self._last_flush = now
            state = self.state()
            try:
                with SessionLocal() as s:
                    res = s.execute(update(AgentRun).where(AgentRun.id == self.run_id, AgentRun.status == "running")
                                    .values(progress=state))
                    s.commit()
                    if res.rowcount == 0:  # no longer running: you pressed Stop
                        self.cancelled = True
            except Exception:  # progress must never break a search
                logger.debug("finder progress write failed", exc_info=True)
        push_update(self.user_id, "tinyfish_progress", {"search_id": str(self.run_id), "state": state})

    def should_cancel(self) -> bool:
        if self.cancelled:
            return True
        now = time.monotonic()
        if now - self._last_check >= CANCEL_CHECK_INTERVAL:
            self._last_check = now
            try:
                with SessionLocal() as s:
                    if s.scalar(select(AgentRun.status).where(AgentRun.id == self.run_id)) != "running":
                        self.cancelled = True
            except Exception:
                logger.debug("finder cancel check failed", exc_info=True)
        return self.cancelled


# --------------------------------------------------------------------------- create / execute
def initial_request(body: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The request as the search will run it: no Agent when it is switched off or in DEMO_MODE; runs clamped."""
    req = dict(body)
    notes: list[str] = []
    runs = orch.clamp_agent_runs(req.get("agent_runs"))
    if settings.DEMO_MODE:
        runs = 0
        req["use_agent"] = False
        notes.append("Agent skipped: DEMO_MODE (the TinyFish Agent never runs in the demo)")
    elif not req.get("use_agent", True):
        runs = 0
    req["agent_runs"] = runs
    return req, notes


def running_search(db: Session, user: User) -> AgentRun | None:
    window = settings.TINYFISH_FINDER_TIMEOUT_SECONDS * 2 + 60
    return db.scalar(select(AgentRun).where(AgentRun.user_id == user.id, AgentRun.run_type == RUN_TYPE,
                                            AgentRun.status == "running",
                                            AgentRun.started_at > datetime.now(UTC) - timedelta(seconds=window)))


def create_search(db: Session, user: User, body: dict[str, Any]) -> AgentRun:
    hint = unavailable_hint()
    if hint:
        raise HTTPException(503, hint)
    if user.automation_paused_at is not None:
        raise HTTPException(409, "HireFlow is paused. Resume it to search.")
    if running_search(db, user):
        raise HTTPException(409, "A TinyFish search is already running")
    request, notes = initial_request(body)
    run = AgentRun(user_id=user.id, run_type=RUN_TYPE, trigger="user", status="running", started_at=datetime.now(UTC),
                   log=[{"ts": datetime.now(UTC).isoformat(), "level": "info", "message": "TinyFish search queued"}])
    db.add(run)
    db.flush()
    tracker = FinderTracker(run, request)
    tracker.message = "Queued"
    tracker.notes = notes
    run.progress = tracker.state()
    return run


def build_query(request: dict[str, Any]) -> SearchQuery:
    return SearchQuery(
        keywords=list(request.get("roles") or []), locations=list(request.get("locations") or []),
        remote=bool(request.get("remote")), posted_within_days=int(request.get("posted_within_days") or 30),
        limit=int(request.get("limit") or 30), sources={"career_pages": list(request.get("career_pages") or [])},
        seniority=list(request.get("seniority") or []), needs_sponsorship=bool(request.get("visa_sponsorship")),
        skills=list(request.get("keywords") or []), companies=list(request.get("companies") or []),
        agent_runs=int(request.get("agent_runs") or 0))


def execute(db: Session, user: User, run: AgentRun) -> None:
    """Run one search to its end (called by the worker task). Never raises: failures become status ``failed``."""
    prior = dict(run.progress or {})
    request = prior.get("request") or {}
    if run.status != "running":  # stopped before the worker got to it
        run.progress = {**prior, "status": "cancelled", "phase": "done", "message": "Stopped"}
        return
    log = orch.RunLog(db, user, RUN_TYPE, "user", existing=run)
    log.log("TinyFish search started")
    checkpoint(db)  # no open write transaction while the network calls run
    scraper = None
    try:
        client = get_client(str(user.id))
        scraper = TinyFishScraper(client=client)
        client_usage = client.usage
    except ScraperError as exc:
        client_usage = None
        error: str | None = str(exc)
    else:
        error = None
    tracker = FinderTracker(run, request, client_usage)
    tracker.started_at = run.started_at or datetime.now(UTC)
    tracker.notes = list(prior.get("notes") or [])
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    status = "failed" if error else "completed"
    tracker.error = error
    if scraper is not None:
        query = build_query(request)
        hooks = DiscoveryHooks(stage=tracker.stage_update, agent_event=tracker.agent_event, should_cancel=tracker.should_cancel)
        deadline = started + settings.TINYFISH_FINDER_TIMEOUT_SECONDS
        try:
            found = scraper.discover(query, hooks=hooks, agent_budget=query.agent_runs, deadline=deadline)
        except ScraperError as exc:
            tracker.error, status, found = str(exc), "failed", None
        except Exception as exc:
            logger.exception("Finder search %s failed", run.id)
            tracker.error, status, found = f"Search failed: {type(exc).__name__}", "failed", None
        if found is not None:
            tracker.stage_update("rank", "Ranking and removing duplicates", 0, 1)
            if tracker.should_cancel():
                status = "cancelled"
            elif time.monotonic() > deadline:
                tracker.note(f"Stopped at the {settings.TINYFISH_FINDER_TIMEOUT_SECONDS}s limit: ranked what was found so far")
            for note in found.notes:
                tracker.note(note)
            ranked_all = rank_jobs(found.jobs, query, drop_no_sponsorship=False)
            refused = [r for r in ranked_all if query.needs_sponsorship and r.visa_status == "no_sponsorship"]
            kept = [r for r in ranked_all if r not in refused]
            min_score = int(request.get("min_score") or 0)
            above = [r for r in kept if r.score >= min_score]
            top = above[: int(request.get("limit") or 30)]
            results = [to_listing(r) for r in top]
            with tracker.lock:
                tracker.counts = {**found.counts, "listings": len(found.jobs) + found.counts.get("duplicates", 0),
                                  "after_dedupe": len(found.jobs), "excluded_no_sponsorship": len(refused),
                                  "below_min_score": len(kept) - len(above), "returned": len(results)}
                tracker.agent_runs = {}
                for info in found.agent_runs:
                    tracker.agent_runs[info.url] = {
                        "url": info.url, "status": "completed" if info.status == "COMPLETED" and not info.error else "failed",
                        "steps": info.steps, "found": info.found,
                        "streaming_url": info.streaming_url if (info.streaming_url or "").startswith("https://") else None,
                        "error": info.error}
                tracker.stage_detail["search"] = f"{found.counts.get('raw_hits', 0)} results"
                tracker.stage_detail["fetch"] = f"{found.counts.get('pages_read', 0)} pages read"
                steps = found.usage.get("agent_steps", 0)
                tracker.stage_detail["agent"] = f"{len(found.agent_runs)} runs, {steps} steps" if found.agent_runs else None
                tracker.stage_detail["rank"] = f"{len(results)} listings"
                for _, n in STAGES:
                    tracker.stage_status[n] = "done" if tracker.stage_status[n] == "running" else tracker.stage_status[n]
                    if tracker.stage_status[n] == "pending":
                        tracker.stage_status[n] = "skipped"
            tracker.stage_status["rank"] = "done"
    if status == "failed":
        with tracker.lock:
            for _, n in STAGES:
                if tracker.stage_status[n] == "running":
                    tracker.stage_status[n] = "failed"
    # a Stop pressed at the very end still counts
    if status == "completed" and db.scalar(select(AgentRun.status).where(AgentRun.id == run.id)) == "cancelled":
        status = "cancelled"
    tracker.status = status
    tracker.message = {"completed": f"Found {len(results)} listing{'s' if len(results) != 1 else ''}", "cancelled": "Stopped: ranked what was found",
                       "failed": tracker.error or "Search failed"}[status]
    tracker.completed_at = datetime.now(UTC)
    tracker.duration = int(time.monotonic() - started)
    final = tracker.state()
    if status != "failed":
        final["results"] = results
    run.progress = final
    run.jobs_discovered = len(results)
    if status == "failed":
        log.log(f"Search failed: {tracker.error}", level="error")
    else:
        log.log(f"Found {len(results)} listings" + (" (stopped early)" if status == "cancelled" else ""))
    log.finish(status)
    checkpoint(db)
    push_update(str(user.id), "tinyfish_progress", {"search_id": str(run.id), "state": {k: v for k, v in final.items() if k != "results"}})


# --------------------------------------------------------------------------- reading
def search_state(run: AgentRun) -> dict[str, Any]:
    """The state a client sees: stored progress without the private job payloads."""
    state = dict(run.progress or {})
    state.setdefault("search_id", str(run.id))
    state.setdefault("request", {})
    if state.get("status") == "running":
        if run.status == "cancelled":
            stuck = run.completed_at is not None and datetime.now(UTC) - run.completed_at > timedelta(seconds=90)
            if stuck:  # the worker never reported back
                state.update(status="cancelled", phase="done", message="Stopped", completed_at=iso(run.completed_at))
            else:
                state["message"] = "Stopping: ranking what was found so far"
        elif run.status == "running" and run.started_at and datetime.now(UTC) - run.started_at > timedelta(
                seconds=settings.TINYFISH_FINDER_TIMEOUT_SECONDS * 2 + 60):
            state.update(status="failed", phase="done", error="The search did not finish", message="The search did not finish")
        elif run.status in ("failed", "completed"):
            state.update(status=run.status, phase="done")
    if isinstance(state.get("results"), list):
        state["results"] = [public_listing(x) for x in state["results"]]
    return state


# --------------------------------------------------------------------------- save to Swipe Review
def save_listings(db: Session, user: User, run: AgentRun, ids: list[str]) -> dict[str, Any]:
    progress = dict(run.progress or {})
    if run.status == "running" or progress.get("status") == "running":
        raise HTTPException(409, "This search hasn't finished")
    listings = progress.get("results")
    if not isinstance(listings, list):
        raise HTTPException(404, "Listing not found in this search")
    listings = copy.deepcopy(listings)  # (a changed copy: an in-place edit would not be seen as a change)
    by_id = {x["id"]: x for x in listings}
    unique = list(dict.fromkeys(ids))
    missing = [i for i in unique if i not in by_id]
    if missing:
        raise HTTPException(404, f"Listing not found in this search: {missing[0]}")
    master = orch.get_master_resume(db, user)
    request = progress.get("request") or {}
    wanted_skills = [s for s in request.get("keywords") or [] if s]
    items: list[dict[str, Any]] = []
    saved = already = skipped = 0
    for lid in unique:
        listing = by_id[lid]
        job, _ = orch.upsert_job(db, payload_to_job(listing["job"]))
        app, created = orch.ensure_application(db, user, job)
        if created:
            if master is not None:
                orch.evaluate_application(db, user, app, master, use_llm=False)
            else:
                score = int(listing["score"])
                text = f"{job.role_title} {job.description or ''}".lower()
                strong = [s for s in wanted_skills if s.lower() in text]
                heads_up = []
                visa = listing.get("visa") or {}
                if request.get("visa_sponsorship") and visa.get("status") != "sponsors":
                    heads_up.append(visa.get("evidence") or "The posting doesn't mention visa sponsorship")
                top = "; ".join(f"{r['note']}" for r in listing["reasons"][:3])
                reasoning = f"TinyFish preference match {score}/100 (no resume yet): {top}"
                app.match_score = score
                app.match_reasoning = reasoning
                app.match_details = {"match_score": score, "reasoning": reasoning, "method": "tinyfish_preferences",
                                     "strong_matches": strong, "missing_skills": [], "heads_up": heads_up}
                set_status(db, app, ApplicationStatus.MATCHED, "agent", f"TinyFish preference match {score}")
        if created and app.status == ApplicationStatus.SKIPPED:
            skipped += 1
        elif created:
            saved += 1
        else:
            already += 1
        listing["saved"] = True
        listing["application_id"] = str(app.id)
        items.append({"listing_id": lid, "job_id": str(job.id), "application_id": str(app.id), "created": created})
    run.progress = {**progress, "results": listings}
    db.flush()
    push_update(str(user.id), "application_updated", {"source": "tinyfish_save", "count": saved})
    return {"search_id": str(run.id), "saved": saved, "already_in_deck": already, "skipped": skipped, "items": items,
            "review_url": "/dashboard/review"}


def parse_run_id(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
