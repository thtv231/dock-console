"""Guardrails (S7): bad messages are blocked, normal mail gets through, and Dock's replies never guarantee a date."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BAD = json.loads((ROOT / "dock" / "bad.json").read_text(encoding="utf-8"))
INBOX = json.loads((ROOT / "dock" / "inbox.json").read_text(encoding="utf-8"))
GUARDRAILS = ROOT / "replays" / "guardrails.json"


@pytest.mark.parametrize("message", BAD, ids=lambda m: m["id"])
def test_bad_message_is_blocked(replayed, message):
    got = replayed("/api/guardrails/screen", {"message_id": message["id"]})
    assert got["block"] is True and got["reasons"], f"{message['kind']} let through: {message['text']}"


@pytest.mark.parametrize("mail", INBOX[:6], ids=lambda m: m["id"])
def test_normal_mail_is_let_through(replayed, mail):
    got = replayed("/api/guardrails/screen", {"message_id": mail["id"]})
    assert got["block"] is False and got["reasons"] == [], f"blocked for {got['reasons']}: {mail['subject']}"


def test_no_recorded_reply_guarantees_a_date():
    """Replies under Dock's own rule (the default variant). A recorded rule=plain run may guarantee one: that's the contrast."""
    assert GUARDRAILS.exists(), f"{GUARDRAILS.relative_to(ROOT)} not recorded yet (python scripts/record.py guardrails)"
    records = json.loads(GUARDRAILS.read_text(encoding="utf-8"))
    replies = {k: v for k, v in records.items() if k.startswith("reply|") and k.endswith("|")}
    assert replies, "no reply under the default rule is recorded"
    promised = [f"{k}: {v['quote']}" for k, v in replies.items() if v["guarantees_date"]]
    assert not promised, "\n".join(promised)
