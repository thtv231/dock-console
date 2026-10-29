"""Replay or Live, per request.

The X-Mistral-Key header is read into dock._client's request scope and removed from the ASGI scope,
so no route, log or error handler downstream can see it. As a last line, any copy of the key in the
response (headers or body) is masked with stars of the same length, so Content-Length stays right.
"""
from fastapi.responses import JSONResponse

from dock._client import live, meter, request_scope

HEADER = b"x-mistral-key"


class ModeMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        key, headers = None, []
        for name, value in scope["headers"]:
            if name == HEADER:
                key = value.decode("latin-1").strip() or None
            else:
                headers.append((name, value))
        scope = {**scope, "headers": headers}
        secret = key.encode("latin-1") if key else None
        mask = b"*" * len(secret) if secret else None

        async def masked_send(message):
            if secret and message["type"] == "http.response.start":
                message = {**message, "headers": [(n, v.replace(secret, mask)) for n, v in message["headers"]]}
            elif secret and message["type"] == "http.response.body":
                message = {**message, "body": message.get("body", b"").replace(secret, mask)}
            await send(message)

        async with request_scope(key):
            await self.app(scope, receive, masked_send)


def mode():
    return "live" if live() else "replay"


def error(status, code, **extra):
    return JSONResponse({"error": code, **extra, "mode": mode(), "meter": meter()}, status_code=status)
