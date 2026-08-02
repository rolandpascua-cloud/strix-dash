# Security

strix-dash writes to hardware and installs packages as root. This document
states plainly what it can do, what protects it, and what it does not protect
against.

## Threat model

The application is a **loopback-only service with no authentication**. Its
security boundary is:

1. It binds `127.0.0.1` and never `0.0.0.0`.
2. Privileged actions go through a small, fixed set of `sudoers` rules.
3. Writable sysfs nodes are granted by group ownership, not by escalation.

**Anything that can reach `127.0.0.1:10001` can use every control.** That is a
coherent model for a single-user laptop and an inadequate one for a shared or
network-exposed machine. Do not change `STRIX_DASH_HOST`.

## CSRF

Binding to loopback does **not** stop a page in your browser from POSTing to
this service. State-changing requests therefore require:

- a custom `X-Strix-Dash` header (which forces a CORS preflight cross-origin), and
- an `Origin` matching the service, when one is present.

## Privilege layers, least first

| Layer | Used for | Escalation |
|---|---|---|
| None | All telemetry, snapshot diffing | — |
| `tmpfiles.d` group grant | Profile, fan curve, battery, backlight | None — group ownership on specific nodes |
| `sudoers` | NPU power mode, TuneD, install helper | NOPASSWD, fixed command paths |

The sysfs write path is **allowlisted in code** (`core/sysfs.py`): a path not in
the registry is refused, so a bug cannot become an arbitrary file write.

### sudoers scope

- `xrt-smi configure --pmode` enumerates its five valid values **literally**.
  A wildcard would permit arbitrary trailing arguments.
- `tuned-adm profile *` is a wildcard, so the backend validates the profile name
  against `tuned-adm list` before invoking it.
- The two helper scripts take a fixed verb plus an identifier — never a path,
  URL or filename.

## Installing packages

`strix-dash-req-helper.sh` is the only code that reaches the network or installs
software. Its guarantees:

- The caller supplies a **requirement id** matching `[a-z0-9-]+` and nothing
  else. Repository, asset name and URL are re-resolved from a hardcoded
  allowlist inside the helper.
- The download URL must lie under the expected release path, and the asset must
  be a `.deb` with a bare filename.
- The file is verified against the SHA256 digest published with the release.
  **Installation is refused outright if no digest is available.**
- Staging is root-owned `0700`; the file is deleted on every exit path.
- Nothing is automatic. No timer, no "update all". Each install requires a
  confirmation token issued after the user has been shown the filename, source
  and size.

### What digest verification does not prove

The digest is served by the same host as the file. It authenticates the
**integrity of the transfer** — a truncated download, a corrupted mirror, an
interfering proxy. It does **not** authenticate the publisher. A compromised
upstream account would publish a matching digest for a malicious asset. There is
no signature to check, and the UI says so rather than implying otherwise.

## Deliberately not implemented

- **TDP writes.** The `ppt_*` nodes all read `5`; their unit is unconfirmed.
  Writing a wrong-unit value to a power limit is the highest-consequence bug
  available here, so they are read-only and not in the sysfs allowlist.
- **Undervolt.** Instability from an aggressive curve-optimiser offset appears
  as a crash minutes later, which read-back cannot catch. Not shipped without a
  revert-on-unclean-boot watchdog.

## Hardening note

The systemd unit does **not** set `SystemCallFilter`. Installing a seccomp
filter on an unprivileged service forces `PR_SET_NO_NEW_PRIVS`, which makes the
kernel ignore the setuid bit on `sudo` — `systemctl show` still reports
`NoNewPrivileges=no` while `/proc/<pid>/status` shows `NoNewPrivs: 1`. A syscall
filter and the privileged controls are mutually exclusive. Everything else
(`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `IPAddressDeny=any`,
`MemoryDenyWriteExecute`, namespace and kernel protections) remains enabled.

## Reporting

Open an issue. Include `GET /api/v1/capabilities` output — it is already free of
identifying data. Do not attach raw tool output without running
`scripts/sanitize-fixtures.sh` over it first.
