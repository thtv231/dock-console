# Dock Console

A web app that shows every capability of **Dock**, the operations agent of Harbor Freight Co.
(a made-up freight forwarder).

## Layout

- `dock/` — the existing agent: `app.py` (triage, handbook search/answer, screening, reply,
  policy check, `email_task` tool loop), `mcp_server.py`, and its data (`handbook.json`,
  `inbox.json`, `questions.json`, `documents/`, `calls/`, `*truth.json`, `dock.sqlite`).
  **Reuse it. Do not rewrite its prompts or Pydantic schemas**; import and call them.
- `api/` — FastAPI backend, deployed as Vercel Python Functions.
- Next.js app (App Router, TypeScript, Tailwind, shadcn/ui) at the repo root.

## Stack

- Mistral Python SDK `mistralai` 3.6: `from mistralai.client import Mistral`.
- FastAPI + uvicorn locally; Vercel Python runtime in production.

## Modes

- **Replay (default):** serve recorded responses from real runs. No API key needed.
- **Live:** the visitor's own Mistral key, sent per request in the `X-Mistral-Key` header.
  Build the client per request from that header. Never store, cache, or log the key.

## Rules

- Never put a real key in code, tests, fixtures, logs, or replay files. `.env` stays
  gitignored; `.env.example` holds only an empty `MISTRAL_API_KEY=`.
- All data is made up. `send_email` never sends a real email; it only records to an outbox.
- Every number shown in the UI must come from a real run (replay files or a live call).
  Never type numbers by hand into components or copy.
- Writes (`send_email`) always need explicit user approval in the UI. The server also
  refuses any recipient who is not in `list_customers()`, regardless of what the UI sent.

## Commands

```bash
# API (from repo root)
python -m venv .venv && .venv/Scripts/activate   # Windows; use .venv/bin/activate elsewhere
pip install -r requirements.txt
uvicorn api.index:app --reload --port 8000

# Web app
npm install
npx next dev        # open http://localhost:3000 (not 127.0.0.1)

# Tests
pytest
```

Tests must run in replay mode with no key set.
