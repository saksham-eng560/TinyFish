#!/usr/bin/env python3
"""Bring your AutoApply AI data into HireFlow (local mode, the SQLite database ./start.sh uses).

    backend/.venv/bin/python scripts/import_autoapply.py ~/autoapply-ai            # the AutoApply AI folder
    backend/.venv/bin/python scripts/import_autoapply.py ~/autoapply-ai --replace  # HireFlow already has accounts

Stop both apps first (Ctrl-C). It copies, and never changes anything in the AutoApply folder:

- the database (backend/data/autoapply.db -> backend/data/hireflow.db): accounts and passwords, resumes,
  jobs, swipes, applications, answers, interviews, e-mails, settings;
- the stored files (backend/data/storage: uploaded resumes, generated PDFs, form screenshots);
- SECRET_KEY and ENCRYPTION_KEY from AutoApply's .env, so saved LinkedIn, Internshala, Google and ATS logins stay
  readable, plus your own settings (AI keys, Google client, Ollama, SMTP...) where HireFlow's .env has none yet.

Then the database gets HireFlow's new columns (your accounts skip the first-run onboarding). With --replace, an
existing backend/data/hireflow.db and .env are kept as timestamped .bak copies first. Only names are printed,
never a key or password.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET_DB = ROOT / "backend" / "data" / "hireflow.db"
TARGET_STORAGE = ROOT / "backend" / "data" / "storage"
TARGET_ENV = ROOT / ".env"

# Always taken from AutoApply: they decide whether existing encrypted data and sign-ins can be read.
KEYS = ("SECRET_KEY", "ENCRYPTION_KEY")
# Never copied: they describe the machine or the old name, not you (HireFlow's own values are right).
MACHINE = {"ENVIRONMENT", "DOMAIN", "POSTGRES_PASSWORD", "FRONTEND_URL", "PUBLIC_API_URL", "CORS_ORIGINS", "COOKIE_SECURE",
           "DATABASE_URL", "REDIS_URL", "CELERY_TASK_ALWAYS_EAGER", "COMPOSE_PROFILES", "GOOGLE_REDIRECT_URI",
           "STORAGE_BACKEND", "S3_BUCKET", "S3_ENDPOINT_URL", "S3_REGION", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY",
           "LOG_LEVEL", "LOCAL_STORAGE_PATH", "LOCAL_DATABASE_URL", "DEMO_MODE", "DEMO_ACCOUNT_EMAIL", "DEMO_SITE_URL"}
LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def env_values(path: Path) -> dict[str, str]:
    """KEY=value lines, read like start.sh does (inline comments and quotes stripped)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = LINE.match(line.strip())
        if not match:
            continue
        value = re.sub(r"(^|\s)#.*$", "", match.group(2)).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def set_env(path: Path, updates: dict[str, str]) -> None:
    """Replace each KEY= line (or add it at the end); every other line stays as it was."""
    text = path.read_text(encoding="utf-8")
    added = []
    for key, value in updates.items():
        pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")
        if pattern.search(text):
            text = pattern.sub(lambda _m, k=key, v=value: f"{k}={v}", text, count=1)
        else:
            added.append(f"{key}={value}")
    if added:
        text = text.rstrip("\n") + "\n\n# Brought over from AutoApply AI by scripts/import_autoapply.py\n" + "\n".join(added) + "\n"
    path.write_text(text, encoding="utf-8")


def read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def accounts(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with closing(read_only(path)) as con:
            return con.execute("SELECT count(*) FROM users").fetchone()[0]
    except sqlite3.Error:
        return 0


def counts(path: Path) -> dict[str, int]:
    out = {}
    with closing(read_only(path)) as con:
        for table in ("users", "resumes", "jobs", "applications", "interviews"):
            try:
                out[table] = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]  # fixed table names
            except sqlite3.Error:
                out[table] = 0
    return out


def backup(path: Path, stamp: str) -> Path:
    copy = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, copy)
    return copy


