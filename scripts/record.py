"""Record real runs into replays/<capability>.json, through the same API code a visitor's Live request runs.

    python scripts/record.py triage        # or: handbook guardrails documents agents, or all
    python scripts/record.py designs       # only designs A and C, into replays/agents.json
    python scripts/record.py approvals     # HF-26038, plain and safe, one real run per decision policy

Every record names the commit that made it, so the tree must be clean: commit first, then record.

Reads MISTRAL_API_KEY from .env and sends it as X-Mistral-Key to the in-process app. The key is never
printed, and a record that contains it (or anything shaped like a key) is refused before it is written.
"""
import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DOCK_LIVE_PER_MIN"] = "0"   # the recorder is not a visitor: no rate limit

import numpy as np                             # noqa: E402
from dotenv import dotenv_values               # noqa: E402
from fastapi.testclient import TestClient      # noqa: E402

from api.core import replay, state             # noqa: E402
from api.index import app                      # noqa: E402
from api.routes import agents, handbook        # noqa: E402
from dock import hitl                          # noqa: E402
from dock import app as dock                   # noqa: E402
from dock._client import client, request_scope  # noqa: E402

# Records hold unsigned state payloads, so any secret signs the states the walk passes along: .env's, or a throwaway.
os.environ.setdefault("DOCK_STATE_SECRET", secrets.token_hex(32))

# 32 letters and digits on their own. Mistral's object ids (ag_<32 hex>) follow an underscore, so they pass.
KEY_LIKE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9]{32}(?![A-Za-z0-9])")


def git_sha(root):
    """HEAD's short sha, or None when the tree has changes no commit holds (or this isn't a git checkout)."""
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
    return sha if sha and status.returncode == 0 and not status.stdout.strip() else None


def require_clean(root):
    """The commit every record will name. A dirty tree is refused: its records would name code that no commit holds."""
    sha = git_sha(root)
    if sha is None:
        changes = subprocess.run(["git", "status", "--short"], cwd=root, capture_output=True, text=True).stdout
        raise SystemExit("refusing to record on a dirty tree: commit first, so every record names the code that "
                         "made it\n" + changes)
    return sha


def stamp(response, key):
    record = {k: v for k, v in response.items() if k != "mode"}
    record.update(recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  git_sha=GIT_SHA, sdk_version=version("mistralai"))
    text = json.dumps(record)
    if key in text or KEY_LIKE.search(text):
        raise SystemExit("refusing to write a record that contains a key-like string")
    return record


def record_triage(http, key):
    inbox = json.loads((ROOT / "dock" / "inbox.json").read_text(encoding="utf-8"))
    records, failed = {}, []
    for mail in inbox:
        r = http.post("/api/triage", json={"mail_id": mail["id"]}, headers={"X-Mistral-Key": key})
        if r.status_code != 200 or r.json().get("mode") != "live":
            failed.append(mail["id"])
            print(f"{mail['id']}  FAILED {r.status_code} {r.json().get('error')}")
            continue
        got = r.json()
        records[replay.key("triage", mail["id"])] = stamp(got, key)
        call = got["meter"][0]
        wrong = ",".join(got["wrong_fields"]) or "-"
        print(f"{mail['id']}  {got['intent']:<8} {str(got['shipment_id']):<9} {got['language']}  "
              f"wrong={wrong:<12} {call['ms']:>5} ms {call['tokens']:>4} tokens")
    return records, failed


def dataset(name):
    return json.loads((ROOT / "dock" / name).read_text(encoding="utf-8"))


def cost(got):
    pages = sum(c.get('pages') or 0 for c in got['meter'])
    return f"{sum(c['ms'] for c in got['meter']):>6} ms {sum(c['tokens'] or 0 for c in got['meter']):>5} tokens" + (f" {pages} pages" if pages else "")


