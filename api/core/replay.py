"""Recorded real runs: replays/<capability>.json holds {"route|case_id|variant": response}."""
import json
from functools import lru_cache
from pathlib import Path

from api.core.mode import error

REPLAYS = Path(__file__).resolve().parents[2] / "replays"


def key(route, case_id, **options):
    """Options are the non-default ones only: key("triage", "M01", model="ministral-8b-latest")."""
    variant = ",".join(f"{k}={v}" for k, v in sorted(options.items()) if v is not None)
    return f"{route}|{case_id}|{variant}"


@lru_cache
def load(capability):
    path = REPLAYS / f"{capability}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def lookup(capability, k):
    record = load(capability).get(k)
    return None if record is None else {**record, "mode": "replay"}


def files():
    return sorted(p.stem for p in REPLAYS.glob("*.json"))


def not_recorded():
    return error(404, "not_recorded")
