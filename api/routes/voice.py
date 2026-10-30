"""POST /api/voice/run and /upload: S9, a voicemail transcribed, noted and answered by voice, via dock.voice.

A recorded reply is a WAV in replays/voice/, served by URL. A live reply comes back as base64 in the response
(Vercel has no disk to write it to), and the recorder turns that into a file and a URL.
"""
import base64
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from api.core import ratelimit, replay
from api.core.mode import error, mode
from dock import voice
from dock._client import live, meter

AUDIO = replay.REPLAYS / "voice"
MAX_UPLOAD = 4 * 1024 * 1024   # bytes of audio; under Vercel's 4.5 MB request limit
FORM_SLACK = 64 * 1024         # the multipart boundaries and form fields around the audio
FIELDS = ["shipment_id", "issue", "callback"]
STAGES = {"transcribe": "audio.transcriptions.complete", "understand": "chat.parse",
          "reply": "chat.complete", "speech": "audio.speech.complete"}
RECORDED_WAV = re.compile(r"^C\d+_(bias|nobias)(_run\d+)?\.wav$")
RUNS = 5   # recorded runs per call, with and without bias: one run cannot show what bias changes
NOTE_CALLS = ("C2",)   # calls whose recorded transcripts are noted again, to tell hearing from understanding

router = APIRouter()


def audio_name(call_id, bias, run=1):
    return f"{call_id}_{'bias' if bias else 'nobias'}{'' if run == 1 else f'_run{run}'}.wav"


def run_key(call_id, bias, run=1, speak=True):
    """voice|C2| is run 1 without bias; voice|C2|run=3 and voice|C2|bias=1 are the others."""
    return replay.key("voice", call_id, bias=1 if bias else None, run=None if run == 1 else run,
                      speak=None if speak else 0)


def handle(file_name, content, bias, speak, call_id=None):
    """The s09 pipeline on one recording: transcribe, note, reply text, and the reply spoken."""
    transcript, _ = voice.transcribe(file_name, content, bias=voice.BIAS if bias else None)
    note, _ = voice.note(transcript)
    reply_text = voice.reply(transcript)
    audio = None
    if speak:
        voice_id = voice.voices()[voice.VOICE]   # listed before the speech is timed, as in s09
        audio, _ = voice.speak(reply_text, voice_id)
    note = note.model_dump()
    truth = voice.TRUTH.get(call_id)
    calls = meter()
    return {"call_id": call_id, "bias": bias, "speak": speak, "transcript": transcript, "note": note,
            "truth": truth and {f: truth[f] for f in FIELDS},
            "score_vs_truth": {f: note[f] == truth[f] for f in FIELDS} if truth else None,
            "reply_text": reply_text, "voice": voice.VOICE if speak else None,
            "audio_b64": base64.b64encode(audio).decode() if audio else None, "audio_bytes": len(audio or b""),
            "stages": {stage: next((c["ms"] for c in calls if c["op"] == op), None) for stage, op in STAGES.items()},
            "mode": mode(), "meter": calls}


class RunIn(BaseModel):
    call_id: str = Field(max_length=8)
    bias: bool = False
    speak: bool = True
    run: int = Field(1, ge=1, le=RUNS)   # which recorded run (Replay); a live call is always a new run


@router.post("/api/voice/run")
async def run(body: RunIn, request: Request):
    if not live():
        return replay.lookup("voice", run_key(body.call_id, body.bias, body.run, body.speak)) or replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    if body.call_id not in voice.TRUTH:
        return error(404, "unknown_call")
    path = voice.CALLS / f"{body.call_id}.wav"
    return await run_in_threadpool(handle, path.name, path.read_bytes(), body.bias, body.speak, body.call_id)


def note_key(call_id, source, run=1):
    """note|C2| is note() run 1 on the no-bias transcript; note|C2|run=3,source=bias the third on the bias one."""
    return replay.key("note", call_id, source=None if source == "nobias" else source, run=None if run == 1 else run)


class NoteIn(BaseModel):
    call_id: str = Field(max_length=8)
    source: Literal["nobias", "bias"] = "nobias"
    run: int = Field(1, ge=1, le=RUNS)


