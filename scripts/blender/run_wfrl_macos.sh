#!/bin/zsh
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PYTHON_BIN="${WFRL_LAUNCHER_PYTHON:-python3}"
exec "$PYTHON_BIN" "$SCRIPT_DIR/wfrl_launcher.py" launch "$@"
