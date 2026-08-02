# Hardware notes

Verified on an ASUS ROG Flow Z13 (GZ302EA), Ryzen AI MAX+ 395, running AMD's
Ryzen AI Developer Platform 1 (rex).

## Scope

strix-dash is a **companion to the rex OS**, not a replacement for its own
dashboard. Support splits into two tiers:

| Tier | Needs | Covers |
|---|---|---|
| Platform | Strix Halo SoC (gfx1151 + aie2p) on Debian 13 | Telemetry, snapshot auditor, requirements |
| Chassis | **ASUS ROG Flow Z13 (GZ302EA)** + `asus-nb-wmi` | The Controls tab, only |

Everything in the Controls tab reaches the `asus-nb-wmi` platform driver or the
`asus_custom_fan_curve` hwmon device. On other hardware those nodes are absent,
the capability probe reports each control unavailable with a reason, and no
endpoint errors -- the rest of the dashboard is unaffected.

## Control surface

| Control | Path | Notes |
|---|---|---|
| Performance profile | `/sys/firmware/acpi/platform_profile` | `quiet balanced performance` |
| Thermal policy | `asus-nb-wmi/throttle_thermal_policy` | 0/1/2 |
| Fan curve | hwmon named `asus_custom_fan_curve` | 8 points x 2 fans |
| Fan RPM | hwmon named `asus` | `fan1_input`, `fan2_input` |
| Battery limit | `/sys/class/power_supply/BAT0/charge_control_end_threshold` | 40-100; see the ownership note below |
| Keyboard backlight | `/sys/class/leds/asus::kbd_backlight/brightness` | 0-3, **no colour** |
| Aura RGB | none | No multicolour LED node exists. Requires [z13ctl](https://github.com/dahui/z13ctl/). |
| NPU power mode | `xrt-smi configure --pmode` | `default powersaver balanced performance turbo` |

## hwmon indices are not stable

The fan-curve device was `hwmon10` at time of writing. Indices are assigned in
probe order and change across reboots — always resolve by reading
`/sys/class/hwmon/*/name`. `install.sh` does this when generating the tmpfiles
config, and the backend repeats it at every startup.

## ppt_* power limits: read-only

The five nodes (`ppt_pl1_spl`, `ppt_pl2_sppt`, `ppt_fppt`, `ppt_apu_sppt`,
`ppt_platform_sppt`) read `5` on a fresh boot and settle to plausible wattages
once something programs them -- 52/71/70/70/70 was observed after z13ctl's setup
ran. So they probably are watts, but "probably" is not a basis for writing to a
power limit: the accepted range and the effect of each node are undocumented.

They are therefore **read-only**: displayed by node name with their raw value,
absent from the sysfs write allowlist, and absent from the tmpfiles grant.

There is no write path for them at all -- not a disabled one. Adding it would
mean confirming the semantics against the `asus-wmi` driver source, adding the
nodes to `_writable_registry()` in `backend/core/sysfs.py`, and uncommenting the
corresponding lines in the tmpfiles template.

## Memory: three sources that disagree

| Source | Reports | Value here |
|---|---|---|
| `rocm-smi --showmeminfo vram` | Fixed BIOS carveout | 512 MB, ~90% used at idle |
| `mem_info_gtt_total` | Dynamically-shared pool | ~107 GB |
| `ttm pages_limit` | Kernel ceiling on that pool | 28174103 pages |

Present GTT as primary. The carveout's high utilisation is normal and is not a
capacity limit.

## Tool quirks

- `flm`, `rocminfo`, `amd-ttm`, `rocm-smi` emit ANSI escapes **even when piped**.
  Only `xrt-smi` offers `--batch` to suppress them.
- `tuned-adm active` **exits 0 even when its daemon is down**, printing
  `Preset profile:` instead of `Current active profile:`. Never trust its exit code.
- `xrt-smi -f JSON` requires `-o <file>`; it exits 1 without one.
- `rocm-smi --json` alone exits 1 — it needs explicit `--show*` queries, and all
  its values are strings.
- `rocminfo`'s `aie2p` agent has an **empty ISA block**. A parser that indexes
  `isa[0]` crashes.


## The battery node is contested

z13ctl's `setup` installs a udev rule that reassigns this node's group on every
matching event:

```
ACTION=="add", SUBSYSTEM=="platform-profile", KERNELS=="asus-nb-wmi",
  RUN+="/usr/bin/chgrp users .../charge_control_end_threshold"
```

That overwrites the group strix-dash's tmpfiles.d rule grants. Two packages
cannot both own one node's group, and whichever udev rule runs last wins -- so
the control would break unpredictably for whoever installed second.

Rather than fight, `set_battery_limit()` prefers a direct sysfs write and falls
back to `strix-dash-priv-helper.sh`, which runs as root and does not care who
owns the node. The Controls tab reports which path was used in `backend`.

The range is **40-100**, matching z13ctl. The kernel accepts lower values (39
writes fine), but both paths must agree or the same request would succeed or
fail depending on which backend happened to run.
