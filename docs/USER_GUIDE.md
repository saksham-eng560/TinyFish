# Using HireFlow

Everything HireFlow does, and how to set it up for real. Start with the [README](../README.md) for a
quick overview.

## What it does

**Discovery.** Scans Greenhouse, Lever, Ashby and Workday boards through their public APIs, reads
company careers pages (schema.org `JobPosting` markup, and embedded ATS boards it detects), and
searches LinkedIn, Indeed, Glassdoor and Wellfound with a real browser. It removes duplicates across
sources, and scans run on a schedule (every 6 hours by default) or when you click **Scan for jobs now**.
You can also paste any job URL.

**Matching.** Each job gets a 0–100 score across five parts: skills, experience, industry, location
and compensation. The score also explains which of your skills are strong matches and which the job
wants that you don't have. Only your hard filters remove a job automatically (companies to avoid,
excluded title keywords, job types, expired deadlines and, optionally, postings that don't sponsor
visas). Everything else — even a low score — goes to **Swipe Review**, best matches first, with
heads-ups such as "not a remote role". (The original automatic mode, which skips jobs under a
threshold and prepares the rest, is still available in Settings.)

**Your resume, your way.** By default the agent sends **your original resume file, unchanged**.
Switch to *light tweaks* (every word kept, relevant items moved to the top) or *full AI tailoring*
in Settings › Mass apply. With full tailoring, your master resume is rewritten for each job: the summary, bullet order,
emphasis and wording (for example, using the job's name for a skill you really have). A guard checks
the result against your master resume and reverts anything that adds an employer, title, date,
degree, skill or number that isn't in the original. You get an ATS-friendly PDF (classic or modern
template) and a cover letter that references the actual role.

**Form filling.** Dedicated submitters for Greenhouse, Lever, Workday and LinkedIn Easy Apply, and a
generic engine that handles any other HTML form: text, selects, radios, checkboxes, file uploads and
multi-step flows. Answers come from your saved answers, your resume and deterministic rules first,
then from the AI for open-ended questions. Eligibility questions such as visa sponsorship and work
authorization are **never guessed**. If you haven't saved an answer, the question stays blank and
flagged until you answer it, and the agent remembers your answer for next time. EEO questions default
to "decline to self-identify". Browsing uses human-like typing, mouse paths and pacing, and supports
proxy rotation and CAPTCHA solving (2Captcha / Anti-Captcha). Per-platform rate limits are enforced.

**Swipe, then submission.** Keep a job and it's prepared. With **Apply automatically** on (new accounts
start with it off), the filled application waits 10 minutes in **Sending soon**, where you can stop it,
and is then submitted, provided every eligibility question has your saved answer.
Anything else waits in **Needs approval** with the filled-form screenshot, tailored resume, cover
letter, answers and match analysis. Submission happens
in a fresh browser session that re-fills the form with what you approved. The agent checks for a
confirmation page, saves a screenshot, and records the confirmation number when there is one.
**Dry run** (Settings › Mass apply; on in the demo) or `SUBMISSION_DRY_RUN=true` (the whole server) disables the final click entirely.

**Inbox and calendar.** With Google connected, the agent reads job-related e-mail (Pub/Sub push or
polling every 5 minutes) and classifies each message: rejection, interview invite, assessment, offer,
information request, or acknowledgement. It links the message to the right application and updates
its status. Messages are filed under `HireFlow/…` Gmail labels, and reply drafts are prepared
(never sent) for invites and requests. Interviews are added to Google Calendar with a prep document
covering the company, likely questions, STAR stories drawn from your resume, and questions to ask.
Reminders go out before each interview.

**Dashboard.** Next.js dashboard with a landing page, an overview, **Swipe Review**, an applications
pipeline, a job browser, an e-mail feed, interviews, analytics (response, interview and offer rates,
time to response, platform effectiveness, keywords that get callbacks), a resume editor, agent logs,
and settings. Live updates arrive over WebSocket. It works on phones, can be installed as a PWA, and
sends browser notifications.

