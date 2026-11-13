"""Fault injection for owned Down export resources and retryable finalization."""
import io
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace as NS

import pytest


class Scene(dict):
    alive = True
    wfrl_manual_turbines = ()

    def as_pointer(self):
        if not self.alive:
            raise ReferenceError('Scene was removed')
        return 77


@pytest.fixture
def down(frontend_module, stub_module, monkeypatch):
    stub_module('bpy_extras.io_utils', ExportHelper=type('ExportHelper', (), {}),
                ImportHelper=type('ImportHelper', (), {}))
    module = frontend_module('wfrl_blender.manual_down')
    monkeypatch.setattr(module.bpy.app, 'is_job_running', lambda _kind: False)
    for name in ('frame_change_pre', 'render_write', 'render_complete', 'render_cancel',
                 'load_pre', 'undo_pre', 'redo_pre'):
        monkeypatch.setattr(module.bpy.app.handlers, name, [])
    return module


@pytest.fixture
def export_job(down, monkeypatch, tmp_path):
    scene = Scene()
    temporary = tmp_path / 'render'
    temporary.mkdir()
    (temporary / 'partial.mp4').write_bytes(b'partial')
    job = dict(temp=temporary, saved={})
    down._own_job(scene, job)
    monkeypatch.setattr(down, '_restore', lambda *_args: None)
    monkeypatch.setattr(down, '_restore_manual_pose', lambda *_args: None)
    return scene, job


class Process:
    def __init__(self, *, running=True):
        self.running = running
        self.stdin = io.BytesIO()
        self.kills = 0

    def poll(self):
        return None if self.running else 0

    def kill(self):
        self.kills += 1

    def wait(self, timeout):
        if self.running:
            raise subprocess.TimeoutExpired('encoder', timeout)
        return 0


class Writer:
    ident = 1

    def __init__(self, *, alive=True):
        self.alive = alive
        self.joins = 0
        self.checks = 0

    def join(self, timeout):
        self.joins += 1

    def is_alive(self):
        self.checks += 1
        return self.alive


def encoder_resource(job, *, running=True, alive=True):
    resource = dict(process=Process(running=running), writer=Writer(alive=alive),
                    log=io.BytesIO(), stopping=threading.Event())
    job.setdefault('encoders', []).append(resource)
    return resource


@pytest.mark.parametrize(('running', 'alive'), [(True, False), (False, True), (True, True)])
def test_encoder_shutdown_retains_every_handle_until_process_and_thread_exit(down, export_job, running, alive):
    scene, job = export_job
    resource = encoder_resource(job, running=running, alive=alive)
    assert down._finish(scene, completed=False) is None
    assert job['state'] == 'STOPPING' and down._EXPORT[77] is job
    assert job['encoders'] == [resource]
    assert scene['wfrl_manual_exporting'] and job['temp'].exists()
    assert not resource['log'].closed and not resource['process'].stdin.closed
    assert resource['writer'].joins and resource['writer'].checks
    resource['process'].running = resource['writer'].alive = False
    assert down._retry_exports() is None
    assert not down._EXPORT and not down._JOBS and not job['encoders']
    assert resource['log'].closed and resource['process'].stdin.closed
    assert not scene['wfrl_manual_exporting'] and not job['temp'].exists()


def test_context_manager_checks_last_join_and_keeps_stuck_writer(down, monkeypatch):
    job, process, writer = {}, Process(running=False), Writer()
    writer.start = lambda: None
    monkeypatch.setattr(down.subprocess, 'Popen', lambda *_a, **_k: process)
    monkeypatch.setattr(down.threading, 'Thread', lambda **_k: writer)
    with pytest.raises(RuntimeError, match='停止中'):
        with down._offscreen_encoder(['encoder'], job=job):
            pass
    assert writer.joins == 2 and writer.checks == 2
    assert job['state'] == 'STOPPING' and len(job['encoders']) == 1
    assert not job['encoders'][0]['log'].closed
    writer.alive = False
    assert down._reap_encoders(job) and not job['encoders']


def test_encoder_start_failure_closes_registered_log(down, monkeypatch):
    job, log = {}, io.BytesIO()
    monkeypatch.setattr(down.tempfile, 'TemporaryFile', lambda: log)
    def fail(*_a, **_k):
        assert job['encoders'][0]['log'] is log
        raise OSError('encoder startup failed')
    monkeypatch.setattr(down.subprocess, 'Popen', fail)
    with pytest.raises(OSError, match='startup failed'):
        with down._offscreen_encoder(['encoder'], job=job):
            pytest.fail('encoder must not start')
    assert log.closed and not job['encoders']


