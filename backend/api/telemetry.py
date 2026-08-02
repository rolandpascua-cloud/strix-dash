"""Telemetry endpoints."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.collectors import flm, host, memory, rocm, xrt
from backend.core import models
from backend.core.cache import cache
from backend.core.errors import ToolError

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


async def _envelope(
    source: str,
    cache_key: str,
    producer: Callable[..., Awaitable[Any]],
    *,
    force: bool = False,
) -> Any:
    """Run a collector and wrap it, converting ToolError into panel state."""
    try:
        data = await producer(force=force)
    except ToolError as exc:
        return models.respond(exc, source=source)

    entry = cache.peek(cache_key)
    duration = None
    if isinstance(data, dict):
        duration = data.pop("_duration_ms", None)
    return models.ok(
        data,
        source=source,
        cached=entry is not None and not force,
        age_ms=entry.age_ms if entry else None,
        stale=entry.stale if entry else False,
        duration_ms=duration,
    )


@router.get("/flm/validate")
async def flm_validate(force: bool = Query(False)) -> Any:
    return await _envelope("flm", "flm_validate", flm.validate, force=force)


@router.get("/flm/version")
async def flm_version(force: bool = Query(False)) -> Any:
    return await _envelope("flm", "flm_version", flm.version, force=force)


@router.get("/flm/models")
async def flm_models(force: bool = Query(False)) -> Any:
    return await _envelope("flm", "flm_list", flm.models, force=force)


@router.get("/gpu")
async def gpu(force: bool = Query(False)) -> Any:
    return await _envelope("rocm-smi", "rocm_smi", rocm.live, force=force)


@router.get("/memory")
async def unified_memory(force: bool = Query(False)) -> Any:
    """Unified/GTT is primary; the VRAM carveout is secondary and labelled."""
    return await _envelope("memory", "rocm_smi", memory.unified, force=force)


@router.get("/npu")
async def npu(
    report: str = Query("all", pattern="^(all|host|platform|aie-partitions)$"),
    force: bool = Query(False),
) -> Any:
    try:
        data = await xrt.examine(report, force=force)
    except ToolError as exc:
        return models.respond(exc, source="xrt-smi")
    duration = data.pop("_duration_ms", None)
    entry = cache.peek(f"xrt:{report}")
    return models.ok(
        data,
        source="xrt-smi",
        cached=entry is not None and not force,
        age_ms=entry.age_ms if entry else None,
        duration_ms=duration,
    )


@router.get("/host")
async def host_metrics() -> Any:
    """Live host metrics. psutil + sysfs only -- safe to poll at 2s."""
    return models.ok(
        {"static": host.static_info(), "live": host.snapshot(), "fans": host.fans()},
        source="psutil",
    )


@router.get("/rocminfo")
async def rocminfo(force: bool = Query(False)) -> Any:
    """Static hardware topology. Cached at startup; never polled."""
    return await _envelope("rocminfo", "rocminfo", rocm.info, force=force)
