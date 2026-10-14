"""Security and privacy (docs/HIREFLOW_PLAN.md §5.3): unsafe production configs refused, CSRF and HTTPS,
uploads checked by their real type, stricter rate limits, no personal data in logs, and "Download my
data" / "Delete my account" covering every row and file."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.config import DEFAULT_SECRET, Settings, settings
from app.core.database import Base, SessionLocal, engine
from app.core.log_privacy import PIIFilter, mask_pii
from app.core.storage import get_storage, user_prefix
from app.models.user import User
from app.services.pdf_generator import render_resume_pdf
from app.services.uploads import UploadRejected, check_resume_upload, clean_filename
from tests.conftest import SAMPLE_RESUME_TEXT

GOOD_KEY = "q0X1y2Z3a4B5c6D7e8F9g0H1i2J3k4L5m6N7o8P9q0Y="
RESUME_PDF = render_resume_pdf({"personal_info": {"name": "Jane Doe", "email": "jane@example.com"},
                                "summary": "Backend engineer building Python APIs and data pipelines for four years.",
                                "experience": [{"company": "Acme Corp", "title": "Software Engineer",
                                                "bullets": ["Built REST APIs in Python and FastAPI serving 2M requests a day"]}],
                                "skills": {"technical": ["Python", "FastAPI", "PostgreSQL"]}})


def _docx(extra: dict[str, bytes] | None = None) -> bytes:
    import docx

    document = docx.Document()
    for line in SAMPLE_RESUME_TEXT.splitlines():
        document.add_paragraph(line)
    buf = io.BytesIO()
    document.save(buf)
    if not extra:
        return buf.getvalue()
    with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED) as archive:
        for name, data in extra.items():
            archive.writestr(name, data)
    return buf.getvalue()


# --------------------------------------------------------------------------- production config
@pytest.mark.parametrize(("overrides", "problem"), [
    ({"SECRET_KEY": DEFAULT_SECRET}, "SECRET_KEY"),
    ({"SECRET_KEY": "short"}, "SECRET_KEY"),
    ({"ENCRYPTION_KEY": None}, "ENCRYPTION_KEY"),
    ({"COOKIE_SECURE": False}, "COOKIE_SECURE"),
])
def test_production_refuses_to_start_with_an_unsafe_config(overrides: dict[str, Any], problem: str) -> None:
    good = {"ENVIRONMENT": "production", "SECRET_KEY": "x" * 48, "ENCRYPTION_KEY": GOOD_KEY, "COOKIE_SECURE": True}
    Settings(_env_file=None, **good).require_production_ready()  # a safe config starts
    with pytest.raises(RuntimeError, match=problem):
        Settings(_env_file=None, **{**good, **overrides}).require_production_ready()
    Settings(_env_file=None, **{**good, **overrides, "ENVIRONMENT": "development"}).require_production_ready()


# --------------------------------------------------------------------------- CSRF & HTTPS
def test_cookie_changes_from_another_site_are_blocked(auth_client: TestClient) -> None:
    body = {"preferences": {"dry_run": True}}
    evil = auth_client.put("/api/v1/users/me/preferences", json=body, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and evil.json()["detail"] == "Cross-site request blocked"
    referer = auth_client.put("/api/v1/users/me/preferences", json=body, headers={"Referer": "https://evil.example/page"})
    assert referer.status_code == 403
    dashboard = settings.FRONTEND_URL.rstrip("/")
    assert auth_client.put("/api/v1/users/me/preferences", json=body, headers={"Origin": dashboard}).status_code == 200
    assert auth_client.put("/api/v1/users/me/preferences", json=body).status_code == 200  # scripts send no Origin
    assert auth_client.get("/api/v1/auth/me", headers={"Origin": "https://evil.example"}).status_code == 200  # reads


def test_bearer_requests_are_not_csrf_checked(auth_client: TestClient) -> None:
    token = auth_client.post("/api/v1/auth/login", json={"email": "jane@example.com", "password": "supersecret1"}).json()["access_token"]
    auth_client.cookies.clear()
    r = auth_client.put("/api/v1/users/me/preferences", json={"preferences": {"dry_run": True}},
                        headers={"Authorization": f"Bearer {token}", "Origin": "chrome-extension://abc"})
    assert r.status_code == 200


def test_production_is_https_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    r = client.get("/api/v1/auth/config", headers={"X-Forwarded-Proto": "http"}, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"].startswith("https://")
    ok = client.get("/api/v1/auth/config", headers={"X-Forwarded-Proto": "https"})
    assert ok.status_code == 200 and "max-age" in ok.headers["strict-transport-security"]


# --------------------------------------------------------------------------- uploads
def test_upload_checks_the_real_file_type() -> None:
    assert check_resume_upload("cv.pdf", RESUME_PDF).kind == "pdf"
    assert check_resume_upload("CV.DOCX", _docx()).content_type.endswith("wordprocessingml.document")
    assert check_resume_upload("notes.txt", SAMPLE_RESUME_TEXT.encode()).kind == "txt"
    with pytest.raises(UploadRejected, match="really a DOCX"):
        check_resume_upload("resume.pdf", _docx())  # a renamed file
    with pytest.raises(UploadRejected, match="doesn't look like"):
        check_resume_upload("resume.pdf", b"MZ\x90\x00" + b"\x00" * 200)  # a Windows program
    with pytest.raises(UploadRejected, match="doesn't look like"):
        check_resume_upload("resume.docx", b"PK\x03\x04" + b"junk" * 50)  # a broken / non-Word zip
    with pytest.raises(UploadRejected, match="far too large"):
        check_resume_upload("resume.docx", _docx({"word/media/huge.bin": b"\x00" * (61 * 1024 * 1024)}))  # zip bomb
    with pytest.raises(UploadRejected, match="over 5 MB"):
        check_resume_upload("resume.pdf", RESUME_PDF + b"\x00" * (5 * 1024 * 1024))
    with pytest.raises(UploadRejected, match="plain text"):
        check_resume_upload("resume.html", b"<html><script>alert(1)</script></html>")


def test_filenames_are_cleaned() -> None:
    assert clean_filename("../../etc/passwd.pdf", "pdf") == "passwd.pdf"
    assert clean_filename("C:\\Users\\me\\My CV <final>.PDF", "pdf") == "My CV final.pdf"
    assert clean_filename("", "docx") == "resume.docx" and clean_filename("x" * 300 + ".pdf", "pdf") == "x" * 120 + ".pdf"


def test_upload_endpoint(auth_client: TestClient) -> None:
    up = auth_client.post("/api/v1/resumes/upload", files={"file": ("../../evil/My CV.pdf", RESUME_PDF, "text/html")})
    assert up.status_code == 201, up.text
    body = up.json()
    assert body["original_filename"] == "My CV.pdf" and body["original_file_url"]
    key = body["original_file_url"].split("key=")[-1] if "key=" in body["original_file_url"] else None
    with SessionLocal() as db:
        from app.models.resume import Resume

        stored = db.query(Resume).one().original_file_url
        owner = db.query(User).one().id  # inside the session: a query after it closes leaves a transaction open
    assert stored.endswith(".pdf") and "evil" not in stored and stored.startswith(user_prefix(owner))
    assert key is None or key == stored
    renamed = auth_client.post("/api/v1/resumes/upload", files={"file": ("resume.pdf", _docx(), "application/pdf")})
    assert renamed.status_code == 422 and "Rename it to .docx" in renamed.json()["detail"]
    big = auth_client.post("/api/v1/resumes/upload", files={"file": ("big.pdf", b"%PDF-1.4" + b"0" * (5 * 1024 * 1024), "application/pdf")})
    assert big.status_code == 413


def test_files_are_served_only_to_their_owner(client: TestClient) -> None:
    def register(email: str) -> None:
        assert client.post("/api/v1/auth/register", json={"email": email, "password": "supersecret1", "full_name": "X"}).status_code == 201

    register("owner@example.com")
    url = client.post("/api/v1/resumes/upload", files={"file": ("cv.pdf", RESUME_PDF, "application/pdf")}).json()["original_file_url"]
    assert client.get(url).status_code == 200
    client.cookies.clear()
    register("other@example.com")
    assert client.get(url).status_code == 404


# --------------------------------------------------------------------------- rate limits
def test_stricter_limits_on_sign_in_sign_up_uploads_and_scans() -> None:
    from app.api.deps import limiter
    from app.main import app  # noqa: F401 - registers the routes

    limits = {name.rsplit(".", 1)[-1]: {str(lim.limit) for lim in route} for name, route in limiter._route_limits.items()}
    assert limits["login"] == {"10 per 1 minute", "60 per 1 hour"}
    assert limits["register"] == {"5 per 1 minute", "30 per 1 hour"}
    assert limits["try_the_demo"] == {"20 per 1 minute", "200 per 1 hour"}  # signs in to the shared account, creates nothing
    for endpoint in ("upload_resume", "create_from_text"):
        assert limits[endpoint] == {"10 per 1 minute", "60 per 1 hour"}
    for endpoint in ("start_scan", "complete_onboarding"):
        assert limits[endpoint] == {"6 per 1 minute", "40 per 1 hour"}


def test_a_made_up_cookie_does_not_get_round_the_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The limit's bucket is the address (and a signed-in user's id), never the raw cookie: a different invented
    session cookie on every attempt is still the same client."""
    from app.api.deps import limiter

    monkeypatch.setattr(limiter, "enabled", True)
    try:
        login = {"email": "nobody@example.com", "password": "wrong-password"}
        codes = []
        for attempt in range(11):
            client.cookies.set(settings.COOKIE_NAME, f"made-up-{attempt}")
            codes.append(client.post("/api/v1/auth/login", json=login).status_code)
        assert codes == [401] * 10 + [429]
    finally:
        limiter.reset()


