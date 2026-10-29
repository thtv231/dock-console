"""Agents & handoffs (S4): every inbox email is routed under both rules, the fixed rule matches the answer key,
and a recorded handoff leaves no agent behind on the account it ran on."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INBOX = json.loads((ROOT / "dock" / "inbox.json").read_text(encoding="utf-8"))
TRUTH = json.loads((ROOT / "dock" / "inbox_truth.json").read_text(encoding="utf-8"))
AGENTS = ROOT / "replays" / "agents.json"
ROUTE = {"tracking": "tracking", "invoice": "billing"}   # s04 step 6: everything else stays with triage
RULES = ["v1", "v2"]


def expected(mail_id):
    return ROUTE.get(TRUTH[mail_id]["intent"], "triage")


def handoff(replayed, mail_id, rule):
    return replayed("/api/agents/handoff", {"mail_id": mail_id, "rule": rule})


@pytest.mark.parametrize("rule", RULES)
@pytest.mark.parametrize("mail", INBOX, ids=lambda m: m["id"])
def test_every_mail_has_a_routing(replayed, mail, rule):
    got = handoff(replayed, mail["id"], rule)
    assert got["rule"] == rule and got["route"] in {"tracking", "billing", "triage"}, got.get("route")
    handoffs = [e["to"] for e in got["entries"] if e["type"] == "handoff"]
    assert got["route"] == (handoffs[-1] if handoffs else "triage"), f"route {got['route']} but entries say {handoffs}"


def test_v2_routes_19_of_20_to_the_answer_keys_specialist(replayed):
    routes = {m["id"]: handoff(replayed, m["id"], "v2")["route"] for m in INBOX}
    wrong = {mid: f"went to {got}, expected {expected(mid)}" for mid, got in routes.items() if got != expected(mid)}
    assert len(INBOX) - len(wrong) >= 19, wrong


def test_recorded_handoff_deletes_every_agent_it_created():
    assert AGENTS.exists(), f"{AGENTS.relative_to(ROOT)} not recorded yet (python scripts/record.py agents)"
    records = {k: v for k, v in json.loads(AGENTS.read_text(encoding="utf-8")).items() if k.startswith("handoff|")}
    assert records, "no handoff is recorded"
    left = []
    for k, record in records.items():
        created = record["agents"]["created"]
        assert len(created) == 3 and all(a["id"] and a["name"] for a in created), f"{k}: created {created}"
        missing = {a["id"] for a in created} - set(record["agents"]["deleted"])
        if missing:
            left.append(f"{k}: never deleted {sorted(missing)}")
    assert not left, "\n".join(left)
