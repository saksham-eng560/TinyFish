# AI models

Which AI HireFlow uses, and how to choose. See also
[how the providers fall back to each other](ARCHITECTURE.md#how-the-llm-provider-fallback-works).

| Option | Cost | Set in `.env` | Notes |
|---|---|---|---|
| None | Free | nothing | Built-in heuristics score jobs, fill forms and write template answers. Everything works. |
| **Ollama** | Free | `./start.sh --ollama` | Open models on your own computer or server, private, no key. **[Step-by-step guide](OLLAMA.md)** |
| Ollama Cloud | Free plan, then paid | `OLLAMA_BASE_URL=https://ollama.com` + `OLLAMA_API_KEY` | Much bigger models, nothing to download. [Guide](OLLAMA.md#ollama-cloud-big-models-no-download) |
| Claude (Anthropic) | Pay per use | `ANTHROPIC_API_KEY` | The best writer, with structured JSON outputs. First choice when set. |
| OpenAI | Pay per use | `OPENAI_API_KEY` | A secondary provider, and optional embeddings. |

`LLM_PROVIDER` picks the order: empty (auto) tries Claude, then OpenAI, then Ollama, whichever is set up;
`anthropic`, `openai` or `ollama` puts that one first and keeps the others as fallbacks. When every provider
fails, or a user's daily AI budget (`LLM_CALLS_PER_USER_PER_DAY`) is spent, the heuristics take over.

**Settings › Integrations › AI model** shows what's active, tests it (**Test AI**) and, for Ollama, downloads the
model. Keys are only ever set in `.env` (or your host's secret settings), never in the dashboard.
