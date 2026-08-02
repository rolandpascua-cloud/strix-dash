"""ANSI/terminal escape removal.

Several AMD tools emit colour escapes even when their output is piped -- they do
no isatty() check. Confirmed on this hardware:

    flm validate      4 escape sequences
    amd-ttm           2  (plus a leading emoji)
    rocm-smi          1
    rocminfo          1

The JSON-producing invocations (``flm ... -j``, ``rocm-smi --json``) are clean,
but everything routed through :mod:`backend.core.runner` gets stripped anyway so
no parser has to care which is which.
"""

from __future__ import annotations

import re

# CSI: ESC [ ... final-byte. The final byte is any @-~ character, not just "m" --
# rocm-smi emits erase-line (K) and rocminfo emits cursor moves alongside colour.
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# OSC: ESC ] ... terminated by BEL or ST (ESC \).
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

# Remaining two-character escapes (ESC ( B and friends).
_SIMPLE = re.compile(r"\x1b[@-Z\\-_]")

_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)


def strip_ansi(text: str) -> str:
    """Remove every escape sequence from *text*."""
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    return _SIMPLE.sub("", text)


def clean(text: str) -> str:
    """Strip escapes, normalise newlines, and drop per-line trailing whitespace.

    xrt-smi pads values out with spaces (``Name : RyzenAI-npu5   ``), so the
    trailing-whitespace pass matters for exact-match assertions downstream.
    """
    text = strip_ansi(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _TRAILING_WS.sub("", text)


def has_escapes(text: str) -> bool:
    """True if any escape byte survives. Used by the test-suite as a guard."""
    return "\x1b" in text
