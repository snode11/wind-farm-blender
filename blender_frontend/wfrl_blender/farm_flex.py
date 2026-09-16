"""Portable three-turbine flexible review playback on the shared timeline."""
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import numpy as np

_ACTIVE=None


def default_package():
    return Path(__file__).resolve().parent / 'assets' / 'mappo'



def is_active(scene):
    from . import clearance_replay
    return (_ACTIVE is not None and _ACTIVE.enabled and _ACTIVE.scene==scene
            and clearance_replay.reader_for(scene) in _ACTIVE.readers.values())


def read_package(path):
    path=Path(path)
    manifest=json.loads((path/'manifest.json').read_text())
    if (manifest.get('schema') not in ('wfrl.farm-flex-review.v1','wfrl.farm-flex-review.v2')
            or manifest.get('status')!='REVIEW_ONLY'
            or manifest.get('turbine_ids')!=['T1','T2','T3']):
        raise ValueError('Expected a complete three-turbine flexible review package')
    for name in set(manifest['files']) | {'geometry.npz','data.json','source-surfaces.json'}:
        if hashlib.sha256((path/name).read_bytes()).hexdigest()!=manifest['files'][name]:
            raise ValueError('Farm package integrity mismatch: '+name)
    with np.load(path/'geometry.npz',allow_pickle=False) as data:
        times=data['times'].copy();transforms=data['transforms'].copy();poses=data['poses'].copy()
    n=len(times)
    if (n<2 or times.ndim!=1 or not np.isfinite(times).all() or np.any(np.diff(times)<=0)
            or transforms.shape!=(n,3,3,19,3,4) or poses.shape!=(n,3,6)
            or not np.isfinite(transforms).all() or not np.isfinite(poses).all()
            or times[0]!=manifest['segment']['start_s'] or times[-1]!=manifest['segment']['end_s']):
        raise ValueError('Incomplete farm geometry or time axis')
    from .tower_motion import read_tower
    tower_data=read_tower(path,manifest,times)
    contents=json.loads((path/'data.json').read_text())
    try:
        from ._vendor.lidar.replay import ReplayReader
    except ImportError:
        from wfrl.lidar.replay import ReplayReader
    readers={}
    for index,tid in enumerate(manifest['turbine_ids']):
        payload=contents[tid]
        if not np.array_equal([r['time_s'] for r in payload['motion']],times):
            raise ValueError('Motion and geometry time axes differ: '+tid)
        expected=np.array([[r['yaw_deg'],r['azimuth_deg'],r['rotor_speed_rpm'],*r['pitch_deg']]
                           for r in payload['motion']])
        if not np.allclose(expected,poses[:,index],atol=.001,rtol=0):
            raise ValueError('Motion and geometry poses differ: '+tid)
        if tower_data is not None:
            actual=np.asarray([r['nacelle_transform'] for r in payload['motion']])
            if not np.allclose(actual,tower_data['nacelle'][:,index],atol=1e-8,rtol=0):
                raise ValueError('Nacelle motion and geometry differ: '+tid)
            top=np.einsum('nij,j->ni',actual[:,:,:3],[0,0,87.6])+actual[:,:,3]+manifest['layout_m'][index]
            if not np.allclose(top,[r['nacelle_position_m'] for r in payload['motion']],atol=1e-8,rtol=0):
                raise ValueError('Nacelle position and geometry differ: '+tid)
        readers[tid]=ReplayReader(SimpleNamespace(manifest={**manifest,'turbine_id':tid},**payload))
    return manifest,times,transforms,poses,readers


