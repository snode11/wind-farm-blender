"""NREL six-morphology editor bound to the native recorded flexible replay."""
import copy
import json
import math
import time
from pathlib import Path
import bpy
import numpy as np
from bpy.app.handlers import persistent
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from .reference_surface import ReferenceSurface
from .defect_schema import KIND_DESCRIPTIONS, KIND_LABELS, PRESETS, new_document, new_defect, validate_document, save_document
from .defect_shapes import lightning_crater
from .defect_store import DefectStore
from .defect_geometry import DefectGeometry

_SESSIONS = {}
_UPDATING = False
DOCUMENT_KEY = 'wfrl_nrel_defect_document'


def session(context=None):
    return _SESSIONS.get((context or bpy.context).scene.as_pointer())


def redraw_editor(context):
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()


class GeometrySet:
    def __init__(self, editor):
        self.editor = editor
        self.managers = {}
        for tid in editor.surface.turbine_ids:
            rig = dict(scene=editor.scene, collection=editor.rig['collection'],
                       blades=[editor.blade(tid,b) for b in (1,2,3)], turbine_id=tid)
            self.managers[tid] = DefectGeometry(rig, editor.surface)
        self.active = None

    @property
    def boolean_calls(self):
        return sum(m.boolean_calls for m in self.managers.values())

    def prepare(self, document):
        payload = dict(document=copy.deepcopy(document), turbines={}, rest={}, supports=[], measurements=[])
        try:
            for tid, manager in self.managers.items():
                child = manager.prepare(document)
                payload['turbines'][tid] = child
                for bid, mesh in child['meshes'].items():
                    coords = np.empty(len(mesh.vertices)*3, np.float32)
                    mesh.vertices.foreach_get('co', coords)
                    payload['rest'][(tid,bid)] = coords.reshape(-1,3).copy()
                for field in ('supports','measurements'):
                    for item in child[field]:
                        payload[field].append(dict(item, turbine_id=tid))
            return payload
        except Exception:
            self.dispose(payload)
            raise

    def activate(self, payload, document=None, version=None):
        previous = self.active
        try:
            for tid, manager in self.managers.items():
                manager.activate(payload['turbines'][tid], document, version)
                for bid in (1,2,3):
                    self.editor.flex.rebind_blade(self.editor.blade(tid,bid), payload['rest'][(tid,bid)])
            self.active = payload
            self.editor.flex.update(self.editor.scene, record_telemetry=False)
        except Exception:
            if previous is not None:
                for tid, manager in self.managers.items():
                    manager.activate(previous['turbines'][tid])
                    for bid in (1,2,3):
                        self.editor.flex.rebind_blade(self.editor.blade(tid,bid), previous['rest'][(tid,bid)])
                self.active = previous
                self.editor.flex.update(self.editor.scene, record_telemetry=False)
            raise
        return previous

    def dispose(self, payload):
        if payload is None or payload is self.active: return
        for tid, child in payload.get('turbines',{}).items():
            self.managers[tid].dispose(child)


