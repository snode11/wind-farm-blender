#!/bin/zsh
set -eu
lidar_project_root="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
exec /Applications/Blender.app/Contents/MacOS/Blender --factory-startup \
  --python "$lidar_project_root/scripts/blender/open_clearance_demo.py"
