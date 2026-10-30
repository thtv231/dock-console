"""GET /api/overview: numbers computed from replays/*.json only, never typed in. So far: the S8 scorecard."""
from fastapi import APIRouter

from api.core import replay
from dock import scorer

router = APIRouter()


def recorded_results(version):
    """{case_id: (ok, detail)} for one version, or None while any of its cases is unrecorded."""
    records = replay.load("scorecard")
    keys = {c["id"]: replay.key("case", c["id"], versions=None if version == "baseline" else version)
            for c in scorer.cases}
    if any(k not in records for k in keys.values()):
        return None
    return {cid: (records[k]["ok"], records[k]["detail"]) for cid, k in keys.items()}


def scored(results):
    per_kind, passed = scorer.summary(results)
    return {"passed": passed, "per_kind": per_kind,
            "failures": [{"case_id": cid, "detail": detail} for cid, (ok, detail) in results.items() if not ok]}


def scorecard():
    baseline, cheaper = recorded_results("baseline"), recorded_results("cheaper")
    if baseline is None:
        return None
    return {"total": len(scorer.cases), "baseline": scored(baseline),
            "cheaper": cheaper and scored(cheaper),
            "regressions": [{"case_id": cid, "detail": cheaper[cid][1]} for cid in baseline
                            if baseline[cid][0] and not cheaper[cid][0]] if cheaper else None}


@router.get("/api/overview")
def overview():
    recorded = [r["recorded_at"] for r in replay.load("scorecard").values()]
    return {"scorecard": scorecard(), "recorded_at": {"scorecard": max(recorded, default=None)},
            "mode": "replay", "meter": []}
