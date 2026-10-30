"""Overview: every screen of the sidebar, each with the median latency and tokens of one request, recomputed here
from the replay files it is built from. Every record it counts names a commit on this branch's history: the recorder
only records from a clean tree, so that commit holds the code that made the record."""
import json
import re
import subprocess
from functools import lru_cache
from statistics import median
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPLAYS = ROOT / "replays"
HREFS = re.findall(r'\{ href: "([^"]+)"', (ROOT / "lib" / "screens.ts").read_text(encoding="utf-8"))
REPLAYS_OF = {"/": [], "/inbox": ["triage"], "/documents": ["documents"], "/handbook": ["handbook"],
              "/agents": ["agents"], "/approvals": ["approvals"], "/guardrails": ["guardrails"],
              "/voice": ["voice"], "/scorecard": ["scorecard"], "/operations": ["ops"]}


@lru_cache
def records(name):
    path = REPLAYS / f"{name}.json"
    assert path.exists(), f"{path.relative_to(ROOT)} not recorded yet (python scripts/record.py {name})"
    return list(json.loads(path.read_text(encoding="utf-8")).values())


def expected(names):
    """One request is one record with a meter: its latency is the sum of its calls' ms, its tokens the sum of the
    tokens its calls report (OCR reports pages, not tokens)."""
    every = [r for name in names for r in records(name)]
    requests = [r for r in every if r.get("meter")]
    ms = [sum(c["ms"] for c in r["meter"]) for r in requests]
    tokens = [sum(c["tokens"] for c in r["meter"] if c["tokens"] is not None) for r in requests
              if any(c["tokens"] is not None for c in r["meter"])]
    return {"replays": names, "requests": len(requests),
            "median_ms": median(ms) if ms else None, "median_tokens": median(tokens) if tokens else None,
            "recorded_at": max((r["recorded_at"] for r in every), default=None),
            "git_shas": sorted({r["git_sha"] for r in every})}


@pytest.fixture(scope="module")
def overview():
    from fastapi.testclient import TestClient
    from api.index import app
    r = TestClient(app).get("/api/overview")
    assert r.status_code == 200, f"/api/overview: {r.status_code} {r.text[:200]}"
    return r.json()


def test_the_fixtures_are_what_the_tests_assume():
    assert len(HREFS) == 10 and set(HREFS) == set(REPLAYS_OF), HREFS
    used = {n for names in REPLAYS_OF.values() for n in names}
    assert {p.stem for p in REPLAYS.glob("*.json")} <= used, "a replay file that no screen shows"


def test_overview_covers_all_10_screens_in_sidebar_order(overview):
    assert overview["mode"] == "replay"
    assert [s["href"] for s in overview["screens"]] == HREFS


@pytest.mark.parametrize("href", [h for h in REPLAYS_OF if REPLAYS_OF[h]])
def test_median_latency_and_tokens_are_computed_from_the_replays(overview, href):
    screen = next((s for s in overview["screens"] if s["href"] == href), None)
    assert screen is not None, f"{href} is missing from the overview"
    want = expected(REPLAYS_OF[href])
    assert want["requests"] and want["median_ms"], f"{href}: no recorded request with a meter"
    assert {k: screen.get(k) for k in want} == want


def test_the_scorecard_is_computed_from_the_replays(overview):
    baseline = {k: r for k, r in json.loads((REPLAYS / "scorecard.json").read_text(encoding="utf-8")).items()
                if k.startswith("case|") and k.endswith("|")}
    assert baseline, "replays/scorecard.json holds no baseline case"
    got = overview["scorecard"]
    assert got["total"] == len(baseline)
    assert got["baseline"]["passed"] == sum(r["ok"] for r in baseline.values())


def on_this_branch(sha):
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=ROOT,
                          capture_output=True).returncode == 0


