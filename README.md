# strix-dash

A system control panel for AMD Strix Halo laptops running Debian 13 — live
NPU/GPU telemetry, a btrfs snapshot package auditor, prerequisite management,
and native hardware controls.

## Scope and requirements

> **strix-dash is a companion to AMD's *Ryzen AI Developer Platform 1 (rex)*
> OS, not a replacement for it.** It runs alongside the vendor's own dashboard
> (`halo-lp`, on `:10001` vs its `:10000`) and fills the gaps that one leaves —
> it does not duplicate or interfere with it.

| | Requirement |
|---|---|
| **OS** | AMD *Ryzen AI Developer Platform 1 (rex)* — a Debian 13 derivative. Other Debian 13 systems will mostly work; the snapshot auditor additionally needs the platform's btrfs layout under `/var/snapshots/system/`. |
| **SoC** | Ryzen AI MAX+ 395 (Strix Halo) — gfx1151 GPU + `aie2p` NPU. Telemetry is written against these specifically. |
| **Chassis — Controls tab only** | **ASUS ROG Flow Z13 (GZ302EA).** The Controls tab drives the `asus-nb-wmi` platform driver and the `asus_custom_fan_curve` hwmon device. On any other machine those nodes are absent and every control renders as unavailable with a reason — the rest of the dashboard is unaffected. |

Telemetry, the snapshot auditor and requirements detection work on any Strix
Halo machine. Only the **Controls** tab is chassis-specific.

> **This application writes to hardware.** It can change performance profiles,
> fan curves, battery charge limits and NPU power modes. Read
> [SECURITY.md](SECURITY.md) before installing.

---

## Quick start

```bash
# 1. Check prerequisites (read-only, no root needed)
./scripts/bootstrap-npu.sh --check-only

# 2. Install as a system service
sudo ./scripts/install.sh

# 3. Open the dashboard
xdg-open http://127.0.0.1:10001
```

`install.sh` creates the `strix-dash` service user, installs the systemd unit,
the scoped sudoers rules and the tmpfiles sysfs grants, then prints a capability
report so you can see immediately what is available and what is not.

### Optional: RGB lighting via z13ctl

