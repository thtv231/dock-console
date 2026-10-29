"""The meter counts the retries the SDK makes inside one call, and keeps each concurrent call's count its own."""
import asyncio

import httpx2
import pytest

FAKE_KEY = "dock-test-key-0000000000000000"   # not a real key
COMPLETION = {"id": "cmpl-1", "object": "chat.completion", "model": "mistral-small-latest", "created": 0,
              "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
              "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]}


@pytest.fixture
def fake_mistral(monkeypatch):
    """Answer 429 the first time a message saying "busy" arrives, 200 every other time."""
    seen = []

    async def handle(self, request):
        busy = b"busy" in request.content
        seen.append(busy)
        if busy and seen.count(True) == 1:
            return httpx2.Response(429, request=request, json={"message": "rate limited"})
        return httpx2.Response(200, request=request, json=COMPLETION)

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", handle)
    return seen


def test_retries_are_counted_per_call(fake_mistral):
    from dock._client import client, request_scope

    async def both():
        async with request_scope(FAKE_KEY) as meter:
            await asyncio.gather(*[client.chat.complete_async(model="mistral-small-latest",
                                                              messages=[{"role": "user", "content": text}])
                                   for text in ("busy", "quiet")])
            return list(meter)

    meter = asyncio.run(both())
    assert len(fake_mistral) == 3, fake_mistral
    assert sorted((c["retries"], c["retried"]) for c in meter) == [(0, []), (1, [429])], meter


def test_a_hung_connect_fails_fast_and_is_retried(monkeypatch):
    """The ~21 s waits in the first design C recording: an attempt whose connection never opens. Every attempt now
    gets a short connect timeout, so it fails, the SDK retries on a new connection, and the meter names the cause."""
    from dock._client import CONNECT_S, client, request_scope
    timeouts = []

    async def handle(self, request):
        timeouts.append(request.extensions["timeout"])
        if len(timeouts) == 1:
            raise httpx2.ConnectTimeout("connection never opened", request=request)
        return httpx2.Response(200, request=request, json=COMPLETION)

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", handle)

    async def one():
        async with request_scope(FAKE_KEY) as meter:
            await client.chat.complete_async(model="mistral-small-latest", messages=[{"role": "user", "content": "hi"}])
            return list(meter)

    [call] = asyncio.run(one())
    assert CONNECT_S <= 5 and all(t["connect"] == CONNECT_S for t in timeouts), timeouts
    assert all(t["read"] > CONNECT_S for t in timeouts), "only connecting is capped: a long answer may still take its time"
    assert (call["retries"], call["retried"]) == (1, ["ConnectTimeout"]), call
