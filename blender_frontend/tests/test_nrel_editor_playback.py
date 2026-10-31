"""Editor transactions preserve Blender's single-player scene and window owner."""
import ast
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import time

import pytest
from wfrl_blender import playback


class Scene(dict):
    def __init__(self, name):
        super().__init__(name=name, defect_time_s=120.)
        self.name = name
        self.view_layers = [object()]
        self.wfrl_nrel_defects = NS(defect_items=[], selected=-1, turbine_id='T1')

    def as_pointer(self):
        return id(self)


@pytest.fixture
def editor_runtime(monkeypatch):
    scene, other = Scene('Editor A'), Scene('Player B')
    window = NS(scene=scene, screen=NS(is_animation_playing=False))
    foreign = NS(scene=other, screen=NS(is_animation_playing=False))
    class WindowCollection(list):
        def __contains__(self, value):
            raise TypeError('RNA collections accept names, not Window objects')

    windows = WindowCollection([window, foreign])
    context = NS(scene=scene, window=window, screen=window.screen,
                 view_layer=scene.view_layers[0], window_manager=NS(windows=windows))

    @contextmanager
    def override(**values):
        saved = {key: getattr(context, key) for key in
                 ('scene', 'window', 'screen', 'view_layer')}
        if 'window' in values:
            selected = values['window']
            values = dict(scene=selected.scene, screen=selected.screen,
                          view_layer=selected.scene.view_layers[0]) | values
        for key, value in values.items():
            setattr(context, key, value)
        try:
            yield context
        finally:
            for key, value in saved.items():
                setattr(context, key, value)

    context.temp_override = override
    calls = []

    def set_player(owner, live=True):
        playback.on_load_pre()
        if owner is not None:
            with override(window=window if owner is scene else foreign):
                playback.animation_started(owner)
        for target in windows:
            target.screen.is_animation_playing = live

    def play():
        calls.append(('play', context.scene))
        playback.animation_started(context.scene)
        for target in windows:
            target.screen.is_animation_playing = True
        return {'FINISHED'}

    def cancel(**_kwargs):
        calls.append(('cancel', context.scene))
        playback.animation_stopped(playback.owner_scene())
        for target in windows:
            target.screen.is_animation_playing = False
        return {'PASS_THROUGH'}

    bpy = NS(context=context, app=NS(background=False),
             ops=NS(screen=NS(animation_play=play, animation_cancel=cancel)))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    playback.on_load_pre()
    source = Path(__file__).parents[1] / 'wfrl_blender/nrel_defects/editor.py'
    tree = ast.parse(source.read_text())
    nodes = [node for node in tree.body
             if isinstance(node, ast.ClassDef) and node.name == 'EditorSession'
             or isinstance(node, ast.FunctionDef) and node.name == 'detach']
    scan = next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == 'NREL_OT_defect_scan')
    nodes.append(ast.ClassDef(name='Scan', bases=[], keywords=[], decorator_list=[],
                             body=[node for node in scan.body if isinstance(node, ast.FunctionDef)
                                   and node.name in {'bound_session_valid', 'finish_scan'}]))
    sessions = {}
    namespace = dict(__package__='wfrl_blender.nrel_defects', bpy=bpy, time=time,
                     _SESSIONS=sessions, sync_defect_list=lambda *_args: None,
                     new_defect=lambda *_a, **_kw: {'id': 'new'})
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(source), 'exec'), namespace)
    session = namespace['EditorSession'].__new__(namespace['EditorSession'])

    class Store:
        def retry_disposals(self, *, strict=False):
            return True

        @property
        def document(self):
            return deepcopy({'defects': [{'id': 'existing'}]})

    restored = []

    def set_time(value):
        assert context.scene is scene, 'time restoration must target the editor scene'
        restored.append(value)
        scene['defect_time_s'] = value

    session.scene = scene
    session.surface = object()
    session.store = Store()
    session.draft = session.edit_state = session.healthy_payload = session.preview = None
    session.busy = False
    session.helpers, session.helper_meshes, session.helper_rest = [], [], {}
    session.closing = False
    session.commit_times = []
    session.discard_preview = lambda: None
    session.commit_document = lambda _document: None
    session.set_time = set_time
    sessions[scene.as_pointer()] = session
    yield NS(session=session, scene=scene, other=other, window=window, foreign=foreign,
             context=context, windows=windows, bpy=bpy, calls=calls, set_player=set_player,
             restored=restored, namespace=namespace)
    playback.on_load_pre()