**Design.** Blue and white: white surfaces with HireFlow blue accents, and a deep-navy dark theme. Status
is always an icon plus a label in shades of blue, never colour alone; the only red is for destructive
actions. Inter for the interface and JetBrains Mono for numbers, rounded corners throughout. It's built
from [shadcn/ui](https://ui.shadcn.com) (Radix) components restyled through the design tokens in
`frontend/src/app/globals.css`, with framer-motion for the swipe deck, page transitions, staggered lists,
counting numbers and the landing-page pipeline graphic. Every animation turns off when your system's
"reduce motion" setting is on. The logo, favicon, PWA and extension icons share one mark, a bold white "H"
whose crossbar rises, on blue (`frontend/public/icon.svg`).

**Notifications.** In-app, browser, e-mail (SMTP or your own Gmail), Discord and Slack. Every update on
a job you applied to (submitted, "I Applied", reply, test, interview, offer, rejection) goes to all of
them, and a progress e-mail summarises everything each evening (see [below](#i-applied-and-progress-tracking)).

**Your data.** Download everything (a ZIP: your data as JSON plus your files), or delete your account with all files and tokens (GDPR/CCPA).
OAuth tokens and cookies are encrypted with AES-256-GCM, and closed applications older than the retention period are
cleaned up automatically.

## Swipe Review and mass applying

**Swipe Review** (`/dashboard/review`) is a deck of every job that passed your filters, best matches
first. Each card shows the role, company, location, term, salary, visa sponsorship, the score
breakdown, the skills you have and the ones they want.

- **Drag right** or press **→** to keep: the agent tailors, fills and applies.
- **Drag left** or press **←** to skip.
- **Z** undoes the last swipe (until preparation has started).
- **A** or **I Applied to this myself**: you already applied on your own. The card flies off the deck,
  moves to Applied and is tracked from then on.
- **Keep in bulk**: keep every card at or above a score in one click (with the current filters).
- Filters: search, internship / full-time, remote only. A counter shows how many are left.

**Presets** (Settings › Mass apply) set everything up in one click and keep your own lists:

| Preset | What it adds |
|---|---|
| **AI engineer · Python** | Built from an AI-engineering resume: AI engineer, generative AI, LLM, Python / FastAPI and backend internships plus big tech software internships. Java, Spring Boot and data-science / analyst roles are left out (Tech focus and Skip these technologies in Settings › Preferences). Only changes what you look for: your sources, limits and location stay. |
| **India · Summer 2027** | Internships only for Summer 2027, ~90% in India with Delhi NCR first: Internshala, LinkedIn India and Indeed India, plus the Summer 2027 lists for the rest. |
| **Internships** | Intern versions of your target roles, internship-only job types, the SimplifyJobs and vanshb03 internship lists (refreshed daily), 107 startup Greenhouse / Ashby / Lever boards, the daily limit at the server's cap (25), 300 jobs per source per scan. |
| **Startups** | The 107 startup boards, keeping your roles and job types. |
| **New grad** | Entry-level full-time roles from the SimplifyJobs new-grad list plus the startup boards. |

Mass-apply settings (all in Settings › Mass apply):

| Preference | Default | Meaning |
|---|---|---|
| `resume_strategy` | `original` | Which resume is sent. `original`: your uploaded file, byte for byte (your design and words). `light`: every word kept, only the most relevant bullets, projects and skills moved to the top per job. `full`: AI rewrite, guarded against invented facts. |
| `review_mode` | `swipe` | `swipe`: nothing is skipped for a low score. `auto`: the original threshold mode. |
| `auto_submit_kept` | off | Submit kept jobs once the form is filled, after a 10-minute undo window (**Sending soon**). Off: every kept job waits for your approval. |
| `dry_run` | off | Fill forms and take screenshots, but never click Submit. On in the demo. |
| `trust_generated_answers` | on | The agent's answers to open questions ("Why this company?") don't hold a kept job back. Eligibility questions are never guessed either way. |
| `auto_keep_min_score` | off | Keep jobs scoring at least this without swiping. |
| `exclude_no_sponsorship` | off | Skip postings that say they don't sponsor visas or require citizenship. |
| `max_jobs_per_source` | 50 | How many postings each source may return per scan (10–1000). |
| `sources.internship_lists` | SimplifyJobs + vanshb03 | Curated lists: `simplify-internships`, `vanshb03-internships`, `simplify-new-grad`, or any `listings.json` URL in the same format. |

Every submission also respects your daily limit (25 at most, enforced by the server), at most 3
applications to one company a week, never the same job twice (same link, or same company and title),
and per-platform caps with randomized cool-downs (for example 10 Workday applications a day).

## Internships in India: Summer 2027, Delhi NCR first

Out of the box the agent hunts **internships only**, for **Summer 2027**, with **about 90% of every scan
in India** and **Delhi NCR** (Delhi, New Delhi, Gurugram, Noida, Greater Noida, Faridabad, Ghaziabad)
as the prime location. All of it is in Settings › Preferences › Internship focus.

- **Where it looks.** [Internshala](https://internshala.com) (India's biggest internship board, searched
  by your roles in your prime cities, work-from-home and all of India; paste your own Internshala search
  URLs in Settings › Job sources), LinkedIn searched for "Delhi, India" and "India", Indeed and
  Glassdoor on their Indian sites (`in.indeed.com`, `glassdoor.co.in`), plus the Summer 2027 GitHub lists
  and startup boards for the remaining ~10%.
- **What you see first.** Swipe Review shows prime-city internships first, then the rest of India, then
  remote, then abroad; within each group, postings that name Summer 2027 come first. Cards carry
  **Prime location**, **India** and **Summer 2027** badges.
- **~90% India.** After each scan the agent keeps roughly 9 Indian postings for every 1 from elsewhere
  (remote roles preferred). Cities such as "Bengaluru, Karnataka" count as India even when the listing
  doesn't say so. Change the share with the slider (50–100%) or switch the focus off.
- **Summer 2027.** Postings clearly for another term ("Summer 2026", "Fall '26", "Intern 2026") are
  skipped. Ones that don't say are kept, and ones that start immediately get a heads-up.
- **Intern roles only, for your year.** Every source (big tech, top companies, Internshala, LinkedIn,
  the lists) keeps internships only: full-time, new-grad and contract jobs, and full-time jobs about
  interns ("Intern Program Manager"), never reach your deck. With your year of study (default 2nd year)
  and graduation year (read from your resume, or set it), internships for other students are skipped
  too: final-year or pre-final-year only, "rising seniors", PhD / Master's / MBA only, another graduating
  batch, or "2+ years of experience". Ones that name your year (Google STEP, "1st and 2nd year
  students") are tagged **Open to 2nd-year students**. Settings › Preferences › Search preferences.
- **Your tech focus.** With **Tech focus** (e.g. AI, LLMs, Python, FastAPI) a posting must name one of
  them; with **Skip these technologies** (e.g. Java, Spring Boot) a role whose title names one, or that
  asks for one and none of your languages or frameworks, is left out. "Java or Python" roles and big tech
  software internships stay. Cards already waiting that don't fit are skipped with the reason.
- **At most 10 from Internshala a scan.** Each scan adds the best 10 new Internshala postings at most
  (and never more than 25% of the scan), so they don't crowd out the rest. Settings › Mass apply.
- **Internshala applications** need your own Internshala login. By default the agent prepares your
  resume and answers and asks you to apply there; click **I Applied** afterwards and it's tracked. If
  you turn on the opt-in **Internshala bot** (see
  [LinkedIn, Internshala and the Chrome extension](#linkedin-internshala-and-the-chrome-extension)),
  the agent fills the Internshala form itself with your synced login and sends it when you click
  **Submit**.

Internshala changes its pages from time to time. Check the scraper on your server with
`backend/.venv/bin/python scripts/test_scraper.py internshala -k "Software Engineer" -l Delhi`.

## "I Applied" and progress tracking

Applied to something on your own? Click **I Applied**. It's on every job in All jobs, every card in
Applications, the application page and Swipe Review (key **A**). The job moves to **Applied** and into
its own section, **I Applied** (`/dashboard/applied`), and the agent stops working on it:

- It watches your Gmail for replies from that company and updates the status by itself (applied →
  heard back → interview → offer, or closed). Nothing ever moves backwards.
- Every update is sent **everywhere**: the dashboard and browser, your Gmail, and Discord/Slack if
  connected ("Application status: applied → interview (SDE Intern @ Zomato)").
- A **progress e-mail** at about 8 PM your time (daily by default; weekly or off in Settings ›
  Integrations › Notifications) lists what changed, where everything stands, applications with no
  reply after 7 days (time for a polite follow-up) and interviews this week. **Progress e-mail** on the
  I Applied page sends one right away.
- **Log an application** adds one the agent never found (a referral, a company site): company, role,
  link, date and notes.

The I Applied page shows how far your applications got (applied, heard back, interviewing, offers),
lets you filter and search them, and update a status by hand when a recruiter calls instead of e-mailing.

## Fast scans with a live progress bar

A scan searches all your job sources **at the same time** and loads company boards (Greenhouse,
Lever, Ashby, Workday, career pages) several at once. Postings it has already saved are not
downloaded again, and Claude scores several jobs at once while the rest get an instant score. Cards
land in Swipe Review as they're scored, so you can start swiping before the scan ends.

While a scan runs, Overview and Swipe Review show a **progress bar**:
- the percentage, time elapsed and time left;
- the step it's on (search → save → score → done);
- each source with its status and how many postings it found;
- a **Stop** button. Jobs already scored stay in your deck.

Every other page shows a compact "Scanning 42%" in the top bar.

A slow site never holds up a scan. Each source wraps up with what it has at 80% of
`SCAN_SOURCE_TIMEOUT_SECONDS` (240 s by default). One still running at the limit is left out of that
scan, and the rest carry on. The speed settings are optional `.env` entries, and the defaults suit
most setups:

| Setting | Default | What it controls |
|---|---|---|
| `SCAN_SOURCE_CONCURRENCY` | 8 | job sources searched at the same time |
| `SCRAPER_BOARD_CONCURRENCY` | 6 | company boards or pages loaded at once within a source |
| `SCAN_LLM_CONCURRENCY` | 6 | jobs Claude scores at the same time |
| `SCAN_SOURCE_TIMEOUT_SECONDS` | 240 | time limit for a single source |

## How forms get filled

For every field on an application form, the agent picks the right way to fill it and then **checks
the page kept the value**. If it didn't, for example a React form that ignored the typing, it sets the
value again the way the page expects.

- **Dropdowns, radio buttons and suggestion lists** match the way forms word things. "India" picks
  "India (+91)", not "British Indian Ocean Territory". "B.Tech" picks "Bachelor's Degree", "USA"
  picks "United States" and "Bangalore" picks "Bengaluru". When no option really fits, the field is
  left for you in Needs approval instead of taking a wrong answer.
- **Dates and numbers** are typed the way the field expects. "2 weeks" becomes a real start date in a
  date picker, "₹15,000" becomes `15000` in a number box, and a `DD/MM/YYYY` box gets that format.
- **Length limits** are respected. A 300-character box gets an answer cut at a sentence or word
  boundary.
- **Follow-up questions** that appear after an answer ("If yes, please explain", a city after a
  country) are found on a second look and filled too.
- **Education and location questions** are answered from your resume: college, degree, branch,
  graduation year, CGPA, city, country and phone country code. You can override any of them under
  **Settings › Saved answers**.
- **Facts are never guessed.** Date of birth, ID numbers, visa and work authorization are filled only
  from your saved answers. Otherwise the application waits for you.
- **Written answers** ("Why this internship?") are specific, grounded in your resume and the job,
  and fit the field's length limit.

## Ready to submit: check it, fix it, send it

When the agent has filled an application and is waiting for you, it appears in **Ready to submit**
(`/dashboard/submit`, in the sidebar with a count). Applications show one at a time, best match first,
with a screenshot of the filled form. Anything that needs your attention is at the top: a required
question it couldn't answer, a low-confidence answer, a field it couldn't fill.

Each prefilled item has two buttons:

- **✓ Correct** confirms it.
- **✗ Fix** turns it into an edit box right there. What you type is exactly what gets sent.

The cover letter is editable in place, and the resume links to the PDF that will be uploaded. When
every row is checked, press **Submit application**. The agent opens the form again, fills it with what
you approved, including your fixes, and submits. Then the next application loads.

| Key | Action |
|---|---|
| `Y` | Confirm the selected row and move to the next |
| `N` | Fix the selected row |
| `J` / `K` | Move down / up |
| `A` | Confirm all rows that aren't flagged |
| `Enter` | Submit, once everything is confirmed |
| `S` | Skip for now |

Your fixes are learned. Corrected answers to common questions (notice period, relocation, highest education
and work authorization) are saved, so they come pre-filled correctly next time.

Internshala postings are submitted here too once the [Internshala bot](#the-internshala-bot-opt-in) is
on. Otherwise the card shows **Apply on Internshala** with your prepared answers (each with a **Copy**
button) and **I Applied** for afterwards.

## Using it for real

1. **Upload your master resume** in Resume Lab and check the parsed result. Everything the agent writes
   is derived from it, so make it complete and accurate.
2. **Saved answers** (Settings): fill in work authorization, sponsorship, notice period, salary
   expectation, address, EEO preferences and so on. These answer most form questions directly.
3. **Mass apply** (Settings): apply the **Internships**, **Startups** or **New grad** preset, then check
   **Preferences**: target roles and locations, remote preference, salary range, job types, companies
   to target or avoid, excluded keywords and the daily application limit.
4. **Job sources**: add the companies you care about:
   - Greenhouse board tokens (`stripe` from `job-boards.greenhouse.io/stripe`)
   - Lever slugs (`jobs.lever.co/<company>`)
   - Ashby boards (`jobs.ashbyhq.com/<board>`)
   - Workday site URLs (`https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite`)
   - any careers page URL

   LinkedIn, Indeed, Glassdoor and Wellfound searches use your target roles and locations.
5. Optionally **connect Google** (Gmail + Calendar) and **sync LinkedIn** (and Internshala) with the
   Chrome extension.
6. Start with **Dry run** on (Settings › Mass apply) for a day, or `SUBMISSION_DRY_RUN=true` for the whole
   server. You get everything except the final click. Then turn it off.

Use **Scan for jobs now**, or let the scheduler scan every `scan_interval_hours`. New jobs land in
**Swipe Review**; kept jobs are applied to, and anything that needs you arrives in **Needs approval**
with a notification.

## Demo mode

`DEMO_MODE=true` (or `./start.sh --demo`, which also uses its own database) turns HireFlow into a safe practice
copy, for trying the whole loop or showing it to others:

- Create any account and go through onboarding with **Load sample profile**. There is no demo button on the site:
  people use HireFlow for real. (A shared, pre-filled demo account exists for the end-to-end tests, which sign in to
  it through the API.)
- Scans read only the **bundled demo careers site** (`/api/v1/demo-careers`): twelve internships at
  fictional companies with real application forms. Applications to any other site are held for review.
- **Dry run** is on: forms are filled and screenshotted, never submitted. Nothing reaches a real
  employer.
- Real-world actions are off: password changes, Google (Gmail and Calendar), the extension, job-site
  logins, ATS credentials, webhooks and e-mail. They answer 403 and the settings say why.
- A banner on every page says **Demo — nothing is really sent**, and every night (03:37 UTC) all
  accounts and jobs are wiped and the demo account is re-created.

In Docker the worker's browser reaches the demo site through the api container
(`DEMO_SITE_URL=http://api:8000/api/v1/demo-careers`, set in `docker-compose.yml`).

## Try the whole loop on the sample careers site

`scripts/demo_site.py` is a fake company, "Acme Robotics". It has a careers page with three job postings
and real application forms that record submissions locally. Use it to watch the complete loop before
pointing the agent at real employers.

```bash
./start.sh --sample                             # starts it on :8765 along with everything else
python3 scripts/demo_site.py --host 0.0.0.0     # or on its own (no dependencies)
```

1. In the dashboard, upload your resume in **Resume Lab** (PDF, DOCX or TXT), or paste it as text.
2. In **Settings → Saved answers**, save your work authorization and sponsorship answers.
3. In **Settings → Preferences**, set a target role such as `Software Engineer`.
4. In **Settings → Job sources**, add a careers page and enable **Career pages** under platforms:
   - Docker: `http://host.docker.internal:8765/careers`
   - Local (non-Docker) setup: `http://127.0.0.1:8765/careers`
5. Click **Scan for jobs now**. The postings appear in **Swipe Review**.
6. Keep one (drag right or press →). It's tailored, filled in Chromium and submitted. Turn off
   **Apply automatically** first if you'd rather check the form screenshot, resume, cover letter and
   answers and click **Review & approve** yourself.
7. Open http://localhost:8765/submissions to see exactly what was submitted, including your resume PDF.

## Sign in with Google

**Continue with Google** on the sign-in and sign-up pages signs you in with your Google account, or creates your
HireFlow account the first time (you then go through onboarding as usual). HireFlow runs on your own computer, so
it needs its own free Google OAuth client. Until one is set, the button explains this and email sign-in works.

1. Open https://console.cloud.google.com/projectcreate, name the project `HireFlow` and press **Create**.
2. Open https://console.cloud.google.com/auth/overview (with the **HireFlow** project selected at the top) and
   press **Get started**: **App name** `HireFlow` and your support email, **Audience** **External**, your contact
   email, tick the agreement, **Create**.
3. **Branding** (left menu): check that the app name, support email and developer contact are filled in, leave
   the rest empty (no logo: a logo makes Google review the app first) and press **Save**. Then **Audience** (left
   menu): press **Publish app**, then **Confirm**. Sign-in only asks Google for your name and
   email address, which Google allows without a review. (Or leave it in *Testing* and add each Gmail address that
   may sign in under **Test users**.)
4. **Clients** (left menu): **Create client**, **Application type** **Web application**, any name. Under
   **Authorized redirect URIs** add exactly:
   ```
   {FRONTEND_URL}/api/v1/auth/google/callback      e.g. http://localhost:3000/api/v1/auth/google/callback
   ```
   Press **Create**, then copy the **Client ID** and **Client secret**.
5. Put them into `.env` with a text editor (`GOOGLE_CLIENT_ID=` and `GOOGLE_CLIENT_SECRET=`), and restart.

How it behaves:

- **Same email, same account.** If you signed up with an email and password first, Google signs you in to that
  account, and the password keeps working. Only an email address Google has verified is accepted.
- **Safe round trip.** The browser that pressed the button keeps a one-time code in a cookie; Google's answer is
  accepted only in that same browser, within 15 minutes.
- **Only your name and email.** Sign-in asks Google for nothing else. Gmail and Calendar are a separate, explicit
  step (below), with the same client.
- **Off in the demo**, like every other real-world sign-in. `ALLOW_REGISTRATION=false` also stops Google from
  creating new accounts; existing ones still sign in.

## Connect Gmail and Google Calendar

1. In the [Google Cloud console](https://console.cloud.google.com/), use the project from
   [Sign in with Google](#sign-in-with-google) (or create one) and enable the **Gmail API** and the
   **Google Calendar API**.
2. **OAuth consent screen** (Google Auth Platform): choose External, add your e-mail as a test user, and add the
   scopes `gmail.readonly`, `gmail.modify`, `gmail.labels`, `calendar.events` and `calendar.readonly`.
   > While the app's publishing status is **Testing**, Google expires refresh tokens after 7 days and
   > you'll have to reconnect weekly. For personal use you can set the status to **In production**
   > without verification. You'll see an "unverified app" warning when connecting, and tokens stop
   > expiring.
3. **Clients → Create client** (skip if you made one for sign-in: it's the same client): choose type
   **Web application**, and add this authorized redirect URI:
   ```
   {FRONTEND_URL}/api/v1/auth/google/callback      e.g. http://localhost:3000/api/v1/auth/google/callback
   ```
4. Paste the client ID and client secret into their places in your `.env` file. Keep the secret
   private and never commit it. Restart HireFlow (**Ctrl-C** and `./start.sh`, or `docker compose up -d`),
   then go to **Settings › Integrations › Connect Google account**.

**Optional: real-time Gmail push** (otherwise the inbox is polled every `EMAIL_POLL_MINUTES`). This
needs a public HTTPS URL.

1. Create a Pub/Sub topic.
2. Grant `gmail-api-push@system.gserviceaccount.com` the **Pub/Sub Publisher** role on the topic.
3. Create a push subscription to `https://<your-domain>/api/v1/webhooks/gmail?token=<GMAIL_PUBSUB_VERIFICATION_TOKEN>`.
4. Set `GMAIL_PUBSUB_TOPIC=projects/<project>/topics/<topic>` and `GMAIL_PUBSUB_VERIFICATION_TOKEN`.

The watch is renewed daily.

## LinkedIn, Internshala and the Chrome extension

> **Off until you opt in.** LinkedIn, Internshala, Indeed and Glassdoor restrict automated use in their
> terms, so HireFlow never scans or applies on them until you turn each one on in **Settings › Job
> sources** and accept the risk to your account there. You can turn them off again at any time.

LinkedIn search and Easy Apply run under your own LinkedIn session, and the optional Internshala bot
under your own Internshala login. The extension in [`extension/`](../extension) ("HireFlow — Session
Sync") copies those sessions to your HireFlow server. They are stored encrypted and re-synced every
12 hours and whenever LinkedIn or Internshala renews them.

1. Open `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and select the
   `extension/` folder. CI also builds a zip artifact of it. **Updating from 1.0?** Click the reload
   icon on the extension card (or load the folder again): version 1.1 asks for access to
   internshala.com.
2. In the dashboard, go to **Settings → Integrations → Generate extension token**.
3. Open the extension popup and enter your dashboard URL (for example `http://localhost:3000`) and the
   token. Allow access to that origin when Chrome asks, then click **Sync LinkedIn session** while
   logged in to LinkedIn.

The extension reads the LinkedIn `li_at` cookie and, once you click **Sync Internshala session**, your
internshala.com cookies (including the httpOnly login cookies, which a web page can't read). Nothing
is sent anywhere except the dashboard URL you entered, and the dashboard never shows the cookie values.

### The Internshala bot (opt-in)

Internshala only takes applications from your own logged-in account, and logging in from a script hits
a reCAPTCHA, so the bot borrows the login of your real Chrome instead.

1. Log into [Internshala](https://internshala.com) in Chrome and click **Sync Internshala session** in
   the extension popup. **Settings → Integrations → Internshala** shows the login as synced; **Check
   session** opens Internshala with it to confirm it still works.
2. Turn on **Let the agent apply on Internshala** and read the warning (below) before you confirm.
3. Keep Internshala jobs in Swipe Review as usual. The agent opens each one in a real browser, clicks
   **Apply now** (through the "Proceed to application" step when Internshala shows it) and fills the
   form: the cover letter ("Why should you be hired for this role?", as plain text without a
   salutation), your availability (kept at "available immediately" unless your saved *notice period*
   answer says otherwise), the relocation box (from your saved *willing to relocate* answer) and every
   assessment question, using your saved answers first. Your **Internshala profile resume** is what
   Internshala attaches; the agent never replaces it. Nothing is sent: it takes a screenshot and the
   application waits for you.
4. Review it (the filled fields, answers and screenshot) and click **Submit**. Only then does the
   agent open the form again, fill it the same way and click Internshala's Submit button; the
   confirmation screenshot is saved with the application.
5. Optional: **Submit automatically** sends Internshala jobs you keep once they're filled, after the
   10-minute **Sending soon** window.
   Anything the agent is unsure about (a required question it couldn't answer, a low-confidence
   eligibility answer) still waits for you.

Limits: at most **Daily limit** Internshala applications a day (15 by default, 25 at most), 60–180
seconds apart. Approved applications over the limit wait and go out the next day.

What happens when an internship can't be applied to here:

- **External listings** ("You will be redirected to another website"): nothing is submitted; the
  application shows the company's own link so you can apply there.
- **Already applied** on Internshala: the application is marked **Applied** and tracked, not failed.
- **Applications closed**, or Internshala asks you to **complete your profile** first: the application
  stays in your review queue with the reason.
- **Session expired**: you get a notification ("open Internshala in Chrome and click Sync in the
  extension"), Submit is paused until you re-sync, and the application stays in your queue.

> **Account risk.** Internshala's terms don't allow bots or automated access without its consent, and
> Internshala can restrict or suspend accounts it believes are automated. The bot is off by default,
> applies slowly and only to internships you kept, and you review every application unless you turn on
> Submit automatically. You use it at your own risk. Internshala also changes its pages from time to
> time; every selector the bot uses is in one place, `SELECTORS` in
> [`backend/app/submitters/internshala_apply.py`](../backend/app/submitters/internshala_apply.py).

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Invalid email or password" on a Mac / local setup | Each copy of the project folder has its own database (`backend/data/hireflow.db`), so the account may be in another copy. Run `backend/.venv/bin/python scripts/account.py where you@example.com` to see which database the app uses, which accounts it holds and where else your account is. Forgot the password? `backend/.venv/bin/python scripts/account.py reset-password you@example.com` (typed at a hidden prompt). `./start.sh` also prints how many accounts its database holds. |
| Dashboard shows "degraded" at `/api/health` | The API isn't reachable from the dashboard. Check `docker compose logs api` and that `BACKEND_URL` points to it. |
| Nothing gets prepared after a scan | In swipe mode nothing is prepared until you keep it: open **Swipe Review**. Upload a master resume first. **All jobs** shows each job's score and why it was skipped, and **Agent Logs** shows each run step by step. |
| Kept jobs stop in "Needs approval" | A question needs you (usually visa sponsorship or work authorization). Save the answer in **Settings › Saved answers** once and future forms are filled automatically. |
| `./start.sh` says a port is in use | Something else runs on :3000 or :8000. Stop it, or run `API_PORT=8010 WEB_PORT=3010 ./start.sh`. |
| "Required answer(s) are empty" at approval | Eligibility questions are never guessed. Answer them once and they're remembered (also editable in **Settings → Saved answers**). |
| Application fails with a CAPTCHA or bot block | Add a CAPTCHA-solver key and residential proxies to `.env`, or use **Mark as applied** after applying manually through the form link. |
| LinkedIn session invalid | Log in to LinkedIn in Chrome and click **Sync LinkedIn session** in the extension. |
| Internshala session expired | Open Internshala in Chrome (log in if needed) and click **Sync Internshala session** in the extension, then **Check session** in Settings › Integrations. |
| Internshala bot says it can't find the Apply button or the form | Internshala changed its pages. Update the selectors in `SELECTORS` in `backend/app/submitters/internshala_apply.py`; meanwhile apply yourself and click **I Applied**. |
| Google disconnects every 7 days | Your OAuth consent screen is in Testing mode (see [Connect Gmail and Google Calendar](#connect-gmail-and-google-calendar)). |
| Chromium crashes in Docker | Give the worker more shared memory (`shm_size`, 1–2 GB is already set) and RAM. |
| Change the AI's behaviour | Edit the prompt files in `prompts/`, then restart the API and worker. |
| Test AI: "Ollama isn't running" | Open the Ollama app (Mac) or run `ollama serve`. On a server: `COMPOSE_PROFILES=ollama` in `.env`, then `docker compose -f docker-compose.prod.yml up -d`. Check `OLLAMA_BASE_URL`. |
| Test AI: "model isn't downloaded" | Press **Download model** in Settings › Integrations, or run `ollama pull <model>` where Ollama runs. |
| Ollama answers are cut off or slow | Raise `OLLAMA_NUM_CTX` (e.g. 16384) or `OLLAMA_TIMEOUT_SECONDS`, or pick a smaller model such as `llama3.2:3b`. |
