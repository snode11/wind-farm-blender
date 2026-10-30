from pathlib import Path
from contextlib import nullcontext
import importlib.util
import io
import json
import sys
import threading
import time
from types import SimpleNamespace as NS
import pytest
from PIL import Image
from wfrl_blender import custom_camera_capture as capture
from wfrl_blender import custom_camera_preview as preview
from wfrl_blender import custom_cameras as core
from wfrl_blender.custom_camera_capture import write_png,write_json


def test_png_preserves_display_bytes_and_flips_only_rows(tmp_path):
    # GPU rows start at lower left, PNG rows at upper left. Explicit asymmetric
    # colour/alpha fixture detects gamma reapplication, channels and orientation.
    bottom=bytes([0,0,255,255,17,91,143,128])
    top=bytes([255,0,0,255,0,255,0,255])
    target=tmp_path/'image.png'
    write_png(target,2,2,bottom+top)
    with Image.open(target) as image:
        assert list(image.getdata())==[(255,0,0,255),(0,255,0,255),(0,0,255,255),(17,91,143,128)]
    with pytest.raises(FileExistsError):write_png(target,2,2,bottom+top)
    with pytest.raises(ValueError):write_png(tmp_path/'bad.png',3,3,bottom)


def test_manifest_rejects_nan_and_preserves_committed_version(tmp_path):
    target=tmp_path/'manifest.json'
    write_json(target,{'status':'incomplete'})
    with pytest.raises(ValueError):write_json(target,{'time_s':float('nan')})
    assert json.loads(target.read_text())=={'status':'incomplete'}


@pytest.mark.parametrize('sequence', [False, True])
def test_capture_step_requests_pixels_without_texture_and_exports_state(tmp_path, monkeypatch, sequence):
    job = capture.Capture.__new__(capture.Capture)
    matrix = tuple(tuple(float(i == j) for j in range(4)) for i in range(4))
    pixels = bytes([0, 0, 255, 255, 17, 91, 143, 128,
                    255, 0, 0, 255, 0, 255, 0, 255])
    image = preview.CameraImage.__new__(preview.CameraImage)
    image.width = image.height = 2
    image.view = image.world = image.projection = matrix
    image.params = core.CameraParameters((1., 2., 3.))
    image.state_hash, image.texture, image.pixels = 'geometry-state', None, pixels
    job.closed = job.in_group = job.cancel_requested = False
    job._writer = job._pending_records = None
    job.index, job.times, job.sequence = 0, [1.25], sequence
    job.scene = NS(frame_current=76, frame_subframe=0., frame_start=1,
                   objects={core.ROOT_NAME: NS(matrix_world=matrix)}, get=lambda _key, default: default)
    job.cameras, job.render_target = [{'wfrl_custom_slot': 3}], object() if sequence else None
    job.path, job.capture_id, job.source, job.layout_hash = tmp_path, 'capture-id', {}, 'layout-hash'
    job.reader = NS(start_s=0., at=lambda _at: {'motion': {'yaw_deg': 10.}})
    job.profile = {'color_management': {'gamma': 1}, 'image_encoding': 'RGBA8 display-transformed once; PNG sRGB'}
    job.manifest = {'active_camera_ids': ['C3'], 'status': 'incomplete'}
    context, calls, finishes, evaluations = object(), [], [], []
    monkeypatch.setattr(job, 'guard', lambda: None)
    monkeypatch.setattr(job, 'evaluate', lambda *args: evaluations.append(args))
    monkeypatch.setattr(job, 'finish', lambda **kwargs: finishes.append(kwargs))
    def render(render_context, cameras, **kwargs):
        calls.append((render_context, cameras, kwargs))
        assert kwargs.get('create_texture') is False
        return {3: image}
    monkeypatch.setattr(preview, 'render_group', render)
    assert job.step(context) is False
    deadline = time.monotonic() + 5
    while not job.step(context):
        assert time.monotonic() < deadline, 'PNG capture did not complete'
        threading.Event().wait(.001)
    assert calls == [(context, job.cameras, {'target': job.render_target, 'create_texture': False})]
    assert finishes == [{'success': True}] and not job.in_group
    assert evaluations == ([(76, 0., 1.25)] if sequence else [])
    assert image.texture is None and image.rgba() is None
    with Image.open(tmp_path/'C3/frame_000000.png') as png:
        assert list(png.getdata()) == [(255, 0, 0, 255), (0, 255, 0, 255),
                                       (0, 0, 255, 255), (17, 91, 143, 128)]
    record = json.loads((tmp_path/'frames.jsonl').read_text())
    assert record['simulation_state_hash'] == 'geometry-state'
    assert record['camera_id'] == 'C3' and record['time_s'] == 1.25
    assert record['T_world_from_camera_blender'] == [list(row) for row in matrix]
    assert record['color_management'] == {'gamma': 1}
    assert json.loads((tmp_path/'manifest.json').read_text())['completed_samples'] == 1


