"""Support-base contact stays fixed while housing/cameras swivel at the outer end."""
from pathlib import Path
import copy
import importlib
import json
import os
import sys

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [os.environ.get('WFRL_ADDON_ROOT', str(ROOT / 'blender_frontend')), str(ROOT)]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
addon = importlib.import_module(MODULE)
core = importlib.import_module(MODULE + '.custom_cameras')
rig = importlib.import_module(MODULE + '.stacked_camera_rig')
projection = importlib.import_module(MODULE + '.camera_projection')
history = importlib.import_module(MODULE + '.custom_camera_history')
panel = importlib.import_module(MODULE + '.panels.stacked_camera_rig')
addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
root = scene.objects[core.ROOT_NAME]
arm = scene.objects[rig.PREFIX + '.MountArm']
baseline = core.layout_dict(scene)
baseline_hash = core.live_layout_hash(scene)
baseline_arm = arm.matrix_basis.copy()
baseline_rotation = rig.pose_rotation(baseline['rig_pose'])
depth = json.loads(scene.objects[rig.PREFIX]['assumptions_json'])['depth_m']
geometry = core._geometry(scene)[0]
cases = []


def difference(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(len(a)) for j in range(len(a[0])))


for origin, direction in (((0, 0, 10), (0, 0, -1)), ((1, -10, 1.5), (0, 1, 0)), ((0, -10, 2.3), (0, 1, 0))):
    point, normal, *_ = geometry[-1].ray_cast(Vector(origin), Vector(direction))
    assert point is not None
    anchor = core.SurfaceAnchor(tuple(point), tuple(normal), 0., geometry[0])
    for spin in (0., 37.):
        transaction = panel.Placement(scene)
        expected_arm = None
        for aim in (0., -30., 30., -45., 45.):
            transaction.preview(anchor, spin, aim)
            bpy.context.view_layer.update()
            arm_matrix = root.matrix_world.inverted() @ arm.matrix_world
            if expected_arm is None:
                expected_arm = arm_matrix.copy()
            assert difference(arm_matrix, expected_arm) < 3e-5, 'support moved while housing swivelled'
            distances = [((arm_matrix @ vertex.co) - point).dot(normal) for vertex in arm.data.vertices]
            assert abs(min(distances)) < 3e-5 and abs(max(distances) - .28) < 3e-5
            actual = rig.pose(scene)
            rotation = rig.pose_rotation(actual)
            pivot = Vector(actual['center']) + rotation @ Vector((0., depth-.004, 0.))
            assert (pivot - (point + normal*.274)).length < 3e-5, 'rear-face pivot moved'
            assert actual['surface_mount']['aim_deg'] == aim
            delta = rotation @ baseline_rotation.transposed()
            for record in baseline['cameras']:
                camera = core.get_camera(scene, record['slot_id'])
                parameters = core.parameters(camera)
                before = record['parameters']
                expected = delta @ Matrix(projection.rotation(before['yaw'], before['pitch'], before['roll']))
                assert difference(expected, camera.matrix_basis.to_3x3()) < 3e-5
                assert (parameters.fov, parameters.vfov) == (before['fov'], before['vfov'])
            layout = json.loads(json.dumps(core.layout_dict(scene)))
            core.validate_layout(scene, layout)
        transaction.finish()
        assert core.live_layout_hash(scene) == baseline_hash
        assert difference(arm.matrix_basis, baseline_arm) < 3e-5
    cases.append({'point': list(point), 'normal': list(normal), 'spins': [0, 37], 'aims': [0, -30, 30, -45, 45]})

# Commit + undo and JSON import each restore the compensating arm transform.
transaction = panel.Placement(scene)
transaction.preview(anchor, 37., -30.)
before_count = len(history.HISTORY.entries)
transaction.finish(confirm=True)
assert len(history.HISTORY.entries) == before_count + 1
saved = json.loads(json.dumps(core.layout_dict(scene)))
saved_hash = core.live_layout_hash(scene)
saved_arm = arm.matrix_basis.copy()
history.undo(scene)
assert core.live_layout_hash(scene) == baseline_hash
assert difference(arm.matrix_basis, baseline_arm) < 3e-5
core.import_layout(scene, saved, overwrite=True)
bpy.context.view_layer.update()
assert core.live_layout_hash(scene) == saved_hash
assert difference(arm.matrix_basis, saved_arm) < 3e-5

# A changed swivel metadata value cannot forge a pose with unchanged cameras.
for value in (0., float('nan')):
    invalid = copy.deepcopy(saved)
    invalid['rig_pose']['surface_mount']['aim_deg'] = value
    try:
        core.validate_layout(scene, invalid)
    except ValueError:
        pass
    else:
        raise AssertionError('inconsistent or nonfinite swivel accepted')

# A pre-swivel SUPPORT JSON without aim_deg stays valid and unchanged.
legacy_pose = rig.surface_pose(scene, anchor, 0.)
legacy = rig.transformed_layout(scene, baseline, legacy_pose)
del legacy['rig_pose']['surface_mount']['aim_deg']
core.restore_layout(scene, legacy)
assert 'aim_deg' not in rig.pose(scene)['surface_mount']
core.validate_layout(scene, core.layout_dict(scene))
core.restore_layout(scene, baseline)
assert core.live_layout_hash(scene) == baseline_hash
assert difference(arm.matrix_basis, baseline_arm) < 3e-5
OUT = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/wfrl-box-swivel'))
OUT.mkdir(parents=True, exist_ok=True)
(OUT / 'swivel-validation.json').write_text(json.dumps({
    'status': 'PASS', 'module': MODULE, 'blender': bpy.app.version_string, 'cases': cases,
    'checks': ['zero-aim geometry preserved', 'fixed support base and arm under housing yaw',
               'fixed rear pivot', 'rigid optics', 'cancel', 'single undo', 'JSON round trip',
               'forged/nonfinite aim rejection', 'legacy no-aim SUPPORT layout'],
}, indent=2))
print('BOX_SWIVEL_PASS', flush=True)
