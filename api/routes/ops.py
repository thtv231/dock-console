"""Operations (S10) and the MCP tool tester.

GET /api/ops/runs: the recorded runs of dock/ops.py (scripts/record.py ops), one record per request the notebook
made. S10 creates agents and spends a budget, so this screen is Replay only, whatever key is sent.
GET /api/ops/mcp/tools and POST /api/ops/mcp/call: dock/mcp_server.py through an in-process MCP client, the same
server /api/mcp serves. send_email there refuses non-customers itself and writes only to DOCK_OUTBOX.
"""
from fastapi import APIRouter
from fastmcp import Client
from pydantic import BaseModel

from api.core import replay
from api.core.mode import error
from dock import mcp_server

router = APIRouter()


def stamped(records):
    return {"recorded_at": max(r["recorded_at"] for r in records),
            "git_sha": ",".join(sorted({r["git_sha"] for r in records}))}


def step(records, name):
    """This step's records, in the order the recorder made them."""
    return sorted((r for k, r in records.items() if k.startswith(f"ops|{name}|")), key=lambda r: r["n"])


def ms(record):
    return {"ms": record["ms"], "meter": record["meter"]}


@router.get("/api/ops/runs")
def runs():
    records = replay.load("ops")
    if not records:
        return replay.not_recorded()
    endpoint, budget, answer = step(records, "endpoint"), step(records, "budget"), step(records, "answer")
    notify, one_line, release = step(records, "notify"), step(records, "reply"), step(records, "release")
    agent = records["ops|agent|"]
    checks = [{"version": c["version"], "line": c["line"],
               "replies": [{k: r[k] for k in ("ask", "reply", "guarantees_date")} | ms(r)
                           for r in release if r["version"] == c["version"]]} for c in agent["checks"]]
    return {
        "endpoint": {"requests": [{k: r[k] for k in ("mail_id", "status", "response")} | ms(r) for r in endpoint],
                     **stamped(endpoint)},
        "budget": {"tokens_per_day": budget[0]["tokens_per_day"], "reserve": budget[0]["reserve"],
                   "requests": [{k: r[k] for k in ("mail_id", "status", "tokens", "detail", "used")} | ms(r)
                                for r in budget], **stamped(budget)},
        "timeout": {"question": answer[0]["question"],
                    "limits": [{"seconds": r["seconds"], **r["response"], "abandoned": r.get("abandoned")} | ms(r)
                               for r in answer], **stamped(answer)},
        "idempotency": {"key": notify[0]["idempotency_key"], "message": notify[0]["message"],
                        "attempts": [{"attempt": r["attempt"], **r["response"]} for r in notify],
                        "outbox": notify[-1]["outbox"], **stamped(notify)},
        "release": {"versions": agent["versions"], "asks": agent["asks"],
                    "one_line": [{k: r[k] for k in ("prod", "ask", "reply", "guarantees_date")} | ms(r)
                                 for r in one_line],
                    "checks": checks, "prod": agent["prod"], "deleted": agent["deleted"], "left": agent["left"],
                    "meter": agent["meter"], **stamped([agent, *one_line, *release])},
        "mode": "replay", "meter": []}


async def tools():
    async with Client(mcp_server.mcp) as c:
        return await c.list_tools()


@router.get("/api/ops/mcp/tools")
async def mcp_tools():
    return {"tools": [{"name": t.name, "description": t.description, "input_schema": t.input_schema,
                       "read_only": bool(t.annotations and t.annotations.read_only_hint),
                       "destructive": bool(t.annotations and t.annotations.destructive_hint)}
                      for t in await tools()],
            "mode": "replay", "meter": []}


class Call(BaseModel):
    tool: str
    args: dict = {}
    approved: bool = False   # set by the UI only after the person approved this exact write


@router.post("/api/ops/mcp/call")
async def mcp_call(body: Call):
    known = {t.name: t for t in await tools()}
    if body.tool not in known:
        return error(400, "unknown_tool", tool=body.tool)
    writes = not (known[body.tool].annotations and known[body.tool].annotations.read_only_hint)
    if writes and not body.approved:   # CLAUDE.md: a write always needs explicit approval
        return error(400, "needs_approval", tool=body.tool)
    async with Client(mcp_server.mcp) as c:
        r = await c.call_tool(body.tool, body.args, raise_on_error=False)
    return {"tool": body.tool, "args": body.args, "is_error": bool(r.is_error),
            "text": "\n".join(getattr(part, "text", "") for part in r.content), "mode": "replay", "meter": []}
