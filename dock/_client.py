"""The Mistral client Dock calls, built per request from the visitor's key, with a meter of every SDK call.

    from dock._client import client                 # what dock/app.py calls, unchanged: client.chat.parse(...)
    async with request_scope(key) as meter: ...     # what the API wraps each request in

Inside a request scope the key comes only from that scope: no key means NoKey, never a fallback.
Outside any scope (a notebook, a script) the developer's MISTRAL_API_KEY is used, as app.py did before.
"""
import inspect
import os
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar

from mistralai.client import Mistral
from mistralai.client.basesdk import BaseSDK
from mistralai.client.utils import BackoffStrategy, RetryConfig

# One request's key, client and meter. A mutable dict, so the threadpool's copy of the context shares it.
_SCOPE = ContextVar("dock_scope", default=None)
_env_client = None


class NoKey(Exception):
    """A Mistral call was attempted with no key (Replay mode)."""


def _build(key):
    # app.py's retry and timeout, capped at 50 s so a call ends before Vercel's 60 s maxDuration.
    return Mistral(api_key=key, timeout_ms=50000,
                   retry_config=RetryConfig("backoff", BackoffStrategy(1000, 20000, 2.0, 50000),
                                            retry_connection_errors=True))


def _current():
    global _env_client
    scope = _SCOPE.get()
    if scope is None:
        if _env_client is None:
            if not os.environ.get("MISTRAL_API_KEY"):
                raise NoKey()
            _env_client = _build(os.environ["MISTRAL_API_KEY"])
        return _env_client
    if scope["client"] is None:
        if not scope["key"]:
            raise NoKey()
        scope["client"] = _build(scope["key"])
    return scope["client"]


@asynccontextmanager
async def request_scope(key=None):
    """Bind one request's key; yield its meter. The client is closed and the key dropped on exit."""
    scope = {"key": key, "client": None, "meter": []}
    token = _SCOPE.set(scope)
    try:
        yield scope["meter"]
    finally:
        _SCOPE.reset(token)
        built = scope["client"]
        scope.clear()
        if built is not None:
            built.__exit__(None, None, None)
            await built.__aexit__(None, None, None)


def live():
    scope = _SCOPE.get()
    return bool(scope and scope.get("key"))


def meter():
    scope = _SCOPE.get()
    return list(scope["meter"]) if scope else []


def scrub(text):
    """The text with this request's key masked, for anything that came back from the SDK."""
    scope = _SCOPE.get()
    key = scope and scope.get("key")
    return text.replace(key, "***") if key else text


def _usage(result):
    u = getattr(result, "usage", None) or getattr(result, "usage_info", None)
    return {"tokens": getattr(u, "total_tokens", None),
            "tokens_in": getattr(u, "prompt_tokens", None),
            "tokens_out": getattr(u, "completion_tokens", None)}


def _record(op, kwargs, started, result=None, error=None):
    scope = _SCOPE.get()
    if scope is None:
        return
    entry = {"op": op, "model": kwargs.get("model"), "ms": round((time.perf_counter() - started) * 1000)}
    entry.update(_usage(result) if error is None else {"error": type(error).__name__})
    scope["meter"].append(entry)


def _metered(fn, op):
    if inspect.iscoroutinefunction(fn):
        async def call(*args, **kwargs):
            started = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except Exception as e:
                _record(op, kwargs, started, error=e)
                raise
            _record(op, kwargs, started, result)
            return result
    else:
        def call(*args, **kwargs):
            started = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                _record(op, kwargs, started, error=e)
                raise
            _record(op, kwargs, started, result)
            return result
    return call


class _Proxy:
    """client.chat.parse(...) resolves to this request's Mistral, and the call lands in its meter as 'chat.parse'."""

    def __init__(self, target, path):
        self._target, self._path = target, path

    def __getattr__(self, name):
        value = getattr(self._target() if self._path == "" else self._target, name)
        op = f"{self._path}.{name}" if self._path else name
        if isinstance(value, BaseSDK):
            return _Proxy(value, op)
        if callable(value) and not inspect.isclass(value):
            return _metered(value, op)
        return value


client = _Proxy(_current, "")
