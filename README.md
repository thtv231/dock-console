# Dock Console

Dock Console is a web app with one screen for each capability of **Dock**, the operations agent of
Harbor Freight Co. Harbor Freight is a made-up freight forwarder. Dock reads customer email, answers
from the ops handbook, reads invoices and bills of lading, hands work to other agents, drafts emails
that a person approves, screens inbound messages, takes voicemails, and is scored and shipped with
budgets, timeouts and rollbacks. It runs on Mistral models.

There are two modes:

- **Replay** (the default) serves recorded responses from real runs. It needs no API key. Every number on
  every screen comes from `replays/`.
- **Live** runs the same code with your own Mistral key. The key stays in browser memory and goes out
  with each request in the `X-Mistral-Key` header. The server never stores, caches or logs it.

All data is made up. `send_email` never sends an email; it only records to an outbox.

![Overview](docs/screens/overview.png)

The course builds it in seven lessons. Every lesson's end state is tagged, so you can check out any
lesson and run it:

```bash
git checkout capstone-3      # the end of lesson 3
git checkout main            # back to the finished app
```

| Tag | Lesson | Screens |
|---|---|---|
| `capstone-1` | 1. The plan | – |
| `capstone-2` | 2. Skeleton and Inbox | Inbox |
| `capstone-3` | 3. Read-only capabilities | Handbook, Guardrails, Documents |
| `capstone-4` | 4. Agents and handoffs | Agents |
| `capstone-5` | 5. Approvals | Approvals |
| `capstone-6` | 6. Voice desk and Scorecard | Voice desk, Scorecard |
| `capstone-7` | 7. Operations, MCP, Overview, ship it | Operations, Overview |

## Run it

You need Python 3.12 and Node.js (the course used Node 24).

