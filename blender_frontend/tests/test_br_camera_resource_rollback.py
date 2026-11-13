"""Camera startup rollback and retryable teardown with host-side Blender adapters."""
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wfrl_blender import cameras


@pytest.fixture
def preview_operator(frontend_module, stub_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.custom_cameras')
    released = []
    camera = object()
    scene = {}
    area = NS(type='VIEW_3D', regions=[NS(type='WINDOW')],
              spaces=NS(active=NS(lock_camera=True)), tag_redraw=Mock(), header_text_set=Mock())
    window = NS(scene=scene, screen=NS(areas=[area]))
    timer = NS(time_duration=0.)
    handle = object()
    wm = NS(wfrl_custom_slot=1, windows=[window],
            event_timer_add=Mock(return_value=timer),
            event_timer_remove=Mock(side_effect=lambda _timer: released.append('timer')),
            modal_handler_add=Mock(return_value=True))
    draw = NS(draw_handler_add=Mock(return_value=handle),
              draw_handler_remove=Mock(side_effect=lambda *_: released.append('draw')))
    snapshot = NS(restore=Mock(side_effect=lambda: released.append('restore')))
    renderer = NS(free=Mock(side_effect=lambda: released.append('preview')))
    session = NS(closed=False, original_camera=None, draft=None)

    def begin():
        session.draft = camera
        return camera

    def cancel(**_kwargs):
        released.append('draft')
        session.draft, session.closed = None, True

    session.begin, session.cancel = Mock(side_effect=begin), Mock(side_effect=cancel)
    api = NS(migrate_scene=Mock(), get_camera=Mock(return_value=camera))
    for name, value in dict(_ACTIVE=None, core=api,
            bpy=NS(types=NS(SpaceView3D=draw), context=NS(scene=None)),
            preview=NS(Preview=Mock(return_value=renderer)),
            ViewportSnapshot=Mock(return_value=snapshot), EditSession=Mock(return_value=session),
            installation_block_reason=lambda _: '', session_identity=Mock(return_value='identity'),
            sync_fields=Mock(), _redraw_if_changed=Mock(),
            history=NS(HISTORY=NS(entries=[]))).items():
        monkeypatch.setattr(panel, name, value)
    stub_module('wfrl_blender.custom_camera_capture', active=lambda: False)
    stub_module('wfrl_blender.panels.gimbal', _ACTIVE=None)
    stub_module('wfrl_blender.panels.stacked_camera_rig', _ACTIVE=None)
    op = panel.WFRL_OT_CustomCamera()
    op.mode, op.report = 'EDIT', Mock()
    context = NS(area=area, scene=scene, window=window, window_manager=wm)
    return NS(op=op, panel=panel, context=context, session=session, renderer=renderer,
              timer=timer, handle=handle, wm=wm, draw=draw, released=released)


@pytest.mark.parametrize('boundary,expected', [
    ('preview', ['restore']),
    ('draft', ['draft', 'preview', 'restore']),
    ('sync', ['draft', 'preview', 'restore']),
    ('timer', ['draft', 'preview', 'restore']),
    ('timer_clock', ['timer', 'draft', 'preview', 'restore']),
    ('draw', ['timer', 'draft', 'preview', 'restore']),
    ('modal', ['draw', 'timer', 'draft', 'preview', 'restore']),
    ('modal_false', ['draw', 'timer', 'draft', 'preview', 'restore']),
    ('redraw', ['draw', 'timer', 'draft', 'preview', 'restore']),
])
def test_preview_start_failure_rolls_back_in_reverse_order(preview_operator, boundary, expected):
    r = preview_operator
    failure = RuntimeError('injected startup failure')
    if boundary == 'timer_clock':
        class InvalidTimer:
            @property
            def time_duration(self):
                raise ReferenceError('Timer RNA removed')
        r.wm.event_timer_add.return_value = InvalidTimer()
    elif boundary == 'modal_false':
        r.wm.modal_handler_add.return_value = False
    elif boundary == 'draft':
        def fail_begin():
            r.session.draft = object()
            raise failure
        r.session.begin.side_effect = fail_begin
    else:
        target = {'preview': r.panel.preview.Preview, 'sync': r.panel.sync_fields,
                  'timer': r.wm.event_timer_add, 'draw': r.draw.draw_handler_add,
                  'modal': r.wm.modal_handler_add, 'redraw': r.panel._redraw_if_changed}[boundary]
        target.side_effect = failure
    assert r.op.invoke(r.context, None) == {'CANCELLED'}
    assert r.released == expected
    assert r.op.timer is r.op.handle is None
    assert r.op.closed and not r.op._modal_registered
    assert r.panel._ACTIVE is None and r.session.draft is None


def test_failed_replacement_does_not_resurrect_closed_operator(preview_operator):
    r = preview_operator
    previous = NS(stage='WATCH', area=r.context.area, external_snapshot=NS(restore=Mock()), closed=False)
    def finish(**_kwargs):
        previous.closed = True
        r.panel._ACTIVE = None
    previous.finish = Mock(side_effect=finish)
    r.panel._ACTIVE = previous
    r.panel.preview.Preview.side_effect = RuntimeError('allocation failed')
    assert r.op.invoke(r.context, None) == {'CANCELLED'}
    assert previous.closed and r.panel._ACTIVE is None
    previous.external_snapshot.restore.assert_called_once_with()


@pytest.mark.parametrize('resource', ['timer', 'draw'])
@pytest.mark.parametrize('startup', [False, True])
@pytest.mark.parametrize('error_type', [RuntimeError, ValueError])
def test_preview_cleanup_failure_retains_resources_for_watchdog_retry(preview_operator, resource, startup, error_type):
    r = preview_operator
    remove = r.wm.event_timer_remove if resource == 'timer' else r.draw.draw_handler_remove
    failure = error_type('resource release unavailable')
    remove.side_effect = failure
    if startup:
        r.panel._redraw_if_changed.side_effect = RuntimeError('startup failed')
        assert r.op.invoke(r.context, None) == {'CANCELLED'}
    else:
        assert r.op.invoke(r.context, None) == {'RUNNING_MODAL'}
        with pytest.raises(RuntimeError, match='resource release unavailable'):
            r.op.finish()
    assert not r.op.closed and r.panel._ACTIVE is r.op
    assert r.op.timer is r.timer
    assert r.op.handle is (r.handle if resource == 'draw' else None)
    assert r.op.session is r.session and r.session.draft is not None and not r.session.closed
    assert r.op.preview is r.renderer
    r.renderer.free.assert_not_called()
    remove.side_effect = None
    assert r.panel.lifecycle_watchdog() == .25
    assert r.op.closed and r.panel._ACTIVE is None
    assert r.op.timer is r.op.handle is None
    assert r.session.closed and r.session.draft is None
    assert remove.call_count == 2
    r.renderer.free.assert_called_once_with()
    r.op.finish()
    assert remove.call_count == 2


@pytest.mark.parametrize('resource', ['timer', 'draw'])
@pytest.mark.parametrize('failure', [ReferenceError('RNA removed'),
    RuntimeError('handler already removed'), ValueError('invalid handle')])
def test_removed_preview_handles_count_as_success(preview_operator, resource, failure):
    r = preview_operator
    assert r.op.invoke(r.context, None) == {'RUNNING_MODAL'}
    remove = r.wm.event_timer_remove if resource == 'timer' else r.draw.draw_handler_remove
    remove.side_effect = failure
    r.op.finish()
    assert r.op.closed and r.op.timer is r.op.handle is None
    assert r.panel._ACTIVE is None


def test_pending_cleanup_modal_is_inert_and_retries(preview_operator):
    r = preview_operator
    assert r.op.invoke(r.context, None) == {'RUNNING_MODAL'}
    r.draw.draw_handler_remove.side_effect = RuntimeError('busy')
    with pytest.raises(RuntimeError):
        r.op.finish()
    assert r.op.modal(r.context, NS(type='LEFTMOUSE')) == {'RUNNING_MODAL'}
    assert r.op.draw_overlay() is None
    r.draw.draw_handler_remove.side_effect = None
    assert r.op.modal(r.context, NS(type='TIMER')) == {'FINISHED'}
    assert r.op.closed and not r.op._modal_registered


@pytest.fixture(params=['clearance', 'radar'])
def closeup(monkeypatch, request):
    class Vector:
        def __init__(self, *_args): pass
        def __add__(self, _other): return self
        def __sub__(self, _other): return self
        def __mul__(self, _other): return self
        def normalized(self): return self
        def to_track_quat(self, *_args): return self
        def to_euler(self): return self

    class Camera(dict):
        pass

    data = NS(users=0, use_fake_user=False, angle_x=1.)
    camera = Camera()
    camera.users, camera.use_fake_user, camera.users_collection = 0, False, []
    camera.data, camera.matrix_parent_inverse = data, NS(identity=Mock())
    collection = NS(objects=NS())
    root = NS(users_collection=[collection])
    radar = NS(parent=root, users_collection=[collection], location=Vector())
    scene = NS(objects={'WFRL.Turbine.T1.YawRoot': root,
                        'WFRL.Turbine.T1.ClearanceRadar': radar},
               render=NS(resolution_x=1920, resolution_y=1080, pixel_aspect_x=1, pixel_aspect_y=1))

    def new(name, _data):
        camera.name = name
        data.users += 1
        return camera

    def link(obj):
        obj.users += 1
        obj.users_collection.append(collection)
        scene.objects[obj.name] = obj

    def remove(obj, **_kwargs):
        data.users -= 1
        scene.objects.pop(obj.name, None)

    collection.objects.link = Mock(side_effect=link)
    objects = NS(new=Mock(side_effect=new), remove=Mock(side_effect=remove))
    datablocks = NS(new=Mock(return_value=data), remove=Mock())
    bpy = NS(data=NS(objects=objects, cameras=datablocks, scenes=[scene]))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setitem(sys.modules, 'mathutils', NS(Vector=Vector))
    ensure = cameras.ensure_clearance_camera if request.param == 'clearance' else cameras.ensure_radar_closeup_camera
    return NS(scene=scene, camera=camera, data=data, collection=collection,
              objects=objects, datablocks=datablocks, bpy=bpy, ensure=ensure)


@pytest.mark.parametrize('boundary', ['data', 'object', 'link', 'configuration'])
def test_closeup_construction_failure_releases_only_new_ids(closeup, boundary):
    r = closeup
    primary = KeyError('closeup construction failed')
    if boundary == 'configuration':
        del r.scene.render.resolution_x
        error_type = AttributeError
    else:
        {'data': r.datablocks.new, 'object': r.objects.new,
         'link': r.collection.objects.link}[boundary].side_effect = primary
        error_type = KeyError
    with pytest.raises(error_type) as caught:
        r.ensure(r.scene, 'T1')
    if boundary != 'configuration':
        assert caught.value is primary
    assert r.objects.remove.call_count == int(boundary in {'link', 'configuration'})
    assert r.datablocks.remove.call_count == int(boundary != 'data')
    assert r.data.users == 0


@pytest.mark.parametrize('owner', ['existing', 'object_user', 'object_fake', 'foreign_scene',
                                 'shared_collection', 'data_user', 'data_fake'])
def test_closeup_rollback_preserves_existing_or_shared_ids(closeup, owner):
    r = closeup
    if owner == 'existing':
        r.ensure(r.scene, 'T1')
    elif owner == 'object_user':
        r.camera.users = 1
    elif owner == 'object_fake':
        r.camera.use_fake_user = True
    elif owner == 'foreign_scene':
        r.bpy.data.scenes.append(NS(user_of_id=lambda obj: int(obj is r.camera)))
    elif owner == 'shared_collection':
        r.bpy.data.scenes.append(NS(user_of_id=lambda _obj: 0, objects=r.scene.objects))
    elif owner == 'data_user':
        r.data.users = 1
    else:
        r.data.use_fake_user = True
    del r.scene.render.resolution_x
    with pytest.raises(AttributeError):
        r.ensure(r.scene, 'T1')
    assert r.objects.remove.call_count == int(owner in {'data_user', 'data_fake'})
    r.datablocks.remove.assert_not_called()


def test_closeup_cleanup_failure_preserves_initiating_error(closeup, caplog):
    r = closeup
    del r.scene.render.resolution_x
    r.objects.remove.side_effect = RuntimeError('remove failed')
    with pytest.raises(AttributeError, match='resolution_x'):
        r.ensure(r.scene, 'T1')
    assert 'remove failed' in caplog.text
    r.datablocks.remove.assert_not_called()