@router.post("/api/voice/note")
def note_again(body: NoteIn, request: Request):
    """Only the understand step: note() on a transcript already recorded (run 1 of that source), no audio.
    If the issue changes on the same words, the miss is in understanding, not in hearing."""
    if not live():
        return replay.lookup("voice", note_key(body.call_id, body.source, body.run)) or replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    heard = replay.load("voice").get(run_key(body.call_id, body.source == "bias"))
    if heard is None:
        return error(404, "no_recorded_transcript")
    note, _ = voice.note(heard["transcript"])
    note = note.model_dump()
    truth = voice.TRUTH[body.call_id]
    return {"call_id": body.call_id, "source": body.source, "run": body.run, "transcript": heard["transcript"],
            "note": note, "truth": {f: truth[f] for f in FIELDS},
            "score_vs_truth": {f: note[f] == truth[f] for f in FIELDS}, "mode": mode(), "meter": meter()}


def counted(rows, truth):
    issues = {}
    for r in rows:
        issues[r["note"]["issue"]] = issues.get(r["note"]["issue"], 0) + 1
    return {"runs": len(rows), "right": {f: sum(r["note"][f] == truth[f] for r in rows) for f in FIELDS},
            "issues": issues}


@router.get("/api/voice/runs")
def runs():
    """Every recorded run counted against the truth, per call and in total, with and without bias; and note() run
    again on one recorded transcript per source, with the distinct transcripts it was given."""
    records = replay.load("voice")
    out = {"calls": {}, "totals": {}, "notes": {}}
    for variant, bias in (("nobias", False), ("bias", True)):
        total = {"runs": 0, "right": dict.fromkeys(FIELDS, 0)}
        for call_id in voice.TRUTH:
            mine = [r for n in range(1, RUNS + 1) if (r := records.get(run_key(call_id, bias, n)))]
            c = counted(mine, voice.TRUTH[call_id])
            out["calls"].setdefault(call_id, {})[variant] = c
            total["runs"] += c["runs"]
            total["right"] = {f: total["right"][f] + c["right"][f] for f in FIELDS}
        out["totals"][variant] = total
    for call_id in NOTE_CALLS:
        for source in ("nobias", "bias"):
            mine = [r for n in range(1, RUNS + 1) if (r := records.get(note_key(call_id, source, n)))]
            if mine:
                out["notes"].setdefault(call_id, {})[source] = {
                    **counted(mine, voice.TRUTH[call_id]), "transcripts": sorted({r["transcript"] for r in mine})}
    return {**out, "mode": "replay", "meter": []}


@router.post("/api/voice/upload")
async def upload(request: Request):
    """A live mic recording (multipart: file, bias, speak). The size is checked before anything goes to Mistral."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD + FORM_SLACK:
        return error(413, "audio_too_large", max_bytes=MAX_UPLOAD)
    form = await request.form(max_part_size=MAX_UPLOAD + 1)
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        return error(422, "need_file")
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        return error(413, "audio_too_large", max_bytes=MAX_UPLOAD)
    if not content:
        return error(422, "empty_file")
    if not live():   # a recording made in the browser has no case id, so it is Live only
        return replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    suffix = Path(file.filename or "").suffix.lower()
    file_name = "mic" + (suffix if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix) else ".wav")
    flag = lambda name, default: str(form.get(name, default)).lower() in ("1", "true", "yes", "on")
    return await run_in_threadpool(handle, file_name, content, flag("bias", "true"), flag("speak", "true"))


@router.get("/api/files/voice/{name}")
def reply_audio(name: str):
    """A recorded spoken reply, by its own file name only (C2_bias.wav): no other path reaches the disk."""
    if not RECORDED_WAV.match(name) or not (AUDIO / name).is_file():
        return error(404, "unknown_audio")
    return FileResponse(AUDIO / name, media_type="audio/wav")


@router.get("/api/files/calls/{call_id}")
def call_audio(call_id: str):
    """One of the six voicemails, by its id in calls/truth.json."""
    if call_id not in voice.TRUTH:
        return error(404, "unknown_call")
    return FileResponse(voice.CALLS / f"{call_id}.wav", media_type="audio/wav")
