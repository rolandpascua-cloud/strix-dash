"""Declarative requirements registry.

This is the file you edit to teach the dashboard about a new prerequisite --
deliberately data, not code, and deliberately not baked into the frontend.

Statuses a requirement can report:

    satisfied   installed and current
    outdated    installed, newer release available
    missing     not installed
    degraded    installed but not functional (e.g. tuned's daemon is stopped)
    local-only  installed from a local .deb with NO repository origin, so apt
                will never offer an update. fastflowlm is in this state today;
                reporting it as "up-to-date" would be misleading.
    unknown     detection failed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

DetectKind = Literal["dpkg", "file", "binary", "service", "device"]
SourceKind = Literal["apt", "github-release", "manual", "none"]


@dataclass(frozen=True)
class Requirement:
    id: str
    name: str
    summary: str
    required_for: tuple[str, ...]
    detect_kind: DetectKind
    # Interpretation depends on detect_kind: package name, path, tool key or unit.
    detect_target: str
    source_kind: SourceKind = "none"
    # GitHub "owner/repo" when source_kind is github-release.
    repo: str | None = None
    # {version} and {distro_tag} are substituted at resolve time.
    asset_template: str | None = None
    optional: bool = False
    remediation: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement(
        id="fastflowlm",
        name="FastFlowLM",
        summary="NPU inference runtime providing the flm CLI.",
        required_for=("NPU inference", "flm validate telemetry"),
        detect_kind="dpkg",
        detect_target="fastflowlm",
        source_kind="github-release",
        repo="FastFlowLM/FastFlowLM",
        # The same release ships debian13 AND three ubuntu variants; the tag is
        # derived from /etc/os-release, never hardcoded, because installing the
        # wrong one is a silent incompatible install.
        asset_template="fastflowlm_{version}_{distro_tag}_amd64.deb",
    ),
    Requirement(
        id="debian-backports",
        name="Debian backports repository",
        summary="Required before installing or upgrading FastFlowLM on Debian 13.",
        required_for=("FastFlowLM install",),
        detect_kind="file",
        detect_target="/etc/apt/sources.list.d/debian-backports.sources",
        source_kind="manual",
        remediation="Run scripts/bootstrap-npu.sh (idempotent).",
    ),
    Requirement(
        id="npu-device",
        name="NPU device node",
        summary="The amdxdna driver must expose /dev/accel/accel0.",
        required_for=("NPU inference",),
        detect_kind="device",
        detect_target="/dev/accel/accel0",
        source_kind="none",
        remediation=(
            "In-tree on this kernel (amdxdna 0.6). If absent, the driver did "
            "not bind; amdxdna-dkms is only needed on older kernels."
        ),
    ),
    Requirement(
        id="xrt",
        name="Xilinx Runtime (NPU)",
        summary="Provides xrt-smi for NPU examination and power-mode control.",
        required_for=("NPU telemetry", "NPU power mode"),
        detect_kind="dpkg",
        detect_target="libxrt-utils-npu",
        source_kind="apt",
    ),
    Requirement(
        id="rocm",
        name="ROCm SDK (gfx1151)",
        summary="GPU compute stack backing rocm-smi and PyTorch.",
        required_for=("GPU telemetry", "PyTorch"),
        detect_kind="dpkg",
        detect_target="therock-gfx1151",
        source_kind="apt",
    ),
    Requirement(
        id="amd-debug-tools",
        name="AMD debug tools",
        summary="Supplies amd-ttm, amd-s2idle, amd-pstate and amd-bios.",
        required_for=("TTM memory reporting",),
        detect_kind="dpkg",
        detect_target="amd-debug-tools",
        source_kind="apt",
    ),
    Requirement(
        id="tuned",
        name="TuneD",
        summary="System tuning profiles; the daemon must be running to switch them.",
        required_for=("Performance profiles",),
        detect_kind="service",
        detect_target="tuned",
        source_kind="apt",
        remediation="sudo systemctl enable --now tuned",
    ),
    Requirement(
        id="z13ctl",
        name="z13ctl",
        summary="ASUS ROG Flow Z13 control tool, used here for Aura RGB lighting.",
        required_for=("RGB lighting",),
        detect_kind="binary",
        detect_target="z13ctl",
        # Third-party with no packaged source we can verify, so no automatic
        # install path is offered -- only a note that it must be done by hand.
        source_kind="manual",
        optional=True,
        remediation=(
            "Not in Debian. Download the .deb from "
            "https://github.com/dahui/z13ctl/ then: "
            "sudo apt install ./z13ctl_*.deb && sudo z13ctl setup. "
            "Everything else on the Controls page works without it via sysfs."
        ),
    ),
)

BY_ID = {r.id: r for r in REQUIREMENTS}
