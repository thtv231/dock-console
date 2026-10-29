"""Dock so far: the pieces built in Sections 1-7, in one module, so Section 8 can test them all.

    from dock import app
    app.triage(mail) · app.answer(question) · app.screen(text) · app.reply(text) · await app.email_task(task)

Every function takes the model as a parameter, so an eval can compare versions.
"""
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from dotenv import load_dotenv
from mistralai.client import Mistral
from mistralai.client.utils import BackoffStrategy, RetryConfig
from mistralai.extra.exceptions import DeferredToolCallsException
from mistralai.extra.run.context import RunContext
from pydantic import BaseModel, Field, field_validator

HERE = Path(__file__).resolve().parent
load_dotenv()
client = Mistral(api_key=os.environ["MISTRAL_API_KEY"], timeout_ms=120000,
                 retry_config=RetryConfig("backoff", BackoffStrategy(1000, 20000, 2.0, 120000),
                                          retry_connection_errors=True))
db = sqlite3.connect(HERE / "dock.sqlite", check_same_thread=False)


# ---------- Section 1: triage an email ----------
class Triage(BaseModel):
    intent: Literal["tracking", "customs", "invoice", "booking", "damage", "other"]
    shipment_id: Optional[str] = Field(
        description="The shipment ref exactly as written in the email, like HF-26001. "
                    "null only if the email mentions no shipment")
    language: str = Field(description="Two-letter ISO 639-1 code, lowercase: en, de, fr, nl")

    @field_validator("shipment_id")
    @classmethod
    def normalize_ref(cls, value):
        if value is None:
            return None
        digits = re.sub(r"\D", "", value)
        return f"HF-{digits}" if len(digits) == 5 else value

    @field_validator("language")
    @classmethod
    def two_letters(cls, value):
        return value.lower()[:2]


def triage(mail, model="mistral-small-latest", schema=Triage):
    """Section 1: intent, shipment and language of one email, validated."""
    r = client.chat.parse(model=model, response_format=schema, messages=[
        {"role": "system", "content": "You triage emails for Harbor Freight Co."},
        {"role": "user", "content": mail["subject"] + "\n\n" + mail["body"]}])
    return r.choices[0].message.parsed


# ---------- Section 3: answer from the handbook, with sources ----------
HANDBOOK = json.loads((HERE / "handbook.json").read_text(encoding="utf-8"))
_vectors = None


def _embed(texts):
    r = client.embeddings.create(model="mistral-embed", inputs=texts)
    return np.array([d.embedding for d in r.data])


def search(question, k=3):
    global _vectors
    if _vectors is None:
        _vectors = _embed([d["title"] + ". " + d["text"] for d in HANDBOOK])
    scores = _vectors @ _embed([question])[0]
    return [HANDBOOK[i]["id"] for i in np.argsort(-scores)[:k]]


class Answer(BaseModel):
    found: bool = Field(description="true if a rule in the sections answers the question, also when you must "
                                    "apply it, like a limit or a deadline. false only if no section covers the topic")
    answer: str = Field(description="Short answer, using only the sections")
    sources: list[str] = Field(description="Ids of the sections you used, like HB-03")


def answer(question, model="mistral-medium-latest"):
    """Section 3: an answer from the handbook, with found + sources, and the ids retrieved."""
    retrieved = search(question)
    context = "\n\n".join(f"[{d['id']}] {d['title']}: {d['text']}" for d in HANDBOOK if d["id"] in retrieved)
    r = client.chat.parse(model=model, response_format=Answer, messages=[
        {"role": "system", "content": "You answer questions for Harbor Freight's ops team. Use ONLY the handbook "
                                      "sections below. If they don't answer the question, set found to false.\n\n" + context},
        {"role": "user", "content": question}])
    return r.choices[0].message.parsed, retrieved


# ---------- Section 7: screen inbound messages, never guarantee a date ----------
class DataRequest(BaseModel):
    others_data: bool = Field(description="true only if the sender asks us to give them contact details or records "
                                          "of OTHER companies. false for any question about the sender's own business")


