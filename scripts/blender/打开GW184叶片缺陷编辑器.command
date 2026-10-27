#!/bin/zsh
set -e
TASK_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec /Applications/Blender.app/Contents/MacOS/Blender --factory-startup \
  --python "$TASK_ROOT/scripts/blender/gw184_defects.py" -- --mode edit --all-presets "$@"
