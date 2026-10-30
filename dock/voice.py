"""Section 9: drivers leave voicemails. Dock transcribes them with Voxtral, writes a call note, and answers by voice.

    from dock import voice
    text, s = voice.transcribe("C2.wav", audio, bias=voice.BIAS) · note, s = voice.note(text)
    answer = voice.reply(text) · audio, s = voice.speak(answer, voice.voices()[voice.VOICE])

Ported from notebooks/s09_voice.ipynb: prompts, bias list and schema exactly as written.
"""
import base64
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from dock._client import client   # per-request key and meter, see dock/_client.py

HERE = Path(__file__).resolve().parent
CALLS = HERE / "calls"
TRUTH = json.loads((CALLS / "truth.json").read_text(encoding="utf-8"))
db = sqlite3.connect((HERE / "dock.sqlite").as_uri() + "?mode=ro", uri=True, check_same_thread=False)


# ---------- s09 step 2: transcribe one call (the notebook's path, as a name and its bytes) ----------
def transcribe(file_name, content, model="voxtral-mini-latest", bias=None):
    start = time.time()
    r = client.audio.transcriptions.complete(
        model=model, language="en", context_bias=bias,
        file={"file_name": file_name, "content": content})
    return r.text, time.time() - start


# ---------- s09 step 4: extract, normalize ----------
class CallNote(BaseModel):
    shipment_id: Optional[str] = Field(description="The shipment number as said, like HF-26038")
    issue: Literal["delay", "arrived", "address", "damage", "question"] = Field(
        description="delay: running late · arrived: delivered · address: wrong or closed delivery "
                    "address · damage: goods damaged · question: anything else, like a gate or paperwork problem")
    callback: bool = Field(description="false only if the driver says no reply or call back is needed")

    @field_validator("shipment_id")
    @classmethod
    def normalize_ref(cls, value):
        if value is None:
            return None
        digits = re.sub(r"\D", "", value)
        return f"HF-{digits}" if len(digits) == 5 else value


def note(text, model="mistral-small-latest"):
    start = time.time()
    r = client.chat.parse(model=model, response_format=CallNote, messages=[
        {"role": "system", "content": "You write call notes for Harbor Freight's ops team."},
        {"role": "user", "content": text}])
    return r.choices[0].message.parsed, time.time() - start


# ---------- s09 step 5: tell Voxtral what words to expect ----------
NAMES = {"C1": "Marco", "C2": "Jenny", "C3": "Tom", "C4": "Pieter", "C5": "Sam", "C6": "Ana"}
ACTIVE = [r[0] for r in db.execute("SELECT id FROM shipments WHERE status != 'delivered'")]
BIAS = list(NAMES.values()) + ["Felixstowe", "Lotus"] + ACTIVE    # one word or code per item


# ---------- s09 step 6: answer by voice ----------
REPLY = ("You are Dock. Reply to a driver's voicemail in two short sentences. "
         "Never promise a time you don't know.")
VOICE = "gb_oliver_neutral"


def reply(text, model="mistral-small-latest"):
    r = client.chat.complete(model=model, messages=[
        {"role": "system", "content": REPLY},
        {"role": "user", "content": text}])
    return r.choices[0].message.content


def voices():
    """slug -> voice id, as s09 lists them: once per run, outside the speech timing."""
    return {v.slug: v.id for v in client.audio.voices.list(limit=100).items}


def speak(text, voice_id, model="voxtral-mini-tts-latest"):
    start = time.time()
    r = client.audio.speech.complete(model=model, input=text, voice_id=voice_id,
                                     response_format="wav")
    audio = base64.b64decode(r.audio_data)
    return audio, time.time() - start
