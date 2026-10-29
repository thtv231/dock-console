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
from types import SimpleNamespace

import httpx2   # the transport mistralai 3.6 uses (it imports httpx2 as httpx)
from mistralai.client import Mistral
from mistralai.client.basesdk import BaseSDK
from mistralai.client.utils import BackoffStrategy, RetryConfig

# One request's key, client and meter. A mutable dict, so the threadpool's copy of the context shares it.
_SCOPE = ContextVar("dock_scope", default=None)
# The HTTP attempts of the SDK call running in this task or thread: a status code each, None when no response came.
_ATTEMPTS = ContextVar("dock_attempts", default=None)
_env_client = None


class NoKey(Exception):
    """A Mistral call was attempted with no key (Replay mode)."""


def _sent(request):
    attempts = _ATTEMPTS.get()
    if attempts is not None:
        attempts.append(None)   # stays None if the attempt never gets a response (a timeout, a dropped connection)


def _answered(response):
    attempts = _ATTEMPTS.get()
    if attempts:
        attempts[-1] = response.status_code


async def _sent_async(request):
    _sent(request)


async def _answered_async(response):
    _answered(response)


def _build(key):
    # app.py's retry and timeout, capped at 50 s so a call ends before Vercel's 60 s maxDuration.
    # The SDK retries inside one call; the hooks on our own HTTP clients see every attempt, so the meter can count them.
    http = [httpx2.Client(follow_redirects=True, event_hooks={"request": [_sent], "response": [_answered]}),
            httpx2.AsyncClient(follow_redirects=True,
                               event_hooks={"request": [_sent_async], "response": [_answered_async]})]
    built = Mistral(api_key=key, timeout_ms=50000, client=http[0], async_client=http[1],
                    retry_config=RetryConfig("backoff", BackoffStrategy(1000, 20000, 2.0, 50000),
                                             retry_connection_errors=True))
    built._dock_http = http   # supplied clients are ours to close, Mistral's __exit__ leaves them open
    return built


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
    scope = {"key": key, "client": None, "meter": [], "results": []}
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
            for http in getattr(built, "_dock_http", ()):
                await http.aclose() if isinstance(http, httpx2.AsyncClient) else http.close()


def live():
    scope = _SCOPE.get()
    return bool(scope and scope.get("key"))


def meter():
    scope = _SCOPE.get()
    return list(scope["meter"]) if scope else []


def results(op):
    """This request's raw SDK results for one op, oldest first: what a route shows that a dock function doesn't return."""
    scope = _SCOPE.get()
    return [r for o, r in scope["results"] if o == op] if scope else []


def scrub(text):
    """The text with this request's key masked, for anything that came back from the SDK."""
    scope = _SCOPE.get()
    key = scope and scope.get("key")
    return text.replace(key, "***") if key else text


def _usage(result):
    u = getattr(result, "usage", None) or getattr(result, "usage_info", None)
    return {"tokens": getattr(u, "total_tokens", None),
            "tokens_in": getattr(u, "prompt_tokens", None),
            "tokens_out": getattr(u, "completion_tokens", None),
            "pages": getattr(u, "pages_processed", None)}   # OCR bills pages, not tokens


def _detail(op, result):
    """What a screen shows that the dock function doesn't return: the raw moderation flags and scores."""
    if op == "classifiers.moderate":
        return {"categories": [{k: {"flagged": bool(r.categories[k]), "score": round(r.category_scores[k], 4)}
                                for k in r.categories} for r in result.results]}
    return {}


def _retries(attempts):
    """Every failed attempt before the last one: each was retried. A run's start/append calls succeed in turn."""
    failed = [a for a in attempts[:-1] if a is None or a >= 400]
    return {"retries": len(failed), "retried": ["no response" if a is None else a for a in failed]}


def _record(op, kwargs, started, result=None, error=None, usage_of=None, attempts=()):
    scope = _SCOPE.get()
    if scope is None:
        return
    model = kwargs.get("model") or getattr(kwargs.get("run_ctx"), "model", None)
    entry = {"op": op, "model": model, "ms": round((time.perf_counter() - started) * 1000), **_retries(attempts)}
    entry.update({**_usage(usage_of or result), **_detail(op, result)} if error is None
                 else {"error": type(error).__name__})
    if error is None:
        scope["results"].append((op, result))
    scope["meter"].append(entry)


def _metered(fn, op):
    if inspect.iscoroutinefunction(fn):
        async def call(*args, **kwargs):
            started, attempts = time.perf_counter(), []
            token = _ATTEMPTS.set(attempts)
            try:
                result = await fn(*args, **kwargs)
            except Exception as e:
                _record(op, kwargs, started, error=e, attempts=attempts)
                raise
            finally:
                _ATTEMPTS.reset(token)
            _record(op, kwargs, started, result, attempts=attempts)
            return result
    else:
        def call(*args, **kwargs):
            started, attempts = time.perf_counter(), []
            token = _ATTEMPTS.set(attempts)
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                _record(op, kwargs, started, error=e, attempts=attempts)
                raise
            finally:
                _ATTEMPTS.reset(token)
            _record(op, kwargs, started, result, attempts=attempts)
            return result
    return call


class _Calls:
    """The self Conversations.run_async runs with: everything goes to the real SDK, and start/append usage is kept."""

    def __init__(self, target):
        self._target, self.usage = target, []

    def __getattr__(self, name):
        value = getattr(self._target, name)
        if name not in ("start_async", "append_async"):
            return value

        async def call(*args, **kwargs):
            response = await value(*args, **kwargs)
            self.usage.append(response.usage)
            return response
        return call


def _metered_run(conversations, op):
    """run_async returns a RunResult with no usage: its one meter entry sums the start/append calls the run made."""
    async def call(*args, **kwargs):
        calls = _Calls(conversations)
        started, attempts = time.perf_counter(), []
        token = _ATTEMPTS.set(attempts)
        try:
            result = await type(conversations).run_async(calls, *args, **kwargs)
        except Exception as e:
            _record(op, kwargs, started, error=e, attempts=attempts)
            raise
        finally:
            _ATTEMPTS.reset(token)
        used = [u for u in calls.usage if u is not None]
        total = {f: sum(getattr(u, f) or 0 for u in used) if used else None
                 for f in ("total_tokens", "prompt_tokens", "completion_tokens")}
        _record(op, kwargs, started, result, usage_of=SimpleNamespace(usage=SimpleNamespace(**total)), attempts=attempts)
        return result
    return call


class _Proxy:
    """client.chat.parse(...) resolves to this request's Mistral, and the call lands in its meter as 'chat.parse'."""

    def __init__(self, target, path):
        self._target, self._path = target, path

    def __getattr__(self, name):
        value = getattr(self._target() if self._path == "" else self._target, name)
        op = f"{self._path}.{name}" if self._path else name
        if op == "beta.conversations.run_async":
            return _metered_run(value.__self__, op)
        if isinstance(value, BaseSDK):
            return _Proxy(value, op)
        if callable(value) and not inspect.isclass(value):
            return _metered(value, op)
        return value


client = _Proxy(_current, "")
