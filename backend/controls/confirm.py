"""Confirm tokens for consequential writes.

Fan curves and power limits can affect thermals, so those writes are two-step:
the client asks for a token (receiving the current value, the proposed value and
a warning to display), then submits the write with that token.

This is a deliberate speed bump against an accidental slider drag, not a
security control -- CSRF is handled by the middleware in backend.main.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

from backend.core import errors

TTL_SECONDS = 30.0


@dataclass
class Token:
    value: str
    control_id: str
    proposed: Any
    issued_at: float

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.issued_at) > TTL_SECONDS


_tokens: dict[str, Token] = {}


def _sweep() -> None:
    for key in [k for k, t in _tokens.items() if t.expired]:
        _tokens.pop(key, None)


def issue(control_id: str, proposed: Any, current: Any, warning: str) -> dict[str, Any]:
    _sweep()
    token = Token(
        value=secrets.token_urlsafe(16),
        control_id=control_id,
        proposed=proposed,
        issued_at=time.monotonic(),
    )
    _tokens[token.value] = token
    return {
        "token": token.value,
        "expires_in_s": int(TTL_SECONDS),
        "control_id": control_id,
        "current_value": current,
        "proposed_value": proposed,
        "warning": warning,
    }


def consume(token_value: str | None, control_id: str, proposed: Any) -> None:
    """Validate and burn a token. Raises ToolError when it doesn't check out."""
    _sweep()
    if not token_value:
        raise errors.ToolError(
            code=errors.ErrorCode.CONFIRM_REQUIRED,
            message="This change requires confirmation",
            hint="Request a token from /controls/confirm, then resubmit.",
        )

    token = _tokens.pop(token_value, None)
    if token is None:
        raise errors.ToolError(
            code=errors.ErrorCode.CONFIRM_EXPIRED,
            message="Confirmation token is invalid or already used",
            hint="Request a fresh token and try again.",
        )
    if token.expired:
        raise errors.ToolError(
            code=errors.ErrorCode.CONFIRM_EXPIRED,
            message=f"Confirmation expired after {int(TTL_SECONDS)}s",
            hint="Request a fresh token and try again.",
        )
    # A token is bound to one control AND one value, so it cannot be reused to
    # apply something other than what the user was shown.
    if token.control_id != control_id or token.proposed != proposed:
        raise errors.ToolError(
            code=errors.ErrorCode.CONFIRM_REQUIRED,
            message="Confirmation does not match the requested change",
            hint="Request a token for this exact value.",
        )
