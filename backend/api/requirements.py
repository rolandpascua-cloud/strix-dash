"""Requirements endpoints.

``GET /requirements`` is local-only and always works offline. Reaching upstream
for a newer release is a separate, explicitly-triggered POST -- there is no
background update check and no "update all".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.core import models
from backend.core.errors import ToolError
from backend.requirements import detect, install, releases

router = APIRouter(prefix="/requirements", tags=["requirements"])


class InstallBody(BaseModel):
    confirm_token: str | None = None


@router.get("")
async def list_requirements() -> Any:
    try:
        return models.ok(await detect.detect_all(), source="dpkg")
    except ToolError as exc:
        return models.respond(exc, source="dpkg")


@router.post("/{requirement_id}/check")
async def check_release(requirement_id: str, force: bool = Query(True)) -> Any:
    """Look up the newest upstream release for one requirement."""
    try:
        return models.ok(await releases.latest(requirement_id, force=force), source="github")
    except ToolError as exc:
        return models.respond(exc, source="github")


@router.post("/{requirement_id}/preview")
async def preview_install(requirement_id: str) -> Any:
    """Exactly what would be downloaded, plus a confirmation token.

    The user approves a specific artefact -- filename, source and size -- before
    anything is fetched.
    """
    try:
        return models.ok(await install.preview(requirement_id), source="github")
    except ToolError as exc:
        return models.respond(exc, source="github")


@router.post("/{requirement_id}/install")
async def perform_install(requirement_id: str, body: InstallBody) -> Any:
    """Download, verify the digest, and install as root via the helper."""
    try:
        return models.ok(await install.perform(requirement_id, body.confirm_token), source="apt")
    except ToolError as exc:
        return models.respond(exc, source="apt")
