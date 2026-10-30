"""Dock Console API: the FastAPI app Vercel serves at /api/*, and uvicorn serves locally."""
from importlib.metadata import version

from fastapi import FastAPI
from starlette.routing import Route
from mistralai.client.errors import MistralError, NoResponseError

from api.core import replay
from api.core.mode import ModeMiddleware, error, mode
from api.routes import (agents, approvals, data, documents, guardrails, handbook, ops, overview, scorecard,
                        triage, voice)
from dock import mcp_server
from dock._client import NoKey, scrub

# Dock's MCP server at /api/mcp. Stateless with JSON replies: no session survives between serverless instances.
# Its lifespan runs the session manager, so it is FastAPI's lifespan; without it /api/mcp answers 500.
mcp_app = mcp_server.mcp.http_app(path="/api/mcp", stateless_http=True, json_response=True)

# Everything outside /api belongs to Next.
app = FastAPI(title="Dock Console API", docs_url="/api/docs", openapi_url="/api/openapi.json",
              redoc_url=None, swagger_ui_oauth2_redirect_url=None, lifespan=mcp_app.lifespan)
app.add_middleware(ModeMiddleware)
app.include_router(data.router)
app.include_router(triage.router)
app.include_router(handbook.router)
app.include_router(guardrails.router)
app.include_router(documents.router)
app.include_router(agents.router)
app.include_router(approvals.router)
app.include_router(voice.router)
app.include_router(scorecard.router)
app.include_router(ops.router)
app.include_router(overview.router)
app.router.routes.append(Route("/api/mcp", mcp_app))   # the whole MCP app (its middleware too), at exactly /api/mcp


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
