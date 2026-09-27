#!/bin/zsh
set -eu
task_root="$(cd "$(dirname "$0")" && pwd)"
exec /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python-exit-code 1 --python "$task_root/scripts/blender/open_recommended_three_cameras.py"
