# TinyFish in HireFlow

HireFlow uses the three TinyFish endpoints (Search, Fetch and Agent) to find openings on live careers pages and job
boards. This page covers the endpoints, the pipeline, the budget controls, the ranking, the Finder API and
troubleshooting.

**Verification.** The API contracts below were checked against the TinyFish documentation, and one live Search call was
recorded. The automated tests use mocked responses. A live run needs your own `TINYFISH_API_KEY`.

## The endpoints

All calls send the header `X-API-Key: <your key>`. The key is read from `TINYFISH_API_KEY` in `.env` only. Keys:
https://agent.tinyfish.ai/api-keys

| Endpoint | Host (from config) | Used for | Limits (TinyFish docs) |
|---|---|---|---|
| **Search** | `TINYFISH_SEARCH_URL` = `https://api.search.tinyfish.ai` | `GET /` with `query`, `purpose`, `location`, `include_domains`, `exclude_domains`, `after_date`, `page` | 30 requests per minute per key. HireFlow throttles to 30 per minute and gives up waiting after 20 seconds. |
| **Fetch** | `TINYFISH_FETCH_URL` = `https://api.fetch.tinyfish.ai` | `POST /` with `urls`, `format`, `links`, `purpose`, `ttl`, `per_url_timeout_ms` | Up to 10 URLs per request (HireFlow batches). 150 URLs per minute. HireFlow always sends `ttl=0` (live). |
| **Agent** | `TINYFISH_AGENT_URL` = `https://agent.tinyfish.ai` | `POST /v1/automation/run-sse` (streaming), `POST /v1/automation/run` (fallback), `GET /v1/runs/{id}` (status), `POST /v1/runs/{id}/cancel` | 2 concurrent runs per key. About $0.016 per step. HireFlow never runs more than 2 at once. |

Examples of the Search and Fetch requests (the query is one HireFlow builds from your preferences):

```http
GET https://api.search.tinyfish.ai/?query=backend+engineer+intern&include_domains=boards.greenhouse.io,jobs.lever.co,jobs.ashbyhq.com,myworkdayjobs.com,job-boards.greenhouse.io&location=us
X-API-Key: <your key>
```

```http
POST https://api.fetch.tinyfish.ai/
X-API-Key: <your key>
Content-Type: application/json

{"urls": ["https://job-boards.greenhouse.io/stripe/jobs/8031833"], "format": "html", "links": true, "ttl": 0, "per_url_timeout_ms": 45000}
```

The Search response has a `results` list of `position`, `title`, `snippet`, `url`, `site_name` and an optional `date`.
The `date` is free text (for example "2 days ago"), so HireFlow treats it as a hint and never parses it as a fixed date.

Errors use one envelope, `{"error": {"code", "message", "details"}, "request_id"}`. HireFlow maps the status codes as
follows (`backend/app/services/tinyfish_client.py`):

| HTTP | Meaning in HireFlow |
|---|---|
| 400 | A request bug. Shown with the message. |
| 401 | The key is invalid or was rejected. |
| 402 | Credits or allowance used up. TinyFish is paused. |
| 429 with `details` (or `DAILY_LIMIT_EXCEEDED`) | An account limit. TinyFish is paused (until midnight UTC for a daily limit, otherwise an hour). |
| 429 without `details`, 409 `RETRY_REQUIRED`, 502, 503, 504 | Retried at most twice with backoff, honouring `Retry-After` (capped at 20 seconds). |
| 500 | Shown as unavailable. |

A run-level error (for example `SERVICE_BUSY`, `TIMEOUT`, `BILLING_REJECTED`, `SITE_BLOCKED`) comes back as the Agent
result, not as an exception. A billing error disables the Agent for the rest of that search.

## Pipeline

The scan and the Finder share one pipeline, `TinyFishScraper.discover` in `backend/app/scrapers/tinyfish.py`.

