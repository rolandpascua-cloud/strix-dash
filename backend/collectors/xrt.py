"""XRT / NPU collector -- parses ``xrt-smi --batch examine``.

Why text and not JSON: ``-f JSON`` exits 1 unless it is also given ``-o <file>``.
Making the service write files into a staging path just to read them back is a
needless privilege and cleanup surface, so the text report is parsed instead.
``--batch`` suppresses this tool's escape codes (it is the only one of the six
that offers such a flag).

Format quirks that drive the parser:

* separator is ``" : "``, but values themselves contain colons -- the BDF is
  ``[0000:00:00.1]``. Split on the FIRST occurrence only.
* every value is right-padded with spaces (handled by the runner's clean()).
* ``Estimated Power : N/A`` must become None, never 0.
"""

from __future__ import annotations

import re
from typing import Any

from backend import config
from backend.core import errors
from backend.core.cache import cache
from backend.core.runner import run_tool

REPORTS = ("all", "host", "platform", "aie-partitions")

_BDF_LINE = re.compile(r"^\[([0-9a-fA-F:.]+)\]\s*:\s*(.+)$")
_TABLE_ROW = re.compile(r"^\|([^|]*)\|([^|]*)\|\s*$")


def _value(raw: str) -> Any:
    text = raw.strip()
    if not text or text in {"N/A", "n/a", "--"}:
        return None
    return text


def parse_examine(text: str) -> dict[str, Any]:
    """Parse an examine report into sections plus promoted well-known fields."""
    sections: dict[str, dict[str, Any]] = {}
    devices: list[dict[str, str]] = []
    current: dict[str, Any] | None = None
    section_name: str | None = None

    for line in text.split("\n"):
        if not line.strip() or set(line.strip()) <= {"-"}:
            continue

        if match := _BDF_LINE.match(line.strip()):
            bdf, name = match.group(1), match.group(2).strip()
            if not any(d["bdf"] == bdf for d in devices):
                devices.append({"bdf": bdf, "name": name})
            continue

        # Device table rows: |BDF|Name| -- skip the header and separator.
        if match := _TABLE_ROW.match(line):
            left, right = match.group(1).strip(), match.group(2).strip()
            if left and not left.startswith("-") and left != "BDF":
                bdf = left.strip("[]")
                if bdf and not any(d["bdf"] == bdf for d in devices):
                    devices.append({"bdf": bdf, "name": right})
            continue

        if not line.startswith(" ") and ":" not in line:
            section_name = line.strip()
            current = sections.setdefault(section_name, {})
            continue

        if ":" in line and current is not None:
            key, _, value = line.partition(":")  # FIRST colon only
            key = key.strip()
            if key:
                current[key] = _value(value)
        elif ":" in line:
            # A field before any section header (e.g. "Estimated Power").
            key, _, value = line.partition(":")
            sections.setdefault("Platform", {})[key.strip()] = _value(value)

    xrt = sections.get("XRT", {})
    platform = sections.get("Platform", {})
    system = sections.get("System Configuration", {})

    columns = platform.get("Total Columns")
    return {
        "sections": sections,
        "devices": devices,
        "xrt_version": xrt.get("Version"),
        "npu_firmware_version": xrt.get("NPU Firmware Version"),
        "amdxdna_version": xrt.get("amdxdna Version"),
        "device_bdf": devices[0]["bdf"] if devices else None,
        "device_name": devices[0]["name"] if devices else platform.get("Name"),
        "power_mode": platform.get("Power Mode"),
        "total_columns": int(columns) if columns and str(columns).isdigit() else None,
        # N/A on this hardware -- must stay None so the UI shows "unsupported"
        # rather than a plausible-looking 0 W.
        "estimated_power": platform.get("Estimated Power"),
        "aie_partitions": sections.get("AIE Partitions", {}),
        "model": system.get("Model"),
        "processor": system.get("Processor"),
        "bios_version": system.get("BIOS Version"),
    }


async def _fetch(report: str) -> dict[str, Any]:
    if report not in REPORTS:
        raise errors.invalid_value("report", f"must be one of {', '.join(REPORTS)}")
    result = await run_tool("xrt-smi", "--batch", "examine", "-r", report)
    if not result.ok:
        raise result.error or errors.parse_error("xrt-smi", "unknown failure")
    data = parse_examine(result.stdout)
    data["_duration_ms"] = result.duration_ms
    return data


async def examine(report: str = "all", *, force: bool = False) -> dict[str, Any]:
    entry = await cache.get(
        f"xrt:{report}", lambda: _fetch(report), ttl=config.CACHE_TTL["xrt"], force=force
    )
    return entry.value
