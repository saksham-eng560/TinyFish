# Testing and CI

```bash
make test        # the backend suite on SQLite, including real-Chromium tests
make test-pg     # the same suite on PostgreSQL + pgvector (TEST_DATABASE_URL)
make e2e         # only the backend's browser end-to-end tests
make e2e-demo    # Playwright through the real dashboard in demo mode (run `npm run build` in frontend/ first)
make lint        # ruff (backend, scripts, e2e), ESLint + TypeScript (dashboard), colour contrast
make metrics     # the numbers below, measured (ARGS=--coverage adds a coverage run)
```

## What's covered

**Backend** (`backend/tests/`, pytest). Unit and API tests for every service and route, scraper tests
against saved fixtures, and real-browser tests:

- `test_e2e_pipeline.py` starts a mock company site with a careers page and an ATS form, then runs the real
  pipeline (scan, match, tailor, fill in Chromium, pause, approve, submit) and asserts the exact fields the
  employer received, and that nothing was sent before approval.
- `test_form_filling.py` fills an Indian internship form with the fields that trip up naive fillers
  (dial-code dropdowns, masked phone inputs, length limits, follow-up questions).
- `test_guardrails.py`, `test_ai_guardrails.py`, `test_security_privacy.py`, `test_platform_rules.py`,
  `test_eligibility.py` and `test_demo_mode.py` pin down the rules in [ARCHITECTURE.md](ARCHITECTURE.md#guardrails-and-where-theyre-enforced):
  caps, duplicates, the undo window, pause, dry run, truthfulness, prompt injection, output validation,
  budgets, CSRF, uploads, PII in logs, data export and deletion, consent-gated sources, back-off, and that
  the demo never reaches a real site.
- `test_import_autoapply.py` brings a real AutoApply AI layout over (database in WAL mode with a pending change,
  stored files, `.env`): every row and file arrives, the keys and personal settings come along, machine settings
  don't, secrets are never printed, HireFlow's new columns and indexes are added, existing HireFlow accounts are
  never overwritten without `--replace`, and the AutoApply folder is byte-for-byte unchanged.
- `test_google_signin.py` runs **Continue with Google** end to end with Google faked: a new account is created,
  an existing one with the same email is signed in, an unverified email, a cancel, a closed registration or a
  disabled account is turned away, and Google's answer only counts in the browser that started the sign-in
  (for Gmail connect too).

**End to end** (`e2e/`, Playwright, through the built dashboard in demo mode, `scripts/e2e.sh`):

- sign up, load the sample profile in onboarding, run the first scan, land on a Swipe Review deck, keep a
  job and find its form filled in Ready to submit (dry run);
- the shared demo account opens with its Swipe Review deck ready;
- **Continue with Google** on the sign-in and sign-up pages: it explains the one-time setup until Google is
  configured, starts Google's sign-in once it is, turns Google's answers into plain messages, and is hidden in the
  demo; no "Try the demo" button anywhere;
- the landing page: every section, its links, the FAQ by keyboard, SEO tags, the 1200×630 social image,
  `robots.txt` and `sitemap.xml`;
- Swipe Review on a phone: a real touch swipe keeps, the left arrow skips, nothing scrolls sideways;
- accessibility: axe-core finds no serious or critical problems on the public pages and every dashboard
  page, in the light and the dark theme;
- when the API can't be reached (not started yet, or stopped), sign-in and creating an account say so instead of
  showing a status code, and real API errors keep their own message;
- the smoke test (`scripts/smoke_test.py`) passes against these servers; run it against any running HireFlow
  with `python3 scripts/smoke_test.py --site http://localhost:3000 --api http://localhost:8000 --demo`.

## The numbers

`scripts/metrics.py` measures them instead of guessing (see its `--help`). The demo benchmark uses a
throwaway database in demo mode with no AI keys, so anyone can reproduce it:

| Metric | Measured |
|---|---|
| Sign-up to first Swipe Review deck | under 1 s of server time; about 5 s through the UI in the Playwright test |
| First scan of the demo careers site | 12 postings found and in the deck in about 0.3 s |
| Forms filled on the demo careers site (Chromium, dry run) | 12 of 12, every required field filled |
| Time per kept job (tailor, cover letter, fill) | about 2 s median |
| Tests | 455 backend (pytest) + 19 end-to-end (Playwright) |
| Backend coverage | 81% of `app/` (`--coverage`) |

Fill rates on real Greenhouse, Lever, Ashby and Workday postings can only come from real use:
`scripts/metrics.py --from-db "$DATABASE_URL"` reports them per ATS from your own database, along with jobs
found per scan and scan time.

## CI

[`ci.yml`](../.github/workflows/ci.yml) runs on every push and pull request:

- **Backend**: ruff, bandit, a migration round trip (`upgrade`, `check` for drift, `downgrade`, `upgrade`),
  and the suite on SQLite and on PostgreSQL + pgvector with coverage.
- **Dashboard**: ESLint, `tsc --noEmit`, a WCAG AA colour-contrast check of the theme tokens, `next build`.
- **Security**: `pip-audit`, `npm audit --omit=dev` and gitleaks over the code and its history.
- **End to end**: the Playwright tests above, against a fresh demo-mode API and the production build.
- **Extension**: manifest and script validation, packaging.
- **Docker**: both images build, and the backend image launches Chromium.