class EditorSession:
    def __init__(self, scene, document=None):
        from .. import farm_flex, clearance_replay
        if not farm_flex.is_active(scene):
            raise ValueError('请先加载 NREL 5MW MAPPO 柔性回放')
        self.scene, self.flex = scene, farm_flex._ACTIVE
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
        self.payload = None; self.records = None; self.preview = None; self.draft = None
        self.edit_state = None; self.helpers = []; self.helper_rest = {}; self.commit_times = []
        self.busy = False; self.healthy_payload = None
        self.geometry = GeometrySet(self)
        self.repair_source = scene.objects.get('WFRL.RepairReference.T1.B1')
        self.repair_rest = self.baseline.get('WFRL.Turbine.T1.Blade1')
        self.camera_signature = None
        self.store = DefectStore(self.surface, document=document, prepare=self.geometry.prepare,
            activate=self._activate, dispose=self.geometry.dispose)
        _SESSIONS[scene.as_pointer()] = self
        self.scene['wfrl_nrel_defect_enabled'] = True
        self.scene['defect_time_s'] = clearance_replay.sample(scene)['time_s']
        self.update_helpers()
        p = scene.wfrl_nrel_defects
        p.time_s = p.scan_start = self.scene['defect_time_s']

    def blade(self, turbine_id, blade_id):
        return self.scene.objects[f'WFRL.Turbine.{turbine_id}.Blade{blade_id}']

    def cameras(self):
        # Native custom-camera IDs, never rebuild or move the calibrated rig.
        from .. import custom_cameras
        return [camera for slot in (1,2,3) if (camera := custom_cameras.get_camera(self.scene,slot)) is not None]

    def camera(self, slot):
        from .. import custom_cameras
        camera = custom_cameras.get_camera(self.scene, slot)
        if camera is None:
            raise ValueError(f'C{slot} 相机缺失，请在三相机面板恢复该相机')
        return camera

    def camera_resolution(self, camera):
        from .. import camera_projection, custom_cameras
        p = custom_cameras.parameters(camera)
        return camera_projection.resolution(p.fov,p.vfov,p.output_long_edge_px)

    def fixed_camera_signature(self):
        return [(c.name, c.parent.name if c.parent else None,
            tuple(tuple(row) for row in c.matrix_basis), c.data.lens,c.data.sensor_width,c.data.sensor_height,
            c.data.sensor_fit,c.data.shift_x,c.data.shift_y,c.data.clip_start,c.data.clip_end,
            self.camera_resolution(c)) for c in self.cameras()]

    def verify_cameras(self):
        # A missing slot must not shift identities or silently reduce G/A to
        # fewer than the three cameras named in the editor's output contract.
        for slot in (1, 2, 3):
            self.camera(slot)
        current = self.fixed_camera_signature()
        if self.camera_signature is None: self.camera_signature = current
        if current != self.camera_signature:
            raise ValueError('相机安装或镜头已修改；关闭编辑器后重新打开以冻结新配置')

    def commit_document(self, document):
        token = self.store.prepare(document)
        try: return self.store.commit(token)
        except BaseException:
            if not token.consumed: self.store.cancel(token)
            raise

    def _activate(self, payload, document, version):
        self.geometry.activate(payload,document,version)
        self.payload = payload; self.records = None
        self.scene[DOCUMENT_KEY] = json.dumps(document, allow_nan=False)
        self.scene['defect_state_version'] = version
        self.scene['defect_analysis_status'] = 'NOT_EVALUATED'
        self.update_helpers(document)

    def update_helpers(self, document=None):
        for obj in self.helpers:
            data = obj.data
            bpy.data.objects.remove(obj,do_unlink=True)
            if data and data.users==0: bpy.data.meshes.remove(data)
        self.helpers = []; self.helper_rest = {}
        document = document or (self.store.document if hasattr(self,'store') else new_document(self.surface))
        props = self.scene.wfrl_nrel_defects
        def add(points, edges, d, role):
            mesh = bpy.data.meshes.new('NREL.Defect.Helper')
            mesh.from_pydata(points,edges,[]); mesh.update()
            obj = bpy.data.objects.new('NREL.Defect.Helper.'+d['id'],mesh)
            self.rig['collection'].objects.link(obj)
            obj.parent = self.blade(d['turbine_id'],d['blade_id'])
            obj.hide_render = True; obj.show_in_front = True
            obj['nrel_defect_role'] = 'editor_marker'; obj['turbine_id'] = d['turbine_id']; obj['blade_id'] = d['blade_id']
            obj['defect_id'] = d['id']; obj['outline'] = role
            obj.hide_viewport = not props.show_helpers or self.healthy_payload is not None
            self.helpers.append(obj); self.helper_rest[obj.name] = np.array(points)
        for d in document['defects']:
            if not d['enabled']: continue
            for shape,label in ((d,'support'),(lightning_crater(d),'crater')) if d['morphology']=='lightning' else ((d,'support'),):
                sampled = self.surface.sample_shape(shape)
                points = sampled['left']+list(reversed(sampled['right']))
                add(points,[(i,(i+1)%len(points)) for i in range(len(points))],d,label)
        self.update_attachments(float(self.scene.get('wfrl_clearance_time_s',self.flex.times[0])))

    def update_attachments(self, t):
        for obj in self.helpers:
            coords = self.flex.deform_points(obj.parent,self.helper_rest[obj.name],t)
            obj.data.vertices.foreach_set('co',coords.astype(np.float32).ravel()); obj.data.update()
        if self.repair_source is not None and len(self.repair_source.data.vertices)==len(self.repair_rest):
            coords = self.flex.deform_points(self.blade('T1',1),self.repair_rest,t)
            self.repair_source.data.vertices.foreach_set('co',coords.astype(np.float32).ravel());self.repair_source.data.update()

    def set_time(self, t):
        global _UPDATING
        if not math.isfinite(t) or not self.flex.times[0] <= t <= self.flex.times[-1]:
            raise ValueError('时刻超出 NREL 源数据时间线')
        _UPDATING=True
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
            self.scene['defect_time_s']=t; self.records=None
            self.scene['defect_analysis_status']='NOT_EVALUATED'
        finally: _UPDATING=False

    def begin(self, index=None, kind='fine_crack'):
        if self.draft is not None or self.healthy_payload is not None or self.busy:
            raise ValueError('请先完成当前编辑、扫描或健康对照')
        if index is not None and not 0<=index<len(self.store.document['defects']):
            raise ValueError('请选择已有缺陷')
        playing=bool(bpy.context.screen and bpy.context.screen.is_animation_playing)
        if playing: bpy.ops.screen.animation_cancel(restore_frame=False)
        self.edit_state=(float(self.scene['defect_time_s']),playing)
        self.draft=self.store.document
        if index is None:
            self.draft['defects'].append(new_defect(self.surface,kind,turbine_id=self.scene.wfrl_nrel_defects.turbine_id))
            index=len(self.draft['defects'])-1
        self.draft_index=index
        return self.draft['defects'][index]

    def discard_preview(self):
        if self.preview is not None:
            self.geometry.activate(self.payload)
            self.store.cancel(self.preview); self.preview=None
            self.update_helpers(self.store.document)

    def preview_draft(self):
        self.discard_preview()
        self.preview=self.store.prepare(self.draft)
        self.geometry.activate(self.preview.resource)
        self.update_helpers(self.draft)

    def finish(self, commit=False):
        if self.draft is None: return
        self.discard_preview()
        if commit:
            started=time.perf_counter();self.commit_document(self.draft)
            self.commit_times.append(time.perf_counter()-started)
        self.draft=None
        t,playing=self.edit_state;self.edit_state=None
        self.set_time(t)
        if playing and bpy.context.screen and not bpy.context.screen.is_animation_playing: bpy.ops.screen.animation_play()

    def mutate(self, operation, index):
        if self.draft is not None: raise ValueError('请先确认或取消编辑')
        doc=self.store.document
        if not 0<=index<len(doc['defects']): raise ValueError('请选择已有缺陷')
        item=doc['defects'][index]
        if operation=='DELETE': del doc['defects'][index]
        elif operation=='TOGGLE': item['enabled']=not item['enabled'];item['revision']+=1
        elif operation=='COPY':
            import uuid
            copied=copy.deepcopy(item);copied.update(id='D'+uuid.uuid4().hex,revision=1,enabled=False);doc['defects'].append(copied)
        self.commit_document(doc)

    def save(self,path):
        if self.draft is not None: raise ValueError('请先确认或取消编辑')
        save_document(path,self.store.document,self.surface)

    def load(self,path):
        if self.draft is not None: raise ValueError('请先确认或取消编辑')
        self.store.restore(validate_document(json.loads(Path(path).read_text()),self.surface))

    def healthy(self,index):
        if self.draft is not None: raise ValueError('请先确认或取消编辑')
        if self.healthy_payload is not None:
            old=self.healthy_payload;self.geometry.activate(self.payload);self.healthy_payload=None
            self.geometry.dispose(old);self.update_helpers();return
        doc=self.store.document
        target=doc['defects'][index]
        for d in doc['defects']:
            if (d['turbine_id'],d['blade_id'])==(target['turbine_id'],target['blade_id']): d['enabled']=False
        self.healthy_payload=self.geometry.prepare(doc)
        self.geometry.activate(self.healthy_payload)
        for helper in self.helpers: helper.hide_viewport=True

    def locate_ray(self,origin,direction,blade_id,turbine_id=None):
        blade=self.blade(turbine_id or self.scene.wfrl_nrel_defects.turbine_id,blade_id)
        rest=np.asarray(self.surface.vertices)
        world=self.flex.deform_points(blade,rest,self.scene['defect_time_s'],world=True)
        tree=BVHTree.FromPolygons(world.tolist(),self.surface.triangles,all_triangles=True)
        p,_,face,_=tree.ray_cast(Vector(origin),Vector(direction).normalized())
        if p is None: raise ValueError('未命中所选叶片的健康参考面')
        from mathutils.geometry import barycentric_transform
        ids=self.surface.triangles[face]
        local=barycentric_transform(p,*(Vector(world[i]) for i in ids),*(Vector(rest[i]) for i in ids))
        return self.surface.locate(tuple(local))

    def closeup(self,index,oblique=False):
        d=(self.draft or self.store.document)['defects'][index]
        frame=self.surface.frame(d['anchor']);blade=self.blade(d['turbine_id'],d['blade_id'])
        p=Vector(frame['point']);n=Vector(frame['normal']);e=Vector(frame['e_t'])
        mapped=self.flex.deform_points(blade,[p,p+n*.01,p+e*.01],self.scene['defect_time_s'],world=True)
        target=Vector(mapped[0]);direction=Vector(mapped[1]-mapped[0]).normalized()
        if oblique: direction+=Vector(mapped[2]-mapped[0]).normalized()*.7
        for area in bpy.context.screen.areas if bpy.context.screen else []:
            if area.type=='VIEW_3D':
                r=area.spaces.active.region_3d;r.view_perspective='PERSP';r.view_location=target
                r.view_rotation=(-direction).to_track_quat('-Z','Y');r.view_distance=max(.35,3*d['shape']['length_m'])
                area.spaces.active.overlay.show_overlays=True
                area.spaces.active.shading.type='MATERIAL'

    def world_support(self,support,time_s):
        blade=self.blade(support['turbine_id'],support['blade_id'])
        def transform(points): return self.flex.deform_points(blade,points,time_s,world=True,high_precision=True).tolist()
        result=dict(support)
        triangles = np.asarray(support['triangles_local']).reshape(-1,3)
        result['triangles_world'] = self.flex.deform_points(blade,triangles,time_s,world=True,high_precision=True).reshape(-1,3,3).tolist()
        normals=[]
        for original,triangle,normal in zip(support['triangles_local'],result['triangles_world'],support['normals_local']):
            a,b,c=np.asarray(triangle,dtype=float)
            n=np.cross(b-a,c-a);length=np.linalg.norm(n)
            if length>0: n=n/length
            oa,ob,oc=np.asarray(original,dtype=float)
            if np.dot(np.cross(ob-oa,oc-oa),normal)<0: n=-n
            normals.append(tuple(n))
        result['normals_world']=normals
        result['centreline_world']=transform(support.get('centreline_local',[]))
        result['width_sections_world']=[transform(pair) for pair in support.get('width_sections_local',[])]
        return result

    def analyze(self,t=None,frame_index=None,run_id='',state_version=None,cancel=None):
        from .defect_visibility import analyze_support,scene_tree,_camera_descriptor,AnalysisCancelled
        self.verify_cameras()
        t=float(self.scene['defect_time_s']) if t is None else t
        self.set_time(t);tree=scene_tree(self.rig)
        def ray(origin,direction,maximum):
            return tree.ray_cast(Vector(origin),Vector(direction),maximum)[3] if tree else None
        rows=[]
        saved_resolution = (self.scene.render.resolution_x,self.scene.render.resolution_y,self.scene.render.resolution_percentage)
        descriptors = {}
        try:
            self.scene.render.resolution_percentage = 100
            for camera in self.cameras():
                self.scene.render.resolution_x,self.scene.render.resolution_y = self.camera_resolution(camera)
                descriptors[camera.name] = _camera_descriptor(camera,self.scene)
        finally:
            self.scene.render.resolution_x,self.scene.render.resolution_y,self.scene.render.resolution_percentage = saved_resolution
        for support in self.payload['supports']:
            world=self.world_support(support,t)
            for camera in self.cameras():
                if cancel and cancel(): raise AnalysisCancelled('Cancelled')
                descriptor = descriptors[camera.name]
                row=analyze_support(world,descriptor,ray,cancel=cancel)
                row.update(turbine_id=support['turbine_id'],run_id=run_id,state_version=self.store.version if state_version is None else state_version,
                    camera_id=camera.name,frame_index=frame_index,time_s=t,mode='G',camera_world_matrix=[list(r) for r in camera.matrix_world],
                    resolution=[descriptor['width'],descriptor['height']])
                rows.append(row)
        return rows


