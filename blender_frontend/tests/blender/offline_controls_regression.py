"""Native imports with recorded panel controls: offline isolation and recovery."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import bpy
from wfrl_blender import runtime
from wfrl_blender.cameras import ensure_radar_closeup_camera
from wfrl_blender.panels import clearance, status, run


class Layout:
    def __init__(self, opened=()):
        self.opened, self.labels, self.operators, self.props = set(opened), [], [], []
    def label(self, *, text='', **kwargs): self.labels.append(text)
    def row(self, **kwargs): return self
    def column(self, **kwargs): return self
    def box(self): return self
    def panel(self, name, **kwargs): return self, self if name in self.opened else None
    def operator(self, name, **kwargs):
        self.operators.append(name)
        return SimpleNamespace()
    def prop(self, owner, name, **kwargs): self.props.append(name)


def draw(connection, opened=(), error=''):
    layout = Layout(opened)
    ui = SimpleNamespace(connection=connection, run_status='READY', confirmed=True, error=error)
    with patch.object(runtime, 'get_state', return_value=ui), \
         patch.object(runtime, 'progress', return_value='loading'), \
         patch.object(runtime, 'configuration_editable', return_value=True), \
         patch.object(runtime, 'desired_mode', return_value='demo'), \
         patch.object(status, 'get_preferences', return_value=SimpleNamespace()), \
         patch.object(status, 'draw_diagnostic') as diagnostic:
        status.WFRL_PT_connection.draw(SimpleNamespace(layout=layout), SimpleNamespace())
        if error:
            assert diagnostic.call_args_list[0].args[2] == error
        assert run.WFRL_PT_WorkflowRun.poll(None) == (connection not in {'OFFLINE RESULTS', 'LOCAL DEMO'})
    return layout


# A folded offline panel still reports errors; it cannot expose backend run actions.
closed = draw('OFFLINE RESULTS', error='Transport lost')
assert '雷达离线回放 · 无需连接后端' in closed.labels
assert not closed.operators and not closed.props
opened = draw('OFFLINE RESULTS', ('wfrl_replay_backend_connection', 'wfrl_backend_environment'))
assert 'wfrl.bridge_connect' in opened.operators
assert 'wfrl.bridge_disconnect' not in opened.operators
assert 'wfrl.backend_run' not in opened.operators
assert 'backend_python' in opened.props and 'wfrl.check_environment' in opened.operators
for connection in ('CONNECTED', 'DISCONNECTED', 'LOCAL DEMO'):
    controls = draw(connection)
    assert 'wfrl.bridge_connect' in controls.operators
    assert ('wfrl.bridge_disconnect' in controls.operators) == (connection != 'LOCAL DEMO')
    assert 'wfrl.backend_run' not in controls.operators
    assert not controls.props

# Removing connection-panel run actions must preserve their backend panel home.
backend = Layout()
with patch.object(runtime, 'desired_mode', return_value='demo'), \
     patch.object(runtime, 'configuration_editable', return_value=True), \
     patch.object(runtime, 'allows_command', return_value=True), \
     patch.object(runtime, 'get_state', return_value=SimpleNamespace(run_status='READY')):
    run.WFRL_PT_WorkflowRun.draw(SimpleNamespace(layout=backend),
                              SimpleNamespace(scene=SimpleNamespace(wfrl_workflow=SimpleNamespace())))
assert backend.operators.count('wfrl.backend_run') == 6

# No loaded reader means slider writes and playback cannot start a stale clip.
scene = SimpleNamespace(frame_start=1, frame_end=451, frame_current=200, frame_set=Mock())
context = SimpleNamespace(scene=scene, screen=SimpleNamespace())
with patch.object(clearance.clearance_replay, 'reader_for', return_value=None):
    assert clearance.progress_get(scene) == 0
    clearance.progress_set(scene, 50)
    scene.frame_set.assert_not_called()
    assert not clearance.WFRL_OT_ClearancePlayback.poll(context)
with patch.object(clearance.clearance_replay, 'reader_for', return_value=object()):
    assert clearance.WFRL_OT_ClearancePlayback.poll(context)
    assert not clearance.WFRL_OT_ClearancePlayback.poll(SimpleNamespace(scene=scene, screen=None))
    scene.frame_end = scene.frame_start
    assert clearance.progress_get(scene) == 0

# At the end the button restarts; before it, it delegates play/pause exactly once.
ops = SimpleNamespace(wfrl=SimpleNamespace(clearance_restart=Mock(return_value={'FINISHED'})),
                      screen=SimpleNamespace(animation_play=Mock(return_value={'FINISHED'})))
with patch.object(clearance, 'bpy', SimpleNamespace(ops=ops)):
    assert clearance.WFRL_OT_ClearancePlayback.execute(None, context) == {'FINISHED'}
    ops.wfrl.clearance_restart.assert_called_once_with()
    ops.screen.animation_play.assert_not_called()
    scene.frame_end = 451
    assert clearance.WFRL_OT_ClearancePlayback.execute(None, context) == {'FINISHED'}
    ops.screen.animation_play.assert_called_once_with()
assert clearance.playback_control(scene, None) == ('播放', 'PLAY', '已暂停')

# Missing radar is a recoverable operator error, never a half-created camera.
initial_objects = set(bpy.context.scene.objects.keys())
try:
    ensure_radar_closeup_camera(bpy.context.scene, 'Missing')
except ValueError as exc:
    assert '雷达' in str(exc)
else:
    raise AssertionError('Missing radar must be rejected')
assert set(bpy.context.scene.objects.keys()) == initial_objects
print('OFFLINE_CONTROLS_REGRESSION_PASS')