def test_login_is_rate_limited(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.deps import limiter

    monkeypatch.setattr(limiter, "enabled", True)
    try:
        login = {"email": "nobody@example.com", "password": "wrong-password"}
        codes = [client.post("/api/v1/auth/login", json=login).status_code for _ in range(11)]
        assert codes == [401] * 10 + [429]
    finally:
        limiter.reset()


# --------------------------------------------------------------------------- logs
def test_personal_data_is_masked_in_logs() -> None:
    line = ("register jane.doe@example.com +91 98765 43210 Authorization: Bearer abcdefghijklmnop "
            "GET /ws?token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlMTIz key sk-ant-api03-ABCDEFGHIJKLMNOP "
            "password=hunter22 at 2026-10-05 04:23:10 run 3f2a1b9c-1234-4567-89ab-1234567890ab took 1500 ms")
    masked = mask_pii(line)
    for secret in ("jane.doe@", "98765", "abcdefghijklmnop", "eyJhbGci", "ABCDEFGHIJKLMNOP", "hunter22"):
        assert secret not in masked, secret
    assert "j***@example.com" in masked and "***10" in masked
    assert "2026-10-05 04:23:10" in masked and "3f2a1b9c-1234-4567-89ab-1234567890ab" in masked and "1500 ms" in masked


def test_the_log_filter_masks_records() -> None:
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "Signed in %s from %s", ("jane@example.com", "+1 (415) 555-0100"), None)
    assert PIIFilter().filter(record) is True
    assert record.getMessage() == "Signed in j***@example.com from ***00"


