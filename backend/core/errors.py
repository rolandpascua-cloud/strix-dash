"""Error taxonomy.

Every failure the dashboard can show the user is one of these codes. The frontend
switches on ``code`` and never string-matches a message, so wording can change
without breaking the UI.

Degraded-but-understood conditions (a tool that isn't installed, a daemon that
isn't running) are *not* HTTP errors -- they return 200 with ``ok: false`` so a
panel renders its own explanatory state instead of a network failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ErrorCode:
    TOOL_MISSING = "TOOL_MISSING"
    TOOL_NOT_EXECUTABLE = "TOOL_NOT_EXECUTABLE"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_FAILED = "TOOL_FAILED"
    PARSE_ERROR = "PARSE_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DAEMON_INACTIVE = "DAEMON_INACTIVE"
    MEMLOCK_TOO_LOW = "MEMLOCK_TOO_LOW"
    INVALID_VALUE = "INVALID_VALUE"
    CONFIRM_REQUIRED = "CONFIRM_REQUIRED"
    CONFIRM_EXPIRED = "CONFIRM_EXPIRED"
    APPLIED_MISMATCH = "APPLIED_MISMATCH"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    INTERNAL = "INTERNAL"


# Conditions the user can act on, rather than faults. These stay HTTP 200.
DEGRADED_CODES = frozenset(
    {
        ErrorCode.TOOL_MISSING,
        ErrorCode.TOOL_NOT_EXECUTABLE,
        ErrorCode.DAEMON_INACTIVE,
        ErrorCode.MEMLOCK_TOO_LOW,
        ErrorCode.NOT_SUPPORTED,
    }
)


@dataclass
class ToolError(Exception):
    """A normalised failure with everything the UI needs to explain itself."""

    code: str
    message: str
    hint: str | None = None
    install_command: str | None = None
    retryable: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message}"

    @property
    def degraded(self) -> bool:
        return self.code in DEGRADED_CODES

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.hint:
            out["hint"] = self.hint
        if self.install_command:
            out["install_command"] = self.install_command
        if self.detail:
            out["detail"] = self.detail
        return out


def tool_missing(name: str, *, install_command: str | None = None) -> ToolError:
    return ToolError(
        code=ErrorCode.TOOL_MISSING,
        message=f"{name} is not installed",
        hint=f"Install {name} to enable this panel.",
        install_command=install_command,
    )


def tool_not_executable(name: str, path: str) -> ToolError:
    return ToolError(
        code=ErrorCode.TOOL_NOT_EXECUTABLE,
        message=f"{name} exists at {path} but is not executable by this service",
        hint=(
            "If this resolves into a user's home directory it is unreachable by "
            "the strix-dash service user; use the system-wide copy instead."
        ),
        detail={"path": path},
    )


def timeout(name: str, seconds: float) -> ToolError:
    return ToolError(
        code=ErrorCode.TOOL_TIMEOUT,
        message=f"{name} did not finish within {seconds:g}s",
        retryable=True,
        detail={"timeout_s": seconds},
    )


def parse_error(name: str, reason: str, raw: str = "") -> ToolError:
    return ToolError(
        code=ErrorCode.PARSE_ERROR,
        message=f"Could not parse {name} output: {reason}",
        hint="The tool's output format may have changed between versions.",
        # Bounded so a runaway tool can't inflate the response.
        detail={"raw_excerpt": raw[:2048]} if raw else {},
    )


def permission_denied(what: str) -> ToolError:
    return ToolError(
        code=ErrorCode.PERMISSION_DENIED,
        message=f"Permission denied writing {what}",
        hint=(
            "The tmpfiles.d grant may not have been applied. Try: "
            "sudo systemd-tmpfiles --create /usr/lib/tmpfiles.d/strix-dash.conf"
        ),
        detail={"target": what},
    )


def daemon_inactive(name: str, *, start_command: str) -> ToolError:
    return ToolError(
        code=ErrorCode.DAEMON_INACTIVE,
        message=f"The {name} daemon is not running",
        hint=f"Start it with: {start_command}",
        install_command=start_command,
    )


def invalid_value(what: str, reason: str, **detail: Any) -> ToolError:
    return ToolError(
        code=ErrorCode.INVALID_VALUE,
        message=f"Invalid value for {what}: {reason}",
        detail=detail,
    )


def not_supported(what: str, reason: str) -> ToolError:
    return ToolError(
        code=ErrorCode.NOT_SUPPORTED,
        message=f"{what} is not supported on this hardware",
        hint=reason,
    )
