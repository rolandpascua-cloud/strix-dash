"""Hardware controls.

Design rules, applied uniformly:

* **sysfs first.** Native nodes exist for performance profile, thermal policy,
  fan curves, battery limit and keyboard backlight -- all reachable through the
  tmpfiles.d group grant with no privilege escalation at all. z13ctl is only
  needed for RGB colour, which has no kernel interface on this hardware.
* **Clamp server-side.** Never trust a slider.
* **Read back.** Every write returns what the hardware actually holds
  afterwards, not an echo of the request. Firmware silently clamping a value is
  reported as ``verified: false`` -- a first-class UI state, not an error.
* **TDP is read-only telemetry.** All five ``ppt_*`` nodes read "5", which is
  not plausibly watts, so there is no write path for them at all. Shipping a
  "TDP (W)" slider on that guess is the one change here that could damage
  hardware.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Any

from backend import config
from backend.controls import confirm
from backend.core import errors, sysfs
from backend.core.runner import run, run_tool

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _fan_curve_state() -> dict[str, Any]:
    hwmon = sysfs.find_hwmon(config.HWMON_FAN_CURVE_DRIVER)
    if hwmon is None:
        return {"available": False, "reason": "asus_custom_fan_curve hwmon not present"}

    fans = []
    for index in config.FAN_CURVE_FANS:
        enable_node = hwmon / f"pwm{index}_enable"
        if not enable_node.exists():
            continue
        points = []
        for point in range(1, config.FAN_CURVE_POINTS + 1):
            temp = sysfs.read_int(hwmon / f"pwm{index}_auto_point{point}_temp")
            pwm = sysfs.read_int(hwmon / f"pwm{index}_auto_point{point}_pwm")
            if temp is None or pwm is None:
                continue
            points.append({"point": point, "temp": temp, "pwm": pwm})
        fans.append(
            {
                "index": index,
                "enable": sysfs.read_int(enable_node),
                "writable": sysfs.describe(enable_node)["writable"],
                "points": points,
            }
        )
    return {"available": bool(fans), "hwmon": str(hwmon), "fans": fans}


async def _tuned_state() -> dict[str, Any]:
    result = await run_tool("tuned-adm", "active", trust_exit_code=False)
    if result.error and result.error.code == errors.ErrorCode.TOOL_MISSING:
        return {"available": False, "reason": "tuned-adm is not installed"}

    combined = f"{result.stdout}\n{result.stderr}"
    # tuned-adm exits 0 even when its daemon is down, so the exit code is
    # meaningless here -- the DBus warning is the only reliable signal.
    daemon_down = "Cannot talk to TuneD daemon" in combined

    active = None
    kind = None
    for line in result.stdout.splitlines():
        if line.startswith("Current active profile:"):
            active, kind = line.split(":", 1)[1].strip(), "current"
        elif line.startswith("Preset profile:"):
            active, kind = line.split(":", 1)[1].strip(), "preset"

    # "- <name><padding>- <description>"; the name is the first token after "- ".
    listing = await run_tool("tuned-adm", "list", trust_exit_code=False)
    profiles = [
        line[2:].split()[0]
        for line in listing.stdout.splitlines()
        if line.startswith("- ") and len(line) > 2
    ]

    return {
        "available": True,
        "daemon_active": not daemon_down,
        "active_profile": active,
        "profile_kind": kind,
        "profiles": profiles,
    }


async def read_all() -> dict[str, Any]:
    """Every control's current state and whether it can be written."""
    profile_node = sysfs.describe(config.PLATFORM_PROFILE)
    throttle_node = sysfs.describe(config.THROTTLE_POLICY)
    battery_node = sysfs.describe(config.BATTERY_CHARGE_LIMIT)
    backlight_node = sysfs.describe(config.KBD_BACKLIGHT)

    ppt = {name: sysfs.describe(config.ASUS_PLATFORM / name) for name in config.PPT_NODES}

    from backend.collectors import xrt

    power_mode = None
    try:
        power_mode = (await xrt.examine("platform")).get("power_mode")
    except errors.ToolError:
        pass

    return {
        "platform_profile": {
            "id": "platform-profile",
            "kind": "choice",
            "value": profile_node["value"],
            "choices": sysfs.read_choices(config.PLATFORM_PROFILE_CHOICES),
            "writable": profile_node["writable"],
            "requires_confirm": False,
            "source": "sysfs",
        },
        "throttle_policy": {
            "id": "throttle-policy",
            "kind": "int",
            "value": sysfs.read_int(config.THROTTLE_POLICY),
            "min": 0,
            "max": 2,
            "writable": throttle_node["writable"],
            "requires_confirm": False,
            "source": "sysfs",
            "note": "0 balanced, 1 turbo, 2 silent (ASUS thermal policy)",
        },
        "battery_limit": {
            "id": "battery-limit",
            "kind": "int",
            "value": sysfs.read_int(config.BATTERY_CHARGE_LIMIT),
            "min": config.BATTERY_LIMIT_RANGE[0],
            "max": config.BATTERY_LIMIT_RANGE[1],
            "unit": "%",
            "writable": battery_node["writable"],
            "requires_confirm": False,
            "source": "sysfs",
        },
        "kbd_backlight": {
            "id": "kbd-backlight",
            "kind": "int",
            "value": sysfs.read_int(config.KBD_BACKLIGHT),
            "min": 0,
            "max": sysfs.read_int(config.KBD_BACKLIGHT_MAX) or 3,
            "writable": backlight_node["writable"],
            "requires_confirm": False,
            "source": "sysfs",
            "note": "Brightness only; this machine has no multicolour LED node.",
        },
        "npu_pmode": {
            "id": "npu-pmode",
            "kind": "choice",
            "value": (power_mode or "").lower() or None,
            "choices": list(config.NPU_PMODES),
            "writable": True,
            "requires_confirm": False,
            "source": "xrt-smi (sudo)",
        },
        "tuned": await _tuned_state(),
        "fan_curve": _fan_curve_state(),
        "ppt": {
            "id": "ppt",
            "kind": "readonly-group",
            "writable": False,
            "values": {name: node["value"] for name, node in ppt.items()},
            "reason": (
                "Units unconfirmed -- every node reads the same value, which is "
                "not plausibly watts. Read-only until verified."
            ),
            "source": "sysfs",
        },
    }


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def _readback(node: Path, requested: Any) -> dict[str, Any]:
    raw = sysfs.read_text(node)
    applied: Any = raw
    if isinstance(requested, int):
        try:
            applied = int(raw) if raw is not None else None
        except ValueError:
            applied = raw
    return {
        "requested": requested,
        "applied": applied,
        "verified": applied == requested,
        "raw": raw,
    }


