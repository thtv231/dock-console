"""Dock works with no MISTRAL_API_KEY, and the visitor's X-Mistral-Key never comes back out."""
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKE_KEY = "dock-test-key-0000000000000000"   # not a real key


def test_dock_imports_without_key():
    env = {k: v for k, v in os.environ.items() if k != "MISTRAL_API_KEY"}
    code = "import dotenv; dotenv.load_dotenv = lambda *a, **k: False; import dock.app"
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.fixture
def fake_mistral(monkeypatch):
    """Stand in for api.mistral.ai: answer 401 and echo the bearer token, the worst case for a leak."""
    def reply(request):
        return httpx.Response(401, request=request,
                              json={"message": "Unauthorized: " + request.headers.get("authorization", "")})

    def handle(self, request):
        return reply(request)

    async def handle_async(self, request):
        return reply(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle_async)


def _requests():
    """Every route on the app, plus the lesson-1 routes with real inputs and a path that doesn't exist."""
    from fastapi.routing import APIRoute
    from api.index import app

    found = [(m, r.path_format.replace("{", "").replace("}", ""), {})
             for r in app.routes if isinstance(r, APIRoute) for m in r.methods if m != "HEAD"]
    paths = {p for _, p, _ in found}
    assert {"/api/health", "/api/triage"} <= paths, f"lesson-1 routes missing, app has {sorted(paths)}"
    return found + [
        ("GET", "/api/health", None),
        ("GET", "/api/data/inbox", None),
        ("POST", "/api/triage", {"mail_id": "M01"}),
        ("POST", "/api/triage", {"mail_id": "M999"}),
        ("POST", "/api/triage", {"mail": {"subject": "Where is HF-26012?", "body": "Hi, where is it?"}}),
        ("GET", "/api/does-not-exist", None),
    ]


def test_key_never_in_response(client, fake_mistral):
    leaks = []
    for method, path, body in _requests():
        r = client.request(method, path, json=body, headers={"X-Mistral-Key": FAKE_KEY})
        if FAKE_KEY in r.text:
            leaks.append(f"{method} {path} -> body")
        if any(FAKE_KEY in v for v in r.headers.values()):
            leaks.append(f"{method} {path} -> header value")
    assert not leaks, leaks


def test_header_never_echoed(client, fake_mistral):
    echoed = []
    for method, path, body in _requests():
        r = client.request(method, path, json=body, headers={"X-Mistral-Key": FAKE_KEY})
        if "x-mistral-key" in r.headers:
            echoed.append(f"{method} {path}")
    assert not echoed, echoed
