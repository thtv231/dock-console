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


def decided(pending, decisions):
    """One pause's answers by recipient, never by a draft's position: "audit@…=reject,import@…=approve".
    Several drafts to one recipient share its one decision."""
    recipients = sorted({json.loads(p["arguments"])["to"] for p in pending})
    return ",".join(f"{to}={decisions[to]}" for to in recipients)


def approvals_key(shipment_id, instructions, run, pause, pending, decisions):
    """approvals|HF-26038|safe|<run>|<pause>|<decided>. A replay plays one recorded run (consignee-1 … none-5) from
    start to finish, so every pause shows that run's own drafts. Pause 0 is /start."""
    return f"approvals|{shipment_id}|{instructions}|{run}|{pause}|{decided(pending, decisions)}"


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
