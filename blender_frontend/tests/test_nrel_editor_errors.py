"""Editor failures preserve primary diagnostics, retry ownership and callback recovery."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from blender_frontend.tests.test_nrel_editor_playback import editor_runtime
from blender_frontend.tests.test_defect_store_cleanup import nrel_contract


def test_editor_initialization_error_survives_rollback_failure(editor_runtime, monkeypatch, caplog):
    r = editor_runtime
    r.scene.wfrl_nrel_defects.time_s = r.scene.wfrl_nrel_defects.scan_start = 120.
    primary = KeyError('initialization field missing')
    monkeypatch.setattr(r.namespace.EditorSession, '_initialize', Mock(side_effect=primary))
    monkeypatch.setattr(r.namespace.EditorSession, 'rollback_initial', Mock(side_effect=RuntimeError('rollback failed')))
    with pytest.raises(KeyError) as caught:
        r.namespace.EditorSession(r.scene)
    assert caught.value is primary
    assert r.scene.as_pointer() in r.namespace._INITIALIZING
    assert 'rollback failed' in caplog.text


@pytest.mark.parametrize('error_type', [KeyError, KeyboardInterrupt])
def test_failed_editor_commit_keeps_primary_and_candidate_for_cleanup(editor_runtime, nrel_contract, error_type):
    r = editor_runtime
    module, surface, initial, changed = nrel_contract
    store = module.DefectStore(surface, initial, prepare=lambda _doc: object())
    primary = error_type('commit failed')
    store.commit = Mock(side_effect=primary)
    store._dispose = Mock(side_effect=RuntimeError('dispose failed'))
    r.session.store = store
    with pytest.raises(error_type) as caught:
        type(r.session).commit_document(r.session, changed)
    assert caught.value is primary
    assert len(store.pending_disposals) == 1 and store.version == 0
    assert store.document == initial


def test_failed_begin_reports_both_recovery_errors_and_keeps_primary(editor_runtime, caplog):
    r = editor_runtime
    primary = KeyError('draft list field missing')
    r.namespace.sync_defect_list = Mock(side_effect=[primary, RuntimeError('list restore failed')])
    r.session.set_time = Mock(side_effect=RuntimeError('time restore failed'))
    with pytest.raises(KeyError) as caught:
        r.session.begin()
    assert caught.value is primary
    assert r.session.draft is r.session.edit_state is None
    assert 'list restore failed' in caplog.text and 'time restore failed' in caplog.text


def test_finish_list_error_survives_time_restore_failure(editor_runtime, caplog):
    r = editor_runtime
    r.session.begin()
    primary = KeyError('committed list field missing')
    r.namespace.sync_defect_list = Mock(side_effect=primary)
    r.session.set_time = Mock(side_effect=RuntimeError('time restore failed'))
    with pytest.raises(KeyError) as caught:
        r.session.finish(False)
    assert caught.value is primary
    assert r.session.draft is r.session.edit_state is None
    assert 'time restore failed' in caplog.text


def test_healthy_comparison_error_survives_disposal_failure(editor_runtime, caplog):
    r = editor_runtime
    r.session.store = NS(document={'defects': [dict(turbine_id='T1', blade_id=1, enabled=True)]})
    primary = TypeError('invalid comparison geometry')
    payload = object()
    r.session.geometry = NS(prepare=Mock(return_value=payload), activate=Mock(side_effect=primary),
                            dispose=Mock(side_effect=RuntimeError('comparison release failed')))
    with pytest.raises(TypeError) as caught:
        r.session.healthy(0)
    assert caught.value is primary and r.session.healthy_payload is None
    r.session.geometry.dispose.assert_called_once_with(payload)
    assert 'comparison release failed' in caplog.text


def test_geometry_preparation_error_survives_cleanup_failure(editor_runtime, caplog):
    geometry = editor_runtime.namespace.GeometrySet.__new__(editor_runtime.namespace.GeometrySet)
    primary = KeyError('geometry payload field missing')
    geometry.managers = {'T1': NS(prepare=Mock(side_effect=primary))}
    geometry.pending = []
    geometry.dispose = Mock(side_effect=RuntimeError('geometry release failed'))
    with pytest.raises(KeyError) as caught:
        geometry.prepare({'defects': []})
    assert caught.value is primary
    assert len(geometry.pending) == 1
    geometry.dispose.assert_called_once_with(geometry.pending[0])
    assert 'geometry release failed' in caplog.text


def test_geometry_activation_error_survives_rollback_failure(editor_runtime, caplog):
    geometry = editor_runtime.namespace.GeometrySet.__new__(editor_runtime.namespace.GeometrySet)
    primary = KeyError('activation field missing')
    geometry.active = {'turbines': {'T1': 'old'}}
    activate = Mock(side_effect=[primary, RuntimeError('geometry rollback failed')])
    geometry.managers = {'T1': NS(activate=activate)}
    with pytest.raises(KeyError) as caught:
        geometry.activate({'turbines': {'T1': 'new'}})
    assert caught.value is primary
    assert geometry.active == {'turbines': {'T1': 'old'}}
    assert activate.call_count == 2
    assert 'geometry rollback failed' in caplog.text


def test_original_disposal_failure_is_logged_and_retryable(editor_runtime, caplog):
    geometry = editor_runtime.namespace.GeometrySet.__new__(editor_runtime.namespace.GeometrySet)
    original = object()
    geometry.original_payloads = {'T1': original}
    dispose = Mock(side_effect=RuntimeError('original release failed'))
    geometry.managers = {'T1': NS(dispose=dispose)}
    geometry.retry_originals()
    assert geometry.original_payloads == {'T1': original}
    assert 'original release failed' in caplog.text
    dispose.side_effect = None
    geometry.retry_originals(strict=True)
    assert not geometry.original_payloads


def test_original_disposal_programming_error_propagates(editor_runtime):
    geometry = editor_runtime.namespace.GeometrySet.__new__(editor_runtime.namespace.GeometrySet)
    geometry.original_payloads = {'T1': object()}
    geometry.managers = {'T1': NS(dispose=Mock(side_effect=TypeError('invalid payload')))}
    with pytest.raises(TypeError, match='invalid payload'):
        geometry.retry_originals()


@pytest.mark.parametrize('resource', ['object', 'mesh'])
def test_helper_cleanup_programming_error_propagates(editor_runtime, resource):
    r = editor_runtime
    helper = NS(as_pointer=lambda: 1)
    r.session.helpers = [helper] if resource == 'object' else []
    r.session.helper_meshes = [NS(users=0)] if resource == 'mesh' else []
    primary = TypeError('invalid resource collection')
    r.bpy.data = NS(objects=NS(remove=Mock(side_effect=primary)),
                    meshes=NS(remove=Mock(side_effect=primary)))
    with pytest.raises(TypeError) as caught:
        r.session.clear_helpers()
    assert caught.value is primary


def test_save_preparation_error_survives_recovery_failure(editor_runtime, caplog):
    r = editor_runtime
    primary = KeyError('save payload field missing')
    r.session.suspend_for_save = Mock(side_effect=primary)
    r.namespace.restore_after_save = Mock(side_effect=RuntimeError('save recovery failed'))
    with pytest.raises(KeyError) as caught:
        r.namespace.save_pre(None)
    assert caught.value is primary
    assert 'save recovery failed' in caplog.text


def test_save_recovery_continues_past_removed_scene_and_keeps_primary(editor_runtime, caplog):
    r = editor_runtime
    class RemovedScene:
        @property
        def wfrl_nrel_defects(self):
            raise ReferenceError('scene removed')
    primary = KeyError('resume payload field missing')
    failed = NS(scene=RemovedScene(), resume_after_save=Mock(side_effect=primary))
    healthy = NS(resume_after_save=Mock())
    r.namespace._SESSIONS = {1: failed, 2: healthy}
    with pytest.raises(KeyError) as caught:
        r.namespace.restore_after_save(saved=True)
    assert caught.value is primary
    healthy.resume_after_save.assert_called_once_with(True)
    assert 'scene removed' in caplog.text


def test_corrupt_saved_document_is_rejected_even_if_status_rna_is_gone(editor_runtime, caplog):
    r = editor_runtime
    class RemovedProps:
        @property
        def status(self):
            return ''
        @status.setter
        def status(self, value):
            raise ReferenceError('status removed')
    r.scene.wfrl_nrel_defects = RemovedProps()
    r.scene['wfrl_nrel_defect_enabled'] = True
    r.scene[r.namespace.DOCUMENT_KEY] = '{broken'
    r.namespace._SESSIONS.clear()
    with pytest.raises(json.JSONDecodeError):
        r.namespace.restore_saved(r.scene)
    assert r.scene['defect_analysis_status'].startswith('RESTORE_FAILED:')
    assert not r.namespace._SESSIONS
    assert 'status removed' in caplog.text


@pytest.fixture
def scan_runtime(editor_runtime, stub_module):
    r = editor_runtime
    outputs = stub_module('wfrl_blender.nrel_defects.defect_outputs',
        save_records=Mock(), write_json=Mock(), update_run=Mock(), save_review=Mock())
    stub_module('wfrl_blender.nrel_defects.defect_timeline', summarize_intervals=lambda *_args: [])
    scan = r.namespace.NREL_OT_defect_scan()
    scan.finished = False
    scan.scan_session, scan.scan_scene = r.session, r.scene
    scan.scene_key = r.scene.as_pointer()
    scan.timer = None
    scan.scan_window, scan.scan_wm = r.window, r.context.window_manager
    scan.scan_view_layer = r.scene.view_layers[0]
    scan.saved_time, scan.playing = 120., False
    scan.index, scan.rows = 0, []
    scan.mapping = NS(frame_count=0, to_dict=lambda: {})
    scan.run = dict(output=Path('/unused-no-writes'), manifest=dict(products={}))
    scan.report = Mock()
    r.session.scan_operator = scan
    r.session.busy = True
    return r, scan, outputs


def test_scan_output_failure_and_failed_manifest_are_both_reported(scan_runtime, caplog):
    r, scan, outputs = scan_runtime
    outputs.save_records.side_effect = TypeError('invalid scan rows')
    outputs.update_run.side_effect = OSError('manifest disk failure')
    assert scan.finish_scan(r.context) == {'CANCELLED'}
    assert scan.run['manifest']['state'] == 'FAILED'
    assert 'invalid scan rows' in scan.run['manifest']['error']
    assert 'invalid scan rows' in scan.report.call_args.args[1]
    assert 'manifest disk failure' in caplog.text
    assert not r.session.busy and r.session.scan_operator is None


def test_scan_timer_cleanup_failure_is_reported(scan_runtime, caplog):
    r, scan, _outputs = scan_runtime
    scan.timer = object()
    scan.scan_wm.event_timer_remove = Mock(side_effect=RuntimeError('timer cleanup failed'))
    assert scan.finish_scan(r.context) == {'FINISHED'}
    assert 'timer cleanup failed' in caplog.text


def test_action_failure_survives_viewport_cleanup_failure(editor_runtime, caplog):
    r = editor_runtime
    r.session.analyze = Mock(side_effect=KeyError('analysis field missing'))
    r.namespace.redraw_editor = Mock(side_effect=ReferenceError('viewport removed'))
    action = r.namespace.NREL_OT_defect_action()
    action.action, action.report = 'ANALYZE', Mock()
    assert action.execute(r.context) == {'CANCELLED'}
    assert 'analysis field missing' in r.scene.wfrl_nrel_defects.status
    assert 'viewport removed' in caplog.text
