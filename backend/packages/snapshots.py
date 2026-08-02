"""Btrfs snapshot enumeration and package diffing.

Root on this OS is a btrfs subvolume under /var/snapshots/system/, and every
snapshot carries a complete dpkg admindir. Those are world-readable, so the
entire auditor runs with NO privileges at all -- ``dpkg-query --admindir=`` is
happy to read another root's database.

Two traps this module works around:

1. ``status.json`` is MALFORMED -- it contains two concatenated top-level JSON
   arrays, so ``json.load()`` raises "Extra data". It is also incomplete
   (factory and post-asus have no entry, and one recorded uuid has no matching
   directory). Directories are the source of truth; status.json is best-effort
   metadata recovered with a raw_decode loop.

2. ``dpkg-query -W`` with no filter includes packages in ``deinstall`` and
   ``config-files`` states -- removed packages whose conffiles remain. Counting
   those makes a diff wrong, so results are filtered on the ``ii`` status.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend import config
from backend.core import errors
from backend.core.cache import cache
from backend.core.runner import run, run_tool

# db:Status-Abbrev is two chars (want + state) plus an error flag column.
_QUERY_FORMAT = "${db:Status-Abbrev}\\t${binary:Package}\\t${Version}\\n"

_UUID_SNAPSHOT = re.compile(r"^\d{14}\.([0-9a-f-]{36})\.snapshot$")


def _label_for(name: str) -> str:
    """Human label for a snapshot directory name."""
    if name == "@":
        return "live (@)"
    if name == "factory.snapshot":
        return "Factory image"
    if match := _UUID_SNAPSHOT.match(name):
        return f"Auto snapshot {match.group(1)[:8]}"
    return name.removesuffix(".snapshot").replace("-", " ").replace("_", " ")


def _read_status_metadata() -> list[dict[str, Any]]:
    """Recover records from the malformed status.json.

    Uses raw_decode in a loop so both concatenated arrays are recovered rather
    than the whole file being discarded. Any failure degrades to no metadata --
    never an exception, since this is decoration on top of the directory scan.
    """
    try:
        text = config.SNAPSHOT_STATUS.read_text()
    except OSError:
        return []

    decoder = json.JSONDecoder()
    records: list[dict[str, Any]] = []
    index = 0
    length = len(text)

    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        if isinstance(value, list):
            records.extend(r for r in value if isinstance(r, dict))
        elif isinstance(value, dict):
            records.append(value)
        index = end

    # The file repeats at least one record between its two arrays.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        key = record.get("uuid") or json.dumps(record, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(record)
    return unique


def enumerate_snapshots() -> list[dict[str, Any]]:
    """List every snapshot directory that has a usable dpkg admindir."""
    if not config.SNAPSHOT_ROOT.is_dir():
        return []

    metadata = _read_status_metadata()
    by_uuid = {r["uuid"]: r for r in metadata if r.get("uuid")}

    entries: list[dict[str, Any]] = []
    for path in sorted(config.SNAPSHOT_ROOT.iterdir()):
        if not path.is_dir():
            continue
        admindir = path / "var" / "lib" / "dpkg"
        if not (admindir / "status").exists():
            continue

        name = path.name
        uuid = match.group(1) if (match := _UUID_SNAPSHOT.match(name)) else None
        record = by_uuid.get(uuid or "", {})

        entries.append(
            {
                "id": name,
                "label": record.get("label") or _label_for(name),
                "path": str(path),
                "admindir": str(admindir),
                "kind": "live" if name == "@" else "snapshot",
                "uuid": uuid,
                "type": record.get("type"),
                "created": record.get("created"),
                "versions": record.get("versions"),
                "mtime": (admindir / "status").stat().st_mtime,
                "size": (admindir / "status").stat().st_size,
            }
        )

    entries.append(
        {
            "id": "current",
            "label": "Current system",
            "path": "/",
            "admindir": str(config.LIVE_DPKG_ADMINDIR),
            "kind": "live",
            "uuid": None,
            "type": "running",
            "created": None,
            "versions": None,
            "mtime": (config.LIVE_DPKG_ADMINDIR / "status").stat().st_mtime,
            "size": (config.LIVE_DPKG_ADMINDIR / "status").stat().st_size,
        }
    )
    return entries


def _admindir_for(snapshot_id: str) -> Path:
    for entry in enumerate_snapshots():
        if entry["id"] == snapshot_id:
            return Path(entry["admindir"])
    raise errors.invalid_value("snapshot", f"unknown snapshot {snapshot_id!r}")


async def _query_packages(admindir: Path) -> dict[str, str]:
    result = await run_tool("dpkg-query", f"--admindir={admindir}", "-W", f"-f={_QUERY_FORMAT}")
    if not result.ok:
        raise result.error or errors.parse_error("dpkg-query", "query failed")

    packages: dict[str, str] = {}
    for line in result.stdout.split("\n"):
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        status, name, version = parts[0].strip(), parts[1].strip(), parts[2].strip()
        # Only genuinely installed packages. "rc" (removed, config-files) and
        # "deinstall" entries would otherwise inflate every count.
        if status.startswith("ii") and name:
            packages[name] = version
    return packages


async def packages_for(snapshot_id: str) -> dict[str, str]:
    """Installed packages for a snapshot, cached on the admindir's mtime."""
    admindir = _admindir_for(snapshot_id)
    mtime = (admindir / "status").stat().st_mtime
    key = f"pkgs:{admindir}:{mtime}"
    entry = await cache.get(key, lambda: _query_packages(admindir), ttl=None)
    return entry.value


