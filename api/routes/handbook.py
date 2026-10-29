"""POST /api/answer: S3, an answer from the handbook with its sources, via dock.app.answer."""
import inspect
import json
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.core import ratelimit, replay
from api.core.mode import error, mode
from dock import app as dock
from dock._client import live, meter

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = {q["id"]: q for q in json.loads((ROOT / "dock" / "questions.json").read_text(encoding="utf-8"))}
VECTORS = replay.REPLAYS / "handbook_vectors.npy"
DEFAULT_MODEL = inspect.signature(dock.answer).parameters["model"].default

# Recorded once by scripts/record.py, so a live answer costs one embedding of the question, not 13.
if VECTORS.exists():
    _recorded = np.load(VECTORS)
    if len(_recorded) == len(dock.HANDBOOK):
        dock._vectors = _recorded

router = APIRouter()


class AnswerIn(BaseModel):
    question_id: Optional[str] = Field(None, max_length=20)
    question: Optional[str] = Field(None, max_length=2000)
    model: str = Field(DEFAULT_MODEL, max_length=64)


def checks(expected, got):
    """The answer key: cited sources were retrieved, and every must_contain entry ("a|b" = either) is in the answer."""
    text = got["answer"].lower()
    return {"sources_ok": set(got["sources"]) <= set(got["retrieved"]),
            "found_ok": got["found"] == bool(expected["sources"]),
            "must_contain_ok": all(any(w in text for w in words.split("|")) for words in expected["must_contain"])}


@router.post("/api/answer")
def answer(body: AnswerIn, request: Request):
    if body.question_id is None and body.question is None:
        return error(422, "need_question_id_or_question")
    if not live():
        if body.question_id is None:   # free text has no case id, so it is Live only
            return replay.not_recorded()
        options = {"model": body.model} if body.model != DEFAULT_MODEL else {}
        return replay.lookup("handbook", replay.key("answer", body.question_id, **options)) or replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    if body.question_id is not None and body.question_id not in QUESTIONS:
        return error(404, "unknown_question")
    expected = QUESTIONS.get(body.question_id)
    question = expected["question"] if expected else body.question
    parsed, retrieved = dock.answer(question, model=body.model)
    got = {**parsed.model_dump(), "retrieved": retrieved}
    return {"question_id": body.question_id, "question": question, **got, "model": body.model,
            "expected": expected and {"sources": expected["sources"], "must_contain": expected["must_contain"]},
            "checks": checks(expected, got) if expected else None, "mode": mode(), "meter": meter()}
