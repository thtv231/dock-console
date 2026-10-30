"""GET /api/overview: numbers computed from replays/*.json only, never typed in: the S8 scorecard, and for every
screen the median latency and tokens of one recorded request, when it was recorded, and from which commits."""
from statistics import median

from fastapi import APIRouter

from api.core import replay
from api.routes import approvals, ops
from api.routes import voice as voice_routes
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


def ratio(label, n, of):
    return {"label": label, "value": f"{n}/{of}", "n": n, "of": of}


def records(name, prefix=""):
    return {k: r for k, r in replay.load(name).items() if k.startswith(prefix)}


def full(covered):
    """A design run's "9/10" coverage: did it cover every held shipment?"""
    got, of = covered.split("/")
    return got == of


def inbox():
    rows = records("triage").values()
    return [ratio("Emails with intent, shipment and language right", sum(not r["wrong_fields"] for r in rows), len(rows))]


def documents():
    rows = records("documents")
    right = lambda r: not r["truth"]["wrong_fields"] and r["truth"]["flag_right"]
    final = [r for k, r in rows.items() if k.endswith("|")]
    first = [r for k, r in rows.items() if k.endswith("|schema=v1")]
    return [ratio("Documents read right, final schema", sum(map(right, final)), len(final)),
            ratio("Invoices read right, first schema", sum(map(right, first)), len(first))]


def handbook():
    rows = records("handbook").values()
    return [ratio("Answers with the right sources and words", sum(all(r["checks"].values()) for r in rows), len(rows))]


def agents():
    out = []
    for rule, suffix in (("v1", "|rule=v1"), ("v2", "|")):
        rows = [r for k, r in records("agents", "handoff|").items() if k.endswith(suffix)]
        out.append(ratio(f"Routed right, rule {rule}", sum(r["right"] for r in rows), len(rows)))
    for design, label in (("a", "Design A"), ("parallel", "Parallel workers")):
        rows = records("agents", f"design|{design}|").values()
        out.append(ratio(f"{label}: runs covering every held shipment", sum(full(r["score"]["covered"]) for r in rows),
                         len(rows)))
    return out


def approval_runs():
    rows = approvals.runs()["runs"]
    return [ratio("Runs that emailed a non-customer", sum(bool(r["sent_non_customer"]) for r in rows), len(rows)),
            ratio("Runs that emailed the consignee", sum(r["emailed_consignee"] for r in rows), len(rows)),
            ratio("Answers that contradict the outbox", sum(r["answer_contradicts"] for r in rows), len(rows))]


def guardrails():
    screened = records("guardrails", "screen|").values()
    out = [ratio("Messages screened right", sum(r["right"] for r in screened), len(screened))]
    for rule, suffix in (("Dock's rule", "|"), ("Plain prompt", "|rule=plain")):
        rows = [r for k, r in records("guardrails", "reply|").items() if k.endswith(suffix)]
        out.append(ratio(f"{rule}: replies guaranteeing a date", sum(r["guarantees_date"] for r in rows), len(rows)))
    return out


def voice():
    out = []
    for label, bias in (("no bias", False), ("with bias", True)):
        rows = [r for k, r in records("voice", "voice|").items() if ("bias=1" in k) == bias]
        out.append(ratio(f"Calls noted fully right, {label}", sum(all(r["score_vs_truth"].values()) for r in rows),
                         len(rows)))
    # The same words each time, so only understanding varies: what one transcript per source gives, run again.
    for call_id, sources in voice_routes.runs()["notes"].items():
        for source, label in (("nobias", "no bias"), ("bias", "with bias")):
            if source in sources:
                n = sources[source]
                out.append(ratio(f"{call_id} noted again on one transcript, issue right, {label}", n["right"]["issue"],
                                 n["runs"]))
    return out


def scorecard_scores():
    card = scorecard()
    if card is None:
        return []
    return [ratio(f"{label} passes", card[v]["passed"], card["total"])
            for v, label in (("baseline", "Baseline"), ("cheaper", "Cheaper")) if card[v]]


def operations():
    runs = ops.runs()
    if not isinstance(runs, dict):
        return []
    budget, idem, release = runs["budget"]["requests"], runs["idempotency"], runs["release"]
    return [ratio("Requests refused by the budget", sum(q["status"] == 429 for q in budget), len(budget)),
            ratio("Emails sent over the retries", len(idem["outbox"]), len(idem["attempts"])),
            *[ratio(f"Version {c['version']}: replies guaranteeing a date",
                    sum(r["guarantees_date"] for r in c["replies"]), len(c["replies"])) for c in release["checks"]],
            {"label": "Prod after the rollback", "value": f"version {release['prod']}"},
            {"label": "Tokens spent after the handover", "value": str(sum(
                c["tokens"] or 0 for limit in runs["timeout"]["limits"] for c in (limit.get("abandoned") or {}).get("meter", [])))}]


SCORES = {"/inbox": inbox, "/documents": documents, "/handbook": handbook, "/agents": agents,
          "/approvals": approval_runs, "/guardrails": guardrails, "/voice": voice, "/scorecard": scorecard_scores,
          "/operations": operations}


def screen(href, names):
    """One request is one record with a meter: its latency is the sum of its calls' ms, its tokens the sum of the
    tokens its calls report (OCR reports pages, not tokens, so a request with only OCR calls has no tokens)."""
    every = [r for name in names for r in replay.load(name).values()]
    requests = [r for r in every if r.get("meter")]
    ms = [sum(c["ms"] for c in r["meter"]) for r in requests]
    tokens = [sum(c["tokens"] for c in r["meter"] if c["tokens"] is not None) for r in requests
              if any(c["tokens"] is not None for c in r["meter"])]
    return {"href": href, "replays": names, "scores": SCORES[href]() if href in SCORES else [], "requests": len(requests),
            "median_ms": median(ms) if ms else None, "median_tokens": median(tokens) if tokens else None,
            "recorded_at": max((r["recorded_at"] for r in every), default=None),
            "git_shas": sorted({r["git_sha"] for r in every})}


@router.get("/api/overview")
def overview():
    recorded = [r["recorded_at"] for r in replay.load("scorecard").values()]
    return {"screens": [screen(href, names) for href, names in SCREENS], "scorecard": scorecard(),
            "recorded_at": {"scorecard": max(recorded, default=None)}, "mode": "replay", "meter": []}
