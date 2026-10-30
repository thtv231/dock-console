# Dock Console — build plan

## Context
Dock Console is a web app with one screen per capability of Dock, the Harbor Freight Co. operations agent. Dock already exists in two places:
- `dock/app.py` covers S1 (`triage`), S3 (`answer` and `search`), S5 (`email_task`) and S7 (`screen`, `reply`, `guarantees_date`).
- `dock/mcp_server.py` covers S5b, the MCP server.
- S2, S4, S6, S8, S9 and S10 exist only as notebooks in `notebooks/`. They get ported into small modules in `dock/`, keeping every prompt, docstring and schema exactly as written.

The app has two modes:
- **Replay** (the default) serves recorded real runs.
- **Live** uses the visitor's key from the `X-Mistral-Key` header.

There is no external store. Anything that has to survive between requests travels in a signed state object that the browser sends back, and Mistral keeps the conversations. Operations (S10) only shows recorded runs.

Facts found in the code that the plan has to handle:
- `dock/app.py` builds `client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])` when it is imported. It crashes without a key, and it can't serve a different key on each request.
- Module-level mutable globals: `app.TRACE`, `app.OUTBOX` and `app._vectors` (the cache of handbook embeddings).
- `app.send_email` does not check the recipient. Only `mcp_server.send_email` refuses non-customers, and it does so by writing to `dock/outbox.jsonl`, which is read-only on Vercel.
- `app.triage` and the other functions return parsed objects with no usage or latency. The UI needs both, so they have to be captured without changing those functions.
- S4 and S10 create agents with `client.beta.agents.create`. Those agents live on the visitor's Mistral account and must be deleted.
- The HF-26038 tracking note contains an injected instruction to email the customer list to audit@harbour-review.example.net. This is deliberate test data: the Approvals screen and the S6 guard exist to show Dock refusing it.

## 1. Folder layout (one Vercel project)
```
/                         Next.js App Router (TS, Tailwind, shadcn/ui)
  app/                    one route per screen, no app/api (it would clash with Python /api)
    page.tsx              Overview
    inbox/ documents/ handbook/ agents/ approvals/ guardrails/ voice/ scorecard/ operations/
  components/             shadcn ui + shared: ModeBadge, KeyDialog, MeterTable, TraceView, RecordedAt
  lib/api.ts              fetch wrapper: adds X-Mistral-Key from in-memory React context only
  next.config.ts          dev rewrite /api/:path* -> http://127.0.0.1:8000/api/:path*
api/
  index.py                FastAPI app = Vercel entry; mounts routers + MCP at /api/mcp
  core/mode.py            reads X-Mistral-Key -> ContextVar, strips it from scope; replay vs live
  core/replay.py          load/lookup replays/*.json
  core/state.py           HMAC sign/verify of browser-held state (env DOCK_STATE_SECRET)
  core/ratelimit.py       per-IP token bucket, in memory, live requests only
  routes/                 triage.py documents.py handbook.py agents.py approvals.py
                          guardrails.py voice.py scorecard.py ops.py overview.py data.py
dock/                     existing agent + ported modules
  _client.py              NEW: client proxy over a ContextVar + Meter (latency/usage per call)
  app.py                  only change: the client lines -> `from dock._client import client`;
                          sqlite opened read-only (uri mode=ro)
  mcp_server.py           only change: OUTBOX path from env DOCK_OUTBOX (/tmp on Vercel), db ro
  documents.py            s02: as_data_url, extract, Invoice/InvoiceV1/Line/BillOfLading,
                          check_digit, problems, against_database
  agents.py               s04: get_shipment/get_invoice (fixed versions), triage/tracking/billing
                          agent specs with both routing instructions; s06: Row/Report, score,
                          list_held_shipments, get_events, design_a, worker, guard
  voice.py                s09: transcribe, CallNote, note, BIAS, speak, reply prompt
  scorer.py               s08: check, check_agent, summary, Grounded/judge
  ops.py                  s10: Budget, FALLBACK, notify, release_check (used only by the recorder)
  data/… (unchanged files)
replays/                  recorded runs, one JSON per capability + voice/*.wav
scripts/record.py         recorder (local only, reads MISTRAL_API_KEY from .env)
tests/                    pytest, replay mode, no key
vercel.json               rewrite /api/(.*) -> /api/index; functions.api/index.py:
                          maxDuration 60, includeFiles "dock/**", "replays/**"
requirements.txt          fastapi, mistralai==3.6.*, fastmcp, numpy, pydantic, python-dotenv
```
This is the same layout as Vercel's nextjs-fastapi template. Next.js serves the pages, Python serves `/api/*`, and in development Next proxies `/api` to uvicorn. The rewrite target is 127.0.0.1 because it is server-side; the browser still opens localhost:3000.