@pytest.mark.parametrize('termination', ['success', 'cancel', 'exception', 'invalid_session'])
def test_capture_closes_gpu_resources_for_every_termination(tmp_path, termination, monkeypatch):
    job = capture.Capture.__new__(capture.Capture)
    job.closed = job.in_group = job.cancel_requested = False
    job._writer = job._pending_records = None
    job.cancel_accepted_at = job.cancel_stopped_at = None
    job.index, job.times, job.error = 0, [0, 1], ''
    job.path, job.manifest = tmp_path, {'status': 'incomplete'}
    job.render_target = preview.RenderTargetPool()
    freed = []
    job.render_target.targets[(10, 10)] = NS(releases=0)
    def release():
        freed.append(True)
        job.render_target.targets[(10, 10)].releases += 1
    job.render_target.targets[(10, 10)].free = release
    monkeypatch.setattr(job, 'same_session', lambda: termination != 'invalid_session')
    # The restoration mechanism is independently exercised in native tests.
    monkeypatch.setattr(job, '_play', lambda *_: None)
    monkeypatch.setattr(job, '_clock_unchanged', lambda: True)
    job.sequence, job.original_time_property, job.scene, job.playing = False, None, {}, False
    monkeypatch.setattr(capture, '_ACTIVE', job)
    if termination == 'exception':
        def failure(_context):
            raise RuntimeError('injected GPU failure')
        monkeypatch.setattr(job, '_step_group', failure)
        with pytest.raises(RuntimeError, match='injected GPU failure'):
            job.step(None)
    elif termination == 'cancel':
        job.request_cancel()
    else:
        job.finish(success=termination == 'success')
    assert job.closed and not capture.active() and freed == [True]
    manifest = json.loads((tmp_path/'manifest.json').read_text())
    assert manifest['gpu_target_usage']['active_targets'] == 0
    assert manifest['gpu_target_usage']['releases'] == 1
    assert manifest['status'] == ('complete' if termination == 'success' else 'incomplete')
    job.finish()
    assert freed == [True]


def test_guard_reports_invalid_native_pose_before_layout_hashing(monkeypatch):
    job = capture.Capture.__new__(capture.Capture)
    job.scene = NS(frame_current=1, frame_subframe=0)
    job.window = NS(screen=NS(is_animation_playing=False))
    job.expected_frame = (1, 0)
    job.playback_generation = capture.playback.generation()
    monkeypatch.setattr(job, 'same_session', lambda: True)
    monkeypatch.setitem(sys.modules, core.__package__+'.panels.custom_cameras',
                        NS(installation_block_reason=lambda _scene: ''))
    def invalid(_scene):
        raise ValueError('C1 实际姿态含非有限数值')
    def unexpected(_scene):
        raise AssertionError('layout hashing ran on invalid native state')
    monkeypatch.setattr(core, 'validate_native_state', invalid)
    monkeypatch.setattr(core, 'layout_dict', unexpected)
    with pytest.raises(RuntimeError, match='C1 实际姿态含非有限数值'):
        job.guard()


