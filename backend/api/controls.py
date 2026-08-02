"""Hardware control endpoints.

Every write returns the READ-BACK, never an echo. ``verified: false`` means the
firmware applied something other than what was asked -- surfaced as a warning in
the UI, not an error.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.controls import confirm, hardware
from backend.core import models
from backend.core.errors import ToolError

router = APIRouter(prefix="/controls", tags=["controls"])


class ChoiceBody(BaseModel):
    value: str
    confirm_token: str | None = None


class IntBody(BaseModel):
    value: int
    confirm_token: str | None = None


class CurvePoint(BaseModel):
    temp: int
    pwm: int


class FanCurveBody(BaseModel):
    fan: int = Field(ge=1, le=2)
    points: list[CurvePoint]
    confirm_token: str | None = None


class ConfirmBody(BaseModel):
    control_id: str
    value: Any


async def _write(source: str, coroutine) -> Any:
    try:
        return models.ok(await coroutine, source=source)
    except ToolError as exc:
        return models.respond(exc, source=source)


@router.get("")
async def get_controls() -> Any:
    try:
        return models.ok(await hardware.read_all(), source="sysfs")
    except ToolError as exc:
        return models.respond(exc, source="sysfs")


@router.post("/confirm")
async def request_confirm(body: ConfirmBody) -> Any:
    """Issue a short-lived token for a change that needs explicit consent."""
    try:
        state = await hardware.read_all()
        key = body.control_id.replace("-", "_")
        current = state.get(key, {}).get("value")
        return models.ok(
            hardware.confirm_for(body.control_id, body.value, current),
            source="confirm",
        )
    except ToolError as exc:
        return models.respond(exc, source="confirm")


@router.post("/platform-profile")
async def set_platform_profile(body: ChoiceBody) -> Any:
    return await _write("sysfs", hardware.set_platform_profile(body.value))


@router.post("/battery-limit")
async def set_battery_limit(body: IntBody) -> Any:
    return await _write("sysfs", hardware.set_battery_limit(body.value))


@router.post("/kbd-backlight")
async def set_kbd_backlight(body: IntBody) -> Any:
    return await _write("sysfs", hardware.set_kbd_backlight(body.value))


@router.post("/npu-pmode")
async def set_npu_pmode(body: ChoiceBody) -> Any:
    return await _write("xrt-smi", hardware.set_npu_pmode(body.value))


@router.post("/tuned")
async def set_tuned(body: ChoiceBody) -> Any:
    return await _write("tuned-adm", hardware.set_tuned_profile(body.value))


@router.post("/throttle-policy")
async def set_throttle_policy(body: IntBody) -> Any:
    try:
        confirm.consume(body.confirm_token, "throttle-policy", body.value)
    except ToolError as exc:
        return models.respond(exc, source="sysfs")
    return await _write("sysfs", hardware.set_throttle_policy(body.value))


@router.post("/fan-curve")
async def set_fan_curve(body: FanCurveBody) -> Any:
    points = [p.model_dump() for p in body.points]
    try:
        confirm.consume(body.confirm_token, "fan-curve", {"fan": body.fan, "points": points})
    except ToolError as exc:
        return models.respond(exc, source="sysfs")
    return await _write("sysfs", hardware.set_fan_curve(body.fan, points))
