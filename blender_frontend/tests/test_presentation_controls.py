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
    pass


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


if __name__ == '__main__':
    unittest.main()
