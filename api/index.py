"""Dock Console API: the FastAPI app Vercel serves at /api/*, and uvicorn serves locally."""
from importlib.metadata import version

from fastapi import FastAPI
from mistralai.client.errors import MistralError, NoResponseError

from api.core import replay
from api.core.mode import ModeMiddleware, error, mode
from api.routes import data, documents, guardrails, handbook, triage
from dock._client import NoKey, scrub

app = FastAPI(title="Dock Console API", docs_url="/api/docs", openapi_url="/api/openapi.json",
              redoc_url=None, swagger_ui_oauth2_redirect_url=None)   # everything outside /api belongs to Next
app.add_middleware(ModeMiddleware)
app.include_router(data.router)
app.include_router(triage.router)
app.include_router(handbook.router)
app.include_router(guardrails.router)
app.include_router(documents.router)


@app.get("/api/health")
def health():
    return {"ok": True, "sdk_version": version("mistralai"), "replay_files": replay.files(), "mode": mode()}


@app.exception_handler(NoKey)
async def no_key(request, exc):
    return error(401, "no_key")


@app.exception_handler(MistralError)
async def mistral_error(request, exc):
    # Mistral's own status for a bad key or a rate limit, so the UI can say which; anything else is a 502.
    status = exc.raw_response.status_code if exc.raw_response is not None else 502
    return error(status if status in (401, 403, 429) else 502, "mistral_error",
                 mistral_status=status, message=scrub(exc.message)[:300])


@app.exception_handler(NoResponseError)
async def no_response(request, exc):
    return error(504, "mistral_unreachable")
