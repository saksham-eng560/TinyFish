#!/usr/bin/env python3
"""Check WCAG contrast of the dashboard's colour tokens (light and dark mode).

    python scripts/check_contrast.py [path/to/globals.css]

Every text-on-surface pair must reach 4.5:1 (WCAG AA, normal text). Exits 1 if one doesn't.
"""

from __future__ import annotations

import colorsys
import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "app" / "globals.css"
PAIRS = [
    ("foreground", "background"), ("muted-foreground", "background"), ("muted-foreground", "muted"),
    ("primary", "background"), ("primary", "card"), ("primary", "secondary"), ("primary-foreground", "primary"),
    ("secondary-foreground", "secondary"), ("accent-foreground", "accent"), ("card-foreground", "card"),
    ("success", "background"), ("success-foreground", "success"), ("info", "background"), ("info-foreground", "info"),
    ("warning", "background"), ("warning-foreground", "warning"), ("destructive", "background"),
    ("destructive-foreground", "destructive"), ("danger", "background"), ("danger-foreground", "danger"),
]
TOKEN = re.compile(r"--([a-z0-9-]+):\s*([0-9.]+) ([0-9.]+)% ([0-9.]+)%")


def rgb(h: float, s: float, lightness: float) -> tuple[float, float, float]:
    return colorsys.hls_to_rgb(h / 360, lightness / 100, s / 100)


def luminance(c: tuple[float, float, float]) -> float:
    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tokens(block: str) -> dict[str, tuple[float, float, float]]:
    return {m.group(1): rgb(float(m.group(2)), float(m.group(3)), float(m.group(4))) for m in TOKEN.finditer(block)}


def main() -> int:
    css = Path(sys.argv[1] if len(sys.argv) > 1 else CSS).read_text(encoding="utf-8")
    light = tokens(re.search(r":root \{(.*?)\n  \}", css, re.S).group(1))
    dark = {**light, **tokens(re.search(r"\.dark \{(.*?)\n  \}", css, re.S).group(1))}
    failures = 0
    for mode, toks in (("light", light), ("dark", dark)):
        for fg, bg in PAIRS:
            r = ratio(toks[fg], toks[bg])
            ok = r >= 4.5
            failures += not ok
            print(f"{'ok ' if ok else 'LOW'} {mode:5s} {fg:24s} on {bg:12s} {r:5.2f}:1")
    print(f"\n{failures} pair(s) below 4.5:1" if failures else "\nAll pairs meet WCAG AA (4.5:1).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
