"""Editor geometry resources, flexible attachments and camera projection."""
import copy
from dataclasses import asdict
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from .defect_schema import new_document
from .defect_shapes import lightning_crater
from .defect_geometry import DefectGeometry


class GeometrySet:
    def __init__(self, editor):
        self.editor = editor
        self.managers = {}
        self.active = None
        self.pending = []
        self.original_payloads = {}
        self.originals = [(row[0], row[0].data) for row in editor.flex.blades]
        self.flex_blades = list(editor.flex.blades)
        self.flex_groups = dict(editor.flex.groups)
        for tid in editor.surface.turbine_ids:
            rig = dict(scene=editor.scene, collection=editor.rig['collection'],
                       blades=[editor.blade(tid,b) for b in (1,2,3)], turbine_id=tid)
            manager = DefectGeometry.__new__(DefectGeometry)
            self.managers[tid] = manager
            manager._defer_initial_dispose = True
            DefectGeometry.__init__(manager, rig, editor.surface)
            self.original_payloads[tid] = manager.active

    @property
    def boolean_calls(self):
        return sum(m.boolean_calls for m in self.managers.values())

    def prepare(self, document):
        payload = dict(document=copy.deepcopy(document), turbines={}, rest={}, supports=[], measurements=[])
        self.pending.append(payload)
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

    def rollback_initial(self):
        errors = []
        for obj, mesh in self.originals:
            try:
                obj.data = mesh
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError('Initial blade restoration failed: ' + '; '.join(map(str, errors)))
        self.editor.flex.blades[:] = self.flex_blades
        self.editor.flex.groups = dict(self.flex_groups)
        self.editor.flex.cache.clear()
        self.editor.flex._update_geometry = self.editor.flex._world_matrices = None
        self.active = None
        for manager in self.managers.values():
            manager._active = None
        for payload in list(self.pending):
            try:
                self.dispose(payload)
            except Exception as exc:
                errors.append(exc)
        for manager in self.managers.values():
            try:
                manager.close(restore=False)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError('Initial resources await cleanup: ' + '; '.join(map(str, errors)))

    def finish_initial(self):
        for manager in self.managers.values():
            manager._defer_initial_dispose = False
        self.pending.clear()
        self.retry_originals()

    def retry_originals(self, *, strict=False):
        errors = []
        for tid, payload in list(self.original_payloads.items()):
            try:
                self.managers[tid].dispose(payload)
            except Exception as exc:
                errors.append(exc)
                continue
            self.original_payloads.pop(tid, None)
        if errors and strict:
            raise RuntimeError('Original defect resources await cleanup: ' + '; '.join(map(str, errors)))

    def dispose(self, payload):
        if payload is None or payload is self.active: return
        if any(child is getattr(self.managers[tid], '_active', None)
               for tid, child in payload.get('turbines', {}).items()):
            return
        for tid, child in payload.get('turbines',{}).items():
            self.managers[tid].dispose(child)
        self.pending[:] = [item for item in self.pending if item is not payload]