@pytest.mark.parametrize('owner', ['foreign', 'unknown'])
def test_edit_does_not_pause_or_replace_another_player(editor_runtime, owner):
    r = editor_runtime
    r.set_player(r.other if owner == 'foreign' else None)
    r.session.begin()
    r.session.finish(False)
    assert r.calls == []
    assert playback.any_playing()
    assert playback.owner_scene() is (r.other if owner == 'foreign' else None)


def test_cancel_restores_original_scene_window_from_foreign_context_once(editor_runtime):
    r = editor_runtime
    r.set_player(r.scene)
    r.session.begin()
    assert not playback.any_playing()
    r.scene['defect_time_s'] = 121.
    with r.context.temp_override(window=r.foreign):
        r.session.finish(False)
        r.session.finish(False)
    assert r.calls == [('cancel', r.scene), ('play', r.scene)]
    assert playback.owner_window() is r.window
    assert r.restored == [120.]
    assert r.session.edit_state is None


def test_finish_does_not_interrupt_player_started_during_edit(editor_runtime):
    r = editor_runtime
    r.set_player(r.scene)
    r.session.begin()
    r.set_player(r.other)
    r.session.finish(True)
    assert r.calls == [('cancel', r.scene)]
    assert playback.is_playing(r.other)
    assert r.restored == [120.]


@pytest.mark.parametrize('change', ['closed', 'scene_switched', 'session_replaced'])
def test_finish_never_resumes_through_stale_owner(editor_runtime, change):
    r = editor_runtime
    r.set_player(r.scene)
    r.session.begin()
    if change == 'closed':
        r.windows.remove(r.window)
    elif change == 'scene_switched':
        r.window.scene = r.other
    else:
        r.namespace['_SESSIONS'][r.scene.as_pointer()] = object()
    r.session.finish(False)
    assert r.calls == [('cancel', r.scene)]
    assert not playback.any_playing()
    assert r.session.edit_state is None


def test_failed_draft_preparation_does_not_pause_playback(editor_runtime):
    r = editor_runtime
    r.set_player(r.scene)
    def fail(*_a, **_kw):
        raise ValueError('invalid defect')
    r.namespace['new_defect'] = fail
    with pytest.raises(ValueError, match='invalid defect'):
        r.session.begin()
    assert r.calls == [] and playback.is_playing(r.scene)
    assert r.session.draft is r.session.edit_state is None


def test_failed_begin_restores_paused_owner_and_clears_transaction(editor_runtime):
    r = editor_runtime
    r.set_player(r.scene)
    def sync(_session, document, _selected):
        if len(document['defects']) > 1:
            raise RuntimeError('list failure')
    r.namespace['sync_defect_list'] = sync
    with pytest.raises(RuntimeError, match='list failure'):
        r.session.begin()
    assert r.calls == [('cancel', r.scene), ('play', r.scene)]
    assert r.session.draft is r.session.edit_state is None
    assert playback.is_playing(r.scene)


def test_failed_commit_keeps_edit_paused_until_cancel(editor_runtime):
    r = editor_runtime
    r.set_player(r.scene)
    r.session.begin()
    def fail(_document):
        raise ValueError('invalid commit')
    r.session.commit_document = fail
    with pytest.raises(ValueError, match='invalid commit'):
        r.session.finish(True)
    assert r.session.draft is not None and r.session.edit_state is not None
    assert not playback.any_playing()
    r.session.finish(False)
    assert playback.is_playing(r.scene)
    assert r.calls == [('cancel', r.scene), ('play', r.scene)]


def test_failed_time_restore_consumes_resume_state_without_starting_player(editor_runtime):
    r = editor_runtime
    r.set_player(r.scene)
    r.session.begin()
    def fail(_value):
        raise RuntimeError('time failure')
    r.session.set_time = fail
    with pytest.raises(RuntimeError, match='time failure'):
        r.session.finish(False)
    r.session.finish(False)
    assert r.session.draft is r.session.edit_state is None
    assert r.calls == [('cancel', r.scene)]


def test_detach_discards_resume_token_without_starting_any_player(editor_runtime):
    r = editor_runtime
    r.set_player(r.scene)
    r.session.begin()
    r.session.helpers = []
    r.session.geometry = NS(managers={})
    r.namespace['detach'](r.scene, restore=False)
    r.session.finish(False)
    assert r.session.draft is r.session.edit_state is None
    assert r.calls == [('cancel', r.scene)]


