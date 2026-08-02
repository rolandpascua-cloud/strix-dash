"""Offline tests for the xrt-smi, rocminfo, rocm-smi and amd-ttm parsers."""

from __future__ import annotations

import json
from pathlib import Path

from backend.collectors.memory import parse_amd_ttm
from backend.collectors.rocm import parse_rocminfo, parse_smi
from backend.collectors.xrt import parse_examine
from backend.core.ansi import clean

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


def fixture(name: str) -> str:
    """Fixtures are raw captures; clean() them exactly as the runner would."""
    return clean((FIXTURES / f"{name}.stdout").read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# xrt-smi
# ---------------------------------------------------------------------------


def test_xrt_examine_all() -> None:
    data = parse_examine(fixture("xrt_examine_all"))

    assert data["xrt_version"] == "2.21.75"
    assert data["npu_firmware_version"] == "1.1.2.65"
    assert data["total_columns"] == 8
    assert data["device_name"] == "RyzenAI-npu5"


def test_xrt_bdf_survives_the_colon_split() -> None:
    """The BDF contains colons; splitting on the last one would mangle it.

    The value is redacted in the fixture (see scripts/sanitize-fixtures.sh) but
    keeps its real shape, so this still exercises the parsing behaviour.
    """
    bdf = parse_examine(fixture("xrt_examine_all"))["device_bdf"]
    assert bdf.count(":") == 2 and "." in bdf
    assert bdf == "0000:00:00.1"


def test_xrt_estimated_power_is_none_not_zero() -> None:
    """This NPU reports N/A. Zero would look like a real 0 W reading."""
    data = parse_examine(fixture("xrt_examine_platform"))
    assert data["estimated_power"] is None


def test_xrt_platform_report() -> None:
    data = parse_examine(fixture("xrt_examine_platform"))
    assert data["power_mode"] == "Default"
    assert data["total_columns"] == 8


def test_xrt_host_report_has_system_details() -> None:
    data = parse_examine(fixture("xrt_examine_host"))
    assert data["processor"] == "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S"
    assert data["model"].startswith("ROG Flow Z13")


def test_xrt_aie_partitions_does_not_crash() -> None:
    data = parse_examine(fixture("xrt_examine_aie"))
    assert isinstance(data["sections"], dict)


# ---------------------------------------------------------------------------
# rocminfo
# ---------------------------------------------------------------------------


def test_rocminfo_finds_three_agents() -> None:
    data = parse_rocminfo(fixture("rocminfo"))
    assert data["agent_count"] == 3


def test_rocminfo_identifies_gpu_and_npu() -> None:
    data = parse_rocminfo(fixture("rocminfo"))
    assert data["gpu"]["name"] == "gfx1151"
    assert data["gpu"]["marketing_name"] == "Radeon 8060S Graphics"
    assert data["npu"]["name"] == "aie2p"
    assert data["npu"]["marketing_name"] == "RyzenAI-npu5"


def test_rocminfo_npu_has_empty_isa_and_does_not_raise() -> None:
    """The aie2p agent's ISA Info block is present but empty.

    A parser that assumes at least one ISA per agent crashes here.
    """
    data = parse_rocminfo(fixture("rocminfo"))
    assert data["npu"]["isa"] == []
    assert data["gpu"]["isa"], "the GPU agent should still report its ISAs"
    assert "amdgcn-amd-amdhsa--gfx1151" in data["gpu"]["isa"]


def test_rocminfo_every_agent_has_a_list_isa() -> None:
    for agent in parse_rocminfo(fixture("rocminfo"))["agents"]:
        assert isinstance(agent["isa"], list)


# ---------------------------------------------------------------------------
# rocm-smi
# ---------------------------------------------------------------------------


def test_rocm_smi_coerces_string_values() -> None:
    data = parse_smi(json.loads(fixture("rocm_smi_full_json")))
    assert data["vram_total"] == 536870912
    assert data["gtt_total"] == 115401125888
    assert isinstance(data["temperature_c"], float)
    assert data["gpu_percent"] == 6


def test_rocm_smi_memory_only_query() -> None:
    data = parse_smi(json.loads(fixture("rocm_smi_mem_json")))
    assert data["vram_total"] == 536870912
    assert data["temperature_c"] is None  # not requested -> absent, not zero


def test_rocm_smi_handles_na_sensors() -> None:
    data = parse_smi({"card0": {"Current Socket Graphics Package Power (W)": "N/A"}})
    assert data["power_w"] is None


# ---------------------------------------------------------------------------
# amd-ttm
# ---------------------------------------------------------------------------


def test_amd_ttm_parses_through_emoji_and_colour() -> None:
    data = parse_amd_ttm(fixture("amd_ttm"))
    assert data["pages_limit"] == 28174103
    assert data["limit_gb"] == 107.48
    assert data["system_total_gb"] == 124.95


def test_amd_ttm_page_maths_matches_reported_gb() -> None:
    data = parse_amd_ttm(fixture("amd_ttm"))
    assert abs(data["limit_bytes"] / 1024**3 - data["limit_gb"]) < 0.01
