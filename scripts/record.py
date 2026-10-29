"""Record real runs into replays/<capability>.json, through the same API code a visitor's Live request runs.

    python scripts/record.py triage        # or: all

Reads MISTRAL_API_KEY from .env and sends it as X-Mistral-Key to the in-process app. The key is never
printed, and a record that contains it (or anything shaped like a key) is refused before it is written.
"""
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

from dotenv import dotenv_values               # noqa: E402
from fastapi.testclient import TestClient      # noqa: E402

from api.core import replay                    # noqa: E402
from api.index import app                      # noqa: E402

KEY_LIKE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{32}(?![A-Za-z0-9])")


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


CAPABILITIES = {"triage": record_triage}


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
