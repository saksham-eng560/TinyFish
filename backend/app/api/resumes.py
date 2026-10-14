"""Resume management: upload + parse master resume, edit, render PDFs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, limiter, parse_uuid
from app.api.serializers import resume_out
from app.config import settings
from app.core.storage import get_storage, user_prefix
from app.models.resume import Resume
from app.models.user import User
from app.schemas.resume import ResumeCreateFromText, ResumeUpdate
from app.schemas.resume_content import ResumeContent, normalize_resume
from app.services.embeddings import embed_text
from app.services.pdf_generator import TEMPLATES, render_resume_pdf
from app.services.resume_parser import ResumeParseError, extract_text, parse_resume_text
from app.services.uploads import MAX_RESUME_BYTES, UploadRejected, check_resume_upload

router = APIRouter(prefix="/resumes", tags=["resumes"])


def _embed(resume: Resume) -> None:
    rc = ResumeContent.model_validate(resume.parsed_content)
    resume.skills_embedding = embed_text(f"{rc.skills_text()}\n{rc.full_text()}")


def _make_master(db: DB, user_id: uuid.UUID, resume: Resume) -> None:
    db.execute(update(Resume).where(Resume.user_id == user_id, Resume.id != resume.id).values(is_master=False))
    resume.is_master = True


def _get_owned(db: DB, user_id: uuid.UUID, resume_id: str) -> Resume:
    resume = db.get(Resume, parse_uuid(resume_id))
    if resume is None or resume.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resume not found")
    return resume


def _sync_profile(user, content: dict) -> None:  # type: ignore[no-untyped-def]
    info = content.get("personal_info") or {}
    if info.get("phone") and not user.phone:
        user.phone = info["phone"]
    if info.get("location") and not user.location:
        user.location = info["location"]
    if info.get("linkedin") and not user.linkedin_url:
        user.linkedin_url = info["linkedin"]
    if info.get("github") and not user.github_url:
        user.github_url = info["github"]
    if info.get("portfolio") and not user.portfolio_url:
        user.portfolio_url = info["portfolio"]


@router.post("/upload", status_code=201)
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def upload_resume(request: Request, user: CurrentUser, db: DB, file: UploadFile = File(...), is_master: bool = Form(True),
                        label: str | None = Form(None)) -> dict:
    data = await file.read(MAX_RESUME_BYTES + 1)  # never more than the limit (plus one byte to tell) into memory
    if len(data) > MAX_RESUME_BYTES:
        raise HTTPException(413, "That file is over 5 MB. Export a smaller PDF and try again.")
    try:
        checked = check_resume_upload(file.filename, data)
        text = extract_text(checked.filename, data)
    except UploadRejected as exc:
        raise HTTPException(422, str(exc)) from exc
    except ResumeParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    # LLM parsing is blocking I/O; run it in a worker thread
    import anyio

    parsed, method = await anyio.to_thread.run_sync(parse_resume_text, text)
    if not parsed["personal_info"].get("email"):
        parsed["personal_info"]["email"] = user.email
    if not parsed["personal_info"].get("name"):
        parsed["personal_info"]["name"] = user.full_name
    # Stored under a random name with the type we detected: never the name or type the browser sent
    key = get_storage().save(f"{user_prefix(user.id)}/uploads/{uuid.uuid4().hex}.{checked.extension}", data,
                             checked.content_type)
    resume = Resume(
        user_id=user.id, label=(label or f"Master resume ({checked.filename})")[:255], original_file_url=key,
        original_filename=checked.filename, raw_text=text, parsed_content=parsed, is_master=False,
    )
    db.add(resume)
    db.flush()
    _embed(resume)
    if is_master:
        _make_master(db, user.id, resume)
    _sync_profile(user, parsed)
    db.flush()
    return {**resume_out(resume), "parse_method": method}


def create_resume_from_text(db: Session, user: User, text: str, label: str | None = None,
                            is_master: bool = True) -> tuple[Resume, str]:
    """Parse pasted resume text into a resume (the master one by default). Returns it and the parse method."""
    parsed, method = parse_resume_text(text)
    if not parsed["personal_info"].get("email"):
        parsed["personal_info"]["email"] = user.email
    if not parsed["personal_info"].get("name"):
        parsed["personal_info"]["name"] = user.full_name
    resume = Resume(user_id=user.id, label=label or "Master resume", raw_text=text, parsed_content=parsed)
    db.add(resume)
    db.flush()
    _embed(resume)
    if is_master:
        _make_master(db, user.id, resume)
    _sync_profile(user, parsed)
    return resume, method


@router.post("/from-text", status_code=201)
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
def create_from_text(request: Request, body: ResumeCreateFromText, user: CurrentUser, db: DB) -> dict:
    resume, method = create_resume_from_text(db, user, body.text, body.label, body.is_master)
    return {**resume_out(resume), "parse_method": method}


@router.get("")
def list_resumes(user: CurrentUser, db: DB, tailored: bool | None = None) -> dict:
    query = select(Resume).where(Resume.user_id == user.id, Resume.is_active.is_(True))
    if tailored is True:
        query = query.where(Resume.parent_resume_id.is_not(None))
    elif tailored is False:
        query = query.where(Resume.parent_resume_id.is_(None))
    resumes = db.scalars(query.order_by(Resume.is_master.desc(), Resume.created_at.desc()).limit(200)).all()
    return {"items": [resume_out(r, include_content=False) for r in resumes]}


@router.get("/master")
def get_master(user: CurrentUser, db: DB) -> dict:
    resume = db.scalar(select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True)))
    if resume is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No master resume yet")
    return resume_out(resume)


@router.get("/{resume_id}")
def get_resume(resume_id: str, user: CurrentUser, db: DB) -> dict:
    return resume_out(_get_owned(db, user.id, resume_id))


@router.put("/{resume_id}")
def update_resume(resume_id: str, body: ResumeUpdate, user: CurrentUser, db: DB) -> dict:
    resume = _get_owned(db, user.id, resume_id)
    if body.parsed_content is not None:
        resume.parsed_content = normalize_resume(body.parsed_content)
        resume.version = (resume.version or 1) + 1
        resume.pdf_url = None
        _embed(resume)
    if body.label is not None:
        resume.label = body.label
    return resume_out(resume)


@router.post("/{resume_id}/set-master")
def set_master(resume_id: str, user: CurrentUser, db: DB) -> dict:
    resume = _get_owned(db, user.id, resume_id)
    _make_master(db, user.id, resume)
    return resume_out(resume)


@router.get("/{resume_id}/pdf")
def resume_pdf(resume_id: str, user: CurrentUser, db: DB, template: str = "classic", download: bool = False) -> Response:
    resume = _get_owned(db, user.id, resume_id)
    if template not in TEMPLATES:
        template = "classic"
    pdf = render_resume_pdf(resume.parsed_content, template=template)
    name = (resume.parsed_content.get("personal_info", {}).get("name") or "resume").replace(" ", "_")
    disposition = "attachment" if download else "inline"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disposition}; filename="{name}_Resume.pdf"'})


@router.delete("/{resume_id}")
def delete_resume(resume_id: str, user: CurrentUser, db: DB) -> dict:
    resume = _get_owned(db, user.id, resume_id)
    if resume.is_master:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Set another resume as master before deleting this one")
    resume.is_active = False
    return {"ok": True}
