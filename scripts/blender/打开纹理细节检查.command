#!/bin/zsh
set -eu
task_root="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
blender_bin="${BLADE_RECON_BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
exec "$blender_bin" --python "$task_root/scripts/blender/open_blade_recon_review.py" -- \
  --recon "$task_root/blade_recon0.2/out/synth_tex/recon.json" \
  --texture "$task_root/blade_recon0.2/out/synth_tex/texture" --inspect