def test_failed_restoration_keeps_finalizing_ownership_and_retries(down, export_job, monkeypatch):
    scene, job = export_job
    calls = []
    def restore(*_args):
        calls.append(1)
        assert down._EXPORT[77] is job and scene['wfrl_manual_exporting']
        assert job['state'] == 'FINALIZING'
        if len(calls) == 1:
            raise RuntimeError('restore camera failed')
    monkeypatch.setattr(down, '_restore', restore)
    with pytest.raises(RuntimeError, match='restore camera failed'):
        down._finish(scene, completed=False)
    assert job['state'] == 'FINALIZING' and job['temp'].exists()
    assert 'scene' not in job['restored'] and scene['wfrl_manual_exporting']
    assert down._retry_exports() is None
    assert len(calls) == 2 and job['state'] == 'FINISHED'
    assert not scene['wfrl_manual_exporting'] and not down._EXPORT


def test_reentrant_finish_does_not_release_ownership(down, export_job, monkeypatch):
    scene, job = export_job
    def restore(*_args):
        assert down._finish(scene, completed=False) is None
        assert down._EXPORT[77] is job and scene['wfrl_manual_exporting']
    monkeypatch.setattr(down, '_restore', restore)
    down._finish(scene, completed=False)
    assert not down._EXPORT


def test_late_old_finalizer_cannot_clear_new_job_or_restore_its_pose(down, export_job, monkeypatch):
    scene, old = export_job
    new = dict(identity='new', scene_key=77, scene=scene, state='RUNNING')
    def replaced(*_args):
        down._EXPORT[77] = down._JOBS['new'] = new
        scene[down._JOB_KEY] = 'new'
        scene['wfrl_manual_last_video'] = 'new.mp4'
        scene['wfrl_manual_error'] = 'new status'
    monkeypatch.setattr(down, '_restore', replaced)
    monkeypatch.setattr(down, '_restore_manual_pose', lambda *_a: pytest.fail('stale pose write'))
    down._finish(scene, completed=False, job=old)
    down._job_error(old, 'late error')
    assert down._EXPORT[77] is new and down._JOBS == {'new': new}
    assert scene[down._JOB_KEY] == 'new' and scene['wfrl_manual_exporting']
    assert scene['wfrl_manual_last_video'] == 'new.mp4' and scene['wfrl_manual_error'] == 'new status'
    assert not old['temp'].exists()


def test_restore_rechecks_identity_after_frame_set_before_setting_camera(down, tmp_path):
    scene = Scene()
    job = dict(temp=tmp_path)
    down._own_job(scene, job)
    scene.render = NS(image_settings=NS(), ffmpeg=NS())
    scene.view_settings, scene.eevee, scene.display = NS(), NS(), NS()
    scene.timeline_markers = ()
    scene.camera = 'new camera'
    def frame_set(*_a, **_k):
        scene[down._JOB_KEY] = 'new identity'
    scene.frame_set = frame_set
    saved = dict(image={}, ffmpeg={}, render={}, view=('Standard', 'None'), eevee=(1, 1),
                 render_aa='FXAA', markers=[], frame=(1, 4, 1, 1, 0.), camera='old camera')
    with pytest.raises(down._ExportOwnershipLost):
        down._restore(scene, saved, job)
    assert scene.camera == 'new camera'


def test_camera_transform_failure_keeps_restore_record(down):
    class Camera:
        fail = True
        def __setattr__(self, name, value):
            if name == 'location' and self.fail:
                raise RuntimeError('camera is temporarily locked')
            object.__setattr__(self, name, value)
    camera = Camera()
    job = dict(rig=[(camera, None, dict(location=(1, 2, 3)))])
    with pytest.raises(RuntimeError, match='locked'):
        down._end_shake(job)
    assert len(job['rig']) == 1
    camera.fail = False
    down._end_shake(job)
    assert camera.location == (1, 2, 3) and 'rig' not in job


def test_temp_directory_failure_reports_path_and_keeps_retry(down, export_job, monkeypatch):
    scene, job = export_job
    remove = down.video_output.shutil.rmtree
    def fail(path, **_kwargs):
        raise PermissionError('directory locked')
    monkeypatch.setattr(down.video_output.shutil, 'rmtree', fail)
    with pytest.raises(RuntimeError, match=str(job['temp'])):
        down._finish(scene, completed=False)
    assert job['cleanup'] and job['temp'].exists() and down._EXPORT[77] is job
    assert str(job['temp']) in scene['wfrl_manual_error']
    monkeypatch.setattr(down.video_output.shutil, 'rmtree', remove)
    down._retry_exports()
    assert not job['temp'].exists() and not down._EXPORT


