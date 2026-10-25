"""Down export timing, decoded media and exclusive pair publication."""
import ast
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import subprocess
import tempfile
import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'wfrl_blender/manual_down.py'


def helpers():
    names = {'pose_at', '_output_paths', '_publish_pair', '_verify_video', '_plan', '_frame_handler'}
    tree = ast.parse(SOURCE.read_text())
    namespace = dict(Path=Path, Fraction=Fraction, json=json, math=math, os=os,
                     shutil=shutil, tempfile=tempfile, subprocess=subprocess)
    from wfrl_blender.manual_motion import pitch_at
    namespace['pitch_at'] = pitch_at
    exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                 and node.name in names], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


def test_pose_clamps_pitch_and_wraps_rotor():
    function = helpers()['pose_at']
    item = dict(azimuth_deg=359, rpm=10, pitch_start_deg=[0, 20, -3],
                pitch_target_deg=[10, -10, -3], pitch_rate_deg_s=5)
    assert function(item, 1) == (59, [5, 15, -3])
    assert function(item, 100) == (239, [10, -10, -3])


def test_pair_preflight_rejects_either_destination_and_dangling_links(tmp_path):
    function = helpers()['_output_paths']
    target = tmp_path / 'down.mp4'
    assert function(str(target)) == (target, target.with_suffix('.json'))
    for path in (target, target.with_suffix('.json')):
        path.symlink_to(tmp_path / 'missing')
        with pytest.raises(FileExistsError):
            function(str(target))
        path.unlink()


def test_pair_publication_preserves_collisions_and_retains_master(tmp_path, monkeypatch):
    function = helpers()['_publish_pair']
    master = tmp_path / 'encoded.mp4'
    master.write_bytes(b'encoded master')
    target = tmp_path / 'final.mp4'
    sidecar = target.with_suffix('.json')
    sidecar.write_text('existing')
    with pytest.raises(FileExistsError):
        function(master, target, {'samples': [1]})
    assert sidecar.read_text() == 'existing'
    assert not target.exists()
    sidecar.unlink()
    original = os.link
    def race(source, destination, **kwargs):
        if Path(destination) == target:
            target.write_bytes(b'racing owner')
        return original(source, destination, **kwargs)
    monkeypatch.setattr(os, 'link', race)
    with pytest.raises(FileExistsError):
        function(master, target, {'samples': [1]})
    assert target.read_bytes() == b'racing owner'
    assert not sidecar.exists()
    assert master.read_bytes() == b'encoded master'


def test_pair_publication_writes_complete_bytes(tmp_path):
    function = helpers()['_publish_pair']
    master, target = tmp_path / 'encoded.mp4', tmp_path / 'final.mp4'
    master.write_bytes(b'encoded master')
    function(master, target, {'samples': [1, 2]})
    assert target.read_bytes() == master.read_bytes()
    assert json.loads(target.with_suffix('.json').read_text()) == {'samples': [1, 2]}


def test_verification_requires_actual_count_size_fps_and_full_decode(monkeypatch):
    function = helpers()['_verify_video']
    result = dict(streams=[dict(codec_name='h264', width=64, height=36, avg_frame_rate='25/1')],
                  frames=[dict(best_effort_timestamp_time='0.0'), dict(best_effort_timestamp_time='0.04')])
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=json.dumps(result) if command[0] == 'probe' else '', returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    assert function(Path('/tmp/master.mp4'), 2, 25, (64, 36), 'probe', 'codec')['frame_count'] == 2
    assert commands[-1][0] == 'codec' and '-xerror' in commands[-1]
    for field, value in [('width', 62), ('height', 34), ('avg_frame_rate', '24/1')]:
        old = result['streams'][0][field]
        result['streams'][0][field] = value
        with pytest.raises(ValueError):
            function(Path('/tmp/master.mp4'), 2, 25, (64, 36), 'probe', 'codec')
        result['streams'][0][field] = old
    result['frames'].pop()
    with pytest.raises(ValueError):
        function(Path('/tmp/master.mp4'), 2, 25, (64, 36), 'probe', 'codec')


def test_plan_animates_only_selected_turbine_from_actual_snapshot():
    namespace = helpers()
    entries = [SimpleNamespace(turbine_id=tid, rpm=10, pitch_start_b1_deg=0,
                pitch_start_b2_deg=0, pitch_start_b3_deg=0) for tid in ('T1', 'T2', 'T3')]
    namespace['manual_control'] = SimpleNamespace(target_pitch=lambda entry, index: 20)
    scene = SimpleNamespace(wfrl_manual_pitch_rate_deg_s=5, wfrl_manual_turbines=entries)
    poses = {tid: dict(azimuth_deg=30, individual_pitch=True, pitch_b1_deg=2,
             pitch_b2_deg=3, pitch_b3_deg=4) for tid in ('T1', 'T2', 'T3')}
    plan = namespace['_plan'](scene, 25, 2, True, 'T2', poses)
    for item in plan['turbines']:
        if item['turbine'] == 'T2':
            assert item['rpm'] == 10 and item['pitch_start_deg'] == [0, 0, 0]
        else:
            assert item['rpm'] == 0 and item['pitch_start_deg'] == item['pitch_target_deg'] == [2, 3, 4]


def test_plan_current_pose_start_and_full_decode_failure(monkeypatch):
    namespace = helpers()
    entry = SimpleNamespace(turbine_id='T1', rpm=8)
    namespace['manual_control'] = SimpleNamespace(target_pitch=lambda entry, index: 30)
    scene = SimpleNamespace(wfrl_manual_pitch_rate_deg_s=4, wfrl_manual_turbines=[entry])
    pose = dict(azimuth_deg=123, individual_pitch=False, pitch_deg=7)
    plan = namespace['_plan'](scene, 30, 2, False, 'T1', {'T1': pose})
    assert plan['turbines'][0]['pitch_start_deg'] == [7,7,7]
    assert plan['turbines'][0]['pitch_target_deg'] == [30,30,30]
    result = dict(streams=[dict(codec_name='h264', width=64, height=36, avg_frame_rate='25/1')],
                  frames=[dict(best_effort_timestamp_time='0.0')])
    def fail_decode(command, **kwargs):
        if command[0] == 'codec':
            raise subprocess.CalledProcessError(1, command, stderr='corrupt frame')
        return SimpleNamespace(stdout=json.dumps(result))
    monkeypatch.setattr(subprocess, 'run', fail_decode)
    with pytest.raises(subprocess.CalledProcessError):
        namespace['_verify_video'](Path('/tmp/corrupt.mp4'), 1, 25, (64,36), 'probe', 'codec')


@pytest.mark.parametrize('state', [{'cancelled': True}, {'failure': 'native render failed'}])
def test_cancelled_or_failed_frame_handler_avoids_removed_control_rna(state):
    namespace = helpers()
    scene = SimpleNamespace(as_pointer=lambda: 7)
    namespace['_EXPORT'] = {7: state}
    def forbidden(*args, **kwargs):
        raise AssertionError('Terminal job accessed pose controls')
    namespace['apply_frame'] = forbidden
    namespace['_frame_handler'](scene)