class FarmFlex:
    def __init__(self,scene,path):
        import bpy
        from .turbine_geometry import geometry_data,blade_mesh
        from .materials import get_material,ensure_blade_tip_markings
        from . import clearance_replay
        self.manifest,self.times,self.transforms,self.poses,self.readers=read_package(path)
        from .tower_motion import read_tower
        self.tower_motion = read_tower(path,self.manifest,self.times)
        self.towers = []
        self.fittings = []
        from .deflection import read_comparison, ComparisonView
        comparison_data = read_comparison(path, self.manifest, self.times, self.poses)
        self.comparison = None
        telemetry_path = Path(path) / 'telemetry.json'
        self.telemetry = json.loads(telemetry_path.read_text()) if 'telemetry.json' in self.manifest['files'] else None
        if self.telemetry is not None:
            extra = self.telemetry
            if (extra['schema'] != 'wfrl.farm-telemetry.v1'
                    or extra['geometry_sha256'] != self.manifest['files']['geometry.npz']
                    or not np.array_equal(extra['times'], self.times)
                    or set(extra['turbines']) != set(self.readers)):
                raise ValueError('Telemetry and geometry do not match')
            for channels in extra['turbines'].values():
                for record in channels.values():
                    values = np.asarray(record['values'])
                    if values.shape != self.times.shape or not np.isfinite(values).all():
                        raise ValueError('Invalid telemetry samples')
        self.scene=scene;self.blades=[];self.groups={};self.cache={};self.enabled=True
        self.visible_turbines={0,1,2}
        for tid in self.readers:
            scene.objects[f'WFRL.Turbine.{tid}.YawRoot'].rotation_euler=(0,0,0)
            scene.objects[f'WFRL.Turbine.{tid}.YawRoot'].location=(0,0,geometry_data()['scalars']['TowerHt'])
            scene.objects[f'WFRL.Turbine.{tid}.Rotor'].rotation_euler.x=0
            for b in (1,2,3):scene.objects[f'WFRL.Turbine.{tid}.Blade{b}'].rotation_euler.z=0
        bpy.context.view_layer.update()
        spans=np.array([s[0]+geometry_data()['scalars']['HubRad'] for s in geometry_data()['blade_stations']])
        # Retain the same 19-station loft, with fewer presentation samples.
        # Physical transforms, amplitude and the stable tip apex are unchanged.
        vertices,faces=blade_mesh(subdiv=4,ring_points=48)
        # Preserve the cosmetic apex at -1 for existing coloured trails. The
        # extra loose vertex follows exactly the same deformation as the mesh.
        if comparison_data is not None:
            vertices = list(vertices)
            apex = len(vertices) - 1
            vertices.insert(apex, (0., 0., comparison_data['scalars']['TipRad']))
            faces = [tuple(v + 1 if v == apex else v for v in face) for face in faces]
        display_mesh=bpy.data.meshes.new('WFRL.FarmFlex.SourceLoft')
        display_mesh.from_pydata(vertices,[],faces)
        display_mesh.materials.append(get_material('blade'))
        for polygon in display_mesh.polygons:polygon.use_smooth=True
        display_mesh.update()
        for turbine,tid in enumerate(self.readers):
            offset=np.array(self.manifest['layout_m'][turbine])
            for b in (1,2,3):
                obj=scene.objects[f'WFRL.Turbine.{tid}.Blade{b}']
                obj.data=display_mesh.copy()
                ensure_blade_tip_markings([obj])
                # The source loft already has smooth normals. Avoid rebuilding
                # a 12 mm cosmetic bevel on nine moving meshes every frame.
                for modifier in obj.modifiers:
                    if modifier.type=='BEVEL':
                        modifier.show_viewport=False
                        modifier.show_render=False
                rest=np.empty(len(obj.data.vertices)*3,np.float32)
                obj.data.vertices.foreach_get('co',rest);rest=rest.reshape(-1,3)
                matrix=np.array(obj.matrix_world)
                world=(rest@matrix[:3,:3].T+matrix[:3,3]-offset).astype(np.float32)
                index=np.clip(np.searchsorted(spans,rest[:,2],side='right')-1,0,len(spans)-2)
                weight=np.clip((rest[:,2]-spans[index])/(spans[index+1]-spans[index]),0,1)[:,None]
                self.blades.append((obj,rest,world,index,weight.astype(np.float32),turbine,b-1,offset))
                self.groups[obj.name]=[(s,np.flatnonzero(index==s)) for s in np.unique(index)]
        if self.tower_motion is not None:
            from .turbine_geometry import tower_mesh
            tower_vertices, tower_faces = tower_mesh()
            heights = self.tower_motion['heights']
            rest = np.asarray(tower_vertices, dtype=np.float32)
            index = np.clip(np.searchsorted(heights,rest[:,2],side='right')-1,0,len(heights)-2)
            weight = ((rest[:,2]-heights[index])/(heights[index+1]-heights[index]))[:,None]
            for k,tid in enumerate(self.readers):
                prefix = f'WFRL.Turbine.{tid}'
                obj = scene.objects[prefix+'.Tower']
                # Rebuild from rest geometry so save/reload never compounds bending.
                mesh = bpy.data.meshes.new(prefix+'.FlexibleTower')
                mesh.from_pydata(tower_vertices,[],tower_faces)
                mesh.materials.append(get_material('tower'))
                for polygon in mesh.polygons: polygon.use_smooth=True
                obj.data=mesh
                self.towers.append((obj,rest.copy(),index,weight,k))
                for suffix,z in (('.YawBearing',87.6),('.TowerCollar87',87.52),('.TowerCollar0',.28)):
                    fitting=scene.objects.get(prefix+suffix)
                    if fitting:
                        fitting.rotation_euler=(0,0,0)
                        fitting.location=(0,0,z)
                        self.fittings.append((fitting,k,z))
        scene['wfrl_scene_kind']='clearance_replay'
        scene['wfrl_clearance_turbine']='T1'
        scene['wfrl_clearance_demo']='near_tower'
        scene['wfrl_clearance_timebase_fps']=60.
        scene['wfrl_clearance_status']='REVIEW_ONLY'
        scene['wfrl_flex_provisional']=True
        scene['wfrl_flex_active']=True
        scene['wfrl_flex_provenance']='三机 FAST.Farm · MAPPO 偏航 · 保守转速目标/变桨 · 随机阵风'
        if self.tower_motion is not None:
            scene['wfrl_flex_provenance'] += ' · 真实塔架柔性'
        scene['wfrl_tip_reference_note']=scene['wfrl_flex_provenance']
        scene['wfrl_farm_flex_path']=str(Path(path).resolve())
        scene.render.fps=60;scene.render.fps_base=1
        scene.frame_start=1;scene.frame_end=1+round((self.times[-1]-self.times[0])*60)
        scene.sync_mode='FRAME_DROP'
        scene.use_preview_range=False
        self.last_telemetry_frame=None
        scene['wfrl_fidelity']='FAST.Farm / REVIEW_ONLY'
        scene['wfrl_backend']='MAPPO recorded results'
        for key in ('wfrl_power_mw', 'wfrl_demo_phase', 'wfrl_demo_time_s'):
            if key in scene: del scene[key]
        scene['wfrl_power_available']=False
        clearance_replay._READERS[scene.as_pointer()]=self.readers['T1']
        if comparison_data is not None:
            self.comparison = ComparisonView(self, comparison_data, vertices, faces)

    def restore_support(self):
        for obj,rest,*_ in self.towers:
            try:
                obj.data.vertices.foreach_set('co',rest.ravel());obj.data.update()
            except ReferenceError: pass
        for obj,k,z in self.fittings:
            try: obj.location=(0,0,z);obj.rotation_euler=(0,0,0)
            except ReferenceError: pass
        if self.tower_motion is not None:
            for tid in self.readers:
                obj=self.scene.objects.get(f'WFRL.Turbine.{tid}.YawRoot')
                if obj: obj.location=(0,0,87.6);obj.rotation_euler=(0,0,0)

    def record_telemetry(self, scene, pose=None):
        if not getattr(scene, 'wfrl_channel_telemetry', True):
            self.last_telemetry_frame = None
            return
        from . import clearance_replay, charts
        value = clearance_replay.sample(scene)
        if value is None or value['time_s'] == self.last_telemetry_frame:
            return
        t = value['time_s']
        if pose is None:
            pose = np.array([[np.interp(t, self.times, self.poses[:, k, j])
                              for j in range(6)] for k in range(3)])
        if self.last_telemetry_frame is not None and t < self.last_telemetry_frame:
            charts.clear()
        extra = {}
        if self.telemetry:
            for tid, channels in self.telemetry['turbines'].items():
                extra[tid] = {name: dict(value=float(np.interp(t, self.times, record['values'])),
                                        unit=record['unit'], source_channel=record['source_channel'],
                                        source_sha256=self.telemetry['sources'][tid]['sha256'])
                              for name, record in channels.items()}
        charts.record_farm(t, pose, self.manifest, extra)
        scene['wfrl_yaw_deg'] = pose[:, 0].tolist()
        scene['wfrl_pitch_deg'] = pose[:, 3:].mean(axis=1).tolist()
        scene['wfrl_rpm'] = pose[:, 2].tolist()
        scene['wfrl_telemetry_time_s'] = t
        self.last_telemetry_frame = t

    def deformed_at(self,frame):
        if frame in self.cache:return self.cache[frame]
        transforms=self.transforms[frame]
        result=[]
        for obj,rest,world,index,weight,k,b,offset in self.blades:
            if k not in self.visible_turbines and not (k == 0 and self.comparison is not None):
                result.append(None)
                continue
            deformed=np.empty_like(world)
            for section,ids in self.groups[obj.name]:
                lo,hi=transforms[k,b,section],transforms[k,b,section+1]
                points=world[ids]
                a=points@lo[:,:3].T+lo[:,3]
                z=points@hi[:,:3].T+hi[:,3]
                deformed[ids]=a*(1-weight[ids])+z*weight[ids]
            result.append(deformed)
        self.cache[frame]=result
        while len(self.cache)>3:self.cache.pop(next(iter(self.cache)))
        return result

    def update(self,scene):
        import bpy
        from . import clearance_replay
        if scene!=self.scene or not self.enabled:return
        reader=clearance_replay.reader_for(scene)
        if reader not in self.readers.values():
            for obj,rest,*_ in self.blades:
                obj.data.vertices.foreach_set('co',rest.ravel());obj.data.update()
            self.restore_support()
            self.enabled=False
            scene['wfrl_flex_active']=False
            if self.comparison is not None:
                self.comparison.close()
            return
        t=clearance_replay.sample(scene)['time_s']
        i=int(np.clip(np.searchsorted(self.times,t,side='right')-1,0,len(self.times)-2))
        alpha=float(np.clip((t-self.times[i])/(self.times[i+1]-self.times[i]),0,1))
        lower=self.deformed_at(i);upper=self.deformed_at(i+1)
        pose=self.poses[i]*(1-alpha)+self.poses[i+1]*alpha
        self.record_telemetry(scene, pose)
        scene['wfrl_clearance_time_s']=float(t)
        if scene.wfrl_show_wake:
            from . import wake
            wake.update_proxy_objects(scene, phase=float(t)*.9)
        for k,tid in enumerate(self.readers):
            scene.objects[f'WFRL.Turbine.{tid}.YawRoot'].rotation_euler.z=math.radians(float(pose[k,0]))
            scene.objects[f'WFRL.Turbine.{tid}.Rotor'].rotation_euler.x=math.radians(float(pose[k,1]))
            for b in (1,2,3):
                scene.objects[f'WFRL.Turbine.{tid}.Blade{b}'].rotation_euler.z=-math.radians(float(pose[k,b+2]))
        if self.tower_motion is not None:
            from mathutils import Matrix
            from .tower_motion import interpolate_transform
            support = self.tower_motion
            tower = support['transforms'][i]*(1-alpha)+support['transforms'][i+1]*alpha
            nacelle = interpolate_transform(support['nacelle'][i],support['nacelle'][i+1],alpha)
            for k,tid in enumerate(self.readers):
                obj=scene.objects[f'WFRL.Turbine.{tid}.YawRoot']
                obj.rotation_euler=Matrix(nacelle[k,:,:3].tolist()).to_euler()
                obj.location=nacelle[k,:,:3]@np.array([0,0,87.6])+nacelle[k,:,3]
            for obj,rest,index,weight,k in self.towers:
                lo,hi=tower[k,index],tower[k,index+1]
                a=np.einsum('nij,nj->ni',lo[:,:,:3],rest)+lo[:,:,3]
                b=np.einsum('nij,nj->ni',hi[:,:,:3],rest)+hi[:,:,3]
                obj.data.vertices.foreach_set('co',((1-weight)*a+weight*b).astype(np.float32).ravel())
                obj.data.update()
            heights=support['heights']
            for obj,k,z in self.fittings:
                j=int(np.clip(np.searchsorted(heights,z)-1,0,len(heights)-2))
                f=float((z-heights[j])/(heights[j+1]-heights[j]))
                tr=interpolate_transform(tower[k,j],tower[k,j+1],f)
                obj.location=tr[:,:3]@np.array([0,0,z])+tr[:,3]
                obj.rotation_euler=Matrix(tr[:,:3].tolist()).to_euler()
        bpy.context.view_layer.update()
        inverses=[np.array(obj.matrix_world.inverted()) for obj,*_ in self.blades]
        for (obj,rest,world,index,weight,k,b,offset),inverse,lo,hi in zip(self.blades,inverses,lower,upper):
            if lo is None:continue
            deformed=lo*(1-alpha)+hi*alpha+offset
            local=deformed@inverse[:3,:3].T+inverse[:3,3]
            obj.data.vertices.foreach_set('co',local.astype(np.float32).ravel());obj.data.update()
        if self.comparison is not None:
            self.comparison.update(scene, i, alpha, t)


