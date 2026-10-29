"""POST /api/documents/extract: S2, typed fields from one PDF plus the checks on them, via dock.documents."""
import json
from typing import Literal, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.core import ratelimit, replay
from api.core.mode import error, mode
from dock import documents as docs
from dock._client import live, meter

TRUTH = json.loads((docs.DOCUMENTS / "truth.json").read_text(encoding="utf-8"))
SCHEMAS = {"v1": docs.InvoiceV1, "invoice": docs.Invoice, "bill_of_lading": docs.BillOfLading}
LOW_WORDS = 5   # s02 step 5 prints the five least sure words

router = APIRouter()


class ExtractIn(BaseModel):
    name: str = Field(max_length=64)
    schema_: Optional[Literal["v1", "invoice", "bill_of_lading"]] = Field(None, alias="schema")


def container_ok(box):
    """s02's check digit, guarded so a misread box is a failed check, not a crash."""
    return len(box) == 11 and box[:10].isalnum() and box[10].isdigit() and docs.check_digit(box[:10]) == int(box[10])


@router.post("/api/documents/extract")
def extract(body: ExtractIn, request: Request):
    if body.name not in TRUTH:
        return error(404, "unknown_document")
    truth = TRUTH[body.name]
    schema = body.schema_ or truth["kind"]
    if not live():
        options = {"schema": schema} if schema != truth["kind"] else {}
        return replay.lookup("documents", replay.key("extract", body.name, **options)) or replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    if (schema == "bill_of_lading") != (truth["kind"] == "bill_of_lading"):
        return error(422, "wrong_schema_for_document", kind=truth["kind"])
    path = docs.DOCUMENTS / body.name
    page = docs.ocr(path).pages[0]
    scores = page.confidence_scores
    words = sorted(scores.word_confidence_scores, key=lambda w: w.confidence)
    fields = docs.extract(path, SCHEMAS[schema])

    invoice = schema != "bill_of_lading"
    digit_ok = container_ok(fields.container_no)
    found = docs.problems(fields) if invoice else None
    flagged = bool(found) if invoice else not digit_ok
    return {"name": body.name, "schema": schema, "fields": fields.model_dump(),
            "ocr": {"avg": scores.average_page_confidence_score, "min": scores.minimum_page_confidence_score,
                    "low_words": [{"text": w.text, "confidence": w.confidence} for w in words[:LOW_WORDS]],
                    "markdown": page.markdown},
            "problems": found, "against_database": docs.against_database(fields) if invoice else None,
            "check_digit_ok": digit_ok,
            "truth": {"wrong_fields": [f for f in docs.FIELDS[SCHEMAS[schema]] if getattr(fields, f) != truth[f]],
                      "flags": truth["flags"], "flag_right": flagged == bool(truth["flags"])},
            "mode": mode(), "meter": meter()}
