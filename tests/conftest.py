"""Tests run in Replay with no key: keep the developer's .env out of the test process."""
import os
import tempfile

import dotenv
import pytest

os.environ.pop("MISTRAL_API_KEY", None)
dotenv.load_dotenv = lambda *args, **kwargs: False   # dock/app.py would otherwise find the repo .env


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from api.index import app
    return TestClient(app)


@pytest.fixture
def replayed(client):
    """POST one dataset case with no key and return the recorded response, failing plainly if there is none."""
    def post(path, body):
        r = client.post(path, json=body)
        assert r.status_code == 200, f"{path} {body}: {r.status_code} {r.text[:200]}"
        got = r.json()
        assert got.get("mode") == "replay", f"{path} {body}: mode={got.get('mode')!r}"
        return got
    return post


os.environ.setdefault("DOCK_STATE_SECRET", "dock-tests-only-state-secret")   # not a real secret
os.environ["DOCK_OUTBOX"] = tempfile.mkdtemp(prefix="dock-outbox-")   # never the repo: read-only on Vercel


@pytest.fixture
def fake_conversations(monkeypatch):
    """Stand in for Mistral's conversations API on a resume: the first set of results for a tool call ends the run,
    a second set for the same call is refused, as Mistral refuses a pause answered twice.
    Returns the inputs each append sent, so a test can read the function results the server gave back."""
    import json

    import httpx2

    sent, answered = [], set()

    def reply(request):
        body = json.loads(request.content or b"{}")
        inputs = body.get("inputs") or []
        sent.append(inputs)
        ids = {i.get("tool_call_id") for i in inputs if i.get("type") == "function.result"}
        if ids & answered:   # what Mistral returned when a recorded pause was answered twice (lesson 4 probe)
            return httpx2.Response(400, request=request, json={
                "object": "Error", "type": "invalid_request_error", "code": 3001,
                "message": f"Function results reference tool_call_ids that already have a result: {sorted(ids & answered)}"})
        answered.update(ids)
        cid = request.url.path.rsplit("/", 1)[-1]
        return httpx2.Response(200, request=request, json={
            "object": "conversation.response", "conversation_id": cid,
            "outputs": [{"object": "entry", "type": "message.output", "id": "msg_fake",
                         "created_at": "2026-10-30T00:00:00Z", "content": "Done."}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    def handle(self, request):
        return reply(request)

    async def handle_async(self, request):
        return reply(request)

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", handle)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", handle_async)
    return sent