def ensure(scene):
    found=_SESSIONS.get(scene.as_pointer())
    if found: return found
    document=json.loads(scene[DOCUMENT_KEY]) if scene.get(DOCUMENT_KEY) else None
    return EditorSession(scene,document)


def after_update(scene,t):
    found=_SESSIONS.get(scene.as_pointer())
    if found:
        found.update_attachments(t);scene['defect_time_s']=float(t)
        found.records=None;scene['defect_analysis_status']='NOT_EVALUATED'


def restore_saved(scene):
    if scene.get('wfrl_nrel_defect_enabled') and scene.get(DOCUMENT_KEY):
        try: ensure(scene)
        except Exception as exc:
            scene['defect_analysis_status']='RESTORE_FAILED: '+str(exc)
            scene.wfrl_nrel_defects.status=str(exc)
            raise


def detach(scene=None,*,restore=True):
    for key,s in list(_SESSIONS.items()):
        if scene is not None and s.scene!=scene: continue
        _SESSIONS.pop(key,None)
        if s.preview is not None:
            s.geometry.activate(s.payload);s.store.cancel(s.preview);s.preview=None
        if s.healthy_payload is not None:
            s.geometry.activate(s.payload);s.geometry.dispose(s.healthy_payload);s.healthy_payload=None
        for obj in s.helpers:
            data=obj.data;bpy.data.objects.remove(obj,do_unlink=True)
            if data.users==0:bpy.data.meshes.remove(data)
        s.helpers=[]
        for manager in s.geometry.managers.values(): manager.close(restore=restore)
        if restore:
            for row in list(s.flex.blades):
                obj=row[0]
                if obj.name in s.baseline: s.flex.rebind_blade(obj,s.baseline[obj.name])
            from .. import repair_marks
            repair_marks.ensure(s.scene,force=True)
            s.flex.update(s.scene,record_telemetry=False)


