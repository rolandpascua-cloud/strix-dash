"""FastFlowLM (flm) collector -- NPU stack validation.

``flm validate -j`` is the highest-quality data source on this machine: real
JSON, no escape codes, and a single top-level ``ready`` boolean that answers
"is the NPU stack usable?" without any interpretation on our part.

The text form of the same command emits four colour sequences, which is why
everything goes through the runner's strip.
"""

from __future__ import annotations

from typing import Any

from backend import config
from backend.core import errors
from backend.core.cache import cache
from backend.core.runner import run_tool


def _firmware_version(device: dict[str, Any]) -> str | None:
    parts = [device.get(k) for k in ("fw_major", "fw_minor", "fw_patch", "fw_build")]
    if any(p is None for p in parts):
        return None
    return ".".join(str(p) for p in parts)


def _shape_validate(raw: dict[str, Any]) -> dict[str, Any]:
    devices = []
    for dev in raw.get("devices") or []:
        devices.append(
            {
                "device": dev.get("device"),
                "columns": dev.get("cols"),
                "firmware_version": _firmware_version(dev),
                "firmware_ok": dev.get("fw_ok"),
            }
        )

    checks = [
        {"id": "kernel", "ok": raw.get("kernel_ok"), "value": raw.get("kernel")},
        {
            "id": "device",
            "ok": raw.get("amd_device_found"),
            "value": devices[0]["device"] if devices else None,
        },
        {
            "id": "columns",
            "ok": raw.get("enough_cols"),
            "value": devices[0]["columns"] if devices else None,
        },
        {
            "id": "firmware",
            "ok": raw.get("all_fw_ok"),
            "value": devices[0]["firmware_version"] if devices else None,
        },
        {
            "id": "memlock",
            "ok": raw.get("memlock_ok"),
            "value": raw.get("memlock_limit"),
        },
        {
            "id": "driver",
            "ok": raw.get("drm_version") is not None,
            "value": raw.get("drm_version"),
        },
    ]

    return {
        "ready": bool(raw.get("ready")),
        "kernel": raw.get("kernel"),
        "amdxdna_version": raw.get("drm_version"),
        "memlock_limit": raw.get("memlock_limit"),
        "memlock_ok": bool(raw.get("memlock_ok")),
        "devices": devices,
        "checks": checks,
        "failed_checks": [c["id"] for c in checks if not c["ok"]],
    }


async def _fetch_validate() -> dict[str, Any]:
    result = await run_tool("flm", "validate", "-j", parse="json")
    if not result.ok:
        raise result.error or errors.parse_error("flm validate", "unknown failure")
    if not isinstance(result.parsed, dict):
        raise errors.parse_error("flm validate", "expected a JSON object", result.stdout)
    shaped = _shape_validate(result.parsed)
    shaped["_duration_ms"] = result.duration_ms
    return shaped


async def validate(*, force: bool = False) -> dict[str, Any]:
    entry = await cache.get(
        "flm_validate",
        _fetch_validate,
        ttl=config.CACHE_TTL["flm_validate"],
        force=force,
    )
    return entry.value


async def _fetch_version() -> dict[str, Any]:
    result = await run_tool("flm", "version", "-j", parse="json")
    if not result.ok:
        raise result.error or errors.parse_error("flm version", "unknown failure")
    parsed = result.parsed if isinstance(result.parsed, dict) else {}
    return {"version": parsed.get("version")}


async def version(*, force: bool = False) -> dict[str, Any]:
    entry = await cache.get(
        "flm_version", _fetch_version, ttl=config.CACHE_TTL["flm_version"], force=force
    )
    return entry.value


async def _fetch_models() -> dict[str, Any]:
    result = await run_tool("flm", "list", "-j", parse="json")
    if not result.ok:
        raise result.error or errors.parse_error("flm list", "unknown failure")

    payload = result.parsed
    # The shape has moved between releases; accept a bare list or a wrapper
    # object rather than hard-failing the whole panel on a cosmetic change.
    if isinstance(payload, dict):
        for key in ("models", "data", "items"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise errors.parse_error("flm list", "expected a list of models", result.stdout)

    models_out = []
    for item in payload:
        if isinstance(item, str):
            models_out.append({"name": item, "installed": False})
        elif isinstance(item, dict):
            name = item.get("name") or item.get("model") or item.get("tag")
            installed = item.get("installed")
            if installed is None:
                installed = item.get("is_installed", False)
            models_out.append({"name": name, "installed": bool(installed), "raw": item})

    return {
        "total": len(models_out),
        "installed": sum(1 for m in models_out if m["installed"]),
        "models": models_out,
    }


async def models(*, force: bool = False) -> dict[str, Any]:
    entry = await cache.get(
        "flm_list", _fetch_models, ttl=config.CACHE_TTL["flm_list"], force=force
    )
    return entry.value
