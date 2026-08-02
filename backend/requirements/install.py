"""Requirement installation.

The most consequential path in the application: it downloads an executable from
the internet and installs it as root. Every safeguard is deliberate.

* The backend passes a requirement **id** to the helper -- never a URL, repo,
  filename or path. The helper re-resolves everything from its own hardcoded
  allowlist, so even a fully compromised backend cannot install something
  arbitrary.
* The helper verifies the SHA256 digest the release API reports and refuses to
  install anything unverified. That authenticates the integrity of the
  transfer, not the identity of the publisher.
* Nothing here is automatic. There is no timer, no "update all", and each
  install requires a confirmation token issued after the user has been shown the
  filename, source and size.
"""

from __future__ import annotations

import os
from typing import Any

from backend import config
from backend.controls import confirm
from backend.core import errors
from backend.core.runner import run
from backend.requirements import detect, releases
from backend.requirements.registry import BY_ID

HELPER = "/usr/lib/strix-dash/backend/strix-dash-req-helper.sh"


def available() -> bool:
    return os.path.exists(HELPER)


async def preview(requirement_id: str) -> dict[str, Any]:
    """What would be installed: filename, source, size and digest.

    Shown to the user before any download begins -- they approve a specific
    artefact, not a vague "update".
    """
    req = BY_ID.get(requirement_id)
    if req is None:
        raise errors.invalid_value("requirement", f"unknown id {requirement_id!r}")
    if req.source_kind != "github-release":
        raise errors.ToolError(
            code=errors.ErrorCode.NOT_SUPPORTED,
            message=f"{req.name} cannot be installed automatically",
            hint=req.remediation or "Install it manually.",
        )

    release = await releases.latest(requirement_id)
    asset = release.get("asset")
    if not asset:
        raise errors.ToolError(
            code=errors.ErrorCode.NOT_SUPPORTED,
            message=(
                f"No asset for {release.get('distro_tag')} in "
                f"{req.name} {release.get('latest_version')}"
            ),
            hint=f"Available: {', '.join(release.get('available_assets') or [])}",
        )

    current = next(
        (i for i in (await detect.detect_all())["items"] if i["id"] == requirement_id),
        {},
    )
    token = confirm.issue(
        f"install:{requirement_id}",
        release["latest_version"],
        current.get("installed_version"),
        (
            f"This downloads {asset['name']} ({asset['size']:,} bytes) from "
            f"github.com/{req.repo} and installs it as root via apt. "
            "The download is checked against the digest published with the "
            "release, which verifies the transfer but not the publisher."
        ),
    )

    return {
        "requirement": requirement_id,
        "name": req.name,
        "installed_version": current.get("installed_version"),
        "latest_version": release["latest_version"],
        "asset": asset,
        "repo": req.repo,
        "distro_tag": release.get("distro_tag"),
        "helper_available": available(),
        **token,
    }


async def perform(requirement_id: str, token: str | None) -> dict[str, Any]:
    if requirement_id not in BY_ID:
        raise errors.invalid_value("requirement", f"unknown id {requirement_id!r}")
    if not available():
        raise errors.ToolError(
            code=errors.ErrorCode.NOT_SUPPORTED,
            message="The privileged install helper is not installed",
            hint="Run scripts/install.sh (or deploy.sh) to install it.",
        )

    release = await releases.latest(requirement_id)
    confirm.consume(token, f"install:{requirement_id}", release["latest_version"])

    # Only the id crosses this boundary.
    result = await run(
        [config.TOOLS["sudo"], "-n", HELPER, "install", requirement_id],
        tool="sudo",
        timeout=900,
        parse="json",
    )
    if not result.ok:
        raise result.error or errors.ToolError(
            code=errors.ErrorCode.TOOL_FAILED, message="Install failed"
        )

    payload = result.parsed if isinstance(result.parsed, dict) else {}
    after = next(
        (i for i in (await detect.detect_all())["items"] if i["id"] == requirement_id),
        {},
    )
    return {
        "requirement": requirement_id,
        "installed": payload.get("installed", False),
        "asset": payload.get("asset"),
        "digest": payload.get("digest"),
        "version": after.get("installed_version") or payload.get("version"),
        "status": after.get("status"),
    }
