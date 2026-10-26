"""Exercise the production modal/overlay code with counted host-side adapters."""
import ast
from contextlib import nullcontext
from dataclasses import replace
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import pytest

from wfrl_blender import camera_projection as projection, custom_cameras as core


SOURCE = Path(__file__).parents[1] / 'wfrl_blender/panels/custom_cameras.py'


class Camera(dict):
    name = 'camera'
    matrix_world = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))

    def as_pointer(self):
        return id(self)


class Preview:
    def __init__(self):
        self.version, self.calls = 0, 0
        self.error, self.images, self.request = '', {}, None
        self.stale, self.fail, self.closed = False, False, False

    def status(self):
        return self.error or ('waiting' if self.stale else 'ready')

    def refresh(self, context, cameras):
        self.calls += 1
        if self.fail:
            raise RuntimeError('GPU unavailable')
        self.error = ''
        self.request = dict(time_s=context.scene.time_s,
                            frame=context.scene.frame_current,
                            subframe=context.scene.frame_subframe)
        if not self.images:
            self.images[1] = object()
            self.version += 1

    def free(self):
        self.images.clear()
        self.closed = True


@pytest.fixture
def rig(monkeypatch):
    tree = ast.parse(SOURCE.read_text())
    functions = {'_timer_advanced', '_display_state', '_redraw_if_changed',
                 'fields_changed', 'distance_changed'}
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in functions]
    operator = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                    and n.name == 'WFRL_OT_CustomCamera')
    operator.bases = []
    operator.body = [n for n in operator.body if not isinstance(n, ast.AnnAssign)]
    body.append(operator)
    state = NS(now=0., redraws=0, writes=0, pauses=0, valid=True, capture=False,
               restored=0, removed_timers=[], events=[], picked=None)
    camera = Camera(params=core.CameraParameters((1., 2., 3.)), custom_label='C1')
    camera.anchor = core.SurfaceAnchor((1., 2., 2.9), (0., 0., 1.), .1)
    scene = NS(objects={'camera': camera}, frame_current=1, frame_subframe=0., time_s=None)
    region = NS(type='WINDOW', x=0, y=0, width=1000, height=800)
    sidebar = NS(type='UI', x=800, y=0, width=200, height=800)
    area = NS(type='VIEW_3D', regions=[region, sidebar],
              spaces=NS(active=NS(lock_camera=False)))
    area.tag_redraw = lambda: setattr(state, 'redraws', state.redraws + 1)
    area.header_text_set = lambda _text: None
    window = NS(screen=NS(areas=[area], is_animation_playing=False), scene=scene)
    wm = NS(windows=[window], wfrl_custom_engineering=False, wfrl_custom_show_axis=True,
            wfrl_custom_show_frustum=True, wfrl_custom_linked_zoom=False,
            wfrl_custom_distance=.1, wfrl_custom_mount_mode='SURFACE', wfrl_custom_label='C1')
    wm.event_timer_remove = state.removed_timers.append
    system = NS(ui_scale=1., use_region_overlap=True)
    context = NS(window=window, scene=scene, preferences=NS(system=system),
                 temp_override=lambda **_kwargs: nullcontext())
    bpy = NS(context=context, types=NS(SpaceView3D=NS(draw_handler_remove=lambda *_: None)))
    capture = ModuleType('wfrl_blender.custom_camera_capture')
    capture.active = lambda: state.capture
    monkeypatch.setitem(sys.modules, capture.__name__, capture)
    import wfrl_blender
    monkeypatch.setattr(wfrl_blender, 'custom_camera_capture', capture, raising=False)

    def apply(cam, params, **_kwargs):
        state.writes += 1
        cam['params'] = params

    api = NS(ROOT_NAME=core.ROOT_NAME, SLOTS=core.SLOTS,
             parameters=lambda cam: cam['params'], anchor=lambda cam: cam.anchor,
             get_camera=lambda _scene, slot: camera if slot == 1 else None,
             screen_direction_delta=core.screen_direction_delta, apply_parameters=apply,
             set_distance=lambda *_args: None)
    preview = NS(action_buttons=lambda *_: [], contains=lambda *_: False,
                 available_rectangle=lambda *_: (0, 0, 800, 800), close_button=lambda *_: ())
    namespace = dict(__package__='wfrl_blender.panels', core=api, preview=preview,
                     projection=projection, bpy=bpy, math=math, replace=replace,
                     time=NS(monotonic=lambda: state.now), _VIEWING={'WATCH', 'LAYOUT'},
                     _SYNCING=False, session_identity=lambda _scene: state.valid,
                     sync_fields=lambda *_args: None,
                     diagnostics=NS(record=lambda *args, **kwargs: state.events.append((args, kwargs))))
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    op = namespace['WFRL_OT_CustomCamera']()
    op.__dict__.update(scene=scene, area=area, region=region, window=window, wm=wm,
                       camera=camera, slot=1, stage='WATCH', closed=False, ready=True, error='',
                       drag=None, stick=(0, 0), hover=None, candidates=[], press=None,
                       preview=Preview(), identity=True, last_time=0., _timer_duration=0.,
                       timer=NS(time_duration=0.), handle=object(), session=None)
    op.external_snapshot = NS(restore=lambda: setattr(state, 'restored', state.restored + 1))
    op.pick = lambda *_: state.picked
    namespace['_ACTIVE'] = op

    def event(kind, value='PRESS', **kwargs):
        return NS(type=kind, value=value, mouse_x=100, mouse_y=100,
                  shift=False, alt=False, ctrl=False, **kwargs)

    def tick(dt=.025, advance=True, **kwargs):
        state.now += dt
        if advance:
            op.timer.time_duration += dt
        return op.modal(context, event('TIMER', **kwargs))

    def edit(stage='AIM'):
        op.stage = stage
        op.session = NS(closed=False, frame=1, subframe=0., valid=lambda: True,
                        _play=lambda _playing: setattr(state, 'pauses', state.pauses + 1))
        op.session.cancel = lambda **_kwargs: setattr(op.session, 'closed', True)

    return NS(op=op, state=state, context=context, namespace=namespace,
              tick=tick, event=event, edit=edit, camera=camera, api=api)


