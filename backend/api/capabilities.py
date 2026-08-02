"""Capability endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from backend.core import models
from backend.core.cache import cache
from backend.core.capabilities import CACHE_KEY, probe

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities")
async def get_capabilities() -> dict:
    entry = cache.peek(CACHE_KEY)
    data = await probe()
    return models.ok(
        data,
        source="capabilities",
        cached=entry is not None,
        age_ms=entry.age_ms if entry else None,
    )


@router.post("/capabilities/refresh")
async def refresh_capabilities() -> dict:
    data = await probe(force=True)
    return models.ok(data, source="capabilities")
