"""Unified memory -- the one place the APU presentation rule is enforced.

On Strix Halo the three available sources disagree *by design*:

* ``rocm-smi --showmeminfo vram``  ->  the 512 MB fixed BIOS carveout, which sits
  at ~90% used at idle. Reported as "VRAM" it reads as a machine about to run
  out of graphics memory. It is not.
* ``mem_info_gtt_total``          ->  ~107 GB, the dynamically-shared pool that
  vLLM, ComfyUI and PyTorch actually allocate from.
* ``ttm pages_limit``             ->  the kernel's ceiling on that pool.

So the canonical object composed here always presents **GTT/unified as primary**
and the carveout as clearly-labelled secondary. No endpoint returns a bare
``vram_pct``; getting this wrong is how a dashboard tells you the GPU is full
when 100 GB is free.
"""

from __future__ import annotations

import re
from typing import Any

import psutil

from backend import config
from backend.core import sysfs
from backend.core.cache import cache
from backend.core.runner import run_tool

_TTM_RE = re.compile(r"Current TTM pages limit:\s*(\d+)\s*pages\s*\(([\d.]+)\s*GB\)")
_TOTAL_RE = re.compile(r"Total system memory:\s*([\d.]+)\s*GB")

PAGE_SIZE = 4096


def parse_amd_ttm(text: str) -> dict[str, Any]:
    """Parse amd-ttm output (emoji + colour already stripped by the runner)."""
    out: dict[str, Any] = {"pages_limit": None, "limit_bytes": None, "system_total_gb": None}
    if match := _TTM_RE.search(text):
        pages = int(match.group(1))
        out["pages_limit"] = pages
        out["limit_bytes"] = pages * PAGE_SIZE
        out["limit_gb"] = float(match.group(2))
    if match := _TOTAL_RE.search(text):
        out["system_total_gb"] = float(match.group(1))
    return out


async def _fetch_ttm() -> dict[str, Any]:
    # Prefer sysfs: no subprocess, and it is the value amd-ttm itself reads.
    pages = sysfs.read_int(config.TTM_PAGES_LIMIT)
    if pages is not None:
        return {
            "pages_limit": pages,
            "limit_bytes": pages * PAGE_SIZE,
            "limit_gb": round(pages * PAGE_SIZE / 1024**3, 2),
            "source": "sysfs",
        }
    result = await run_tool("amd-ttm")
    if not result.ok:
        return {"pages_limit": None, "source": "unavailable"}
    parsed = parse_amd_ttm(result.stdout)
    parsed["source"] = "amd-ttm"
    return parsed


async def ttm(*, force: bool = False) -> dict[str, Any]:
    entry = await cache.get("amd_ttm", _fetch_ttm, ttl=config.CACHE_TTL["amd_ttm"], force=force)
    return entry.value


def _pct(used: int | None, total: int | None) -> float | None:
    if not used or not total:
        return None
    return round(used / total * 100, 1)


async def unified(*, force: bool = False) -> dict[str, Any]:
    """The canonical memory object."""
    from backend.collectors import rocm  # local import avoids a cycle

    gpu = await rocm.memory(force=force)
    ttm_info = await ttm(force=force)

    vram_total = gpu.get("vram_total") or sysfs.read_int(config.VRAM_TOTAL)
    vram_used = gpu.get("vram_used")
    gtt_total = gpu.get("gtt_total") or sysfs.read_int(config.GTT_TOTAL)
    gtt_used = gpu.get("gtt_used")

    host_mem = psutil.virtual_memory()

    return {
        "primary": {
            "label": "Unified (GTT)",
            "total": gtt_total,
            "used": gtt_used,
            "percent": _pct(gtt_used, gtt_total),
            "note": "Dynamically shared pool used by GPU compute workloads.",
        },
        "secondary": {
            "label": "VRAM carveout",
            "total": vram_total,
            "used": vram_used,
            "percent": _pct(vram_used, vram_total),
            "note": (
                "Fixed BIOS carveout reserved before boot. High utilisation here "
                "is normal and is NOT a capacity limit."
            ),
        },
        "ttm": ttm_info,
        "host": {
            "total": host_mem.total,
            "used": host_mem.total - host_mem.available,
            "available": host_mem.available,
            "percent": host_mem.percent,
        },
    }
