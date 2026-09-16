#!/bin/zsh
set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
export WFRL_AUTOPLAY=1
exec /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python "$SCRIPT_DIR/open_blade_flex_down.py"