def copy_database(source: Path, target: Path) -> None:
    """A consistent snapshot. AutoApply's database runs in WAL mode: its -wal file can hold the latest changes, so the
    files are copied to a scratch folder first and opened there (nothing is ever written next to the original)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    for leftover in (target, target.with_name(target.name + "-wal"), target.with_name(target.name + "-shm")):
        leftover.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        snapshot = Path(scratch) / source.name
        for suffix in ("", "-wal", "-shm"):
            part = source.with_name(source.name + suffix)
            if part.is_file():
                shutil.copy2(part, snapshot.with_name(snapshot.name + suffix))
        with closing(sqlite3.connect(snapshot)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)


def copy_files(source: Path, target: Path) -> tuple[int, int]:
    """Copy every stored file; one that already exists in HireFlow is left alone."""
    copied = kept = 0
    if not source.is_dir():
        return copied, kept
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        dest = target / path.relative_to(source)
        if dest.exists():
            kept += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        copied += 1
    return copied, kept


def carry_settings(old: dict[str, str], new_env: Path) -> tuple[list[str], list[str]]:
    """The keys always; your other settings only where HireFlow has no value yet."""
    current = env_values(new_env)
    updates: dict[str, str] = {}
    if old.get("SECRET_KEY") and not old["SECRET_KEY"].startswith("change-me"):
        updates["SECRET_KEY"] = old["SECRET_KEY"]
        if not old.get("ENCRYPTION_KEY"):
            # AutoApply derived its encryption key from SECRET_KEY; write that key down, so a new one is never generated
            derived = hashlib.sha256(("enc:" + old["SECRET_KEY"]).encode("utf-8")).digest()
            updates["ENCRYPTION_KEY"] = base64.urlsafe_b64encode(derived).decode()
    if old.get("ENCRYPTION_KEY"):
        updates["ENCRYPTION_KEY"] = old["ENCRYPTION_KEY"]
    filled = []
    for key, value in old.items():
        if key in KEYS or key in MACHINE or not value or "autoapply" in value.lower() or current.get(key):
            continue
        updates[key] = value
        filled.append(key)
    if updates:
        set_env(new_env, updates)
    return [k for k in KEYS if k in updates], sorted(filled)


def upgrade(target: Path) -> None:
    """HireFlow's new columns (and the onboarding backfill), exactly as ./start.sh prepares the database."""
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{target}", REDIS_URL="")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate.py")], env=env, check=True,
                   stdout=subprocess.DEVNULL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("autoapply_folder", type=Path, help="the AutoApply AI folder (the one with start.sh)")
    parser.add_argument("--replace", action="store_true",
                        help="replace a HireFlow database that already has accounts (kept as a .bak copy)")
    args = parser.parse_args(argv)

    folder = args.autoapply_folder.expanduser().resolve()
    source = folder / "backend" / "data" / "autoapply.db"
    if not source.is_file():
        print(f"No AutoApply AI database at {source}.\nPass the AutoApply AI folder, the one with start.sh in it.",
              file=sys.stderr)
        return 1
    if folder == ROOT:
        print("That's this HireFlow folder: pass the AutoApply AI folder.", file=sys.stderr)
        return 1
    existing = accounts(TARGET_DB)
    if existing and not args.replace:
        print(f"HireFlow's database already has {existing} account(s): {TARGET_DB}\n"
              "Run again with --replace to swap it for your AutoApply data (the current one is kept as a .bak copy).",
              file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"Bringing AutoApply AI data from {folder}")
    if TARGET_DB.is_file():
        print(f"  kept the old HireFlow database as {backup(TARGET_DB, stamp).name}")
    copy_database(source, TARGET_DB)
    found = counts(TARGET_DB)
    print("  database: " + ", ".join(f"{n} {table}" for table, n in found.items()))

    copied, kept = copy_files(folder / "backend" / "data" / "storage", TARGET_STORAGE)
    print(f"  files: {copied} copied" + (f", {kept} already here" if kept else ""))

    old_env = env_values(folder / ".env")
    if not old_env:
        print("  settings: AutoApply AI has no .env, so saved logins (LinkedIn, Google...) need connecting again")
    else:
        if not TARGET_ENV.is_file():
            shutil.copy2(ROOT / ".env.example", TARGET_ENV)
        else:
            print(f"  kept the old .env as {backup(TARGET_ENV, stamp).name}")
        keys, filled = carry_settings(old_env, TARGET_ENV)
        print(f"  settings: {', '.join(keys) or 'no keys'} brought over"
              + (f"; also {', '.join(filled)}" if filled else ""))

    upgrade(TARGET_DB)
    print("  upgraded to HireFlow's database layout")
    print("\nDone. Start HireFlow with ./start.sh and sign in with your AutoApply AI email and password.\n"
          "Using the Chrome extension? Load HireFlow's extension/ folder instead of AutoApply's, then paste a new token\n"
          "from Settings > Integrations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
