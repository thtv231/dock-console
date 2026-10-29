"""GET /api/data/{name}: Dock's made-up datasets, as stored in dock/."""
import json
from pathlib import Path

from fastapi import APIRouter

from api.core.mode import error

DOCK = Path(__file__).resolve().parents[2] / "dock"
DATASETS = {
    "inbox": "inbox.json",
    "inbox_truth": "inbox_truth.json",
    "handbook": "handbook.json",
    "questions": "questions.json",
    "bad": "bad.json",
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