def record_cases(http, key, path, cases, summary):
    """cases: [(record key, request body)]. Each is one live request; a failure is reported, never stored."""
    records, failed = {}, []
    for record_key, body in cases:
        r = http.post(path, json=body, headers={"X-Mistral-Key": key})
        if r.status_code != 200 or r.json().get("mode") != "live":
            failed.append(record_key)
            print(f"{record_key}  FAILED {r.status_code} {r.json().get('error')}")
            continue
        got = r.json()
        records[record_key] = stamp(got, key)
        print(f"{record_key:<44} {summary(got):<60} {cost(got)}")
    return records, failed


def record_vectors(key):
    """The handbook embeddings, once: every live answer then embeds only its question."""
    if handbook.VECTORS.exists():
        return

    async def embed():
        async with request_scope(key):
            return dock._embed([d["title"] + ". " + d["text"] for d in dock.HANDBOOK])

    vectors = asyncio.run(embed())
    np.save(handbook.VECTORS, vectors)
    dock._vectors = vectors
    print(f"wrote {len(vectors)} handbook vectors to {handbook.VECTORS.relative_to(ROOT)}")


def record_handbook(http, key):
    record_vectors(key)
    cases = [(replay.key("answer", q["id"]), {"question_id": q["id"]}) for q in dataset("questions.json")]
    return record_cases(http, key, "/api/answer", cases, lambda g: (
        f"found={g['found']!s:<5} sources={','.join(g['sources']) or '-':<6} retrieved={','.join(g['retrieved'])} "
        f"{'ok' if all(g['checks'].values()) else 'CHECK ' + ','.join(k for k, v in g['checks'].items() if not v)}"))


def record_guardrails(http, key):
    ids = [m["id"] for m in dataset("bad.json")] + [m["id"] for m in dataset("inbox.json")]
    screens, failed = record_cases(http, key, "/api/guardrails/screen",
                                   [(replay.key("screen", i), {"message_id": i}) for i in ids],
                                   lambda g: f"block={g['block']!s:<5} {'ok' if g['right'] else 'WRONG'} {g['reasons']}")
    cases = [(replay.key("reply", a["id"], **({"rule": rule} if rule != "dock" else {})), {"ask_id": a["id"], "rule": rule})
             for rule in ("dock", "plain") for a in dataset("asks.json")]
    replies, failed_replies = record_cases(http, key, "/api/guardrails/reply", cases,
                                           lambda g: f"guarantees_date={g['guarantees_date']}")
    return {**screens, **replies}, failed + failed_replies


def record_documents(http, key):
    truth = json.loads((ROOT / "dock" / "documents" / "truth.json").read_text(encoding="utf-8"))
    cases = [(replay.key("extract", name), {"name": name}) for name in truth]
    cases += [(replay.key("extract", name, schema="v1"), {"name": name, "schema": "v1"})
              for name, t in truth.items() if t["kind"] == "invoice"]
    return record_cases(http, key, "/api/documents/extract", cases, lambda g: (
        f"wrong={','.join(g['truth']['wrong_fields']) or '-':<14} flag_right={g['truth']['flag_right']!s:<5} "
        f"{g['problems'] or ''} {g['against_database'] or ''} digit_ok={g['check_digit_ok']}"))


def left_on_account(key):
    """Every agent on the account that carries the console's tag: there should be none after a recording."""
    async def tagged():
        found, page = [], 0
        async with request_scope(key):
            while True:
                batch = await client.beta.agents.list_async(page=page, page_size=100, metadata=agents.TAG)
                found += [a for a in batch if (a.metadata or {}).get("created_by") == agents.TAG["created_by"]]
                if len(batch) < 100:
                    return found
                page += 1
    return asyncio.run(tagged())


def record_designs(http, key, designs, serial=False):
    cases = [(replay.key("design", d, guard=1 if guard else None, run=n), {"design": d, "guard": guard, "run": n})
             for d in designs for guard in (False, True) for n in range(1, agents.RUNS + 1)]
    if serial:
        cases.append((replay.key("design", "serial", run=1), {"design": "serial", "run": 1}))
    return record_cases(http, key, "/api/agents/design", cases, lambda g: (
        f"{g['seconds']:>5} s {g['tool_calls']:>2} calls {g['score']['covered']} "
        f"invented={len(g['score']['invented reasons'])} note={g['score']['follows the note'] or '-'} "
        f"retries={sum(c.get('retries', 0) for c in g['meter'])} {sorted(c['ms'] for c in g['meter'])}"))


