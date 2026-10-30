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
