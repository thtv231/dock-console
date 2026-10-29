"""Record real runs into replays/<capability>.json, through the same API code a visitor's Live request runs.

    python scripts/record.py triage        # or: handbook guardrails documents agents, or all

Reads MISTRAL_API_KEY from .env and sends it as X-Mistral-Key to the in-process app. The key is never
printed, and a record that contains it (or anything shaped like a key) is refused before it is written.
"""
import asyncio
import json
import os
import re
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

from api.core import replay                    # noqa: E402
from api.index import app                      # noqa: E402
from api.routes import agents, handbook        # noqa: E402
from dock import app as dock                   # noqa: E402
from dock._client import client, request_scope  # noqa: E402

# 32 letters and digits on their own. Mistral's object ids (ag_<32 hex>) follow an underscore, so they pass.
KEY_LIKE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9]{32}(?![A-Za-z0-9])")


def git_sha():
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return sha + ("-dirty" if dirty else "")


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
    cases = [(replay.key("design", d, guard=1 if guard else None, run=n), {"design": d, "guard": guard, "run": n})
             for d in ("a", "parallel") for guard in (False, True) for n in range(1, agents.RUNS + 1)]
    cases.append((replay.key("design", "serial", run=1), {"design": "serial", "run": 1}))
    designs, failed_designs = record_cases(http, key, "/api/agents/design", cases, lambda g: (
        f"{g['seconds']:>5} s {g['tool_calls']:>2} calls {g['score']['covered']} "
        f"invented={len(g['score']['invented reasons'])} note={g['score']['follows the note'] or '-'}"))
    left = left_on_account(key)
    print(f"tagged agents left on the account: {len(left)}" + "".join(f"
  {a.id} {a.name}" for a in left))
    return {**handoffs, **designs}, failed + failed_designs + [f"left {a.id}" for a in left]


CAPABILITIES = {"triage": record_triage, "handbook": record_handbook, "guardrails": record_guardrails,
                "documents": record_documents, "agents": record_agents}


def save(capability, records):
    path = replay.REPLAYS / f"{capability}.json"
    path.parent.mkdir(exist_ok=True)
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    existing.update(records)
    path.write_text(json.dumps(dict(sorted(existing.items())), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(records)} records to {path.relative_to(ROOT)} ({len(existing)} total)")


def main(names):
    key = dotenv_values(ROOT / ".env").get("MISTRAL_API_KEY")
    if not key:
        raise SystemExit("MISTRAL_API_KEY is not set in .env")
    names = list(CAPABILITIES) if names in ([], ["all"]) else names
    unknown = [n for n in names if n not in CAPABILITIES]
    if unknown:
        raise SystemExit(f"unknown capability {unknown}; known: {sorted(CAPABILITIES)}")
    http, any_failed = TestClient(app), False
    for name in names:
        records, failed = CAPABILITIES[name](http, key)
        if records:
            save(name, records)
        if failed:
            any_failed = True
            print(f"{name}: {len(failed)} not recorded: {failed}")
    sys.exit(1 if any_failed else 0)


GIT_SHA = git_sha()

if __name__ == "__main__":
    main(sys.argv[1:])
