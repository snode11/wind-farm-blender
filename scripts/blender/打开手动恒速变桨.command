#!/bin/zsh
set -eu
WFRL_SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
WFRL_BLENDER_BIN="${WFRL_BLENDER_BIN:-/Applications/Blender.app/Contents/MacOS/Blender}"
if [[ ! -x "$WFRL_BLENDER_BIN" ]]; then
  print -u2 '未找到 Blender；请用 WFRL_BLENDER_BIN 指定可执行文件。'
  exit 1
fi
WFRL_TASK_CONFIG="$(mktemp -d "${TMPDIR:-/tmp}/wfrl-manual-control.XXXXXX")"
export BLENDER_USER_CONFIG="$WFRL_TASK_CONFIG"
exec "$WFRL_BLENDER_BIN" --factory-startup --python-exit-code 1 --python "$WFRL_SCRIPT_DIR/open_manual_control.py"
