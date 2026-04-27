# QuickBites Support Bot

A GenAI-powered customer support bot for QuickBites that handles order complaints, issues refunds, files complaints, detects abuse, and escalates to humans.

**Stack**: Python 3.11 · FastAPI · LangGraph · LangChain · SQLite · claude-sonnet-4-6 · FAISS

---

## Quick start

```bash
# 1. Install pipenv if you don't have it
pip install pipenv

# 2. Install dependencies
pipenv install

# 3. Copy the environment file
cp .env.example .env  # edit if needed

# 4. Start the server
pipenv run python main.py
```

The server starts at `http://localhost:8000`.

- API docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/api/v1/healthz`

---

## Run a dev session

```bash
# Random dev scenario
curl -s -X POST http://localhost:8000/api/v1/session/run \
  -H 'Content-Type: application/json' \
  -d '{"mode":"dev"}' | python3 -m json.tool

# Specific rehearsal scenario (101–105)
curl -s -X POST http://localhost:8000/api/v1/session/run \
  -H 'Content-Type: application/json' \
  -d '{"mode":"dev","scenario_id":101}' | python3 -m json.tool
```

---

## Run tests

```bash
pipenv run python -m pytest tests/ -v
```

---

## Architecture (short version)

```
POST /api/v1/session/run
  → SessionRunner (drives conversation loop)
    → LangGraph Agent (per turn):
        gather_context  →  SQLite (8 tables) + FAISS policy RAG
        assess_risk     →  rule-based abuse scoring
        decide          →  Claude claude-sonnet-4-6 with tool-use (structured output)
    → POST /v1/session/{id}/reply → Simulator
```

See `docs/DESIGN.md` for the full design document.

---

## Project layout

```
app/
├── agent/
│   ├── graph.py              # LangGraph StateGraph
│   ├── state.py              # Shared state schema
│   └── nodes/
│       ├── gather_context.py # SQL + RAG context loading
│       ├── assess_risk.py    # Rule-based risk scoring
│       └── decide.py         # LLM decision + guardrails
├── api/v1/endpoints/
│   ├── sessions.py           # POST /session/run, GET /session/*/status
│   └── health.py
├── core/
│   ├── config.py             # Settings (pydantic-settings)
│   └── logging.py
├── prompts/
│   └── support_agent.py      # System prompt + tool schema
├── repositories/
│   └── database.py           # All SQLite queries
├── schemas/
│   ├── actions.py            # Pydantic action models
│   └── session.py            # Request/response schemas
└── services/
    ├── rag.py                # FAISS policy RAG
    └── simulator.py          # Simulator HTTP client + SessionRunner
tests/
├── test_agent/               # Unit tests for all nodes
├── test_api/                 # API endpoint tests
└── test_services/            # Simulator client + schema tests
docs/
├── ASSIGNMENT.md
├── DESIGN.md                 # Architecture, policy, evals
└── SIMULATOR_API.md
```