1. **Search.** `build_search_plans` (in `backend/app/scrapers/tinyfish_parse.py`) makes up to 8 plans per scan
   (`TINYFISH_SEARCH_QUERIES_PER_SCAN`), run 4 at a time:
   - an **ATS pass** over `boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com` and
     `myworkdayjobs.com`;
   - an **open pass** over the open web, with the consent-gated sites and job aggregators excluded;
   - a **company pass**, one query per company you name (and up to 3 catalogue companies that have no ATS board).
   Every hit is checked against the gated-host list, then canonicalised (tracking parameters and fragments removed) and
   classified: an ATS board root, an ATS posting, a posting, a careers page, or other. Your own career page URLs go first.
2. **Delegate.** ATS board roots and the job boards found on pages are read through the existing Greenhouse, Lever,
   Ashby and Workday scrapers (their public APIs). Those jobs keep their native data.
3. **Read.** Postings are read with Fetch (HTML, parsed from JSON-LD or the `h1` and `main` elements). Careers pages are
   read with Fetch (markdown and links). Matching job links on a careers page are followed, up to 15 per page. The total
   is capped at `TINYFISH_FETCH_URLS_PER_SCAN` (60).
4. **Agent.** Pages that Fetch could not read (`empty_content`, `bot_blocked`, `login_required`, `timeout`), JavaScript
   shells, and careers pages with no job links are sent to the Agent, within the run budget. A `bot_blocked` page uses
   the stealth browser profile. Each run has a time limit (`TINYFISH_AGENT_MAX_DURATION_SECONDS`, 180 by default) and is
   skipped when less than 60 seconds remain before the scan deadline.
5. **Normalise.** Results are de-duplicated by canonical URL (the record kept comes from the best source: ATS API, then
   Fetch HTML, then Agent, then search snippet), then by `dedupe_key(company, title, location)`. Each job is tagged with
   its provenance. Any gated URL is dropped again here.
