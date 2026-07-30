# Agentic Loop

> A research agent that thinks, executes tools in a loop, and stops when it has a verified final answer. Built with FastAPI + SQLAlchemy (async) + PostgreSQL.

---

## Architecture

```
User request
    │
    ▼
POST /v1/agent/query ──► create Run (status=running)
    │
    ▼
┌───────────────────── AGENT LOOP (max N iterations) ─────────────────────┐
│  1. Build context = system prompt + conversation history                │
│                     + execution memory (prior tool results)             │
│  2. Call LLM with tool schema list (local + MCP)                        │
│  3. LLM responds:                                                        │
│       ├─ has tool_calls  → execute in parallel → store in memory        │
│       └─ no tool_calls   → self-check for "is_final?"                   │
│              ├─ yes  → exit loop, status=success                         │
│              └─ no   → inject reflection prompt, continue               │
│  4. Stop: final answer | max_iterations | budget | timeout | error      │
└──────────────────────────────────────────────────────────────────────────┘
    │
    ▼
Response: { "answer": "...", "run_id": "..." }   ← answer only
Trace:    GET /v1/runs/{run_id}/trace             ← full reasoning
```

---

## Prerequisites

- Docker & Docker Compose
- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or pip

---

## Quick Start

### 1. Clone and set up environment

```bash
git clone <repo-url>
cd agentic-loop

# Copy environment file and fill in your secrets
cp .env.example .env
# Edit .env — at minimum set LLM_API_KEY and VEROSIGHT_API_KEY
```

### 2. Start PostgreSQL

```bash
make up
# Starts postgres:16 container and waits for it to be healthy
```

### 3. Install dependencies

```bash
make install
# Uses uv if available, falls back to pip
```

### 4. Run database migrations

```bash
make migrate
# Runs: alembic upgrade head
```

### 5. Start the development server

```bash
make dev
# Starts: uvicorn app.main:app --reload on port 8000
```

### 6. Verify

```bash
curl localhost:8000/health
# → {"status":"ok"}

curl localhost:8000/docs
# → OpenAPI Swagger UI (local env only)
```

---

## Available Make Targets

| Command | Description |
|---|---|
| `make up` | Start Docker services (PostgreSQL) |
| `make down` | Stop Docker services |
| `make dev` | Start FastAPI dev server with hot reload |
| `make migrate` | Run Alembic migrations (`upgrade head`) |
| `make migrate-create` | Create a new migration (prompts for name) |
| `make test` | Run pytest test suite |
| `make test-cov` | Run tests with coverage report |
| `make lint` | Lint with ruff + mypy |
| `make format` | Auto-format with ruff |
| `make install` | Install dependencies (uv or pip) |

---

## Environment Variables

See [`.env.example`](.env.example) for the full list. Key variables:

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | Async PostgreSQL URL (must use `+asyncpg`) | `postgresql+asyncpg://postgres:postgres@localhost:5432/rag_db` |
| `LLM_API_KEY` | OpenAI-compatible API key | `sk-xxx` |
| `LLM_MODEL` | Model name | `gpt-4o-mini` |
| `VEROSIGHT_MODE` | `rest` \| `mcp` \| `both` | `mcp` |

---

## Project Structure

```
agentic-loop/
├── app/
│   ├── main.py          # FastAPI app factory + lifespan
│   ├── config.py        # pydantic-settings (reads .env)
│   ├── api/             # HTTP routes
│   ├── agent/           # Agent loop, planner, state, policy
│   ├── llm/             # OpenAI-compatible async client
│   ├── tools/           # Modular tool system (registry, executor)
│   ├── mcp/             # MCP SSE client + adapter
│   ├── db/              # SQLAlchemy models, session, repositories
│   ├── logging/         # structlog setup, RunTracer, formatters
│   └── schemas/         # Pydantic API schemas
├── migrations/          # Alembic migration scripts
├── scripts/
│   ├── simulate.py      # Local simulation CLI
│   └── seed_scenarios.py
├── tests/
├── docker-compose.yml
├── pyproject.toml
├── alembic.ini
├── Makefile
└── .env.example
```

---

## Development Notes

- **No API key?** Set a real `LLM_API_KEY` in `.env`. Without a valid key the
  agent will fail with a structured `ErrorReport(component="llm", stage="auth")` —
  no silent mock fallback exists (see PROMPT_PLAN.md §1.6).
- **Adding a new tool**: Create one file in `app/tools/` and use the `@register_tool` decorator — no changes to core agent code needed.
- **Trace reasoning**: Agent reasoning is never exposed in API responses. Use `GET /v1/runs/{id}/trace` or the CLI `simulate replay <run_id>`.

---

## Prompt Build Plan

This project is built incrementally following `docs/PROMPT_PLAN.md`. Current progress:

- [x] **PROMPT 0** — Bootstrap repo & configuration
- [ ] PROMPT 1 — Database layer & migrations
- [ ] PROMPT 2 — Logging & tracing core
- [ ] PROMPT 3 — LLM client (OpenAI-compatible)
- [ ] PROMPT 4 — Tool registry modular
- [ ] PROMPT 5 — Verosight REST tools
- [ ] PROMPT 6 — MCP client & tool ingestion
- [ ] PROMPT 7 — State management
- [ ] PROMPT 8 — Agent loop (core)
- [ ] PROMPT 9 — API layer
- [ ] PROMPT 10 — Trace viewer & CLI simulation
- [ ] PROMPT 11 — Local simulation scenarios
- [ ] PROMPT 12 — Hardening, tests, documentation
