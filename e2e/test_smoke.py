"""The smoke test (scripts/smoke_test.py) passes against the demo-mode servers, so it can be trusted to check any
running HireFlow."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import BASE

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_test.py"


def test_the_smoke_test_passes_on_the_demo() -> None:
    api = os.environ.get("E2E_API_URL", "")
    run = subprocess.run([sys.executable, str(SCRIPT), "--site", BASE, *(["--api", api] if api else []), "--demo"],
                         capture_output=True, text=True, timeout=300, check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "all checks passed" in run.stdout
