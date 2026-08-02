"""ROCm collectors: rocm-smi (live) and rocminfo (static topology).

rocm-smi notes
--------------
* ``--json`` works only alongside explicit ``--show*`` queries. A bare
  ``rocm-smi --json`` exits 1 with "Cannot print JSON/CSV output for concise
  output", so the concise table is never used.
* Every value comes back as a **string**, including numbers.
* Keys are the human-readable labels verbatim, units included, e.g.
  ``"VRAM Total Memory (B)"``.

rocminfo notes
--------------
No flags at all -- ``--help`` is ignored and dumps the full 256-line report. It
is slow and purely descriptive of the hardware, so it is cached once at startup.
The ``aie2p`` NPU agent has an **empty ISA Info block**, so agent ISA lists must
default to [] and never be indexed.
"""

from __future__ import annotations

import re
from typing import Any

from backend import config
from backend.core import errors
from backend.core.cache import cache
from backend.core.runner import run_tool

_MEM_ARGS = ("--showmeminfo", "vram", "gtt")
_LIVE_ARGS = (*_MEM_ARGS, "--showtemp", "--showpower", "--showuse")


def _num(value: Any) -> float | int | None:
    """rocm-smi hands back strings, and 'N/A' for unsupported sensors."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NONE", "NA"}:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return None


def _first_card(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key, value in payload.items():
        if key.startswith("card") and isinstance(value, dict):
            return value
    return {}


def parse_smi(payload: Any) -> dict[str, Any]:
    card = _first_card(payload)
    return {
        "vram_total": _num(card.get("VRAM Total Memory (B)")),
        "vram_used": _num(card.get("VRAM Total Used Memory (B)")),
        "gtt_total": _num(card.get("GTT Total Memory (B)")),
        "gtt_used": _num(card.get("GTT Total Used Memory (B)")),
        "temperature_c": _num(card.get("Temperature (Sensor edge) (C)")),
        "power_w": _num(card.get("Current Socket Graphics Package Power (W)")),
        "gpu_percent": _num(card.get("GPU use (%)")),
    }


async def _fetch_live() -> dict[str, Any]:
    result = await run_tool("rocm-smi", *_LIVE_ARGS, "--json", parse="json")
    if not result.ok:
        raise result.error or errors.parse_error("rocm-smi", "unknown failure")
    data = parse_smi(result.parsed)
    data["_duration_ms"] = result.duration_ms
    return data


async def live(*, force: bool = False) -> dict[str, Any]:
    entry = await cache.get("rocm_smi", _fetch_live, ttl=config.CACHE_TTL["rocm_smi"], force=force)
    return entry.value


async def memory(*, force: bool = False) -> dict[str, Any]:
    """Memory subset, tolerating a missing rocm-smi so the panel still renders."""
    try:
        return await live(force=force)
    except errors.ToolError:
        return {}


# ---------------------------------------------------------------------------
# rocminfo
# ---------------------------------------------------------------------------

_AGENT_RE = re.compile(r"^Agent\s+(\d+)\s*$")
_FIELD_RE = re.compile(r"^\s{2,}([A-Za-z][^:]*?):\s*(.*)$")


def parse_rocminfo(text: str) -> dict[str, Any]:
    """Extract agents from the rocminfo report.

    Deliberately tolerant: fields are collected into a dict per agent and only
    the interesting ones are promoted, so an added or reordered line upstream
    cannot break the parse.
    """
    agents: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_isa = False

    for line in text.split("\n"):
        if _AGENT_RE.match(line.strip()) and line.startswith("Agent"):
            current = {"fields": {}, "isa": []}
            agents.append(current)
            in_isa = False
            continue
        if current is None:
            continue

        stripped = line.strip()
        if stripped.startswith("ISA Info"):
            # NOTE: the aie2p agent has this header with NOTHING under it.
            in_isa = True
            continue

        match = _FIELD_RE.match(line)
        if not match:
            continue
        key, value = match.group(1).strip(), match.group(2).strip()
        if in_isa and key == "Name" and value.startswith("amdgcn"):
            current["isa"].append(value)
            continue
        if key == "Name" and not in_isa:
            in_isa = False
        current["fields"].setdefault(key, value)

    shaped = []
    for index, agent in enumerate(agents, start=1):
        fields = agent["fields"]
        shaped.append(
            {
                "index": index,
                "name": fields.get("Name"),
                "marketing_name": fields.get("Marketing Name"),
                "device_type": fields.get("Device Type"),
                "uuid": fields.get("Uuid"),
                "compute_units": _num(fields.get("Compute Unit")),
                "max_clock_mhz": _num(fields.get("Max Clock Freq. (MHz)")),
                "chip_id": fields.get("Chip ID"),
                "isa": agent["isa"],
            }
        )

    gpus = [a for a in shaped if a["device_type"] == "GPU"]
    dsps = [a for a in shaped if a["device_type"] == "DSP"]
    return {
        "agents": shaped,
        "agent_count": len(shaped),
        "gpu": gpus[0] if gpus else None,
        "npu": dsps[0] if dsps else None,
    }


async def _fetch_info() -> dict[str, Any]:
    result = await run_tool("rocminfo")
    if not result.ok:
        raise result.error or errors.parse_error("rocminfo", "unknown failure")
    data = parse_rocminfo(result.stdout)
    if not data["agents"]:
        raise errors.parse_error("rocminfo", "no agents found", result.stdout)
    data["_duration_ms"] = result.duration_ms
    return data


async def info(*, force: bool = False) -> dict[str, Any]:
    """Static topology. TTL is None: computed once, then never re-run."""
    entry = await cache.get("rocminfo", _fetch_info, ttl=config.CACHE_TTL["rocminfo"], force=force)
    return entry.value