@pytest.fixture
def clock_capture(tmp_path, monkeypatch):
    class Scene(dict):
        frame_current = 10
        frame_subframe = .25

    job = capture.Capture.__new__(capture.Capture)
    job.scene = Scene(wfrl_clearance_time_s=9.25)
    job.window = NS(screen=NS(is_animation_playing=False))
    job.closed = job.in_group = job.cancel_requested = False
    job._writer = job._pending_records = None
    job.cancel_accepted_at = job.cancel_stopped_at = None
    job.index, job.times = 1, [0., 1., 2.]
    job.path, job.manifest, job.render_target = tmp_path, {'status': 'incomplete'}, None
    job.sequence, job.playing = True, True
    job.frame, job.subframe, job.time = 1, .5, .5
    job.expected_frame = (10, .25)
    job.original_time_property = .5
    job.playback_generation = 3
    job.layout_hash, job.live_layout_hash, job.profile = 'saved', 'native', {}
    calls = []
    generation = [3]

    def evaluate(frame, subframe, time_s):
        job.scene.frame_current, job.scene.frame_subframe = frame, subframe
        job.scene['wfrl_clearance_time_s'] = time_s
        calls.append(('evaluate', frame, subframe, time_s))

    monkeypatch.setattr(job, 'same_session', lambda: True)
    monkeypatch.setattr(job, '_play', lambda playing: calls.append(('play', playing)))
    monkeypatch.setattr(job, 'evaluate', evaluate)
    monkeypatch.setattr(capture.playback, 'generation', lambda: generation[0])
    monkeypatch.setattr(capture.playback, 'owner_scene', lambda: None)
    monkeypatch.setattr(capture, '_ACTIVE', job)
    monkeypatch.setitem(sys.modules, core.__package__ + '.panels.custom_cameras',
                        NS(installation_block_reason=lambda _scene: ''))
    monkeypatch.setattr(core, 'validate_native_state', lambda _scene: None)
    monkeypatch.setattr(core, 'layout_dict', lambda _scene: {})
    monkeypatch.setattr(core, 'layout_hash', lambda _layout: 'saved')
    monkeypatch.setattr(core, 'live_layout_hash', lambda _scene, layout=None: 'native')
    monkeypatch.setattr(preview, 'render_profile', lambda _scene: {})
    return job, calls, generation


def test_guard_reuses_one_fresh_layout_and_detects_subsequent_mutation(clock_capture, monkeypatch):
    job, _calls, _generation = clock_capture
    layouts = []
    current = {'model_signature': 'mesh-a'}

    def layout_dict(_scene):
        layout = dict(current)
        layouts.append(layout)
        return layout

    def live_hash(_scene, layout=None):
        assert layout is layouts[-1]
        return 'native'

    monkeypatch.setattr(core, 'layout_dict', layout_dict)
    monkeypatch.setattr(core, 'layout_hash', lambda layout: 'saved' if layout == {'model_signature': 'mesh-a'} else 'changed')
    monkeypatch.setattr(core, 'live_layout_hash', live_hash)
    job.guard()
    assert len(layouts) == 1
    current['model_signature'] = 'mesh-b'
    with pytest.raises(RuntimeError, match='相机布局已变化'):
        job.guard()
    assert len(layouts) == 2


@pytest.mark.parametrize('intervention', ['frame', 'subframe', 'playback'])
def test_external_clock_change_aborts_capture_without_restoring_over_user_state(clock_capture, intervention):
    job, calls, generation = clock_capture
    job.guard()
    if intervention == 'playback':
        generation[0] += 2
    else:
        setattr(job.scene, 'frame_current' if intervention == 'frame' else 'frame_subframe', 20 if intervention == 'frame' else .75)
        job.scene['wfrl_clearance_time_s'] = 19.75
    edited = (job.scene.frame_current, job.scene.frame_subframe, dict(job.scene))
    with pytest.raises(RuntimeError, match='采集时刻'):
        job.guard()
    job.finish(error='采集时刻被外部操作改变，采集未完成')
    assert (job.scene.frame_current, job.scene.frame_subframe, dict(job.scene)) == edited
    assert calls == []
    assert job.closed and not capture.active()
    assert job.manifest['status'] == 'incomplete'