class EditorGeometry:

    """Geometry and projection methods for an editor session."""

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
        from .. import custom_cameras
        return [(c.name, c.parent.name if c.parent else None,
            tuple(tuple(row) for row in c.matrix_basis),
            tuple(tuple(row) for row in c.matrix_parent_inverse),
            c.data.type,c.data.lens,c.data.sensor_width,c.data.sensor_height,
            c.data.sensor_fit,c.data.shift_x,c.data.shift_y,c.data.clip_start,c.data.clip_end,
            self.camera_resolution(c), c.get('wfrl_custom_slot'),
            asdict(custom_cameras.parameters(c))) for c in self.cameras()]

    def verify_cameras(self):
        from .. import custom_cameras
        # A missing slot must not shift identities or silently reduce G/A to
        # fewer than the three cameras named in the editor's output contract.
        cameras = [self.camera(slot) for slot in (1, 2, 3)]
        # Evaluated poses are checked relative to YawRoot so legitimate replay
        # motion remains valid while native constraints and drivers are checked.
        custom_cameras.validate_native_state(self.scene, cameras=cameras)
        current = self.fixed_camera_signature()
        if self.camera_signature is None: self.camera_signature = current
        if current != self.camera_signature:
            raise ValueError('相机安装或镜头已修改；关闭编辑器后重新打开以冻结新配置')

    def live_helpers(self):
        """Discard deleted RNA handles without changing the defect document."""
        live = []
        for obj in self.helpers:
            try:
                if obj.as_pointer():
                    live.append(obj)
            except ReferenceError:
                pass
        self.helpers = live
        return live

    def clear_helpers(self):
        """Release only session-owned meshes and retain failed releases for retry."""
        from . import editor as _editor
        errors, pending = [], []
        for obj in self.live_helpers():
            try:
                _editor.bpy.data.objects.remove(obj, do_unlink=True)
            except ReferenceError:
                pass
            except Exception as exc:
                errors.append(exc)
                pending.append(obj)
        self.helpers = pending
        pending_meshes = []
        for mesh in getattr(self, 'helper_meshes', []):
            try:
                if mesh.users == 0:
                    _editor.bpy.data.meshes.remove(mesh)
                elif pending:
                    pending_meshes.append(mesh)
            except ReferenceError:
                pass
            except Exception as exc:
                errors.append(exc)
                pending_meshes.append(mesh)
        self.helper_meshes = pending_meshes
        if errors:
            raise RuntimeError('辅助标记清理失败：' + '; '.join(map(str, errors)))
        self.helper_rest.clear()

    def update_helpers(self, document=None):
        from . import editor as _editor
        self.clear_helpers()
        document = document or (self.store.document if hasattr(self,'store') else new_document(self.surface))
        props = self.scene.wfrl_nrel_defects
        def add(points, edges, d, role):
            mesh = _editor.bpy.data.meshes.new('NREL.Defect.Helper')
            self.helper_meshes.append(mesh)
            mesh.from_pydata(points,edges,[])
            mesh.update()
            obj = _editor.bpy.data.objects.new('NREL.Defect.Helper.'+d['id'],mesh)
            self.helpers.append(obj)
            self.rig['collection'].objects.link(obj)
            obj.parent = self.blade(d['turbine_id'],d['blade_id'])
            obj.hide_render = True
            obj.show_in_front = True
            obj['nrel_defect_role'] = 'editor_marker'
            obj['turbine_id'] = d['turbine_id']
            obj['blade_id'] = d['blade_id']
            obj['defect_id'] = d['id']
            obj['outline'] = role
            obj.hide_viewport = not props.show_helpers or self.healthy_payload is not None
            self.helper_rest[obj.name] = np.array(points)
        for d in document['defects']:
            if not d['enabled']: continue
            for shape,label in ((d,'support'),(lightning_crater(d),'crater')) if d['morphology']=='lightning' else ((d,'support'),):
                sampled = self.surface.sample_shape(shape)
                points = sampled['left']+list(reversed(sampled['right']))
                add(points,[(i,(i+1)%len(points)) for i in range(len(points))],d,label)
        self.update_attachments(float(self.scene.get('wfrl_clearance_time_s',self.flex.times[0])))

    def update_attachments(self, t):
        for obj in self.live_helpers():
            rest = self.helper_rest.get(obj.name)
            if obj.parent is None or obj.data is None or rest is None or obj.data not in self.helper_meshes:
                continue
            if len(obj.data.vertices) != len(rest):
                continue
            coords = self.flex.deform_points(obj.parent,rest,t)
            obj.data.vertices.foreach_set('co',coords.astype(np.float32).ravel())
            obj.data.update()
        try:
            repair_live = self.repair_source is not None and self.repair_source.as_pointer()
        except ReferenceError:
            repair_live = False
        if repair_live and self.repair_source.data is not None and len(self.repair_source.data.vertices)==len(self.repair_rest):
            coords = self.flex.deform_points(self.blade('T1',1),self.repair_rest,t)
            self.repair_source.data.vertices.foreach_set('co',coords.astype(np.float32).ravel())
            self.repair_source.data.update()

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
        from . import editor as _editor
        d=(_editor.defect_from_props(self, self.scene.wfrl_nrel_defects) if self.draft is not None
           else self.store.document['defects'][index])
        frame=self.surface.frame(d['anchor'])
        blade=self.blade(d['turbine_id'],d['blade_id'])
        p=Vector(frame['point'])
        n=Vector(frame['normal'])
        e=Vector(frame['e_t'])
        mapped=self.flex.deform_points(blade,[p,p+n*.01,p+e*.01],self.scene['defect_time_s'],world=True)
        target=Vector(mapped[0])
        direction=Vector(mapped[1]-mapped[0]).normalized()
        if oblique: direction+=Vector(mapped[2]-mapped[0]).normalized()*.7
        for area in _editor.bpy.context.screen.areas if _editor.bpy.context.screen else []:
            if area.type=='VIEW_3D':
                r=area.spaces.active.region_3d
                r.view_perspective='PERSP'
                r.view_location=target
                r.view_rotation=(-direction).to_track_quat('-Z','Y')
                r.view_distance=max(.35,3*d['shape']['length_m'])
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
            n=np.cross(b-a,c-a)
            length=np.linalg.norm(n)
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
        self.set_time(t)
        tree=scene_tree(self.rig)
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
                    camera_id=camera.name,frame_index=frame_index,time_s=t,mode='G',camera_world_matrix=descriptor['matrix_world'],
                    K=descriptor['K'], P=descriptor['P'],
                    resolution=[descriptor['width'],descriptor['height']])
                rows.append(row)
        return rows
