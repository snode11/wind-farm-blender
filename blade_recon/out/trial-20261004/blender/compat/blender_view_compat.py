"""Blender 5.2.1 compatibility wrapper around the unchanged delivered viewer.
Only matrix assignment and the imported camera output resolution are adjusted.
Intrinsics retain the original viewer's fx-only approximation (fy is not imported).
Example (paths relative to blade_recon):
 blender --python out/trial-20261004/blender/compat/blender_view_compat.py -- \
   --recon out/trial-20261004/synth20/recon.json \
   --truth data/synth20/truth.json --cameras data/synth20/cameras.json
This clears/recreates the selected process's scene, as the original viewer does.
Frame handlers live in that process only; reopen playback by running this wrapper.
"""
from pathlib import Path
import bpy
from mathutils import Matrix

ROOT=Path(__file__).resolve().parents[4]
original=ROOT/'blender_view.py'
source=original.read_text(encoding='utf-8')
needle='ob.matrix_world = [list(r) for r in M]'
if source.count(needle)!=1:
    raise RuntimeError('Expected original camera assignment not found exactly once; inspect source before updating wrapper.')
source=source.replace(needle,'ob.matrix_world = Matrix(M.tolist())',1)
namespace={'__name__':'__main__','__file__':str(original),'Matrix':Matrix}
exec(compile(source,str(original),'exec'),namespace)
a=namespace['parse_args']()
if a['cameras']:
    dims={(int(d['W']),int(d['H'])) for d in namespace['load_json'](a['cameras'])['cameras']}
    if len(dims)!=1:
        raise ValueError('This trial wrapper expects equal output dimensions for all imported cameras.')
    W,H=dims.pop()
    scene=bpy.context.scene
    scene.render.resolution_x=W; scene.render.resolution_y=H
    scene.render.resolution_percentage=100
    # Choose the middle trial camera for a usable saved camera-view snapshot.
    if 'C2' in bpy.data.objects:
        scene.camera=bpy.data.objects['C2']
bpy.context.view_layer.update()
print('[blade_recon compat] Camera matrix uses mathutils.Matrix; scene resolution matches camera JSON. Original fx-only K approximation retained.')
