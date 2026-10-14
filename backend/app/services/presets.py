"""One-click preference presets for mass applying (internships at startups and beyond).

The startup boards below all had open internship postings on their public Greenhouse, Ashby or
Lever boards when this list was compiled (taken from the SimplifyJobs / vanshb03 lists). Boards
without postings cost one cheap API call per scan, so the list errs on the side of breadth.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from app.config import settings
from app.models.user import DEFAULT_PREFERENCES, merge_preferences

STARTUP_GREENHOUSE = [
    "anthropic", "figma", "vercel", "verkada", "scaleai", "robinhood", "cloudflare", "togetherai", "cresta",
    "astranis", "andurilindustries", "neuralink", "lightmatter", "brave", "workato", "singlestore", "figureai",
    "k2spacecorporation", "kodiak", "viamrobotics", "vardaspace", "pacificfusion", "gensyn", "haizelabs", "argmax",
    "redwoodmaterials", "truveta", "instalilyai", "eudia", "docugami", "garnerhealth", "hudl", "gemini",
    "spacex", "doordashusa", "tanium", "newsbreak", "perpay", "hometapjobs", "lilasciences", "efficientcomputer",
]
STARTUP_ASHBY = [
    "ramp", "notion", "replit", "perplexity", "modal", "cohere", "mistral.ai", "decagon", "sierra", "mercor",
    "deepgram", "etched", "exa", "physicalintelligence", "applied", "skydio", "saronic", "whatnot", "semgrep",
    "incident", "composio", "rilla", "valon", "zip", "pylon-labs", "hadrian-automation", "1x", "bedrock-robotics",
    "eightsleep", "envoy", "homebase", "uncountable", "ellipsislabs", "windborne-systems", "reflect-orbital",
    "northwoodspace", "base-power", "pika", "opusclip", "meshy", "grow-therapy", "rho", "serval", "firetiger",
    "circleback", "tilderesearch", "dailypay", "primer", "poshmark", "qumulo", "gecko-robotics",
]
STARTUP_LEVER = [
    "palantir", "shieldai", "zoox", "machinalabs", "hermeus", "rigetti", "quantinuum", "tutorintelligence",
    "multiplylabs", "cesiumastro", "acceldata", "immuta", "neighbor", "kepler", "kitware",
]

DEFAULT_INTERN_ROLES = [
    "Software Engineer Intern", "Software Developer Intern", "Backend Engineer Intern", "Frontend Engineer Intern",
    "Full Stack Engineer Intern", "Machine Learning Intern", "AI Engineer Intern", "Data Science Intern",
    "Data Engineer Intern",
]
MASS_APPLY_PLATFORMS = ["internships", "greenhouse", "lever", "ashby", "workday", "linkedin", "indeed", "wellfound",
                        "generic"]


def _union(existing: list[str] | None, extra: list[str]) -> list[str]:
    out: list[str] = []
    for item in [*(existing or []), *extra]:
        if item and item.lower() not in {o.lower() for o in out}:
            out.append(item)
    return out


def intern_roles(current: list[str] | None) -> list[str]:
    """Turn your target roles into intern searches ("Software Engineer" -> "Software Engineer Intern")."""
    roles = [r.strip() for r in current or [] if r and r.strip()]
    if not roles:
        return list(DEFAULT_INTERN_ROLES)
    out = []
    for role in roles:
        out.append(role if re.search(r"\b(intern|internship|co-?op)\b", role, re.IGNORECASE) else f"{role} Intern")
    return _union(out, [])


# "AI engineer · Python": an AI-native developer's internships (Python, FastAPI, LLM apps, agents, on-device AI),
# Python backend next, big tech software internships kept; Java and data-science / analyst roles out.
AI_ENGINEER_ROLES = ["AI Engineer Intern", "Generative AI Intern", "Python Developer Intern",
                     "Machine Learning Engineer Intern", "Backend Developer Intern", "Software Engineer Intern"]
AI_FOCUS_SKILLS = ["AI", "LLMs", "Generative AI", "AI agents", "RAG", "Machine Learning", "Python", "FastAPI",
                   "Django", "Flask", "PyTorch", "Hugging Face", "LangChain"]
AI_AVOID_SKILLS = ["Java", "Spring Boot"]
AI_EXCLUDED_TITLES = ["Java", "Spring Boot", "Data Science", "Data Scientist", "Data Analyst", "Data Analytics",
                      "Business Analyst", "Business Analytics"]
_OFF_FOCUS_SEARCH = re.compile(r"java(?!script)|data-scien|data-analy|business-analy", re.I)

PRESETS = ("ai-engineer", "internships", "startups", "new-grad", "india-internships")
INDIA_PLATFORMS = ["internshala", "linkedin", "indeed", "internships", "greenhouse", "lever", "ashby", "generic"]


def clamp_daily_cap(prefs: dict[str, Any]) -> dict[str, Any]:
    """Bring a daily cap saved above MAX_APPLICATIONS_PER_DAY_CEILING (e.g. before the ceiling existed) down to it."""
    value = prefs.get("max_applications_per_day")
    ceiling = settings.MAX_APPLICATIONS_PER_DAY_CEILING
    if isinstance(value, int) and not isinstance(value, bool) and value > ceiling:
        return {**prefs, "max_applications_per_day": ceiling}
    return prefs


def apply_preset(prefs: dict[str, Any], name: str) -> dict[str, Any]:
    """Return new preferences with the preset merged in (your own lists are kept and extended)."""
    if name not in PRESETS:
        raise ValueError(f"Unknown preset '{name}' (choose from {', '.join(PRESETS)})")
    current = merge_preferences(prefs, None)
    sources = copy.deepcopy(current.get("sources") or {})
    if name == "ai-engineer":  # only what you look for changes: your sources, limits and location stay
        sources["internshala_urls"] = [u for u in sources.get("internshala_urls") or [] if not _OFF_FOCUS_SEARCH.search(u)]
        return merge_preferences(current, {
            "target_roles": list(AI_ENGINEER_ROLES),
            "focus_skills": list(AI_FOCUS_SKILLS),
            "avoid_skills": list(AI_AVOID_SKILLS),
            "keywords_exclude": _union(current.get("keywords_exclude"), AI_EXCLUDED_TITLES),
            "internships_only": True,
            "job_types": ["internship"],
            "experience_level": ["internship"],
            "sources": sources,
        })
    sources["greenhouse_boards"] = _union(sources.get("greenhouse_boards"), STARTUP_GREENHOUSE)
    sources["ashby_boards"] = _union(sources.get("ashby_boards"), STARTUP_ASHBY)
    sources["lever_companies"] = _union(sources.get("lever_companies"), STARTUP_LEVER)
    update: dict[str, Any] = {
        "review_mode": "swipe",
        "auto_submit_kept": True,
        "platforms": _union(current.get("platforms"), MASS_APPLY_PLATFORMS),
        "max_applications_per_day": settings.MAX_APPLICATIONS_PER_DAY_CEILING,  # mass apply: as many as the server allows
        "max_jobs_per_source": max(int(current.get("max_jobs_per_source") or 0), 300),
        "posted_within_days": max(int(current.get("posted_within_days") or 14), 30),
        "remote_preference": "any",
        "scan_interval_hours": min(int(current.get("scan_interval_hours") or 6), 6),
    }
    if name == "india-internships":  # Summer 2027 internships, ~90% in India with Delhi NCR first
        sources["internship_lists"] = _union(sources.get("internship_lists"), ["simplify-internships", "vanshb03-internships"])
        update.update({
            "target_roles": intern_roles(current.get("target_roles")),
            "job_types": ["internship"],
            "experience_level": ["internship"],
            "internships_only": True,
            "internship_season": "Summer 2027",
            "location_focus": copy.deepcopy(DEFAULT_PREFERENCES["location_focus"]),
            "target_locations": ["Delhi, India", "India"],
            "platforms": _union(INDIA_PLATFORMS, current.get("platforms") or []),
            "timezone": "Asia/Kolkata",
        })
    elif name == "internships":
        sources["internship_lists"] = _union(sources.get("internship_lists"), ["simplify-internships", "vanshb03-internships"])
        update.update({
            "target_roles": intern_roles(current.get("target_roles")),
            "job_types": ["internship"],
            "experience_level": ["internship", "entry"],
            "internships_only": True,
        })
    elif name == "new-grad":
        sources["internship_lists"] = _union(sources.get("internship_lists"), ["simplify-new-grad"])
        update.update({
            "job_types": ["full-time"],
            "experience_level": ["entry"],
            "internships_only": False,  # the one preset for full-time jobs
        })
    elif not current.get("internships_only", True):  # startups: keep your roles and job types, add the boards
        update["job_types"] = _union(current.get("job_types"), ["internship", "full-time"])
    update["sources"] = sources
    return merge_preferences(current, update)
