"""The bundled demo careers site (docs/HIREFLOW_PLAN.md §5.5): internships at fictional companies, each with
a real application form, so the public demo runs the whole loop (scan, match, tailor, fill, submit) without
anything reaching a real employer. Served by ``app/api/demo_site.py``; scanned by ``app/scrapers/demo.py``.

Submissions are kept in memory, per server process: the demo resets every night anyway.
"""

from __future__ import annotations

import html
import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

# All fictional companies (no real employer is shown with made-up jobs)
POSTINGS: list[dict[str, Any]] = [
    {"id": "acme-backend", "company": "Acme Robotics", "title": "Backend Engineering Intern", "location": ("Bengaluru", "India"),
     "remote": False, "stipend": 40000, "days_ago": 1,
     "description": "Build the Python and FastAPI services that schedule fleets of warehouse robots. You'll design REST APIs, "
                    "model data in PostgreSQL, and ship with Docker. Good to have: Redis, Celery. Summer 2027, 12 weeks."},
    {"id": "northwind-data", "company": "Northwind Analytics", "title": "Data Engineering Intern", "location": ("Hyderabad", "India"),
     "remote": False, "stipend": 35000, "days_ago": 2,
     "description": "Own a data pipeline end to end: ingest events with Python, transform them with SQL and dbt, and publish "
                    "dashboards. You know Python and SQL; Airflow or Spark is a plus. Summer 2027."},
    {"id": "contoso-fullstack", "company": "Contoso Cloud", "title": "Full-Stack Developer Intern", "location": ("Remote", "India"),
     "remote": True, "stipend": 30000, "days_ago": 3,
     "description": "Ship features across our React and TypeScript dashboard and its Node.js API. You'll write tests, review "
                    "code and talk to customers. Comfortable with JavaScript, React and REST. Summer 2027, remote."},
    {"id": "fabrikam-ml", "company": "Fabrikam AI", "title": "Machine Learning Intern", "location": ("Bengaluru", "India"),
     "remote": False, "stipend": 50000, "days_ago": 1,
     "description": "Train and evaluate models for document understanding with Python and PyTorch, and serve them behind a "
                    "FastAPI endpoint. You know Python, NumPy and the basics of deep learning. Summer 2027."},
    {"id": "tailspin-mobile", "company": "Tailspin Mobility", "title": "Android Developer Intern", "location": ("Pune", "India"),
     "remote": False, "stipend": 25000, "days_ago": 5,
     "description": "Build features in our Kotlin Android app used by 2 million riders: maps, payments and offline sync. "
                    "Kotlin or Java required; Jetpack Compose is a plus. Summer 2027."},
    {"id": "woodgrove-platform", "company": "Woodgrove Health", "title": "Platform Engineering Intern", "location": ("Gurugram", "India"),
     "remote": False, "stipend": 45000, "days_ago": 2,
     "description": "Help run our Kubernetes platform on AWS: write Terraform, improve CI with GitHub Actions, and add "
                    "observability with Prometheus and Grafana. Linux and Docker required; Python or Go a plus. Summer 2027."},
    {"id": "litware-security", "company": "Litware Security", "title": "Security Engineering Intern", "location": ("Remote", "India"),
     "remote": True, "stipend": 40000, "days_ago": 4,
     "description": "Find and fix vulnerabilities in our web apps, automate checks in CI, and write internal guides. You know "
                    "web security basics (OWASP), Python, and Linux. Summer 2027, remote."},
    {"id": "proseware-frontend", "company": "Proseware Labs", "title": "Frontend Engineering Intern", "location": ("Delhi", "India"),
     "remote": False, "stipend": 30000, "days_ago": 6,
     "description": "Build accessible interfaces in React, TypeScript and Tailwind CSS for our writing tool, with a design "
                    "system and visual tests. HTML, CSS and React required. Summer 2027."},
    {"id": "adatum-ai", "company": "Adatum Data", "title": "AI Engineer Intern (LLM apps)", "location": ("Bengaluru", "India"),
     "remote": False, "stipend": 55000, "days_ago": 1,
     "description": "Build LLM-powered features with Python: retrieval with embeddings and a vector database, evaluation "
                    "harnesses, and FastAPI services. Python required; experience with LLM APIs is a plus. Summer 2027."},
    {"id": "wingtip-game", "company": "Wingtip Games", "title": "Game Developer Intern", "location": ("Mumbai", "India"),
     "remote": False, "stipend": 20000, "days_ago": 8,
     "description": "Prototype gameplay in Unity with C#, profile performance on low-end phones, and ship to our beta "
                    "testers. C# and Unity required. Summer 2027."},
    {"id": "lucerne-devtools", "company": "Lucerne Software", "title": "Developer Tools Intern", "location": ("Remote", "India"),
     "remote": True, "stipend": 35000, "days_ago": 3,
     "description": "Improve our open-source CLI written in Go and Python: faster builds, better error messages and docs. "
                    "Git and either Go or Python required. Summer 2027, remote."},
    {"id": "margie-backend-java", "company": "Margie's Travel", "title": "Java Backend Intern", "location": ("Chennai", "India"),
     "remote": False, "stipend": 25000, "days_ago": 9,
     "description": "Work on our booking platform's Java and Spring Boot services, with MySQL and Kafka. Java required. "
                    "Summer 2027."},
]

_lock = threading.Lock()
SUBMISSIONS: list[dict[str, Any]] = []