async def listing() -> list[dict[str, Any]]:
    """Snapshots with package counts, de-duplicating @ against the live system."""
    entries = enumerate_snapshots()
    live_signature: tuple[int, float] | None = None

    for entry in entries:
        try:
            entry["package_count"] = len(await packages_for(entry["id"]))
        except errors.ToolError:
            entry["package_count"] = None

        signature = (entry["size"], entry["mtime"])
        if entry["id"] == "current":
            live_signature = signature

    # "@" is the live root subvolume seen through the snapshot directory; when
    # it is byte-identical to /var/lib/dpkg, say so rather than offering a diff
    # that is guaranteed to be empty.
    for entry in entries:
        entry["alias_of"] = (
            "current"
            if entry["id"] == "@" and live_signature == (entry["size"], entry["mtime"])
            else None
        )
    return entries


async def diff(base_id: str, target_id: str) -> dict[str, Any]:
    base = await packages_for(base_id)
    target = await packages_for(target_id)

    base_names = set(base)
    target_names = set(target)

    added = sorted(target_names - base_names)
    removed = sorted(base_names - target_names)
    changed = sorted(name for name in base_names & target_names if base[name] != target[name])

    return {
        "base": base_id,
        "target": target_id,
        "added": [{"package": n, "version": target[n]} for n in added],
        "removed": [{"package": n, "version": base[n]} for n in removed],
        "changed": [{"package": n, "from": base[n], "to": target[n]} for n in changed],
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "base_total": len(base),
            "target_total": len(target),
        },
    }


async def create() -> dict[str, Any]:
    """Take a read-only btrfs snapshot of the running root.

    The helper generates the timestamped name itself -- nothing
    caller-supplied ever reaches a path.

    Note the retention caveat surfaced to the user: the platform's own
    amd-halo-snapshot cleanup exempts exactly one name, factory.snapshot, and
    deletes every other *.snapshot the next time it runs. A snapshot taken here
    is therefore a short-term checkpoint, not durable storage.
    """
    import os

    if not os.path.exists(config.PRIV_HELPER):
        raise errors.ToolError(
            code=errors.ErrorCode.NOT_SUPPORTED,
            message="The privileged helper is not installed",
            hint=(
                "The helper is installed by install.sh and reinstalled by deploy.sh. If it vanished after a code change, the deploy dropped it -- re-run sudo ./scripts/deploy.sh."
            ),
        )

    result = await run(
        [config.TOOLS["sudo"], "-n", config.PRIV_HELPER, "snapshot-create"],
        tool="sudo",
        timeout=120,
    )
    if not result.ok:
        raise result.error or errors.ToolError(
            code=errors.ErrorCode.TOOL_FAILED, message="Snapshot creation failed"
        )

    name = result.stdout.strip()
    created = next((e for e in enumerate_snapshots() if e["id"] == name), None)
    return {
        "created": name,
        "path": created["path"] if created else None,
        "package_count": len(await packages_for(name)) if created else None,
        "retention_warning": (
            "The platform's own snapshot cleanup preserves only "
            "factory.snapshot and removes every other *.snapshot when it next "
            "runs. Treat this as a short-term checkpoint."
        ),
    }
