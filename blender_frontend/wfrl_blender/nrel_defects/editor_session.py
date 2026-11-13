"""Defect edit transactions, session lifetime and RNA data synchronization."""
import copy
import json
import logging
import math
import time
import numpy as np
from .reference_surface import ReferenceSurface
from .defect_schema import KIND_LABELS, PRESETS, validate_document
from .defect_store import DefectStore
from .editor_geometry import EditorGeometry
from .editor_persistence import EditorPersistence

_LOG = logging.getLogger(__name__)


class _TimeState:
    """Retain displayed geometry without relying on a failed deformation to rerun."""

    def __init__(self, session):
        from .. import clearance_replay, tip_tracking
        self.session = session
        scene, flex = session.scene, session.flex
        self.frame = getattr(scene, 'frame_current', None)
        self.subframe = getattr(scene, 'frame_subframe', 0.)
        self.records = getattr(session, 'records', None)
        self.properties = {key: scene[key] for key in scene.keys() if self._time_property(key)}
        self.reader = clearance_replay._READERS.get(scene.as_pointer())
        self.attributes = []

        def retain(owner, names):
            for name in names:
                if hasattr(owner, name):
                    value = getattr(owner, name)
                    if name == 'rotation_axis_angle':
                        value = tuple(value)
                    else:
                        value = value.copy() if hasattr(value, 'copy') else copy.copy(value)
                    self.attributes.append((owner, name, value))

        retain(scene, ('wfrl_clearance_show_config',))
        retain(flex, ('enabled', 'frame_error', 'last_telemetry_frame'))
        self.visibility, self.curves, self.materials, self.beams = [], [], [], []
        for obj in getattr(scene, 'objects', ()):
            retain(obj, ('location', 'rotation_euler', 'rotation_quaternion',
                         'rotation_axis_angle', 'scale', 'hide_viewport', 'hide_render'))
            if hasattr(obj, 'hide_get'):
                self.visibility.append((obj, obj.hide_get()))
            if getattr(obj, 'type', None) == 'CURVE':
                curve = obj.data
                if curve is not None and all(s.type == 'POLY' for s in curve.splines):
                    splines = []
                    for spline in curve.splines:
                        points = np.empty(len(spline.points) * 4, dtype=np.float32)
                        spline.points.foreach_get('co', points)
                        splines.append((points, spline.use_cyclic_u, spline.material_index))
                    self.curves.append((curve, splines))
                    retain(curve, ('bevel_depth',))
                    if hasattr(curve, 'materials'):
                        self.materials.append((curve.materials, tuple(curve.materials)))
            if hasattr(obj, 'keys') and 'wfrl_beam_active' in obj:
                self.beams.append((obj, obj['wfrl_beam_active']))
        trail = tip_tracking.active(scene)
        if trail is not None:
            for name in ('points', 'times', 'completed', 'last_frame', 'last_time',
                         'last_angle', 'turn_angle', 'compare_since', '_geometry_revision', '_drawn'):
                self.attributes.append((trail, name, copy.deepcopy(getattr(trail, name))))
        objects = [row[0] for row in (*getattr(flex, 'blades', ()), *getattr(flex, 'towers', ()))]
        objects.extend(getattr(session, 'helpers', ()))
        objects.append(getattr(session, 'repair_source', None))
        comparison = getattr(flex, 'comparison', None)
        if comparison is not None:
            retain(comparison, ('rows', 'labels'))
            objects.extend(comparison.objects)
        self.meshes = []
        seen = set()
        for obj in objects:
            if obj is None:
                continue
            try:
                mesh = obj.data
            except ReferenceError:
                # Removed helper objects are also ignored by update_attachments.
                continue
            if mesh is None or not hasattr(mesh, 'vertices'):
                continue
            identity = mesh.as_pointer() if hasattr(mesh, 'as_pointer') else id(mesh)
            if identity in seen:
                continue
            seen.add(identity)
            coordinates = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            mesh.vertices.foreach_get('co', coordinates)
            self.meshes.append((mesh, coordinates))

    @staticmethod
    def _time_property(key):
        return (key.startswith(('defect_', 'wfrl_clearance_', 'wfrl_flex_', 'wfrl_tip_trail_'))
                or key == 'wfrl_home_load_error')

    @staticmethod
    def _restore_curve(curve, saved):
        if (len(curve.splines) != len(saved) or
                any(spline.type != 'POLY' or len(spline.points) * 4 != len(points)
                    for spline, (points, _cyclic, _material) in zip(curve.splines, saved))):
            curve.splines.clear()
            for points, _cyclic, _material in saved:
                curve.splines.new('POLY').points.add(len(points) // 4 - 1)
        for spline, (points, cyclic, material) in zip(curve.splines, saved):
            spline.points.foreach_set('co', points)
            spline.use_cyclic_u, spline.material_index = cyclic, material

    def restore(self, error):
        from .. import clearance_replay
        scene, flex = self.session.scene, self.session.flex
        self.session.records = self.records

        def attempt(operation):
            try:
                operation()
            except BaseException as rollback_error:
                error.add_note('Defect time restoration failed: ' + str(rollback_error))
                _LOG.exception('Defect time restoration failed')

        if self.frame is not None:
            attempt(lambda: scene.frame_set(self.frame, subframe=self.subframe))
            attempt(lambda: setattr(scene, 'frame_current', self.frame))
            attempt(lambda: setattr(scene, 'frame_subframe', self.subframe))
        for owner, name, value in self.attributes:
            attempt(lambda owner=owner, name=name, value=value: setattr(owner, name, value))
        for obj, hidden in self.visibility:
            attempt(lambda obj=obj, hidden=hidden: obj.hide_set(hidden))
        for mesh, coordinates in self.meshes:
            attempt(lambda mesh=mesh, coordinates=coordinates: mesh.vertices.foreach_set('co', coordinates))
            attempt(lambda mesh=mesh: mesh.update())
        for curve, splines in self.curves:
            attempt(lambda curve=curve, splines=splines: self._restore_curve(curve, splines))
        for materials, saved in self.materials:
            if tuple(materials) != saved:
                attempt(lambda materials=materials: materials.clear())
                for material in saved:
                    attempt(lambda materials=materials, material=material: materials.append(material))
        for obj, active in self.beams:
            attempt(lambda obj=obj, active=active: obj.__setitem__('wfrl_beam_active', active))
        for key in tuple(scene.keys()):
            if self._time_property(key) and key not in self.properties:
                attempt(lambda key=key: scene.pop(key))
        for key, value in self.properties.items():
            attempt(lambda key=key, value=value: scene.__setitem__(key, value))
        if self.reader is None:
            clearance_replay._READERS.pop(scene.as_pointer(), None)
        else:
            clearance_replay._READERS[scene.as_pointer()] = self.reader
        # Matrices and interpolated geometry can contain partially written poses.
        for name in ('_world_matrices', '_update_geometry'):
            if hasattr(flex, name):
                setattr(flex, name, None)


class EditorSession(EditorGeometry, EditorPersistence):

    def __init__(self, scene, document=None):
        from . import editor as _editor
        self.scene = scene
        self._initial_properties = {key: scene[key] for key in scene.keys()
            if key.startswith(('defect_', 'wfrl_nrel_defect'))}
        props = scene.wfrl_nrel_defects
        self._initial_rows = [(item.defect_id, item.name, item.enabled) for item in props.defect_items]
        self._initial_rna = {key: getattr(props, key) for key in ('selected', 'time_s', 'scan_start')}
        _editor._INITIALIZING[scene.as_pointer()] = self
        try:
            self._initialize(scene, document)
            self.geometry.finish_initial()
        except Exception:
            try:
                self.rollback_initial()
            except Exception:
                _LOG.exception('Defect editor initialization failed; rollback awaits retry')
            raise
        _editor._INITIALIZING.pop(scene.as_pointer(), None)
        _editor._SESSIONS[scene.as_pointer()] = self

    def rollback_initial(self):
        from . import editor as _editor
        if hasattr(self, 'helpers'):
            self.clear_helpers()
        if hasattr(self, 'geometry'):
            self.geometry.rollback_initial()
        for key in list(self.scene.keys()):
            if key.startswith(('defect_', 'wfrl_nrel_defect')) and key not in self._initial_properties:
                del self.scene[key]
        for key, value in self._initial_properties.items():
            self.scene[key] = value
        props = self.scene.wfrl_nrel_defects
        props.defect_items.clear()
        for defect_id, name, enabled in self._initial_rows:
            item = props.defect_items.add()
            item.defect_id, item.name, item.enabled = defect_id, name, enabled
        for key, value in self._initial_rna.items():
            setattr(props, key, value)
        _editor._INITIALIZING.pop(self.scene.as_pointer(), None)

    def _initialize(self, scene, document=None):
        from . import editor as _editor
        from .. import farm_flex, clearance_replay
        if not farm_flex.is_active(scene):
            raise ValueError('请先加载 NREL 5MW MAPPO 柔性回放')
        if farm_flex.active_for(scene).model != 'nrel5mw':
            raise ValueError('NREL 缺陷编辑器需要 NREL 叶片外形，请先切回 NREL 5MW')
        self.scene, self.flex = scene, farm_flex.active_for(scene)
        self.baseline = {obj.name:rest.copy() for obj,rest,*_ in self.flex.blades}
        obj, rest, *_ = self.flex.blades[0]
        self.surface = ReferenceSurface(rest.tolist(), [tuple(p.vertices) for p in obj.data.polygons],
            ring_points=48, metadata=dict(turbine_ids=list(self.flex.readers),
            reference_state='NREL frontend rest loft; existing prebend retained',
            prebend_reference=self.flex.reference))
        if document is not None:
            document = validate_document(document, self.surface)
        self.rig = dict(scene=scene, collection=obj.users_collection[0], editor=self,
            motion=dict(fps=60., start_s=float(self.flex.times[0]), end_s=float(self.flex.times[-1])),
            geometry=self.surface.metadata())
        self.payload = None
        self.records = None
        self.preview = None
        self.draft = None
        self.edit_state = None
        self.helpers = []
        self.helper_rest = {}
        self.commit_times = []
        self.helper_meshes = []
        self.closing = False
        self.busy = False
        self.healthy_payload = None
        self.save_state = None
        self.geometry = _editor.GeometrySet.__new__(_editor.GeometrySet)
        _editor.GeometrySet.__init__(self.geometry, self)
        self.repair_source = scene.objects.get('WFRL.RepairReference.T1.B1')
        self.repair_rest = self.baseline.get('WFRL.Turbine.T1.Blade1')
        self.camera_signature = None
        self.store = DefectStore(self.surface, document=document, prepare=self.geometry.prepare,
            activate=self._activate, dispose=self.geometry.dispose)
        self.scene['wfrl_nrel_defect_enabled'] = True
        self.scene['defect_time_s'] = clearance_replay.sample(scene)['time_s']
        self.update_helpers()
        p = scene.wfrl_nrel_defects
        p.time_s = p.scan_start = self.scene['defect_time_s']

    def blade(self, turbine_id, blade_id):
        return self.scene.objects[f'WFRL.Turbine.{turbine_id}.Blade{blade_id}']

    def commit_document(self, document):
        token = self.store.prepare(document)
        try: return self.store.commit(token)
        except BaseException:
            self.store._cancel_after_error(token)
            raise

    def _activate(self, payload, document, version):
        from . import editor as _editor
        self.geometry.activate(payload,document,version)
        self.payload = payload
        self.records = None
        self.scene[_editor.DOCUMENT_KEY] = json.dumps(document, allow_nan=False)
        self.scene['defect_state_version'] = version
        self.scene['defect_analysis_status'] = 'NOT_EVALUATED'
        self.update_helpers(document)
        _editor.sync_defect_list(self, document)

    def set_time(self, t):
        """Commit a seek only after deformation succeeds; restore display on failure."""
        from . import editor as _editor
        if not math.isfinite(t) or not self.flex.times[0] <= t <= self.flex.times[-1]:
            raise ValueError('时刻超出 NREL 源数据时间线')
        previous = _TimeState(self)
        updating, _editor._UPDATING = _editor._UPDATING, True
        try:
            f=1+(t-self.flex.times[0])*60
            self.scene.frame_set(math.floor(f),subframe=f-math.floor(f))
            visible = self.flex.visible_turbines
            try:
                # G occlusion uses every renderable turbine, including hidden
                # viewport meshes skipped by ordinary playback optimization.
                self.flex.visible_turbines = set(range(len(self.flex.readers)))
                self.flex.update(self.scene,sim_time_s=t,record_telemetry=False)
            finally:
                self.flex.visible_turbines = visible
            self.scene['defect_time_s']=t
            self.records=None
            self.scene['defect_analysis_status']='NOT_EVALUATED'
        except BaseException as error:
            try:
                previous.restore(error)
            except BaseException as rollback_error:
                error.add_note('Defect time restoration failed: ' + str(rollback_error))
                _LOG.exception('Defect time restoration failed')
            raise
        finally: _editor._UPDATING=updating

    def begin(self, index=None, kind='fine_crack'):
        from . import editor as _editor
        from .. import playback
        if self.draft is not None or self.healthy_payload is not None or self.busy:
            raise ValueError('请先完成当前编辑、扫描或健康对照')
        draft = self.store.document
        if index is not None and not 0<=index<len(draft['defects']):
            raise ValueError('请选择已有缺陷')
        p = self.scene.wfrl_nrel_defects
        self.edit_selection = (p.defect_items[p.selected].defect_id
                               if 0 <= p.selected < len(p.defect_items) else None)
        if index is None:
            draft['defects'].append(_editor.new_defect(self.surface,kind,turbine_id=p.turbine_id))
            index=len(draft['defects'])-1
        window = None
        if playback.is_playing(self.scene):
            window = playback.owner_window()
            if window is None or window.scene != self.scene:
                raise RuntimeError('无法确定当前场景的播放窗口')
            playback.cancel(self.scene)
            if playback.is_playing(self.scene):
                raise RuntimeError('无法暂停当前场景播放')
        self.edit_state=(float(self.scene['defect_time_s']),window)
        self.draft, self.draft_index = draft, index
        try:
            _editor.sync_defect_list(self, draft, draft['defects'][index]['id'])
            return draft['defects'][index]
        except BaseException:
            self.draft = None
            try:
                _editor.sync_defect_list(self, self.store.document, self.edit_selection)
            except Exception:
                _LOG.exception('Defect edit failed; list restoration also failed')
            try:
                self._restore_edit_state()
            except Exception:
                _LOG.exception('Defect edit failed; playback restoration also failed')
            raise

    def _restore_edit_state(self):
        """Restore edit time and resume only the original scene's surviving window."""
        from . import editor as _editor
        from .. import playback
        state, self.edit_state = self.edit_state, None
        if state is None:
            return
        t, window = state
        with _editor.bpy.context.temp_override(scene=self.scene, view_layer=self.scene.view_layers[0]):
            self.set_time(t)
        if window is None:
            return
        try:
            def can_resume():
                owner = playback.owner_scene()
                return (_editor._SESSIONS.get(self.scene.as_pointer()) is self
                        and window in tuple(_editor.bpy.context.window_manager.windows)
                        and window.scene == self.scene
                        and (owner is None or owner == self.scene)
                        and not playback.any_playing())
            if can_resume():
                with _editor.bpy.context.temp_override(window=window):
                    if can_resume():
                        _editor.bpy.ops.screen.animation_play()
        except (ReferenceError, RuntimeError):
            _LOG.warning('Defect edit playback could not resume', exc_info=True)

    def discard_preview(self):
        if self.preview is not None:
            self.geometry.activate(self.payload)
            previous, self.preview = self.preview, None
            self.store._cancel_after_error(previous)
            self.update_helpers(self.store.document)

    def preview_draft(self):
        previous = self.preview
        candidate = self.store.prepare(self.draft)
        try:
            self.geometry.activate(candidate.resource)
            self.update_helpers(candidate.document)
        except BaseException:
            try:
                self.geometry.activate(previous.resource if previous is not None else self.payload)
                self.update_helpers(previous.document if previous is not None else self.store.document)
            except Exception:
                _LOG.exception('Defect preview failed; display restoration also failed')
            self.store._cancel_after_error(candidate)
            raise
        self.preview = candidate
        if previous is not None:
            self.store._cancel_after_error(previous)

    def finish(self, commit=False):
        from . import editor as _editor
        if self.draft is None: return
        self.discard_preview()
        if commit:
            started=time.perf_counter()
            self.commit_document(self.draft)
            self.commit_times.append(time.perf_counter()-started)
        selected_id = self.draft['defects'][self.draft_index]['id'] if commit else self.edit_selection
        self.draft=None
        try:
            _editor.sync_defect_list(self, self.store.document, selected_id)
        except BaseException:
            try:
                self._restore_edit_state()
            except Exception:
                _LOG.exception('Defect list synchronization failed; playback restoration also failed')
            raise
        else:
            self._restore_edit_state()

    def mutate(self, operation, index):
        if self.draft is not None: raise ValueError('请先确认或取消编辑')
        doc=self.store.document
        if not 0<=index<len(doc['defects']): raise ValueError('请选择已有缺陷')
        item=doc['defects'][index]
        if operation=='DELETE': del doc['defects'][index]
        elif operation=='TOGGLE':
            item['enabled']=not item['enabled']
            item['revision']+=1
        elif operation=='COPY':
            import uuid
            copied=copy.deepcopy(item)
            copied.update(id='D'+uuid.uuid4().hex,revision=1,enabled=False)
            doc['defects'].append(copied)
        self.commit_document(doc)

    def healthy(self,index):
        if self.draft is not None: raise ValueError('请先确认或取消编辑')
        if self.healthy_payload is not None:
            old=self.healthy_payload
            self.geometry.activate(self.payload)
            self.healthy_payload=None
            self.geometry.dispose(old)
            self.update_helpers()
            return
        doc=self.store.document
        target=doc['defects'][index]
        for d in doc['defects']:
            if (d['turbine_id'],d['blade_id'])==(target['turbine_id'],target['blade_id']): d['enabled']=False
        payload=self.geometry.prepare(doc)
        try:
            self.geometry.activate(payload)
        except BaseException:
            # The committed geometry stays shown; release the unused comparison.
            try:
                self.geometry.dispose(payload)
            except Exception:
                _LOG.exception('Healthy comparison failed; resource cleanup also failed')
            raise
        self.healthy_payload=payload
        for helper in self.live_helpers(): helper.hide_viewport=True


def ensure(scene):
    from . import editor as _editor
    pending = _editor._INITIALIZING.get(scene.as_pointer())
    if pending is not None:
        pending.rollback_initial()
    found=_editor._SESSIONS.get(scene.as_pointer())
    if found:
        if not getattr(found, 'closing', False): return found
        _editor.detach(scene)
    document=json.loads(scene[_editor.DOCUMENT_KEY]) if scene.get(_editor.DOCUMENT_KEY) else None
    return _editor.EditorSession(scene,document)


def after_update(scene,t):
    from . import editor as _editor
    found=_editor._SESSIONS.get(scene.as_pointer())
    if found and not getattr(found, 'closing', False):
        found.update_attachments(t)
        scene['defect_time_s']=float(t)
        found.records=None
        scene['defect_analysis_status']='NOT_EVALUATED'


def detach(scene=None,*,restore=True):
    from . import editor as _editor
    errors = []
    for pending in list(_editor._INITIALIZING.values()):
        if scene is None or pending.scene == scene:
            try:
                pending.rollback_initial()
            except Exception as exc:
                errors.append(exc)
    for key,s in list(_editor._SESSIONS.items()):
        if scene is not None and s.scene != scene: continue
        s.closing = True
        failed = []
        def attempt(callback):
            try:
                callback()
            except Exception as exc:
                failed.append(exc)
        scan = getattr(s, 'scan_operator', None)
        if scan is not None:
            attempt(lambda: scan.finish_scan(None, True, restore_time=restore, resume_playback=False))
        s.draft = s.edit_state = None
        attempt(s.clear_helpers)
        if hasattr(s.geometry, 'retry_originals'):
            attempt(lambda: s.geometry.retry_originals(strict=True))
        attempt(lambda: s.store.retry_disposals(strict=True))
        for manager in s.geometry.managers.values():
            attempt(lambda manager=manager: manager.close(restore=restore))
        # Geometry managers own restoration. Closing must not reactivate a live
        # preview or access a scene whose RNA has already been deleted.
        s.geometry.active = None
        for payload in (getattr(s, 'payload', None), s.healthy_payload,
                        s.preview.resource if s.preview is not None else None,
                        *list(getattr(s.geometry, 'pending', []))):
            if payload is not None:
                attempt(lambda payload=payload: s.geometry.dispose(payload))
        if not failed and restore:
            def restore_replay():
                for row in list(s.flex.blades):
                    obj = row[0]
                    try:
                        name = obj.name
                    except ReferenceError:
                        continue
                    if name in s.baseline: s.flex.rebind_blade(obj,s.baseline[name])
                try:
                    scene_live = bool(s.scene.as_pointer())
                except ReferenceError:
                    scene_live = False
                if scene_live:
                    from .. import repair_marks
                    repair_marks.ensure(s.scene,force=True)
                    s.flex.update(s.scene,record_telemetry=False)
            attempt(restore_replay)
        if failed:
            errors.extend(failed)
            try:
                s.scene.wfrl_nrel_defects.status = '关闭未完成，请重试关闭：' + '; '.join(map(str, failed))
            except ReferenceError:
                pass
            continue
        s.preview = s.healthy_payload = None
        _editor._SESSIONS.pop(key,None)
    if errors:
        raise RuntimeError('缺陷编辑器清理失败：' + '; '.join(map(str, errors)))


def sync_defect_list(s, document, selected_id=None):
    """Refresh display rows at transaction boundaries, preserving object identity."""
    p = s.scene.wfrl_nrel_defects
    previous_index = p.selected
    if selected_id is None and 0 <= previous_index < len(p.defect_items):
        selected_id = p.defect_items[previous_index].defect_id
    p.defect_items.clear()
    for d in document['defects']:
        item = p.defect_items.add()
        item.defect_id = d['id']
        item.name = f"{d['turbine_id']} · 叶片 {d['blade_id']} · {KIND_LABELS[d['morphology']]}"
        item.enabled = d['enabled']
    p.selected = next((i for i, item in enumerate(p.defect_items)
                       if item.defect_id == selected_id),
                      min(max(previous_index, 0), len(p.defect_items)-1))


def props_from_defect(p, d):
    p.turbine_id = d['turbine_id']
    p.blade_id, p.side = d['blade_id'], d['anchor']['surface_side']
    p.span, p.u, p.theta = d['anchor']['s_m'], d['anchor']['u'], d['anchor']['theta_deg']
    p.length, p.width = d['shape']['length_m'], d['shape']['max_width_m']
    p.depth, p.seed, p.enabled = d['shape']['depth_m'], d['shape']['seed'], d['enabled']
    p.edge_roughness = d['shape'].get('edge_roughness', 0.)
    p.crater_fraction = d['shape'].get('crater_fraction', PRESETS['lightning']['crater_fraction'])
    p.kind = d['morphology']


def defect_from_props(s, p):
    """Read the current input as an independent candidate without changing the preview."""
    d = copy.deepcopy(s.draft['defects'][s.draft_index])
    preset = PRESETS[d['morphology']]
    d['turbine_id'] = p.turbine_id
    d['blade_id'], d['enabled'] = p.blade_id, p.enabled
    d['anchor'].update(s_m=p.span, u=p.u, surface_side=p.side, theta_deg=p.theta % 360)
    d['shape'].update(length_m=p.length, max_width_m=p.width,
                      depth_m=0. if preset['representation'] == 'appearance' else p.depth, seed=p.seed)
    if 'edge_roughness' in preset:
        d['shape']['edge_roughness'] = p.edge_roughness
    if 'crater_fraction' in preset:
        d['shape']['crater_fraction'] = p.crater_fraction
    old = next((i for i in s.store.document['defects'] if i['id'] == d['id']), None)
    if old:
        d['revision'] = old['revision'] + 1
    return d


def draft_from_props(s, p):
    from . import editor as _editor
    s.draft['defects'][s.draft_index] = _editor.defect_from_props(s, p)


def preview_is_current(s, p):
    """Compare displayed parameters at the precision stored by RNA float inputs."""
    from . import editor as _editor
    if s.draft is None:
        return True
    candidate = _editor.defect_from_props(s, p)
    document = s.preview.document if s.preview is not None else s.store.document
    displayed = next((d for d in document['defects'] if d['id'] == candidate['id']), None)
    if displayed is None:
        return False

    def rounded(value):
        if isinstance(value, float):
            return float(np.float32(value))
        if isinstance(value, dict):
            return {key: rounded(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rounded(item) for item in value]
        return value

    return rounded({key: value for key, value in candidate.items() if key != 'revision'}) == rounded(
        {key: value for key, value in displayed.items() if key != 'revision'})
