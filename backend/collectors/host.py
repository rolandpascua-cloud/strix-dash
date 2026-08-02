"""Host telemetry via psutil and /proc -- no subprocesses, sub-millisecond.

This is the only collector safe to poll at 2s.
"""

from __future__ import annotations

import os
import platform
import time
from pathlib import Path
from typing import Any

import psutil

from backend import config
from backend.core import sysfs

_BOOT_TIME = psutil.boot_time()

_DMI = Path("/sys/class/dmi/id")


def _dmi(name: str) -> str | None:
    return sysfs.read_text(_DMI / name)


def os_release() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return out


def static_info() -> dict[str, Any]:
    """Values that cannot change without a reboot."""
    rel = os_release()
    return {
        "hostname": platform.node(),
        "kernel": platform.release(),
        "kernel_version": platform.version(),
        "arch": platform.machine(),
        "os_name": rel.get("NAME"),
        "os_pretty": rel.get("PRETTY_NAME"),
        "os_id": rel.get("ID"),
        "os_version_codename": rel.get("VERSION_CODENAME"),
        "debian_version": sysfs.read_text(Path("/etc/debian_version")),
        "bios_vendor": _dmi("bios_vendor"),
        "bios_version": _dmi("bios_version"),
        "bios_date": _dmi("bios_date"),
        "product": _dmi("product_name"),
        "board": _dmi("board_name"),
        "cpu_count": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "python": platform.python_version(),
    }


def _secure_boot() -> str:
    """Read the EFI SecureBoot variable; absent means non-UEFI or unavailable."""
    root = Path("/sys/firmware/efi/efivars")
    if not root.is_dir():
        return "unavailable"
    for entry in root.glob("SecureBoot-*"):
        try:
            data = entry.read_bytes()
        except OSError:
            continue
        if len(data) >= 5:
            return "enabled" if data[4] else "disabled"
    return "unknown"


def snapshot() -> dict[str, Any]:
    mem = psutil.virtual_memory()
    load1, load5, load15 = os.getloadavg()

    temps: dict[str, float] = {}
    try:
        for chip, readings in (psutil.sensors_temperatures() or {}).items():
            for reading in readings:
                if reading.current:
                    label = f"{chip}:{reading.label}" if reading.label else chip
                    temps[label] = round(reading.current, 1)
    except (AttributeError, OSError):  # pragma: no cover - platform dependent
        pass

    battery: dict[str, Any] | None = None
    try:
        bat = psutil.sensors_battery()
        if bat is not None:
            battery = {
                "percent": round(bat.percent, 1),
                "plugged": bat.power_plugged,
                "secs_left": None if bat.secsleft < 0 else bat.secsleft,
            }
    except (AttributeError, OSError):  # pragma: no cover
        pass

    root_usage = psutil.disk_usage("/")

    return {
        "uptime_s": round(time.time() - _BOOT_TIME),
        "load": {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)},
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_per_core": psutil.cpu_percent(interval=None, percpu=True),
        "cpu_freq_mhz": round(psutil.cpu_freq().current) if psutil.cpu_freq() else None,
        "memory": {
            "total": mem.total,
            "used": mem.total - mem.available,
            "available": mem.available,
            "percent": mem.percent,
        },
        "swap": {
            "total": psutil.swap_memory().total,
            "used": psutil.swap_memory().used,
        },
        "disk_root": {
            "total": root_usage.total,
            "used": root_usage.used,
            "percent": root_usage.percent,
        },
        "temperatures": temps,
        "battery": battery,
        "secure_boot": _secure_boot(),
        "processes": len(psutil.pids()),
    }


def fans() -> dict[str, Any]:
    """Fan RPM plus PWM mode, read from the asus hwmon sensor device."""
    hwmon = sysfs.find_hwmon(config.HWMON_FAN_SENSOR_DRIVER)
    if hwmon is None:
        return {"available": False, "fans": []}

    entries = []
    for index in config.FAN_CURVE_FANS:
        rpm = sysfs.read_int(hwmon / f"fan{index}_input")
        if rpm is None:
            continue
        entries.append(
            {
                "index": index,
                "label": sysfs.read_text(hwmon / f"fan{index}_label") or f"fan{index}",
                "rpm": rpm,
                "pwm_enable": sysfs.read_int(hwmon / f"pwm{index}_enable"),
            }
        )
    return {"available": bool(entries), "hwmon": str(hwmon), "fans": entries}
