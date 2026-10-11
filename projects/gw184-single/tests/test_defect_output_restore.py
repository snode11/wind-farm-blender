"""Pre-image export failure recovery, using only Python objects.

Extract the real function from its AST without importing the Blender module.
No Blender process, image fixture, filesystem output, or renderer is used.
"""
import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'gw184' / 'defect_outputs.py'


def load_export(dependencies):
    tree = ast.parse(SOURCE.read_text(), filename=str(SOURCE))
    node, = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == 'export_appearance']
    module = ast.Module(body=[node], type_ignores=[])
    namespace = dict(dependencies)
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), 'exec'), namespace)
    return namespace['export_appearance']


class Scene(dict):
    """The minimal mapping/attribute interface used before the first image."""


class ExportFixture:
    def __init__(self, *, playing=True, mapping_error=None, update_error=None,
                 analysis_error=None):
        self.events = []
        self.render_calls = 0
        self.update_states = []
        self.original_camera = object()
        self.committed = object()
        self.scene = Scene(defect_time_s=17.875)
        self.scene.camera = self.original_camera
        self.scene.render = SimpleNamespace(filepath='original/output')
        self.scene.cycles = SimpleNamespace(seed=43)
        self.scene.view_settings = SimpleNamespace(view_transform='Standard', look='None', exposure=.25, gamma=1.)
        self.screen = SimpleNamespace(is_animation_playing=playing)
        self.original_playing = playing
        self.geometry = SimpleNamespace(active=self.committed)

        def activate(payload):
            self.events.append('activate')
            self.geometry.active = payload

        def set_time(time_s):
            self.events.append(('set_time', time_s))
            self.scene['defect_time_s'] = time_s

        def cancel(**kwargs):
            self.events.append(('pause', kwargs))
            self.screen.is_animation_playing = False

        def play():
            self.events.append('play')
            self.screen.is_animation_playing = True

        def render(**kwargs):
            self.render_calls += 1
            raise AssertionError('No test may reach the image renderer')

        def current(time_s):
            self.events.append(('mapping', time_s))
            if mapping_error is not None:
                raise mapping_error
            return dict(time_s=time_s, frame_index=None)

        def update(run):
            self.events.append('update')
            self.update_states.append(copy.deepcopy(run['manifest']))
            if update_error is not None and len(self.update_states) == 1:
                raise update_error

        def analysis(editor, run, time_s):
            self.events.append(('analysis', time_s))
            # Exercise restoration of state changed during an interrupted analysis.
            self.scene.camera = object()
            self.scene.render.filepath = 'interrupted/output'
            self.scene.cycles.seed = 99
            self.geometry.active = object()
            if analysis_error is not None:
                raise analysis_error
            raise AssertionError('Every test must stop before producing an image')

        self.geometry.activate = activate
        self.geometry.dispose = lambda payload: self.events.append('dispose')
        self.editor = SimpleNamespace(scene=self.scene, draft=None,
            payload=self.committed, geometry=self.geometry, set_time=set_time)
        self.run = dict(manifest=dict(mode='A', state='PREPARED', images=[]),
                        mapping=SimpleNamespace(current=current))
        self.initial_manifest = copy.deepcopy(self.run['manifest'])
        self.export = load_export(dict(
            bpy=SimpleNamespace(context=SimpleNamespace(screen=self.screen),
                ops=SimpleNamespace(screen=SimpleNamespace(
                    animation_cancel=cancel, animation_play=play),
                    render=SimpleNamespace(render=render))),
            check_run=lambda editor, run: self.events.append('check'),
            update_run=update, save_analysis=analysis,
            save_review=lambda editor, run: self.events.append('review')))

    def assert_restored(self, testcase):
        testcase.assertEqual(self.scene['defect_time_s'], 17.875)
        testcase.assertIs(self.scene.camera, self.original_camera)
        testcase.assertEqual(self.scene.render.filepath, 'original/output')
        testcase.assertEqual(self.scene.cycles.seed, 43)
        testcase.assertIs(self.geometry.active, self.committed)
        testcase.assertEqual(self.screen.is_animation_playing, self.original_playing)
        testcase.assertEqual(self.render_calls, 0)
        testcase.assertEqual(self.run['manifest']['images'], [])


class ExportRestoreTests(unittest.TestCase):
    def test_authorization_rejection_has_no_side_effects(self):
        fixture = ExportFixture()
        with self.assertRaisesRegex(ValueError, 'Rendering is disabled'):
            fixture.export(fixture.editor, fixture.run)
        fixture.assert_restored(self)
        self.assertEqual(fixture.events, [])
        self.assertEqual(fixture.run['manifest'], fixture.initial_manifest)

    def test_invalid_mapping_is_rejected_before_playback_pauses(self):
        fixture = ExportFixture(mapping_error=ValueError('invalid mapping'))
        with self.assertRaisesRegex(ValueError, 'invalid mapping'):
            fixture.export(fixture.editor, fixture.run, render_authorized=True,
                           time_s=200.25)
        fixture.assert_restored(self)
        self.assertEqual(fixture.events, ['check', ('mapping', 200.25)])
        self.assertEqual(fixture.run['manifest'], fixture.initial_manifest)

    def test_initial_manifest_failure_restores_state_and_marks_failed(self):
        fixture = ExportFixture(update_error=OSError('injected manifest failure'))
        with self.assertRaisesRegex(OSError, 'injected manifest failure'):
            fixture.export(fixture.editor, fixture.run, render_authorized=True,
                           time_s=200.25)
        fixture.assert_restored(self)
        self.assertEqual(fixture.run['manifest']['state'], 'FAILED')
        self.assertIn('injected manifest failure', fixture.run['manifest']['error'])
        self.assertEqual([m['state'] for m in fixture.update_states],
                         ['RENDERING', 'FAILED'])
        self.assertIn(('pause', dict(restore_frame=False)), fixture.events)
        self.assertIn('play', fixture.events)
        self.assertIn(('set_time', 17.875), fixture.events)
        self.assertIn('review', fixture.events)
        self.assertNotIn(('analysis', 200.25), fixture.events)

    def test_pre_image_cancellation_restores_playing_and_stopped_states(self):
        for playing in (True, False):
            with self.subTest(playing=playing):
                fixture = ExportFixture(playing=playing,
                                        analysis_error=KeyboardInterrupt())
                with self.assertRaises(KeyboardInterrupt):
                    fixture.export(fixture.editor, fixture.run,
                                   render_authorized=True, time_s=200.25)
                fixture.assert_restored(self)
                self.assertEqual(fixture.run['manifest']['state'], 'CANCELLED')
                self.assertEqual([m['state'] for m in fixture.update_states],
                                 ['RENDERING', 'CANCELLED'])
                self.assertIn(('set_time', 200.25), fixture.events)
                self.assertIn(('analysis', 200.25), fixture.events)
                self.assertEqual('play' in fixture.events, playing)
                self.assertIn('review', fixture.events)


if __name__ == '__main__':
    unittest.main()