@persistent
def save_pre(_unused):
    # Persist only committed JSON; previews and healthy comparisons are temporary.
    for s in list(_SESSIONS.values()):
        if s.draft is not None: s.finish(False)
        if s.healthy_payload is not None: s.healthy(0)


@persistent
def frame_changed(scene,depsgraph=None):
    s=_SESSIONS.get(scene.as_pointer())
    if s is not None and not _UPDATING and (s.draft is not None or s.busy):
        frozen = s.edit_state[0] if s.draft is not None else s.freeze_time
        if abs(float(scene.get('defect_time_s',0))-frozen)>1e-8: s.set_time(frozen)


def helper_changed(props,context):
    s=session(context)
    if s:
        for obj in s.helpers: obj.hide_viewport=not props.show_helpers or s.healthy_payload is not None

class NRELDefectProperties(bpy.types.PropertyGroup):
    selected: bpy.props.IntProperty(name='列表序号', default=0, min=0)
    kind: bpy.props.EnumProperty(name='类型', items=[
        (kind, KIND_LABELS[kind], KIND_DESCRIPTIONS[kind]) for kind in PRESETS])
    turbine_id: bpy.props.EnumProperty(name='机组', items=[(tid,tid,'') for tid in ('T1','T2','T3')], default='T1')
    blade_id: bpy.props.IntProperty(name='叶片', default=1, min=1, max=3)
    side: bpy.props.EnumProperty(name='参考侧', items=[('suction', '吸力面', ''), ('pressure', '压力面', '')])
    span: bpy.props.FloatProperty(name='展向 (m)', default=45., precision=4)
    u: bpy.props.FloatProperty(name='弦向 u', default=.4, precision=4)
    theta: bpy.props.FloatProperty(name='方向角 (°)', default=0.)
    length: bpy.props.FloatProperty(name='长度 (m)', default=.6, min=.001, precision=4)
    width: bpy.props.FloatProperty(name='宽度 (m)', default=.003, min=.00001, precision=5)
    depth: bpy.props.FloatProperty(name='深度 (m)', default=.015, min=0., precision=4)
    edge_roughness: bpy.props.FloatProperty(name='边缘不规则度', default=0., min=0., max=.5, precision=3)
    crater_fraction: bpy.props.FloatProperty(name='雷击坑口比例', default=.4, min=.15, max=.6, precision=3)
    seed: bpy.props.IntProperty(name='形态种子', default=17, min=0)
    enabled: bpy.props.BoolProperty(name='启用缺陷', default=True)
    show_helpers: bpy.props.BoolProperty(name='显示辅助标记', default=True, update=helper_changed)
    config_path: bpy.props.StringProperty(name='配置文件', subtype='FILE_PATH', default='//defects.json')
    output_path: bpy.props.StringProperty(name='新运行目录', subtype='DIR_PATH', default='//defect-run')
    time_s: bpy.props.FloatProperty(name='绝对时刻 (s)', default=117., min=0., max=100000.)
    scan_start: bpy.props.FloatProperty(name='扫描起点 (s)', default=117., min=0.)
    scan_duration: bpy.props.FloatProperty(name='扫描时长 (s)', default=.1, min=1/60)
    status: bpy.props.StringProperty(default='G 模式；外观尚未审核')