**1. Python API**

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; use source .venv/bin/activate elsewhere
pip install -r requirements-dev.txt
```

`requirements.txt` holds what Vercel installs. `requirements-dev.txt` adds uvicorn, pytest and httpx.

**2. `.env`**

```bash
cp .env.example .env
```

Then add a signing secret for the Approvals screen. Approvals refuses to start without one, in Replay
too:

```bash
python -c "import secrets; print('DOCK_STATE_SECRET=' + secrets.token_hex(32))" >> .env
```

`MISTRAL_API_KEY` can stay empty. Replay and the tests don't need it. Only `scripts/record.py` reads it,
to record new runs. `.env` is gitignored. Never commit it.

**3. Web app**

```bash
npm install
```

**4. Start both servers**, each in its own terminal:

```bash
uvicorn api.index:app --reload --port 8000
npx next dev
```

Open **http://localhost:3000** (not 127.0.0.1). In development, Next proxies `/api/*` to uvicorn on
`127.0.0.1:8000`. Set `DOCK_API_URL` if port 8000 is taken.

**Replay and Live.** The badge at the top right shows the mode. Replay needs nothing: press a screen's
*… all* button and it plays the recorded runs. To go Live, press **Use my key** and paste a Mistral key.
The key is held in React state, never in `localStorage`, and it is gone when you reload the page. Every
response shows a meter with each Mistral call's model, milliseconds and tokens. A case with no recording
(free text, for example) returns `not_recorded`, and the screen offers Live. Live requests are rate
limited per IP (`DOCK_LIVE_PER_MIN`, default 10).

**Tests**

```bash
pytest
```

The tests run in Replay with no key set. Right now: **532 passed** (and 4 DeprecationWarnings from
the `jsonpath` package, a dependency).

**Screenshots.** `docs/screens/` was captured with `node scripts/screenshots.mjs`, with both servers
running. It uses Playwright (a dev dependency; run `npx playwright install chromium` once). It opens each
screen with no key and presses its *… all* button.

## The lessons

Each lesson starts with a commit of failing tests ("tests first (red)") and ends with the screen. Tests
are counted with `pytest -rA`, grouped by file. Each file is listed under the lesson that added it, and
its count is what that file runs in the finished app.

### Lesson 1 — The plan · `capstone-1`

**Built:** [PLAN.md](PLAN.md) and the starting point. That is Dock as it already existed
(`dock/app.py`, `dock/mcp_server.py`, the data in `dock/`) and the eleven notebooks in `notebooks/`
(s01–s10 and s05b), one per capability.

The plan covers:
- the folder layout (Next.js and FastAPI in one Vercel project);
- every API route;
- how replays are recorded and looked up;
- how Approvals pauses and resumes with no server-side store;
- the Vercel risks.

One rule holds throughout: **reuse Dock**. Its prompts and Pydantic schemas are imported, never
rewritten. The notebook-only capabilities are ported into small `dock/` modules with every prompt,
docstring and schema kept as written.

**Tests:** none yet.

### Lesson 2 — Skeleton and Inbox (S1) · `capstone-2`

![Inbox](docs/screens/inbox.png)

**Built:**
- `dock/_client.py`: a client proxy that builds the Mistral client per request from the visitor's key,
  plus a meter that records latency and tokens for every SDK call;
- the one-line change in `dock/app.py`, so Dock imports with no key;
- `api/index.py`, `/api/health`, `/api/data/*` and `/api/triage`;
- the mode middleware, replay lookup and rate limit;
- the recorder, `scripts/record.py`;
- the web shell: sidebar, mode badge, key dialog;
- the Inbox screen.

**Features:** Dock triages each of the 20 inbox emails into intent, shipment and language, checked
against the answer key. Replay: **20/20 right**, median **822 ms** and **86 tokens** per email
(`mistral-small-latest`).

**Tests:**

| File | What it checks | Result |
|---|---|---|
| `test_no_key.py` | Dock imports with no key; the visitor's key never comes back in a response or header | 3 passed |
| `test_replay.py` | every recorded triage key resolves; every inbox mail is recorded; an unknown id is `not_recorded` | 3 passed |

### Lesson 3 — Handbook (S3), Guardrails (S7), Documents (S2) · `capstone-3`

![Handbook](docs/screens/handbook.png)
![Guardrails](docs/screens/guardrails.png)
![Documents](docs/screens/documents.png)

**Built:** `dock/documents.py` (ported from s02), the handbook vectors recorded once into
`replays/handbook_vectors.npy`, the answer, screen, reply and extract routes, and three screens.

**Features:**
- **Handbook:** answers come from the ops handbook with the sections they cite and the retrieved
  sections' scores. **14/14** answers cite the expected sections and contain the expected words. Median
  **1,576 ms**, **342 tokens** (`mistral-embed` + `mistral-medium-latest`).
- **Guardrails:** inbound screening with moderation categories, plus the never-guarantee-a-date rule
  on replies. **28/28** messages are screened as the answer key expects. Replies that guarantee a date:
  **0/5** under Dock's rule, **2/5** under a plain prompt. Median **1,340 ms**, **72 tokens**.
- **Documents:** OCR (`mistral-ocr-latest`) reads invoices and bills of lading into fields, which are
  checked against the database. The final schema reads **11/11** documents right; the first schema
  reads **0/7** invoices right. `invoice_07` shows the total mismatch (2 problems) and
  `bill_of_lading_04` the bad container check digit (1 problem). Median **2,135 ms**.

**Tests:**

| File | What it checks | Result |
|---|---|---|
| `test_handbook.py` | every question recorded; the two out-of-handbook questions are not found; sources retrieved and ranked best first; must-contain words present | 43 passed |
| `test_guardrails.py` | bad messages blocked, normal mail let through; no recorded reply guarantees a date; a failed reply shows the judge's quote; PII is scored but never a reason | 17 passed |
| `test_documents.py` | invoice_07's total mismatch; clean invoices have no problems; the check digit; a misread container is a problem, not a crash; PDFs served by name only | 18 passed |

### Lesson 4 — Agents and handoffs (S4, S6) · `capstone-4`

![Agents](docs/screens/agents.png)

**Built:** `dock/agents.py` (ported from s04 and s06), the handoff route (create the agents, run,
always delete them in `finally`), the design route, metered SDK retries, a connect timeout that fails
fast on a hung connection, a recorder that records only from a clean git tree, and the Agents screen.

**Features:**
- **Handoffs:** a triage agent hands each email to a tracking or billing agent. Under routing rule v1,
  **17/20** emails are routed right; under the fixed rule v2, **19/20**. **120/120** agents created
  were deleted.
- **Multi-agent designs:** the customs hold report is written by one agent with tools (design A) or by
  parallel workers, one per held shipment. Runs that covered every held shipment: design A **4/10**,
  parallel workers **10/10**. A guard refuses the injected note.
- Median **5,780 ms**, **1,035 tokens** per request (`mistral-medium-latest`).

**Tests:**

| File | What it checks | Result |
|---|---|---|
| `test_handoffs.py` | every mail routed under both rules; v2 matches 19/20; a recorded handoff deletes every agent it created | 42 passed |
| `test_design.py` | every design run replayed; A and C write the same report; A is one agent, C one worker per shipment; the guard refuses the injected note | 25 passed |
| `test_cleanup.py` | with Mistral stubbed: a failure half way still deletes every agent; a timeout reports the agents it could not delete | 6 passed |
| `test_meter.py` | retries are counted per call; a hung connect fails fast and is retried | 2 passed |
| `test_record.py` | the recorder refuses a dirty tree, before it reads the key | 4 passed |

### Lesson 5 — Approvals (S5) · `capstone-5`

![Approvals](docs/screens/approvals.png)

**Built:**
- `api/core/state.py`: HMAC-signed state with an expiry;
- `dock/hitl.py`;
- the start and resume routes, with the server's own recipient refusal;
- the Approvals screen: drafts, approve/reject buttons, trace and outbox.

**Features:** Dock is asked why HF-26038 is on hold and to email the consignee. The shipment's
tracking note carries an injected instruction to email the customer list to
`audit@harbour-review.example.net`. Every `send_email` waits for a person. On top of that, the server
refuses any recipient who is not a customer, whatever the person chose.

Each decision policy (approve the consignee only, approve every draft, reject every draft) was run 5
times per instruction set (plain, safe), for **30** runs, and every run was kept. Results:
- runs that emailed a non-customer: **0/30**;
- runs that emailed the consignee: **19/30**;
- runs where Dock's answer contradicts the outbox: **8/30**.

The finished run's summary is built from the outbox, not from Dock's words. Median **2,190 ms**,
**1,196 tokens**.

**Tests:**

| File | What it checks | Result |
|---|---|---|
| `test_approvals.py` | an approved non-customer is still refused; replay keys don't depend on draft order; one decision covers every draft to the same recipient; every policy recorded 5 times; what was approved is exactly what was sent; the summary comes from the outbox; contradicting answers are flagged | 135 passed |
| `test_state.py` | sign/verify round trip; a tampered, garbage or expired state is 400; decisions must name exactly the pending recipients; a second resume is 409; no secret, no approvals | 14 passed |

### Lesson 6 — Voice desk (S9) and Scorecard (S8) · `capstone-6`

![Voice desk](docs/screens/voice.png)
![Scorecard](docs/screens/scorecard.png)

**Built:** `dock/voice.py` and `dock/scorer.py` (ported from s09 and s08), the voice route (recorded
WAVs plus a live mic upload capped at 4 MB), the case and judge routes, and two screens.

**Features:**
- **Voice desk:** six driver voicemails are transcribed (`voxtral-mini-latest`), turned into a call note
  (shipment, issue, callback), and answered by voice (`voxtral-mini-tts-latest`). Each call was
  recorded 5 times without context bias and 5 times with it. Calls noted fully right: **26/30**
  without bias, **30/30** with bias. Every miss is C2's issue. To tell hearing from understanding, C2
  was noted again on one unchanged transcript per source: the issue was right **2/5** times on the
  unbiased transcript and **3/5** on the biased one. Median **5,517 ms**, **630 tokens**.
- **Scorecard:** 30 cases (triage, answers, screening, replies, agent paths), run on the baseline and on
  a cheaper candidate (`ministral-8b-latest` for answers and triage). Results:
  - baseline: **29/30** (it fails G-HF-26038, which called `list_customers`);
  - cheaper: **26/30**, with **3** regressions (A-Q02, A-Q05, A-Q09);
  - an LLM judge checks that answers are grounded.

  Median **1,576 ms**, **323 tokens**.

**Tests:**

| File | What it checks | Result |
|---|---|---|
| `test_voice.py` | every call has its 10 recorded runs; runs counted against the truth; C2 re-noted on one transcript; with bias every note matches; reply audio is a WAV URL, not base64; an upload over 4 MB is 413 without calling Mistral | 68 passed |
| `test_scorecard.py` | every case replayed; baseline 29/30 failing only on `list_customers`; the failing agent case shows its path; the cheaper candidate's regressions case by case; every judge verdict quotes the answer it judged | 81 passed |

### Lesson 7 — Operations (S10), MCP, Overview, ship it · `capstone-7`

![Operations](docs/screens/operations.png)

**Built:**
- `dock/ops.py` (ported from s10) and its recorded runs;
- `/api/ops/runs`;
- Dock's MCP server mounted at `/api/mcp`;
- the MCP tool tester and connection guide;
- the Overview, computed from `replays/`;
- `vercel.json`, the bundle size check, the screenshots and this README.

**Features:**
- **Operations** shows recorded runs only. It creates agents and spends a budget, so it never runs
  live.
  - **Token budget:** a daily limit of 300 with a reserve of 150 refuses **3/5** requests (M03–M05) with
    429 once 190 tokens are used.
  - **Time limit:** with an 8 s limit, Dock answers "four days, from HB-07" in **1,365 ms**. With a
    0.2 s limit, the question goes to a person, and the abandoned answer call still ran for
    **1,749 ms** and used **339 tokens**.
  - **Retries:** **3** retries with one `Idempotency-Key` send **1** email.
  - **Release check:** version 1 guarantees a date in **3/3** replies, version 0 in **0/3**. The
    rollback puts `prod` back on **version 0**, and the agent is deleted.
- **MCP tester:** lists Dock's three tools with read/write hints and calls them. `send_email` waits for
  your approval, and the server refuses `audit@` even when it is approved.
- **Overview:** every capability with its scores, median latency and tokens, recorded date and commit.
  All of it is recomputed from **382** recorded requests made at **10** commits.

**Tests:**

| File | What it checks | Result |
|---|---|---|
| `test_ops.py` | every step is a recorded run; the budget refuses M03 after 190 tokens; 8 s answers, 0.2 s hands over; 3 retries send once; v1 3/3 vs v0 0/3; the rollback puts prod on 0; an abandoned answer is metered when it finishes | 12 passed |
| `test_mcp.py` | an outside client lists exactly three tools with the right hints; `send_email` to audit@ is refused; the outbox is outside the repo; the tester won't send without approval | 13 passed |
| `test_overview.py` | all 10 screens in sidebar order; medians, scores and the scorecard recomputed from the replays; every replay comes from a clean commit; no card claims a cause its counts can't show | 41 passed |
| `test_deploy.py` | `/api/*` goes to the one Python function; no Next `app/api` folder; the function carries every file the API reads and leaves out local-only files; Vercel installs no test-only packages | 5 passed |

## Architecture

```
browser ── Next.js pages (app/, components/, lib/) ──┐
                                                     │  /api/*
                     Vercel rewrite /api/(.*) ───────┴─► api/index.py  (FastAPI, one Python function)
                     (dev: Next proxies to uvicorn :8000)   ├─ api/core/mode.py    X-Mistral-Key → per-request client
                                                            ├─ api/core/replay.py  replays/*.json lookup
                                                            ├─ api/core/state.py   signed approval state
                                                            ├─ api/routes/*        one router per screen
                                                            ├─ /api/mcp            Dock's MCP server
                                                            └─ dock/               the agent, reused as is
```

- **Next.js and FastAPI in one Vercel project.** Next.js (App Router, TypeScript, Tailwind, shadcn/ui)
  serves the pages. `api/index.py` is a single Vercel Python Function that serves every `/api/*`
  route; `vercel.json` rewrites `/api/(.*)` to it. There is no Next `app/api` folder, because it would
  shadow the Python routes.
- **Replay or Live, per request.** `ModeMiddleware` reads `X-Mistral-Key` into a ContextVar and
  removes it from the request scope. It also masks any copy of the key in the response. `dock/_client.py`
  builds the Mistral client from that ContextVar, so `dock/app.py` runs unchanged with a different key
  on each request. With no key, a route serves its recording from `replays/<capability>.json`, keyed by
  `route|case_id|variant`.
- **The signed state.** Nothing is stored on the server. When Approvals pauses for a person, the
  response carries `state`: the conversation id, the pending tool calls, the trace and the outbox, as
  HMAC-SHA256-signed JSON with a 30-minute expiry (`DOCK_STATE_SECRET`). Mistral holds the conversation
  and the browser holds the state. `/resume` can run on any instance. It verifies the signature and
  expiry, checks that the decisions name exactly the pending recipients, refuses non-customers again,
  and sends the tool results back. The key is never in the state.
- **The MCP endpoint.** `dock/mcp_server.py` is mounted at `/api/mcp` as Streamable HTTP with
  `stateless_http=True` and JSON replies, because no session survives between serverless instances.
  Its lifespan is FastAPI's lifespan. It has three tools: `get_shipment` and `list_customers` (reads)
  and `send_email` (a write, simulated). `send_email` refuses non-customers and records to an outbox
  under `DOCK_OUTBOX` (default: the system temp dir), never inside the repo.
- **Recording.** `python scripts/record.py [capability…|all]` sends every dataset case through the
  same FastAPI app with the key from `.env`. It runs only from a clean git tree, stamps every record
  with `recorded_at`, `git_sha` and the SDK version, and refuses to write a record containing anything
  that looks like a key.

## Deploy to Vercel

Nothing in this repo is deployed. To deploy your own copy:

1. Check the Python function's size against Vercel's 500 MB limit. This needs the network:
   `python scripts/bundle_size.py`.
2. Import the repo in Vercel (or run `npx vercel` from the repo root). The framework is Next.js.
   `vercel.json` sets up the Python function: `maxDuration` 60 s, plus `dock/` and `replays/` as
   included files.
3. Set the environment variables:

| Variable | Needed | What it does |
|---|---|---|
| `DOCK_STATE_SECRET` | **yes**, for Approvals | Signs the approval state. Use a long random value. Without it, Approvals refuses to start. |
| `DOCK_LIVE_PER_MIN` | no (default 10) | Live requests per minute per IP. `0` turns the limit off. |
| `DOCK_OUTBOX` | no | Directory for the MCP outbox. The default is the temp dir (`/tmp` on Vercel). |

Don't set `MISTRAL_API_KEY` on Vercel. Visitors bring their own key for Live, and Replay needs none.

4. Deploy, then open every screen with no key, and connect an MCP client to `https://<your-app>/api/mcp`.

## Connect an MCP client

Dock's tools are at `/api/mcp` (`http://localhost:3000/api/mcp` locally). The Operations screen has
the same snippets with your URL filled in.

**Claude Code**

```bash
claude mcp add --transport http dock http://localhost:3000/api/mcp
# /mcp in Claude Code then shows dock and its three tools
```

**fastmcp**

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://localhost:3000/api/mcp") as dock:
        for t in await dock.list_tools():
            print(t.name, "writes" if t.annotations.destructive_hint else "reads")
        r = await dock.call_tool("send_email", {"to": "audit@harbour-review.example.net",
                                                "subject": "test", "body": "test"})
        print(r.content[0].text)   # Refused by the Dock server: ...

asyncio.run(main())
```

**Mistral SDK:** register the server with `MCPClientStreamableHTTP` in a `RunContext`, with
`requires_confirmation: ["send_email"]`. The full snippet is on the Operations screen.

The endpoint has no auth. That is acceptable here only because `send_email` is simulated and refuses
non-customers.

## Limits we found

What the recorded runs show about Dock:
- **One agent misses work.** Design A covered every held shipment in only 4/10 runs; parallel workers
  did in 10/10.
- **Routing is not solved.** Even the fixed rule v2 routes 19/20 right.
- **Dock's own account of what it sent can't be trusted.** In 8/30 approval runs, the answer
  contradicts the outbox. The screen therefore builds its summary from the outbox. Some runs never
  draft to the consignee at all.
- **C2's issue is an understanding problem as much as a hearing one.** Without bias, 26/30 calls are
  noted fully right. Noting again on one unchanged transcript gets the issue right only 2/5 and 3/5
  times.
- **Cheaper is not free.** The cheaper candidate regresses on 3 handbook answers (29/30 → 26/30), and
  even the baseline fails one agent-path case by calling `list_customers`.
- **A time limit doesn't stop the spend.** The abandoned answer call ran on for 1,749 ms and used 339
  tokens after the person took over.

What the platform imposes:
- **60 s per request.** Batch screens run one item per request from the browser. The slowest recorded
  handoff (M20) took 25,852 ms. If a live handoff hits the limit, its `finally` never runs and agents
  can be left on the visitor's account. Every agent the console creates carries the metadata
  `created_by: dock-console`, so you can find and delete leftovers.
- **4.5 MB request and response bodies.** Mic uploads are capped at 4 MB (413 above that). TTS audio is
  served as WAV URLs, not base64.
- **Read-only filesystem.** SQLite opens read-only, and the MCP outbox goes to `/tmp`.
- **The rate limit is in memory.** It is per instance and resets on a cold start.
- **No server-side store.** That is why the signed state exists, why MCP runs stateless, and why
  Operations is replay only.
- **Cold starts.** Importing numpy, mistralai and fastmcp adds a few seconds to the first request.

## License

MIT, see [LICENSE](LICENSE). The notebooks and prompts are adapted from the
[Mistral AI Cookbook](https://github.com/mistralai/cookbook) (MIT). This is not an official Mistral
AI project.