@pytest.mark.parametrize("name", sorted({n for names in REPLAYS_OF.values() for n in names}))
def test_every_replay_the_overview_uses_comes_from_a_clean_commit(name):
    """git_sha is None when the recorder ran on a dirty tree (scripts/record.py git_sha); it must also be a commit
    this branch holds, or nobody can check out the code that made the record."""
    shas = {r.get("git_sha") for r in records(name)}
    assert None not in shas, f"replays/{name}.json has records with no git_sha"
    missing = sorted(sha for sha in shas if not on_this_branch(sha))
    assert not missing, f"replays/{name}.json names commits not on this branch: {missing}"


def scores(overview, href):
    return {s["label"]: s for s in next(s for s in overview["screens"] if s["href"] == href)["scores"]}


@pytest.mark.parametrize("href", [h for h in REPLAYS_OF if REPLAYS_OF[h]])
def test_every_screen_card_has_a_score(overview, href):
    got = scores(overview, href)
    assert got, f"{href}: no score"
    for s in got.values():
        assert "n" not in s or (0 <= s["n"] <= s["of"] and s["value"] == f"{s['n']}/{s['of']}"), s


def test_scores_are_recounted_from_the_replays(overview):
    triage = records("triage")
    assert scores(overview, "/inbox")["Emails with intent, shipment and language right"]["value"] == \
        f"{sum(not r['wrong_fields'] for r in triage)}/{len(triage)}"
    routed = [r for k, r in json.loads((REPLAYS / "agents.json").read_text(encoding="utf-8")).items()
              if k.startswith("handoff|") and k.endswith("|rule=v1")]
    assert scores(overview, "/agents")["Routed right, rule v1"]["value"] == \
        f"{sum(r['right'] for r in routed)}/{len(routed)}"
    budget = [r for r in records("ops") if r.get("status") == 429 and "tokens_per_day" in r]
    asked = [r for r in records("ops") if "tokens_per_day" in r]
    assert scores(overview, "/operations")["Requests refused by the budget"]["value"] == f"{len(budget)}/{len(asked)}"
    agent = next(r for r in records("ops") if "checks" in r)
    assert scores(overview, "/operations")["Prod after the rollback"]["value"] == f"version {agent['prod']}"


# ---------- Each card's sentence says only what its numbers show ----------
PROVES = dict(re.findall(r'\{ href: "([^"]+)",.*?proves: "([^"]*)"', (ROOT / "lib" / "screens.ts").read_text(encoding="utf-8")))
# Words that claim a cause or a comparison. A card counts; it doesn't say what made the counts differ.
CLAIMS = re.compile(r"\b(fix\w*|improv\w*|help\w*|better|worse|more|fewer|less|clearer|correct(s|ed|ing)|reduc\w*|prevent\w*|"
                    r"stop\w*|catch\w*|because|caus\w*|so that|ensur\w*|mishear\w*|hears?)\b", re.I)


@pytest.mark.parametrize("href", [h for h in REPLAYS_OF if REPLAYS_OF[h]])
def test_no_card_claims_a_cause(href):
    sentence = PROVES.get(href, "")
    assert sentence, f"{href} has no sentence"
    assert not CLAIMS.search(sentence), f"{href}: {CLAIMS.search(sentence).group(0)!r} in {sentence!r}"


def test_the_voice_card_makes_no_claim_its_counts_do_not_support(client, overview):
    """Lesson 5: C2's transcript was noted again 5 times per source, so that a miss can be put on hearing or on
    understanding. Bias can be credited with a change only if understanding is stable on fixed words (every re-note of
    one transcript gives the same issue). The recordings don't show that, so the card must not claim it, and must show
    the re-note counts that say so."""
    notes = client.get("/api/voice/runs").json()["notes"]
    assert notes, "no call was noted again on a fixed transcript"
    stable = all(len(n["issues"]) == 1 for sources in notes.values() for n in sources.values())
    sentence = PROVES["/voice"]
    if not stable:
        assert not CLAIMS.search(sentence) and not re.search(r"bias (\w+s|is|was|made)\b", sentence), sentence
    shown = scores(overview, "/voice")
    for call_id, sources in notes.items():
        for source, label in (("nobias", "no bias"), ("bias", "with bias")):
            got = shown[f"{call_id} noted again on one transcript, issue right, {label}"]
            assert got["value"] == f"{sources[source]['right']['issue']}/{sources[source]['runs']}", got
