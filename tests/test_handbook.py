"""Handbook (S3): every recorded answer agrees with the answer key in dock/questions.json."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = json.loads((ROOT / "dock" / "questions.json").read_text(encoding="utf-8"))
NOT_IN_HANDBOOK = {"Q13", "Q14"}


def test_every_question_is_recorded(client):
    missing = []
    for q in QUESTIONS:
        r = client.post("/api/answer", json={"question_id": q["id"]})
        if r.status_code != 200 or r.json().get("mode") != "replay":
            missing.append(f"{q['id']}: {r.status_code} {r.text[:80]}")
    assert not missing, "\n".join(missing)


@pytest.mark.parametrize("qid", sorted(NOT_IN_HANDBOOK))
def test_not_in_handbook_is_not_found(replayed, qid):
    got = replayed("/api/answer", {"question_id": qid})
    assert got["found"] is False, got["answer"]


@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q: q["id"])
def test_sources_were_retrieved(replayed, q):
    got = replayed("/api/answer", {"question_id": q["id"]})
    assert set(got["sources"]) <= set(got["retrieved"]), f"cites {got['sources']}, retrieved {got['retrieved']}"


@pytest.mark.parametrize("q", [q for q in QUESTIONS if q["must_contain"]], ids=lambda q: q["id"])
def test_answer_has_must_contain_words(replayed, q):
    """Each must_contain entry is a set of alternatives: "fourth|4th" passes on either word."""
    got = replayed("/api/answer", {"question_id": q["id"]})
    text = got["answer"].lower()
    missing = [words for words in q["must_contain"] if not any(w in text for w in words.split("|"))]
    assert not missing, f"{missing} not in: {got['answer']}"


@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q: q["id"])
def test_retrieved_sections_have_scores_best_first(replayed, q):
    got = replayed("/api/answer", {"question_id": q["id"]})
    scores = [got["scores"][i] for i in got["retrieved"]]
    assert len(scores) == 3 and scores == sorted(scores, reverse=True), got["scores"]
