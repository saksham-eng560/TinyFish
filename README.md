<p align="center">
  <img src="frontend/public/icon.svg" width="84" alt="HireFlow logo" />
</p>

<h1 align="center">HireFlow × TinyFish</h1>

<p align="center"><b>Tell it what you want. TinyFish finds the openings on live careers pages and job boards, and HireFlow ranks them.</b><br />A job and internship finder that needs no resume, built on TinyFish Search, Fetch and Agent.</p>

<p align="center">
  <a href="https://github.com/saksham-eng560/TinyFish/actions/workflows/ci.yml"><img src="https://github.com/saksham-eng560/TinyFish/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="docs/TESTING.md"><img src="https://img.shields.io/badge/tests-700%2B%20backend-FF6A00" alt="Tests" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-FF6A00" alt="MIT license" /></a>
</p>

<p align="center">
  <a href="#how-tinyfish-is-used"><b>How TinyFish is used</b></a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#demo-steps">Demo</a> ·
  <a href="#safety">Safety</a> ·
  <a href="docs/TINYFISH.md">TinyFish docs</a> ·
  <a href="docs/SUBMISSION.md">Submission</a>
</p>

HireFlow is a job and internship finder that runs on your own computer. Tell the **TinyFish Finder** your roles,
locations, keywords, seniority and visa needs. TinyFish Search finds careers pages and job boards, TinyFish Fetch reads
them live, and the TinyFish Agent opens the JavaScript-heavy portals that a plain request can't read. You get one
list of structured, de-duplicated openings, scored from 0 to 100 with the reasons, each with an apply link. No resume is
needed to try it. Keep the ones you like and HireFlow adds them to your **Swipe Review** deck.

Everything runs on your computer. Your preferences and data stay in your own database, and TinyFish is only called
with the search terms and the public careers pages it has to read.

<p align="center"><img src="docs/screenshots/landing.png" alt="HireFlow's landing page (the screenshot shows the earlier blue theme)" width="900" /></p>

> **Screenshots:** the images in this README show the earlier blue theme. The app is now orange and black. Layout and
> features are unchanged.

## How TinyFish is used

HireFlow calls all three TinyFish endpoints. Each one does a different job, and the Finder shows every call live.

| Endpoint | Role in HireFlow | Example | When it runs |
|---|---|---|---|
| **Search** | Discovery: finds job boards, company careers pages and single postings for your roles and places | `GET https://api.search.tinyfish.ai/` with `query="backend engineer intern"`, `include_domains=boards.greenhouse.io,jobs.lever.co,...`, `exclude_domains` set to the consent-gated sites | First, for every Finder search and every scan that uses TinyFish |
| **Fetch** | Reading: returns the live text and links of a careers page or posting (`ttl=0`, so nothing is cached) and its JSON-LD job data | `POST https://api.fetch.tinyfish.ai/` with `urls` (up to 10), `format="html"` or `"markdown"`, `ttl=0` | After Search, for every page that Search found |
| **Agent** | Extraction: a browser agent for pages Fetch can't read (JavaScript apps, "load more" lists, bot checks), returning jobs in a fixed JSON schema | `POST https://agent.tinyfish.ai/v1/automation/run-sse` with a numbered goal and `output_schema` | Only as a fallback, capped by your run budget. Its live browser view is shown in the Finder |

Search finds the openings, Fetch reads the pages, and the Agent handles the pages that need a real browser. Details,
limits and the ranking formula: [docs/TINYFISH.md](docs/TINYFISH.md).

## Architecture

```mermaid
flowchart TD
    UI["TinyFish Finder page or Settings scan"] -->|"POST /api/v1/tinyfish/search"| API["FastAPI"]
    API --> WORKER["Background worker"]
    WORKER --> SEARCH["TinyFish Search: ATS pass, open pass, company pass"]
    SEARCH --> CLS{"Classify each URL"}
    CLS -->|"Consent-gated site (LinkedIn, Indeed, Glassdoor, Internshala)"| DROP["Dropped, never requested"]
    CLS -->|"ATS board root"| ATS["Greenhouse, Lever, Ashby, Workday public APIs"]
    CLS -->|"Job posting"| FETCH["TinyFish Fetch: JSON-LD and page parse"]
    CLS -->|"Careers page"| FETCH2["TinyFish Fetch: links and embedded boards"]
    FETCH2 -->|"Embedded boards"| ATS
    FETCH2 -->|"Matching job links"| FETCH
    FETCH2 -->|"Blocked, empty or JavaScript-only"| AGENT["TinyFish Agent: at most 2 at once, capped runs"]
    FETCH -->|"Empty or thin"| AGENT
    ATS --> NORM["Normalise, canonical URL, de-duplicate"]
    FETCH --> NORM
    AGENT --> NORM
    NORM --> RANK["Rank 0 to 100 with reasons"]
    RANK -->|"Finder"| RESULTS["Ranked listings with apply links"]
    RANK -->|"Scan"| DECK["Swipe Review deck"]
```

