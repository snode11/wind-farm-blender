"""Open the independent reconstruction tab with installed or explicit dev source.

blender --python open_blade_recon_review.py -- [--recon path] [--truth path]
    [--cameras path] [--texture directory] [--inspect] [--save path] [--source]
Default imports the installed extension; --source is a development-only option.
"""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--recon')
parser.add_argument('--truth')
parser.add_argument('--cameras')
parser.add_argument('--texture', help='v0.2 texture.py output directory; images are packed when loaded')
parser.add_argument('--save')
parser.add_argument('--source', action='store_true')
parser.add_argument('--inspect', action='store_true', help='open appearance enhancement and a manual local inspection point')
argv = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
args = parser.parse_args(argv)
if args.source:
    sys.path.insert(0,str(ROOT/'blender_frontend'))
    module_name = 'wfrl_blender'
else:
    module_name = os.environ.get('WFRL_ADDON_MODULE','bl_ext.user_default.wfrl_blender')
addon = importlib.import_module(module_name)
if not hasattr(bpy.types.Scene,'wfrl_recon_show_truth'):
    addon.register()
review = importlib.import_module(module_name+'.blade_recon_review')
scene = review.load(bpy.context,args.recon,truth_path=args.truth,cameras_path=args.cameras,
                    texture_path=args.texture)
if args.texture and Path(args.recon or '').resolve().parent == ROOT/'blade_recon0.2/out/synth_tex':
    # Locations were selected by looking at the saved atlas, not predicted by
    # a defect detector. Keep this annotation separate from solver outputs.
    scene['blade_recon_manual_inspection_points'] = json.dumps([
        {'label':'亮斑','blade':1,'radius_m':59.3,'tau':.60,'size_m':1.6},
        {'label':'横细线','blade':1,'radius_m':57.0,'tau':.65,'size_m':1.6},
        {'label':'纵细线','blade':1,'radius_m':51.3,'tau':.645,'size_m':1.6},
    ],ensure_ascii=False)
    scene['blade_recon_inspection_point_source'] = 'manual visual selection in saved synthetic atlas; not detected defects'
if args.inspect:
    if not args.texture:
        raise ValueError('--inspect requires --texture')
    scene.wfrl_recon_texture_mode = 'FALSE_COLOR'
    scene.wfrl_recon_texture_gain = 6.
    scene.wfrl_recon_inspect_box = True
    scene.wfrl_recon_inspect_radius = 59.3
    scene.wfrl_recon_inspect_tau = .60
    scene.wfrl_recon_inspect_size = 1.6
    scene.frame_set(min(17,scene.frame_end))
    review.inspect_view(bpy.context)
bpy.context.view_layer.update()
for area in bpy.context.screen.areas if bpy.context.screen else []:
    if area.type == 'VIEW_3D':
        area.spaces.active.show_region_ui = True
        for region in area.regions:
            if region.type == 'UI':
                # Blender 5.2 exposes this RNA value as read-only. Opening the
                # sidebar is sufficient for a ready inspection scene; choosing
                # a cosmetic tab must never abort the import or save operation.
                if not region.is_property_readonly('active_panel_category'):
                    try:
                        region.active_panel_category = '叶片三维重建'
                    except (AttributeError, TypeError):
                        pass
if args.save:
    target = Path(args.save).expanduser().resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(target))
print('BLADE_RECON_READY '+json.dumps({'addon':addon.__file__,'scene':scene.name,
    'recon':scene['blade_recon_source_path'],'source':scene['blade_recon_source_label'],
    'frames':scene.frame_end,'model_sha256':scene['blade_recon_model_sha256'],
    'texture':args.texture},ensure_ascii=False))
