"""POST /api/approvals/start and /resume: S5, Dock drafts emails and a person approves or rejects each one.

A run pauses on every send_email (requires_confirmation). The request then ends: Mistral keeps the conversation,
the browser keeps a signed state, the server keeps nothing. /resume, on any instance, answers that pause.
The server has the last word on every approved draft: a recipient who is not a customer is refused anyway.
"""
import json
from typing import Dict, Literal

from fastapi import APIRouter, Request
from mistralai.client.errors import SDKError
from mistralai.client.models import FunctionResultEntry
from mistralai.extra.exceptions import DeferredToolCallEntry, DeferredToolCallsException
from mistralai.extra.run.context import RunContext
from pydantic import BaseModel, Field

from api.core import ratelimit, replay, state
from api.core.mode import error, mode
from dock import hitl
from dock._client import client, live, meter

MAX_PAUSES = 8          # a run that keeps redrafting stops being resumable here
ALREADY_ANSWERED = 3001  # Mistral's code when function results name tool calls that already have one (lesson 4 probe)
REJECTED = "Rejected by the operator"   # s05's reject message

router = APIRouter()


class StartIn(BaseModel):
    shipment_id: str = Field("HF-26038", max_length=20)
    instructions: Literal["plain", "safe"] = "safe"


class ResumeIn(BaseModel):
    state: str = Field(max_length=200_000)
    decisions: Dict[str, Literal["approve", "reject"]]


def refused(to):
    return f"Refused by the Dock server: {to} is not a Harbor Freight customer"


def trace_of(entries):
    """The run as the screen shows it, from this request's output entries only: nothing shared between requests."""
    out = []
    for e in entries:
        kind = type(e).__name__
        if kind == "FunctionCallEntry":
            out.append({"type": "call", "name": e.name, "arguments": e.arguments, "tool_call_id": e.tool_call_id})
        elif kind == "FunctionResultEntry":
            out.append({"type": "result", "tool_call_id": e.tool_call_id, "result": e.result})
        elif kind == "MessageOutputEntry":
            content = e.content if isinstance(e.content, str) else "".join(getattr(c, "text", "") for c in e.content)
            out.append({"type": "message", "content": content})
    return out


def drafts(pending, shipment_id):
    customers, consignee = hitl.customers(), hitl.consignee(shipment_id)
    out = []
    for p in pending:
        args = json.loads(p["arguments"])
        out.append({"id": p["tool_call_id"], "to": args.get("to"), "subject": args.get("subject", ""),
                    "body": args.get("body", ""), "is_customer": args.get("to") in customers,
                    "is_consignee": args.get("to") == consignee})
    return out


async def run(s, inputs):
    """One leg of the s05 loop: run until done or the next pause. s is the state so far (no exp)."""
    outbox = list(s["outbox"])
    async with RunContext(model=hitl.MODEL) as ctx:
        ctx.conversation_id = s.get("conversation_id")
        ctx.register_func(hitl.get_shipment)
        ctx.register_func(hitl.list_customers)
        ctx.register_func(hitl.sender(outbox), requires_confirmation=True)
        try:
            result = await client.beta.conversations.run_async(
                run_ctx=ctx, inputs=inputs, instructions=hitl.INSTRUCTIONS[s["instructions"]])
        except DeferredToolCallsException as paused:
            pending = [{"tool_call_id": d.tool_call_id, "name": d.tool_name, "arguments": d.arguments,
                        "id": d.function_call.id} for d in paused.deferred_calls]
            nxt = {**s, "conversation_id": paused.conversation_id, "pause": s["pause"] + 1, "pending": pending,
                   # results the SDK ran alongside the deferred calls: Mistral still needs them on the resume
                   "executed": [{"tool_call_id": r.tool_call_id, "result": r.result} for r in paused.executed_results],
                   "trace": s["trace"] + trace_of(paused.outputs), "outbox": outbox}
            return paused_response(nxt)
    messages = [t for t in trace_of(result.output_entries) if t["type"] == "message"]
    return {"status": "done", "shipment_id": s["shipment_id"], "instructions": s["instructions"],
            "message": messages[-1]["content"] if messages else "",
            "trace": s["trace"] + trace_of(result.output_entries), "outbox": outbox,
            "mode": mode(), "meter": meter()}


def paused_response(nxt):
    return {"status": "paused", "shipment_id": nxt["shipment_id"], "instructions": nxt["instructions"],
            "pause": nxt["pause"], "drafts": drafts(nxt["pending"], nxt["shipment_id"]),
            "trace": nxt["trace"], "outbox": nxt["outbox"], "state": state.sign(nxt),
            "mode": mode(), "meter": meter()}


