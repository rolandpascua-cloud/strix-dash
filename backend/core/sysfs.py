"""Safe sysfs access.

Writes here change fan curves, charge limits and power state, so the write path
is allowlisted: a caller passes a :class:`Path` that must already appear in the
registry built from :mod:`backend.config`. A bug (or an injected value) cannot
turn into an arbitrary-file write.

Reads are unrestricted -- everything under /sys is world-readable and harmless.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from backend import config
from backend.core import errors


def _writable_registry() -> set[Path]:
    """Every sysfs node this service is ever permitted to write."""
    allowed: set[Path] = {
        config.PLATFORM_PROFILE,
        config.THROTTLE_POLICY,
        config.PANEL_OD,
        config.BATTERY_CHARGE_LIMIT,
        config.KBD_BACKLIGHT,
    }
    # The ppt_* power-limit nodes are deliberately absent: their units are
    # unconfirmed, and a mislabelled write to a power limit is the one change
    # here that could damage hardware. They are read-only telemetry only.

    fan_curve = find_hwmon(config.HWMON_FAN_CURVE_DRIVER)
    if fan_curve is not None:
        for fan in config.FAN_CURVE_FANS:
            allowed.add(fan_curve / f"pwm{fan}_enable")
            for point in range(1, config.FAN_CURVE_POINTS + 1):
                allowed.add(fan_curve / f"pwm{fan}_auto_point{point}_temp")
                allowed.add(fan_curve / f"pwm{fan}_auto_point{point}_pwm")
    return allowed


def find_hwmon(driver_name: str) -> Path | None:
    """Resolve a hwmon directory by its driver name.

    hwmon indices are assigned in probe order and are NOT stable across boots --
    the fan-curve device was hwmon10 when this was written, but hardcoding that
    would break on any reboot that probes devices in a different order.
    """
    if not config.HWMON_ROOT.is_dir():
        return None
    for entry in sorted(config.HWMON_ROOT.iterdir()):
        name_file = entry / "name"
        try:
            if name_file.read_text().strip() == driver_name:
                return entry.resolve()
        except OSError:
            continue
    return None


def read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None


def read_int(path: Path) -> int | None:
    raw = read_text(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def read_choices(path: Path) -> list[str]:
    raw = read_text(path)
    return raw.split() if raw else []


def describe(path: Path) -> dict[str, object]:
    """Presence/permission/value snapshot used by the capability probe."""
    import os

    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "readable": exists and os.access(path, os.R_OK),
        # Writability is the real gate for a control: the node existing says
        # nothing about whether the tmpfiles.d group grant actually landed.
        "writable": exists and os.access(path, os.W_OK),
        "value": read_text(path) if exists else None,
    }


def write_text(path: Path, value: str) -> None:
    """Write to an allowlisted sysfs node.

    Raises :class:`~backend.core.errors.ToolError` rather than OSError so the
    API layer has a single failure type to envelope.
    """
    if path.resolve() not in {p.resolve() for p in _writable_registry() if p.exists()}:
        raise errors.ToolError(
            code=errors.ErrorCode.NOT_SUPPORTED,
            message=f"{path} is not a writable control on this system",
            hint="It is absent, or intentionally read-only in this version.",
        )
    try:
        path.write_text(value)
    except PermissionError as exc:
        raise errors.permission_denied(str(path)) from exc
    except OSError as exc:
        # sysfs rejects out-of-range values with EINVAL at write() time.
        raise errors.invalid_value(
            path.name, f"kernel rejected {value!r} ({exc.strerror})"
        ) from exc


def write_int(path: Path, value: int) -> None:
    write_text(path, str(value))


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def all_present(paths: Iterable[Path]) -> bool:
    return all(p.exists() for p in paths)
