# Running and self-hosting HireFlow

Run it on your own computer (the usual way: [README](../README.md#quick-start)), or on a server of your own.

## One-command start

```bash
git clone https://github.com/saksham-eng560/HireFlow.git && cd HireFlow
./start.sh                 # macOS / Linux / WSL — needs Python 3.11+ and Node 20+
```

The first run creates `.env` with fresh secrets, a Python virtualenv, installs Chromium and the npm
packages, and prepares a local SQLite database. Later runs start in seconds and only reinstall when
requirements change. It then runs the API (:8000), the dashboard (:3000) and the task queue, opens
your browser and streams the logs. **Ctrl-C stops everything.**

| Command | What it does |
|---|---|
| `./start.sh` | Local mode, no Docker. Uses Redis + a Celery worker + beat if `redis-server` is installed, otherwise runs tasks in-process with a built-in scheduler (scheduled scans still happen). |
| `./start.sh --demo` | Practice mode, with its own database: create any account, applications go to a bundled careers site with fictional companies, nothing real is sent, and everything resets nightly. |
| `./start.sh --sample` | Also seeds a sample account (`demo@example.com` / `demo-password-123`) with a swipe deck, and serves the standalone sample careers site on :8765. |
| `./start.sh --prod` | Production build of the dashboard (faster pages). |
| `./start.sh --docker` | The full Docker Compose stack (PostgreSQL + pgvector, Redis, worker, beat). `./start.sh --stop` stops it. |
| `./start.sh --reset` | Wipes the local database first. |
| `./start.sh --ollama` | Sets up a free AI model on this computer with Ollama: checks it, downloads the model, fills in `.env` (see [AI models](AI_MODELS.md)). |
| `start.bat` | Windows: the Docker path (`start.bat stop` to stop). Or use WSL and `./start.sh`. |

Your first ten minutes:

1. **Create your account** at http://localhost:3000/register.
2. **Upload your resume** in Resume Lab (PDF, DOCX or pasted text) and check the parsed result.
3. **Settings › Mass apply › Internships**: one click adds the internship lists and startup boards.
4. **Settings › Saved answers**: save work authorization and visa sponsorship. Without them, kept jobs
   stop in Needs approval instead of being submitted.
5. **Scan for jobs now**, then open **Swipe Review** and start swiping.

Add `ANTHROPIC_API_KEY=...` to `.env` for much better scoring, tailoring and answers, or run
`./start.sh --ollama` for a free model on your own computer (everything also works without either, on
built-in heuristics). `make start` does the same as `./start.sh`.

## Quick start (Docker)

Requirements: Docker with Compose v2, about 4 GB RAM free.

```bash
git clone https://github.com/saksham-eng560/HireFlow.git
cd HireFlow
scripts/init-env.sh      # creates a private .env with freshly generated secrets
```

Open `.env` in a text editor and paste your Anthropic API key where indicated. This is optional but
strongly recommended. Your keys stay in `.env` on your own machine, and git is set up to never commit
that file. Don't share it or paste keys on the command line.

Then start everything:

```bash
docker compose up --build -d        # or: make up
```

| | |
|---|---|
| Dashboard | http://localhost:3000 |
| API docs (OpenAPI) | http://localhost:8000/docs |
| Health | http://localhost:8000/health/ready |

This starts PostgreSQL + pgvector, Redis, the API (which runs database migrations on start), a Celery
worker with Chromium, Celery beat, and the dashboard. Open the dashboard, create your account, and
follow the **Get your agent ready** checklist.

Optional: `docker compose exec api python scripts/seed_db.py` creates a sample account
(`demo@example.com` / `demo-password-123`) with sample data, so every page has something to show.
`./start.sh --docker --demo` runs the stack in demo mode instead (see [Demo mode](USER_GUIDE.md#demo-mode)).

## Local development without Docker

`./start.sh` does all of the following for you; the individual steps are here if you prefer them.

Requirements: Python 3.11+, Node 20+, PostgreSQL 16 with the `pgvector` extension (or SQLite for a
quick try), and Redis (optional with `CELERY_TASK_ALWAYS_EAGER=true`).

```bash
make setup          # backend venv + deps + Chromium, dashboard deps, .env
make migrate        # alembic upgrade head (or create tables on SQLite)
make api            # FastAPI on :8000 (reload)
make worker         # Celery worker        ┐ or set CELERY_TASK_ALWAYS_EAGER=true
make beat           # Celery beat schedule ┘ and skip both
make web            # Next.js dev server on :3000
make demo           # demo careers site on :8765
```

Simplest possible setup, with no Postgres or Redis: set `DATABASE_URL=sqlite:///./data/hireflow.db`,
`CELERY_TASK_ALWAYS_EAGER=true` and `REDIS_URL=` in `.env`, then run `make api` and `make web`.
Scheduled scans need beat and Redis.

### Coming from AutoApply AI

HireFlow was previously called AutoApply AI. Every feature, setting and page carried over, and your data can too.
A fresh install starts empty; to keep your old data:

**Local (`./start.sh`), one command.** Stop both apps (**Ctrl-C**), then in the HireFlow folder:

```bash
backend/.venv/bin/python scripts/import_autoapply.py ~/autoapply-ai
```

(Use the path of your AutoApply AI folder, the one with its `start.sh`. Run `./start.sh` once first if
`backend/.venv` doesn't exist yet.) It copies, and never changes anything in the AutoApply folder:

- the database (`backend/data/autoapply.db` becomes `backend/data/hireflow.db`): accounts and passwords, resumes,
  jobs, swipes, applications, answers, interviews, e-mails and settings, including changes still in its `-wal` file;
- the stored files (`backend/data/storage`: uploaded resumes, generated PDFs, form screenshots);
- `SECRET_KEY` and `ENCRYPTION_KEY` from AutoApply's `.env`, so saved LinkedIn, Internshala, Google and ATS logins
  stay readable, plus your own settings (AI keys, Google client, Ollama, SMTP...) wherever HireFlow's `.env` has
  none yet. Machine settings (database, URLs) are never copied, and only names are printed, never values.

Then it adds HireFlow's new columns and indexes; accounts that already existed skip the first-run onboarding.
If HireFlow already has accounts it stops; `--replace` swaps them for your AutoApply data and keeps the old
`hireflow.db` and `.env` as `.bak-<date>` copies. `./start.sh` and `scripts/account.py where` point to it when
they find an AutoApply AI folder next to HireFlow.

**PostgreSQL (Docker or your own server).** Keep the same `SECRET_KEY` and `ENCRYPTION_KEY` in `.env`, point
`DATABASE_URL` at your old database, and run `cd backend && alembic stamp --purge 0001` once. The six old
migrations are now one revision, so this marks the existing schema as current; the next start applies HireFlow's
own migrations on top. Copy the stored files too if you use local storage.

Either way, everyone signs in once more (the session cookie is now `hireflow_session`), and the Chrome extension
is loaded again from HireFlow's `extension/` folder, with a new token from **Settings › Integrations**.
Applications you logged without a link keep their old placeholder, which HireFlow hides like its own.

Other useful commands:

```bash
make help                                                   # everything available
backend/.venv/bin/python scripts/test_scraper.py greenhouse --source stripe -k "Software Engineer"
backend/.venv/bin/python scripts/test_scraper.py url https://job-boards.greenhouse.io/stripe/jobs/123
backend/.venv/bin/python scripts/migrate.py --reembed       # after changing EMBEDDING_PROVIDER
```

## On your own server (optional)

### Free, always on: Oracle Cloud "Always Free"

Oracle's Always Free tier includes an ARM server with up to 4 cores and 24 GB of RAM that doesn't
expire. That's enough to run everything 24/7 at no cost.

1. **Create an account** at https://www.oracle.com/cloud/free/. A card is needed for verification
   but isn't charged. Your *home region* can't be changed later, so pick one near you.
2. **Create the server.** Go to **Compute → Instances → Create instance** and set:
   - **Image**: Canonical Ubuntu 24.04.
   - **Shape**: *Change shape → Ampere → VM.Standard.A1.Flex*, 4 OCPUs and 24 GB memory. It's
     labelled "Always Free-eligible".
   - **Networking**: keep "Create new virtual cloud network" and "Assign a public IPv4 address".
   - **SSH keys**: *Generate a key pair* and **download the private key**.

   If you see "Out of capacity", try another availability domain, try 2 OCPUs / 12 GB, or retry
   later. This is common for free ARM servers.
3. **Open ports 80 and 443.** On the instance page, click the subnet, then its **Security List**,
   then **Add Ingress Rules**. Set source CIDR `0.0.0.0/0`, IP protocol TCP, and destination port
   range `80,443`.
4. **Connect and run the setup script.** The instance page shows the public IP address.
   ```bash
   chmod 600 ~/Downloads/ssh-key-*.key
   ssh -i ~/Downloads/ssh-key-*.key ubuntu@<PUBLIC_IP>
   curl -fsSL https://raw.githubusercontent.com/saksham-eng560/HireFlow/main/scripts/server-setup.sh | bash
   ```
   [`scripts/server-setup.sh`](../scripts/server-setup.sh) does the rest:
   - installs Docker and opens the server's own firewall (Oracle's Ubuntu image blocks everything
     except SSH);
   - generates fresh secrets into `~/hireflow/.env`;
   - builds and starts the stack with HTTPS. The first build takes about 10 minutes.

   It prints your URL when it's done. Without a `DOMAIN`, the URL is
   `https://<ip-with-dashes>.sslip.io`, a free hostname that points at your server.
5. **Add your API key privately.** On the server, run `nano ~/hireflow/.env`, paste your
   Anthropic key into its line, and save. Then restart:
   `cd ~/hireflow && docker compose -f docker-compose.prod.yml up -d`. Editing the file keeps the
   key out of your shell history and out of git. The file is readable only by your user.
6. **Open the URL and create your account.** Then turn off sign-ups as the script's output shows.

Useful follow-ups:
- **Your own domain**: point its DNS A record at the server, then re-run the script with
  `DOMAIN=jobs.example.com` in front of `bash`. A real domain is recommended before connecting
  Google.
- **Changing settings**: edit `~/hireflow/.env`, then run
  `docker compose -f docker-compose.prod.yml up -d` in that folder.
- **Updating**: re-run the script.
- **Free AI on the server**: re-run the script with `WITH_OLLAMA=1` in front of `bash`. See
  [Free AI with Ollama](OLLAMA.md).
- **Idle servers**: Oracle may reclaim Always Free servers that stay almost completely idle for a
  week. Scheduled scans normally keep the server active enough. Upgrading the account to
  Pay-As-You-Go removes this risk, and Always Free resources stay free.

### Any other server with automatic HTTPS

This works on any Ubuntu VPS with 2+ vCPU and 4+ GB RAM (Hetzner, DigitalOcean, Lightsail…). Either
run the same `server-setup.sh` command as above, or do it by hand:

```bash
# DNS: point your domain at the server, then:
scripts/init-env.sh      # private .env with generated secrets; then add your domain and API keys with an editor
docker compose -f docker-compose.prod.yml up -d --build      # or: make prod-up
```

Caddy obtains a Let's Encrypt certificate for `DOMAIN` automatically. It routes `/api/v1/*`, `/docs`
and `/health*` to the API and everything else to the dashboard. Set `ALLOW_REGISTRATION=false` once
your account exists. Set the Google redirect URI to `https://<DOMAIN>/api/v1/auth/google/callback`.
Back up the `pgdata` and `storage` volumes.
