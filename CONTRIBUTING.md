# Contributing

## Development loop

```bash
./scripts/dev-run.sh      # runs as you on :10001
python3 -m pytest         # offline; no hardware or network needed
./scripts/sync-dist.sh    # after any frontend/src change
sudo ./scripts/deploy.sh  # push to an installed service
```

## Rules worth knowing before you change anything

**Never call `subprocess` outside `core/runner.py`.** It is the only place that
handles timeouts, ANSI stripping and error normalisation. Four of the six tools
this app drives emit colour escapes even when piped.

**Always use absolute tool paths from `config.TOOLS`.** `amd-ttm` resolves via
`PATH` to a pipx copy in a `0700` home that the service user cannot execute.

**Writes read back.** Return what the hardware holds afterwards, never an echo
of the request. `verified: false` is a legitimate outcome, not an error.

**Degraded is not an error.** A missing tool or a stopped daemon returns HTTP
200 with `ok: false` so the UI renders panel state instead of a network failure.
Reserve 4xx/5xx for bad requests and real faults.

## Adding a control

1. Add read state to `controls/hardware.py:read_all()` including a `writable` flag.
2. Add the write function: clamp, write, read back.
3. If the node is new, add it to `core/sysfs.py:_writable_registry()` **and** to
   `packaging/tmpfiles.d/strix-dash.conf.in`.
4. Add an endpoint in `api/controls.py`.

The frontend renders from `GET /controls`, so no UI change is usually needed.

## Adding a requirement

Add an entry to `requirements/registry.py`. That file is data, not code — the
Requirements page renders whatever is in it. Only add an installable
`github-release` source if the release publishes a per-asset digest, and mirror
the id into the allowlist in `packaging/strix-dash-req-helper.sh`.

## Fixtures

`tests/fixtures/raw/` holds real tool output, redacted by
`scripts/sanitize-fixtures.sh`. Run it before committing new captures. CI fails
if any fixture still carries host-identifying data.
