"""strix-dash FastAPI application.

Runs as a system service on 127.0.0.1:10001, alongside the vendor's halo-lp on
:10000. Serves both the JSON API and the static frontend.

IMPORTANT: single uvicorn worker only. The cache in backend.core.cache is
per-process and provides single-flight de-duplication of subprocess calls;
running multiple workers gives each its own cache and multiplies the tool
invocations.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.core import models
from backend.core.errors import ErrorCode, ToolError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("strix-dash")

STARTED_AT = time.time()
VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the slow caches without delaying the port bind.

    rocminfo takes seconds and the capability probe stats a few dozen paths;
    doing them as background tasks means uvicorn accepts connections
    immediately and the UI shows "loading" rather than hanging.
    """
    from backend.core.capabilities import probe

    async def _warm() -> None:
        try:
            await probe()
            log.info("capability probe complete")
        except Exception:
            log.exception("capability warm-up failed")

    task = asyncio.create_task(_warm())
    yield
    task.cancel()


app = FastAPI(
    title="strix-dash",
    version=VERSION,
    lifespan=lifespan,
    docs_url=f"{config.API_PREFIX}/docs",
    openapi_url=f"{config.API_PREFIX}/openapi.json",
)


@app.middleware("http")
async def guard_writes(request: Request, call_next):
    """CSRF guard for state-changing requests.

    Binding to loopback does NOT stop a page in the user's browser from POSTing
    here, and these endpoints change power limits and install packages. Writes
    must carry a custom header (which forces a preflight cross-origin) and, when
    an Origin is present, it must be ours.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin not in config.ALLOWED_ORIGINS:
            return JSONResponse(
                status_code=403,
                content=models.fail(
                    ToolError(
                        code=ErrorCode.PERMISSION_DENIED,
                        message="Cross-origin write rejected",
                        hint=f"Origin {origin} is not permitted.",
                    )
                ),
            )
        if config.WRITE_HEADER.lower() not in (k.lower() for k in request.headers):
            return JSONResponse(
                status_code=403,
                content=models.fail(
                    ToolError(
                        code=ErrorCode.PERMISSION_DENIED,
                        message="Missing required request header",
                        hint=f"State-changing requests must send {config.WRITE_HEADER}.",
                    )
                ),
            )
    return await call_next(request)


@app.exception_handler(ToolError)
async def tool_error_handler(request: Request, exc: ToolError) -> JSONResponse:
    return models.respond(exc)


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a traceback to the browser; log it with a correlation id."""
    correlation = f"{int(time.time() * 1000):x}"
    log.exception("unhandled error [%s] on %s", correlation, request.url.path)
    return JSONResponse(
        status_code=500,
        content=models.fail(
            ToolError(
                code=ErrorCode.INTERNAL,
                message="Internal error",
                hint=f"Check the journal for correlation id {correlation}.",
                detail={"correlation_id": correlation},
            )
        ),
    )


@app.get(f"{config.API_PREFIX}/health")
async def health() -> dict:
    return models.ok(
        {
            "status": "ok",
            "version": VERSION,
            "uptime_s": round(time.time() - STARTED_AT, 1),
            "api_prefix": config.API_PREFIX,
        }
    )


def _mount_routers() -> None:
    """Attach routers that exist. Built incrementally, phase by phase."""
    from backend.api import capabilities as capabilities_api

    app.include_router(capabilities_api.router, prefix=config.API_PREFIX)

    for module_name, attr in (
        ("backend.api.telemetry", "router"),
        ("backend.api.packages", "router"),
        ("backend.api.requirements", "router"),
        ("backend.api.controls", "router"),
    ):
        try:
            module = __import__(module_name, fromlist=[attr])
        except ImportError:
            continue
        app.include_router(getattr(module, attr), prefix=config.API_PREFIX)


_mount_routers()


class RevalidatingStatic(StaticFiles):
    """Serve assets with must-revalidate.

    The frontend is plain ES modules and one stylesheet, with no content
    hashing in their filenames. Left to default caching, a browser keeps
    serving the old bundle after an upgrade -- so a deploy.sh that changes
    behaviour appears to do nothing until a hard reload.

    ETag/Last-Modified still make revalidation a 304, so this costs a
    conditional request, not a re-download. The asset is local anyway.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return super().is_not_modified(response_headers, request_headers)

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


# Static frontend last, so it cannot shadow the API routes above.
if config.FRONTEND_DIST.is_dir():
    app.mount(
        "/static",
        RevalidatingStatic(directory=str(config.FRONTEND_DIST)),
        name="static",
    )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(config.FRONTEND_DIST / "index.html")
else:  # pragma: no cover - dev convenience
    log.warning("frontend not found at %s; API only", config.FRONTEND_DIST)
