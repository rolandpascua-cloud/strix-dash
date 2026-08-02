"""Snapshot auditor and curated package endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from backend.core import models
from backend.core.errors import ToolError
from backend.packages import snapshots

router = APIRouter(tags=["packages"])


@router.get("/snapshots")
async def list_snapshots() -> Any:
    try:
        return models.ok(await snapshots.listing(), source="dpkg-query")
    except ToolError as exc:
        return models.respond(exc, source="dpkg-query")


@router.post("/snapshots/create")
async def create_snapshot() -> Any:
    """Take a timestamped read-only snapshot of the running root."""
    try:
        return models.ok(await snapshots.create(), source="btrfs")
    except ToolError as exc:
        return models.respond(exc, source="btrfs")


@router.get("/snapshots/{snapshot_id}/packages")
async def snapshot_packages(
    snapshot_id: str,
    search: str = Query(""),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> Any:
    try:
        packages = await snapshots.packages_for(snapshot_id)
    except ToolError as exc:
        return models.respond(exc, source="dpkg-query")

    items = sorted(packages.items())
    if search:
        needle = search.lower()
        items = [(n, v) for n, v in items if needle in n.lower()]

    window = items[offset : offset + limit]
    return models.ok(
        {
            "snapshot": snapshot_id,
            "total": len(items),
            "offset": offset,
            "limit": limit,
            "packages": [{"package": n, "version": v} for n, v in window],
        },
        source="dpkg-query",
    )


@router.get("/snapshots/diff")
async def snapshot_diff(
    base: str = Query(...),
    target: str = Query("current"),
) -> Any:
    """Diff two snapshots. On-demand only -- this reads thousands of records."""
    try:
        return models.ok(await snapshots.diff(base, target), source="dpkg-query")
    except ToolError as exc:
        return models.respond(exc, source="dpkg-query")
