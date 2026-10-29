"""Section 2: read Harbor Freight's shipping documents with Document AI, then check what was read.

    from dock import documents
    inv = documents.extract(path, documents.Invoice) · documents.problems(inv) · documents.against_database(inv)

Ported from notebooks/s02_document_ai.ipynb: schemas, descriptions and checks exactly as written there.
"""
import base64
import re
import sqlite3
from pathlib import Path
from typing import Literal

from mistralai.extra import response_format_from_pydantic_model
from pydantic import BaseModel, Field

from dock._client import client   # per-request key and meter, see dock/_client.py

HERE = Path(__file__).resolve().parent
DOCUMENTS = HERE / "documents"
db = sqlite3.connect((HERE / "dock.sqlite").as_uri() + "?mode=ro", uri=True, check_same_thread=False)


# ---------- Step 4: OCR one scanned document ----------
def as_data_url(path):
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return f"data:application/pdf;base64,{data}"


def ocr(path):
    """Steps 4-5: the page markdown, with a confidence score for every word."""
    url = as_data_url(path)
    return client.ocr.process(
        model="mistral-ocr-latest",
        document={"type": "document_url", "document_url": url},
        confidence_scores_granularity="word",
    )


# ---------- Step 6: ask for fields, a first schema ----------
class Line(BaseModel):
    description: str
    hs_code: str
    quantity: int
    unit_price: float
    amount: float


class InvoiceV1(BaseModel):
    invoice_no: str
    shipment_id: str
    container_no: str
    incoterm: str
    seller: str
    buyer: str
    lines: list[Line]
    subtotal: float
    freight: float
    insurance: float
    total: float


def extract(path, schema):
    url = as_data_url(path)
    fmt = response_format_from_pydantic_model(schema)
    response = client.ocr.process(
        model="mistral-ocr-latest",
        document={"type": "document_url", "document_url": url},
        document_annotation_format=fmt,
    )
    return schema.model_validate_json(response.document_annotation)


# ---------- Step 7: a better schema, the descriptions are the prompt ----------
class Invoice(BaseModel):
    invoice_no: str
    shipment_id: str = Field(description="Harbor Freight ref, like HF-26001")
    container_no: str = Field(description="11 characters, like HRBU4100003")
    incoterm: Literal["EXW", "FOB", "CIF", "DAP"]
    incoterm_place: str = Field(description="Place after the incoterm")
    seller: str = Field(description="Company name only, no address")
    buyer: str = Field(description="Company name only, no address")
    lines: list[Line]
    subtotal: float
    freight: float
    insurance: float
    total: float = Field(description="Grand total exactly as printed")


# ---------- Step 8: check what we read ----------
def check_digit(code10):
    values, n = {}, 10
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if n % 11 == 0:
            n += 1
        values[ch] = n
        n += 1
    digits = [values[c] if c.isalpha() else int(c) for c in code10]
    return sum(d * 2 ** i for i, d in enumerate(digits)) % 11 % 10


def container_ok(box):
    """check_digit on a box OCR may have misread: anything but 10 capitals/digits plus a digit fails, never raises."""
    return re.fullmatch(r"[A-Z0-9]{10}[0-9]", box) is not None and check_digit(box[:10]) == int(box[10])


def problems(inv):
    found = []
    lines_sum = round(sum(l.amount for l in inv.lines), 2)
    if abs(lines_sum - inv.subtotal) > 0.01:
        found.append(f"lines add up to {lines_sum:,.2f}, not {inv.subtotal:,.2f}")
    expected = round(inv.subtotal + inv.freight + inv.insurance, 2)
    if abs(expected - inv.total) > 0.01:
        found.append(f"total should be {expected:,.2f}, printed {inv.total:,.2f}")
    box = inv.container_no
    if not container_ok(box):   # the notebook's len/check_digit test raised on a misread box
        found.append(f"container {box} fails the check digit")
    return found


def against_database(inv):
    row = db.execute("SELECT total FROM invoices WHERE invoice_no = ?",
                     (inv.invoice_no,)).fetchone()
    if row is None:
        return "invoice not in database"
    if abs(row[0] - inv.total) > 0.01:
        return f"database says {row[0]:,.2f}"
    return "matches database"


# ---------- Step 9: bills of lading, same pattern, new schema ----------
class BillOfLading(BaseModel):
    bl_no: str
    shipment_id: str = Field(description="Harbor Freight ref, like HF-26001")
    shipper: str = Field(description="Company name only, no address")
    consignee: str = Field(description="Company name only, no address")
    container_no: str = Field(description="11 characters, like HRBU4100003")
    packages: int
    gross_weight_kg: float


# ---------- Step 10: score it against the answer key (the notebook scores invoices; bills get their own fields) ----------
FIELDS = {
    InvoiceV1: ["invoice_no", "shipment_id", "container_no", "incoterm",
                "seller", "buyer", "subtotal", "freight", "insurance", "total"],
    BillOfLading: ["bl_no", "shipment_id", "shipper", "consignee", "container_no", "packages", "gross_weight_kg"],
}
FIELDS[Invoice] = FIELDS[InvoiceV1]
