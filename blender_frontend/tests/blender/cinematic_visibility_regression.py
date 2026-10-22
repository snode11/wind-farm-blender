"""Playback visibility contract: opaque signal and resolvable persistent trails."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
from wfrl_blender import cinematic

scene = bpy.context.scene
root = bpy.data.objects.new('WFRL.Turbine.T1.YawRoot', None)
scene.collection.objects.link(root)
# Existing saved scenes must migrate in place, without accumulating geometry.
curve = bpy.data.curves.new('LegacyFlow', 'CURVE')
obj = bpy.data.objects.new('WFRL.Cinematic.T1', curve)
scene.collection.objects.link(obj)
obj['wfrl_revision'] = 3
legacy = bpy.data.materials.new('WFRL.Cinematic.Silk')
legacy['wfrl_revision'] = 3
legacy.use_nodes = True
curve.materials.append(legacy)
cinematic.ensure(scene)
mat = obj.data.materials[0]
out = next(n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL')
assert out.inputs['Surface'].is_linked, 'Legacy material was not upgraded'
assert out.inputs['Surface'].links[0].from_node.type == 'EMISSION', 'Playback signal still depends on stochastic transparency'
# At the tested farm overview, persistent trails below this world diameter
# disappear between temporal samples. End tapers remain allowed to vanish.
for index in range(0, cinematic.STRANDS, 4):
    coords, widths = cinematic.filament(index, 14, .2)
    for j, width in enumerate(widths):
        if -100 < coords[j*4] < 250:
            assert 2 * obj.data.bevel_depth * width >= .55, 'Persistent trail body is subpixel in the farm overview'
before = (len(bpy.data.objects), len(bpy.data.curves), len(bpy.data.materials))
cinematic.ensure(scene)
assert before == (len(bpy.data.objects), len(bpy.data.curves), len(bpy.data.materials))
assert obj.data == curve and len(curve.splines) == cinematic.STRANDS
print('CINEMATIC_VISIBILITY_REGRESSION=PASS')
