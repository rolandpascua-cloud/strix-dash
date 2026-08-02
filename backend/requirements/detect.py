"""Requirement detection.

Detection is entirely LOCAL and needs no network: dpkg state, file presence,
binary presence, systemd unit state. That matters because the installed service
denies network egress at the unit level -- checking for a newer upstream release
is a separate, explicitly-triggered action (see :mod:`.releases`).

The interesting case is ``local-only``: apt reports fastflowlm as installed with
a candidate equal to the installed version, which looks identical to
"up-to-date". The difference is that its only origin is /var/lib/dpkg/status --
it was sideloaded from a .deb and apt will never offer an upgrade. Presenting
that as up-to-date is exactly the blind spot this dashboard exists to fix.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from backend import config
from backend.core.runner import run_tool
from backend.requirements.registry import REQUIREMENTS, Requirement

_ORIGIN_RE = re.compile(r"^\s+\d+\s+(\S+)", re.MULTILINE)


def _os_release_fields() -> dict[str, str]:
    fields: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                fields[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return fields


def distro_tag(fields: dict[str, str] | None = None, debian_version: str | None = None) -> str:
    """Release-asset tag for this distribution.

    Derived, never hardcoded: FastFlowLM ships debian13, ubuntu24.04,
    ubuntu25.10 and ubuntu26.04 builds in the same release, and picking the
    wrong one silently installs an incompatible binary.

    ``fields`` and ``debian_version`` exist so this can be tested against
    synthetic inputs rather than whatever machine the tests happen to run on.
    """
    if fields is None:
        fields = _os_release_fields()

    ident = fields.get("ID", "")
    like = fields.get("ID_LIKE", "")
    version = fields.get("VERSION_ID", "")

    if ident == "ubuntu":
        return f"ubuntu{version}"

    if ident == "debian" or "debian" in like:
        # Derivatives (this platform reports ID=amd-ryzen-ai-developer-platform
        # with ID_LIKE=debian) must map to their Debian base, not their own
        # VERSION_ID -- which is "1" here and matches no asset.
        major = version.split(".")[0] if ident == "debian" else ""
        if not major:
            if debian_version is None:
                try:
                    debian_version = Path("/etc/debian_version").read_text()
                except OSError:
                    debian_version = ""
            major = debian_version.strip().split(".")[0]
        return f"debian{major}" if major else "debian"

    return ident or "unknown"


async def _dpkg_state(package: str) -> dict[str, Any]:
    result = await run_tool(
        "dpkg-query",
        "-W",
        "-f=${db:Status-Abbrev}\\t${Version}",
        package,
        trust_exit_code=False,
    )
    if result.exit_code != 0 or "\t" not in result.stdout:
        return {"installed": False, "version": None}
    status, _, version = result.stdout.partition("\t")
    return {
        "installed": status.strip().startswith("ii"),
        "version": version.strip() or None,
    }


async def _apt_policy(package: str) -> dict[str, Any]:
    """Installed/candidate versions plus where the candidate would come from."""
    result = await run_tool("apt-cache", "policy", package, trust_exit_code=False)
    out = result.stdout
    installed = candidate = None
    if match := re.search(r"Installed:\s*(\S+)", out):
        installed = None if match.group(1) == "(none)" else match.group(1)
    if match := re.search(r"Candidate:\s*(\S+)", out):
        candidate = None if match.group(1) == "(none)" else match.group(1)

    origins = [o for o in _ORIGIN_RE.findall(out)]
    # A package whose ONLY origin is the dpkg status file has no repository
    # behind it -- apt cannot ever upgrade it.
    repo_origins = [o for o in origins if not o.endswith("/var/lib/dpkg/status")]
    return {
        "installed": installed,
        "candidate": candidate,
        "origins": origins,
        "has_repo_origin": bool(repo_origins),
    }


async def _detect_one(req: Requirement) -> dict[str, Any]:
    info: dict[str, Any] = {
        "id": req.id,
        "name": req.name,
        "summary": req.summary,
        "required_for": list(req.required_for),
        "optional": req.optional,
        "source_kind": req.source_kind,
        "repo": req.repo,
        "remediation": req.remediation,
        "installed_version": None,
        "available_version": None,
        "status": "unknown",
        "detail": {},
    }

    if req.detect_kind == "dpkg":
        state = await _dpkg_state(req.detect_target)
        info["installed_version"] = state["version"]
        if not state["installed"]:
            info["status"] = "missing"
        else:
            policy = await _apt_policy(req.detect_target)
            info["detail"]["origins"] = policy["origins"]
            if not policy["has_repo_origin"]:
                info["status"] = "local-only"
                info["detail"]["note"] = (
                    "Installed from a local .deb with no repository origin; "
                    "apt will not offer updates for it."
                )
            elif policy["candidate"] and policy["installed"] != policy["candidate"]:
                info["status"] = "outdated"
                info["available_version"] = policy["candidate"]
            else:
                info["status"] = "satisfied"

    elif req.detect_kind == "file" or req.detect_kind == "device":
        exists = Path(req.detect_target).exists()
        info["status"] = "satisfied" if exists else "missing"
        info["detail"]["path"] = req.detect_target

    elif req.detect_kind == "binary":
        path = config.TOOLS.get(req.detect_target, req.detect_target)
        exists = os.path.exists(path)
        info["status"] = "satisfied" if exists else "missing"
        info["detail"]["path"] = path

    elif req.detect_kind == "service":
        state = await _dpkg_state(req.detect_target)
        info["installed_version"] = state["version"]
        if not state["installed"]:
            info["status"] = "missing"
        else:
            active = await run_tool(
                "systemctl", "is-active", req.detect_target, trust_exit_code=False
            )
            unit_state = active.stdout.strip() or "unknown"
            info["detail"]["unit_state"] = unit_state
            # Installed but stopped is its own state -- not "missing", and
            # certainly not "satisfied".
            info["status"] = "satisfied" if unit_state == "active" else "degraded"

    return info


async def detect_all() -> dict[str, Any]:
    items = [await _detect_one(req) for req in REQUIREMENTS]
    blocking = [
        i for i in items if not i["optional"] and i["status"] in {"missing", "outdated", "degraded"}
    ]
    return {
        "distro_tag": distro_tag(),
        "items": items,
        "summary": {
            "total": len(items),
            "satisfied": sum(1 for i in items if i["status"] == "satisfied"),
            "needs_attention": len(blocking),
            "blocking": [i["id"] for i in blocking],
        },
    }