def props_from_defect(p, d):
    p.turbine_id = d['turbine_id']
    p.blade_id, p.side = d['blade_id'], d['anchor']['surface_side']
    p.span, p.u, p.theta = d['anchor']['s_m'], d['anchor']['u'], d['anchor']['theta_deg']
    p.length, p.width = d['shape']['length_m'], d['shape']['max_width_m']
    p.depth, p.seed, p.enabled = d['shape']['depth_m'], d['shape']['seed'], d['enabled']
    p.edge_roughness = d['shape'].get('edge_roughness', 0.)
    p.crater_fraction = d['shape'].get('crater_fraction', PRESETS['lightning']['crater_fraction'])
    p.kind = d['morphology']


def draft_from_props(s, p):
    d = s.draft['defects'][s.draft_index]
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


class NREL_OT_defect_action(bpy.types.Operator):
    bl_idname = 'wfrl.nrel_defect_action'
    bl_label = '叶片缺陷操作'
    action: bpy.props.StringProperty()

    def execute(self, context):
        s, p = session(context), context.scene.wfrl_nrel_defects
        try:
            if self.action == 'OPEN':
                ensure(context.scene); return {'FINISHED'}
            if self.action == 'CLOSE':
                if s and s.busy: raise ValueError('扫描进行中；按 Esc 取消后关闭')
                detach(context.scene); context.scene['wfrl_nrel_defect_enabled']=False; return {'FINISHED'}
            if s is None:
                raise ValueError('请先启动编辑器')
            if s.busy:
                raise ValueError('扫描进行中；按 Esc 取消后操作')
            if s.healthy_payload is not None and self.action != 'HEALTHY':
                raise ValueError('当前是健康对照，请先返回损伤状态')
            if self.action in ('NEW', 'EDIT'):
                d = s.begin(None if self.action == 'NEW' else p.selected, p.kind)
                p.selected = s.draft_index
                props_from_defect(p, d)
            elif self.action in ('PREVIEW', 'CONFIRM'):
                draft_from_props(s, p)
                s.preview_draft() if self.action == 'PREVIEW' else s.finish(True)
            elif self.action == 'CANCEL':
                s.finish(False)
            elif self.action in ('COPY', 'DELETE', 'TOGGLE'):
                s.mutate(self.action, p.selected)
            elif self.action in ('UNDO', 'REDO'):
                if s.draft is not None:
                    raise ValueError('请先取消编辑')
                getattr(s.store, self.action.lower())()
            elif self.action == 'SAVE':
                s.save(bpy.path.abspath(p.config_path))
            elif self.action == 'LOAD':
                s.load(bpy.path.abspath(p.config_path))
            elif self.action == 'TIME':
                if s.draft is not None:
                    raise ValueError('编辑期间时刻已冻结')
                s.set_time(p.time_s)
            elif self.action == 'HEALTHY':
                s.healthy(p.selected)
            elif self.action in ('CLOSEUP', 'OBLIQUE'):
                s.closeup(p.selected, self.action == 'OBLIQUE')
            elif self.action.startswith('CAMERA'):
                s.scene.camera = s.camera(int(self.action[-1]))
                for area in context.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.spaces.active.overlay.show_overlays = False
                        area.spaces.active.region_3d.view_perspective = 'CAMERA'
            elif self.action == 'ANALYZE':
                if s.draft is not None:
                    raise ValueError('请先确认或取消编辑')
                s.records = s.analyze()
                s.scene['defect_analysis_status'] = 'EVALUATED'
            elif self.action == 'EXPORT_G':
                from .defect_outputs import create_run, save_analysis
                run = create_run(s, bpy.path.abspath(p.output_path), mode='G')
                save_analysis(s, run)
            elif self.action == 'EXPORT_A':
                from .defect_outputs import capture_images
                capture_images(s,bpy.path.abspath(p.output_path))
            p.status = '已完成：' + self.action
            return {'FINISHED'}
        except Exception as exc:
            p.status = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            redraw_editor(context)


