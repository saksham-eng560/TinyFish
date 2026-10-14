# Security, privacy and responsible use

How HireFlow protects your data and accounts, and the rules it follows. The in-app pages
[/privacy](../frontend/src/app/privacy/page.tsx), [/terms](../frontend/src/app/terms/page.tsx) and
[/responsible-use](../frontend/src/app/responsible-use/page.tsx) say the same in plain words.

## Security and privacy

- **Passwords**: bcrypt.
- **Sessions**: httpOnly SameSite cookies, with `COOKIE_SECURE` in production. Short-lived scoped
  tokens for the WebSocket and the extension.
- **Credentials**: OAuth refresh tokens and the LinkedIn and Internshala sessions are encrypted at rest
  (AES-256-GCM), and never returned by the API or included in your data export.
- **Isolation**: every query is scoped to the signed-in user, and file downloads are checked against
  the owner.
- **LLM prompts** never include passwords or tokens. Resume content is sent to the configured model
  provider only.
- **Your data**: **Settings → Export your data** gives you everything as a ZIP (your data as JSON plus your files). **Delete my account**
  removes the database rows, stored files and tokens. Closed applications are purged after `DATA_RETENTION_DAYS`.
- **Rate limits**: per-platform application limits and randomized pacing protect your accounts.
- **Secrets**: all keys live only in `.env` on your machine or server. That file is created with
  permissions for your user only and is excluded from git. Never paste keys into the README, issues,
  chat or the command line. If a key is ever exposed, revoke it at the provider and put a new one
  in `.env`.
- **Production**: the setup scripts generate strong secrets and enable HTTPS and secure cookies.
  Turn off sign-ups once your own account exists.

## Responsible use

HireFlow is a personal tool. It applies **as you**, with **your real information**, to jobs **you
approved**.

- **Truthfulness is enforced.** The agent can reword and reorder your experience, but it cannot invent
  it. Review every application anyway: you are the one submitting it.
- **Respect site terms.** Some job sites, notably LinkedIn, Internshala, Indeed and Glassdoor, restrict
  automated access in their terms of service, and automated use can get an account restricted. The
  Internshala bot is off until you turn it on.
  - The public ATS APIs (Greenhouse, Lever, Ashby, Workday) and company careers pages are the most
    reliable sources.
  - Use browser-based sources sparingly and keep the default rate limits.
  - You are responsible for how you use this tool.
- **Volume with care.** Mass applying works best when you keep the jobs you'd genuinely take, keep
  your saved answers accurate, and stay within the default rate limits.
