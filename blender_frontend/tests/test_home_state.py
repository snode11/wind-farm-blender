"""User-facing readiness and backend confirmation at the shared entry point."""
from types import SimpleNamespace
import sys

import pytest

import wfrl_blender
from wfrl_blender.home_state import backend_status, scene_status


def test_empty_and_restored_markers_do_not_claim_ready():
    assert scene_status().title == '未加载 WFRL 场景'
    assert scene_status(kind='clearance_replay').title == '回放未就绪'
    ui = SimpleNamespace(connection='LOCAL DEMO', run_status='READY', confirmed=True)
    assert backend_status(ui) == '后端：未连接 · 离线回放无需后端'


def test_ready_playing_failure_and_busy_are_distinct():
    assert scene_status(ready=True).title == '离线回放 · 就绪'
    assert scene_status(ready=True, playing=True).title == '离线回放 · 播放中'
    assert scene_status(load_error='missing').title == '加载失败 · 回放未就绪'
    assert scene_status(ready=True, load_error='missing').title == '加载失败 · 当前回放仍可用'
    assert scene_status(ready=True, playing=True, busy='相机采集中').title == '相机采集中'


def test_manual_motion_does_not_claim_simulation_or_backend_running():
    assert scene_status(kind='manual', ready=True).title == '手动场景 · 已暂停'
    assert scene_status(kind='manual', manual_running=True).title == '手动刚性运动中'
    state = scene_status(kind='manual', manual_running=True, manual_error='旋转冲突')
    assert state.title == '手动控制已保护暂停'
    assert state.icon == 'ERROR'


def test_backend_requires_connection_and_confirmation():
    ui = SimpleNamespace(connection='DISCONNECTED', run_status='RUNNING', confirmed=False)
    assert backend_status(ui) == '后端：未连接 · 运行状态未确认'
    ui.connection = 'CONNECTED'
    assert backend_status(ui) == '后端：已连接 · 运行状态未确认'
    ui.confirmed = True
    assert backend_status(ui) == '后端：已连接 · 运行中'


def test_load_failure_survives_for_display_and_success_clears_it(monkeypatch):
    scene = {}
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(context=SimpleNamespace(scene=scene)))

    def failure(*_args, **_kwargs):
        raise ValueError('数据路径不可读')

    monkeypatch.setattr(wfrl_blender, '_load_demo_scene', failure)
    with pytest.raises(ValueError, match='数据路径不可读'):
        wfrl_blender.load_demo_scene('bad')
    assert scene['wfrl_home_load_error'] == '数据路径不可读'
    loaded = object()
    monkeypatch.setattr(wfrl_blender, '_load_demo_scene', lambda *_args, **_kwargs: loaded)
    assert wfrl_blender.load_demo_scene('valid') is loaded
    assert 'wfrl_home_load_error' not in scene
