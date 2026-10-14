# Submission: HireFlow × TinyFish (Job Portal / Careers Finder)

This page maps each bounty requirement to the code, route or screen that delivers it, explains why HireFlow uses three
TinyFish endpoints, and gives the demo and the steps to run it with your own key.

**What is verified and what is not.** The TinyFish API contracts used here (hosts, the `X-API-Key` header, request
fields, limits, error codes and the Agent SSE events) were checked against the TinyFish documentation. One live Search
call was made and recorded. The automated test suite (700+ backend tests) uses mocked TinyFish responses and makes no
network calls. The full end-to-end flow (live Search, Fetch and Agent together, from the Finder page) needs your own
`TINYFISH_API_KEY` to run. The steps are in [Steps to run and test](#steps-to-run-and-test-after-adding-api-keys).

## Requirement to evidence

| # | Requirement | Evidence (file, route or screen) |
|---|---|---|
| 1 | **Working demo** | The TinyFish Finder page at `/dashboard/tinyfish` (sidebar: TinyFish Finder). The demo steps are in the [README](../README.md#demo-steps). The Finder works with no resume. |
| 2 | **User sets preferences** (role, location, keywords, seniority, visa) | Finder form: `frontend/src/components/tinyfish/finder-form.tsx` (Roles, Locations, Keywords, Companies, Seniority, "I need visa sponsorship", Posted within, Career pages). "Use my preferences" fills it from your saved settings. The same preferences drive scans through `SearchQuery.from_preferences` in `backend/app/scrapers/base.py`. |
| 3 | **Multiple live careers pages and portals** | `backend/app/scrapers/tinyfish.py` runs three Search passes: an ATS pass (`boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`, `myworkdayjobs.com`), an open pass for custom careers sites (with aggregators excluded), and a company pass. Each listing carries `source_host`, `ats` and `discovered_by` (the route it was found by). See [TINYFISH.md](TINYFISH.md#pipeline). |
| 4 | **Structured, de-duplicated listings with apply links** | Each listing has title, company, location, job type, posted date, `apply_url`, `source_url`, and ATS. De-duplication uses the canonical URL (`canonical_url` in `backend/app/scrapers/tinyfish_parse.py`) and then `dedupe_key(company, title, location)`. The listing card is `frontend/src/components/tinyfish/listing-card.tsx`. |
| 5 | **Short explanation of how Search, Fetch and Agent are used** | [TINYFISH.md](TINYFISH.md) and the README table ["How TinyFish is used"](../README.md#how-tinyfish-is-used). The paragraph below is ready to paste. |
| 6 | **Social post tagging @TinyFish** | Drafts below. You post them. |
| Approval | **Live data, not hard-coded; ranked results; several companies and portals** | No listings are hard-coded. Search queries are built from your request (`build_search_plans` in `tinyfish_parse.py`). Results are ranked by `backend/app/services/tinyfish_ranker.py` with a 0 to 100 score and reasons for every factor. |
| Scoring | **Three or more endpoints, used meaningfully** | Search (discovery), Fetch (reading, `ttl=0`), Agent (extraction with `output_schema`). Each endpoint's count is shown live on the Finder page. See the [three-endpoint justification](#why-three-endpoints). |

## Why three endpoints

- **Search** finds the places to look. Without it, HireFlow would have to know every company's careers URL in advance.
  It also returns the job posting URLs directly, so Search alone gives breadth across ATS boards and company sites.
- **Fetch** reads what Search found, live (`ttl=0`), in batches of up to 10 URLs. It returns the page text and links,
  and the JSON-LD job data most careers pages publish. This is the cheap, fast path for most results.
- **Agent** handles the pages Fetch can't read: JavaScript apps, "load more" lists, and pages that block plain requests.
  It is slower and costs more per step, so it runs only as a capped fallback. Its live browser view is shown in the
  Finder while it runs.

Each endpoint does a different job, and removing any one of them leaves a gap: no discovery, no reading, or no way past
JavaScript-only portals.

## Demo script (about 3 minutes)

1. **0:00 to 0:20. The problem.** Job boards are split across many ATS sites and company pages, and many are
   JavaScript apps. A plain search misses most of them.
2. **0:20 to 0:50. Set preferences, no resume.** Open the TinyFish Finder at `/dashboard/tinyfish`. Press **Use my
   preferences**, or type a role, a location and a few keywords. Tick **I need visa sponsorship** to show the visa rule.
3. **0:50 to 1:20. Find openings.** Press **Find openings**. Point at the three cards: **Search** counts queries and
   results, **Fetch** counts pages read, **Agent** shows its current step. Press **Watch live** on the Agent card if a
   run is active.
4. **1:20 to 2:00. Ranked results.** Show the list sorted by score. Open **Why this ranks** on the top card (title,
   location, seniority, keywords, recency, visa), and point at the source host and the route ("via Search, Fetch").
5. **2:00 to 2:30. Apply links.** Press **Apply** on one listing to open the original posting in a new tab. Nothing is
   submitted.
6. **2:30 to 3:00. Save to Swipe Review.** Tick two or three listings, press **Save N to Swipe Review**, then **Open
   Swipe Review** to show the cards. Close with the README's safety points: consent-gated sites are never sent to
   TinyFish, there is no auto-apply, and the Agent budget is capped.

## Ready-to-paste explanation

> HireFlow uses all three TinyFish endpoints, each for a different step. **TinyFish Search** finds where to look: it
> runs a capped set of queries per search (at most 8 per scan), split between the ATS job boards (Greenhouse, Lever,
> Ashby and Workday), the open web for company careers pages, and the companies you name. It leaves out LinkedIn,
> Indeed, Glassdoor and Internshala. **TinyFish Fetch** reads those pages live, with no cache, in batches of up to ten URLs, and returns the
> page text and the job data the page publishes. **TinyFish Agent** runs a browser agent only for the pages Fetch
> can't read, such as JavaScript-only portals and pages that block plain requests, with a fixed JSON output schema and
> a hard cap on runs. Each job is de-duplicated, scored from 0 to 100 against the user's roles, location, seniority,
> keywords, recency and visa needs, and shown with its reasons and apply link. The user doesn't need a resume, and
> nothing is applied for them.

## Draft social posts (you post these)

> **DRAFT. Edit before posting.**

**LinkedIn draft**

> I built HireFlow × TinyFish: a job and internship finder with no resume needed. Tell it your roles, locations and
> keywords, and TinyFish Search finds the careers pages and job boards, TinyFish Fetch reads them live, and the TinyFish
> Agent opens the JavaScript-heavy portals. You get ranked, de-duplicated listings with apply links, each with a score
> and the reasons. Consent-gated sites are never sent to TinyFish, and nothing is applied for you. Thanks @TinyFish for
> the APIs. [link to your repo]

**X draft**

> Built HireFlow × TinyFish for the @TinyFish job finder bounty. Tell it your roles and places, no resume needed. Search
> finds the job boards, Fetch reads them live, Agent handles the JS-only portals. Ranked results with apply links.
> [link to your repo] [demo video link]

## Steps to run and test after adding API keys

Run these in the project folder.

**1. Create `.env` and add your TinyFish key.** If `.env` doesn't exist yet, copy the template (`start.sh` also creates
it on the first run).

```bash
cp .env.example .env
```

Open `.env` and set the key (no spaces or quotes):

```bash
TINYFISH_API_KEY=paste-your-key-here
```

Get the key at https://agent.tinyfish.ai/api-keys. Leave `TINYFISH_ENABLED` unset (it defaults to `true`).

**2. Start the app.**

```bash
./start.sh
```

Open http://localhost:3000, create an account and sign in. A resume is not needed.

**3. Verify the key.** Go to **Settings › Integrations**, find the **TinyFish** card and press **Test connection**. You
should see "Connected", with a latency and a result count. If you see a hint instead, read it: it names the problem
(missing key, rejected key or a limit).

**4. Run a Finder search.** Open **TinyFish Finder** in the sidebar. Press **Use my preferences** or type a role such
as `Backend Engineer` and a location. Press **Find openings**. Watch the Search, Fetch and Agent cards, then check the
ranked list and an apply link.

**5. Run the tests.** The tests use mocked TinyFish responses, so they run without a key:

```bash
make test
```

This runs the backend suite (`backend/.venv/bin/python -m pytest`). `start.sh` creates `backend/.venv`. If you haven't
run it yet, run `make setup` first. For the lint and contrast checks, run `make lint`.

**Optional: the dashboard checks.** `make build` builds the dashboard, and `make e2e-demo` runs the browser tests in demo
mode.

## Links

- [TinyFish endpoints in HireFlow](TINYFISH.md): endpoints, limits, pipeline, budget, ranking and the Finder API
- [README](../README.md): quick start, architecture and safety
- [User guide](USER_GUIDE.md#tinyfish-finder): the Finder, step by step
- [Architecture](ARCHITECTURE.md#tinyfish-job-discovery): how the TinyFish pipeline fits the app