# --------------------------------------------------------------------------- export & delete
def test_download_my_data_includes_every_file(auth_client: TestClient) -> None:
    assert auth_client.post("/api/v1/resumes/upload", files={"file": ("cv.pdf", RESUME_PDF, "application/pdf")}).status_code == 201
    r = auth_client.get("/api/v1/users/me/export.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith('attachment; filename="hireflow-export-')
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        names = archive.namelist()
        data = json.loads(archive.read("hireflow-data.json"))
        uploads = [n for n in names if n.startswith("files/uploads/")]
        assert len(uploads) == 1 and archive.read(uploads[0]) == RESUME_PDF
    assert data["user"]["email"] == "jane@example.com" and "hashed_password" not in data["user"]
    assert "README.txt" in names


def test_delete_my_account_leaves_nothing_behind(auth_client: TestClient, master_resume: dict) -> None:
    from tests.test_guardrails import _app

    assert auth_client.post("/api/v1/resumes/upload", files={"file": ("cv.pdf", RESUME_PDF, "application/pdf")}).status_code == 201
    _app("Acme", "Intern")
    auth_client.post("/api/v1/agent/pause")
    with SessionLocal() as db:
        user_id = db.query(User).one().id
    assert get_storage().list_prefix(user_prefix(user_id))
    assert auth_client.request("DELETE", "/api/v1/users/me", json={"confirm": "DELETE"}).json() == {"deleted": True}
    assert get_storage().list_prefix(user_prefix(user_id)) == []
    with engine.connect() as conn:  # no row anywhere still points at the account
        for table in Base.metadata.sorted_tables:
            columns = {c["name"] for c in inspect(conn).get_columns(table.name)}
            if "user_id" in columns:
                left = conn.execute(text(f"SELECT COUNT(*) FROM {table.name} WHERE user_id = :u"),
                                    {"u": user_id.hex if engine.dialect.name == "sqlite" else user_id}).scalar()
                assert left == 0, table.name