6. **Rank.** For the Finder, `rank_jobs` scores every job (see [Ranking](#ranking)). The scan keeps the jobs and lets
   the normal scan rules apply.

### The Agent goal and output schema

The Agent gets a numbered goal built by `build_agent_goal`. The steps are:

```text
1. Open {url} and dismiss any cookie or consent banner.
2. If the listings are paginated or behind a "Load more" / "Show more" button, press it at most 3 times.
3. Find the open job listings that match these roles: {roles}; locations: {locations}; seniority: {seniority}. Skip listings that clearly do not match.
4. Extract at most 25 listings with exactly these fields: title, apply_url, location, posted (date or text as shown), employment_type, seniority, summary (1-3 sentences), visa_sponsorship (yes, no or unknown).
5. apply_url must be the absolute URL of the posting or its apply page. Use null for anything you cannot see, do not guess, and leave a listing out when its URL is unknown.
6. Never log in, fill in forms, upload files or submit anything. Do not leave the employer's site, except to follow a link to its job board (Greenhouse, Lever, Ashby, Workday).
7. If the page is blocked or behind a login wall, return {"jobs": [], "blocked": true}.
```

The output is constrained by this schema (`JOBS_OUTPUT_SCHEMA`). It uses only the keywords TinyFish accepts:

```json
{"type":"object","properties":{
  "company":{"type":"string","nullable":true},
  "blocked":{"type":"boolean","nullable":true},
  "jobs":{"type":"array","maxItems":25,"items":{"type":"object","properties":{
    "title":{"type":"string"},"apply_url":{"type":"string"},
    "location":{"type":"string","nullable":true},"posted":{"type":"string","nullable":true},
    "employment_type":{"type":"string","nullable":true},"seniority":{"type":"string","nullable":true},
    "summary":{"type":"string","nullable":true},
    "visa_sponsorship":{"type":"string","enum":["yes","no","unknown"]}},
    "required":["title","apply_url"]}}},
 "required":["jobs"]}
```

Agent output is untrusted. `agent_jobs_to_scraped` keeps only `https` apply URLs that are on the page's own site or on a
known job board, rejects gated hosts, and caps the list at 25 jobs and 4000 characters of summary.

## Budget controls

| Setting | Where | Default | Meaning |
|---|---|---|---|
| `TINYFISH_API_KEY` | `.env` | empty | No key means TinyFish is off. |
| `TINYFISH_ENABLED` | `.env` | `true` | Kill switch. `false` turns TinyFish off even with a key. |
| `TINYFISH_AGENT_MAX_RUNS_PER_SCAN` | `.env` | `5` | Hard ceiling on Agent runs per scan or search. |
| `TINYFISH_AGENT_CONCURRENCY` | `.env` | `2` | Agent runs at once (TinyFish's own cap is 2). |
| `TINYFISH_AGENT_MAX_DURATION_SECONDS` | `.env` | `180` | Time limit per Agent run. |
| `TINYFISH_SEARCH_QUERIES_PER_SCAN` | `.env` | `8` | Search queries per scan. |
| `TINYFISH_FETCH_URLS_PER_SCAN` | `.env` | `60` | Pages read per scan. |
| `TINYFISH_FINDER_TIMEOUT_SECONDS` | `.env` | `300` | Time limit for one Finder search. |
| `RATE_LIMIT_TINYFISH` | `.env` | `6/minute;40/hour` | Finder searches per user. |
| `tinyfish_enabled` | Settings › Integrations (per user) | `true` | Use TinyFish in your scans. |
| `tinyfish_agent_runs` | Settings › Integrations (per user) | `2` (0 to 5) | Agent runs per scan. Clamped to the ceiling above. Scheduled scans use at most 1. |

In demo mode (`DEMO_MODE=true`) the Agent never runs, and scans use only the demo site.

## Fallback without a key

With no `TINYFISH_API_KEY`:

- the `tinyfish` source is not added to scans, so every other source works exactly as before;
- "Add job by URL" and the generic careers page reader use their normal path, with no TinyFish retry;
- the Finder returns `503` with the hint, and the Settings card and the Finder page show "add `TINYFISH_API_KEY`".

If a TinyFish call fails during a scan (for example a 401 or a quota error), that source is marked failed with the reason,
and the other sources keep running.

## Provenance

Every TinyFish-found job stores its origin in `raw`, which is saved as `Job.raw_data`:

```json
{"discovered_by":"tinyfish",
 "tinyfish":{"via":["search","fetch"],"query":"backend engineer intern","hit_url":"https://...","hit_title":"...","agent_run_id":null,"fetched_at":"2026-10-14T10:00:00Z"},
 "sponsorship":"We offer visa sponsorship for ...",
 "match":{"score":84,"reasons":[{"factor":"title","points":35,"max":35,"note":"Title contains 'Backend Engineer'"}]}}
```

`via` lists the routes (`search`, `fetch`, `ats_api`, `agent`). No new database column or migration is used.

## Ranking

`backend/app/services/tinyfish_ranker.py` scores each job from 0 to 100 using only your preferences. No resume is needed.

| Factor | Max | How it scores |
|---|---|---|
| Title | 35 | Contains one of your roles: 35. Close match (token ratio at least 0.67): 28. Partial match (at least 0.34): 14. No match: 0. No roles given: 20. |
| Location | 20 | Remote job when you ask for remote: 20. Location matches: 20. No location given: 14. Job's location unknown: 10. Otherwise: 2. |
| Seniority | 15 | Level you asked for: 15. Next level up or down (for example intern and entry): 8. Other: 0. Nothing asked: 10. |
| Keywords | 15 | `round(15 * matched / asked)` over the title and the first 4000 characters of the description. No keywords: 8. |
| Recency | 10 | Up to 3 days: 10. Up to 7: 8. Up to 14: 6. Up to 30: 4. Up to 90: 2. Older: 0. Unknown date: 4. |
| Visa | 5 | Visa needed: sponsors 5, not mentioned 2, "no sponsorship" 0 and the job is dropped. Visa not needed: 5. |

Ties are broken by the newer posting date, then by title. Each listing shows its reasons as `points/max` rows.

## Finder API

The Finder API is served under `/api/v1`, needs a signed-in user, and uses the same `{"detail": "..."}` error shape as the
rest of the API. This is the contract the dashboard uses.

| Method and path | Purpose | Responses |
|---|---|---|
| `GET /tinyfish/status` | Whether a key is set, whether TinyFish and the Agent are on, the Agent run ceiling, and today's usage | 200 |
| `POST /tinyfish/search` | Start a search. Body: `roles`, `locations`, `keywords`, `seniority`, `visa_sponsorship`, `remote`, `companies`, `career_pages`, `posted_within_days`, `limit`, `min_score`, `use_agent`, `agent_runs` | 202 `{"search_id", "status": "running", "started_at"}`. 503 without a key. 409 if a search is already running. 422 for invalid input. 429 for the rate limit. |
| `GET /tinyfish/search/{search_id}` | Progress and, when completed, the ranked `results` | 200 (`status`: running, completed, failed or cancelled; `usage`; `endpoints_used`; `agent`; `stages`; `counts`). 404 for another user's search. |
| `POST /agent/runs/{run_id}/cancel` | Stop a running search (partial results are ranked) | Existing endpoint |
| `POST /tinyfish/save` | Body `{"search_id", "listing_ids"}` (1 to 100 ids). Creates Swipe Review cards. | 200 `{"saved", "already_in_deck", "skipped", "items", "review_url": "/dashboard/review"}`. 404 for an unknown search or listing. 409 if the search hasn't finished. |
| `POST /users/me/integrations/tinyfish/test` | Runs one Search as a connection test | 200 `{"ok", "latency_ms", "results", "error", "hint"}` |
| `PUT /users/me/preferences` | Accepts `tinyfish_enabled` (bool) and `tinyfish_agent_runs` (0 to 5) | 422 for other values |

A listing in `results` has `id`, `title`, `company`, `location`, `remote`, `job_type`, `seniority`, `posted_date`,
`apply_url`, `source_url`, `source_host`, `ats`, `discovered_by`, `snippet`, `visa` (`status` and `evidence`), `score`,
`reasons`, `saved` and `application_id`. The API key never appears in any response.

Progress is pushed on the WebSocket as a `tinyfish_progress` event and also polled every 1.5 seconds while a search runs.

## Troubleshooting

| You see | Why | Do this |
|---|---|---|
| "TinyFish isn't configured. Add TINYFISH_API_KEY..." | No key in `.env` | Add the key, then restart with `./start.sh`. |
| "TINYFISH_API_KEY is invalid or was rejected (HTTP 401)" | The key is wrong or revoked | Check the key for spaces and quotes, or create a new key. |
| "... limit or credits used up ... Paused for N min" (402, or 429 with limit details) | Credits or the account limit is used up | Wait for the pause, or check your TinyFish account. Other sources keep running. |
| "... is rate limiting requests (HTTP 429)" | Too many calls at once | Wait a minute. The client already retries twice. |
| "both TinyFish agent slots are busy" | Two Agent runs are already going | Wait for them to finish, or search again. |
| A careers page shows `bot_blocked` or no jobs | The site blocks plain requests | The Agent reads it with the stealth profile when the run budget allows. Raise **Agent runs per scan** in Settings, or add the page directly under career pages. |
| A JavaScript-only page returns nothing | The page needs a browser | The Agent is used for it when the budget allows. |
| Fewer results than expected | Your filters, the posted-within window or the consent-gated exclusions | Widen the posted-within window or the locations. LinkedIn, Indeed, Glassdoor and Internshala are never searched by TinyFish. |

## Out of scope

- **TinyFish Monitor API.** Not used. Freshness comes from Fetch with `ttl=0` on every scan, from the hourly scheduled
  scans, and from the search date filter.
- **Consent-gated sites.** LinkedIn, Indeed, Glassdoor and Internshala are never sent to TinyFish, even when you have
  consented to them in HireFlow. Their existing rules still apply.
- **Auto-apply from the Finder.** Saving only adds a Swipe Review card.