@pytest.mark.parametrize('stage', ['WATCH', 'AIM', 'PLACE', 'INPUT', 'LAYOUT'])
def test_idle_ticks_keep_watchdog_without_redundant_redraw(rig, stage):
    op = rig.op
    if stage not in {'WATCH', 'LAYOUT'}:
        rig.edit(stage)
    else:
        op.stage = stage
    rig.tick()
    first = rig.state.redraws
    for _ in range(200):
        assert rig.tick() == {'PASS_THROUGH'}
    assert first == 1 and rig.state.redraws == first
    assert not op.closed
    assert rig.state.pauses == (201 if stage not in {'WATCH', 'LAYOUT'} else 0)
    assert op.preview.calls == (201 if stage in {'AIM', 'WATCH'} else 0)


def test_other_timer_events_cannot_multiply_preview_or_joystick_ticks(rig):
    rig.edit()
    op = rig.op
    op.drag, op.stick = 'stick', (.5, .25)
    rig.tick()
    before = (op.preview.calls, rig.state.writes, rig.state.pauses, op.last_time)
    for _ in range(100):
        rig.tick(dt=.001, advance=False)
    assert (op.preview.calls, rig.state.writes, rig.state.pauses, op.last_time) == before
    rig.tick(timer=object())
    assert op.preview.calls == before[0]
    rig.tick()
    assert op.preview.calls == before[0] + 1
    assert rig.state.writes == before[1] + 1


@pytest.mark.parametrize('change', [
    lambda r: setattr(r.op, 'error', 'invalid position'),
    lambda r: setattr(r.op.preview, 'error', 'GPU allocation failed'),
    lambda r: setattr(r.op.preview, 'version', 10),
    lambda r: setattr(r.op.preview, 'stale', True),
    lambda r: setattr(r.op.scene, 'frame_current', 2),
    lambda r: setattr(r.op.scene, 'frame_subframe', .5),
    lambda r: setattr(r.op.scene, 'time_s', 2.5),
    lambda r: setattr(r.op.window.screen, 'is_animation_playing', True),
    lambda r: setattr(r.op.region, 'width', 1200),
    lambda r: setattr(r.op.area.regions[1], 'width', 250),
    lambda r: setattr(r.context.preferences.system, 'ui_scale', 1.5),
    lambda r: setattr(r.op.wm, 'wfrl_custom_engineering', True),
    lambda r: setattr(r.op.wm, 'wfrl_custom_show_axis', False),
    lambda r: r.camera.update(custom_label='renamed'),
    lambda r: r.camera.update(params=replace(r.camera['params'], fov=90)),
])
def test_visible_changes_redraw_once(rig, change):
    rig.tick()
    change(rig)
    assert rig.namespace['_redraw_if_changed'](rig.op) or rig.tick() == {'PASS_THROUGH'}
    assert rig.state.redraws == 2
    assert not rig.namespace['_redraw_if_changed'](rig.op)


