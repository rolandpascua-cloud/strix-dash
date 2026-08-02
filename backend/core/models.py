"""Response envelope.

Every endpoint returns the same shape so the frontend has exactly one unwrap
path and one error-rendering path:

    {"ok": true,  "data": {...}, "meta": {...}}
    {"ok": false, "error": {"code": ..., "hint": ...}, "meta": {...}}

Degraded-but-understood states (tool missing, daemon down) return HTTP 200 with
``ok: false``. A panel then renders its own explanation instead of the browser
reporting a failed request. Genuine 4xx/5xx are reserved for bad requests and
unexpected faults.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from backend.core.errors import ToolError


def meta(
    source: str | None = None,
    *,
    cached: bool = False,
    age_ms: float | None = None,
    stale: bool = False,
    duration_ms: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {"cached": cached, "stale": stale}
    if source:
        out["source"] = source
    if age_ms is not None:
        out["age_ms"] = round(age_ms, 1)
    if duration_ms is not None:
        out["duration_ms"] = round(duration_ms, 1)
    out.update(extra)
    return out


def ok(data: Any, **meta_kwargs: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "meta": meta(**meta_kwargs)}


def fail(error: ToolError, **meta_kwargs: Any) -> dict[str, Any]:
    return {"ok": False, "error": error.to_dict(), "meta": meta(**meta_kwargs)}


def respond(error: ToolError, **meta_kwargs: Any) -> JSONResponse:
    """Envelope an error with the right HTTP status.

    Actionable conditions stay 200 so the UI treats them as panel state.
    """
    status = 200 if error.degraded else 500
    return JSONResponse(status_code=status, content=fail(error, **meta_kwargs))
