"""Launch checks that run before Blender is allowed to start.

Blender 5.2.1 on this machine can segfault while probing Metal when it is
started inside Codex's ``seatbelt`` sandbox.  This module deliberately uses
only the Python standard library so callers can check the environment without
starting Blender (and therefore without triggering that crash path).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
import json
import os
from pathlib import Path
import sys


RESTRICTED_SANDBOX_VALUES = frozenset({"seatbelt", "sandbox", "restricted"})
EXIT_BLOCKED = 78


class BlenderLaunchBlocked(RuntimeError):
    """Raised when launching Blender would enter a known unsafe environment."""


@dataclass(frozen=True)
class PreflightResult:
    executable: str
    exists: bool
    executable_bit: bool
    sandbox: str
    restricted: bool
    launchable: bool
    reason: str = ""


def inspect_blender(executable: str | os.PathLike[str], *, env: dict[str, str] | None = None) -> PreflightResult:
    """Inspect a Blender path without opening the executable."""

    path = Path(executable).expanduser()
    values = os.environ if env is None else env
    sandbox = str(values.get("CODEX_SANDBOX", "")).strip()
    restricted = sandbox.lower() in RESTRICTED_SANDBOX_VALUES
    exists = path.is_file()
    executable_bit = os.access(path, os.X_OK) if exists else False
    if not exists:
        reason = f"Blender executable does not exist: {path}"
    elif not executable_bit:
        reason = f"Blender path is not executable: {path}"
    elif restricted:
        reason = (
            f"Blender launch blocked: CODEX_SANDBOX={sandbox}. "
            "Blender 5.2.1 crashes during Metal capability detection in this "
            "sandbox before Python or a .blend file can run. Start it from "
            "Finder or a normal Terminal outside the sandbox."
        )
    else:
        reason = ""
    return PreflightResult(
        executable=str(path),
        exists=exists,
        executable_bit=executable_bit,
        sandbox=sandbox,
        restricted=restricted,
        launchable=exists and executable_bit and not restricted,
        reason=reason,
    )


def require_launchable(executable: str | os.PathLike[str], *, env: dict[str, str] | None = None) -> PreflightResult:
    """Return a passing result or raise before any Blender process starts."""

    result = inspect_blender(executable, env=env)
    if not result.launchable:
        raise BlenderLaunchBlocked(result.reason or "Blender launch preflight failed")
    return result


def _default_executable() -> str:
    return os.environ.get("WFRL_BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Blender launch safety without starting Blender")
    parser.add_argument("--blender", default=_default_executable())
    parser.add_argument("--quiet", action="store_true", help="print only failures")
    args = parser.parse_args(argv)
    result = inspect_blender(args.blender)
    if not args.quiet or not result.launchable:
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    if not result.launchable:
        print(result.reason, file=sys.stderr)
        return EXIT_BLOCKED if result.restricted else 127
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
