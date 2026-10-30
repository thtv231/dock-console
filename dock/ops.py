"""Section 10: Dock as a web service. It never spends more than its budget, answers in time or hands over to a person,
never sends the same email twice, and a bad release is undone in one line. Used only by the recorder: the console
shows recorded runs of it, never live ones.

    from dock import ops
    http = TestClient(ops.api) · http.post("/triage/v2", json=mail) · http.post("/answer?seconds=0.2", json=q)
    agent, v2 = ops.create_versions() · ops.release_check(v2.version, http) · ops.rollback() · ops.delete()

Ported from notebooks/s10_ship.ipynb: routes, prompts and instructions exactly as written. The notebook's
top-level agent steps are functions here, and the agent carries the console's tag so a leftover can be found.
"""
import asyncio

from fastapi import FastAPI, Header, HTTPException

from dock import app as dock

TAG = {"created_by": "dock-console"}   # api/routes/agents.TAG: the recorder looks for leftovers by it
api = FastAPI(title="Dock")


# ---------- s10 step 1: a first endpoint ----------
@api.post("/triage")
def triage(mail: dict):
    t = dock.triage(mail)
    return t.model_dump()


# ---------- s10 step 2: a token budget ----------
class Budget:
    def __init__(self, tokens_per_day, reserve):
        self.limit, self.reserve, self.used = tokens_per_day, reserve, 0

    def check(self):          # before the call: is there room for one more?
        if self.used + self.reserve > self.limit:
            raise HTTPException(429, f"daily budget of {self.limit} tokens reached")

    def spend(self, tokens):  # after the call: count what it really used
        self.used += tokens


budget = Budget(tokens_per_day=300, reserve=150)


@api.post("/triage/v2")
def triage_v2(mail: dict):
    budget.check()
    r = dock.client.chat.parse(model="mistral-small-latest", response_format=dock.Triage, messages=[
        {"role": "system", "content": "You triage emails for Harbor Freight Co."},
        {"role": "user", "content": mail["subject"] + "\n\n" + mail["body"]}])
    budget.spend(r.usage.total_tokens)
    return {**r.choices[0].message.parsed.model_dump(), "tokens": r.usage.total_tokens}


# ---------- s10 step 3: answer in time, or hand over ----------
FALLBACK = {"status": "queued", "message": "A person from operations will reply."}


@api.post("/answer")
async def answer(body: dict, seconds: float = 8.0):
    try:
        a, sources = await asyncio.wait_for(asyncio.to_thread(dock.answer, body["question"]), timeout=seconds)
    except asyncio.TimeoutError:
        return FALLBACK
    return {"status": "answered", "answer": a.answer, "sources": a.sources}


QUESTION = {"question": "How many free days do we get at Los Angeles before demurrage starts?"}
LIMITS = [8.0, 0.2]


# ---------- s10 step 4: never send the same email twice ----------
SEEN = {}


@api.post("/notify")
def notify(body: dict, idempotency_key: str = Header(...)):
    if idempotency_key in SEEN:
        return {**SEEN[idempotency_key], "repeated": True}
    dock.send_email(body["to"], body["subject"], body["body"])
    SEEN[idempotency_key] = {"status": "sent", "to": body["to"]}
    return SEEN[idempotency_key]


MESSAGE = {"to": "import@nordlicht.example.com", "subject": "HF-26038 update", "body": "Docs are with the broker."}
IDEMPOTENCY_KEY = "HF-26038-update-1"
ATTEMPTS = 3   # the caller retries after a timeout


# ---------- s10 step 5: versions and a one-line rollback ----------
mistral = dock.client
agent = v2 = None


def create_versions():
    """Version 0 never promises a date, version 1 reassures with a firm one, and prod points at version 1."""
    global agent, v2
    agent = mistral.beta.agents.create(
        model="mistral-small-latest", name="dock-reply", metadata=TAG,
        instructions="You are Dock. Reply in one sentence. Never promise a delivery date.")
    v2 = mistral.beta.agents.update(
        agent_id=agent.id,
        instructions="You are Dock. Reply in one sentence. Reassure the customer with a firm delivery date.")
    mistral.beta.agents.create_version_alias(agent_id=agent.id, alias="prod", version=v2.version)
    return agent, v2


@api.post("/reply")
def reply(body: dict):
    r = mistral.beta.conversations.start(agent_id=agent.id, agent_version="prod",
                                         inputs=body["text"], store=False)
    text = str(r.outputs[-1].content)
    return {"reply": text, "guarantees_date": dock.guarantees_date(text)}


ASK = {"text": "Will HF-26012 arrive in Felixstowe by Friday?"}


def rollback():
    """The one line: prod back on version 0."""
    mistral.beta.agents.create_version_alias(agent_id=agent.id, alias="prod", version=agent.version)


def prod():
    """The version the prod alias points at, as Mistral reports it."""
    aliases = mistral.beta.agents.list_version_aliases(agent_id=agent.id)
    return next(a.version for a in aliases if a.alias == "prod")


# ---------- s10 step 6: release check, then clean up ----------
ASKS = ["Will HF-26012 arrive in Felixstowe by Friday?",
        "Can you promise HF-26025 is released before the weekend?",
        "Our sales team needs a firm date for HF-26003. What date can you commit to?"]


def release_check(version, client):
    """client: an HTTP client on `api`, as the notebook's TestClient(api)."""
    mistral.beta.agents.create_version_alias(agent_id=agent.id, alias="prod", version=version)
    bad = sum(client.post("/reply", json={"text": t}).json()["guarantees_date"] for t in ASKS)
    return f"version {version}: {bad}/{len(ASKS)} replies guarantee a date"


def delete():
    mistral.beta.agents.delete(agent_id=agent.id)
