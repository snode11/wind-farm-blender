"""Protection feedback remains latched without advancing manual motion."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from wfrl_blender import manual_pose
from wfrl_blender.manual_motion import PitchClock, RotorClock


SOURCE = Path(__file__).resolve().parents[1] / 'wfrl_blender/manual_control.py'


class Object:
    def __init__(self, name):
        self.name = name
        self.parent = None
        self.constraints = []
        self.animation_data = None
        self.type = 'EMPTY'
        self.rotation_mode = 'XYZ'
        self.rotation_euler = [0.0, 0.0, 0.0]


class Scene(dict):
    def __init__(self):
        super().__init__(wfrl_scene_kind='manual')
        self.wfrl_manual_error = self.wfrl_manual_error_turbine = self.wfrl_manual_recovery = ''
        self.wfrl_manual_status = 'IDLE'
        self.wfrl_manual_trial = SimpleNamespace(engaged=False)
        self.wfrl_manual_pitch_rate_deg_s = 2.0
        self.wfrl_manual_turbines = []

    def as_pointer(self):
        return id(self)


@pytest.fixture
def control():
    tree = ast.parse(SOURCE.read_text())
    names = {'is_manual', '_scene_key', '_entry_key', '_pause_status', '_record_error',
             '_protect_scene', 'recheck_feedback', 'trial_active', 'target_pitches',
             'effective_pitches', 'pitch_ramping', '_validate_native_controls',
             '_validate_controlled_rotation', 'pause_scene', '_tick_scene'}
    namespace = dict(math=math, time=time, _CLOCKS={}, _PITCH_CLOCKS={}, _TRIAL_CLOCKS={},
                     _updating=False, _POSE_FIELDS=manual_pose.POSE_FIELDS,
                     _POINTER_FIELDS=('yaw_object', 'rotor_object', 'blade1_object',
                                      'blade2_object', 'blade3_object'))
    exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                 and node.name in names], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    namespace['validate_scene'] = lambda scene: (True, '')
    namespace['_redraw'] = lambda scene: None
    return namespace


def turbine(scene, turbine_id='T1'):
    entry = SimpleNamespace(turbine_id=turbine_id, id_data=scene, running=True, rpm=9.0,
                            yaw_deg=0.0, azimuth_deg=0.0, pitch_deg=10.0,
                            individual_pitch=False, pitch_state_ready=True, pitch_paused=False)
    for field in ('yaw_object', 'rotor_object', 'blade1_object', 'blade2_object', 'blade3_object'):
        setattr(entry, field, Object(turbine_id + field))
    for index in range(1, 4):
        setattr(entry, f'pitch_actual_b{index}_deg', 0.0)
    scene.wfrl_manual_turbines.append(entry)
    return entry


@pytest.mark.parametrize('conflict', ['rotation', 'constraint'])
def test_running_conflict_latches_reason_and_preserves_pose_until_recheck(control, conflict):
    scene = Scene()
    entry = turbine(scene)
    key = (scene.as_pointer(), 'T1')
    control['_CLOCKS'][key] = RotorClock(0, 9, True, 100)
    control['_PITCH_CLOCKS'][key] = PitchClock((0, 0, 0), (10, 10, 10), 2, True, 100)
    if conflict == 'rotation':
        entry.rotor_object.rotation_euler[0] = .1
    else:
        entry.rotor_object.constraints.append('COPY_ROTATION')
    before = tuple(entry.rotor_object.rotation_euler)
    assert not control['_tick_scene'](scene, 105, {scene.as_pointer()}, set())
    assert not entry.running and entry.pitch_paused
    assert scene.wfrl_manual_status == 'ERROR'
    assert scene.wfrl_manual_error_turbine == 'T1'
    assert ('旋转' if conflict == 'rotation' else '约束') in scene.wfrl_manual_error
    assert scene.wfrl_manual_recovery
    assert tuple(entry.rotor_object.rotation_euler) == before
    assert entry.azimuth_deg == 0 and control['effective_pitches'](entry) == (0, 0, 0)
    assert not control['_CLOCKS'] and not control['_PITCH_CLOCKS']
    error = scene.wfrl_manual_error
    assert not control['recheck_feedback'](scene)[0]
    assert scene.wfrl_manual_error == error
    entry.rotor_object.rotation_euler[0] = 0
    entry.rotor_object.constraints.clear()
    control['_tick_scene'](scene, 110, {scene.as_pointer()}, set())
    assert scene.wfrl_manual_error == error and not entry.running
    assert control['recheck_feedback'](scene) == (True, '')
    assert not scene.wfrl_manual_error and scene.wfrl_manual_status == 'READY'
    assert not entry.running and entry.pitch_paused


def test_normal_pause_reasons_do_not_create_or_clear_protection_errors(control):
    scene = Scene()
    entry = turbine(scene)
    for reason in ('USER', 'HIDDEN', 'RESTORED'):
        control['pause_scene'](scene, now=100, apply=False, reason=reason)
        assert scene.wfrl_manual_status == reason
        assert not scene.wfrl_manual_error and not entry.running
    control['_protect_scene'](scene, 'T1 叶轮的原生旋转不一致', now=100)
    control['pause_scene'](scene, now=101, apply=False, reason='USER')
    assert scene.wfrl_manual_status == 'ERROR' and scene.wfrl_manual_error


def test_invalid_entry_rna_pauses_remaining_entries_without_touching_other_scene_clocks(control):
    class RemovedEntry:
        def __getattribute__(self, name):
            raise ReferenceError('StructRNA has been removed')

        def __setattr__(self, name, value):
            raise ReferenceError('StructRNA has been removed')

    scene, other = Scene(), Scene()
    scene.wfrl_manual_turbines.append(RemovedEntry())
    entry = turbine(scene, 'T2')
    other_clock = RotorClock(30, 8, True, 100)
    control['_CLOCKS'][(other.as_pointer(), 'T1')] = other_clock
    control['_CLOCKS'][(scene.as_pointer(), 'T2')] = RotorClock(0, 9, True, 100)
    control['validate_scene'] = lambda scene: (False, 'T1 控制对象已失效，请重新创建场景')
    assert not control['_tick_scene'](scene, 105, {scene.as_pointer()}, set())
    assert not entry.running and entry.pitch_paused
    assert scene.wfrl_manual_error_turbine == 'T1'
    assert '重新创建' in scene.wfrl_manual_recovery
    assert control['_CLOCKS'] == {(other.as_pointer(), 'T1'): other_clock}