def test_success_publishes_only_once_and_holds_ownership_through_file_cleanup(down, export_job, monkeypatch, tmp_path):
    scene, job = export_job
    video = down.video_output
    movie, samples = job['temp'] / 'movie.mp4', job['temp'] / 'samples.json'
    movie.write_bytes(b'complete movie')
    samples.write_text('{"samples": []}', encoding='utf-8')
    targets = (tmp_path / 'out.mp4', tmp_path / 'out.json')
    job.update(plan=dict(frames=1), frames_written=1, movies=[movie], sample_path=samples,
               publish_targets=video.validate_publish_targets(targets))
    publish, remove, calls = video.publish_staged, video.shutil.rmtree, []
    def publish_owned(*args, **kwargs):
        assert down._EXPORT[77] is job and scene['wfrl_manual_exporting']
        calls.append(1)
        publish(*args, **kwargs)
    def locked(path, **kwargs):
        assert down._EXPORT[77] is job and scene['wfrl_manual_exporting']
        if path == job['temp']:
            raise PermissionError('temporary render directory locked')
        remove(path, **kwargs)
    monkeypatch.setattr(video, 'publish_staged', publish_owned)
    monkeypatch.setattr(video.shutil, 'rmtree', locked)
    with pytest.raises(RuntimeError, match='locked'):
        down._finish(scene, completed=True)
    assert job['state'] == 'FINALIZING' and targets[0].read_bytes() == b'complete movie'
    monkeypatch.setattr(video.shutil, 'rmtree', remove)
    assert down._retry_exports() is None
    assert len(calls) == 1 and scene['wfrl_manual_last_video'] == str(targets[0])
    assert not scene['wfrl_manual_exporting'] and not down._EXPORT


def test_replaced_temp_directory_is_reported_but_never_retried(down, export_job):
    scene, job = export_job
    moved = job['temp'].with_name('original')
    job['temp'].rename(moved)
    job['temp'].mkdir()
    foreign = job['temp'] / 'foreign'
    foreign.write_bytes(b'not ours')
    with pytest.raises(RuntimeError, match='归属已改变'):
        down._finish(scene, completed=False)
    assert not job['cleanup'] and foreign.read_bytes() == b'not ours'
    down._retry_exports()
    assert foreign.read_bytes() == b'not ours' and moved.exists()


@pytest.mark.parametrize('native', [False, True])
def test_deleted_scene_reclaims_resources_without_scene_enumeration(down, export_job, monkeypatch, native):
    scene, job = export_job
    resource = encoder_resource(job, running=False, alive=False)
    job['native_render'] = native
    scene.alive = False
    monkeypatch.setattr(down.bpy.data, 'scenes', None)
    monkeypatch.setattr(down.bpy.app, 'is_job_running', lambda _kind: native)
    if native:
        assert down._retry_exports() == .2
        assert job['state'] == 'STOPPING' and down._EXPORT[77] is job
        assert job['temp'].exists() and not resource['log'].closed
    monkeypatch.setattr(down.bpy.app, 'is_job_running', lambda _kind: False)
    assert down._retry_exports() is None
    assert resource['log'].closed and not job['temp'].exists()
    assert not down._EXPORT and not down._JOBS


@pytest.mark.parametrize('handler', ['load_pre', 'undo_pre', 'redo_pre'])
def test_pre_change_requests_stop_and_invalidates_scene_identity(down, export_job, monkeypatch, handler):
    scene, job = export_job
    job['native_render'] = True
    monkeypatch.setattr(down.bpy.app, 'is_job_running', lambda _kind: True)
    dict(down._LIFECYCLE_HANDLERS)[handler](None)
    assert job['stop_requested'] and job['detached'] and job['state'] == 'STOPPING'
    assert down._EXPORT[77] is job and scene['wfrl_manual_exporting']
    monkeypatch.setattr(down.bpy.app, 'is_job_running', lambda _kind: False)
    monkeypatch.setattr(down, '_restore', lambda *_a: pytest.fail('stale RNA restoration'))
    assert down._retry_exports() is None
    assert not down._EXPORT and not job['temp'].exists()


