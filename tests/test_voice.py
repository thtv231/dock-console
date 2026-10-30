"""Voice desk (S9): six driver voicemails are transcribed, turned into a call note and answered by voice.
Without context bias C2 comes back as an address problem (the notebook's miss); with bias every note matches the truth."""
import io
import json
import re
import wave
from pathlib import Path

import pytest

from api.core import replay

ROOT = Path(__file__).resolve().parent.parent
TRUTH = json.loads((ROOT / "dock" / "calls" / "truth.json").read_text(encoding="utf-8"))
CALLS = sorted(TRUTH)
FIELDS = ["shipment_id", "issue", "callback"]
VOICE = ROOT / "replays" / "voice.json"
LIVE = {"X-Mistral-Key": "dock-test-key-0000000000000000"}   # not a real key: it only switches a request to Live
MAX_UPLOAD = 4 * 1024 * 1024   # PLAN.md: a live mic upload is at most 4 MB, under Vercel's 4.5 MB request limit
BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{2000,}")


def run(replayed, call_id, bias):
    return replayed("/api/voice/run", {"call_id": call_id, "bias": bias})


def recorded():
    assert VOICE.exists(), f"{VOICE.relative_to(ROOT)} not recorded yet (python scripts/record.py voice)"
    return json.loads(VOICE.read_text(encoding="utf-8"))


def test_the_fixtures_are_what_the_tests_assume():
    assert CALLS == ["C1", "C2", "C3", "C4", "C5", "C6"]
    assert TRUTH["C2"]["issue"] == "question"


@pytest.mark.parametrize("bias", [False, True], ids=["no_bias", "bias"])
@pytest.mark.parametrize("call_id", CALLS)
def test_every_call_has_a_recorded_run(replayed, call_id, bias):
    assert replay.key("voice", call_id, bias=1 if bias else None) in recorded()
    got = run(replayed, call_id, bias)
    assert got["call_id"] == call_id and got["bias"] is bias
    assert got["transcript"] and got["reply_text"], got.get("error")
    assert set(FIELDS) <= set(got["note"]), got["note"]
    assert got["score_vs_truth"] == {f: got["note"][f] == TRUTH[call_id][f] for f in FIELDS}, got["score_vs_truth"]


def test_without_bias_c2_is_heard_as_an_address_problem(replayed):
    """s09 step 4: the gate problem reads as a wrong address until Voxtral is told what words to expect."""
    note = run(replayed, "C2", False)["note"]
    assert note["issue"] == "address", f"C2 without bias: issue {note['issue']!r}; the recorded run should show the miss"


def test_with_bias_every_note_matches_the_truth(replayed):
    wrong = []
    for call_id in CALLS:
        note = run(replayed, call_id, True)["note"]
        wrong += [f"{call_id} {f}: {note[f]!r}, expected {TRUTH[call_id][f]!r}"
                  for f in FIELDS if note[f] != TRUTH[call_id][f]]
    assert not wrong, "\n".join(wrong)


def test_reply_audio_is_a_wav_url_not_base64(client):
    for k, record in recorded().items():
        assert "audio_b64" not in record, f"{k}: reply audio stored as base64"
        assert not BASE64_RUN.search(json.dumps(record)), f"{k}: a long base64 run is in the record"
        url = record.get("audio_url")
        assert url and url.startswith("/api/") and url.endswith(".wav"), f"{k}: audio_url {url!r}"
        assert (ROOT / "replays" / "voice" / url.rsplit("/", 1)[-1]).exists(), f"{k}: {url} is not in replays/voice/"
        r = client.get(url)
        assert r.status_code == 200 and r.headers["content-type"].startswith("audio/"), f"{k}: GET {url} {r.status_code}"
        with wave.open(io.BytesIO(r.content)) as w:
            assert w.getnframes() > 0, f"{k}: {url} is an empty WAV"


# ---------- Live mic upload: the size is checked before any Mistral call ----------
def wav_of(size):
    """A silent 16 kHz mono 16-bit WAV of exactly `size` bytes (44-byte header + frames)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0" * (size - 44))
    assert len(buffer.getvalue()) == size
    return buffer.getvalue()


@pytest.fixture
def mistral_calls(monkeypatch):
    """Every request that would leave for Mistral, refused with a 401 (not retried) so a test never waits.
    The rate limit is off: earlier live tests in this process have spent the test client's tokens."""
    import httpx2

    monkeypatch.setenv("DOCK_LIVE_PER_MIN", "0")
    calls = []

    def refuse(request):
        calls.append(str(request.url))
        return httpx2.Response(401, request=request, json={"message": "Unauthorized", "request_id": "fake"})

    async def refuse_async(self, request):
        return refuse(request)

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", lambda self, request: refuse(request))
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", refuse_async)
    return calls


def upload(client, audio):
    return client.post("/api/voice/upload", headers=LIVE, data={"bias": "true"},
                       files={"file": ("mic.wav", audio, "audio/wav")})


def test_an_upload_over_4_mb_gets_413_without_calling_mistral(client, mistral_calls):
    r = upload(client, wav_of(MAX_UPLOAD + 2))
    assert r.status_code == 413, f"{r.status_code} {r.text[:200]}"
    assert r.json()["meter"] == [] and mistral_calls == [], mistral_calls


def test_an_upload_of_4_mb_reaches_mistral(client, mistral_calls):
    """The limit is the reason for the 413 above: at exactly 4 MB the audio goes to transcription."""
    r = upload(client, wav_of(MAX_UPLOAD))
    assert r.status_code != 413, r.text[:200]
    assert any("/audio/transcriptions" in url for url in mistral_calls), mistral_calls
