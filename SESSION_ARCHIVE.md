# Session archive

Build record for **strix-dash** — what was built, why it is shaped this way, and
what is still outstanding. Written at the end of the initial build session so a
later reader (or a later me) does not have to re-derive the reasoning.

**State at close:** 24 commits · 139 files · ~3,950 lines Python, ~1,100 JS ·
82 tests passing · CI green · public at
<https://github.com/rolandpascua-cloud/strix-dash>

---

## 1. What this is

A system control panel for an ASUS ROG Flow Z13 (GZ302EA, Ryzen AI MAX+ 395)
running AMD's *Ryzen AI Developer Platform 1 (rex)* — a Debian 13 derivative
built by Collabora. It runs as a hardened systemd service on `127.0.0.1:10001`,
**alongside** the vendor's own dashboard (`halo-lp`, on `:10000`), not as a
replacement.

It exists to cover three gaps in the vendor tool, each established by reading
the installed system rather than assumed:

| Gap | Evidence |
|---|---|
| Package inventory is unextensible | halo-lp's list is a Python literal in a file that is **not** a dpkg conffile, plus a compiled Vue bundle. Any edit is silently overwritten on upgrade. |
| No snapshot diffing | The OS keeps btrfs snapshots with full dpkg admindirs; nothing surfaced them. |
| Sideloaded packages misreported | `apt-cache policy fastflowlm` shows only `/var/lib/dpkg/status` — no repository origin — yet reads as "up-to-date" forever. |

---

## 2. Architecture

```
backend/
  core/          runner · capabilities · cache · sysfs · errors · models · ansi
  collectors/    flm · xrt · rocm · memory · host        (read-only telemetry)
  packages/      snapshots                                (unprivileged auditing)
  requirements/  registry · detect · releases · install
  controls/      hardware · confirm                       (the write path)
  api/           capabilities · telemetry · packages · requirements · controls
frontend/src/    vanilla ES modules + Tailwind, no bundler; dist/ is committed
packaging/       systemd unit · sudoers · tmpfiles template · 2 privileged helpers
scripts/         install · deploy · uninstall · bootstrap-npu · build-css ·
                 sync-dist · sanitize-fixtures · capture-screenshots
```

### The four rules that carry the design

**1. One subprocess choke point.** Nothing outside `core/runner.py` calls
`subprocess`. Timeouts, ANSI stripping, and error normalisation happen exactly
once. This is not tidiness — four of the six tools driven here emit colour
escapes *even when piped*, and each fails differently.

**2. Absolute tool paths, never `PATH`.** `amd-ttm` resolves through `PATH` to a
pipx copy inside a `0700` home that the service user cannot execute. The usable
binary is `/usr/bin/amd-ttm`. Resolving via `PATH` works in development and
breaks once installed — the exact class of bug this project kept hitting.

**3. Read back, never echo.** Every write returns what the hardware holds
*afterwards*. `verified: false` means the firmware applied something else, and
is a first-class UI state rather than an error.

**4. Degraded is not an error.** A missing tool or stopped daemon returns
HTTP 200 with `ok: false`, so a panel renders its own explanation instead of the
browser reporting a failed request. 4xx/5xx are reserved for bad requests and
genuine faults.

---

## 3. Key decisions

### 3.1 Unified memory is reported GTT-first

The three sources disagree *by design* on this APU:

| Source | Reports | Observed |
|---|---|---|
| `rocm-smi --showmeminfo vram` | fixed BIOS carveout | 512 MB, **~90% used at idle** |
| `mem_info_gtt_total` | dynamically shared pool | ~107 GB, ~2% used |
| `ttm pages_limit` | kernel ceiling on that pool | 28,174,103 pages |

Labelling the carveout "VRAM" shows a GPU about to run out of memory while
~105 GB is free. So the API always presents **GTT as primary**, the carveout as
clearly-labelled secondary, and **no endpoint returns a bare `vram_pct`**. The
rule is enforced in one composer (`collectors/memory.py`) so no panel can
violate it independently.

### 3.2 The test suite never touches hardware

All 82 tests run against captured fixtures in `tests/fixtures/raw/` — raw tool
output *including* the original ANSI bytes. CI on a hosted runner has no NPU, no
ROCm and no ASUS platform driver, and still catches parser regressions.

This paid for itself immediately: three tests initially asserted against the
machine they ran on and passed locally while failing on CI's Ubuntu runner.
`distro_tag()` and `_select_asset()` now take their inputs as arguments.