def test_failed_pause_does_not_start_an_edit(editor_runtime):
    r = editor_runtime
    r.set_player(r.scene)
    r.bpy.ops.screen.animation_cancel = lambda **_kwargs: {'CANCELLED'}
    with pytest.raises(RuntimeError, match='无法暂停'):
        r.session.begin()
    assert r.session.draft is r.session.edit_state is None
    assert playback.is_playing(r.scene)


@pytest.mark.parametrize('foreign_player', [False, True])
def test_scan_resume_handles_native_window_collection_and_owner(editor_runtime, monkeypatch, foreign_player):
    r = editor_runtime
    r.set_player(r.other if foreign_player else None, live=foreign_player)
    monkeypatch.setitem(sys.modules, 'wfrl_blender.nrel_defects.defect_outputs', NS(
        save_records=lambda *_args: None, write_json=lambda *_args: None,
        update_run=lambda *_args: None, save_review=lambda *_args: None))
    monkeypatch.setitem(sys.modules, 'wfrl_blender.nrel_defects.defect_timeline', NS(
        summarize_intervals=lambda *_args: []))
    scan = r.namespace['Scan']()
    scan.finished = False
    scan.scan_session, scan.scan_scene = r.session, r.scene
    scan.scene_key = r.scene.as_pointer()
    scan.timer = None
    scan.scan_window, scan.scan_wm = r.window, r.context.window_manager
    scan.scan_view_layer = r.scene.view_layers[0]
    scan.saved_time, scan.playing = 120., True
    scan.index, scan.rows = 0, []
    scan.mapping = NS(frame_count=0, to_dict=lambda: {})
    scan.run = dict(output=Path('/unused-no-writes'), manifest=dict(products={}))
    r.session.scan_operator = scan
    r.session.busy = True
    with r.context.temp_override(window=r.foreign):
        assert scan.finish_scan(r.context) == {'FINISHED'}
        assert scan.finish_scan(r.context) == {'FINISHED'}
    assert playback.owner_scene() is (r.other if foreign_player else r.scene)
    assert r.calls == ([] if foreign_player else [('play', r.scene)])
    assert not r.session.busy and r.session.scan_operator is None


def test_detach_retries_failure_after_closing_other_managers(editor_runtime):
    r = editor_runtime
    calls = []
    class Manager:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail
        def close(self, restore=True):
            calls.append(self.name)
            if self.fail:
                raise RuntimeError('injected release failure')
    failed = Manager('T1', True)
    r.session.geometry = NS(managers={'T1': failed, 'T2': Manager('T2'), 'T3': Manager('T3')})
    with pytest.raises(RuntimeError, match='injected release failure'):
        r.namespace['detach'](r.scene, restore=False)
    assert calls == ['T1', 'T2', 'T3']
    assert r.namespace['_SESSIONS'][r.scene.as_pointer()] is r.session
    assert r.session.closing
    failed.fail = False
    r.namespace['detach'](r.scene, restore=False)
    assert r.scene.as_pointer() not in r.namespace['_SESSIONS']
    r.namespace['detach'](r.scene, restore=False)


def test_helper_cleanup_keeps_failed_owned_mesh_and_ignores_deleted_object(editor_runtime):
    r = editor_runtime
    class Removed:
        def as_pointer(self):
            raise ReferenceError('Object removed')
    owned, shared, replacement = NS(users=0), NS(users=1), NS(users=0)
    helper = NS(as_pointer=lambda: 1, data=replacement)
    r.session.helpers = [Removed(), helper]
    r.session.helper_meshes = [owned, shared]
    removed = []
    def fail_mesh(_mesh):
        raise RuntimeError('mesh release failed')
    r.bpy.data = NS(objects=NS(remove=lambda obj, **_kw: removed.append(obj)),
                    meshes=NS(remove=fail_mesh))
    with pytest.raises(RuntimeError, match='mesh release failed'):
        r.session.clear_helpers()
    assert removed == [helper]
    assert r.session.helpers == []
    assert r.session.helper_meshes == [owned]
    r.bpy.data.meshes.remove = lambda mesh: removed.append(mesh)
    r.session.clear_helpers()
    assert removed == [helper, owned]
    assert r.session.helper_meshes == []