def test_render_callback_waits_for_native_job_exit(down, export_job, monkeypatch):
    scene, job = export_job
    job['native_render'] = True
    monkeypatch.setattr(down.bpy.app, 'is_job_running', lambda _kind: True)
    down._render_cancel(scene)
    assert job['state'] == 'STOPPING' and scene['wfrl_manual_exporting']
    monkeypatch.setattr(down.bpy.app, 'is_job_running', lambda _kind: False)
    assert down._retry_exports() is None
    assert not scene['wfrl_manual_exporting']


class Modifiers(list):
    def get(self, name):
        return next((modifier for modifier in self if modifier.name == name), None)

    def new(self, name, kind):
        modifier = NS(name=name, type=kind, show_viewport=True, show_render=True)
        self.append(modifier)
        return modifier


class Blade(dict):
    def __init__(self, name, mesh):
        super().__init__()
        self.name, self.data = name, mesh
        self.modifiers = Modifiers([NS(name='Bevel', show_viewport=True, show_render=False)])

    def as_pointer(self):
        return id(self)


@pytest.fixture
def dynamics(down, monkeypatch):
    module = down.manual_dynamics
    mesh = NS(name='original', users=0)
    baked = {'wfrl_flap_span': (0., 10.)}
    blades = [Blade(f'B{i}', mesh) for i in range(3)]
    root = Scene()
    root.matrix_basis = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    entry = NS(turbine_id='T1', yaw_object=NS(parent=root),
               **{f'blade{i}_object': blade for i, blade in enumerate(blades, 1)})
    sockets = [NS(name=name, identifier=name, item_type='SOCKET', in_out='INPUT',
                  socket_type={'Geometry': 'NodeSocketGeometry', 'Direction': 'NodeSocketVector'}.get(
                      name, 'NodeSocketFloat'))
               for name in ('Geometry', 'Direction', 'Root', 'Length', 'Tip')]
    monkeypatch.setattr(module, '_node_group', lambda: NS(interface=NS(items_tree=sockets)))
    monkeypatch.setattr(module, '_set_input', lambda *_a: None)
    monkeypatch.setattr(module.bpy.data.meshes, 'get', lambda name: mesh if name == mesh.name else None)
    return NS(module=module, mesh=mesh, baked=baked, blades=blades, root=root, entry=entry, sockets=sockets)


def assert_rigid(rig):
    assert all(blade.data is rig.mesh and rig.module._RESTORE_KEY not in blade for blade in rig.blades)
    assert all(len(blade.modifiers) == 1 and blade.modifiers[0].show_viewport
               and not blade.modifiers[0].show_render for blade in rig.blades)
    assert rig.module._LEAN_KEY not in rig.root


def test_missing_root_input_rejected_before_any_blade_mutation(dynamics):
    dynamics.sockets[:] = [socket for socket in dynamics.sockets if socket.name != 'Root']
    with pytest.raises(ValueError, match='Root'):
        dynamics.module._attach(dynamics.entry, dynamics.baked)
    assert_rigid(dynamics)


def test_wrong_root_socket_type_rejected_before_mutation(dynamics):
    next(socket for socket in dynamics.sockets if socket.name == 'Root').socket_type = 'NodeSocketVector'
    with pytest.raises(ValueError, match='Root'):
        dynamics.module._attach(dynamics.entry, dynamics.baked)
    assert_rigid(dynamics)


@pytest.mark.parametrize('boundary', ['modifier_creation', 'input_assignment', 'mesh_assignment', 'modifier_disable'])
def test_partial_attach_rolls_back_all_acquired_blades(dynamics, monkeypatch, boundary):
    module, blades = dynamics.module, dynamics.blades
    if boundary == 'modifier_creation':
        def fail(*_a):
            raise RuntimeError('modifier creation failed')
        monkeypatch.setattr(blades[1].modifiers, 'new', fail)
    elif boundary == 'input_assignment':
        def fail(modifier, *_a):
            if modifier is blades[1].modifiers.get(module.MODIFIER):
                raise RuntimeError('input assignment failed')
        monkeypatch.setattr(module, '_set_input', fail)
    elif boundary == 'mesh_assignment':
        original = Blade.__setattr__
        def fail(self, name, value):
            if self is blades[1] and name == 'data' and value is dynamics.baked:
                raise RuntimeError('mesh assignment failed')
            original(self, name, value)
        monkeypatch.setattr(Blade, '__setattr__', fail)
    else:
        class FailingModifier:
            name, show_viewport = 'Bevel', True
            @property
            def show_render(self):
                return True
            @show_render.setter
            def show_render(self, value):
                if not value:
                    raise RuntimeError('modifier disable failed')
        blades[1].modifiers[0] = FailingModifier()
    with pytest.raises(RuntimeError, match='failed'):
        module._attach(dynamics.entry, dynamics.baked)
    if boundary == 'modifier_disable':
        assert blades[1].modifiers[0].show_viewport and blades[1].modifiers[0].show_render
        blades[1].modifiers[0] = NS(name='Bevel', show_viewport=True, show_render=False)
    assert_rigid(dynamics)


