#!/bin/zsh
set -eu
script_dir="${0:A:h}"
exec /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python "$script_dir/open_nrel_defect_editor.py"
