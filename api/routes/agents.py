"""POST /api/agents/handoff and /design: S4 triage hands an email to a specialist, S6 one agent vs parallel workers.

A handoff creates three agents on the visitor's Mistral account, runs one email, and always deletes them.
The run has its own deadline under Vercel's 60 s, so a timeout still answers, and names any agent it could not delete.
"""
import asyncio
import json
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.core import ratelimit, replay
from api.core.mode import error, mode
from dock import agents as dock
from dock._client import client, live, meter

DOCK = Path(__file__).resolve().parents[2] / "dock"
INBOX = {m["id"]: m for m in json.loads((DOCK / "inbox.json").read_text(encoding="utf-8"))}
TRUTH = json.loads((DOCK / "inbox_truth.json").read_text(encoding="utf-8"))
TAG = {"created_by": "dock-console"}   # every agent the console creates, so a visitor can find leftovers
DEADLINE_S = 45   # the run, inside Vercel's 60 s maxDuration
CLEANUP_S = 8     # the deletes after it, all at once
DEFAULT_RULE = "v2"
RUNS = 5          # s06 step 6 runs each design five times; a replay picks one

router = APIRouter()


class HandoffIn(BaseModel):
    mail_id: Optional[str] = Field(None, max_length=20)
    text: Optional[str] = Field(None, max_length=10000)
    rule: Literal["v1", "v2"] = DEFAULT_RULE


def entries(result, names):
    """The run as the screen shows it, like s04's show(): handoffs, tool calls and results, messages."""
    out = []
    for e in result.output_entries:
        kind = type(e).__name__
        who = names.get(getattr(e, "agent_id", None))
        if kind == "AgentHandoffEntry":
            out.append({"type": "handoff", "from": (e.previous_agent_name or "").removeprefix("dock-"),
                        "to": e.next_agent_name.removeprefix("dock-")})
        elif kind == "FunctionCallEntry":
            out.append({"type": "call", "agent": who, "name": e.name, "arguments": e.arguments})
        elif kind == "FunctionResultEntry":
            out.append({"type": "result", "result": e.result})
        elif kind == "MessageOutputEntry":
            content = e.content if isinstance(e.content, str) else "".join(getattr(c, "text", "") for c in e.content)
            out.append({"type": "message", "agent": who, "content": content})
    return out


async def delete(created):
    """Delete every agent at once, each within CLEANUP_S. Returns the ids that are confirmed gone."""
    async def one(agent_id):
        await asyncio.wait_for(client.beta.agents.delete_async(agent_id=agent_id), CLEANUP_S)
        return agent_id
    done = await asyncio.gather(*[one(a["id"]) for a in created], return_exceptions=True)
    return [d for d in done if isinstance(d, str)]


async def create_and_run(text, rule, created):
    """s04 steps 3-4. Each agent goes into `created` the moment it exists, so a failure later still deletes it."""
    for spec in dock.specs(rule):
        agent = await client.beta.agents.create_async(**spec, metadata=TAG)
        created.append({"id": agent.id, "name": agent.name})
    tracking, billing, triage = (a["id"] for a in created)
    await client.beta.agents.update_async(agent_id=triage, handoffs=[tracking, billing])
    return await dock.run(triage, text)


@router.post("/api/agents/handoff")
async def handoff(body: HandoffIn, request: Request):
    if body.mail_id is None and body.text is None:
        return error(422, "need_mail_id_or_text")
    if not live():
        if body.mail_id is None:   # free text has no case id, so it is Live only
            return replay.not_recorded()
        options = {"rule": body.rule} if body.rule != DEFAULT_RULE else {}
        return replay.lookup("agents", replay.key("handoff", body.mail_id, **options)) or replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    if body.mail_id is not None and body.mail_id not in INBOX:
        return error(404, "unknown_mail")
    mail = INBOX.get(body.mail_id)
    text = mail["subject"] + "\n\n" + mail["body"] if mail else body.text   # s04 step 6

    created, timed_out = [], False
    try:
        result = await asyncio.wait_for(create_and_run(text, body.rule, created), DEADLINE_S)
    except asyncio.TimeoutError:
        timed_out = True
    finally:
        deleted = await delete(created)
    if timed_out:
        left = [a["id"] for a in created if a["id"] not in deleted]
        return error(504, "timeout", agents_left=left, tag=TAG)

    names = {a["id"]: a["name"].removeprefix("dock-") for a in created}
    expected = dock.ROUTE.get(TRUTH[body.mail_id]["intent"], "triage") if body.mail_id else None
    got = dock.route(result)
    return {"mail_id": body.mail_id, "rule": body.rule, "route": got, "expected": expected,
            "right": got == expected if expected else None, "entries": entries(result, names),
            "agents": {"created": created, "deleted": deleted},
            "mode": mode(), "meter": meter()}


class DesignIn(BaseModel):
    design: Literal["a", "serial", "parallel"]
    guard: bool = False
    run: int = Field(1, ge=1, le=RUNS)   # which recorded run a replay serves; Live always makes a new one


@router.post("/api/agents/design")
async def design(body: DesignIn, request: Request):
    if not live():
        k = replay.key("design", body.design, guard=1 if body.guard else None, run=body.run)
        return replay.lookup("agents", k) or replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    start = time.time()
    if body.design == "a":
        report, tool_calls = await dock.design_a()
        tokens = sum(c["tokens"] or 0 for c in meter())
    else:
        report, tokens = await (dock.run_parallel() if body.design == "parallel" else dock.run_serial())
        tool_calls = 0
    seconds = round(time.time() - start, 2)
    if body.guard:
        report = dock.Report(rows=[dock.guard(r) for r in report.rows])   # s06 step 7
    return {"design": body.design, "guard": body.guard, "run": body.run,
            "rows": [r.model_dump() for r in report.rows], "score": dock.score(report),
            "held": dock.HELD, "seconds": seconds, "tool_calls": tool_calls, "tokens": tokens,
            "mode": mode(), "meter": meter()}
