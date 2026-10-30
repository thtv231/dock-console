"""State the browser holds between /api/approvals/start and /resume: HMAC-signed JSON with an expiry.

    token = sign(payload)        # base64url(json) "." base64url(hmac-sha256), exp added to the payload
    payload = verify(token)      # BadState("bad_state" | "expired"), NoSecret without DOCK_STATE_SECRET

The payload is readable by the browser (it is only signed), so it never holds the visitor's key.
"""
import base64
import hashlib
import hmac
import json
import os
import time

from dotenv import load_dotenv

load_dotenv()
TTL_S = 30 * 60   # a person has half an hour to decide; Mistral keeps the conversation longer than that


class NoSecret(Exception):
    """DOCK_STATE_SECRET is not set, so a state can be neither signed nor checked."""


class BadState(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _secret():
    secret = os.environ.get("DOCK_STATE_SECRET")
    if not secret:
        raise NoSecret()
    return secret.encode()


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(body):
    return _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())


def sign(payload, ttl_s=TTL_S):
    body = _b64(json.dumps({**payload, "exp": int(time.time()) + ttl_s}, separators=(",", ":")).encode())
    return f"{body}.{_mac(body)}"


def verify(token):
    """The payload, only if this server signed it and it has not run out. The signature is checked first."""
    body, _, sig = token.rpartition(".")
    if not body or not hmac.compare_digest(sig, _mac(body)):
        raise BadState("bad_state")
    try:
        payload = json.loads(_unb64(body))
    except ValueError:
        raise BadState("bad_state")
    if not isinstance(payload, dict) or not isinstance(payload.get("exp"), int):
        raise BadState("bad_state")
    if payload["exp"] < time.time():
        raise BadState("expired")
    return payload
