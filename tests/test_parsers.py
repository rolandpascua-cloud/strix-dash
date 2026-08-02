"""Offline parser tests.

These run entirely against captured fixtures in tests/fixtures/raw/ -- no
hardware, no network, no subprocesses. That is the whole point of capturing the
fixtures during build phase 0: CI on a hosted runner has no NPU, no ROCm and no
ASUS platform driver, but must still catch a parser regression.

The fixtures deliberately retain their original ANSI escape bytes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.collectors.flm import _shape_validate
from backend.core.ansi import clean, has_escapes, strip_ansi

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.stdout").read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# ANSI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["flm_validate_text", "amd_ttm", "rocm_smi_concise", "rocminfo"],
)
def test_tools_really_do_emit_escapes_when_piped(name: str) -> None:
    """Guards the premise: these tools colour their output with no TTY."""
    assert has_escapes(fixture(name)), f"{name} fixture should contain escapes"


@pytest.mark.parametrize(
    "name",
    [
        "flm_validate_text",
        "amd_ttm",
        "rocm_smi_concise",
        "rocminfo",
        "flm_validate_json",
        "xrt_examine_all",
        "tuned_adm_list",
    ],
)
def test_no_escape_survives_cleaning(name: str) -> None:
    assert not has_escapes(clean(fixture(name)))


def test_strip_handles_non_colour_finals() -> None:
    # Erase-line and cursor-move use finals other than "m".
    assert strip_ansi("a\x1b[2Kb\x1b[1;3Hc\x1b[0m") == "abc"


def test_clean_removes_trailing_padding() -> None:
    # xrt-smi right-pads values, which would break exact-match assertions.
    assert clean("Name  : RyzenAI-npu5   \nPower : Default  ") == (
        "Name  : RyzenAI-npu5\nPower : Default"
    )


def test_amd_ttm_emoji_survives_but_escapes_do_not() -> None:
    cleaned = clean(fixture("amd_ttm"))
    assert "\x1b" not in cleaned
    assert "Current TTM pages limit: 28174103 pages (107.48 GB)" in cleaned


# ---------------------------------------------------------------------------
# flm
# ---------------------------------------------------------------------------


def test_flm_validate_json_is_clean() -> None:
    """The -j form must not need stripping; that is why we prefer it."""
    assert not has_escapes(fixture("flm_validate_json"))


def test_flm_validate_shape() -> None:
    shaped = _shape_validate(json.loads(fixture("flm_validate_json")))

    assert shaped["ready"] is True
    assert shaped["kernel"] == "6.18.35+rex+2-amd64"
    assert shaped["memlock_limit"] == "infinity"
    assert shaped["memlock_ok"] is True
    assert shaped["amdxdna_version"] == "0.6"
    assert shaped["failed_checks"] == []

    (device,) = shaped["devices"]
    assert device["device"] == "/dev/accel/accel0"
    assert device["columns"] == 8
    assert device["firmware_version"] == "1.1.2.65"


def test_flm_validate_reports_failures_without_raising() -> None:
    """A degraded stack must produce data, not an exception."""
    raw = json.loads(fixture("flm_validate_json"))
    raw["memlock_ok"] = False
    raw["memlock_limit"] = "8388608"
    raw["ready"] = False

    shaped = _shape_validate(raw)
    assert shaped["ready"] is False
    assert "memlock" in shaped["failed_checks"]


def test_flm_validate_tolerates_missing_devices() -> None:
    shaped = _shape_validate({"ready": False})
    assert shaped["devices"] == []
    assert shaped["ready"] is False


def test_flm_version_fixture() -> None:
    assert json.loads(fixture("flm_version_json"))["version"] == "0.9.46"


# ---------------------------------------------------------------------------
# tuned -- the exit-code trap
# ---------------------------------------------------------------------------


def test_tuned_exits_zero_while_daemon_is_down() -> None:
    """Exit code 0 here means nothing; the daemon was not running."""
    assert (FIXTURES / "tuned_adm_active.exit").read_text().strip() == "0"
    assert "Cannot talk to TuneD daemon" in (FIXTURES / "tuned_adm_active.stderr").read_text()
    assert "Preset profile:" in fixture("tuned_adm_active")


# ---------------------------------------------------------------------------
# rocm-smi -- string values and the APU memory trap
# ---------------------------------------------------------------------------


def test_rocm_smi_values_are_strings_not_numbers() -> None:
    card = json.loads(fixture("rocm_smi_full_json"))["card0"]
    assert isinstance(card["VRAM Total Memory (B)"], str)


def test_gtt_pool_dwarfs_the_vram_carveout() -> None:
    """Why no endpoint may report a bare vram_pct on this APU.

    The carveout is 512 MB and reads ~90% used at idle, which would be alarming
    and wrong; the pool that actually matters is the ~107 GB GTT.
    """
    card = json.loads(fixture("rocm_smi_full_json"))["card0"]
    vram_total = int(card["VRAM Total Memory (B)"])
    gtt_total = int(card["GTT Total Memory (B)"])

    assert vram_total == 536870912  # 512 MiB
    assert gtt_total > 100 * 1024**3
    assert gtt_total > vram_total * 200
