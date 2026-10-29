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
