"""POST /api/scorecard/case and /judge: S8, one scorecard case (or one judge verdict) at a time, via dock.scorer.

The browser runs the 30 cases a few at a time and totals them; GET /api/overview totals the recorded ones.
"""
import asyncio
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from api.core import ratelimit, replay
from api.core.mode import error, mode
from dock import scorer
from dock._client import live, meter, results

CASES = {c["id"]: c for c in scorer.cases}
# app.email_task keeps its trace and outbox in module globals: one agent case at a time on an instance.
AGENT_LOCK = asyncio.Lock()

router = APIRouter()


class CaseIn(BaseModel):
    case_id: str = Field(max_length=20)
    versions: Literal["baseline", "cheaper"] = "baseline"


@router.post("/api/scorecard/case")
async def case(body: CaseIn, request: Request):
    if not live():
        options = {"versions": body.versions if body.versions != "baseline" else None}
        return replay.lookup("scorecard", replay.key("case", body.case_id, **options)) or replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    c = CASES.get(body.case_id)
    if c is None:
        return error(404, "unknown_case")
    out = {"case_id": c["id"], "kind": c["kind"], "versions": body.versions,
           "models": scorer.VERSIONS[body.versions], "expect": c["expect"]}
    if c["kind"] == "agent":
        async with AGENT_LOCK:
            ok, problems, trace = await scorer.check_agent(c)
        out.update(ok=ok, detail="; ".join(problems), trace=trace,
                   sent=[t.split(":", 1)[1] for t in trace if t.startswith("sent:")])
    else:
        ok, detail = await run_in_threadpool(scorer.check, c, **scorer.VERSIONS[body.versions])
        out.update(ok=bool(ok), detail=detail)
    return {**out, "mode": mode(), "meter": meter()}


class JudgeIn(BaseModel):
    case_id: str = Field(max_length=20)
    versions: Literal["baseline", "cheaper"] = "baseline"   # whose answer is judged; the judge is the same


@router.post("/api/scorecard/judge")
async def judge(body: JudgeIn, request: Request):
    if not live():
        options = {"versions": body.versions if body.versions != "baseline" else None}
        record = replay.lookup("scorecard", replay.key("judge", body.case_id, **options))
        # verdicts recorded before they named their version were all on the baseline: the key says whose answer it is
        return {"versions": body.versions, **record} if record else replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    c = CASES.get(body.case_id)
    if c is None or c["kind"] != "answer":
        return error(404, "unknown_answer_case")
    grounded, answer, unsupported = await run_in_threadpool(scorer.judge, c["input"], **scorer.VERSIONS[body.versions])
    verdict = results("chat.parse")[-1].choices[0].message.parsed   # the Grounded judge() reduced with its evidence
    return {"case_id": c["id"], "versions": body.versions, "models": scorer.VERSIONS[body.versions],
            "question": c["input"], "grounded": grounded, "unsupported": unsupported,
            "answer": answer, "judge": {"grounded": verdict.grounded, "unsupported": verdict.unsupported,
                                        "unsupported_in_answer": bool(verdict.unsupported.strip())
                                        and verdict.unsupported.strip() in answer},
            "mode": mode(), "meter": meter()}
