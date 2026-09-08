#!/bin/zsh
set -euo pipefail

WFRL_PROJECT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
WFRL_BLENDER_PATH="${WFRL_BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"

# Blender 5.2.1 can crash in WM_init while probing Metal from Codex's
# seatbelt. Stop before creating a Blender process so this launcher cannot
# turn a known environment problem into another macOS crash report.
if [[ "${CODEX_SANDBOX:-}" == "seatbelt" ]]; then
    print -u2 "WFRL: Blender launch stopped before process start."
    print -u2 "The current Codex seatbelt sandbox triggers Blender's Metal capability probe."
    print -u2 "Open this command from Finder or a normal Terminal outside the sandbox."
    exit 78
fi

if [[ ! -x "$WFRL_BLENDER_PATH" ]]; then
    print -u2 "WFRL: Blender executable is missing or not executable: $WFRL_BLENDER_PATH"
    exit 127
fi

# Keep the embedded Python from adding bytecode files to the signed Blender
# bundle. This does not repair an already modified installation; reinstalling
# Blender is a separate user decision and is intentionally not automated here.
export PYTHONDONTWRITEBYTECODE=1
exec "$WFRL_BLENDER_PATH" --factory-startup --python "$WFRL_PROJECT_DIR/scripts/blender/open_part2.py"
