"""TinyFish Finder API: find openings with TinyFish Search + Fetch + Agent, no resume needed."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, limiter
from app.config import settings
from app.models.agent_run import AgentRun
from app.schemas.tinyfish import FinderRequest, SaveRequest
from app.services import tinyfish_finder as finder
from app.services.tinyfish_client import usage_today
from app.worker.dispatch import enqueue

router = APIRouter(prefix="/tinyfish", tags=["tinyfish"])


def _get_search(db: DB, user: CurrentUser, search_id: str) -> AgentRun:
    run_id = finder.parse_run_id(search_id)
    run = db.scalar(select(AgentRun).where(AgentRun.id == run_id)) if run_id else None
    if run is None or run.user_id != user.id or run.run_type != finder.RUN_TYPE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Search not found")
    return run


@router.get("/status")
def tinyfish_status(user: CurrentUser) -> dict:
    enabled = settings.tinyfish_enabled
    agent = settings.tinyfish_agent_allowed
    return {
        "configured": bool((settings.TINYFISH_API_KEY or "").strip()),
        "enabled": enabled,
        "agent_enabled": agent,
        "agent_runs_max": settings.TINYFISH_AGENT_MAX_RUNS_PER_SCAN,
        "demo_mode": settings.DEMO_MODE,
        "endpoints": (["search", "fetch"] + (["agent"] if agent else [])) if enabled else [],
        "hint": finder.unavailable_hint(),
        "usage_today": usage_today(str(user.id)) if enabled else {},
    }


@router.post("/search", status_code=202)
@limiter.limit(settings.RATE_LIMIT_TINYFISH)
def start_search(request: Request, body: FinderRequest, user: CurrentUser, db: DB) -> dict:
    run = finder.create_search(db, user, body.model_dump())
    enqueue("tinyfish_search", str(user.id), str(run.id), after_commit=db)
    return {"search_id": str(run.id), "status": "running", "started_at": finder.iso(run.started_at)}


@router.get("/search/{search_id}")
def get_search(search_id: str, user: CurrentUser, db: DB) -> dict:
    return finder.search_state(_get_search(db, user, search_id))


@router.post("/save")
def save(body: SaveRequest, user: CurrentUser, db: DB) -> dict:
    return finder.save_listings(db, user, _get_search(db, user, body.search_id), body.listing_ids)
