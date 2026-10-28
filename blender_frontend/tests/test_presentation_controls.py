import math
import unittest
from types import SimpleNamespace

from wfrl_blender.cameras import camera_angles, select_camera
from wfrl_blender.presentation import recording_schedule, set_presentation_mode


class Camera(dict):
    type = 'CAMERA'
    name = 'test'
    data = SimpleNamespace(lens=50)


class Scene(dict):
    def __init__(self):
        super().__init__()
        self.render = SimpleNamespace(resolution_x=1920, resolution_y=1080,
                                      pixel_aspect_x=1.0, pixel_aspect_y=1.0)


class PresentationControlsTests(unittest.TestCase):
    def test_invalid_optics_rejected(self):
        for fov, pitch in ((0, 0), (180, 0), (41, 90), (math.nan, 0), (41, math.inf)):
            with self.assertRaises(ValueError):
                camera_angles(fov, pitch)
        self.assertEqual(camera_angles(75, -30), (75, -30))

    def test_select_camera_changes_optics_without_runtime(self):
        camera = Camera()
        scene = Scene()
        scene.objects = {'test': camera}
        self.assertIs(select_camera(scene, 'test', fov_deg=90, focus='T2'), camera)
        self.assertAlmostEqual(camera.data.lens, 18)
        self.assertEqual(camera['focus'], 'T2')
        self.assertIs(scene.camera, camera)
        self.assertEqual(vars(scene.render), dict(resolution_x=1920, resolution_y=1080,
                                                 pixel_aspect_x=1.0, pixel_aspect_y=1.0))
        with self.assertRaises(ValueError):
            select_camera(scene, 'missing')

    def test_capture_schedule_bounded(self):
        self.assertEqual(recording_schedule(10, 2.5), (.1, 25))
        for fps, duration in ((0, 10), (31, 10), (10, 0), (10, 3601), (math.nan, 1)):
            with self.assertRaises(ValueError):
                recording_schedule(fps, duration)

    def test_layout_mode_preserves_run_and_fidelity(self):
        scene = {'wfrl_run_status': 'RUNNING', 'wfrl_fidelity': 'EXPORTED'}
        space = SimpleNamespace(show_region_toolbar=True, show_region_tool_header=True, show_gizmo=True)
        area = SimpleNamespace(type='VIEW_3D', spaces=SimpleNamespace(active=space), tag_redraw=lambda:None)
        context = SimpleNamespace(scene=scene, workspace={}, screen=SimpleNamespace(areas=[area]))
        set_presentation_mode(context, True)
        self.assertFalse(space.show_gizmo)
        self.assertEqual(scene['wfrl_run_status'], 'RUNNING')
        self.assertEqual(scene['wfrl_fidelity'], 'EXPORTED')
        set_presentation_mode(context, False)
        self.assertTrue(space.show_gizmo)


def test_readonly_sidebar_does_not_leave_native_layout_pending(monkeypatch):
    import sys
    import time
    from wfrl_blender import cameras

    class Sidebar:
        type = 'UI'

        @property
        def active_panel_category(self):
            return 'Item'

    redraws = []
    space = SimpleNamespace(region_3d=SimpleNamespace())
    area = SimpleNamespace(type='VIEW_3D', x=0, y=0, width=900, height=700,
                           spaces=SimpleNamespace(active=space), regions=[Sidebar()],
                           tag_redraw=lambda: redraws.append(1))
    screen = SimpleNamespace(areas=[area])
    scene = Scene()
    camera = object()
    scene.objects = {'camera': camera}
    window = SimpleNamespace(scene=scene, screen=screen)
    job = dict(window=window, scene=scene, screen=screen, count=1, layout='SINGLE',
               camera_names=('camera',), regions_ready=True, deadline=time.monotonic()+10)
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace())
    monkeypatch.setattr(cameras, '_LAYOUT_JOB', job)
    monkeypatch.setattr(cameras, 'fill_camera_view', lambda *_: None)

    assert cameras._layout_timer() is None
    assert cameras._LAYOUT_JOB is None
    assert scene['wfrl_view_layout'] == 'SINGLE'
    assert scene.camera is camera and space.camera is camera
    assert space.show_region_ui and area.regions[0].active_panel_category == 'Item'


def test_native_layout_attribute_error_ends_job_with_visible_error(monkeypatch):
    from wfrl_blender import cameras

    scene = {}
    monkeypatch.setattr(cameras, '_LAYOUT_JOB', {'scene': scene})

    def invalid_native_state():
        raise AttributeError('Native region is unavailable')

    monkeypatch.setattr(cameras, '_layout_step', invalid_native_state)
    assert cameras._layout_timer() is None
    assert cameras._LAYOUT_JOB is None
    assert scene['wfrl_view_layout_error'] == 'Native region is unavailable'


if __name__ == '__main__':
    unittest.main()