def update(scene,depsgraph=None):
    if _ACTIVE is not None:_ACTIVE.update(scene)


def detach(scene=None, *, restore=True):
    """Release handlers and mesh references before scene destruction or unload."""
    import bpy
    global _ACTIVE
    if _ACTIVE is not None and scene is not None and _ACTIVE.scene != scene:
        return
    active, _ACTIVE = _ACTIVE, None
    if update in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(update)
    if on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(on_load_pre)
    if active is not None:
        active.enabled = False
        if active.comparison is not None:
            active.comparison.close()
        if restore:
            for obj, rest, *_ in active.blades:
                try:
                    obj.data.vertices.foreach_set('co', rest.ravel())
                    obj.data.update()
                except ReferenceError:
                    pass
        try:
            active.scene['wfrl_flex_active'] = False
        except ReferenceError:
            pass
        if restore:
            active.restore_support()
        active.cache.clear()


def on_load_pre(_unused):
    # Detach removes this handler; clear trails here before handler-list mutation
    # can skip the following load_pre callback.
    from . import tip_tracking
    tip_tracking.on_load_pre(_unused)
    detach(restore=False)


def attach(scene,path):
    import bpy
    from . import tip_tracking
    global _ACTIVE
    detach()
    try:
        _ACTIVE=FarmFlex(scene,path)
    except Exception:
        from . import clearance_replay
        _ACTIVE=None
        clearance_replay.clear(scene,'三机形变包未就绪：加载失败')
        raise
    # Tip sampling must follow mesh deformation at the same simulation time.
    if update in bpy.app.handlers.frame_change_post:bpy.app.handlers.frame_change_post.remove(update)
    tip_tracking.unregister()
    bpy.app.handlers.frame_change_post.append(update)
    from bpy.app.handlers import persistent
    persistent(on_load_pre)
    if on_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(on_load_pre)
    tip_tracking.register()
    scene.frame_set(1)
    return _ACTIVE
