"""POST /api/guardrails/screen and /reply: S7, block what the business forbids, never guarantee a date."""
import json
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.core import ratelimit, replay
from api.core.mode import error, mode
from dock import app as dock
from dock._client import live, meter

DOCK = Path(__file__).resolve().parents[2] / "dock"
BAD = {m["id"]: m for m in json.loads((DOCK / "bad.json").read_text(encoding="utf-8"))}
INBOX = {m["id"]: m for m in json.loads((DOCK / "inbox.json").read_text(encoding="utf-8"))}
ASKS = {a["id"]: a for a in json.loads((DOCK / "asks.json").read_text(encoding="utf-8"))}
OTHERS_DATA = "asks for other customers' data"   # the reason dock.app.screen adds after its DataRequest parse

# s07's rule before the date rule was added. dock.app.RULE is this plus the date rule.
PLAIN = "You are Dock, support assistant of Harbor Freight Co. Be helpful and reassuring."
assert dock.RULE.startswith(PLAIN)
RULES = {"dock": dock.RULE, "plain": PLAIN}

router = APIRouter()


def message(message_id):
    """A bad message is screened as written; an inbox email as s07 did: subject, blank line, body."""
    if message_id in BAD:
        return BAD[message_id]["text"], {"block": True, "kind": BAD[message_id]["kind"]}
    if message_id in INBOX:
        mail = INBOX[message_id]
        return mail["subject"] + "\n\n" + mail["body"], {"block": False, "kind": "normal"}
    return None, None


class ScreenIn(BaseModel):
    message_id: Optional[str] = Field(None, max_length=20)
    text: Optional[str] = Field(None, max_length=10000)


@router.post("/api/guardrails/screen")
def screen(body: ScreenIn, request: Request):
    if body.message_id is None and body.text is None:
        return error(422, "need_message_id_or_text")
    if not live():
        if body.message_id is None:
            return replay.not_recorded()
        return replay.lookup("guardrails", replay.key("screen", body.message_id)) or replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    text, expected = message(body.message_id) if body.message_id else (body.text, None)
    if text is None:
        return error(404, "unknown_message")
    reasons = dock.screen(text)
    moderation = next((m for m in meter() if m["op"] == "classifiers.moderate"), {})
    return {"message_id": body.message_id, "text": text, "reasons": reasons, "block": bool(reasons),
            "ignored": ["pii"], "others_data": OTHERS_DATA in reasons,
            "categories": moderation.get("categories", [{}])[0], "expected": expected,
            "right": expected["block"] == bool(reasons) if expected else None, "mode": mode(), "meter": meter()}


class ReplyIn(BaseModel):
    ask_id: Optional[str] = Field(None, max_length=20)
    text: Optional[str] = Field(None, max_length=10000)
    rule: Literal["dock", "plain"] = "dock"


@router.post("/api/guardrails/reply")
def reply(body: ReplyIn, request: Request):
    if body.ask_id is None and body.text is None:
        return error(422, "need_ask_id_or_text")
    if not live():
        if body.ask_id is None:
            return replay.not_recorded()
        options = {"rule": body.rule} if body.rule != "dock" else {}
        return replay.lookup("guardrails", replay.key("reply", body.ask_id, **options)) or replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    if body.ask_id is not None and body.ask_id not in ASKS:
        return error(404, "unknown_ask")
    text = ASKS[body.ask_id]["text"] if body.ask_id else body.text
    drafted = dock.reply(text, instructions=RULES[body.rule])
    return {"ask_id": body.ask_id, "text": text, "rule": body.rule, "reply": drafted,
            "guarantees_date": dock.guarantees_date(drafted), "mode": mode(), "meter": meter()}