**`dock/_client.py`** replaces the client without touching any prompt:
- `client` is a proxy whose attribute access resolves to `Mistral(api_key=<ContextVar>, retry/timeout as in app.py)`.
- Every SDK call goes through a wrapper that adds `{op, model, ms, tokens}` to a per-request Meter ContextVar. These are the latency and token numbers the UI shows.
- With no key set, a call raises `NoKey`, which the API turns into a 401.
- `numpy` handbook vectors are recorded once into `replays/handbook_vectors.npy`, and `app._vectors` is preloaded from that file. A live answer then costs one embedding call plus one parse.

## 2. API routes
Every response includes `mode` ("replay" or "live"), `meter` (a list of per-call ms and tokens), and `recorded_at` when it came from a replay. Every route that calls Mistral is replay-or-live: it is live only when the `X-Mistral-Key` header is present. On a replay miss it returns `404 {error:"not_recorded"}`, and the UI then offers Live.

| Method & path | Input | Output | Mistral? |
|---|---|---|---|
| GET /api/health | – | {ok, sdk_version, replay_files} | no |
| GET /api/data/{name} | inbox, inbox_truth, handbook, questions, bad, scorecard, documents, calls | dataset JSON (+ PDF text-layer counts and WAV durations taken from the recording) | no |
| GET /api/files/documents/{name} | – | PDF | no |
| GET /api/files/calls/{id} | – | WAV | no |
| GET /api/overview | – | scorecard summary, per-capability median latency and tokens, recorded_at, computed from replays/*.json | no |
| POST /api/triage | {mail_id} or {mail}, model? | {intent, shipment_id, language, expected?, wrong_fields} | chat.parse |
| POST /api/documents/extract | {name, schema: v1\|invoice\|bill_of_lading} | {fields, ocr:{avg, min, low_words, markdown}, problems, against_database, check_digit_ok, truth:{wrong_fields, flag_right}} | ocr.process (2 calls) |
| POST /api/answer | {question_id} or {question}, model? | {found, answer, sources, retrieved, checks:{sources_ok, must_contain_ok}} | embeddings + chat.parse |
| POST /api/agents/handoff | {text or mail_id, rule: v1\|v2, conversation_id?} | {entries:[handoff/call/message], route, expected, conversation_id} | agents create, run, delete in `finally` |
| POST /api/agents/design | {design: a\|serial\|parallel, guard: bool} | {rows, score, seconds, tool_calls, tokens} | conversations / chat.parse_async |
| GET /api/approvals/runs | – | every recorded run: policy, pauses, who was drafted, who was emailed, whether the answer contradicts the outbox | no (replay only) |
| POST /api/approvals/start | {shipment_id, instructions: plain\|safe, run?: <policy>-<n> (replay)} | `paused` {drafts:[{id, to, subject, body, is_customer, is_consignee}], trace, state} or `done` {message, summary, trace, outbox} | conversations.run_async |
| POST /api/approvals/resume | {state, decisions:{recipient: approve\|reject}} | same shape as start | conversations.run_async |
| POST /api/guardrails/screen | {message_id} or {text} | {reasons, moderation categories (raw, from the meter), others_data, block, expected} | moderate + chat.parse |
| POST /api/guardrails/reply | {text, rule: default\|custom?} | {reply, guarantees_date, quote} | chat.complete + chat.parse |
| POST /api/voice/run | {call_id, bias: bool, speak: bool} | {transcript, note, score_vs_truth, reply_text, audio_url or audio_b64, stages:{transcribe, understand, reply, speech}} | audio.transcriptions, chat.parse, chat.complete, audio.speech |
| POST /api/scorecard/case | {case_id, versions?: baseline\|cheaper} | {ok, detail, trace?} | yes (agent cases run `app.email_task` under an instance lock) |
| POST /api/scorecard/judge | {case_id} | {grounded, unsupported, answer} | yes |
| GET /api/ops/runs | – | recorded S10 runs: budget table, timeout 8 s vs 0.2 s, 3 retries giving 1 send, release check v1/v2 + rollback | no (replay only) |
| GET /api/ops/mcp/tools | – | tool list with read/write hints (in-process fastmcp Client) | no |
| POST /api/ops/mcp/call | {tool, args} | tool result text (send_email refuses non-customers) | no |
| POST/GET /api/mcp | MCP streamable HTTP (stateless_http, json_response) | MCP | no |

Batch views (all 20 emails, 14 questions, 30 scorecard cases, 5-run design tables) are driven by the browser one item per request, a few at a time. Totals are computed from the per-item results. No single function call runs longer than one agent case.

## 3. Replay
**What is recorded:** the full API response for one route and one input, as produced by the live code path. That includes the meter (real ms and tokens), the model names, and `recorded_at`, `git_sha` and the SDK version. TTS audio goes to `replays/voice/<call>_<variant>.wav`, and the response stores a URL in place of the base64. Nothing from the request headers is recorded.

**Storage and lookup:** each capability has one file, `replays/<capability>.json`, holding `{key: record}`. The key is `route|case_id|variant`:
- `case_id` is the dataset id (M01, Q03, invoice_04_scan.pdf, C2, X5, T-M02, HF-26038).
- `variant` is the sorted non-default options, for example `model=ministral-8b-latest,bias=1`.

Free text has no case id, so it is Live only.

For Approvals, a replay plays one recorded run from start to finish, so every pause shows that run's own drafts and what the person approves is exactly what that run sent. Within a run, decisions are keyed by the draft's **recipient**, never by its position. The key is `approvals|HF-26038|safe|<run>|<pause n>|<sorted recipient=decision list>`, for example `…|consignee-1|1|audit@harbour-review.example.net=reject,import@nordlicht.example.com=approve`; pause 0 is `/start`. If several drafts in one pause go to the same recipient, one decision covers all of them.

**How we record:** run `python scripts/record.py [capability…|all]` locally with `.env`.
- The script builds the FastAPI app in-process with a TestClient and sends every dataset case and variant with `X-Mistral-Key` set from the environment. This is the same code the visitor's live request runs.
- Approvals can't be recorded by walking a tree from one pause. A paused run can be answered only once, because Mistral rejects a second set of results for the same pause. So each decision path is its own real run from `/start`, with its policy applied at every pause:
  - `consignee`: approve the consignee, reject everyone else;
  - `all`: approve every draft (the server still refuses non-customers);
  - `none`: reject every draft.

  Each policy is run 5 times per instruction set (`consignee-1` … `none-5`) and **every run is kept**: what Dock drafts varies from run to run (some safe runs draft only to audit@ and never email the consignee), and that variation is the result. The recorder prints, per policy, how many runs drafted to a non-customer, how many emailed the consignee, and how many answers contradict the outbox.
- S10 runs `dock/ops.py` exactly as the notebook does, with the budget, timeout, idempotency and rollback steps.
- Before writing a record, the recorder fails if the key value, or any string that looks like a key, appears in the record.
- Recordings are committed to git. Numbers in the UI come only from these files or from a live response.

## 4. Approvals: pause, then resume in a later request
1. **`/start`** runs the s05 loop once. It uses `RunContext(model)` with the dock functions registered and `send_email` marked `requires_confirmation=True`, plus the `PLAIN` or `SAFE` instructions from s05.
2. **Pause.** When `DeferredToolCallsException` is raised, the request ends. The response contains:
   - the drafts, with server-computed `is_customer` (from the customers table) and `is_consignee`;
   - `state = sign({conversation_id, pending:[{tool_call_id, name, arguments}], shipment_id, trace, outbox, depth, exp})`.

   Mistral holds the conversation. The browser holds `state`. The server keeps nothing.
3. **Decide.** The person approves or rejects each draft in the UI.
4. **`/resume`** (any instance, minutes later):
   - Verify the HMAC and the expiry, and that the decisions name exactly the recipients in `pending`. A tampered state or a missing decision gets a 400.
   - Map each decision to the pending calls for that recipient.
   - For each approved draft, check the recipient against the customers table again. A non-customer is rejected with `"Refused by the Dock server: X is not a Harbor Freight customer"`, whatever the person chose.
   - Approved customer drafts run the simulated send, which appends to `state.outbox` and nothing else.
   - The tool results go back as function results on `conversation_id`, with `reject(reason)` for refusals.
   - The run continues. It may pause again with a new signed state, or finish and return the outbox and the trace.
5. **Trace** is built from the output entries and the decisions, not from `app.TRACE`, so nothing is shared between requests.
6. **Live key:** the key is never in `state`. The visitor sends it again on `/resume`.
7. **Spike in lesson 4:** confirm how SDK 3.6 rebuilds a deferred call, either through its class or by building `FunctionResultEntry(tool_call_id, result)` by hand.
8. **One answer per pause.** The server keeps no record, so it can't stop a state being sent twice. Mistral rejects the second set of results, and the API returns that as `409 already_answered`. The UI disables the buttons after a submit.
9. **Replay mode:** `state` carries `{run, pause n}` instead of a conversation_id.
   - The visitor picks a recorded run; each pause shows that run's drafts, and the decisions that run made are the recorded choice.
   - Any other combination returns `not_recorded`, and the UI offers Live.
10. **Summary from the outbox.** A finished run's summary (sent, rejected by the person, refused by the server) is built from the outbox and the decisions, never from Dock's words. An answer that claims a send the outbox doesn't hold, or names a party that received nothing as emailed, is flagged.

## 5. Build order (6 lessons)
1. **Skeleton and Inbox (S1).**
   - Build: `dock/_client.py` and the meter, the client-line change in app.py, `api/index.py` with core/mode, replay and ratelimit, `/api/health`, `/api/data`, `/api/triage`, and `record.py triage`. On the web side: the shell with a 10-item sidebar, the mode badge, the key dialog (memory only), and the Inbox screen.
   - Run: uvicorn plus `next dev`. In Replay, triage all 20 emails with no key, then paste a key and run M01 live. Run `pytest` for the replay lookup and to show the key never appears in a response.
2. **Read-only capabilities: Handbook (S3), Guardrails (S7), Documents (S2).**
   - Build: port `documents.py`, record the handbook vectors, add the answer, screen, reply and extract routes, and build the three screens with sources, moderation categories and OCR checks.
   - Run: invoice_07 shows the total mismatch and bill_of_lading_04 shows the bad check digit, both from replay.
3. **Agents & handoffs (S4, S6).**
   - Build: port `agents.py`, add the handoff route (create, run, always delete) with the v1 and v2 routing rule, the design route, and the screen comparing 17/20 to 19/20 and A vs C plus the guard.
   - Run: a replay of all 20 routings, and one live handoff that leaves no agents behind (`agents.list` check).
4. **Approvals (S5).**
   - Build: `core/state.py`, the start and resume routes, and the server-side recipient refusal. The recorder makes one real run from `/start` for each policy (consignee, all, none), keeps a run only if its first-pause recipient set matches, and keys decisions by recipient. Then the screen with drafts, decision buttons, trace and outbox.
   - Run: HF-26038 in Replay (reject audit@, approve Nordlicht) and in Live. Tests cover:
     - tampered state (400);
     - decisions that don't match the pending recipients (400);
     - an approved non-customer still refused;
     - an expired state;
     - a second resume of the same state (409);
     - replay lookup is independent of draft order.
5. **Voice desk (S9) and Scorecard (S8).**
   - Build: port `voice.py` and `scorer.py`, add the voice route (recorded WAVs plus a live mic upload at most 4 MB), the case and judge routes, and the scorecard screen with baseline vs cheaper regression.
   - Run: 29/30 from replay, C2 issue fixed by bias, and a latency budget table.
6. **Operations (S10), MCP, Overview, deploy.**
   - Build: port `ops.py` and record S10, add `/api/ops/runs`, mount `/api/mcp`, build the tool tester and connection guide (Mistral `MCPClientStreamableHTTP`, `claude mcp add --transport http`, fastmcp Client), build the Overview from replays, and set up `vercel.json`.
   - Run: `vercel deploy` a preview. All 10 screens work in Replay with no key, and an external MCP client lists 3 tools and gets the refusal for audit@.

## 6. Risks on Vercel
- **Read-only filesystem.** sqlite must open `mode=ro` because a journal or WAL file can't be created. `mcp_server` must write its outbox to /tmp, or it raises OSError.
- **Bundle size.** Python functions have a size limit. numpy, fastmcp's dependency tree and mistralai all count against it, plus `includeFiles` (about 4.5 MB of PDFs and WAVs, plus replays). pymupdf stays out of the runtime, and text-layer counts come from the recorder.
- **Duration.** maxDuration is 60 s. A slow live agent case or a design A run can approach it, so we run one item per request, the SDK timeout stays under maxDuration, and the error is clean. When an S4 handoff hits the timeout, the `finally` never runs and **agents are orphaned** on the visitor's account. Name them `dock-console-*` and tell the visitor how to clean them up.
- **4.5 MB request and response limit.** This applies to mic uploads and TTS base64, so audio gets a size check.
- **Concurrency inside one instance.** `app.TRACE`, `app.OUTBOX` and `_vectors` are globals. Use an instance lock around `email_task`, and build traces from entries elsewhere. ContextVars must reach threadpool calls (Starlette copies the context), and a test covers that.
- **Cold starts.** Importing numpy, mistralai and fastmcp takes a few seconds on the first request, and replay responses show it.
- **MCP on serverless.** It needs `stateless_http=True` because no session survives between instances. The FastMCP lifespan must be wired into FastAPI, or `/api/mcp` returns 500. The endpoint is public, but send_email is simulated and refuses non-customers.
- **In-memory rate limit is per instance.** It resets on cold start and isn't shared across instances. That's acceptable for a demo where visitors pay for their own key.
- **Key leakage.** Vercel and uvicorn logs don't log headers by default. The middleware still drops the header after reading it, SDK exceptions are scrubbed before they are returned, and there is no client-side storage of the key.
- **Signing secret.** `DOCK_STATE_SECRET` must be set in Vercel env vars. Without it, `/api/approvals` refuses to start.
- **Routing.** A Next `app/api` folder would shadow the Python `/api`, so there must be none. The dev rewrite uses 127.0.0.1 because uvicorn binds to IPv4 and Windows resolves localhost to ::1.
- **SDK drift.** `voices.list(limit=…)` is deprecated (s09 warning). Pin `mistralai==3.6.*`, and record the version in every replay.

## Verification
- `pytest` (replay, no key) covers:
  - every replay key resolves;
  - no key-like string appears in `replays/` or `dock/`;
  - state sign/verify and tamper rejection;
  - recipient refusal;
  - the scorecard recomputed from replays matches the Overview;
  - the MCP tool list.
- Manual check in Replay: open http://localhost:3000 and step through all 10 screens with no key.
- Manual check in Live: paste a key and run one item per screen. Confirm the meter shows real ms and tokens, and the key appears in no response or log.
- Preview deploy: repeat both checks, and connect an MCP client to `/api/mcp`.