async def set_platform_profile(value: str) -> dict[str, Any]:
    choices = sysfs.read_choices(config.PLATFORM_PROFILE_CHOICES)
    if value not in choices:
        raise errors.invalid_value(
            "platform_profile", f"must be one of {', '.join(choices)}", choices=choices
        )
    sysfs.write_text(config.PLATFORM_PROFILE, value)
    return _readback(config.PLATFORM_PROFILE, value)


async def set_throttle_policy(value: int) -> dict[str, Any]:
    value = sysfs.clamp(int(value), 0, 2)
    sysfs.write_int(config.THROTTLE_POLICY, value)
    return _readback(config.THROTTLE_POLICY, value)


async def set_battery_limit(value: int) -> dict[str, Any]:
    low, high = config.BATTERY_LIMIT_RANGE
    value = sysfs.clamp(int(value), low, high)
    sysfs.write_int(config.BATTERY_CHARGE_LIMIT, value)
    return _readback(config.BATTERY_CHARGE_LIMIT, value)


async def set_kbd_backlight(value: int) -> dict[str, Any]:
    maximum = sysfs.read_int(config.KBD_BACKLIGHT_MAX) or 3
    value = sysfs.clamp(int(value), 0, maximum)
    sysfs.write_int(config.KBD_BACKLIGHT, value)
    return _readback(config.KBD_BACKLIGHT, value)


async def set_npu_pmode(value: str) -> dict[str, Any]:
    if value not in config.NPU_PMODES:
        raise errors.invalid_value("pmode", f"must be one of {', '.join(config.NPU_PMODES)}")
    # The sudoers rule enumerates these five values literally, so a value not in
    # NPU_PMODES would be refused by sudo even if it got this far.
    result = await run(
        [
            config.TOOLS["sudo"],
            "-n",
            config.TOOLS["xrt-smi"],
            "configure",
            "--pmode",
            value,
        ],
        tool="sudo",
        timeout=20,
    )
    if not result.ok:
        raise result.error or errors.ToolError(
            code=errors.ErrorCode.TOOL_FAILED, message="xrt-smi configure failed"
        )

    from backend.collectors import xrt

    applied = (await xrt.examine("platform", force=True)).get("power_mode")
    normalised = (applied or "").lower()
    return {
        "requested": value,
        "applied": normalised or None,
        "verified": normalised == value,
        "raw": applied,
    }