The Finder runs the same pipeline as a scan. The full picture, with the data flow and the code map, is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Features

- **No resume needed.** The Finder ranks from your preferences alone. A resume is only used for tailoring later.
- **Your preferences:** roles, locations, keywords, companies, seniority, visa sponsorship, remote only, posted-within
  window, and your own careers page URLs (up to 5).
- **Live progress:** Search, Fetch and Agent cards light up as they run, with the Agent's current step and a
  "Watch live" view of its browser when TinyFish provides one.
- **Ranked, not dumped:** every listing gets a 0 to 100 score and a "Why this ranks" breakdown: title 35, location 20,
  seniority 15, keywords 15, recency 10, visa 5.
- **Structured and de-duplicated:** one entry per job, even when it was found through a board, a careers page and a
  search result at once. Each entry shows its company, location, ATS, and the route it was found by.
- **Apply links, not auto-apply:** each listing opens its original posting in a new tab. Saving a listing only adds it
  to Swipe Review. Nothing is submitted.
- **Works in scans too:** TinyFish is one more source in the normal scan, with live detail text in the progress bar.
- **Works without a key:** without `TINYFISH_API_KEY`, the Finder shows a clear "add the key" message and every other
  source works as before.
- **Light and dark themes:** orange and black, with every text pair checked to WCAG AA (4.5:1) by
  `scripts/check_contrast.py`.

## Quick start

