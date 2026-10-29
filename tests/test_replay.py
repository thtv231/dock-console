"""Replay serves every recorded triage run, and says so plainly when a run was never recorded."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRIAGE = ROOT / "replays" / "triage.json"


def _recorded():
    assert TRIAGE.exists(), f"{TRIAGE.relative_to(ROOT)} not recorded yet (python scripts/record.py triage)"
    return json.loads(TRIAGE.read_text(encoding="utf-8"))


def _request(key):
    """triage|M01|model=ministral-8b-latest -> {"mail_id": "M01", "model": "ministral-8b-latest"}"""
    route, case_id, variant = key.split("|")
    assert route == "triage", key
    body = {"mail_id": case_id}
    for option in filter(None, variant.split(",")):
        name, value = option.split("=", 1)
        body[name] = value
    return body


def test_every_triage_key_resolves(client):
    records = _recorded()
    assert records, "replays/triage.json is empty"
    wrong = []
    for key, record in records.items():
        r = client.post("/api/triage", json=_request(key))
        if r.status_code != 200:
            wrong.append(f"{key}: {r.status_code} {r.text[:120]}")
            continue
        got = r.json()
        if got.get("mode") != "replay":
            wrong.append(f"{key}: mode={got.get('mode')!r}")
        diff = sorted(f for f in record if f != "mode" and got.get(f) != record[f])
        if diff:
            wrong.append(f"{key}: differs from the recording in {diff}")
    assert not wrong, "\n".join(wrong)


def test_every_inbox_mail_is_recorded():
    inbox = json.loads((ROOT / "dock" / "inbox.json").read_text(encoding="utf-8"))
    missing = [m["id"] for m in inbox if f"triage|{m['id']}|" not in _recorded()]
    assert not missing, missing


def test_unknown_mail_id_is_not_recorded(client):
    r = client.post("/api/triage", json={"mail_id": "M999"})
    assert r.status_code == 404, r.text
    assert r.json().get("error") == "not_recorded", r.json()
