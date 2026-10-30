"""Operations (S10): recorded runs of dock/ops.py, as the notebook ran them. A token budget refuses a request before
the call that would overrun it, an answer that takes too long is handed to a person, a retried email is sent once,
and a release that guarantees dates is rolled back in one line. The screen only shows recorded runs."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OPS = ROOT / "replays" / "ops.json"
SECTIONS = ["budget", "timeout", "idempotency", "release"]
FALLBACK = {"status": "queued", "message": "A person from operations will reply."}   # s10 step 3
LIVE = {"X-Mistral-Key": "dock-test-key-0000000000000000"}   # not a real key: it only switches a request to Live


@pytest.fixture(scope="module")
def runs():
    assert OPS.exists(), f"{OPS.relative_to(ROOT)} not recorded yet (python scripts/record.py ops)"
    from fastapi.testclient import TestClient
    from api.index import app
    r = TestClient(app).get("/api/ops/runs")
    assert r.status_code == 200, f"/api/ops/runs: {r.status_code} {r.text[:200]}"
    return r.json()


def test_every_step_is_a_recorded_run(runs):
    assert runs["mode"] == "replay"
    for name in SECTIONS:
        assert runs[name]["recorded_at"] and runs[name]["git_sha"], f"{name}: no recorded_at or git_sha"


def test_a_live_key_still_gets_the_recorded_runs(client):
    """S10 creates agents and spends budget on the visitor's account: the screen never runs it live."""
    r = client.get("/api/ops/runs", headers=LIVE)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["mode"] == "replay"


# ---------- Step 2: a token budget ----------

def test_the_budget_refuses_m03_with_429_after_190_tokens(runs):
    budget = runs["budget"]
    requests = budget["requests"]
    assert [q["mail_id"] for q in requests] == ["M01", "M02", "M03", "M04", "M05"]
    assert [q["status"] for q in requests] == [200, 200, 429, 429, 429], requests
    spent = sum(q["tokens"] for q in requests[:2])
    assert spent == 190, f"M01 and M02 spent {spent} tokens"
    for q in requests[2:]:
        assert q["used"] == 190 and q["tokens"] is None, q   # refused before the call: nothing more is spent
        assert q["detail"] == f"daily budget of {budget['tokens_per_day']} tokens reached", q


def test_each_refusal_follows_the_budget_rule(runs):
    """Budget.check: refuse when what is used plus the reserve would pass the daily limit."""
    budget, used = runs["budget"], 0
    for q in budget["requests"]:
        refused = used + budget["reserve"] > budget["tokens_per_day"]
        assert (q["status"] == 429) == refused, f"{q['mail_id']}: used {used}, status {q['status']}"
        used += q["tokens"] or 0
        assert q["used"] == used, q


# ---------- Step 3: answer in time, or hand over ----------

def test_8_seconds_answers_four_days_from_hb07(runs):
    limits = {run["seconds"]: run for run in runs["timeout"]["limits"]}
    assert set(limits) == {8.0, 0.2}, list(limits)
    answered = limits[8.0]
    assert answered["status"] == "answered", answered
    assert answered["answer"] == "Four days" and answered["sources"] == ["HB-07"], answered


def test_a_fifth_of_a_second_hands_over_to_a_person(runs):
    limits = {run["seconds"]: run for run in runs["timeout"]["limits"]}
    handed = limits[0.2]
    assert {k: handed[k] for k in FALLBACK} == FALLBACK, handed
    assert "answer" not in handed or handed["answer"] is None, handed


# ---------- Step 4: never send the same email twice ----------

def test_three_retries_send_the_email_once(runs):
    idem = runs["idempotency"]
    attempts = idem["attempts"]
    assert len(attempts) == 3, attempts
    assert [a.get("repeated", False) for a in attempts] == [False, True, True], attempts
    assert all(a["status"] == "sent" for a in attempts), attempts
    assert len(idem["outbox"]) == 1, idem["outbox"]
    assert idem["outbox"][0]["to"] == attempts[0]["to"]


# ---------- Steps 5 and 6: versions, release check, rollback ----------

def guaranteed(check):
    return sum(reply["guarantees_date"] for reply in check["replies"])


def test_version_1_guarantees_a_date_in_3_of_3_and_version_0_in_0_of_3(runs):
    release = runs["release"]
    checks = {c["version"]: c for c in release["checks"]}
    assert set(checks) == {0, 1}, list(checks)
    assert len(release["asks"]) == 3
    for check in checks.values():
        assert [r["ask"] for r in check["replies"]] == release["asks"], check["version"]
        assert all(r["reply"].strip() for r in check["replies"]), check["version"]
    assert (guaranteed(checks[1]), guaranteed(checks[0])) == (3, 0)


def test_the_rollback_puts_prod_back_on_0(runs):
    release = runs["release"]
    assert [c["version"] for c in release["checks"]] == [1, 0], "release v1 first, then roll back to v0"
    assert release["prod"] == 0, f"prod ends on version {release['prod']}"
    assert guaranteed(release["checks"][-1]) == 0