async def set_tuned_profile(value: str) -> dict[str, Any]:
    state = await _tuned_state()
    if not state.get("available"):
        raise errors.tool_missing("tuned-adm", install_command="sudo apt install tuned")
    if not state.get("daemon_active"):
        raise errors.daemon_inactive("tuned", start_command="sudo systemctl start tuned")

    # The sudoers entry for tuned uses a wildcard, so the name MUST be validated
    # against the real profile list before it reaches the shell.
    if value not in state["profiles"]:
        raise errors.invalid_value(
            "profile", "not a known tuned profile", profiles=state["profiles"]
        )

    result = await run(
        [config.TOOLS["sudo"], "-n", config.TOOLS["tuned-adm"], "profile", value],
        tool="sudo",
        timeout=30,
    )
    if not result.ok:
        raise result.error or errors.ToolError(
            code=errors.ErrorCode.TOOL_FAILED, message="tuned-adm profile failed"
        )

    after = await _tuned_state()
    return {
        "requested": value,
        "applied": after.get("active_profile"),
        "verified": after.get("active_profile") == value,
        "raw": after.get("active_profile"),
    }


def _validate_curve(points: list[dict[str, int]]) -> list[dict[str, int]]:
    if len(points) != config.FAN_CURVE_POINTS:
        raise errors.invalid_value(
            "fan curve", f"expected {config.FAN_CURVE_POINTS} points, got {len(points)}"
        )

    cleaned = []
    for index, point in enumerate(points, start=1):
        temp = sysfs.clamp(int(point["temp"]), *config.FAN_TEMP_RANGE)
        pwm = sysfs.clamp(int(point["pwm"]), *config.PWM_RANGE)
        cleaned.append({"point": index, "temp": temp, "pwm": pwm})

    # A non-monotonic curve is meaningless to the firmware and a good sign the
    # caller made a mistake; reject rather than write something incoherent.
    for previous, current in pairwise(cleaned):
        if current["temp"] < previous["temp"]:
            raise errors.invalid_value(
                "fan curve", "temperatures must be non-decreasing across points"
            )
        if current["pwm"] < previous["pwm"]:
            raise errors.invalid_value(
                "fan curve", "PWM values must be non-decreasing across points"
            )
    return cleaned


async def set_fan_curve(fan: int, points: list[dict[str, int]]) -> dict[str, Any]:
    hwmon = sysfs.find_hwmon(config.HWMON_FAN_CURVE_DRIVER)
    if hwmon is None:
        raise errors.not_supported("Fan curve", "asus_custom_fan_curve hwmon not present")
    if fan not in config.FAN_CURVE_FANS:
        raise errors.invalid_value("fan", f"must be one of {config.FAN_CURVE_FANS}")

    cleaned = _validate_curve(points)
    for point in cleaned:
        sysfs.write_int(hwmon / f"pwm{fan}_auto_point{point['point']}_temp", point["temp"])
        sysfs.write_int(hwmon / f"pwm{fan}_auto_point{point['point']}_pwm", point["pwm"])

    after = []
    for point in range(1, config.FAN_CURVE_POINTS + 1):
        after.append(
            {
                "point": point,
                "temp": sysfs.read_int(hwmon / f"pwm{fan}_auto_point{point}_temp"),
                "pwm": sysfs.read_int(hwmon / f"pwm{fan}_auto_point{point}_pwm"),
            }
        )

    verified = all(
        a["temp"] == c["temp"] and a["pwm"] == c["pwm"] for a, c in zip(after, cleaned, strict=True)
    )
    return {"requested": cleaned, "applied": after, "verified": verified, "raw": after}


def confirm_for(control_id: str, proposed: Any, current: Any) -> dict[str, Any]:
    warnings = {
        "fan-curve": (
            "Changing the fan curve affects cooling. Values are clamped and must "
            "be non-decreasing, but an aggressive curve can still run the machine "
            "hotter or louder. Restoring defaults may need a reboot."
        ),
        "throttle-policy": "Changes the ASUS thermal policy and affects sustained performance.",
    }
    return confirm.issue(
        control_id, proposed, current, warnings.get(control_id, "Confirm this change.")
    )
