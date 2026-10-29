"""Approvals state (S5): the browser holds a signed state between /start and /resume, and the server keeps nothing.
A state that was changed, has run out, or is answered with the wrong recipients is refused before anything runs;
a state answered a second time is refused by Mistral, and the API says so as 409 already_answered."""
import base64
import json

import pytest

from api.core import state

RESUME = "/api/approvals/resume"
AUDIT = "audit@harbour-review.example.net"
NORDLICHT = "import@nordlicht.example.com"
LIVE = {"X-Mistral-Key": "dock-test-key-0000000000000000"}   # not a real key: it only switches a request to Live


def draft(tool_call_id, to):
    return {"tool_call_id": tool_call_id, "name": "send_email",
            "arguments": json.dumps({"to": to, "subject": "HF-26038 status", "body": "Your shipment is on hold."})}


PENDING = [draft("call_1", AUDIT), draft("call_2", NORDLICHT)]


def replay_state(**extra):
    """A first-pause state as Replay signs it: the recorded run is picked by the decisions, so no conversation_id."""
    return {"shipment_id": "HF-26038", "instructions": "safe", "pause": 1, "pending": PENDING,
            "trace": [], "outbox": [], **extra}


def live_state():
    return {"conversation_id": "conv_fake_0001", "shipment_id": "HF-26038", "instructions": "safe", "depth": 1,
            "pending": PENDING, "trace": [], "outbox": []}


BOTH = {AUDIT: "reject", NORDLICHT: "approve"}


def resume(client, token, decisions, headers=None):
    return client.post(RESUME, json={"state": token, "decisions": decisions}, headers=headers or {})


def test_sign_and_verify_round_trip():
    payload = replay_state()
    got = state.verify(state.sign(payload))
    assert {k: got[k] for k in payload} == payload


def flip(text, i):
    return text[:i] + ("A" if text[i] != "A" else "B") + text[i + 1:]


def tampered():
    """Three ways to change a state: a byte of the signature, a byte of the payload, and a payload swapped for
    another one that is valid JSON (audit@ approved in advance) under the old signature."""
    token = state.sign(replay_state())
    body, sig = token.rsplit(".", 1)
    forged = base64.urlsafe_b64encode(json.dumps(replay_state(pending=[draft("call_1", AUDIT)])).encode())
    return {"signature": f"{body}.{flip(sig, 3)}",
            "payload": f"{flip(body, 5)}.{sig}",
            "swapped": f"{forged.decode().rstrip('=')}.{sig}"}


@pytest.mark.parametrize("how", ["signature", "payload", "swapped"])
def test_tampered_state_is_400(client, how):
    r = resume(client, tampered()[how], BOTH)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "bad_state"


def test_garbage_state_is_400(client):
    r = resume(client, "not-a-state", BOTH)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "bad_state"


@pytest.mark.parametrize("decisions", [
    {NORDLICHT: "approve"},                                                        # audit@ left undecided
    {AUDIT: "reject", NORDLICHT: "approve", "info@harbor-freight.example": "approve"},  # a recipient not drafted
    {AUDIT: "reject", "import@nordlicht.example.org": "approve"},                  # close, but not the drafted one
    {},
], ids=["missing", "extra", "wrong", "empty"])
def test_decisions_must_name_exactly_the_pending_recipients(client, decisions):
    r = resume(client, state.sign(replay_state()), decisions)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "decisions_mismatch"


def test_a_decision_must_be_approve_or_reject(client):
    r = resume(client, state.sign(replay_state()), {AUDIT: "maybe", NORDLICHT: "approve"})
    assert r.status_code in (400, 422), r.text


def test_expired_state_is_400(client):
    r = resume(client, state.sign(replay_state(), ttl_s=-1), BOTH)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "expired"


def test_expiry_is_signed(client):
    """Pushing exp forward by hand breaks the signature, so a stale state can't be revived."""
    token = state.sign(replay_state(), ttl_s=-1)
    body, sig = token.rsplit(".", 1)
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["exp"] += 10 ** 6
    revived = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    r = resume(client, f"{revived}.{sig}", BOTH)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "bad_state"


def test_second_resume_of_the_same_state_is_409(client, fake_conversations):
    token = state.sign(live_state())
    first = resume(client, token, BOTH, headers=LIVE)
    assert first.status_code == 200, first.text
    second = resume(client, token, BOTH, headers=LIVE)
    assert second.status_code == 409, second.text
    assert second.json()["error"] == "already_answered"
    assert len(fake_conversations) == 2   # the second set of results did reach Mistral, and Mistral refused it


def test_no_secret_no_approvals(client, monkeypatch):
    """Without DOCK_STATE_SECRET the server can't sign or check a state, so it refuses rather than trust one."""
    token = state.sign(replay_state())
    monkeypatch.delenv("DOCK_STATE_SECRET")
    r = resume(client, token, BOTH)
    assert r.status_code == 503, r.text
    assert r.json()["error"] == "no_state_secret"