def record_designs_only(http, key):
    return record_designs(http, key, ("a", "parallel"))


def record_agents(http, key):
    inbox = dataset("inbox.json")
    cases = [(replay.key("handoff", m["id"], **({"rule": rule} if rule != agents.DEFAULT_RULE else {})),
              {"mail_id": m["id"], "rule": rule}) for rule in ("v1", "v2") for m in inbox]
    handoffs, failed = record_cases(http, key, "/api/agents/handoff", cases, lambda g: (
        f"{g['route']:<9} {'ok' if g['right'] else 'WRONG, expected ' + g['expected']:<26} "
        f"deleted {len(g['agents']['deleted'])}/{len(g['agents']['created'])}"))
    for rule in ("v1", "v2"):
        rows = [v for k, v in handoffs.items() if k.endswith(f"|rule={rule}" if rule != agents.DEFAULT_RULE else "|")]
        print(f"rule {rule}: routed correctly {sum(r['right'] for r in rows)} / {len(rows)}")
    designs, failed_designs = record_designs(http, key, ("a", "parallel"), serial=True)
    left = left_on_account(key)
    print(f"tagged agents left on the account: {len(left)}")
    for a in left:
        print(f"  {a.id} {a.name}")
    return {**handoffs, **designs}, failed + failed_designs + [f"left {a.id}" for a in left]


APPROVAL_POLICIES = {   # PLAN.md section 3: each decision path is its own real run from /start
    "consignee": lambda to, consignee: "approve" if to == consignee else "reject",
    "all": lambda to, consignee: "approve",   # the server still refuses non-customers
    "none": lambda to, consignee: "reject",
}
APPROVAL_RUNS = 5        # per policy and instruction set; every run is kept, what Dock drafts varies between runs
APPROVAL_ERROR_TRIES = 3  # a run that ends in an HTTP error is no result: try that slot again
APPROVAL_SHIPMENT = "HF-26038"


def walk(http, key, instructions, policy, run_id):
    """One real run from /start, answering every pause with one policy. [(record key, response)], or None."""
    consignee = hitl.consignee(APPROVAL_SHIPMENT)
    headers = {"X-Mistral-Key": key}
    r = http.post("/api/approvals/start", json={"shipment_id": APPROVAL_SHIPMENT, "instructions": instructions},
                  headers=headers)
    steps = [(replay.approvals_key(APPROVAL_SHIPMENT, instructions, run_id, 0, [], {}), r)]
    while r.status_code == 200 and r.json()["status"] == "paused":
        got = r.json()
        s = state.verify(got["state"])   # the server stops a run at MAX_PAUSES with a 422, reported below
        decisions = {d["to"]: APPROVAL_POLICIES[policy](d["to"], consignee) for d in got["drafts"]}
        r = http.post("/api/approvals/resume", json={"state": got["state"], "decisions": decisions}, headers=headers)
        steps.append((replay.approvals_key(APPROVAL_SHIPMENT, instructions, run_id, s["pause"], s["pending"],
                                           decisions), r))
    if r.status_code != 200 or r.json().get("mode") != "live":
        print(f"  {instructions} {run_id}: FAILED {r.status_code} {r.json().get('error')}")
        return None
    return [(k, resp.json()) for k, resp in steps]


def approval_record(got, key, run_id):
    """The live response as Replay serves it: no signed state (it would expire), the payload of the next one instead."""
    record = {k: v for k, v in got.items() if k != "state"}
    if got.get("state"):
        s = state.verify(got["state"])
        record["next"] = {**{k: s[k] for k in ("shipment_id", "instructions", "pause", "pending")}, "run": run_id}
    record["recorded_policy"] = run_id.rsplit("-", 1)[0]
    return stamp(record, key)


