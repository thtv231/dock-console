"""Scorecard (S8): 30 cases check Dock's results and the path its agent took. The recorded baseline scores 29/30,
a cheaper candidate is scored the same way, and every regression it brings is named case by case."""
import json
from collections import Counter
from pathlib import Path

import pytest

from api.core import replay

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "dock" / "scorecard.json").read_text(encoding="utf-8"))
SCORECARD = ROOT / "replays" / "scorecard.json"
VERSIONS = ["baseline", "cheaper"]   # s08 step 7: cheaper = ministral-8b-latest for answer and triage
JUDGED = [c for c in CASES if c["kind"] == "answer" and c["expect"]["sources"]]   # s08 step 6 judges found answers


def recorded():
    assert SCORECARD.exists(), f"{SCORECARD.relative_to(ROOT)} not recorded yet (python scripts/record.py scorecard)"
    return json.loads(SCORECARD.read_text(encoding="utf-8"))


def case_key(case_id, version):
    return replay.key("case", case_id, versions=None if version == "baseline" else version)


def results(version):
    """{case_id: (ok, detail)} as recorded, in scorecard.json order."""
    records = recorded()
    missing = [c["id"] for c in CASES if case_key(c["id"], version) not in records]
    assert not missing, f"{version}: not recorded {missing}"
    return {c["id"]: (records[case_key(c["id"], version)]["ok"], records[case_key(c["id"], version)]["detail"])
            for c in CASES}


def summary(got):
    """s08 step 5's summary, recomputed from the recorded results."""
    total, passed = Counter(c["kind"] for c in CASES), Counter()
    for c in CASES:
        passed[c["kind"]] += got[c["id"]][0]
    return {"passed": sum(ok for ok, _ in got.values()),
            "per_kind": {k: f"{passed[k]}/{total[k]}" for k in total},
            "failures": [{"case_id": cid, "detail": detail} for cid, (ok, detail) in got.items() if not ok]}


def regressions(baseline, candidate):
    return [{"case_id": cid, "detail": candidate[cid][1]} for cid in baseline if baseline[cid][0] and not candidate[cid][0]]


def test_the_fixtures_are_what_the_tests_assume():
    assert len(CASES) == 30 and len({c["id"] for c in CASES}) == 30
    assert "G-HF-26038" in {c["id"] for c in CASES if c["kind"] == "agent"}


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_every_case_is_replayed(replayed, case, version):
    body = {"case_id": case["id"]} if version == "baseline" else {"case_id": case["id"], "versions": version}
    got = replayed("/api/scorecard/case", body)
    assert got["case_id"] == case["id"] and isinstance(got["ok"], bool) and isinstance(got["detail"], str), got
    assert got["ok"] or got["detail"], f"{case['id']} {version}: failed with no detail"


def test_baseline_scores_29_of_30_and_fails_only_on_list_customers():
    got = summary(results("baseline"))
    assert got["passed"] == 29, got
    assert got["failures"] == [{"case_id": "G-HF-26038", "detail": "called list_customers"}], got["failures"]


def test_the_failing_agent_case_shows_the_path_it_took():
    """The path check is the point of s08 step 4: the recorded trace itself holds the call the case forbids."""
    record = recorded()[case_key("G-HF-26038", "baseline")]
    assert "list_customers" in record["trace"], record.get("trace")


def test_cheaper_candidate_lists_its_regressions_case_by_case(client):
    baseline, cheaper = results("baseline"), results("cheaper")
    expected = regressions(baseline, cheaper)
    assert expected, "the cheaper candidate regresses on no case, so the screen has nothing to catch"
    assert all(r["detail"] for r in expected), expected
    r = client.get("/api/overview")
    assert r.status_code == 200, r.text[:200]
    assert r.json()["scorecard"]["regressions"] == expected


def test_overview_reports_the_scorecard_recomputed_from_replays(client):
    baseline, cheaper = results("baseline"), results("cheaper")
    r = client.get("/api/overview")
    assert r.status_code == 200, r.text[:200]
    assert r.json()["scorecard"] == {"total": len(CASES), "baseline": summary(baseline), "cheaper": summary(cheaper),
                                     "regressions": regressions(baseline, cheaper)}


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("case", JUDGED, ids=lambda c: c["id"])
def test_every_judge_verdict_quotes_the_answer_it_judged(replayed, case, version):
    """Both versions' answers are judged, and each verdict says whose answer it judged."""
    assert case_key(case["id"], version).replace("case|", "judge|", 1) in recorded()
    got = replayed("/api/scorecard/judge", {"case_id": case["id"], "versions": version})
    assert got["versions"] == version, f"{case['id']}: verdict for {got.get('versions')!r}, asked for {version}"
    assert got["answer"].strip(), f"{case['id']}: the verdict carries no answer"
    unsupported = got["unsupported"].strip()
    if not got["grounded"]:   # s08 step 6: an ungrounded verdict counts only with a phrase found in the answer
        assert unsupported and unsupported in got["answer"], \
            f"{case['id']}: ungrounded on {unsupported!r}, which is not in {got['answer'][:120]!r}"