def by_id(posting_id: str) -> dict[str, Any] | None:
    return next((p for p in POSTINGS if p["id"] == posting_id), None)


def jsonld(post: dict[str, Any], base: str) -> dict[str, Any]:
    """schema.org JobPosting, as real careers pages publish it for search engines."""
    city, country = post["location"]
    return {
        "@context": "https://schema.org", "@type": "JobPosting", "title": post["title"],
        "description": f"<p>{html.escape(post['description'])}</p>",
        "datePosted": (datetime.now(UTC) - timedelta(days=post["days_ago"])).date().isoformat(),
        "employmentType": "INTERN",
        "hiringOrganization": {"@type": "Organization", "name": post["company"]},
        "jobLocation": {"@type": "Place", "address": {"addressLocality": city, "addressCountry": country}},
        **({"jobLocationType": "TELECOMMUTE"} if post["remote"] else {}),
        "baseSalary": {"@type": "MonetaryAmount", "currency": "INR", "value": {
            "@type": "QuantitativeValue", "value": post["stipend"], "unitText": "MONTH"}},
        "url": f"{base}/jobs/{post['id']}", "directApplyUrl": f"{base}/jobs/{post['id']}",
    }


_STYLE = ("body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;color:#0f172a}"
          "label{display:block;margin-top:14px;font-weight:600}input,select,textarea{width:100%;padding:8px;margin-top:4px;"
          "box-sizing:border-box}textarea{min-height:90px}.row label{display:inline;font-weight:400;margin-right:12px}"
          ".row input{width:auto}button{margin-top:20px;padding:10px 18px;background:#2563eb;color:#fff;border:0;"
          "border-radius:8px;font-size:15px}.note{background:#eff6ff;border-radius:8px;padding:10px 12px;font-size:14px}")
_NOTE = ('<p class="note">HireFlow demo careers site: these companies and jobs are fictional, and nothing sent here '
         "reaches anyone.</p>")


def careers_html(base: str) -> str:
    graph = json.dumps({"@context": "https://schema.org", "@graph": [jsonld(p, base) for p in POSTINGS]}).replace("</", "<\\/")
    items = "".join(f'<li><a href="{base}/jobs/{p["id"]}">{html.escape(p["title"])}</a> · {html.escape(p["company"])}</li>'
                    for p in POSTINGS)
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>Internships · HireFlow demo careers</title>'
            f'<style>{_STYLE}</style><script type="application/ld+json">{graph}</script></head>'
            f"<body><h1>Open internships</h1>{_NOTE}<ul>{items}</ul></body></html>")


def form_html(post: dict[str, Any], base: str) -> str:
    city, country = post["location"]
    company = html.escape(post["company"])
    data = json.dumps(jsonld(post, base)).replace("</", "<\\/")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Apply — {html.escape(post['title'])}</title>
<meta property="og:site_name" content="{company}"><style>{_STYLE}</style><script type="application/ld+json">{data}</script></head>
<body><h1>{html.escape(post['title'])}</h1><p>{company} · {html.escape(city)}, {html.escape(country)}</p>{_NOTE}
<p>{html.escape(post['description'])}</p>
<form id="application-form" method="post" action="{base}/jobs/{post['id']}/submit" enctype="multipart/form-data">
<label for="first_name">First Name *</label><input id="first_name" name="first_name" required>
<label for="last_name">Last Name *</label><input id="last_name" name="last_name" required>
<label for="email">Email *</label><input id="email" name="email" type="email" required>
<label for="phone">Phone</label><input id="phone" name="phone" type="tel">
<label for="resume">Resume/CV *</label><input id="resume" name="resume" type="file" required>
<label for="linkedin">LinkedIn Profile</label><input id="linkedin" name="linkedin">
<label for="github">GitHub Profile</label><input id="github" name="github">
<div class="field"><div class="label"><b>Are you legally authorized to work in {html.escape(country)}? *</b></div>
<div class="row"><label><input type="radio" name="auth" value="yes" required> Yes</label>
<label><input type="radio" name="auth" value="no"> No</label></div></div>
<label for="sponsor">Will you now or in the future require visa sponsorship? *</label>
<select id="sponsor" name="sponsor" required><option value="">Select...</option><option>Yes</option><option>No</option></select>
<label for="months">How many months are you available for the internship?</label><input id="months" name="months" type="number">
<label for="why">Why do you want to work at {company}? *</label><textarea id="why" name="why" required></textarea>
<label for="cover_letter">Cover Letter</label><textarea id="cover_letter" name="cover_letter"></textarea>
<div class="row" style="margin-top:14px"><label><input type="checkbox" name="consent" required> I agree to the privacy policy *</label></div>
<button type="submit">Submit application</button></form></body></html>"""


def record(posting_id: str, fields: dict[str, Any]) -> str:
    """Keep a submission and return its confirmation number."""
    with _lock:
        SUBMISSIONS.append({"posting_id": posting_id, "received_at": datetime.now(UTC).isoformat(), **fields})
        return f"DEMO-{1000 + len(SUBMISSIONS)}"


def confirmation_html(number: str, post: dict[str, Any]) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Application received</title><style>{_STYLE}</style>"
            f"</head><body><h1>Thank you for applying!</h1><p>Your application to {html.escape(post['company'])} has been "
            f"submitted. Confirmation number: {number}</p>{_NOTE}</body></html>")