def record_approvals(http, key):
    records, failed = {}, []
    consignee, customers = hitl.consignee(APPROVAL_SHIPMENT), hitl.customers()
    for instructions in ("plain", "safe"):
        for policy in APPROVAL_POLICIES:
            for n in range(1, APPROVAL_RUNS + 1):
                run_id = f"{policy}-{n}"
                for _ in range(APPROVAL_ERROR_TRIES):
                    steps = walk(http, key, instructions, policy, run_id)
                    if steps is not None:
                        break
                else:
                    failed.append(f"{instructions}/{run_id}")
                    continue
                records.update({k: approval_record(got, key, run_id) for k, got in steps})
                last = steps[-1][1]
                drafted = [sorted({d["to"] for d in g["drafts"]}) for _, g in steps if g["status"] == "paused"]
                refusals = [t["to"] for t in last["trace"] if t.get("by") == "server"]
                flags = [c["to"] for c in last["summary"]["answer_contradicts"]]
                print(f"{instructions:<5} {run_id:<11} drafted {drafted} sent {[m['to'] for m in last['outbox']]}"
                      + (f" refused {refusals}" if refusals else "") + (f" ANSWER CLAIMS {flags}" if flags else "")
                      + f"  {cost(last)}")
    # The counts, from what was just recorded: what the screen's recorded-runs table shows.
    print()
    print(f"{'instructions':<12} {'policy':<10} {'drafted to a non-customer':>26} {'emailed the consignee':>22} "
          f"{'sent to a non-customer':>23} {'answer contradicts outbox':>26}")
    for instructions in ("plain", "safe"):
        for policy in APPROVAL_POLICIES:
            mine = [(k, v) for k, v in records.items() if k.split("|")[2] == instructions
                    and k.split("|")[3].rsplit("-", 1)[0] == policy]
            ends = {k.split("|")[3]: v for k, v in mine if v["status"] == "done"}
            drafted = {k.split("|")[3] for k, v in mine for d in v.get("drafts", []) if d["to"] not in customers}
            n = len(ends)
            print(f"{instructions:<12} {policy:<10} {len(drafted):>22} / {n} "
                  f"{sum(consignee in [m['to'] for m in v['outbox']] for v in ends.values()):>18} / {n} "
                  f"{sum(any(m['to'] not in customers for m in v['outbox']) for v in ends.values()):>19} / {n} "
                  f"{sum(bool(v['summary']['answer_contradicts']) for v in ends.values()):>22} / {n}")
    return records, failed


CAPABILITIES = {"triage": record_triage, "handbook": record_handbook, "guardrails": record_guardrails,
                "documents": record_documents, "agents": record_agents, "designs": record_designs_only,
                "approvals": record_approvals}
FILES = {"designs": "agents"}   # a part of a capability, saved into that capability's file
REPLACE = {"approvals": "approvals|"}   # re-recorded as a whole: old paths would mix with the new runs


def save(capability, records):
    path = replay.REPLAYS / f"{capability}.json"
    path.parent.mkdir(exist_ok=True)
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if capability in REPLACE:
        existing = {k: v for k, v in existing.items() if not k.startswith(REPLACE[capability])}
    existing.update(records)
    path.write_text(json.dumps(dict(sorted(existing.items())), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(records)} records to {path.relative_to(ROOT)} ({len(existing)} total)")


def main(names):
    global GIT_SHA
    GIT_SHA = require_clean(ROOT)   # before the key is read or anything is sent
    key = dotenv_values(ROOT / ".env").get("MISTRAL_API_KEY")
    if not key:
        raise SystemExit("MISTRAL_API_KEY is not set in .env")
    names = [n for n in CAPABILITIES if n not in FILES] if names in ([], ["all"]) else names
    unknown = [n for n in names if n not in CAPABILITIES]
    if unknown:
        raise SystemExit(f"unknown capability {unknown}; known: {sorted(CAPABILITIES)}")
    http, any_failed = TestClient(app), False
    for name in names:
        records, failed = CAPABILITIES[name](http, key)
        if records:
            save(FILES.get(name, name), records)
        if failed:
            any_failed = True
            print(f"{name}: {len(failed)} not recorded: {failed}")
    sys.exit(1 if any_failed else 0)


GIT_SHA = None   # set by main(), from a clean tree only

if __name__ == "__main__":
    main(sys.argv[1:])
