"""Safe v0.2 viewer using the project's independent Blade Recon scene.

Blender --python blade_recon0.2/blender_view.py -- --recon result/recon.json
    [--texture result/texture] [--truth truth.json] [--cameras cameras.json]
    [--save result.blend] [--addon-module bl_ext.user_default.wfrl_blender]

The default runtime reuses an enabled compatible installed addon, or loads the
adjacent frontend source in a clean session. It preserves all existing
scenes and handlers, embeds validated JSON, packs images, and bakes native held
geometry/evidence samples so a saved file remains animated without source paths
or the addon. --no-bake is intended for addon-managed development sessions.
"""
import argparse
import importlib
import inspect
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parent.parent


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recon', required=True)
    parser.add_argument('--truth')
    parser.add_argument('--cameras')
    parser.add_argument('--texture')
    parser.add_argument('--save')
    parser.add_argument('--addon-module', default='auto')
    parser.add_argument('--no-bake', action='store_true')
    argv = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    return parser.parse_args(argv)


def main():
    args = parse_args()
    module = args.addon_module
    if module == 'auto':
        installed = 'bl_ext.user_default.wfrl_blender'
        if installed in bpy.context.preferences.addons:
            candidate = importlib.import_module(installed+'.blade_recon_review')
            if 'texture_path' not in inspect.signature(candidate.load).parameters:
                raise RuntimeError('Enabled legacy addon does not support v0.2 textures. '
                    'Use the updated project extension or launch this source viewer '
                    'with Blender --factory-startup in a separate process.')
            module = installed
        else:
            module = 'wfrl_blender'
    if module == 'wfrl_blender':
        sys.path.insert(0, str(ROOT / 'blender_frontend'))
    addon = importlib.import_module(module)
    if not hasattr(bpy.types.Scene, 'wfrl_recon_show_texture'):
        addon.register()
    review = importlib.import_module(module + '.blade_recon_review')
    scene = review.load(bpy.context, args.recon, truth_path=args.truth,
                        cameras_path=args.cameras, texture_path=args.texture)
    if not args.no_bake:
        review.bake_portable(scene)
    review.apply(scene)
    bpy.context.view_layer.update()
    if args.save:
        path = Path(args.save).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
    print('[blade_recon] loaded', scene.frame_end, 'saved samples; runtime:', addon.__file__,
          '; independent scene:', scene.name, '; packed texture:', bool(scene.get('texture_text')),
          '; native animation:', bool(scene.get('blade_recon_portable_geometry')))
    return scene


if __name__ == '__main__':
    main()
