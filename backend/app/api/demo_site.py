"""The bundled demo careers site (DEMO_MODE only): a careers page with JSON-LD postings, application
forms, and a submit endpoint that records what was sent and shows a confirmation page."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.config import settings
from app.services import demo_site

router = APIRouter(prefix="/demo-careers", tags=["demo"], include_in_schema=False)


def _require_demo() -> None:
    if not settings.DEMO_MODE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


def _post(posting_id: str) -> dict:
    post = demo_site.by_id(posting_id)
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such posting")
    return post


@router.get("", response_class=HTMLResponse)
def careers() -> str:
    _require_demo()
    return demo_site.careers_html(settings.demo_site_url)


@router.get("/jobs/{posting_id}", response_class=HTMLResponse)
def posting(posting_id: str) -> str:
    _require_demo()
    return demo_site.form_html(_post(posting_id), settings.demo_site_url)


@router.post("/jobs/{posting_id}/submit", response_class=HTMLResponse)
async def submit(posting_id: str, request: Request) -> str:
    _require_demo()
    post = _post(posting_id)
    form = await request.form()
    fields = {key: (f"{value.filename} ({value.size} bytes)" if hasattr(value, "filename") else str(value)[:2000])
              for key, value in form.multi_items()}
    return demo_site.confirmation_html(demo_site.record(posting_id, fields), post)


@router.get("/submissions")
def submissions() -> dict:
    """How many applications the demo site has received (their content isn't shown: it's other visitors' data)."""
    _require_demo()
    return {"count": len(demo_site.SUBMISSIONS)}