def test_start_registers_restore_state_and_keeps_it_when_rollback_fails(dynamics, monkeypatch):
    module, job = dynamics.module, {}
    baked = NS(users=0)
    def bake(_blade, owner):
        assert job['dynamics_rig'] is owner
        owner['baked'] = baked
        return dynamics.baked
    monkeypatch.setattr(module, '_bake', bake)
    restore = module._restore_blade
    def fail_restore(_blade):
        raise RuntimeError('mesh locked')
    monkeypatch.setattr(module, '_restore_blade', fail_restore)
    def fail_input(*_a):
        raise RuntimeError('input failed')
    monkeypatch.setattr(module, '_set_input', fail_input)
    with pytest.raises(RuntimeError, match='mesh locked'):
        module.start(NS(wfrl_manual_turbines=[dynamics.entry]), job, 'T1', seed=1)
    assert job['dynamics_rig']['rig']['blades']
    assert dynamics.blades[0].data is dynamics.baked
    monkeypatch.setattr(module, '_restore_blade', restore)
    job['dynamics_rig']['baked'] = baked
    module.end(job)
    assert 'dynamics_rig' not in job
    assert_rigid(dynamics)


def test_bake_failure_after_allocation_still_has_owned_mesh(dynamics, monkeypatch):
    module, job = dynamics.module, {}
    class Mesh(dict):
        users = 0
    mesh = Mesh()
    original = Mesh()
    original.name = 'original'
    dynamics.blades[0].data = original
    monkeypatch.setattr(module.bpy.data.meshes, 'new_from_object', lambda *_a, **_k: mesh)
    dynamics.blades[0].evaluated_get = lambda _graph: None
    def fail_span(_mesh):
        assert job['dynamics_rig']['baked'] is mesh
        raise RuntimeError('span failed')
    monkeypatch.setattr(module, '_span', fail_span)
    removed = []
    monkeypatch.setattr(module.bpy.data.meshes, 'remove', removed.append)
    with pytest.raises(RuntimeError, match='span failed'):
        module.start(NS(wfrl_manual_turbines=[dynamics.entry]), job, 'T1', seed=1)
    assert removed == [mesh] and 'dynamics_rig' not in job


def test_publish_cleanup_error_retains_only_owned_directories(down, monkeypatch, tmp_path):
    video = down.video_output
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'encoded')
    target, sidecar = tmp_path / 'out.mp4', tmp_path / 'out.json'
    cleanup = {}
    remove = video.shutil.rmtree
    def fail(path, **_kwargs):
        raise PermissionError('staging locked')
    monkeypatch.setattr(video.shutil, 'rmtree', fail)
    with pytest.raises(RuntimeError, match='暂存清理失败') as error:
        video.publish_staged([(source, target)], sidecar, {}, 'Down 视频', cleanup=cleanup)
    assert target.read_bytes() == b'encoded' and sidecar.is_file()
    assert len(cleanup) == 2 and all(str(path) in str(error.value) for path in cleanup)
    monkeypatch.setattr(video.shutil, 'rmtree', remove)
    video.cleanup_owned_directories(cleanup)
    assert not cleanup and not list(tmp_path.glob('.wfrl-down-*'))


def test_publication_backup_is_not_a_cleanup_retry(down, monkeypatch, tmp_path):
    video, cleanup = down.video_output, {}
    source, target, sidecar = (tmp_path / name for name in ('source.mp4', 'out.mp4', 'out.json'))
    source.write_bytes(b'new')
    target.write_bytes(b'old')
    commit = video._commit_export
    def conflict(src, dst):
        if Path(dst) == target:
            target.write_bytes(b'external')
        commit(src, dst)
    monkeypatch.setattr(video, '_commit_export', conflict)
    with pytest.raises(RuntimeError, match='恢复失败'):
        video.publish_staged([(source, target)], sidecar, {}, 'Down 视频', cleanup=cleanup)
    assert not cleanup
    backups = list(tmp_path.glob('.wfrl-down-*/previous'))
    assert len(backups) == 1 and backups[0].read_bytes() == b'old'
    assert target.read_bytes() == b'external'