Everything on the Controls tab works through native sysfs **except Aura RGB**,
which has no kernel interface on this machine — `/sys/class/leds` exposes only a
0–3 keyboard brightness channel, no multicolour node. That one feature needs
[z13ctl](https://github.com/dahui/z13ctl/):

```bash
# Download z13ctl_<version>_linux_amd64.deb from the releases page, then:
sudo apt install ./z13ctl_*.deb
sudo z13ctl setup
```

Restart strix-dash afterwards (`sudo systemctl restart strix-dash`, or press
**Refresh**) so the capability probe picks it up. Without it the RGB controls
render disabled with a reason; nothing else is affected.

> z13ctl is third-party software with no Debian packaging we can verify, so
> strix-dash offers **no automatic install path** for it — the Requirements tab
> lists it as optional and manual-install only. Verify the download against the
> `checksums.txt` published with the release before installing.

### Updating

```bash
sudo ./scripts/deploy.sh    # after pulling changes
sudo ./scripts/uninstall.sh # remove (--purge also drops user, config, logs)
```

---

## Why

The vendor dashboard's package inventory is a hardcoded Python list in a file
that isn't a dpkg conffile, plus a compiled frontend bundle — so it can't be
extended, and edits are silently overwritten on upgrade. It also can't diff
snapshots, and it reports sideloaded packages as "up-to-date" forever because
they have no repository origin.

strix-dash covers those gaps:

- **Telemetry** — `flm validate`, `xrt-smi`, `rocm-smi`, `rocminfo`, `amd-ttm`,
  hwmon and psutil, normalised into one API.
- **Snapshot auditor** — diffs installed packages across btrfs snapshots
  (`factory`, post-driver-install, current) with no privileges at all.
- **Requirements** — detects prerequisites, distinguishes *local-only* from
  *up-to-date*, and can install a verified release.
- **Controls** — performance profile, fan curves, battery limit, keyboard
  backlight, NPU power mode, TuneD profile.

## Unified memory, reported honestly

On this APU the three memory sources disagree by design. `rocm-smi --showmeminfo
vram` reports the **512 MB fixed BIOS carveout**, which sits at ~90% used while
idle. Labelled "VRAM" that reads as a GPU about to run out of memory — with
~105 GB free. The pool that actually matters is the ~107 GB dynamically-shared
GTT allocation.

So the API always presents **GTT as primary** and the carveout as clearly
labelled secondary, and no endpoint returns a bare `vram_pct`.

## Install

Requires `python3-fastapi`, `python3-uvicorn`, `python3-psutil`,
`python3-pydantic` (all Debian packages — no venv, no pip at runtime).

```bash
./scripts/bootstrap-npu.sh --check-only   # verify prerequisites first
sudo ./scripts/install.sh                 # service user, unit, sudoers, tmpfiles
```

Then open <http://127.0.0.1:10001>. Remove with `sudo ./scripts/uninstall.sh`
(add `--purge` to drop the user, config and logs).

## Development

```bash
./scripts/dev-run.sh          # runs as you, on :10001
python3 -m pytest             # offline: no hardware, no network
./scripts/build-css.sh        # rebuild the stylesheet (needs network, once)
./scripts/sync-dist.sh        # copy src -> dist and rebuild CSS
sudo ./scripts/deploy.sh      # push changes to an installed service
```

The test suite runs entirely against captured fixtures in `tests/fixtures/raw/`,
so CI needs no NPU, no ROCm and no ASUS platform driver. Fixtures are redacted
by `scripts/sanitize-fixtures.sh` — **never commit raw tool output.**

`frontend/dist/` is committed so a clone runs offline. It is generated;
`frontend/src/` is the source of truth.

## Architecture

```
backend/
  core/         runner (the single subprocess choke point), capabilities,
                cache, sysfs, errors, models
  collectors/   flm, xrt, rocm, memory, host       (read-only telemetry)
  packages/     snapshots, dpkg, diff              (unprivileged auditing)
  requirements/ registry, detect, releases, install
  controls/     hardware, confirm                  (the write path)
frontend/src/   vanilla ES modules + Tailwind; no bundler
packaging/      systemd unit, sudoers, tmpfiles template, helpers
```

Three design rules do most of the work:

1. **One subprocess choke point.** No module calls `subprocess` directly.
   Timeouts, ANSI stripping and error normalisation happen exactly once — four
   of the six tools emit colour escapes even when piped.
2. **Absolute tool paths, never `PATH`.** `amd-ttm` resolves via `PATH` to a
   pipx copy inside a `0700` home that the service user cannot execute; the
   usable binary is `/usr/bin/amd-ttm`.
3. **Read back, never echo.** Every write returns what the hardware actually
   holds afterwards. `verified: false` means the firmware clamped the request —
   surfaced as a warning, not silently accepted.

## Known limitations

| Feature | Status |
|---|---|
| TDP (`ppt_*`) | **Read-only telemetry, no write path.** All five nodes read `5`, which is not plausibly watts. Shipping a "TDP (W)" slider on that guess could damage hardware. |
| RGB / Aura | Unavailable. No multicolour LED node exists — only a 0–3 brightness channel. Requires `z13ctl`. |
| NPU power mode | The `amdxdna` driver may reject `DRM_IOCTL_AMDXDNA_SET_STATE` with `EACCES`. Verify with `sudo xrt-smi configure --pmode performance`. |
| `SystemCallFilter` | Deliberately unset in the unit — see the comment there; a seccomp filter forces `NoNewPrivileges` and breaks setuid `sudo`. |

## Requirements

Single uvicorn worker only. The in-process cache provides single-flight
de-duplication of subprocess calls; extra workers each get their own cache and
multiply tool invocations.

## Licence

MIT — see [LICENSE](LICENSE).
