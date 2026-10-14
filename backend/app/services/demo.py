"""Demo mode (``DEMO_MODE=true``): the sample candidate behind onboarding's "Load sample profile" button."""

from __future__ import annotations

from typing import Any

SAMPLE_RESUME_TEXT = """SAM RIVERA
sam.rivera@example.com | +91 98765 43210 | linkedin.com/in/samrivera-demo | github.com/samrivera-demo
Bengaluru, Karnataka, India

SUMMARY
Second-year computer science student who builds Python and machine-learning projects end to end.

EDUCATION
Indian Institute of Technology, Delhi | B.Tech Computer Science   2024 - 2028
• CGPA: 8.7

EXPERIENCE
Machine Learning Intern | Acme AI Labs | Remote   May 2025 - Jul 2025
• Built a FastAPI service that classifies support tickets with a fine-tuned transformer (92% accuracy)
• Wrote data pipelines in Python and SQL that cut model retraining time from 3 hours to 40 minutes

PROJECTS
StudyBuddy | Python, LangChain, PostgreSQL
• Retrieval-augmented chatbot over lecture notes, used by 300+ students
Campus Rides | TypeScript, React, FastAPI
• Ride-sharing app for the campus with real-time matching over WebSockets

SKILLS
Python, FastAPI, PyTorch, scikit-learn, SQL, PostgreSQL, TypeScript, React, Docker, Git, LLMs
"""

SAMPLE_PROFILE: dict[str, Any] = {
    "welcome": {"phone": "+91 98765 43210", "location": "Bengaluru, India", "timezone": "Asia/Kolkata"},
    "profiles": {
        "linkedin_url": "https://www.linkedin.com/in/samrivera-demo",
        "github_url": "https://github.com/samrivera-demo",
        "portfolio_url": "https://samrivera.example.com",
        "profile_links": [{"label": "LeetCode", "url": "https://leetcode.com/u/samrivera-demo"}],
    },
    "targets": {
        "target_roles": ["Software Engineer Intern", "Machine Learning Intern", "Backend Engineer Intern"],
        "target_locations": ["Bengaluru, India", "Remote"],
        "remote_preference": "any",
        "internship_season": "Summer 2027",
        "year_of_study": 2,
        "focus_skills": ["Python", "FastAPI", "Machine Learning"],
        "avoid_skills": [],
    },
    "answers": {
        "work_authorization": "Yes",
        "over_18": "Yes",
        "requires_sponsorship": "No",
        "availability_months": 3,
        "willing_to_relocate": "Yes",
        "notice_period": "Available from May 2027",
        "pronouns": "Prefer not to say",
        "gender": "Prefer not to say",
    },
    "apply": {"review_mode": "swipe", "auto_submit_kept": False, "max_applications_per_day": 10,
              "resume_strategy": "light", "cover_letter_enabled": True},
}
