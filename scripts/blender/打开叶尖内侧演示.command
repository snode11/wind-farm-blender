#!/bin/zsh
set -eu
wfrl_tip_root="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
exec /Applications/Blender.app/Contents/MacOS/Blender --factory-startup \
  --python "$wfrl_tip_root/scripts/blender/open_blade_tip_demo.py"