@pytest.mark.parametrize('success', [False, True])
def test_capture_restores_unchanged_clock_on_completion_or_cancel(clock_capture, success):
    job, calls, _generation = clock_capture
    if success:
        job.finish(success=True)
    else:
        job.request_cancel()
    assert calls == [('play', False), ('evaluate', 1, .5, .5), ('play', True)]
    assert (job.scene.frame_current, job.scene.frame_subframe) == (1, .5)
    assert job.scene['wfrl_clearance_time_s'] == .5
    assert job.closed and not capture.active()
    assert job.manifest['status'] == ('complete' if success else 'incomplete')


def test_capture_restores_own_frame_change_when_geometry_evaluation_fails(clock_capture, monkeypatch):
    from contextlib import nullcontext
    job, calls, _generation = clock_capture
    evaluations = []

    def set_frame(frame, *, subframe):
        job.scene.frame_current, job.scene.frame_subframe = frame, subframe

    def update(scene, *, sim_time_s, **kwargs):
        evaluations.append(sim_time_s)
        if len(evaluations) == 1:
            raise RuntimeError('injected geometry evaluation failure')
        scene['wfrl_clearance_time_s'] = sim_time_s

    job.scene.frame_set = set_frame
    job.farm = NS(update=update)
    monkeypatch.setattr(job, 'evaluate', capture.Capture.evaluate.__get__(job))
    monkeypatch.setattr(capture, 'readonly_frame_handlers', nullcontext)
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=NS(view_layer=NS(update=lambda: None))))
    monkeypatch.setattr(job, '_step_group', lambda _context: job.evaluate(20, .5, 19.5))
    with pytest.raises(RuntimeError, match='geometry evaluation failure'):
        job.step(None)
    assert evaluations == [19.5, .5]
    assert calls == [('play', False), ('play', True)]
    assert (job.scene.frame_current, job.scene.frame_subframe) == (1, .5)
    assert job.scene['wfrl_clearance_time_s'] == .5
    assert job.closed and not capture.active()
    assert job.manifest['status'] == 'incomplete'


@pytest.mark.parametrize('intervention', ['cancel', 'seek', 'write_failure', 'shutdown'])
def test_pending_png_yields_and_never_commits_an_invalid_group(clock_capture, monkeypatch, intervention):
    job, calls, _generation = clock_capture
    entered, release = threading.Event(), threading.Event()
    original_write = capture.write_png
    target = job.path / 'C1/frame_000001.png'

    def blocked_write(*args):
        assert threading.current_thread() is not threading.main_thread()
        entered.set()
        assert release.wait(3), 'PNG worker was not released'
        if intervention == 'write_failure':
            raise OSError('injected PNG disk failure')
        original_write(*args)

    monkeypatch.setattr(capture, 'write_png', blocked_write)
    writer = capture.ImageWriter([(target, 1, 1, bytes([17, 91, 143, 128]))])
    job._writer, job._pending_records = writer, [{'camera_id': 'C1', 'sample_index': 1}]
    job.in_group = True
    try:
        assert entered.wait(1)
        assert job.step(None) is False
        assert job.index == 1 and not (job.path / 'frames.jsonl').exists()
        if intervention == 'cancel':
            job.request_cancel()
            assert not job.closed and capture.active()
            assert job.step(None) is False
        elif intervention == 'seek':
            job.scene.frame_current = 20
            with pytest.raises(RuntimeError, match='采集时刻'):
                job.step(None)
            assert job.scene.frame_current == 20 and calls == []
        elif intervention == 'shutdown':
            capture.shutdown(restore=False)
            assert job.closed and writer.cancelled.is_set() and calls == []
        release.set()
        assert writer.done.wait(3)
        if intervention == 'write_failure':
            with pytest.raises(OSError, match='PNG disk failure'):
                job.step(None)
        elif intervention == 'cancel':
            assert job.step(None)
            assert calls == [('play', False), ('evaluate', 1, .5, .5), ('play', True)]
        assert job.closed and not capture.active()
        manifest = json.loads((job.path / 'manifest.json').read_text())
        assert manifest['status'] == 'incomplete'
        assert job.index == (2 if intervention == 'cancel' else 1)
        if intervention == 'cancel':
            assert manifest['completed_samples'] == 2
            with Image.open(target) as image:
                assert image.getpixel((0, 0)) == (17, 91, 143, 128)
        else:
            assert not (job.path / 'frames.jsonl').exists()
    finally:
        release.set()
        writer.thread.join(3)
        assert not writer.thread.is_alive()


