"""Static configuration: tool paths, sysfs nodes, timeouts, cache TTLs.

Every external tool is referenced by ABSOLUTE PATH. This is not stylistic --
``amd-ttm`` resolves through PATH to ``~/.local/bin/amd-ttm`` (a pipx install
inside a 0700 home) which the service user cannot execute, while the usable
system copy sits at /usr/bin/amd-ttm from the amd-debug-tools package.
Resolving via PATH would work in development and fail once installed.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "strix-dash"
API_PREFIX = "/api/v1"

HOST = os.environ.get("STRIX_DASH_HOST", "127.0.0.1")
PORT = int(os.environ.get("STRIX_DASH_PORT", "10001"))

# Origins permitted to issue state-changing requests. Binding to loopback does
# not stop a page in the user's browser from POSTing here, and these endpoints
# change power limits, so writes are additionally gated on Origin + a header.
ALLOWED_ORIGINS = (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}")
WRITE_HEADER = "X-Strix-Dash"

# ---------------------------------------------------------------------------
# Tool paths
# ---------------------------------------------------------------------------

TOOLS: dict[str, str] = {
    "flm": "/usr/bin/flm",
    "xrt-smi": "/usr/bin/xrt-smi",
    "rocm-smi": "/opt/rocm/bin/rocm-smi",
    "rocminfo": "/opt/rocm/bin/rocminfo",
    "amd-ttm": "/usr/bin/amd-ttm",  # NOT ~/.local/bin -- see module docstring
    "tuned-adm": "/usr/sbin/tuned-adm",
    "dpkg-query": "/usr/bin/dpkg-query",
    "apt-cache": "/usr/bin/apt-cache",
    "systemctl": "/usr/bin/systemctl",
    "sudo": "/usr/bin/sudo",
    "z13ctl": "/usr/bin/z13ctl",  # optional; absent on this machine
}

INSTALL_HINTS: dict[str, str] = {
    "z13ctl": "Third-party ASUS ROG tool; install manually from its project page.",
    "flm": "sudo apt install ./fastflowlm_<version>_debian13_amd64.deb",
    "tuned-adm": "sudo apt install tuned",
}

# Per-tool subprocess deadlines (seconds). rocminfo is by far the slowest.
TIMEOUTS: dict[str, float] = {
    "flm": 5.0,
    "xrt-smi": 10.0,
    "rocm-smi": 8.0,
    "rocminfo": 30.0,
    "amd-ttm": 5.0,
    "tuned-adm": 5.0,
    "dpkg-query": 20.0,
    "apt-cache": 15.0,
    "systemctl": 5.0,
    "z13ctl": 10.0,
}
DEFAULT_TIMEOUT = 10.0

# Cache TTLs in seconds. None == cache once at startup and never expire.
CACHE_TTL: dict[str, float | None] = {
    "rocminfo": None,
    "capabilities": None,
    "flm_list": 300.0,
    "flm_validate": 30.0,
    "flm_version": 300.0,
    "xrt": 5.0,
    "rocm_smi": 2.0,
    "amd_ttm": 60.0,
    "tuned": 15.0,
    "github_release": 900.0,
}

# ---------------------------------------------------------------------------
# sysfs
# ---------------------------------------------------------------------------

ASUS_PLATFORM = Path("/sys/devices/platform/asus-nb-wmi")
HWMON_ROOT = Path("/sys/class/hwmon")

# hwmon indices are NOT stable across boots -- always resolve by reading
# /sys/class/hwmon/*/name. These are the driver names to look for.
HWMON_FAN_CURVE_DRIVER = "asus_custom_fan_curve"
HWMON_FAN_SENSOR_DRIVER = "asus"

PLATFORM_PROFILE = Path("/sys/firmware/acpi/platform_profile")
PLATFORM_PROFILE_CHOICES = Path("/sys/firmware/acpi/platform_profile_choices")
THROTTLE_POLICY = ASUS_PLATFORM / "throttle_thermal_policy"
PANEL_OD = ASUS_PLATFORM / "panel_od"

# Power limits. Every node currently reads "5", which is not plausibly watts --
# the unit is unconfirmed, so v1 exposes these read-only and labels them by node
# name rather than inventing a "TDP (W)" slider. See docs/HARDWARE.md.
PPT_NODES = (
    "ppt_pl1_spl",
    "ppt_pl2_sppt",
    "ppt_fppt",
    "ppt_apu_sppt",
    "ppt_platform_sppt",
)
PPT_WRITABLE = False

BATTERY_CHARGE_LIMIT = Path("/sys/class/power_supply/BAT0/charge_control_end_threshold")
BATTERY_LIMIT_RANGE = (20, 100)

# Single-channel brightness only. There is no multicolour LED node on this
# machine, so RGB colour/effects are z13ctl-only and cannot be done via sysfs.
KBD_BACKLIGHT = Path("/sys/class/leds/asus::kbd_backlight/brightness")
KBD_BACKLIGHT_MAX = Path("/sys/class/leds/asus::kbd_backlight/max_brightness")

FAN_CURVE_POINTS = 8
FAN_CURVE_FANS = (1, 2)
PWM_RANGE = (0, 255)
FAN_TEMP_RANGE = (20, 105)

NPU_DEVICE = Path("/dev/accel/accel0")
NPU_PMODES = ("default", "powersaver", "balanced", "performance", "turbo")

TTM_PAGES_LIMIT = Path("/sys/module/ttm/parameters/pages_limit")
VRAM_TOTAL = Path("/sys/class/drm/card0/device/mem_info_vram_total")
GTT_TOTAL = Path("/sys/class/drm/card0/device/mem_info_gtt_total")

# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

SNAPSHOT_ROOT = Path("/var/snapshots/system")
SNAPSHOT_STATUS = SNAPSHOT_ROOT / "status.json"
LIVE_DPKG_ADMINDIR = Path("/var/lib/dpkg")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_here = Path(__file__).resolve().parent
_repo_dist = _here.parent / "frontend" / "dist"
_installed_dist = Path("/usr/share/strix-dash/frontend")

# STRIX_DASH_FRONTEND wins, then the installed copy, then the repo's dist.
#
# The override matters for development: with the service installed, a dev server
# started from a checkout would otherwise serve the INSTALLED frontend and
# silently ignore local edits. scripts/dev-run.sh sets it for exactly that
# reason.
if _env_frontend := os.environ.get("STRIX_DASH_FRONTEND"):
    FRONTEND_DIST = Path(_env_frontend)
elif _installed_dist.is_dir():
    FRONTEND_DIST = _installed_dist
else:
    FRONTEND_DIST = _repo_dist

LOG_DIR = Path("/var/log/strix-dash")
STAGING_DIR = Path("/var/lib/strix-dash/staging")

BACKPORTS_SOURCES = Path("/etc/apt/sources.list.d/debian-backports.sources")
BACKPORTS_CONTENT = """\
Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie-backports
Components: main
Enabled: yes
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
"""
