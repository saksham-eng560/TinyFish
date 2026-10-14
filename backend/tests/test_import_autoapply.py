"""scripts/import_autoapply.py: AutoApply AI's local data comes over whole, and the AutoApply folder is never touched."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.security import hash_password
from app.models.application import Application
from app.models.job import Job
from app.models.user import User

ROOT = Path(__file__).resolve().parents[2]
SECRET = "autoapply-secret-key-that-is-long-enough-0123456789"
ENC = base64.urlsafe_b64encode(bytes(range(32))).decode()
# Columns HireFlow added after AutoApply AI: an AutoApply database doesn't have them
NEW_COLUMNS = {"users": ["onboarding_step", "onboarding_completed_at", "github_url", "portfolio_url", "profile_links",
                         "automation_paused_at"], "applications": ["send_after"]}


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("import_autoapply", ROOT / "scripts" / "import_autoapply.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _autoapply_folder(base: Path, env: str) -> tuple[Path, str]:
    """An AutoApply AI folder as ./start.sh left it: its database (old layout), stored files and .env."""
    folder = base / "autoapply-ai"
    data = folder / "backend" / "data"
    data.mkdir(parents=True)
    engine = create_engine(f"sqlite:///{data / 'autoapply.db'}")
    Base.metadata.create_all(engine)
    user_id = uuid.uuid4()
    with Session(engine) as db:
        db.add(User(id=user_id, email="priya@example.com", full_name="Priya", hashed_password=hash_password("old-password-1"),
                    preferences={}))
        job = Job(company_name="NoLink Co", role_title="Data Intern", description="",
                  source_url=f"https://manual.autoapply.invalid/{user_id}/{uuid.uuid4()}", source_platform="custom")
        db.add(job)
        db.flush()
        db.add(Application(user_id=user_id, job_id=job.id, status="applied", submitted_at=datetime.now(UTC)))
        db.commit()
    with engine.begin() as conn:
        for table, columns in NEW_COLUMNS.items():
            for column in columns:
                for (index,) in conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = :t "
                                                  "AND sql LIKE :c"), {"t": table, "c": f"%{column}%"}).all():
                    conn.execute(text(f'DROP INDEX "{index}"'))
                conn.execute(text(f'ALTER TABLE "{table}" DROP COLUMN "{column}"'))
    engine.dispose()
    (data / "storage" / "users" / str(user_id) / "uploads").mkdir(parents=True)
    (data / "storage" / "users" / str(user_id) / "uploads" / "resume.txt").write_text("Priya's resume")
    (folder / ".env").write_text(env)
    return folder, str(user_id)


def _fingerprint(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()}


@pytest.fixture
def importer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The script, writing into a throwaway HireFlow data folder and .env."""
    module = _load_script()
    target = tmp_path / "hireflow"
    (target / "backend" / "data").mkdir(parents=True)
    monkeypatch.setattr(module, "TARGET_DB", target / "backend" / "data" / "hireflow.db")
    monkeypatch.setattr(module, "TARGET_STORAGE", target / "backend" / "data" / "storage")
    monkeypatch.setattr(module, "TARGET_ENV", target / ".env")
    yield module


ENV = (f"SECRET_KEY={SECRET}\nENCRYPTION_KEY={ENC}\nANTHROPIC_API_KEY=sk-ant-test-value   # mine\n"
       "DATABASE_URL=postgresql+psycopg://autoapply:autoapply@localhost:5432/autoapply\n"
       "FRONTEND_URL=http://localhost:3000\nOLLAMA_MODEL=qwen3.5:4b\n")


def test_everything_comes_over_and_the_autoapply_folder_is_untouched(importer: Any, tmp_path: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    folder, user_id = _autoapply_folder(tmp_path, ENV)
    # AutoApply runs SQLite in WAL mode: a change still in the -wal file must come over too
    live = sqlite3.connect(folder / "backend" / "data" / "autoapply.db")
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("UPDATE users SET full_name = 'Priya Sharma'")
    live.commit()
    assert (folder / "backend" / "data" / "autoapply.db-wal").is_file()
    before = _fingerprint(folder)

    assert importer.main([str(folder)]) == 0
    # Nothing in the AutoApply folder changed: not the database, its -wal/-shm files or anything else
    assert _fingerprint(folder) == before
    live.close()
    out = capsys.readouterr().out
    assert "1 users" in out and "1 applications" in out and "1 copied" in out

    with sqlite3.connect(importer.TARGET_DB) as con:
        name, done, step = con.execute("SELECT full_name, onboarding_completed_at, onboarding_step FROM users").fetchone()
        assert name == "Priya Sharma"  # from the -wal file
        assert done is not None and step == 8  # existing accounts skip the first-run onboarding
        assert con.execute("SELECT count(*) FROM applications").fetchone()[0] == 1
        columns = {row[1] for row in con.execute("PRAGMA table_info(applications)")}
        assert "send_after" in columns
        indexes = {row[1] for row in con.execute("PRAGMA index_list(applications)")}
        assert "ix_applications_send_after" in indexes  # the new columns' indexes too, not only the columns
    assert (importer.TARGET_STORAGE / "users" / user_id / "uploads" / "resume.txt").read_text() == "Priya's resume"

    env = importer.env_values(importer.TARGET_ENV)
    assert env["SECRET_KEY"] == SECRET and env["ENCRYPTION_KEY"] == ENC
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-test-value" and env["OLLAMA_MODEL"] == "qwen3.5:4b"
    assert "autoapply" not in env.get("DATABASE_URL", "")  # the machine's own settings aren't copied
    for secret in (SECRET, ENC, "sk-ant-test-value"):
        assert secret not in out  # names only, never values


def test_it_wont_overwrite_hireflow_accounts_without_replace(importer: Any, tmp_path: Path) -> None:
    folder, _ = _autoapply_folder(tmp_path, ENV)
    assert importer.main([str(folder)]) == 0
    assert importer.main([str(folder)]) == 1  # HireFlow now has an account: refuse
    assert importer.main([str(folder), "--replace"]) == 0
    backups = list(importer.TARGET_DB.parent.glob("hireflow.db.bak-*"))
    assert len(backups) == 1
    assert list(importer.TARGET_ENV.parent.glob(".env.bak-*"))


def test_an_empty_encryption_key_keeps_working(importer: Any, tmp_path: Path) -> None:
    """AutoApply derived its key from SECRET_KEY when ENCRYPTION_KEY was empty: HireFlow gets that exact key."""
    folder, _ = _autoapply_folder(tmp_path, f"SECRET_KEY={SECRET}\nENCRYPTION_KEY=\n")
    assert importer.main([str(folder)]) == 0
    key = importer.env_values(importer.TARGET_ENV)["ENCRYPTION_KEY"]
    assert base64.urlsafe_b64decode(key) == hashlib.sha256(("enc:" + SECRET).encode()).digest()


def test_wrong_folder_says_what_to_pass(importer: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert importer.main([str(tmp_path)]) == 1
    assert "Pass the AutoApply AI folder" in capsys.readouterr().err
