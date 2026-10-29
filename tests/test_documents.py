"""Documents (S2): the recorded extractions find exactly the problems the answer key in dock/documents/truth.json flags."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TRUTH = json.loads((ROOT / "dock" / "documents" / "truth.json").read_text(encoding="utf-8"))
INVOICES = sorted(n for n in TRUTH if n.startswith("invoice_"))
BILLS = sorted(n for n in TRUTH if n.startswith("bill_of_lading_"))


def extract(replayed, name):
    return replayed("/api/documents/extract", {"name": name, "schema": TRUTH[name]["kind"]})


def test_invoice_07_total_mismatch(replayed):
    got = extract(replayed, "invoice_07_scan_mismatch.pdf")
    assert "total should be 3,862.32, printed 8,362.32" in got["problems"], got["problems"]
    assert got["against_database"] == "database says 3,862.32"


@pytest.mark.parametrize("name", INVOICES[:6])
def test_clean_invoice_has_no_problems(replayed, name):
    assert extract(replayed, name)["problems"] == []


@pytest.mark.parametrize("name", BILLS)
def test_bill_of_lading_check_digit(replayed, name):
    got = extract(replayed, name)
    assert got["check_digit_ok"] is not ("bad_check_digit" in TRUTH[name]["flags"]), got["fields"]["container_no"]


@pytest.mark.parametrize("box", ["HRBU415369O", "hrbu4153690", "HRBU-153690", "HRBU 153690", "HRBU41536", ""])
def test_misread_container_is_a_problem_not_a_crash(box):
    """OCR can misread a container number (O for 0, lower case, a stray mark): problems() reports it, never raises."""
    from dock import documents
    t = TRUTH["invoice_01_clean.pdf"]
    fields = {k: t[k] for k in documents.Invoice.model_fields if k in t}
    inv = documents.Invoice(**{**fields, "container_no": box}, incoterm_place="Los Angeles")
    assert documents.problems(inv) == [f"container {box} fails the check digit"]


def test_pdf_is_served_by_name_only(client):
    r = client.get("/api/files/documents/invoice_07_scan_mismatch.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:5] == b"%PDF-"
    for name in ["truth.json", "..%2Fdock.sqlite", "nope.pdf"]:
        assert client.get(f"/api/files/documents/{name}").status_code == 404, name