class NREL_OT_defect_pick(bpy.types.Operator):
    bl_idname = 'wfrl.nrel_defect_pick'
    bl_label = '点击健康参考表面'

    def invoke(self, context, event):
        s = session(context)
        if s is None or s.draft is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, '先新建或编辑缺陷，再在三维视口中选择位置')
            return {'CANCELLED'}
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set('左键选择健康参考表面；Esc 取消选点')
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            context.workspace.status_text_set(None)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            from bpy_extras import view3d_utils
            region = next(r for r in context.area.regions if r.type == 'WINDOW')
            coord = (event.mouse_x - region.x, event.mouse_y - region.y)
            r3d = context.space_data.region_3d
            origin = view3d_utils.region_2d_to_origin_3d(region, r3d, coord)
            direction = view3d_utils.region_2d_to_vector_3d(region, r3d, coord)
            try:
                p = context.scene.wfrl_nrel_defects
                a = session(context).locate_ray(origin, direction, p.blade_id,p.turbine_id)
                p.span, p.u, p.side = a['s_m'], a['u'], a['surface_side']
                context.workspace.status_text_set(None)
                redraw_editor(context)
                return {'FINISHED'}
            except Exception as exc:
                self.report({'WARNING'}, str(exc))
        return {'RUNNING_MODAL'}


