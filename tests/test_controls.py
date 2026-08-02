"""Offline tests for control validation and the confirm-token flow.

Pure logic only -- no hardware is touched. The write paths themselves are
verified live against the installed service.
"""

from __future__ import annotations

import pytest

from backend.controls import confirm
from backend.controls.hardware import _validate_curve
from backend.core import sysfs
from backend.core.errors import ErrorCode, ToolError

# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-40, 20), (0, 20), (50, 50), (100, 100), (250, 100)],
)
def test_battery_limit_clamps_into_range(value: int, expected: int) -> None:
    assert sysfs.clamp(value, 20, 100) == expected


def test_backlight_clamps_to_hardware_max() -> None:
    # This machine reports max_brightness = 3; a slider must not exceed it.
    assert sysfs.clamp(99, 0, 3) == 3
    assert sysfs.clamp(-5, 0, 3) == 0


# ---------------------------------------------------------------------------
# Fan curve validation
# ---------------------------------------------------------------------------


def _curve(temps: list[int], pwms: list[int]) -> list[dict[str, int]]:
    return [{"temp": t, "pwm": p} for t, p in zip(temps, pwms, strict=True)]


VALID_TEMPS = [48, 53, 57, 60, 63, 65, 70, 76]
VALID_PWMS = [2, 22, 30, 43, 56, 68, 89, 102]


def test_valid_curve_passes_through() -> None:
    cleaned = _validate_curve(_curve(VALID_TEMPS, VALID_PWMS))
    assert [p["temp"] for p in cleaned] == VALID_TEMPS
    assert [p["pwm"] for p in cleaned] == VALID_PWMS
    assert [p["point"] for p in cleaned] == list(range(1, 9))


def test_curve_must_have_exactly_eight_points() -> None:
    with pytest.raises(ToolError) as excinfo:
        _validate_curve(_curve(VALID_TEMPS[:4], VALID_PWMS[:4]))
    assert excinfo.value.code == ErrorCode.INVALID_VALUE


def test_curve_rejects_decreasing_temperatures() -> None:
    temps = list(VALID_TEMPS)
    temps[4] = 55  # below the previous point
    with pytest.raises(ToolError, match="non-decreasing"):
        _validate_curve(_curve(temps, VALID_PWMS))


def test_curve_rejects_decreasing_pwm() -> None:
    pwms = list(VALID_PWMS)
    pwms[6] = 10  # fan would slow down as it gets hotter
    with pytest.raises(ToolError, match="non-decreasing"):
        _validate_curve(_curve(VALID_TEMPS, pwms))


def test_curve_clamps_out_of_range_values() -> None:
    cleaned = _validate_curve(_curve([0] * 8, [999] * 8))
    assert all(p["temp"] == 20 for p in cleaned)  # FAN_TEMP_RANGE floor
    assert all(p["pwm"] == 255 for p in cleaned)  # PWM_RANGE ceiling


# ---------------------------------------------------------------------------
# Confirm tokens
# ---------------------------------------------------------------------------


def test_token_round_trip() -> None:
    issued = confirm.issue("fan-curve", {"fan": 1}, {"fan": 0}, "careful")
    confirm.consume(issued["token"], "fan-curve", {"fan": 1})


def test_missing_token_is_rejected() -> None:
    with pytest.raises(ToolError) as excinfo:
        confirm.consume(None, "fan-curve", {"fan": 1})
    assert excinfo.value.code == ErrorCode.CONFIRM_REQUIRED


def test_token_is_single_use() -> None:
    issued = confirm.issue("fan-curve", 1, 0, "careful")
    confirm.consume(issued["token"], "fan-curve", 1)
    with pytest.raises(ToolError) as excinfo:
        confirm.consume(issued["token"], "fan-curve", 1)
    assert excinfo.value.code == ErrorCode.CONFIRM_EXPIRED


def test_token_is_bound_to_its_value() -> None:
    """A token shown for one value must not apply a different one."""
    issued = confirm.issue("fan-curve", 1, 0, "careful")
    with pytest.raises(ToolError) as excinfo:
        confirm.consume(issued["token"], "fan-curve", 2)
    assert excinfo.value.code == ErrorCode.CONFIRM_REQUIRED


def test_token_is_bound_to_its_control() -> None:
    issued = confirm.issue("fan-curve", 1, 0, "careful")
    with pytest.raises(ToolError):
        confirm.consume(issued["token"], "throttle-policy", 1)


def test_expired_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    issued = confirm.issue("fan-curve", 1, 0, "careful")
    token = confirm._tokens[issued["token"]]
    token.issued_at -= confirm.TTL_SECONDS + 1
    with pytest.raises(ToolError) as excinfo:
        confirm.consume(issued["token"], "fan-curve", 1)
    assert excinfo.value.code == ErrorCode.CONFIRM_EXPIRED


# ---------------------------------------------------------------------------
# sysfs write allowlist
# ---------------------------------------------------------------------------


def test_write_refuses_paths_outside_the_allowlist(tmp_path) -> None:
    """A bug or injected value must not become an arbitrary file write."""
    target = tmp_path / "not-a-control"
    target.write_text("0")
    with pytest.raises(ToolError) as excinfo:
        sysfs.write_text(target, "1")
    assert excinfo.value.code == ErrorCode.NOT_SUPPORTED
    assert target.read_text() == "0"


def test_ppt_nodes_are_not_writable_in_this_version() -> None:
    """TDP stays read-only until the ppt_* units are confirmed."""
    from backend import config

    assert config.PPT_WRITABLE is False
    allowed = sysfs._writable_registry()
    for name in config.PPT_NODES:
        assert (config.ASUS_PLATFORM / name) not in allowed
