"""GET /api/overview: numbers computed from replays/*.json only, never typed in: the S8 scorecard, and for every
screen the median latency and tokens of one recorded request, when it was recorded, and from which commits."""
from statistics import median

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


# lib/screens.ts, in sidebar order, with the replay files each screen shows. The Overview is built from the others.
SCREENS = [("/", []), ("/inbox", ["triage"]), ("/documents", ["documents"]), ("/handbook", ["handbook"]),
           ("/agents", ["agents"]), ("/approvals", ["approvals"]), ("/guardrails", ["guardrails"]),
           ("/voice", ["voice"]), ("/scorecard", ["scorecard"]), ("/operations", ["ops"])]


def screen(href, names):
    """One request is one record with a meter: its latency is the sum of its calls' ms, its tokens the sum of the
    tokens its calls report (OCR reports pages, not tokens, so a request with only OCR calls has no tokens)."""
    every = [r for name in names for r in replay.load(name).values()]
    requests = [r for r in every if r.get("meter")]
    ms = [sum(c["ms"] for c in r["meter"]) for r in requests]
    tokens = [sum(c["tokens"] for c in r["meter"] if c["tokens"] is not None) for r in requests
              if any(c["tokens"] is not None for c in r["meter"])]
    return {"href": href, "replays": names, "requests": len(requests),
            "median_ms": median(ms) if ms else None, "median_tokens": median(tokens) if tokens else None,
            "recorded_at": max((r["recorded_at"] for r in every), default=None),
            "git_shas": sorted({r["git_sha"] for r in every})}


@router.get("/api/overview")
def overview():
    recorded = [r["recorded_at"] for r in replay.load("scorecard").values()]
    return {"screens": [screen(href, names) for href, names in SCREENS], "scorecard": scorecard(),
            "recorded_at": {"scorecard": max(recorded, default=None)}, "mode": "replay", "meter": []}
