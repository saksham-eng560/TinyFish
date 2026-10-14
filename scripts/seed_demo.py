#!/usr/bin/env python3
"""The demo's shared, pre-filled account (the end-to-end tests sign in to it). Needs DEMO_MODE=true.

    python scripts/seed_demo.py            # create it if it's missing (the API also does this when it starts)
    python scripts/seed_demo.py --reset    # the nightly reset, right now: every account and job wiped, demo re-created
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "backend" if (_ROOT / "backend" / "app").is_dir() else _ROOT))

from app.config import settings
from app.core.database import create_all, session_scope
from app.services.demo_seed import ensure_demo_account, reset_demo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="wipe every account and job, then re-create the demo")
    args = parser.parse_args()
    if not settings.DEMO_MODE:
        print("DEMO_MODE isn't on: nothing to do (this never touches a real deployment's data)")
        return 1
    if settings.is_sqlite:
        create_all()
    with session_scope() as db:
        if args.reset:
            print(reset_demo(db))
        else:
            user = ensure_demo_account(db)
            print(f"Demo account ready: {user.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
