"""The single subprocess choke point.

No other module in this codebase calls ``subprocess`` or ``asyncio.create_subprocess_*``.
Routing everything through here means the six tools' six different failure modes
get normalised exactly once:

* ANSI escapes are stripped unconditionally (four of the tools emit them when piped)
* argv[0] is resolved to an absolute path, never via PATH (the amd-ttm trap)
* timeouts kill the whole process group rather than leaking a child
* ``tuned-adm`` exits 0 even when its daemon is down, so callers can opt out of
  trusting the exit code and let the parser decide
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import signal
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from backend import config
from backend.core import errors
from backend.core.ansi import clean

ParseMode = Literal["raw", "json", "lines"]

# Force a predictable locale and ask (politely, mostly in vain) for no colour.
# None of these tools honour NO_COLOR today; costs nothing and guards the future.
_BASE_ENV = {
    "LC_ALL": "C",
    "LANG": "C",
    "NO_COLOR": "1",
    "TERM": "dumb",
    "CLICOLOR": "0",
}


@dataclass
class RunResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    argv: list[str]
    parsed: Any = None
    error: errors.ToolError | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def command(self) -> str:
        return shlex.join(self.argv)


def resolve(tool: str) -> str:
    """Absolute path for a configured tool name, or the name itself if unknown."""
    return config.TOOLS.get(tool, tool)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(_BASE_ENV)
    return env


async def run(
    argv: Sequence[str],
    *,
    tool: str | None = None,
    timeout: float | None = None,
    parse: ParseMode = "raw",
    trust_exit_code: bool = True,
    ok_exit_codes: tuple[int, ...] = (0,),
    input_text: str | None = None,
) -> RunResult:
    """Execute *argv* and return a normalised :class:`RunResult`.

    ``tool`` selects the configured timeout and gives errors a friendly name;
    it defaults to the basename of argv[0].

    Never raises for tool-level failures -- inspect ``result.ok`` and
    ``result.error``. Only programmer errors propagate.
    """
    argv = list(argv)
    if not argv:
        raise ValueError("argv must not be empty")

    name = tool or os.path.basename(argv[0])
    if tool and not os.path.isabs(argv[0]):
        argv[0] = resolve(tool)

    if not os.path.isabs(argv[0]):
        raise ValueError(f"refusing to execute non-absolute path: {argv[0]!r}")

    limit = timeout if timeout is not None else config.TIMEOUTS.get(name, config.DEFAULT_TIMEOUT)
    started = time.perf_counter()

    def _fail(err: errors.ToolError, code: int = -1) -> RunResult:
        return RunResult(
            ok=False,
            exit_code=code,
            stdout="",
            stderr="",
            duration_ms=(time.perf_counter() - started) * 1000,
            argv=argv,
            error=err,
        )

    if not os.path.exists(argv[0]):
        return _fail(errors.tool_missing(name, install_command=config.INSTALL_HINTS.get(name)))
    if not os.access(argv[0], os.X_OK):
        return _fail(errors.tool_not_executable(name, argv[0]))

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_env(),
            # Own process group so a timeout can take out any grandchildren too.
            start_new_session=True,
        )
    except PermissionError:
        return _fail(errors.permission_denied(argv[0]))
    except OSError as exc:  # pragma: no cover - defensive
        return _fail(
            errors.ToolError(
                code=errors.ErrorCode.TOOL_FAILED,
                message=f"Could not start {name}: {exc}",
                retryable=True,
            )
        )

    payload = input_text.encode() if input_text is not None else None
    try:
        raw_out, raw_err = await asyncio.wait_for(proc.communicate(payload), timeout=limit)
    except TimeoutError:
        _kill_group(proc)
        # Reap so the event loop doesn't warn about an orphaned transport.
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except TimeoutError:  # pragma: no cover - very defensive
            pass
        return _fail(errors.timeout(name, limit))

    duration_ms = (time.perf_counter() - started) * 1000
    stdout = clean(raw_out.decode("utf-8", "replace"))
    stderr = clean(raw_err.decode("utf-8", "replace"))
    code = proc.returncode if proc.returncode is not None else -1

    result = RunResult(
        ok=True,
        exit_code=code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        argv=argv,
    )

    if trust_exit_code and code not in ok_exit_codes:
        result.ok = False
        combined = (stderr or stdout).strip()
        lines = combined.splitlines()
        # Some tools print a banner before the real error (xrt-smi leads with its
        # build version), so surface the first line that looks like a diagnosis
        # rather than blindly taking line 0.
        signal = next(
            (
                ln
                for ln in lines
                if any(
                    marker in ln.lower() for marker in ("error", "denied", "failed", "not allowed")
                )
            ),
            lines[0] if lines else "",
        )
        result.error = errors.ToolError(
            code=errors.ErrorCode.TOOL_FAILED,
            message=f"{name} exited {code}" + (f": {signal[:200]}" if signal else ""),
            # Bounded excerpt so the cause is diagnosable from the API alone.
            detail={"exit_code": code, "output_excerpt": combined[:1500]},
        )
        return result

    if parse == "json":
        try:
            result.parsed = json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError as exc:
            result.ok = False
            result.error = errors.parse_error(name, str(exc), stdout)
    elif parse == "lines":
        result.parsed = [ln for ln in stdout.split("\n") if ln.strip()]

    return result


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the child's process group, falling back to the child alone."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def run_tool(tool: str, *args: str, **kwargs: Any) -> RunResult:
    """Convenience wrapper: ``run_tool("flm", "validate", "-j")``."""
    return await run([resolve(tool), *args], tool=tool, **kwargs)