class NREL_PT_defects(bpy.types.Panel):
    bl_label = 'NREL 5MW · 叶片缺陷'
    bl_idname = 'NREL_PT_defects'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MAPPO'

    @classmethod
    def poll(cls, context):
        from .. import farm_flex
        return farm_flex.is_active(context.scene)

    def draw(self, context):
        s, p, layout = session(context), context.scene.wfrl_nrel_defects, self.layout
        if s is None:
            layout.operator('wfrl.nrel_defect_action',text='启动缺陷编辑器').action='OPEN'
            layout.label(text=p.status[:60]);return
        layout.label(text='轮廓为辅助；材质外观使用材质预览')
        layout.label(text='合成视觉缺陷 · 跟随柔性回放 · 未耦合物理')
        layout.label(text='雷达读数仍来自原仿真几何')
        def buttons(items):
            row = layout.row(align=True)
            for code, label in items:
                row.operator('wfrl.nrel_defect_action', text=label).action = code
        for i, d in enumerate(s.store.document['defects']):
            kind_label = KIND_LABELS[d['morphology']]
            layout.label(text=f"{i}: {d['turbine_id']}/B{d['blade_id']} {kind_label} {'启用' if d['enabled'] else '禁用'} · r{d['revision']}")
        layout.prop(p, 'selected')
        layout.prop(p, 'show_helpers')
        if s.draft is None:
            layout.prop(p, 'turbine_id')
            layout.prop(p, 'kind')
            buttons([('NEW', '新建'), ('EDIT', '修改'), ('COPY', '复制为禁用')])
            buttons([('TOGGLE', '启用/禁用缺陷'), ('DELETE', '删除')])
            buttons([('UNDO', '撤销'), ('REDO', '重做')])
        else:
            kind = s.draft['defects'][s.draft_index]['morphology']
            preset = PRESETS[kind]
            layout.label(text=KIND_LABELS[kind])
            for name in ('enabled', 'turbine_id', 'blade_id', 'side', 'span', 'u', 'theta', 'length', 'width'):
                layout.prop(p, name)
            if preset['representation'] != 'appearance':
                layout.prop(p, 'depth')
            else:
                layout.label(text='材质外观缺陷：无几何深度')
            if 'edge_roughness' in preset:
                layout.prop(p, 'edge_roughness')
            if 'crater_fraction' in preset:
                layout.prop(p, 'crater_fraction')
            layout.prop(p, 'seed')
            layout.operator('wfrl.nrel_defect_pick')
            buttons([('PREVIEW', '预览'), ('CONFIRM', '确认'), ('CANCEL', '取消')])
        buttons([('CLOSEUP', '正面近景'), ('OBLIQUE', '斜侧近景')])
        buttons([('HEALTHY', '返回损伤状态' if s.healthy_payload else '健康对照（目标叶片）')])
        buttons([('CAMERA1', 'C1'), ('CAMERA2', 'C2'), ('CAMERA3', 'C3')])
        layout.prop(p, 'time_s')
        buttons([('TIME', '定位时刻'), ('ANALYZE', 'G 分析当前帧')])
        if s.records:
            for row in s.records:
                if row.get('primary', True):
                    layout.label(text=f"{row.get('camera_id', '')} {row.get('defect_id', '')[:8]}: {row.get('status', row.get('state', '?'))}")
                    width = row.get('width_px') or {}
                    if width.get('max') is not None:
                        layout.label(text=f"投影宽 {width['min']:.3f}–{width['max']:.3f} px；非辨识结论")
        layout.label(text=str(context.scene.get('defect_analysis_status', 'NOT_EVALUATED')))
        layout.prop(p, 'config_path')
        buttons([('SAVE', '保存配置'), ('LOAD', '加载配置')])
        layout.prop(p, 'output_path')
        buttons([('EXPORT_G', '保存 G 验收资料')])
        layout.prop(p, 'scan_start')
        layout.prop(p, 'scan_duration')
        layout.operator('wfrl.nrel_defect_scan', text='逐帧 G 扫描（Esc 可取消）')
        buttons([('EXPORT_A', '渲染当前三相机 A 图'),('CLOSE','关闭编辑器')])
        layout.label(text='渲染默认关闭；保存配置不会出图')
        layout.label(text=p.status[:60])


