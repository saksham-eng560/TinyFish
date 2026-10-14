# HireFlow: plan to make it resume-ready

> **Status:** phases 1–5 and 7 are done. Phase 6 (a hosted public demo) was built and then removed on purpose:
> HireFlow is meant to run on your own computer (see the [README](../README.md#quick-start)). Later, the "Try the
> demo" buttons were removed as well, since people use HireFlow for real (demo mode stays as a practice copy,
> `./start.sh --demo`), and **Continue with Google** became the first sign-in option. This file is kept as the record
> of the plan.

This plan turns **AutoApply AI** into **HireFlow**, a polished, deployable, resume-ready project.
**Every main feature stays**: scanning, Swipe Review, resume tailoring, cover letters, form filling,
Gmail tracking, Calendar interviews, analytics, the Chrome extension and the Internshala bot.
The work adds a new name, a blue-and-white look, a first-run onboarding flow, stronger guardrails,
a public demo, and the docs and story that recruiters need.

- Part A lists **what to build** (sections 1–9).
- Part B has **copy-paste prompts** for Claude Code, one per phase (section 10).
- Work through the phases in order. Each one leaves the app working, with CI green.

---

## Part A: What to build

### 1. Goals and non-goals

**Goals**
1. A recruiter can open a link and try the whole flow in under 2 minutes, with no install.
2. A new user is guided from sign-up to their first swipe deck by onboarding, without visiting Settings.
3. The app is safe by default: nothing goes out that the user didn't approve, and no eligibility answer is guessed.
4. The repo reads as professional work: clear README, architecture diagram, tests, CI, demo video.
5. You can explain every part of it in an interview.

**Non-goals (for now)**
- A hosted multi-tenant SaaS with real users' job-site logins. The public instance is a **demo**.
  Real use stays **self-hosted** (`./start.sh`).
- Removing features. Risky features stay behind opt-in switches with a consent record, as the
  Internshala bot already does.

---

### 2. Rename: AutoApply AI → HireFlow

> **Check the name first.** "HireFlow" is a common product name. Search the trademark databases
> and look for a free domain (`hireflow.dev`, `gethireflow.app`, `hireflow-ai.vercel.app`). If it's
> taken, use a variant such as "HireFlow AI" so you don't run into a trademark problem.

**Display names and branding**
- `APP_NAME` in `backend/app/config.py` → `"HireFlow"`.
- Every user-facing string: landing page, auth form, dashboard shell, emails (`notifier.py`,
  `gmail_service.py`), calendar event text (`calendar_manager.py`), notifications, the PWA
  `manifest.webmanifest`, `sw.js`, `layout.tsx` metadata, the extension `manifest.json`,
  `popup.html` and `popup.js`.
- New logo: `frontend/public/icon.svg` plus a favicon, PWA icons, extension icons and an OG image (1200×630).
- Tagline idea: **"Your internship search, on autopilot, with you in control."**

**Identifiers (be careful: these can break existing setups)**
| Item | Change | Watch out for |
|---|---|---|
| `COOKIE_NAME` `autoapply_session` | → `hireflow_session` | Logs out existing sessions (fine) |
| `frontend/package.json` name | → `hireflow-dashboard` | Regenerate `package-lock.json` with `npm install`, never by hand |
| Celery app name and queue names (`worker/celery_app.py`) | → `hireflow` | Drain or restart the workers |
| DB name and user in `docker-compose*.yml`, `.env.example`, `DATABASE_URL` default | → `hireflow` | **Existing local databases won't be found.** Document it, or keep `autoapply` as the DB name |
| Docker image names, CI and deploy workflows | → `hireflow-*` | Update the deploy secrets and targets |
| Extension ID and storage keys | → `hireflow_*` | Users must reload the unpacked extension |
| GitHub repo `autoapply-ai` | → `hireflow` | GitHub redirects old URLs; update the clone commands in the README |

**Files that mention the old name today** (from `git grep -il autoapply`):
`.env.example`, `.github/workflows/ci.yml`, `.github/workflows/deploy-backend.yml`, `LICENSE`,
`Makefile`, `PLAN.md`, `README.md`, `backend/Dockerfile`, `backend/app/{config,main}.py`,
`backend/app/api/{serializers,users}.py`, `backend/app/services/{agent_orchestrator,ai_setup,calendar_manager,gmail_service,linkedin_sync,notifier}.py`,
`backend/app/submitters/internshala_apply.py`, `backend/app/worker/*.py`,
`backend/tests/{conftest,test_self_applied}.py`, `docker-compose*.yml`, `extension/*`,
`frontend/{Dockerfile,package.json,package-lock.json}`, `frontend/public/{icon.svg,manifest.webmanifest,sw.js}`,
`frontend/src/app/{globals.css,layout.tsx,page.tsx,dashboard/settings/page.tsx}`,
`frontend/src/components/auth-form.tsx`, `frontend/src/lib/api-client.ts`, `prompts/master_system.txt`,
`scripts/{account,demo_site,internshala_check,server-setup}.py|sh`, `start.sh`, `start.bat`.

**Done when** `git grep -i autoapply` returns only intentional leftovers (for example a
"formerly AutoApply AI" note), and the tests, typecheck and build pass.

---

### 3. Theme: blue and white only

The dashboard already uses CSS variables (`frontend/src/app/globals.css`, `tailwind.config.ts`),
so most of the change is in the tokens. Today's look is warm beige and red with square corners
(`--radius: 0rem`) and a noise texture.

**Light mode: white surfaces, blue accents**
```css
:root {
  --background: 0 0% 100%;           /* #FFFFFF */
  --foreground: 222 47% 11%;         /* #0F172A deep navy text */
  --card: 0 0% 100%;
  --card-foreground: 222 47% 11%;
  --popover: 0 0% 100%;
  --popover-foreground: 222 47% 11%;
  --primary: 221 83% 53%;            /* #2563EB */
  --primary-foreground: 0 0% 100%;
  --secondary: 214 100% 97%;         /* #EFF6FF */
  --secondary-foreground: 222 47% 20%;
  --muted: 214 60% 97%;
  --muted-foreground: 215 25% 40%;
  --accent: 214 95% 93%;             /* #DBEAFE */
  --accent-foreground: 224 64% 33%;  /* #1E3A8A */
  --border: 214 32% 89%;
  --line: 214 32% 80%;
  --input: 214 32% 85%;
  --ring: 221 83% 53%;
  --radius: 0.75rem;
  --noise-opacity: 0;
  /* status: shades of blue, always paired with an icon and a text label */
  --success: 224 76% 40%;  --success-foreground: 0 0% 100%;
  --info:    199 89% 40%;  --info-foreground: 0 0% 100%;
  --warning: 217 33% 35%;  --warning-foreground: 0 0% 100%;
  --destructive: 222 47% 20%; --destructive-foreground: 0 0% 100%;
  /* charts */
  --series-1: #1E3A8A; --series-2: #2563EB; --series-3: #60A5FA;
  --chart-grid: #E2E8F0; --chart-axis: #CBD5E1; --chart-muted: #64748B;
}
```

**Dark mode: deep navy with white text, still blue and white**
```css
.dark {
  --background: 222 47% 7%;   --foreground: 0 0% 100%;
  --card: 222 44% 10%;        --card-foreground: 0 0% 100%;
  --popover: 222 44% 11%;     --popover-foreground: 0 0% 100%;
  --primary: 213 94% 68%;     --primary-foreground: 222 47% 11%;   /* #60A5FA */
  --secondary: 222 40% 16%;   --secondary-foreground: 0 0% 100%;
  --muted: 222 40% 14%;       --muted-foreground: 214 32% 75%;
  --accent: 222 40% 18%;      --accent-foreground: 0 0% 100%;
  --border: 222 30% 22%;      --input: 222 30% 26%;  --ring: 213 94% 68%;
  --series-1: #BFDBFE; --series-2: #60A5FA; --series-3: #2563EB;
}
```

**Rules**
- No reds, greens, ambers or purples. Status is shown with **blue shades plus an icon plus a
  label**: ✓ Applied, ⏳ Needs approval, ✕ Rejected. Never rely on color alone, which also helps accessibility.
- *Decision for you:* error and destructive buttons ("Delete account") read more clearly in red.
  If you keep one exception, make it a single muted red used only for destructive actions.
- Replace the 14 hard-coded hex colors in `frontend/src` (find them with `git grep -nE "#[0-9a-fA-F]{6}" frontend/src`)
  with tokens. Update `motion-graphics.tsx`, the chart colors in `analytics-charts.tsx`, the swipe card
  and status badges, `icon.svg`, the PWA `theme_color` and the extension popup.
- Check contrast: text on white ≥ 4.5:1 (`#2563EB` on white passes at 5.2:1 for normal text).
- Font: Inter or Geist for the UI, JetBrains Mono for numbers and code.

---

### 4. Onboarding: set up the candidate on first run

A full-screen wizard at **`/onboarding`**. It opens right after registration and on every login
until it's finished. Each step saves as you go, so a refresh never loses work. Optional steps can
be skipped, and everything can be changed later in Settings.

**Steps**
| # | Step | What it collects | Required? |
|---|---|---|---|
| 1 | **Welcome** | Name, email (prefilled), phone, current city, timezone | Name yes |
| 2 | **Resume** | Upload PDF or DOCX (≤ 5 MB). It's parsed with the existing `resume_parser`, then a **"Check what we read"** screen shows the extracted education, skills and experience for editing | Yes |
| 3 | **Profiles & links** | LinkedIn, GitHub, portfolio or website, LeetCode / Codeforces, Kaggle, other (label + URL). Validate URL formats; prefill from the resume when found | LinkedIn and GitHub recommended |
| 4 | **What you're looking for** | Target roles (chips plus presets such as "AI engineer · Python"), locations and location focus, remote preference, internship season, year of study, graduation year, focus and avoid skills, expected stipend | Roles and location yes |
| 5 | **Your answers** | The questions forms ask that only *you* can answer: work authorization, visa sponsorship, earliest start date, availability (months), relocation, notice period, pronouns and EEO (each with "Prefer not to say"). Stored as `UserFieldMapping` rows so form filling uses them | Work authorization yes |
| 6 | **How HireFlow applies for you** | Review mode (Swipe Review by default), **"Submit automatically" off by default for new users**, daily cap (default 10, server ceiling 25), resume strategy (original / light / full AI rewrite), cover letters on/off. A short "What happens when you keep a job" explainer | Yes (defaults preselected) |
| 7 | **Connect (optional)** | Gmail + Calendar (Google OAuth), Chrome extension install link, LinkedIn / Internshala (each with its terms-of-service warning and an explicit consent checkbox, saved to `user.consents`) | No |
| 8 | **All set** | Summary card, then "Run my first scan", then a live scan progress bar, then the first Swipe Review deck | — |

**UX details**
- A progress bar ("Step 3 of 8"), Back / Skip / Continue buttons, keyboard-friendly, works on mobile.
- Each step shows *why* it's asked ("Forms ask this; we never guess it for you").
- A "Load sample profile" button in demo mode, so recruiters can click straight through.
- A dashboard banner when a recommended step was skipped ("Add your GitHub to fill 30% more forms").

**Backend changes**
- Migration `0007_onboarding.py` on `users`: `onboarding_step` (int), `onboarding_completed_at`
  (datetime), `github_url`, `portfolio_url` (Text), `profile_links` (JSON list of `{label, url}`).
- `GET /api/v1/users/me/onboarding`: current step, completed steps, and what's missing.
- `PATCH /api/v1/users/me/onboarding`: saves one step's data (validated per step with Pydantic) and advances the step.
- `POST /api/v1/users/me/onboarding/complete`: checks the required fields, sets `onboarding_completed_at`, and optionally starts the first scan.
- Form filling (`form_filler` / field mapping) reads the new link fields for "GitHub", "Portfolio" and "Website" questions.
- Existing users get `onboarding_completed_at` filled in by the migration, so they aren't forced through it.

**Frontend changes**
- `frontend/src/app/onboarding/page.tsx` plus one component per step in `components/onboarding/`.
- A redirect guard in the dashboard layout: when `onboarding_completed_at` is null, go to `/onboarding`.
- Settings gets a "Profile & links" section with the same fields.

**Tests**
- Backend: per-step validation, rejecting a skipped required step, `complete` without a resume returns 400,
  existing users aren't redirected, saved answers become field mappings.
- Frontend: typecheck, plus a Playwright happy path (register → onboarding → first deck) in demo mode.

---

### 5. Guardrails

Several of these already exist (Swipe Review, Needs approval for eligibility questions,
`max_applications_per_day`, the company scam check, Internshala daily limits and consent, encrypted
tokens and cookies). The list below tightens them and fills the gaps.

**5.1 Applying safely**
- [x] **New users start with "Submit automatically" off.** They see the first filled forms before anything is sent.
- [x] **Server-enforced daily cap** with a hard ceiling (for example 25/day) that the UI can't exceed, plus a per-company cap (1 application per role, at most 3 per company per week).
- [x] **Duplicate guard**: never apply twice to the same job URL or to the same company + title.
- [x] **Eligibility is never guessed** (already the rule). Add a test for every eligibility field type.
- [x] **Global "Pause everything"** switch in the header. It stops scans, preparation and submissions immediately.
- [x] **Dry-run mode**: fills the form and takes screenshots but never clicks Submit. It's the default in demo mode.
- [x] **Screenshot before submit**, saved on every application as proof of what was sent.
- [x] **Undo window**: an auto-submitted application waits 10 minutes in "Sending soon" so you can cancel it.

**5.2 AI guardrails**
- [x] **No fabrication.** A tailored resume may only reorder or reword facts from the original. Add a
  check that flags any skill, company, date or degree not in the parsed resume, and blocks it.
- [x] **Prompt injection.** Job descriptions and web pages are untrusted. Wrap them as data in prompts
  and tell the model to ignore instructions inside them. Strip hidden text. Test with a JD containing "ignore previous instructions".
- [x] **Validate output shape**: Pydantic schemas on every LLM response, with a retry on bad output and a safe fallback.
- [x] **Cost caps**: at most N LLM calls per user per day, with a visible usage meter. In the demo, cheap models only.
- [x] **Generated answers shown before submit**, editable and marked "AI-written".

**5.3 Security and privacy**
- [x] Refuse to start in production with the default `SECRET_KEY` (already the case) or a missing `ENCRYPTION_KEY`.
- [x] `COOKIE_SECURE=true` and HTTPS only in production; check CSRF on cookie-auth routes.
- [x] Uploads: check the size limit, the real file type (magic bytes, not just the extension) and filenames; serve files only to their owner.
- [x] Rate limits on login, register, upload and scan endpoints (stricter than the 300/min default).
- [x] Keep PII out of logs: mask emails, phone numbers and tokens.
- [x] **Download my data** (JSON + files) and **Delete my account** (cascades all rows and files).
- [x] An activity log page: every scan, fill and submission with a timestamp (from `agent_run`).
- [x] Dependency and secret scanning in CI (`pip-audit`, `npm audit --omit=dev`, gitleaks).

**5.4 Platform rules and responsible use**
- [x] Sources that forbid automation (LinkedIn, Internshala, Indeed, Glassdoor) stay **off by default**
  and **opt-in with a terms warning and a stored consent**. The public demo never uses them.
- [x] Respect per-site rate limits and back off on 429 or captcha.
- [x] Pages: `/privacy`, `/terms`, `/responsible-use` (what it does, what it won't do, the risks).

**5.5 Demo-mode guardrails** (`DEMO_MODE=true`)
- [x] Real submissions, real job-site logins and Gmail sending are off. Applications go to the bundled demo careers site.
- [x] A seeded demo account with a "Try the demo" one-click login; the data resets every night.
- [x] Demo users can't change passwords or connect accounts; a banner says "Demo — nothing is really sent".

---

### 6. Making it resume-friendly (polish)

**Landing page** (`frontend/src/app/page.tsx`)
1. Hero: name, tagline, **Try the demo** + **View on GitHub** buttons, product screenshot or loop video.
2. A 60-second demo video (Loom or YouTube, or an autoplay MP4).
3. "How it works" in three steps: **Find → Swipe → Apply & track**.
4. Feature grid: Swipe Review, resume tailoring, form filling across ATSs, Gmail tracking, interview prep, analytics.
5. "Safe by design": the guardrails from section 5 in plain words.
6. Architecture diagram and tech stack logos.
7. FAQ, then a footer with privacy, terms, GitHub and the author's links.
8. SEO: title, description, OG image, sitemap.

**Dashboard polish**
- Empty states that say what to do next, loading skeletons, friendly error messages with a retry.
- Mobile layout for Swipe Review (swipe gestures) and the overview.
- Accessibility: focus rings, labels, keyboard swipe (← →), contrast.

**README rewrite**
- Order: logo + one-liner → demo link + video → screenshots → features → **architecture diagram**
  → tech stack → quick start (`./start.sh --demo`) → guardrails → testing → deployment → roadmap → license.
- Badges: CI, coverage, license, live demo.
- Move the long feature docs into `docs/` so the README stays short.

**Code quality**
- CI green: ruff, mypy (or pyright), pytest with a coverage badge, ESLint, `tsc --noEmit`, `next build`.
- Remove dead code and outdated notes in `PLAN.md`.
- `docs/ARCHITECTURE.md`: components, the data flow of one application, why Celery and Redis, why Playwright, how the LLM provider fallback works.

**Numbers to measure for your resume** (measure them, don't guess)
- Jobs scanned per run and scan time.
- Form-fill success rate per ATS (Greenhouse, Lever, Ashby, Workday) on the demo site and in tests.
- Number of tests and coverage %.
- Time from sign-up to first swipe deck after onboarding.

---

### 7. Deployment (public demo)

| Part | Service (free or cheap tier) |
|---|---|
| Frontend | Vercel |
| API + Celery worker + beat | Render, Railway or Fly.io (worker and beat can share one small instance in demo) |
| PostgreSQL + pgvector | Neon or Supabase |
| Redis | Upstash |
| File storage | Cloudflare R2 (`STORAGE_BACKEND=s3`) |
| LLM | A cheap model with a strict per-day cap, or none (canned demo outputs) |
| Monitoring | Sentry (frontend + backend), an uptime check (UptimeRobot / Better Stack) |
| Domain | `hireflow.<tld>` or the free Vercel subdomain |

Steps: production env vars (`ENVIRONMENT=production`, `DEMO_MODE=true`, keys, `CORS_ORIGINS`,
`COOKIE_SECURE=true`) → run migrations on deploy → seed the demo account → nightly reset job →
smoke test → link it from the README and landing page.

---

### 8. Resume and interview kit

**Resume entry**
> **HireFlow**: AI internship-application assistant · *FastAPI, Next.js, PostgreSQL/pgvector, Celery, Redis, Playwright, LLMs* · [Live demo] · [GitHub]
> - Built an agent that scans **N+** postings per run from ATS APIs and job boards, ranks them by resume match (vector embeddings) and queues them for a Tinder-style human review.
> - Automated tailored resumes, cover letters and form filling across **4 ATS platforms** with a **X%** fill success rate; eligibility questions are never auto-answered.
> - Integrated Gmail and Google Calendar to track replies and schedule interviews automatically; added guardrails (daily caps, dry-run, prompt-injection defenses, no-fabrication checks).
> - **N** tests, CI/CD with GitHub Actions, deployed on Vercel + Render with Sentry monitoring.

**Questions to prepare**
- Walk through one application from posting to submission.
- Why Celery and Redis instead of doing it in the request? What happens when a worker crashes mid-application?
- How do you handle a site changing its HTML? How do you detect which ATS a site uses?
- How do you stop the LLM from inventing experience? How do you handle prompt injection in a job description?
- How would you scale to 10,000 users? (Browser worker pools, queues per user, rate limiting, cost.)
- What would you do differently? What was the hardest bug? (Have one real story ready.)

---

### 9. Phases and checklist

| Phase | Work | Rough time | Status |
|---|---|---|---|
| 1 | Rename to HireFlow (section 2) | 0.5–1 day | ✓ Done |
| 2 | Blue and white theme (section 3) | 1 day | ✓ Done |
| 3 | Onboarding: backend + frontend + tests (section 4) | 3–4 days | ✓ Done |
| 4 | Guardrails (section 5) | 2–3 days | ✓ Done |
| 5 | Landing page, README, architecture doc, polish (section 6) | 2 days | ✓ Done |
| 6 | Demo deployment (section 7) | 1–2 days | Removed: HireFlow runs locally (`./start.sh --demo` is the demo) |
| 7 | Measure numbers, record the video, update resume (section 8) | 1 day | ✓ Measured (below); the video and the resume entry are yours |

**Measured** with `scripts/metrics.py` (see [TESTING.md](TESTING.md#the-numbers)), for the resume entry in section 8:

| Number | Measured |
|---|---|
| Tests | 455 backend (pytest) + 19 end-to-end (Playwright, through the real dashboard); 81% backend coverage |
| Time from sign-up to the first swipe deck | 0.9 s of server time; about 5 s through the UI (e2e) |
| Jobs per scan and scan time (demo careers site) | 12 postings found and all 12 in the deck, in 0.3 s |
| Form-fill success (demo careers site, Chromium, dry run) | 12 of 12 forms, 100% of fields, every required field |
| Time per kept job (tailor, cover letter, fill) | about 2 s median |

Per-ATS fill rates on real Greenhouse, Lever, Ashby and Workday postings, and jobs per scan on real sources, come
from real use: run `scripts/metrics.py --from-db "$DATABASE_URL"` against your own deployment.

Each phase is its own branch and PR, with CI green before merging.

---

## Part B: Prompts for Claude Code

Paste these one at a time, in order. Each assumes the previous phase is merged.

### Master context (paste first in every new session)
```
You are working on HireFlow (formerly AutoApply AI), an AI internship-application assistant:
FastAPI + SQLAlchemy/Alembic + PostgreSQL/pgvector + Celery/Redis + Playwright backend in backend/,
Next.js 14 + Tailwind + Radix dashboard in frontend/, Chrome extension in extension/.
Read docs/HIREFLOW_PLAN.md before starting. Rules:
- Never remove an existing feature. Risky features stay opt-in with a consent record.
- Keep the existing code style; add tests for every behavior change.
- Before finishing, run: ruff, pytest (backend), npm run typecheck && npm run lint && npm run build (frontend).
- Regenerate lockfiles with tooling, never by hand. New DB columns need an Alembic migration.
- Work on a new branch and open a PR when done; summarize what changed and how you tested it.
```

### Phase 1: Rename
```
Do section 2 of docs/HIREFLOW_PLAN.md: rename AutoApply AI to HireFlow everywhere users can see it
(UI, emails, calendar text, notifications, PWA manifest, service worker, extension, README, prompts),
and rename the identifiers in the table (cookie name, package name, Celery app, Docker images, CI).
Keep the database name `autoapply` unless I confirm otherwise, and document it in the README.
Make a simple blue-and-white "HireFlow" SVG logo and replace the icons. Finish with `git grep -i autoapply`
showing only intentional leftovers, and all checks passing.
```

### Phase 2: Theme
```
Do section 3 of docs/HIREFLOW_PLAN.md: switch the frontend to a blue-and-white theme using the exact
tokens given, for light and dark mode. Set --radius to 0.75rem and remove the noise texture.
Replace all hard-coded colors in frontend/src with tokens, recolor charts, status badges, swipe cards,
motion graphics, the PWA theme_color and the extension popup. Status must use blue shades + icon + label.
Check contrast (WCAG AA). Take before/after screenshots of landing, overview, Swipe Review and settings
with Playwright and update docs/screenshots.
```

### Phase 3: Onboarding
```
Do section 4 of docs/HIREFLOW_PLAN.md: build the 8-step onboarding wizard at /onboarding.
Backend: Alembic migration 0007 (onboarding_step, onboarding_completed_at, github_url, portfolio_url,
profile_links; backfill onboarding_completed_at for existing users), GET/PATCH /users/me/onboarding and
POST /users/me/onboarding/complete with per-step Pydantic validation; saved answers become UserFieldMapping
rows; form filling uses the new link fields. Reuse the existing resume parser for step 2 and show an
editable "check what we read" screen. New users get auto_submit_kept=false and max_applications_per_day=10.
Frontend: wizard with progress bar, back/skip/continue, autosave per step, mobile-friendly, a redirect
guard in the dashboard layout, and a "Profile & links" section in Settings. Step 8 starts the first scan
and lands on Swipe Review. Add backend tests and a Playwright happy-path test in demo mode.
```

### Phase 4: Guardrails
```
Do section 5 of docs/HIREFLOW_PLAN.md, checklist by checklist. Already present: swipe review, Needs approval
for eligibility questions, max_applications_per_day, company scam check, Internshala consent/limits,
encrypted tokens. Add: server-enforced daily cap ceiling and per-company cap, duplicate-application guard,
global Pause switch, dry-run mode, screenshot before submit, 10-minute undo window for auto-submits,
no-fabrication check on tailored resumes, prompt-injection hardening for job descriptions (with a test),
Pydantic validation of LLM outputs, per-user LLM cost cap, stricter rate limits on auth/upload/scan,
upload magic-byte checks, PII masking in logs, data export and account deletion, an activity log page,
pip-audit/npm audit/gitleaks in CI, /privacy /terms /responsible-use pages, and DEMO_MODE as described
in 5.5. One commit per checklist group, each with tests.
```

### Phase 5: Landing, README and polish
```
Do section 6 of docs/HIREFLOW_PLAN.md: rebuild the landing page with the sections listed (hero with
Try the demo + GitHub, video slot, how it works, features, safe by design, architecture, FAQ, footer, SEO + OG image).
Add empty states, loading skeletons, error retries, mobile Swipe Review with gestures and arrow keys.
Rewrite README.md in the given order with badges, move long docs into docs/, write docs/ARCHITECTURE.md
with a Mermaid diagram, and add a script that prints the resume numbers (jobs per scan, scan time,
fill success per ATS on the demo site, test count, coverage).
```

### Phase 6: Deployment
```
Do section 7 of docs/HIREFLOW_PLAN.md: prepare a demo deployment — Vercel config for frontend/,
a Render (or Fly) blueprint for the API + worker + beat, Neon Postgres with pgvector, Upstash Redis,
R2 storage, Sentry for both apps, migrations on deploy, demo seeding, a nightly demo reset task, and a
smoke-test script. Write docs/DEPLOY.md listing every env var and step. Do not put secrets in the repo.
```
