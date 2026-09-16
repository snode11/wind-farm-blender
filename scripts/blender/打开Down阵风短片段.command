#!/bin/zsh
set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
exec /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python "$PROJECT_DIR/scripts/blender/open_down_gust_preview.py"
