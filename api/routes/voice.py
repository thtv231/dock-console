"""POST /api/voice/run and /upload: S9, a voicemail transcribed, noted and answered by voice, via dock.voice.

A recorded reply is a WAV in replays/voice/, served by URL. A live reply comes back as base64 in the response
(Vercel has no disk to write it to), and the recorder turns that into a file and a URL.
"""
import base64
import re
from pathlib import Path

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
RECORDED_WAV = re.compile(r"^C\d+_(bias|nobias)\.wav$")

router = APIRouter()


def audio_name(call_id, bias):
    return f"{call_id}_{'bias' if bias else 'nobias'}.wav"


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


@router.post("/api/voice/run")
async def run(body: RunIn, request: Request):
    if not live():
        options = {"bias": 1 if body.bias else None, "speak": None if body.speak else 0}
        return replay.lookup("voice", replay.key("voice", body.call_id, **options)) or replay.not_recorded()

    if not ratelimit.allow(ratelimit.client_ip(request)):
        return error(429, "rate_limited")
    if body.call_id not in voice.TRUTH:
        return error(404, "unknown_call")
    path = voice.CALLS / f"{body.call_id}.wav"
    return await run_in_threadpool(handle, path.name, path.read_bytes(), body.bias, body.speak, body.call_id)


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
