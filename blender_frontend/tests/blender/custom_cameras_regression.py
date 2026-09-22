"""Native T1 camera contracts, without modifying a delivered .blend or package.

Run with --background --factory-startup --python-exit-code 1 --python <this file>.
WFRL_TEST_OUTPUT optionally selects a directory for the JSON result.
"""
from dataclasses import replace
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = os.environ.get('WFRL_TEST_RUNTIME', 'wfrl_blender')
if RUNTIME == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / "blender_frontend"), str(ROOT)]
wfrl_blender = importlib.import_module(RUNTIME)
core = importlib.import_module(RUNTIME + '.custom_cameras')


def close(a, b, tolerance=2e-5):
    assert max(abs(x-y) for x, y in zip(a, b)) < tolerance, (list(a), list(b))


def reject(callback):
    try:
        callback()
    except ValueError:
        return
    raise AssertionError("Invalid installation was accepted")


def pose(camera):
    return (core.parameters(camera), tuple(camera.location), tuple(camera.rotation_euler),
            camera.data.lens, camera.parent.name)


def same_parameters(actual, expected):
    # Blender locations use float32; recorded angles retain double precision.
    close(actual.location, expected.location, 1e-6)
    assert replace(actual, location=expected.location) == expected


def make_fixture():
    scene = bpy.context.scene
    for obj in tuple(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    root = bpy.data.objects.new("WFRL.Turbine.T1.YawRoot", None)
    scene.collection.objects.link(root)
    root.location = (12, -17, 83)
    root.rotation_euler = (.13, -.21, .63)
    bpy.ops.mesh.primitive_cube_add(size=2)
    shell = bpy.context.object
    shell.name = "WFRL.Turbine.T1.Nacelle"
    shell.parent = root
    shell.location = (1, .2, .4)
    shell.scale = (2, 1, 1)
    bpy.context.view_layer.update()
    return scene, root, shell


def main():
    wfrl_blender.register()
    scene, root, shell = make_fixture()
    assert core.is_available(scene)
    resolution = (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage)
    output_camera = bpy.data.objects.new("Regression.OutputCamera", bpy.data.cameras.new("Regression.OutputCamera"))
    scene.collection.objects.link(output_camera)
    scene.camera = output_camera
    default_camera = bpy.data.objects.new("WFRL.Camera.T1.Nacelle", bpy.data.cameras.new("Existing default"))
    scene.collection.objects.link(default_camera)
    default_camera.location = (7, 8, 9)
    default_matrix = default_camera.matrix_basis.copy()
    scene.frame_set(147, subframe=.25)

    # A test-owned baseline supplies an actual source file whose bytes and mtime
    # must stay untouched by all camera operations below.
    with tempfile.TemporaryDirectory(prefix="wfrl-custom-camera-") as directory:
        baseline = Path(directory) / "initial.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(baseline))
        file_state = (hashlib.sha256(baseline.read_bytes()).hexdigest(), baseline.stat().st_mtime_ns)
        files_before = set(Path(directory).iterdir())
        save_events = []
        def saved(*args):
            save_events.append(True)
        bpy.app.handlers.save_pre.append(saved)
        try:
            for slot in (1, 2):
                assert core.get_camera(scene, slot) is None
            for slot in (0, 5):
                reject(lambda slot=slot: core.begin_draft(scene, slot))
            params = core.CameraParameters(location=(1.123456789, .234567891, 1.5),
                yaw=123.456789, pitch=-32.123456, roll=47.345678, fov=75.123456)
            anchors = core.validate_position(scene, params.location)
            assert anchors
            anchor = anchors[0]
            close(anchor.point, (params.location[0], params.location[1], 1.4))
            close(anchor.normal, (0, 0, 1))
            assert abs(anchor.distance-.1) < 2e-5
            corner = core.validate_position(scene, (3.1, 1.3, .4))[0]
            close(Vector(corner.point) + Vector(corner.normal)*corner.distance, (3.1, 1.3, .4))
            assert abs(corner.distance-math.sqrt(.02)) < 2e-5
            for bad in ((1, .2, .4), (1, .2, 1.41), (1, .2, 2.1), (math.nan, 0, 0)):
                reject(lambda bad=bad: core.validate_position(scene, bad))
            origin = root.matrix_world @ Vector((1, .2, 10))
            direction = root.matrix_world.to_3x3() @ Vector((0, 0, -1))
            hit = core.raycast_surface(scene, origin, direction)
            assert hit is not None
            close(hit.point, (1, .2, 1.4))
            close(hit.normal, (0, 0, 1))
            # Independent roof attachments are explicitly not installable.
            bpy.ops.mesh.primitive_cube_add(size=1)
            roof = bpy.context.object
            roof.name = "WFRL.Turbine.T1.NacelleRoof"
            roof.parent = root
            roof.location = (1, .2, 4)
            bpy.context.view_layer.update()
            assert core.raycast_surface(scene, origin, direction) is None
            bpy.data.objects.remove(roof, do_unlink=True)
            bpy.context.view_layer.update()

            draft = core.begin_draft(scene, 1)
            assert core.get_camera(scene, 1) is None
            core.apply_parameters(draft, params, anchor=anchor)
            camera = core.commit_draft(scene, 1, draft)
            assert camera == core.get_camera(scene, 1)
            assert camera.parent == root
            same_parameters(core.parameters(camera), params)
            close(camera.location, params.location)
            assert abs(math.degrees(camera.data.angle_x)-params.fov) < 1e-4
            # Parent inverse must not add or cancel the tower transform.
            basis = camera.matrix_basis.copy()
            for translation, rotation in [((15, 29, 90), (.3, -.4, 1.7)),
                    ((-20, 9, 110), (-.2, .5, -2.1))]:
                root.location = translation
                root.rotation_euler = rotation
                bpy.context.view_layer.update()
                expected = root.matrix_world @ basis
                for row in range(4):
                    close(camera.matrix_world[row], expected[row])
                same_parameters(core.parameters(camera), params)

            original = pose(camera)
            draft = core.begin_draft(scene, 1)
            assert draft != camera and draft.data != camera.data
            core.apply_parameters(draft, replace(params, yaw=50, roll=90), anchor=anchor)
            assert pose(camera) == original
            core.cancel_draft(draft)
            assert pose(core.get_camera(scene, 1)) == original

            # Verify arbitrary-roll screen controls against Blender's actual
            # camera optical axes, including looking straight up/down.
            draft = core.begin_draft(scene, 1)
            for pitch in (-90, -23, 0, 90):
                for roll in (0, 37, 90, 180, 271):
                    start = replace(params, pitch=pitch, roll=roll)
                    core.apply_parameters(draft, start, anchor=anchor)
                    q = draft.rotation_euler.to_quaternion()
                    forward = q @ Vector((0, 0, -1))
                    right = q @ Vector((1, 0, 0))
                    up = q @ Vector((0, 1, 0))
                    expected = (forward + math.tan(math.radians(2))*right
                                + math.tan(math.radians(1))*up).normalized()
                    yaw, new_pitch = core.screen_direction_delta(start.yaw, pitch, roll, 2, 1)
                    core.apply_parameters(draft, replace(start, yaw=yaw, pitch=new_pitch), anchor=anchor)
                    close(draft.rotation_euler.to_quaternion() @ Vector((0, 0, -1)), expected, 2e-5)
                    close(draft.location, start.location, 1e-6)
            core.cancel_draft(draft)

            second = core.begin_draft(scene, 2)
            core.place_on_surface(second, anchor, .2)
            close(second.location, Vector(anchor.point) + Vector(anchor.normal)*.2)
            core.set_distance(second, .3)
            close(second.location, Vector(anchor.point) + Vector(anchor.normal)*.3)
            core.commit_draft(scene, 2, second)
            assert pose(camera) == original
            assert core.get_camera(scene, 2) != camera
            text = core.copy_text(scene, camera, 1)
            assert "T1.YawRoot.local" in text and "v1" in text
            assert core.model_signature(scene) in text
            assert "123.456" in text and "1.123456" in text
            core.clear_slot(scene, 1)
            assert core.get_camera(scene, 1) is None and core.get_camera(scene, 2) is not None
            restored = core.begin_draft(scene, 1)
            core.apply_parameters(restored, params, anchor=core.validate_position(scene, params.location)[0])
            restored = core.commit_draft(scene, 1, restored)
            assert pose(restored) == original
            for row in range(4):
                close(restored.matrix_basis[row], basis[row])

            # Session cancellation uses the same public lifecycle as the UI.
            EditSession = importlib.import_module(RUNTIME + '.panels.custom_cameras').EditSession
            runtime = importlib.import_module(RUNTIME + '.runtime')
            FrontendState = importlib.import_module(RUNTIME + '.state').FrontendState
            saved_runtime = runtime._state
            try:
                runtime._state = FrontendState(connection='CONNECTED', run_status='RUNNING')
                blocked = EditSession(scene, 1)
                reject(blocked.begin)
                assert blocked.draft is None
                assert not any(obj.get('wfrl_custom_draft') for obj in scene.objects)
                runtime._state = FrontendState(connection='CONNECTED', run_status='PAUSED', confirmed=False)
                reject(EditSession(scene, 1).begin)
            finally:
                runtime._state = saved_runtime
            session = EditSession(scene, 1)
            draft = session.begin()
            assert session.valid()
            core.apply_parameters(draft, replace(params, yaw=70), anchor=anchor)
            session.cancel()
            assert session.closed and pose(core.get_camera(scene, 1)) == original
            assert scene.frame_current == 147 and scene.frame_subframe == .25
            core.clear_slot(scene, 2)
            session = EditSession(scene, 2)
            session.begin()
            session.cancel()
            assert session.closed and core.get_camera(scene, 2) is None

            # A new replay identity invalidates a draft without resuming the
            # previous session's playback. Observe the real _play call boundary.
            session = EditSession(scene, 2)
            play_calls = []
            session._play = play_calls.append
            session.playing = True
            session.begin()
            scene['wfrl_farm_demo'] = 'regression-replaced-session'
            assert not session.valid()
            session.cancel()
            assert play_calls == [False] and session.closed
            assert core.get_camera(scene, 2) is None
            del scene['wfrl_farm_demo']

            # A failed confirmation retains the input draft for correction.
            session = EditSession(scene, 2)
            draft = session.begin()
            reject(session.confirm)
            assert not session.closed and session.draft == draft
            core.apply_parameters(draft, params, anchor=anchor)
            confirmed = session.confirm()
            assert session.closed and core.get_camera(scene, 2) == confirmed
            same_parameters(core.parameters(confirmed), params)
            core.clear_slot(scene, 2)
            assert not any(obj.get('wfrl_custom_draft') for obj in scene.objects)

            assert default_camera.matrix_basis == default_matrix
            assert scene.camera == output_camera
            assert resolution == (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage)
            assert not save_events
            assert set(Path(directory).iterdir()) == files_before
            assert file_state == (hashlib.sha256(baseline.read_bytes()).hexdigest(), baseline.stat().st_mtime_ns)
        finally:
            bpy.app.handlers.save_pre.remove(saved)
        bpy.ops.wm.open_mainfile(filepath=str(baseline))
        assert all(core.get_camera(bpy.context.scene, slot) is None for slot in (1, 2))
    result = dict(status="PASS", blender=bpy.app.version_string, two_slots=True,
        transformed_surface=True, attachment_rejected=True, outside_validation=True,
        parent_motion_once=True, draft_cancel=True, session_cancel=True,
        session_replacement_cleanup=True, failed_confirm_correctable=True, live_backend_guard=True,
        parameter_roundtrip=True, original_file_unchanged=True, reopening_starts_empty=True,
        default_camera_unchanged=True, output_camera_and_resolution_unchanged=True)
    if os.environ.get("WFRL_TEST_OUTPUT"):
        out = Path(os.environ["WFRL_TEST_OUTPUT"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "custom-cameras-checks.json").write_text(json.dumps(result, indent=2)+"\n")
    print("CUSTOM_CAMERAS_PASS", json.dumps(result), flush=True)


main()