class NREL_OT_defect_scan(bpy.types.Operator):
    bl_idname = 'wfrl.nrel_defect_scan'
    bl_label = '扫描固定三相机'

    def invoke(self, context, event):
        from .defect_timeline import FrameMapping
        from .defect_outputs import create_run
        s, p = session(context), context.scene.wfrl_nrel_defects
        try:
            if s.busy or s.draft is not None or s.healthy_payload is not None:
                raise ValueError('请先完成编辑、对照或当前扫描')
            # RNA FloatProperty stores float32; recover an intended integral
            # frame count before strict timing validation (e.g. 2/60 seconds).
            duration = float(p.scan_duration)
            frames = duration*60.
            nearest = round(frames)
            if math.isclose(frames,nearest,rel_tol=1e-7,abs_tol=1e-7):
                duration = nearest/60.
            self.mapping = FrameMapping(p.scan_start,duration,60.,60.,float(s.flex.times[0]))
            if p.scan_start < s.flex.times[0] or p.scan_start+p.scan_duration > s.flex.times[-1]+1e-9:
                raise ValueError('扫描超出时间线')
            self.run = create_run(s, bpy.path.abspath(p.output_path), mapping=self.mapping)
            self.saved_time = float(s.scene['defect_time_s'])
            s.freeze_time = self.saved_time
            self.playing = context.screen.is_animation_playing
            if self.playing:
                bpy.ops.screen.animation_cancel(restore_frame=False)
            self.rows, self.index, self.version = [], 0, s.store.version
            self.started = time.perf_counter()
            s.busy = True
            self.timer = context.window_manager.event_timer_add(.05, window=context.window)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def finish_scan(self, context, cancelled=False, error=None):
        from .defect_outputs import save_records, write_json, update_run, save_review
        from .defect_timeline import summarize_intervals
        s = session(context)
        context.window_manager.event_timer_remove(self.timer)
        s.busy = False
        s.set_time(self.saved_time)
        if self.playing:
            bpy.ops.screen.animation_play()
        complete = not cancelled and error is None and self.index == self.mapping.frame_count
        save_records(self.run, self.rows)
        write_json(self.run['output']/'visible_intervals.json', dict(
            completed=complete, completed_frames=self.index, next_frame_index=None if complete else self.index,
            frame_mapping=self.mapping.to_dict(), error=error,
            intervals=summarize_intervals(self.rows, self.mapping, complete)))
        self.run['manifest']['state'] = 'FAILED' if error else 'G_SCAN_COMPLETE' if complete else 'G_SCAN_PARTIAL'
        self.run['manifest']['products']['visible_intervals'] = dict(state='GENERATED', complete=complete)
        update_run(self.run)
        save_review(s, self.run)
        context.scene.wfrl_nrel_defects.status = f'扫描 {self.index}/{self.mapping.frame_count} 帧；' + self.run['manifest']['state']
        return {'FINISHED'} if complete else {'CANCELLED'}

    def modal(self, context, event):
        if session(context) is None:
            context.window_manager.event_timer_remove(self.timer)
            return {'CANCELLED'}
        if event.type == 'ESC':
            return self.finish_scan(context, True)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        s = session(context)
        try:
            s.verify_cameras()
            if not s.store.accepts_version(self.version):
                return self.finish_scan(context, True, 'STALE_STATE')
            sample = self.mapping.sample(self.index)
            self.rows.extend(s.analyze(sample['time_s'],self.index,self.run['manifest']['run_id'],self.version))
            self.index += 1
            context.scene.wfrl_nrel_defects.status = f'扫描 {self.index}/{self.mapping.frame_count}；Esc 取消'
            if self.index == self.mapping.frame_count:
                return self.finish_scan(context)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            return self.finish_scan(context, True, repr(exc))


CLASSES=(NRELDefectProperties,NREL_OT_defect_action,NREL_OT_defect_pick,NREL_OT_defect_scan,NREL_PT_defects)


def register_properties():
    bpy.types.Scene.wfrl_nrel_defects=bpy.props.PointerProperty(type=NRELDefectProperties)
    for handlers,fn in ((bpy.app.handlers.save_pre,save_pre),(bpy.app.handlers.frame_change_post,frame_changed)):
        if fn not in handlers: handlers.append(fn)


def unregister_properties():
    detach()
    for handlers,fn in ((bpy.app.handlers.save_pre,save_pre),(bpy.app.handlers.frame_change_post,frame_changed)):
        if fn in handlers: handlers.remove(fn)
    if hasattr(bpy.types.Scene,'wfrl_nrel_defects'): del bpy.types.Scene.wfrl_nrel_defects
