# Free AI with Ollama

[Ollama](https://ollama.com) runs open AI models on your own computer or server: free, private, and with no API
key. With it, HireFlow's AI features work without a paid key: scoring jobs, tailoring your resume, writing cover
letters and answering form questions. Everything also works with no AI at all, on built-in heuristics; Ollama
makes the writing and matching much better, and Claude (an `ANTHROPIC_API_KEY`) better still.

- [The quick way (Mac or Linux)](#the-quick-way)
- [Which model to choose](#which-model-to-choose)
- [Windows](#windows) · [Docker](#with-docker) · [Your own server](#on-your-own-server) ·
  [Ollama Cloud](#ollama-cloud-big-models-no-download)
- [Check that it works](#check-that-it-works) · [What to expect](#what-to-expect) ·
  [Switch models or turn it off](#switch-models-or-turn-it-off) · [Troubleshooting](#troubleshooting) ·
  [All settings](#all-settings)

## The quick way

**1. Install Ollama.**

- **Mac:** download the app from https://ollama.com/download, drag it to Applications and open it once (a llama
  icon appears in the menu bar). Or, with Homebrew: `brew install ollama`, then `ollama serve` in a terminal
  you leave open.
- **Linux:** `curl -fsSL https://ollama.com/install.sh | sh` (it starts as a background service).

Check it's running: `curl http://localhost:11434/api/version` prints a version number.

**2. Run HireFlow with `--ollama`.** In the HireFlow folder:

```bash
./start.sh --ollama
```

It finds Ollama (and opens the Mac app if it isn't running), downloads the default model `qwen3.5:4b` once
(about 3.4 GB, with a progress bar), and adds two lines to `.env`:

```bash
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3.5:4b
```

It only fills in values that are missing or empty: it never overwrites something you set, and never touches any
other line. Next time, plain `./start.sh` (or `./start.sh --demo`) keeps using Ollama.

**3. Check it.** Open **Settings › Integrations › AI model** and press **Test AI** ([details](#check-that-it-works)).

> `LLM_PROVIDER=ollama` puts Ollama first. If you also have `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, they stay as
> fallbacks. With `LLM_PROVIDER` empty (auto), Claude goes first and Ollama is the fallback.

## Which model to choose

Pick the biggest one that fits comfortably in your computer's memory (RAM), leaving room for the browser and the
app. Set it as `OLLAMA_MODEL` in `.env` and run `./start.sh --ollama` again to download it.

| `OLLAMA_MODEL` | Download | Good for | Rough time per answer: Apple Silicon Mac / 4-core CPU, no GPU |
|---|---|---|---|
| `llama3.2:3b` | ~2 GB | 8 GB RAM; fastest, weakest on long resumes | seconds / under 1 min |
| `qwen3.5:4b` (default) | ~3.4 GB | 8–16 GB RAM; good scores and answers, plain writing | seconds / 1–2 min |
| `qwen3:4b` / `qwen3:8b` | ~2.5 / ~5.2 GB | proven, text-only alternatives: use them if a `qwen3.5` model won't load on your Mac | similar to their sizes |
| `qwen3.5:9b` | ~6.6 GB | 16 GB RAM or more; better writing and reasoning | 10–30 s / 2–4 min |

Bigger models are slower, and a model that doesn't fit in memory is very slow. Times are rough estimates.

## Windows

HireFlow runs on Windows through [WSL](https://learn.microsoft.com/windows/wsl/install) (Ubuntu). The simplest
setup is to install Ollama inside WSL too: in the Ubuntu terminal, run the Linux line from
[step 1](#the-quick-way), then `./start.sh --ollama`.

If you'd rather use the Ollama app for Windows (it can use your graphics card), HireFlow in WSL reaches it at
`localhost` only with WSL's mirrored networking. Otherwise put the Windows host's address in `.env`:
`OLLAMA_BASE_URL=http://<windows-ip>:11434`, and in the Windows app's settings allow connections from the
network.

## With Docker

`./start.sh --docker --ollama` runs the full stack in Docker and uses the Ollama app on your computer: inside a
container, `localhost` in `OLLAMA_BASE_URL` automatically means your computer (`host.docker.internal`).

To run Ollama in Docker as well, add these lines to `.env` and start the stack:

```bash
COMPOSE_PROFILES=ollama
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=qwen3.5:4b
```

```bash
docker compose up -d
docker compose exec ollama ollama pull qwen3.5:4b    # or press "Download model" in Settings
```

Models are kept in the `ollama-models` volume, so restarts and updates don't download them again. Ollama's API
has no password, so its port is never published: only HireFlow's containers can reach it.

## On your own server

On a server set up with [`scripts/server-setup.sh`](../scripts/server-setup.sh) (Oracle Cloud's Always Free ARM
machine has 24 GB of RAM: enough for the 4B and 9B models next to the app), re-run it with `WITH_OLLAMA=1`:

```bash
curl -fsSL https://raw.githubusercontent.com/saksham-eng560/HireFlow/main/scripts/server-setup.sh | WITH_OLLAMA=1 bash
# another model:  ... | WITH_OLLAMA=1 OLLAMA_MODEL=qwen3.5:9b bash
```

It keeps your `.env`, fills in only the four lines above (with `docker-compose.prod.yml`), downloads the model
inside the container and prints the disk space it uses. To do it by hand, add those lines to `~/hireflow/.env`,
run `docker compose -f docker-compose.prod.yml up -d`, then
`docker compose -f docker-compose.prod.yml exec ollama ollama pull qwen3.5:4b`.

## Ollama Cloud (big models, no download)

Ollama also runs much bigger models on its own servers, with a free plan. Create an API key at
https://ollama.com/settings/keys, then open `.env` **in a text editor** and add:

```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=https://ollama.com
OLLAMA_MODEL=gpt-oss:120b
OLLAMA_API_KEY=            # paste the key here
```

Restart HireFlow. Keep the key in `.env` only: never on the command line, in the dashboard or in git. Cloud models
don't support JSON-schema output, so HireFlow asks for JSON in the prompt and checks every answer, retrying once.
The free plan runs one request at a time, which is what `OLLAMA_CONCURRENCY=1` (the default) does.

You can also use cloud models through a local Ollama: run `ollama signin`, then pick a model whose name ends in
`-cloud` (no `OLLAMA_API_KEY` needed in that case).

## Check that it works

**Settings › Integrations › AI model** shows:

- the active provider and model, and the fallbacks;
- whether Ollama is reachable, its version, and whether the model is downloaded. If it isn't, a **Download
  model** button downloads it with a progress bar;
- **Test AI**: sends one small request and shows the answer and how long it took;
- copyable `.env` lines for each setup above.

The page never asks for keys or shows them: edit `.env` and restart instead.

From a terminal: `ollama list` shows the downloaded models, and `ollama ps` the one currently loaded.
`curl http://localhost:8000/health/ready` shows HireFlow's view: `"llm"` lists the providers in the order they're
tried, `ollama` first when it's in charge.

## What to expect

- **Slower than Claude**, especially without a GPU. A scan gives the best `OLLAMA_MAX_EVALUATIONS_PER_SCAN` (15)
  jobs to the model one at a time and scores the rest instantly with the built-in heuristic; cards reach Swipe
  Review as they're scored. Preparing a kept job (resume, cover letter, answers) can take a few minutes on a CPU.
- **Plainer writing.** Small models score jobs well, but their cover letters and written answers are less
  polished. Now and then a reply misses the requested format: the app asks once more, then falls back to the
  heuristic. Every application goes through your review first, so you can fix anything before it's submitted.
- **Context.** `OLLAMA_NUM_CTX` (8192 tokens) is how much the model reads at once (Ollama's own default is only
  4096). Long job descriptions and resumes are shortened to fit; raise it, e.g. to 16384, if you have RAM to spare.
- **Thinking models** (`qwen3.5`, `gpt-oss`) answer directly by default; `OLLAMA_THINK=true` lets them reason
  first: a little better, much slower.
- **Embeddings (optional).** `EMBEDDING_PROVIDER=ollama` matches jobs with `nomic-embed-text`
  (`ollama pull nomic-embed-text`) instead of the built-in keyword matcher. After switching, re-compute the stored
  vectors with `python scripts/migrate.py --reembed` (in Docker: `docker compose exec api python scripts/migrate.py --reembed`).

## Switch models or turn it off

- **Another model:** change `OLLAMA_MODEL` in `.env`, run `./start.sh --ollama` (it downloads the new one), and
  free the old one's disk space with `ollama rm <old-model>`.
- **Back to Claude first:** empty `LLM_PROVIDER=` (auto) and set `ANTHROPIC_API_KEY`; Ollama stays as the fallback.
- **Off:** empty both `LLM_PROVIDER=` and `OLLAMA_MODEL=`.

Restart HireFlow after editing `.env`.

## Troubleshooting

| What you see | What to do |
|---|---|
| `Ollama isn't running at http://localhost:11434` | Open the Ollama app (Mac), or run `ollama serve` (Linux: `sudo systemctl start ollama`). Check with `curl http://localhost:11434/api/version`. |
| Settings says the model isn't downloaded | Press **Download model**, or run `ollama pull <model>`. The name must match `OLLAMA_MODEL` exactly, tag included (`qwen3.5:4b`). |
| Answers time out or take very long | Use a smaller model, close other heavy apps, or raise `OLLAMA_TIMEOUT_SECONDS` (default 600). Keep `OLLAMA_CONCURRENCY=1` on a CPU. |
| Test AI says `failed to allocate context` or `failed to initialize the Metal library` | Quit Ollama from the menu bar and open it again, then press **Test AI** again (`ollama ps` shows the loaded models, `ollama stop <model>` unloads one: an `ollama run` in a terminal keeps its model for a few minutes). Still failing: some Ollama versions can't load the image part of `qwen3.5` models on some Macs, so switch to a text-only model, `OLLAMA_MODEL=qwen3:8b` (or `qwen3:4b` with 8 GB of RAM), and run `./start.sh --ollama`. |
| The computer slows down or the model crashes | It doesn't fit in memory: switch to a smaller model (see [the table](#which-model-to-choose)). |
| In Docker, the app can't reach Ollama | With the app on your computer: keep `OLLAMA_BASE_URL` empty or `http://localhost:11434`. With the `ollama` container: `COMPOSE_PROFILES=ollama` and `OLLAMA_BASE_URL=http://ollama:11434`. |
| Ollama Cloud: `401` or "unauthorized" | `OLLAMA_API_KEY` is missing or wrong in `.env`, or `OLLAMA_BASE_URL` isn't `https://ollama.com`. |
| Cover letters look generic | Expected from small models: try `qwen3.5:9b`, Ollama Cloud, or a Claude key; edit before approving. |
| Test AI works but scans still say "heuristic" | Only the top `OLLAMA_MAX_EVALUATIONS_PER_SCAN` jobs go to the model; the rest are scored by the heuristic on purpose. |

## All settings

Set in `.env`. Only `OLLAMA_MODEL` (or `LLM_PROVIDER=ollama`) is
needed; the rest have sensible defaults.

| Setting | Default | What it does |
|---|---|---|
| `LLM_PROVIDER` | empty (auto) | `ollama` puts Ollama first; empty = Claude, then OpenAI, then Ollama, whichever is set up |
| `OLLAMA_MODEL` | empty (off) | The model, e.g. `qwen3.5:4b`. With `LLM_PROVIDER=ollama` and this empty, `qwen3.5:4b` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where Ollama runs; `http://ollama:11434` in Docker, `https://ollama.com` for the cloud |
| `OLLAMA_API_KEY` | empty | Only for Ollama Cloud |
| `OLLAMA_NUM_CTX` | `8192` | Context window in tokens |
| `OLLAMA_KEEP_ALIVE` | `30m` | How long the model stays loaded between calls |
| `OLLAMA_TIMEOUT_SECONDS` | `600` | Longest wait for one answer |
| `OLLAMA_CONCURRENCY` | `1` | Calls at once (1–2 on a CPU) |
| `OLLAMA_MAX_EVALUATIONS_PER_SCAN` | `15` | Jobs per scan scored by the model; the rest by the heuristic |
| `OLLAMA_THINK` | `false` | Let thinking models reason before answering |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | With `EMBEDDING_PROVIDER=ollama` |

How the providers fall back to each other: [ARCHITECTURE.md](ARCHITECTURE.md#how-the-llm-provider-fallback-works).
