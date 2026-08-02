"""Offline tests for requirement detection, asset selection and the helper's
safety properties.

The helper script is exercised as a subprocess with its root check stubbed, so
its input validation and allowlist can be tested without privileges or network.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from backend.requirements import detect
from backend.requirements.registry import BY_ID, REQUIREMENTS
from backend.requirements.releases import _select_asset

HELPER_SRC = Path(__file__).parent.parent / "packaging" / "strix-dash-req-helper.sh"

# The real payload shape from the FastFlowLM release API.
RELEASE = {
    "tag_name": "v0.9.46",
    "assets": [
        {
            "name": "fastflowlm_0.9.46_debian13_amd64.deb",
            "size": 40166224,
            "browser_download_url": "https://github.com/FastFlowLM/FastFlowLM/releases/download/v0.9.46/fastflowlm_0.9.46_debian13_amd64.deb",
            "digest": "sha256:068a9f30f079d772074696ec4a8a40cc4818c21825d3b9a6549bb51cc2bce948",
        },
        {
            "name": "fastflowlm_0.9.46_ubuntu24.04_amd64.deb",
            "size": 28093344,
            "browser_download_url": "https://example.invalid/u24",
            "digest": "sha256:aaa",
        },
        {
            "name": "fastflowlm_0.9.46_ubuntu26.04_amd64.deb",
            "size": 29298124,
            "browser_download_url": "https://example.invalid/u26",
            "digest": "sha256:bbb",
        },
        {
            "name": "fastflowlm_0.9.46_windows_amd64.zip",
            "size": 35528044,
            "browser_download_url": "https://example.invalid/win",
            "digest": "sha256:ccc",
        },
    ],
}


# ---------------------------------------------------------------------------
# Distro tag / asset selection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "debian_version", "expected"),
    [
        # This platform: a Debian derivative whose own VERSION_ID is "1", which
        # matches no release asset. It must map to its Debian base instead.
        (
            {"ID": "amd-ryzen-ai-developer-platform", "ID_LIKE": "debian", "VERSION_ID": "1"},
            "13.4",
            "debian13",
        ),
        ({"ID": "debian", "VERSION_ID": "13"}, None, "debian13"),
        ({"ID": "ubuntu", "VERSION_ID": "24.04"}, None, "ubuntu24.04"),
        ({"ID": "ubuntu", "VERSION_ID": "26.04"}, None, "ubuntu26.04"),
        ({}, None, "unknown"),
    ],
)
def test_distro_tag_derives_from_os_release(
    fields: dict[str, str], debian_version: str | None, expected: str
) -> None:
    """Synthetic inputs only -- this must not depend on the host running it."""
    assert detect.distro_tag(fields, debian_version) == expected


def test_selects_the_asset_matching_the_distro() -> None:
    asset = _select_asset(BY_ID["fastflowlm"], "v0.9.46", RELEASE["assets"], distro="debian13")
    assert asset is not None
    assert asset["name"] == "fastflowlm_0.9.46_debian13_amd64.deb"
    assert asset["size"] == 40166224
    assert "ubuntu" not in asset["name"]


def test_never_falls_back_to_another_distros_asset() -> None:
    """Wrong-distro assets are present in the same release; picking one would
    silently install an incompatible binary."""
    req = BY_ID["fastflowlm"]
    others = [a for a in RELEASE["assets"] if "debian" not in a["name"]]
    assert _select_asset(req, "v0.9.46", others, distro="debian13") is None


def test_requirements_without_a_feed_declare_no_asset_template() -> None:
    for req in REQUIREMENTS:
        if req.source_kind != "github-release":
            assert req.asset_template is None, req.id
            assert req.repo is None, req.id


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------


def test_z13ctl_offers_no_automatic_install() -> None:
    """Third-party, unverifiable source -- manual only, and marked optional."""
    z13 = BY_ID["z13ctl"]
    assert z13.source_kind == "manual"
    assert z13.optional is True
    assert z13.remediation


def test_every_requirement_declares_what_it_is_for() -> None:
    for req in REQUIREMENTS:
        assert req.required_for, req.id
        assert req.summary, req.id


# ---------------------------------------------------------------------------
# Helper safety (subprocess, root check stubbed)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def helper() -> Path:
    source = HELPER_SRC.read_text()
    stubbed = re.sub(
        r'^\[ "\$\(id -u\)" -eq 0 \].*$',
        ": # root check stubbed",
        source,
        flags=re.MULTILINE,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
        handle.write(stubbed)
        path = Path(handle.name)
    path.chmod(0o755)
    yield path
    path.unlink(missing_ok=True)


def _run(helper: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(helper), *args], capture_output=True, text=True, timeout=30, check=False
    )
    return proc.returncode, proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd",
        "fastflowlm; id",
        "fastflowlm && curl evil.example",
        "fast/flow",
        "FASTFLOWLM",
        "$(whoami)",
        "",
    ],
)
def test_helper_rejects_injection_attempts(helper: Path, bad_id: str) -> None:
    """The id is the only caller-supplied value; it must be strictly bounded."""
    code, output = _run(helper, "install", bad_id)
    assert code != 0
    assert "invalid requirement id" in output


def test_helper_rejects_ids_outside_its_allowlist(helper: Path) -> None:
    code, output = _run(helper, "install", "not-a-real-requirement")
    assert code != 0
    assert "not installable" in output


def test_helper_rejects_unknown_actions(helper: Path) -> None:
    code, _ = _run(helper, "destroy", "fastflowlm")
    assert code != 0


def test_caller_input_never_reaches_curl() -> None:
    """The one caller-supplied value must never appear in a network call.

    $REQ_ID selects an allowlist entry; the repo and asset URL are derived from
    that entry. If $REQ_ID ever appeared on a curl line, the caller would be
    steering the request.
    """
    source = HELPER_SRC.read_text()
    curl_lines = [ln for ln in source.splitlines() if re.search(r"\bcurl\b", ln)]
    assert curl_lines, "expected the helper to make network calls"
    for line in curl_lines:
        assert "REQ_ID" not in line, line
        assert "$1" not in line and "$2" not in line, line


def test_helper_fetches_only_github_and_the_resolved_asset() -> None:
    source = HELPER_SRC.read_text()
    urls = re.findall(r'https://[^"\s]+', source)
    for url in urls:
        assert url.startswith(("https://api.github.com/repos/", "https://github.com/")), url


def test_helper_requires_a_digest_before_installing() -> None:
    source = HELPER_SRC.read_text()
    assert "refusing to install unverified" in source
    assert "SHA256 mismatch" in source
    # And the mismatch path must abort, not merely warn.
    mismatch = source.split("SHA256 mismatch")[0]
    assert "die " in mismatch.splitlines()[-1] or "die" in source.split("SHA256 mismatch")[1][:80]


def test_helper_constrains_the_asset_url_to_the_release_path() -> None:
    source = HELPER_SRC.read_text()
    assert 'https://github.com/"$REPO"/releases/download/*' in source


def test_helper_only_accepts_deb_assets() -> None:
    source = HELPER_SRC.read_text()
    assert "refusing non-.deb asset" in source
