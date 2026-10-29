"""POST /api/triage: S1, intent + shipment + language of one email, via dock.app.triage."""
import inspect
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.core import ratelimit, replay
from api.core.mode import error, mode
from dock import app as dock
from dock._client import live, meter

DOCK = Path(__file__).resolve().parents[2] / "dock"
INBOX = {m["id"]: m for m in json.loads((DOCK / "inbox.json").read_text(encoding="utf-8"))}
TRUTH = json.loads((DOCK / "inbox_truth.json").read_text(encoding="utf-8"))
DEFAULT_MODEL = inspect.signature(dock.triage).parameters["model"].default

router = APIRouter()


class Mail(BaseModel):
    subject: str = Field(max_length=500)
    body: str = Field(max_length=10000)


class TriageIn(BaseModel):
    mail_id: Optional[str] = Field(None, max_length=20)
    mail: Optional[Mail] = None
    model: str = Field(DEFAULT_MODEL, max_length=64)


@router.post("/api/triage")
def triage(body: TriageIn, request: Request):
    if body.mail_id is None and body.mail is None:
        return error(422, "need_mail_id_or_mail")
    if not live():
        if body.mail_id is None:   # free text has no case id, so it is Live only
            return replay.not_recorded()
        options = {"model": body.model} if body.model != DEFAULT_MODEL else {}
        return replay.lookup("triage", replay.key("triage", body.mail_id, **options)) or replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    if body.mail_id is not None and body.mail_id not in INBOX:
        return error(404, "unknown_mail")
    mail = INBOX[body.mail_id] if body.mail_id else body.mail.model_dump()
    got = dock.triage(mail, model=body.model).model_dump()
    expected = TRUTH.get(body.mail_id)
    return {"mail_id": body.mail_id, **got, "model": body.model, "expected": expected,
            "wrong_fields": [f for f in expected if got[f] != expected[f]] if expected else [],
            "mode": mode(), "meter": meter()}