### 3.3 Privilege in layers, least first

| Layer | Covers | Escalation |
|---|---|---|
| none | all telemetry, the entire snapshot auditor | — |
| `tmpfiles.d` group grant | profile, fan curve, battery, backlight | group ownership on named nodes |
| `sudoers` | NPU pmode, TuneD, the two helpers | NOPASSWD, fixed command paths |

The sysfs write path is **allowlisted in code**, so a bug cannot become an
arbitrary file write. `xrt-smi configure --pmode` enumerates its five values
*literally* in sudoers rather than using a wildcard; `tuned-adm profile *` is a
wildcard, so the backend validates the name against `tuned-adm list` first.

### 3.4 TDP is read-only, permanently

The `ppt_*` nodes read `5` at boot and settle to plausible wattages
(52/71/70/70/70 observed) once something programs them. They are *probably*
watts — but "probably" is not a basis for writing to a power limit whose
accepted range and per-node effect are undocumented. They appear nowhere in the
write allowlist, and the `PPT_WRITABLE` flag was deleted so there is no disabled
path to re-enable by accident.

### 3.5 Cooperate with z13ctl rather than contest ownership

z13ctl's `setup` installs udev rules that chgrp the battery node **and** the
fan-curve `pwm*` nodes to `users` on every matching event. Two packages cannot
both own a node's group, and whichever udev rule runs last wins — so contesting
it would break for whoever installed second, in either order.

Both controls therefore prefer a direct sysfs write and fall back to
`strix-dash-priv-helper.sh`, which is root and does not care who owns the node.
The response reports which path ran (`backend: "sysfs" | "priv-helper"`).

---

## 4. Bugs found by testing rather than reasoning

Every one of these was invisible in development and surfaced only on the
installed service or in CI. This is why the plan sequenced packaging *before*
the write features.

| Bug | Cause |
|---|---|
| **All privileged controls failed** | The unit set `NoNewPrivileges=false` **and** `SystemCallFilter=`. A seccomp filter forces `PR_SET_NO_NEW_PRIVS`, so the kernel ignored `sudo`'s setuid bit. `systemctl show` reported `NoNewPrivileges=no` while `/proc/<pid>/status` showed `1`. My plan had claimed the filter was "safe here, unlike halo-lp" — it was not. |
| **Both NPU panels failed** | `RLIMIT_MEMLOCK`. xrt-smi maps 64 MiB with `MAP_LOCKED`; the unit had no `LimitMEMLOCK` and inherited systemd's 8 MiB default. `/etc/security/limits.conf` says `unlimited` but is applied by **PAM**, which services never traverse. |
| **Snapshot creation refused** | `ReadOnlyPaths=…/var/snapshots` in our own unit. sudo does not escape a mount namespace, so the helper inherited the read-only bind and btrfs failed *as root*. |
| **Create-snapshot button did nothing** | `deploy.sh` deleted `backend/` and copied `backend/` — but the helpers live in `packaging/`. Every deploy silently removed them; `install.sh` put them back, so only the first install worked. |
| **Every page bled through** | `[hidden]`'s `display:none` comes from the UA stylesheet and loses on specificity to Tailwind's `.grid{display:grid}`. Every section carries a layout class, so the Overview grid painted under every tab. |
| **Sanitiser leaked what it removed** | It hardcoded the hostname and username it was written to redact. Now derived at runtime. |
| **CI staleness check was inert** | It compared mtimes; git does not preserve them, so after a clone every file shares the checkout time. Replaced with a content rebuild-and-diff. |

Two near-misses worth recording: a naive IPv4 redaction rule would have rewritten
the NPU firmware version `1.1.2.65` to `0.0.0.0` and corrupted every firmware
assertion; and matching the NPU by PCI class `0x1180` reported the wrong IOMMU
group, because two devices share that class — it now resolves through
`/sys/class/accel/accel0/device`.

---

## 5. Pending technical debt

### Blocking / needs a decision

**NPU power mode does not work.** Re-verified at session close:

```
sudo -n xrt-smi configure --pmode performance
  [xrt-smi] ERROR: DRM_IOCTL_AMDXDNA_SET_STATE IOCTL failed (err=-13): Permission denied
```

sudo elevation is confirmed working (the memlock and seccomp fixes proved that),
and nothing holds the device, so the `amdxdna` driver is refusing the ioctl
itself. It has *never* succeeded from this app.