You need **Git**, **Python 3.11 or newer** and **Node.js 20 or newer**. On macOS, `brew install git python@3.12 node`
installs them. On Ubuntu or WSL, `sudo apt install -y git python3 python3-venv python3-pip nodejs npm` does it (use
Node 20 from https://nodejs.org if `node -v` shows an older version).

### 1. Get a TinyFish API key

1. Create an account and a key at **https://agent.tinyfish.ai/api-keys**.
2. Keep the key private. HireFlow reads it from `.env` only. It is never stored in the database, never sent to the
   browser, and never shown in the app.

### 2. Download and start HireFlow

```bash
git clone https://github.com/saksham-eng560/TinyFish.git
```

```bash
cd TinyFish
```

```bash
./start.sh
```

The first run takes 3 to 5 minutes. It installs the Python packages, the browser HireFlow uses for forms, and the
dashboard, and creates a `.env` file if there isn't one. When it's ready, your browser opens **http://localhost:3000**.
Press **Ctrl-C** in the terminal to stop.

### 3. Add your key

Open `.env` (on a Mac, `open -e .env`; on Linux or WSL, `nano .env`) and set:

```bash
TINYFISH_API_KEY=paste-your-key-here
```

Save, then restart: **Ctrl-C**, then `./start.sh`. `TINYFISH_API_KEY` is the only key you need for the Finder.

### 4. Optional keys, added later

HireFlow works without these. Add them to the same `.env` file whenever you want the feature:

| Key in `.env` | What it adds | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` | Better tailoring, cover letters and answers | https://console.anthropic.com or https://platform.openai.com |
| `OLLAMA_MODEL` | Free AI on your own computer, no key | [docs/OLLAMA.md](docs/OLLAMA.md) |
| `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` | **Continue with Google** sign-in, and Gmail and Calendar | [Sign in with Google](docs/USER_GUIDE.md#sign-in-with-google) |
| `CAPTCHA_API_KEY` | Captcha solving for form filling | https://2captcha.com |

After adding a key, restart HireFlow. Settings › Integrations shows which features are on.

### Check that TinyFish is connected

Open **Settings › Integrations**, find the **TinyFish** card and press **Test connection**. It runs one Search. A
green "Connected" line means the key works. The key itself is never displayed.

### Everyday commands

| Command | What it does |
|---|---|
| `./start.sh` | Start HireFlow at http://localhost:3000 |
| `./start.sh --demo` | Practice mode: fictional jobs, nothing really sent |
| `./start.sh --reset` | Start over with an empty database |
| `make test` | Run the backend tests |
| `make lint` | Lint the backend, the dashboard, and check the colour contrast |

## Demo steps

1. Start HireFlow with `./start.sh` and create an account at http://localhost:3000. You don't need to upload a resume.
2. Open **TinyFish Finder** in the sidebar (under **Agent**, right after Overview). The page says "Powered by TinyFish".
3. Press **Use my preferences** to fill the form from your settings, or type your own roles (for example
   *Backend Engineer* and *Software Engineer Intern*), a location, and some keywords.
4. Press **Find openings**. Watch the three cards for **Search**, **Fetch** and **Agent** count up. Press **Watch live**
   on the Agent card to see its browser when a run starts.
5. When the search finishes, the results show a score, the reasons, the company, the ATS host and the route each job
   came from. Press **Apply** to open the original posting in a new tab.
6. Tick the listings you want and press **Save N to Swipe Review**. Open **Swipe Review** to see them as cards.

Without a key, step 4 shows the "add TINYFISH_API_KEY" message instead, and the rest of HireFlow works as before.

## Safety

- **Consent-gated sites are never sent to TinyFish.** LinkedIn, Indeed, Glassdoor and Internshala stay behind the
  consent rules in the app. Search excludes them, and a second check drops any such URL before it is requested. Tests
  assert this.
- **No auto-apply from the Finder.** Saving a listing only creates a Swipe Review card. Nothing is sent to an employer.
- **Agent budget.** Each scan uses at most your **Agent runs per scan** setting (default 2, the hard ceiling is 5).
  Scheduled scans use at most 1 run. At most 2 Agent runs run at once, each with a time limit.
- **Untrusted pages.** Careers pages and Agent output are treated as data. Only `https` apply links from the employer
  or a known job board are kept. The Agent is told never to log in, fill forms or submit anything.
- **Your key stays on your computer.** It lives in `.env` and is never written to the database, logs or the browser.
- **Quota errors pause TinyFish, not HireFlow.** A 401, 402 or 429 is shown in the source list and every other source
  keeps running.

Details: [docs/TINYFISH.md](docs/TINYFISH.md) and [docs/SECURITY.md](docs/SECURITY.md).

## Verification status

- The TinyFish API contracts (hosts, headers, request fields, limits and error codes) were checked against the TinyFish
  documentation, and one live Search call was made and recorded.
- The automated tests use mocked TinyFish responses. They do not make network calls.
- The full end-to-end run (a live search, live Fetch and a live Agent run) needs your own TinyFish key. Follow the steps
  above to run it.

## Troubleshooting

<details>
<summary><b>TinyFish</b></summary>

| You see | Do this |
|---|---|
| "TinyFish isn't configured" | Add `TINYFISH_API_KEY` to `.env` (key at https://agent.tinyfish.ai/api-keys), then restart. |
| "TINYFISH_API_KEY is invalid or was rejected (HTTP 401)" | Check the key for spaces or quotes, or create a new one. |
| A quota or credits message (HTTP 402, or a 429 with limit details) | TinyFish is paused for that period and the other sources keep running. Check your TinyFish account. |
| "TinyFish ... is rate limiting requests" (HTTP 429) | Wait a minute and search again. Lower the number of roles or companies if it keeps happening. |
| A page shows "bot blocked" or stays empty | The Agent is used for it when your run budget allows. Raise **Agent runs per scan** in Settings, or add the careers page URL directly. |

More: [docs/TINYFISH.md#troubleshooting](docs/TINYFISH.md#troubleshooting).
</details>

<details>
<summary><b>Starting HireFlow</b></summary>

| You see | Do this |
|---|---|
| `Python 3.11+ is required` / `Node.js 20+ is required` | Install a newer version (see the first lines of [Quick start](#quick-start)). |
| `Port 3000 is already in use` | Close the other app, or start with `WEB_PORT=3001 ./start.sh`. For port 8000, use `API_PORT`. |
| `Chromium install failed` | Run `backend/.venv/bin/python -m playwright install chromium` (on Linux add `--with-deps`). |
| "Can't reach the HireFlow server right now" | Look for an error in the terminal, or in `logs/api.log`. |
</details>

Anything else: the logs are in the `logs/` folder (`api.log`, `web.log`).

## Docs

| | |
|---|---|
| [TinyFish in HireFlow](docs/TINYFISH.md) | The endpoints, limits, pipeline, budget settings, ranking and the Finder API |
| [Submission checklist](docs/SUBMISSION.md) | Requirement-to-evidence map, demo script, and steps to run and test |
| [User guide](docs/USER_GUIDE.md) | Every screen and setting, including the TinyFish Finder |
| [Architecture](docs/ARCHITECTURE.md) | How the pieces fit, and where each guardrail lives |
| [Free AI with Ollama](docs/OLLAMA.md) | Models, Ollama Cloud, Docker, troubleshooting |
| [AI models](docs/AI_MODELS.md) | Claude, OpenAI and Ollama compared |
| [Running and self-hosting](docs/SELF_HOSTING.md) | Docker, development setup, your own server |
| [Testing](docs/TESTING.md) | What's tested and how to run it |
| [Security and responsible use](docs/SECURITY.md) | How your data is protected, and the rules for using job sites |

## Origin

HireFlow began as AutoApply AI. Its full history is in [docs/HIREFLOW_PLAN.md](docs/HIREFLOW_PLAN.md).

## License

[MIT](LICENSE)
