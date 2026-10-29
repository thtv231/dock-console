"""GET /api/data/{name}: Dock's made-up datasets, as stored in dock/."""
import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from api.core.mode import error

DOCK = Path(__file__).resolve().parents[2] / "dock"
DATASETS = {
    "inbox": "inbox.json",
    "inbox_truth": "inbox_truth.json",
    "handbook": "handbook.json",
    "questions": "questions.json",
    "bad": "bad.json",
    "asks": "asks.json",
    "scorecard": "scorecard.json",
    "documents": "documents/truth.json",
    "calls": "calls/truth.json",
}

router = APIRouter()


@router.get("/api/data/{name}")
def data(name: str):
    if name not in DATASETS:
        return error(404, "unknown_dataset", known=sorted(DATASETS))
    return {"name": name, "data": json.loads((DOCK / DATASETS[name]).read_text(encoding="utf-8"))}


@router.get("/api/files/documents/{name}")
def document_file(name: str):
    """One of the shipping PDFs, by its name in documents/truth.json only: no other path reaches the disk."""
    known = json.loads((DOCK / "documents" / "truth.json").read_text(encoding="utf-8"))
    if name not in known:
        return error(404, "unknown_document")
    return FileResponse(DOCK / "documents" / name, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{name}"'})
