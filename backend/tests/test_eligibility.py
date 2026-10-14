"""Eligibility is never guessed: one test per kind of eligibility question (docs/HIREFLOW_PLAN.md §5.1).

Without your saved answer, an eligibility question stays blank for you to answer: it isn't sent to
the LLM, it gets no default, and it isn't read off your resume. A blank or machine-made answer to
one always holds the application for you, required or not.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.agent_orchestrator import unanswered_questions
from app.services.question_answerer import answer_questions, is_eligibility

RESUME = {
    "personal_info": {"name": "Asha Rao", "phone": "+91 98765 43210", "location": "Bengaluru, Karnataka, India"},
    "summary": "CS student",
    "education": [{"institution": "IIT Example", "degree": "B.Tech", "field": "Computer Science", "end_date": "2027"}],
    "skills": {"technical": ["Python"]},
}
YES_NO = ["Yes", "No"]

# (kind, question, options, the saved-answer key that answers it, if onboarding / approvals can save one)
ELIGIBILITY: list[tuple[str, str, list[str] | None, str | None]] = [
    ("work_authorization", "Are you legally authorized to work in India?", YES_NO, "work_authorization"),
    ("right_to_work", "Do you have the right to work in the UK?", YES_NO, "work_authorization"),
    ("work_permit", "Do you hold a valid work permit for this country?", YES_NO, None),
    ("sponsorship", "Will you now or in the future require sponsorship for an employment visa?", YES_NO, "requires_sponsorship"),
    ("visa_status", "What is your current visa status?", None, None),
    ("immigration", "Immigration status", None, None),
    ("citizenship", "Country of citizenship", None, None),
    ("criminal_record", "Have you ever been convicted of a criminal offence?", YES_NO, None),
    ("felony", "Have you been convicted of a felony in the last 7 years?", YES_NO, None),
    ("security_clearance", "Do you hold an active security clearance?", YES_NO, None),
    ("relocation", "Are you willing to relocate to Bengaluru?", YES_NO, "willing_to_relocate"),
    ("age", "Are you at least 18 years of age?", YES_NO, "over_18"),
    ("background_check", "Do you consent to a background check?", YES_NO, None),
    ("drug_test", "Are you willing to take a pre-employment drug test?", YES_NO, None),
    ("date_of_birth", "Date of birth", None, None),
    ("aadhaar", "Aadhaar number", None, None),
    ("pan", "PAN number", None, None),
    ("passport", "Do you have a valid passport?", YES_NO, None),
    ("ssn", "Social Security Number", None, None),
]
IDS = [kind for kind, *_ in ELIGIBILITY]


def _question(text: str, options: list[str] | None) -> dict[str, Any]:
    return {"question": text, "options": options or [], "field_type": "select" if options else "text", "required": True}


def _confident_llm(fake_llm: Any, question: str) -> Any:
    """An LLM that would happily answer "Yes" with full confidence, if it were ever asked."""
    return fake_llm({"CUSTOM QUESTION ANSWERING": {"custom_answers": [
        {"question": question, "field_type": "text", "answer": "Yes", "confidence": 0.99, "needs_user_review": False},
    ]}})


@pytest.mark.parametrize(("kind", "text", "options", "key"), ELIGIBILITY, ids=IDS)
def test_recognised_as_eligibility(kind: str, text: str, options: list[str] | None, key: str | None) -> None:
    assert is_eligibility(text), kind


@pytest.mark.parametrize(("kind", "text", "options", "key"), ELIGIBILITY, ids=IDS)
def test_never_guessed_without_your_answer(fake_llm: Any, kind: str, text: str, options: list[str] | None,
                                          key: str | None) -> None:
    provider = _confident_llm(fake_llm, text)
    prefs = {"salary_min": 20000, "willing_to_relocate": True, "remote_preference": "onsite"}
    [answer] = answer_questions([_question(text, options)], RESUME, prefs, {})
    assert answer["answer"] == "", f"{kind} was answered without a saved answer: {answer}"
    assert answer["needs_user_review"] is True
    assert provider.calls == [], f"{kind} was sent to the LLM"
    assert unanswered_questions(SimpleNamespace(custom_answers=[answer])) == [text]
    # Optional or not, a blank eligibility answer still waits for you
    assert unanswered_questions(SimpleNamespace(custom_answers=[{**answer, "required": False}])) == [text]


@pytest.mark.parametrize(("kind", "text", "options", "key"), [e for e in ELIGIBILITY if e[3]], ids=[e[0] for e in ELIGIBILITY if e[3]])
def test_your_saved_answer_is_used(kind: str, text: str, options: list[str] | None, key: str) -> None:
    [answer] = answer_questions([_question(text, options)], RESUME, {}, {key: "No"}, use_llm=False)
    assert answer["answer"] == "No" and answer["needs_user_review"] is False and answer["source"] == "rule", kind
    assert unanswered_questions(SimpleNamespace(custom_answers=[answer])) == []


def test_an_llm_eligibility_answer_never_counts() -> None:
    """Defence in depth: even if a machine-made eligibility answer slipped in, it holds the application."""
    answers = [
        {"question": "Are you legally authorized to work in the US?", "answer": "Yes", "source": "llm",
         "needs_user_review": False, "required": True},
        {"question": "Do you hold an active security clearance?", "answer": "No", "source": "fallback",
         "needs_user_review": False, "required": False},
        {"question": "Are you legally authorized to work in the US?", "answer": "Yes", "source": "user",
         "needs_user_review": False, "required": True},
    ]
    assert unanswered_questions(SimpleNamespace(custom_answers=answers)) == [answers[0]["question"], answers[1]["question"]]


def test_other_questions_still_use_the_llm(fake_llm: Any) -> None:
    provider = fake_llm({"CUSTOM QUESTION ANSWERING": {"custom_answers": [
        {"question": "Why do you want to join us?", "field_type": "text", "answer": "Because I build things.",
         "confidence": 0.9, "needs_user_review": False},
    ]}})
    answers = answer_questions([{"question": "Why do you want to join us?"},
                                {"question": "Are you legally authorized to work in India?", "options": YES_NO}], RESUME, {}, {})
    assert answers[0]["answer"] == "Because I build things." and answers[0]["source"] == "llm"
    assert answers[1]["answer"] == "" and answers[1]["needs_user_review"] is True
    assert len(provider.calls) == 1 and "legally authorized" not in provider.calls[0]["prompt"]