def test_unfinished_png_cannot_be_published_as_a_complete_capture(clock_capture, monkeypatch):
    job, _calls, _generation = clock_capture
    entered, release = threading.Event(), threading.Event()

    def blocked_write(*_args):
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(capture, 'write_png', blocked_write)
    writer = capture.ImageWriter([(job.path / 'C1/frame_000001.png', 1, 1, bytes(4))])
    job._writer, job._pending_records, job.in_group = writer, [], True
    try:
        assert entered.wait(1)
        job.finish(success=True)
        assert job.manifest['status'] == 'incomplete'
        assert '写入' in job.error
    finally:
        release.set()
        writer.thread.join(3)


@pytest.mark.parametrize('already_closed', [False, True])
@pytest.mark.parametrize('restore_failure', [False, True])
def test_capture_operator_reports_the_committed_transaction_result(clock_capture, monkeypatch,
                                                                  already_closed, restore_failure):
    job, _calls, _generation = clock_capture
    props = NS(EnumProperty=lambda **kw: None, FloatProperty=lambda **kw: None,
               StringProperty=lambda **kw: None)
    monkeypatch.setitem(sys.modules, 'bpy', NS(types=NS(Operator=type('Operator', (), {}),
                                                       Panel=type('Panel', (), {}))))
    monkeypatch.setitem(sys.modules, 'bpy.props', props)
    path = Path(capture.__file__).parent / 'panels/custom_camera_output.py'
    spec = importlib.util.spec_from_file_location('wfrl_blender.panels._capture_result_test', path)
    panel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(panel)
    operator = panel.WFRL_OT_CustomCapture.__new__(panel.WFRL_OT_CustomCapture)
    operator.transaction = job
    operator.timer = object()
    operator.wm = NS(event_timer_remove=lambda timer: None)
    operator.area = NS(regions=[NS(type='WINDOW')], tag_redraw=lambda: None)
    reports = []
    operator.report = lambda level, message: reports.append((level, message))
    context = NS(temp_override=lambda **kw: nullcontext())

    def final_group(_context):
        job.finish(success=True)
        return True

    monkeypatch.setattr(job, '_step_group', final_group)
    if restore_failure:
        def fail_restore(*args):
            raise RuntimeError('injected restore failure')
        monkeypatch.setattr(job, 'evaluate', fail_restore)
    if already_closed:
        final_group(context)
    result = operator.modal(context, NS(type='TIMER', timer=operator.timer))
    assert result == ({'CANCELLED'} if restore_failure else {'FINISHED'})
    assert reports and reports[-1][0] == ({'ERROR'} if restore_failure else {'INFO'})
    assert ('injected restore failure' in reports[-1][1]) is restore_failure
    assert json.loads((job.path / 'manifest.json').read_text())['status'] == (
        'incomplete' if restore_failure else 'complete')
