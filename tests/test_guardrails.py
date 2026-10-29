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
    promised = [f"{k}: {v['reply'][:120]}" for k, v in replies.items() if v["guarantees_date"]]
    assert not promised, "\n".join(promised)


def test_a_failed_reply_shows_the_judges_quote():
    """Every recorded reply that guarantees a date carries the sentence that does it, found in the reply itself."""
    records = json.loads(GUARDRAILS.read_text(encoding="utf-8"))
    failed = {k: v for k, v in records.items() if k.startswith("reply|") and v["guarantees_date"]}
    assert failed, "no recorded reply guarantees a date, so the quote is never shown (the rule=plain runs should)"
    for k, v in failed.items():
        assert v["judge"]["quote"].strip() and v["judge"]["quote"].strip() in v["reply"], k


def test_pii_is_scored_but_never_a_reason(replayed):
    got = replayed("/api/guardrails/screen", {"message_id": "M01"})
    assert "pii" in got["categories"] and "pii" in got["ignored"] and "pii" not in got["reasons"]
