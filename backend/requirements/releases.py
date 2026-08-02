"""Upstream release lookup -- explicitly triggered, never automatic.

Kept apart from detection for two reasons:

1. The installed unit sets ``IPAddressDeny=any``, so the service itself has no
   network. Reaching upstream is delegated to the privileged helper, which runs
   outside that namespace via sudo. In development (running as your own user,
   helper not installed) a direct fetch is used instead.

2. Checking for updates is a user-initiated action. Nothing here runs on a
   timer, and there is no "update all".

The repository is looked up from the local registry by requirement id -- a
caller never supplies a URL or repo name.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from backend import config
from backend.core import errors
from backend.core.cache import cache
from backend.core.runner import run
from backend.requirements.detect import distro_tag
from backend.requirements.registry import BY_ID, Requirement

HELPER = "/usr/lib/strix-dash/backend/strix-dash-req-helper.sh"
_TIMEOUT = 15


def _select_asset(
    req: Requirement, tag_name: str, assets: list[dict], distro: str | None = None
) -> dict | None:
    """Pick the asset matching this distribution.

    ``distro`` may be supplied so callers (and tests) are not tied to the
    machine they happen to be running on.
    """
    if not req.asset_template:
        return None
    version = tag_name.lstrip("v")
    wanted = req.asset_template.format(version=version, distro_tag=distro or distro_tag())
    for asset in assets:
        if asset.get("name") == wanted:
            return asset
    return None


def _shape(req: Requirement, payload: dict[str, Any]) -> dict[str, Any]:
    tag = payload.get("tag_name") or ""
    assets = payload.get("assets") or []
    asset = _select_asset(req, tag, assets)
    return {
        "id": req.id,
        "latest_version": tag.lstrip("v") or None,
        "tag_name": tag or None,
        "published_at": payload.get("published_at"),
        "asset": None
        if asset is None
        else {
            "name": asset.get("name"),
            "size": asset.get("size"),
            "url": asset.get("browser_download_url"),
            # GitHub exposes a per-asset digest; there are no separate checksum
            # files. This authenticates integrity of transfer, NOT publisher
            # identity -- the digest is served by the same host as the asset.
            "digest": asset.get("digest"),
        },
        "available_assets": [a.get("name") for a in assets],
        "distro_tag": distro_tag(),
    }


def _fetch_direct(repo: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    # Defence in depth: the host is hardcoded above, but assert it anyway so
    # no future edit can introduce a file:// or custom-scheme fetch.
    if not url.startswith("https://api.github.com/"):  # pragma: no cover
        raise errors.invalid_value("release url", "must be the GitHub API over https")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "strix-dash",
        },
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
        return json.loads(response.read().decode())


async def _fetch_via_helper(requirement_id: str) -> dict[str, Any]:
    result = await run(
        [config.TOOLS["sudo"], "-n", HELPER, "check", requirement_id],
        tool="sudo",
        timeout=_TIMEOUT + 5,
        parse="json",
    )
    if not result.ok:
        raise result.error or errors.parse_error("req-helper", "check failed")
    if not isinstance(result.parsed, dict):
        raise errors.parse_error("req-helper", "expected JSON", result.stdout)
    return result.parsed


async def _fetch(requirement_id: str) -> dict[str, Any]:
    req = BY_ID.get(requirement_id)
    if req is None:
        raise errors.invalid_value("requirement", f"unknown id {requirement_id!r}")
    if req.source_kind != "github-release" or not req.repo:
        raise errors.ToolError(
            code=errors.ErrorCode.NOT_SUPPORTED,
            message=f"{req.name} has no automatic release feed",
            hint=req.remediation or "This requirement is tracked but must be updated manually.",
        )

    if os.path.exists(HELPER):
        payload = await _fetch_via_helper(requirement_id)
    else:
        # Development path only; the installed service has no egress.
        try:
            payload = _fetch_direct(req.repo)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise errors.ToolError(
                code=errors.ErrorCode.TOOL_FAILED,
                message=f"Could not reach the release feed for {req.name}: {exc}",
                hint="Check network connectivity, or install the privileged helper.",
                retryable=True,
            ) from exc

    return _shape(req, payload)


async def latest(requirement_id: str, *, force: bool = False) -> dict[str, Any]:
    entry = await cache.get(
        f"release:{requirement_id}",
        lambda: _fetch(requirement_id),
        ttl=config.CACHE_TTL["github_release"],
        force=force,
    )
    return entry.value
