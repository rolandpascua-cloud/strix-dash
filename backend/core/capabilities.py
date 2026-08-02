"""Capability probing.

Answers "what can this machine actually do?" once at startup, so no collector
carries its own is-it-installed branch and the frontend can grey out a panel
with a *specific reason* instead of showing an error.

Three kinds of thing get probed:

* **binaries** -- absolute path exists and is executable *by this process*.
  Executable-by-root is not the question; the service user is what matters.
* **sysfs nodes** -- writability is the real gate. A node existing says nothing
  about whether the tmpfiles.d group grant was applied.
* **services** -- tuned is installed here but its daemon is not running, which
  is a different state from "missing" and deserves different UI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from backend import config
from backend.core import sysfs
from backend.core.cache import cache
from backend.core.runner import run_tool

CACHE_KEY = "capabilities"


@dataclass
class Capability:
    id: str
    available: bool
    reason: str | None = None
    hint: str | None = None
    install_command: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "available": self.available}
        for key in ("reason", "hint", "install_command"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.detail:
            out["detail"] = self.detail
        return out


def _probe_binary(name: str) -> Capability:
    path = config.TOOLS.get(name, name)
    hint = config.INSTALL_HINTS.get(name)
    if not os.path.exists(path):
        return Capability(
            id=f"tool:{name}",
            available=False,
            reason=f"{path} not found",
            hint=hint,
            install_command=hint,
            detail={"path": path},
        )
    if not os.access(path, os.X_OK):
        return Capability(
            id=f"tool:{name}",
            available=False,
            reason=f"{path} is not executable by this service user",
            hint=(
                "A path inside a user's home is unreachable once running as a "
                "system user; prefer the system-wide copy."
            ),
            detail={"path": path},
        )
    return Capability(id=f"tool:{name}", available=True, detail={"path": path})


async def _probe_tuned() -> Capability:
    """tuned has three states, not two: missing, installed-but-stopped, running."""
    binary = _probe_binary("tuned-adm")
    if not binary.available:
        return Capability(
            id="service:tuned",
            available=False,
            reason="tuned is not installed",
            hint="sudo apt install tuned",
            install_command="sudo apt install tuned",
        )
    result = await run_tool("systemctl", "is-active", "tuned", trust_exit_code=False)
    state = result.stdout.strip() or "unknown"
    active = state == "active"
    return Capability(
        id="service:tuned",
        available=active,
        reason=None if active else f"tuned daemon is {state}",
        hint=None if active else "sudo systemctl start tuned",
        install_command=None if active else "sudo systemctl start tuned",
        detail={"state": state},
    )


def _probe_sysfs() -> dict[str, Any]:
    nodes: dict[str, Any] = {
        "platform_profile": sysfs.describe(config.PLATFORM_PROFILE),
        "throttle_thermal_policy": sysfs.describe(config.THROTTLE_POLICY),
        "battery_charge_limit": sysfs.describe(config.BATTERY_CHARGE_LIMIT),
        "kbd_backlight": sysfs.describe(config.KBD_BACKLIGHT),
        "ttm_pages_limit": sysfs.describe(config.TTM_PAGES_LIMIT),
    }
    for node in config.PPT_NODES:
        nodes[node] = sysfs.describe(config.ASUS_PLATFORM / node)

    fan_curve = sysfs.find_hwmon(config.HWMON_FAN_CURVE_DRIVER)
    fan_sensor = sysfs.find_hwmon(config.HWMON_FAN_SENSOR_DRIVER)
    nodes["fan_curve_hwmon"] = {
        "path": str(fan_curve) if fan_curve else None,
        "exists": fan_curve is not None,
        "driver": config.HWMON_FAN_CURVE_DRIVER,
    }
    nodes["fan_sensor_hwmon"] = {
        "path": str(fan_sensor) if fan_sensor else None,
        "exists": fan_sensor is not None,
        "driver": config.HWMON_FAN_SENSOR_DRIVER,
    }
    return nodes


def _probe_features(sysfs_nodes: dict[str, Any]) -> list[Capability]:
    """Feature-level capabilities the UI switches whole panels on."""
    feats: list[Capability] = []

    npu = config.NPU_DEVICE.exists()
    feats.append(
        Capability(
            id="feature:npu_device",
            available=npu,
            reason=None if npu else f"{config.NPU_DEVICE} is missing",
            hint=None if npu else "The amdxdna driver may not be loaded.",
        )
    )

    # No multicolour LED node exists on this machine -- only a 0-3 brightness
    # channel. RGB colour and effects genuinely require z13ctl.
    z13 = os.path.exists(config.TOOLS["z13ctl"])
    feats.append(
        Capability(
            id="feature:led_rgb",
            available=z13,
            reason=None if z13 else "no multicolour LED node; requires z13ctl",
            hint=config.INSTALL_HINTS.get("z13ctl"),
        )
    )
    feats.append(
        Capability(
            id="feature:undervolt",
            available=z13,
            reason=None if z13 else "curve optimiser is not exposed via sysfs",
            hint=config.INSTALL_HINTS.get("z13ctl"),
        )
    )

    backlight = sysfs_nodes["kbd_backlight"]
    feats.append(
        Capability(
            id="feature:led_brightness",
            available=bool(backlight["writable"]),
            reason=None if backlight["writable"] else "brightness node not writable",
            hint=None
            if backlight["writable"]
            else "sudo systemd-tmpfiles --create /usr/lib/tmpfiles.d/strix-dash.conf",
        )
    )

    fan = sysfs_nodes["fan_curve_hwmon"]
    feats.append(
        Capability(
            id="feature:fan_curve",
            available=bool(fan["exists"]),
            reason=None if fan["exists"] else "asus_custom_fan_curve hwmon not present",
        )
    )

    # Deliberately unavailable: the ppt_* nodes all read "5", which is not
    # plausibly watts. Shipping a "TDP (W)" slider on that guess is the one bug
    # here that could damage hardware, so v1 exposes them read-only.
    feats.append(
        Capability(
            id="feature:tdp_write",
            available=False,
            reason="ppt_* units are unconfirmed; read-only in this version",
            hint="See docs/HARDWARE.md.",
        )
    )

    snapshots = config.SNAPSHOT_ROOT.is_dir()
    feats.append(
        Capability(
            id="feature:snapshots",
            available=snapshots,
            reason=None if snapshots else f"{config.SNAPSHOT_ROOT} not found",
        )
    )

    backports = config.BACKPORTS_SOURCES.exists()
    feats.append(
        Capability(
            id="feature:backports_enabled",
            available=backports,
            reason=None if backports else "debian-backports.sources is absent",
            hint=None if backports else "Run scripts/bootstrap-npu.sh",
        )
    )
    return feats


async def _build() -> dict[str, Any]:
    tools = {
        name: _probe_binary(name).to_dict()
        for name in ("flm", "xrt-smi", "rocm-smi", "rocminfo", "amd-ttm", "tuned-adm", "z13ctl")
    }
    nodes = _probe_sysfs()
    features = {cap.id.split(":", 1)[1]: cap.to_dict() for cap in _probe_features(nodes)}
    tuned = await _probe_tuned()
    features["tuned_daemon"] = tuned.to_dict()

    return {
        "tools": tools,
        "sysfs": nodes,
        "features": features,
        "summary": {
            "tools_available": sum(1 for t in tools.values() if t["available"]),
            "tools_total": len(tools),
            "degraded": sorted(
                k for k, v in features.items() if not v["available"]
            ),
        },
    }


async def probe(*, force: bool = False) -> dict[str, Any]:
    entry = await cache.get(CACHE_KEY, _build, ttl=config.CACHE_TTL["capabilities"], force=force)
    return entry.value


async def tool_available(name: str) -> bool:
    caps = await probe()
    return bool(caps["tools"].get(name, {}).get("available"))