Two hypotheses were tested and rejected: the mode is **not** derived from
`platform_profile` (that reads `performance` while xrt reports `Default`), and
no process holds `/dev/accel/accel0`.

**Handled, not fixed.** The failure is now mapped to `NOT_SUPPORTED` naming the
driver and ioctl, so the UI renders a reason instead of a bare `TOOL_FAILED`
implying a permissions problem that does not exist. It is deliberately *not*
hardcoded as unsupported — a kernel that implements the ioctl will start
working with no code change. Confirm the platform limitation independently with
`sudo xrt-smi configure --pmode performance`.

**The installed service is behind the repo.** `diff -rq backend
/usr/lib/strix-dash/backend` reports drift. The fan-curve fallback, the IOMMU
panel and the corrected TDP wording are committed but not deployed. Run
`sudo ./scripts/deploy.sh`.

**Screenshots misrepresent the app.** They were captured from a dev server
running as the invoking user, so Performance profile and Keyboard backlight show
`READ-ONLY`, one frame caught the header mid-load as `CONNECTING`, and fan
curves show read-only because the installed service predates that fix. Re-run
`./scripts/capture-screenshots.sh` after deploying — it defaults to `:10001`
precisely for this reason.

### Known and accepted

- **Commit email is public.** All 24 commits are authored
  `roland.pascua@gmail.com`. The repo is now public, so rewriting history will
  not retract what has been fetched or indexed. Set
  `user.email` to a `users.noreply.github.com` address for future commits.
- **Screenshots reveal the hostname** (`amd-halo`) and machine model.
- **Single uvicorn worker is mandatory**, not a default. The in-process cache
  provides single-flight de-duplication; extra workers each get their own cache
  and multiply subprocess invocations. Enforced only by a comment in the unit
  and the launcher.
- **No authentication.** The entire boundary is the loopback bind plus scoped
  sudoers. Coherent for a single-user laptop; inadequate for a shared machine.
  `STRIX_DASH_HOST` must stay `127.0.0.1`.
- **Snapshots taken here are not durable.** The platform's own
  `amd-halo-snapshot` cleanup preserves exactly one name, `factory.snapshot`,
  and deletes every other `*.snapshot` when it next runs. The confirm dialog
  says so before taking one. Your existing `20260801-manual.snapshot` and
  `post-asus-drivers-clean.snapshot` are both on borrowed time.
- **`hwmon` indices are unstable across boots.** Resolved by driver name at both
  install and startup. A udev rule would be more robust than tmpfiles for the
  fan-curve nodes.

### Nice to have

- **`mypy` is not in CI.** The plan called for it; only `ruff` (lint + format),
  `pytest`, `shellcheck`, `visudo -c` and `systemd-analyze verify` run today.
- **`/requirements/{id}/check` is UI-unreachable.** The button was removed
  because it timed out under `IPAddressDeny=any` without the helper. The
  endpoint remains tested and API-reachable; the install flow resolves releases
  itself via `/preview`.
- **No frontend tests.** The JS is verified by DOM inspection during
  development, not automatically. The panel renderers are pure functions and
  would be straightforward to test.
- **`docs/screenshots/` will drift** from the UI without a CI check.
- **The requirements registry has one installable entry.** The
  `github-release` machinery is general, but only `fastflowlm` uses it; z13ctl
  is deliberately manual-install because it publishes no digest we verify.

---

## 6. Operational cheat-sheet

```bash
./scripts/bootstrap-npu.sh --check-only   # prerequisites, read-only
sudo ./scripts/install.sh                 # first install
sudo ./scripts/deploy.sh                  # after code changes (installs helpers!)
./scripts/dev-run.sh                      # dev server, runs as you
python3 -m pytest                         # offline, no hardware
./scripts/sync-dist.sh                    # after any frontend/src edit
./scripts/capture-screenshots.sh          # against :10001, not a dev server
./scripts/sanitize-fixtures.sh --check    # before committing captures
```

**Traps worth remembering**

- `deploy.sh` must reinstall the helpers; if the snapshot button, requirement
  installer or battery fallback report "the privileged helper is not installed",
  a deploy dropped it.
- The browser aggressively caches ES modules. Static assets are served
  `no-cache, must-revalidate` for this reason; a stale page can still mislead
  you mid-session.
- A dev server started from a checkout will serve `/usr/share/strix-dash`
  frontend if installed. `dev-run.sh` sets `STRIX_DASH_FRONTEND` to avoid it.
