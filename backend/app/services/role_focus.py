"""Your tech focus: keep the roles in your stack, drop the ones in a stack you don't use.

``focus_skills`` (e.g. AI, LLMs, Python, FastAPI) and ``avoid_skills`` (e.g. Java, Spring Boot) are in Settings ›
Preferences; the "AI engineer · Python" preset fills both from an AI-engineering resume. A posting is left out when:

* its title names a technology you skip ("Java Development Intern", "Spring Boot Developer Intern");
* it asks for a technology you skip and none of your languages / frameworks ("Java, Spring Boot, MySQL"
  for a backend internship when you work in Python and FastAPI). "AI" alone doesn't rescue it;
* it names none of your focus skills at all ("Web Development Intern" with React and PHP), judged on the
  full posting. A posting with no description to judge (only a title from a job board) is kept, except an
  Internshala card, whose title is its category.

Both lists empty (the default) = nothing is filtered here.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.text_utils import normalize_text

# How a skill is written in postings, when that differs from its name.
_PATTERNS: dict[str, str] = {
    "ai": r"\bai\b|artificial intelligence|\bgen ?ai\b",
    "artificial intelligence": r"\bai\b|artificial intelligence",
    "llm": r"\bllms?\b|large language models?",
    "llms": r"\bllms?\b|large language models?",
    "generative ai": r"generative ai|\bgen ?ai\b",
    "genai": r"generative ai|\bgen ?ai\b",
    "ai agents": r"\bai agents?\b|\bagentic\b|\bagents?\b",
    "agents": r"\bagents?\b|\bagentic\b",
    "rag": r"\brag\b|retrieval[- ]augmented",
    "machine learning": r"machine learning|\bml\b",
    "ml": r"machine learning|\bml\b",
    "nlp": r"\bnlp\b|natural language processing",
    "fastapi": r"\bfast ?api\b",
    "java": r"\bjava\b(?!\s*script)",
    "spring boot": r"\bspring ?boot\b|\bspring (framework|mvc)\b",
    "prompt engineering": r"prompt engineering|\bprompting\b",
    "hugging face": r"hugging ?face",
    "vector databases": r"vector (database|db|store)s?",
}
# Focus skills too broad to make a posting "yours" when it also asks for a technology you skip:
# "AI-powered platform, Java and Spring Boot" is still a Java job.
_TOPICS = {"ai", "artificial intelligence", "llm", "llms", "generative ai", "genai", "ai agents", "agents", "rag",
           "machine learning", "ml", "nlp", "deep learning", "computer vision", "prompt engineering",
           "ai engineering", "ai integration"}


def _key(skill: str) -> str:
    return normalize_text(skill).strip()


def _pattern(skill: str) -> str:
    key = _key(skill)
    words = re.escape(key).replace("\\ ", "[\\s-]?")  # "spring boot" also matches "spring-boot", "springboot"
    return _PATTERNS.get(key) or rf"(?<![\w+#.]){words}(?![a-z+#])"  # "python3" yes, "pythonic" no


def _names(skill: str, text: str) -> bool:
    return bool(skill.strip()) and re.search(_pattern(skill), text, re.I) is not None


def _skills(prefs: dict[str, Any], key: str) -> list[str]:
    return [s for s in (prefs.get(key) or []) if isinstance(s, str) and s.strip()]


def _role_text(job: Any) -> tuple[str, str]:
    title = normalize_text(getattr(job, "role_title", "") or "")
    body = "\n".join(filter(None, [(getattr(job, "description", "") or "")[:8000], getattr(job, "requirements", None)]))
    return title, normalize_text(body)


def _is_internshala(job: Any) -> bool:
    raw = getattr(job, "raw_data", None) or getattr(job, "raw", None) or {}
    return raw.get("listing_source") == "internshala" or "internshala.com/" in (getattr(job, "source_url", "") or "")


def _label(skills: list[str]) -> str:
    """ "an AI / Python": your first topic and your first language / framework."""
    topic = next((s for s in skills if _key(s) in _TOPICS), None)
    stack = next((s for s in skills if _key(s) not in _TOPICS), None)
    named = " / ".join(s for s in (topic, stack) if s)
    return f"{'an' if named[:1].lower() in 'aeiou' else 'a'} {named}"


def focus_reasons(job: Any, prefs: dict[str, Any]) -> list[str]:
    """Hard reasons a job (a saved Job or a just-scraped ScrapedJob) is outside your tech focus."""
    focus, avoid = _skills(prefs, "focus_skills"), _skills(prefs, "avoid_skills")
    if not focus and not avoid:
        return []
    title, body = _role_text(job)
    for skill in avoid:
        if _names(skill, title):
            return [f"{skill} role (you skip {skill})"]
    stack = [s for s in focus if _key(s) not in _TOPICS]  # your languages / frameworks
    asked = next((s for s in avoid if _names(s, body)), None)
    if asked and not any(_names(s, f"{title}\n{body}") for s in stack):
        return [f"Asks for {asked}, not {' / '.join(stack[:2]) or 'your stack'}"]
    if focus and not any(_names(s, f"{title}\n{body}") for s in focus):
        if len(body) >= 300 or _is_internshala(job):  # a real posting to judge (or an Internshala category)
            return [f"Not {_label(focus)} role"]
    return []


def drop_off_focus(jobs: list[Any], prefs: dict[str, Any]) -> tuple[list[Any], int]:
    """(the postings in your tech focus, how many were left out)."""
    if not _skills(prefs, "focus_skills") and not _skills(prefs, "avoid_skills"):
        return jobs, 0
    kept = [j for j in jobs if not focus_reasons(j, prefs)]
    return kept, len(jobs) - len(kept)