def recorded_choices(shipment_id, instructions, pause, history):
    """At a replayed pause, the answers some recorded run gave, so the UI can mark them."""
    prefix = f"approvals|{shipment_id}|{instructions}|{pause}|"
    suffix = f"|after={';'.join(history)}" if history else ""
    choices = []
    for k in replay.load("approvals"):
        if not k.startswith(prefix):
            continue
        rest = k[len(prefix):]
        if (suffix and rest.endswith(suffix)) or (not suffix and "|" not in rest):
            rest = rest[:len(rest) - len(suffix)] if suffix else rest
            choices.append(dict(pair.split("=", 1) for pair in rest.split(",")))
    return choices


def served(record):
    """A recorded response with a fresh signed state for its next pause (records hold the unsigned payload)."""
    if record is None:
        return replay.not_recorded()
    out = {k: v for k, v in record.items() if k != "next"}
    nxt = record.get("next")
    if nxt:
        out["state"] = state.sign(nxt)
        out["recorded_choices"] = recorded_choices(nxt["shipment_id"], nxt["instructions"], nxt["pause"],
                                                   nxt.get("history", []))
    return out


@router.post("/api/approvals/start")
async def start(body: StartIn, request: Request):
    try:
        state.sign({})   # no secret, no approvals: refuse before anything runs
    except state.NoSecret:
        return error(503, "no_state_secret")
    if not live():
        return served(replay.lookup("approvals", replay.approvals_key(body.shipment_id, body.instructions, 0, [], {})))

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    if hitl.consignee(body.shipment_id) is None:
        return error(404, "unknown_shipment")
    s = {"shipment_id": body.shipment_id, "instructions": body.instructions, "pause": 0,
         "trace": [], "outbox": [], "history": []}
    return await run(s, [{"role": "user", "content": hitl.task(body.shipment_id)}])


@router.post("/api/approvals/resume")
async def resume(body: ResumeIn, request: Request):
    try:
        s = state.verify(body.state)
    except state.NoSecret:
        return error(503, "no_state_secret")
    except state.BadState as e:
        return error(400, e.code)
    s.pop("exp")
    pending = s["pending"]
    recipients = {json.loads(p["arguments"])["to"] for p in pending}
    if set(body.decisions) != recipients:
        return error(400, "decisions_mismatch", pending=sorted(recipients))
    history = s.get("history", [])

    if "conversation_id" not in s:   # a replayed run: the decisions pick the recorded path
        k = replay.approvals_key(s["shipment_id"], s["instructions"], s["pause"], pending, body.decisions, history)
        return served(replay.lookup("approvals", k))

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    if s["pause"] >= MAX_PAUSES:
        return error(422, "too_many_pauses", pauses=s["pause"])
    customers = hitl.customers()
    inputs = [FunctionResultEntry(tool_call_id=r["tool_call_id"], result=r["result"]) for r in s.get("executed", [])]
    decisions = []
    for p in pending:
        call = DeferredToolCallEntry.from_dict({"function_call": {
            "id": p.get("id"), "tool_call_id": p["tool_call_id"], "name": p["name"], "arguments": p["arguments"]}})
        to = json.loads(p["arguments"])["to"]
        if body.decisions[to] == "approve" and to not in customers:
            inputs.append(call.reject(refused(to)))   # whatever the person chose
            decisions.append({"type": "decision", "to": to, "decision": "refused", "by": "server",
                              "tool_call_id": p["tool_call_id"], "result": refused(to)})
        elif body.decisions[to] == "approve":
            inputs.append(call.confirm())
            decisions.append({"type": "decision", "to": to, "decision": "approve", "by": "person",
                              "tool_call_id": p["tool_call_id"]})
        else:
            inputs.append(call.reject(REJECTED))
            decisions.append({"type": "decision", "to": to, "decision": "reject", "by": "person",
                              "tool_call_id": p["tool_call_id"], "result": REJECTED})
    s = {**s, "trace": s["trace"] + decisions, "history": history + [replay.decided(pending, body.decisions)]}
    s.pop("pending"), s.pop("executed", None)
    try:
        return await run(s, inputs)
    except SDKError as e:
        if e.status_code == 400 and _code(e.body) == ALREADY_ANSWERED:
            return error(409, "already_answered")
        raise


def _code(body):
    try:
        return json.loads(body).get("code")
    except (ValueError, AttributeError):
        return None