def screen(text, ignore=("pii",)):
    """Section 7: reasons to block an inbound message (empty list = let it through)."""
    cats = client.classifiers.moderate(model="mistral-moderation-latest", inputs=[text]).results[0].categories
    reasons = [k for k, v in cats.items() if v and k not in ignore]
    r = client.chat.parse(model="mistral-small-latest", response_format=DataRequest, messages=[
        {"role": "system", "content": "You screen inbound email for Harbor Freight Co."},
        {"role": "user", "content": text}])
    if r.choices[0].message.parsed.others_data:
        reasons.append("asks for other customers' data")
    return reasons


RULE = ("You are Dock, support assistant of Harbor Freight Co. Be helpful and reassuring. Never promise or guarantee "
        "a delivery or arrival date. You may give the carrier's estimated ETA, and you must say it is an estimate.")


def reply(text, model="mistral-small-latest", instructions=RULE):
    """Section 7: a customer reply under the never-guarantee-a-date rule."""
    r = client.chat.complete(model=model, messages=[{"role": "system", "content": instructions},
                                                    {"role": "user", "content": text}])
    return r.choices[0].message.content


class PolicyCheck(BaseModel):
    guarantees_date: bool = Field(description="true if the reply promises or guarantees a delivery or arrival "
                                              "date; quoting an estimated ETA is allowed")
    quote: str = Field(description="The exact sentence that guarantees a date, or empty")


def guarantees_date(text):
    """Section 7: True only if a judge quotes a sentence of the reply that guarantees a date."""
    r = client.chat.parse(model="mistral-small-latest", response_format=PolicyCheck, messages=[
        {"role": "system", "content": "You check replies against Harbor Freight's policy."},
        {"role": "user", "content": text}])
    c = r.choices[0].message.parsed
    return bool(c.guarantees_date and c.quote.strip() and c.quote.strip() in text)


# ---------- Section 5: an email task with approval in code ----------
TRACE = []   # every tool step of the last email_task, in order


def get_shipment(shipment_id: str) -> str:
    """Look up a shipment (like HF-26001): status, consignee and tracking events."""
    TRACE.append("get_shipment")
    s = db.execute("SELECT s.id, s.status, c.company, c.email FROM shipments s JOIN customers c "
                   "ON c.id = s.consignee_id WHERE s.id = ?", (shipment_id,)).fetchone()
    if s is None:
        return f"No shipment {shipment_id}"
    events = db.execute("SELECT at, event, note FROM tracking_events WHERE shipment_id = ? ORDER BY at", (shipment_id,))
    return json.dumps({"id": s[0], "status": s[1], "consignee": s[2], "consignee_email": s[3],
                       "events": [list(e) for e in events.fetchall()]})


def list_customers() -> str:
    """List all Harbor Freight customers with their emails."""
    TRACE.append("list_customers")
    return json.dumps(db.execute("SELECT company, email FROM customers").fetchall())


OUTBOX = []


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email from Harbor Freight operations."""
    OUTBOX.append({"to": to, "subject": subject, "body": body})
    TRACE.append(f"sent:{to}")
    return f"Email sent to {to}"


async def email_task(task, shipment_id, model="mistral-medium-latest"):
    """Section 5: run Dock on a task about one shipment. Only that shipment's consignee may receive email.
    Returns (the trace of tool steps in order, emails actually sent)."""
    allowed = {json.loads(get_shipment(shipment_id))["consignee_email"]}
    OUTBOX.clear()
    TRACE.clear()
    inputs, conversation_id = [{"role": "user", "content": task}], None
    while True:
        async with RunContext(model=model) as ctx:
            ctx.conversation_id = conversation_id
            ctx.register_func(get_shipment)
            ctx.register_func(list_customers)
            ctx.register_func(send_email, requires_confirmation=True)
            try:
                await client.beta.conversations.run_async(
                    run_ctx=ctx, inputs=inputs, instructions="You are Dock, the operations assistant of Harbor Freight Co.")
                return list(TRACE), [m["to"] for m in OUTBOX]
            except DeferredToolCallsException as paused:
                conversation_id = paused.conversation_id
                inputs = []
                for call in paused.deferred_calls:
                    to = json.loads(call.arguments)["to"]
                    TRACE.append(f"draft:{to}")
                    inputs.append(call.confirm() if to in allowed else call.reject(f"Blocked: only email {allowed}"))