def test_clock_change_is_detected_after_preview_observation(rig):
    rig.tick()
    rig.op.scene.time_s = .75
    rig.tick()
    assert rig.state.redraws == 2
    rig.tick()
    assert rig.state.redraws == 2


def test_error_retries_do_not_repeat_redraw_and_recovery_updates(rig):
    rig.tick()
    rig.op.preview.fail = True
    rig.tick()
    assert rig.state.redraws == 2 and rig.op.preview.error == 'GPU unavailable'
    for _ in range(20):
        rig.tick()
    assert rig.state.redraws == 2
    rig.op.preview.fail = False
    rig.tick()
    assert rig.state.redraws == 3 and not rig.op.preview.error


def test_drag_feedback_and_release_are_immediate_without_timer(rig):
    rig.edit()
    op = rig.op
    rig.tick()
    cx, cy, radius = op.geometry()
    event = rig.event('LEFTMOUSE')
    event.mouse_x, event.mouse_y = cx + radius / 2, cy
    op.modal(rig.context, event)
    assert op.drag == 'stick' and rig.state.redraws == 2
    before = op.camera['params']
    rig.tick(dt=.05)
    expected = core.screen_direction_delta(before.yaw, before.pitch, before.roll, .5 * .05 * 45, 0)
    assert (op.camera['params'].yaw, op.camera['params'].pitch) == pytest.approx(expected)
    count = rig.state.redraws
    op.modal(rig.context, rig.event('LEFTMOUSE', 'RELEASE'))
    assert op.stick == (0, 0) and op.drag is None and rig.state.redraws == count + 1
    writes = rig.state.writes
    rig.tick()
    assert rig.state.writes == writes


def test_zero_stick_and_unlinked_wheel_do_not_write_parameters(rig):
    rig.edit()
    rig.op.drag = 'stick'
    for _ in range(10):
        rig.tick()
    rig.op.change(zoom=3)
    assert rig.state.writes == 0
    assert rig.state.redraws == 1


def test_numeric_and_distance_errors_redraw_in_the_callback(rig):
    rig.edit()
    rig.tick()

    def invalid(*_args):
        raise ValueError('invalid numeric value')

    rig.namespace['_parameters'] = invalid
    rig.namespace['fields_changed'](rig.op.wm, rig.context)
    assert not rig.op.ready and rig.op.error == 'invalid numeric value'
    assert rig.state.redraws == 2
    rig.api.set_distance = lambda *_args: None
    rig.namespace['distance_changed'](rig.op.wm, rig.context)
    assert rig.op.ready and not rig.op.error and rig.state.redraws == 3


@pytest.mark.parametrize('invalidate', ['scene', 'window', 'area', 'camera', 'identity', 'frame'])
def test_lifecycle_and_edit_clock_still_cancel(rig, invalidate):
    rig.edit()
    rig.tick()
    if invalidate == 'scene':
        rig.op.window.scene = object()
    elif invalidate == 'window':
        rig.op.wm.windows.clear()
    elif invalidate == 'area':
        rig.op.area.type = 'TEXT_EDITOR'
    elif invalidate == 'camera':
        rig.op.scene.objects.clear()
    elif invalidate == 'identity':
        rig.state.valid = False
    else:
        rig.op.scene.frame_current += 1
    assert rig.tick() == {'CANCELLED'}
    assert rig.op.closed and rig.op.session.closed and rig.op.timer is None
    assert rig.op.handle is None and rig.op.preview.closed
    assert len(rig.state.removed_timers) == 1


def test_capture_pauses_preview_polling_and_closing_releases_resources(rig):
    rig.tick()
    rig.state.capture = True
    rig.tick()
    assert rig.op.preview.calls == 1 and rig.state.redraws == 2
    assert rig.op.modal(rig.context, rig.event('ESC')) == {'PASS_THROUGH'}
    rig.state.capture = False
    assert rig.op.modal(rig.context, rig.event('ESC')) == {'FINISHED'}
    assert rig.op.closed and rig.op.preview.closed
    assert rig.state.restored == 1 and len(rig.state.removed_timers) == 1
