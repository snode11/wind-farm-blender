"""Portable three-turbine flexible review playback on the shared timeline."""
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from . import performance

_ACTIVE=None
# Each scene owns its deformation runtime. _ACTIVE remains the last explicitly
# selected instance for existing panel integrations; callbacks use active_for.
_ACTIVES={}


def dual_beam_api():
    try:
        from ._vendor.lidar import dual_beam_replay
    except ImportError:
        from wfrl.lidar import dual_beam_replay
    return dual_beam_replay


def default_package():
    return Path(__file__).resolve().parent / 'assets' / 'mappo'


def default_dual_package():
    return Path(__file__).resolve().parent / 'assets' / 'dual_beam'


def default_dual_tls_package():
    """The optional TLS candidate shares the original package's saved geometry."""
    return Path(__file__).resolve().parent / 'assets' / 'dual_beam_tls'


def saved_package(scene):
    """Relocate an installed built-in only when its saved manifest is identical."""
    saved = Path(scene['wfrl_farm_flex_path'])
    if saved.is_dir():
        return saved
    expected = scene.get('wfrl_farm_manifest_sha256')
    if expected:
        for candidate in (default_package(), default_dual_package(), default_dual_tls_package()):
            manifest = candidate / 'manifest.json'
            if manifest.is_file() and hashlib.sha256(manifest.read_bytes()).hexdigest() == expected:
                return candidate
    # The caller retains its normal missing/integrity error and clears old data.
    return saved



def active_for(scene):
    """Return the runtime bound to this scene, never another window's scene."""
    if scene is None:
        return None
    active = _ACTIVES.get(scene.as_pointer())
    if active is not None and active.enabled and active.scene == scene:
        return active
    return None


def is_active(scene):
    from . import clearance_replay
    global _ACTIVE
    active = active_for(scene)
    ready = (active is not None
             and clearance_replay.reader_for(scene) in active.readers.values())
    if ready:
        _ACTIVE = active
    return ready


def read_package(path):
    path, overlay = dual_beam_api().resolve_package(path)
    manifest=json.loads((path/'manifest.json').read_text())
    if (manifest.get('schema') not in ('wfrl.farm-flex-review.v1','wfrl.farm-flex-review.v2','wfrl.farm-flex-review.v3')
            or manifest.get('status')!='REVIEW_ONLY'
            or manifest.get('turbine_ids') not in (['T1','T2','T3'], ['T1'])
            or (manifest.get('turbine_ids') == ['T1'] and (manifest.get('schema') != 'wfrl.farm-flex-review.v3' or not manifest.get('interface_only')))):
        raise ValueError('Expected a complete three-turbine flexible review package')
    for name in set(manifest['files']) | {'geometry.npz','data.json','source-surfaces.json'}:
        if hashlib.sha256((path/name).read_bytes()).hexdigest()!=manifest['files'][name]:
            raise ValueError('Farm package integrity mismatch: '+name)
    with np.load(path/'geometry.npz',allow_pickle=False) as data:
        times=data['times'].copy();transforms=data['transforms'].copy();poses=data['poses'].copy()
    n=len(times); turbines=len(manifest['turbine_ids'])
    if (n<2 or times.ndim!=1 or not np.isfinite(times).all() or np.any(np.diff(times)<=0)
            or transforms.shape!=(n,turbines,3,19,3,4) or poses.shape!=(n,turbines,6)
            or not np.isfinite(transforms).all() or not np.isfinite(poses).all()
            or times[0]!=manifest['segment']['start_s'] or times[-1]!=manifest['segment']['end_s']):
        raise ValueError('Incomplete farm geometry or time axis')
    from .prebend import read_reference
    reference = read_reference(path, manifest)
    if reference is not None and 'deflection-t1.json' not in manifest['files']:
        raise ValueError('BeamDyn package requires mapped deflection data')
    from .tower_motion import read_tower
    tower_data=read_tower(path,manifest,times)
    contents=json.loads((path/'data.json').read_text())
    try:
        from ._vendor.lidar.replay import ReplayReader
    except ImportError:
        from wfrl.lidar.replay import ReplayReader
    readers={}; motion_poses=[]
    for index,tid in enumerate(manifest['turbine_ids']):
        payload=contents[tid]
        if not np.array_equal([r['time_s'] for r in payload['motion']],times):
            raise ValueError('Motion and geometry time axes differ: '+tid)
        expected=np.array([[r['yaw_deg'],r['azimuth_deg'],r['rotor_speed_rpm'],*r['pitch_deg']]
                           for r in payload['motion']])
        if not np.allclose(expected,poses[:,index],atol=.001,rtol=0):
            raise ValueError('Motion and geometry poses differ: '+tid)
        # Keep the source motion precision. Unwrapped azimuth reaches thousands
        # of degrees; the float32 NPZ poses lose sub-millidegree rigid phase.
        motion_poses.append(expected)
        if tower_data is not None:
            actual=np.asarray([r['nacelle_transform'] for r in payload['motion']])
            if not np.allclose(actual,tower_data['nacelle'][:,index],atol=1e-8,rtol=0):
                raise ValueError('Nacelle motion and geometry differ: '+tid)
            top=np.einsum('nij,j->ni',actual[:,:,:3],[0,0,87.6])+actual[:,:,3]+manifest['layout_m'][index]
            if not np.allclose(top,[r['nacelle_position_m'] for r in payload['motion']],atol=1e-8,rtol=0):
                raise ValueError('Nacelle position and geometry differ: '+tid)
        readers[tid]=ReplayReader(SimpleNamespace(manifest={**manifest,'turbine_id':tid},**payload))
        if overlay is not None:
            readers[tid] = dual_beam_api().DualBeamReader(readers[tid], overlay, tid)
    if overlay is not None:
        manifest = {**manifest, 'measurement_mode': 'dual_beam',
                    'dual_beam_calibration': overlay['config'],
                    'dual_beam': overlay['manifest']}
    return manifest,times,transforms,np.stack(motion_poses,axis=1),readers


class FarmFlex:
    def __init__(self,scene,path):
        import bpy
        from .turbine_geometry import geometry_data,blade_mesh
        from .materials import get_material,ensure_blade_tip_markings
        from . import clearance_replay
        self.manifest,self.times,self.transforms,self.poses,self.readers=read_package(path)
        result_path = Path(path).resolve()
        path, overlay = dual_beam_api().resolve_package(path)
        from .tower_motion import read_tower
        self.tower_motion = read_tower(path,self.manifest,self.times)
        from .prebend import read_reference
        self.reference = read_reference(path, self.manifest)
        self.towers = []
        self.fittings = []
        from .deflection import read_comparison, ComparisonView
        comparison_data = read_comparison(path, self.manifest, self.times, self.poses,
                                          rigid_poses=self.poses)
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
        self.reference_matrices = {}
        self.visible_turbines=set(range(len(self.readers)))
        for tid in self.readers:
            scene.objects[f'WFRL.Turbine.{tid}.YawRoot'].rotation_euler=(0,0,0)
            scene.objects[f'WFRL.Turbine.{tid}.YawRoot'].location=(0,0,geometry_data()['scalars']['TowerHt'])
            scene.objects[f'WFRL.Turbine.{tid}.Rotor'].rotation_euler.x=0
            for b in (1,2,3):scene.objects[f'WFRL.Turbine.{tid}.Blade{b}'].rotation_euler.z=0
        phase_started = performance.begin()
        bpy.context.view_layer.update()
        performance.end("flex.depsgraph_update", phase_started)
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
        if self.reference is not None:
            from .prebend import bend_vertices
            vertices = bend_vertices(vertices, self.reference).tolist()
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
                self.reference_matrices[obj.name] = matrix.copy()
                world=(rest@matrix[:3,:3].T+matrix[:3,3]-offset).astype(np.float32)
                index=np.clip(np.searchsorted(spans,rest[:,2],side='right')-1,0,len(spans)-2)
                weight=np.clip((rest[:,2]-spans[index])/(spans[index+1]-spans[index]),0,1)[:,None]
                self.blades.append((obj,rest,world,index,weight.astype(np.float32),turbine,b-1,offset))
                self.groups[obj.name]=[(s,np.flatnonzero(index==s)) for s in np.unique(index)]
        # Bind cosmetic surface attachments to the new reference loft before
        # any playback frame writes deform it. Saved anchor indices from the
        # rigid presentation mesh must never be reused on this replacement.
        from . import repair_marks
        repair_marks.ensure(scene, force=True)
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
                # Primitive collars store height in object.location; decorative
                # rings store it in their vertices. Apply the same support
                # transform without adding the baked height a second time.
                for suffix,z,origin_z in (('.YawBearing',87.6,87.6),
                                          ('.TowerCollar87',87.52,87.52),
                                          ('.TowerCollar0',.28,.28),
                                          ('.YawSeal',87.6,0.),
                                          ('.TowerWeld28',28.,0.),
                                          ('.TowerWeld56',56.,0.)):
                    fitting=scene.objects.get(prefix+suffix)
                    if fitting:
                        if suffix == '.YawSeal':
                            from .mechanical_details import recess_yaw_seal
                            recess_yaw_seal(fitting, geometry_data()['scalars']['TowerHt'])
                        fitting.rotation_euler=(0,0,0)
                        rest_location=np.array([0.,0.,origin_z])
                        fitting.location=rest_location
                        self.fittings.append((fitting,k,z,rest_location))
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
        if self.reference is not None:
            scene['wfrl_flex_provenance'] = self.manifest['provenance_label']
        scene['wfrl_tip_reference_note']=scene['wfrl_flex_provenance']
        scene['wfrl_farm_flex_path']=str(result_path)
        scene['wfrl_farm_manifest_sha256']=hashlib.sha256((result_path/'manifest.json').read_bytes()).hexdigest()
        from .clearance_visual import ensure_radar
        if overlay is not None:
            config = overlay['config']
            directions = [(-math.sin(math.radians(a)), 0., -math.cos(math.radians(a)))
                          for a in config['angles_deg']]
            origins = [[o[0], o[1], o[2]-geometry_data()['scalars']['TowerHt']]
                       for o in config['origins_m']]
            for tid in self.readers:
                ensure_radar(scene, tid, origins[0], directions, beam_origins=origins)
        else:
            calibration = self.manifest['calibration']
            origin = list(calibration['origin_m'])
            origin[2] -= geometry_data()['scalars']['TowerHt']
            for tid in self.readers:
                ensure_radar(scene, tid, origin, calibration['beam_directions'])
        scene.render.fps=60;scene.render.fps_base=1
        scene.frame_start=1;scene.frame_end=1+round((self.times[-1]-self.times[0])*60)
        scene.sync_mode='FRAME_DROP'
        scene.use_preview_range=False
        self.last_telemetry_frame=None
        scene['wfrl_fidelity']='FAST.Farm / REVIEW_ONLY'
        scene['wfrl_backend']='OpenFAST interface probe' if self.manifest.get('interface_only') else 'MAPPO recorded results'
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
        for obj,k,z,rest_location in self.fittings:
            try: obj.location=rest_location;obj.rotation_euler=(0,0,0)
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
                              for j in range(6)] for k in range(len(self.readers))])
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

    @performance.timed("flex.deformation_cache")
    def rebind_blade(self, obj, coordinates):
        """Bind an edited rest mesh without using its currently posed vertices."""
        from .turbine_geometry import geometry_data
        rest = np.asarray(coordinates, dtype=np.float32).reshape(-1, 3).copy()
        if len(rest) != len(obj.data.vertices) or not np.isfinite(rest).all():
            raise ValueError('Edited blade reference does not match its mesh')
        position = next(i for i, row in enumerate(self.blades) if row[0] == obj)
        _, _, _, _, _, k, b, offset = self.blades[position]
        matrix = self.reference_matrices[obj.name]
        spans = np.array([s[0]+geometry_data()['scalars']['HubRad']
                          for s in geometry_data()['blade_stations']])
        world = (rest@matrix[:3,:3].T+matrix[:3,3]-offset).astype(np.float32)
        index = np.clip(np.searchsorted(spans,rest[:,2],side='right')-1,0,len(spans)-2)
        weight = np.clip((rest[:,2]-spans[index])/(spans[index+1]-spans[index]),0,1)[:,None].astype(np.float32)
        self.blades[position] = (obj,rest,world,index,weight,k,b,offset)
        self.groups[obj.name] = [(s,np.flatnonzero(index==s)) for s in np.unique(index)]
        self.cache.clear()

    def deform_points(self, obj, coordinates, time_s, *, world=False, high_precision=False):
        """Map immutable rest points through the same saved blade motion as replay."""
        from .turbine_geometry import geometry_data
        # Mesh/helper coordinates follow Blender's float32 rest representation.
        # Analytic support slivers need double precision at z~50 m so their
        # sub-millimetre triangles do not collapse during geometry analysis.
        points = np.asarray(coordinates, dtype=np.float64 if high_precision else np.float32).reshape(-1,3)
        if not len(points):
            return points.copy()
        t = float(time_s)
        if not math.isfinite(t) or not self.times[0] <= t <= self.times[-1]:
            raise ValueError('Simulation time outside source geometry range')
        _, _, _, _, _, k, b, offset = next(row for row in self.blades if row[0] == obj)
        matrix = self.reference_matrices[obj.name]
        original = points@matrix[:3,:3].T+matrix[:3,3]-offset
        spans = np.array([s[0]+geometry_data()['scalars']['HubRad']
                          for s in geometry_data()['blade_stations']])
        index = np.clip(np.searchsorted(spans,points[:,2],side='right')-1,0,len(spans)-2)
        weight = np.clip((points[:,2]-spans[index])/(spans[index+1]-spans[index]),0,1)[:,None]
        i = int(np.clip(np.searchsorted(self.times,t,side='right')-1,0,len(self.times)-2))
        alpha = float(np.clip((t-self.times[i])/(self.times[i+1]-self.times[i]),0,1))
        def endpoint(frame):
            tr = self.transforms[frame,k,b]
            low, high = tr[index], tr[index+1]
            a = np.einsum('nij,nj->ni',low[:,:,:3],original)+low[:,:,3]
            z = np.einsum('nij,nj->ni',high[:,:,:3],original)+high[:,:,3]
            return a*(1-weight)+z*weight
        lo, hi = endpoint(i), endpoint(i+1)
        if self.reference is not None and 1e-8 < alpha < 1-1e-8:
            from .deflection import rigid_frame
            from .tower_motion import interpolate_transform
            pose = self.poses[i]*(1-alpha)+self.poses[i+1]*alpha
            nacelle = interpolate_transform(self.tower_motion['nacelle'][i],self.tower_motion['nacelle'][i+1],alpha)
            scalars = self.comparison.data['scalars']
            h0,_,a0 = rigid_frame(scalars,self.poses[i,k],b+1,self.tower_motion['nacelle'][i,k])
            h1,_,a1 = rigid_frame(scalars,self.poses[i+1,k],b+1,self.tower_motion['nacelle'][i+1,k])
            hub,_,axes = rigid_frame(scalars,pose[k],b+1,nacelle[k])
            local_shape = ((lo-h0)@a0)*(1-alpha)+((hi-h1)@a1)*alpha
            result = local_shape@axes.T+hub+offset
        else:
            result = lo*(1-alpha)+hi*alpha+offset
        if world:
            return result
        inverse = np.array(obj.matrix_world.inverted())
        return result@inverse[:3,:3].T+inverse[:3,3]

    def deformed_at(self,frame):
        if frame in self.cache:
            performance.count("flex.cache_hit")
            return self.cache[frame]
        performance.count("flex.cache_miss")
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

    @performance.timed("flex.update")
    def update(self,scene, *, sim_time_s=None, record_telemetry=True, update_comparison=True):
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
        t=clearance_replay.sample(scene)['time_s'] if sim_time_s is None else float(sim_time_s)
        if not math.isfinite(t) or t < self.times[0] or t > self.times[-1]:
            raise ValueError('Simulation time outside source geometry range')
        i=int(np.clip(np.searchsorted(self.times,t,side='right')-1,0,len(self.times)-2))
        alpha=float(np.clip((t-self.times[i])/(self.times[i+1]-self.times[i]),0,1))
        performance.count("flex.scene_updates")
        performance.count("flex.participating_turbines", len(self.visible_turbines))
        lower=self.deformed_at(i);upper=self.deformed_at(i+1)
        pose=self.poses[i]*(1-alpha)+self.poses[i+1]*alpha
        if record_telemetry:
            self.record_telemetry(scene, pose)
        scene['wfrl_clearance_time_s']=float(t)
        if scene.wfrl_show_wake:
            from . import wake
            wake.update_proxy_objects(scene, phase=float(t)*.9)
        for k,tid in enumerate(self.readers):
            if getattr(self, 'export_turbines', None) is not None and k not in self.export_turbines:
                continue
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
                if getattr(self, 'export_turbines', None) is not None and k not in self.export_turbines:
                    continue
                obj=scene.objects[f'WFRL.Turbine.{tid}.YawRoot']
                obj.rotation_euler=Matrix(nacelle[k,:,:3].tolist()).to_euler()
                obj.location=nacelle[k,:,:3]@np.array([0,0,87.6])+nacelle[k,:,3]
            for obj,rest,index,weight,k in self.towers:
                phase_started = performance.begin()
                lo,hi=tower[k,index],tower[k,index+1]
                a=np.einsum('nij,nj->ni',lo[:,:,:3],rest)+lo[:,:,3]
                b=np.einsum('nij,nj->ni',hi[:,:,:3],rest)+hi[:,:,3]
                obj.data.vertices.foreach_set('co',((1-weight)*a+weight*b).astype(np.float32).ravel())
                obj.data.update()
                performance.end("flex.tower_interpolate_write", phase_started)
                performance.count("flex.tower_vertices_written", len(rest))
            heights=support['heights']
            for obj,k,z,rest_location in self.fittings:
                j=int(np.clip(np.searchsorted(heights,z)-1,0,len(heights)-2))
                f=float((z-heights[j])/(heights[j+1]-heights[j]))
                tr=interpolate_transform(tower[k,j],tower[k,j+1],f)
                obj.location=tr[:,:3]@rest_location+tr[:,3]
                obj.rotation_euler=Matrix(tr[:,:3].tolist()).to_euler()
        phase_started = performance.begin()
        bpy.context.view_layer.update()
        performance.end("flex.depsgraph_update", phase_started)
        inverses=[np.array(obj.matrix_world.inverted()) for obj,*_ in self.blades]
        for (obj,rest,world,index,weight,k,b,offset),inverse,lo,hi in zip(self.blades,inverses,lower,upper):
            if lo is None:continue
            phase_started = performance.begin()
            if self.reference is not None and 1e-8 < alpha < 1-1e-8:
                # Interpolate deformation in the moving blade root frame.
                # A world-space chord between rotating tips creates artificial
                # axial shortening (about 7 mm at this 40 Hz source rate).
                from .deflection import rigid_frame
                scalars=self.comparison.data['scalars']
                h0,_,a0=rigid_frame(scalars,self.poses[i,k],b+1,self.tower_motion['nacelle'][i,k])
                h1,_,a1=rigid_frame(scalars,self.poses[i+1,k],b+1,self.tower_motion['nacelle'][i+1,k])
                hub,_,axes=rigid_frame(scalars,pose[k],b+1,nacelle[k])
                local_shape=((lo-h0)@a0)*(1-alpha)+((hi-h1)@a1)*alpha
                deformed=local_shape@axes.T+hub+offset
            else:
                deformed=lo*(1-alpha)+hi*alpha+offset
            local=deformed@inverse[:3,:3].T+inverse[:3,3]
            performance.end('flex.interpolation', phase_started)
            phase_started = performance.begin()
            obj.data.vertices.foreach_set('co',local.astype(np.float32).ravel());obj.data.update()
            performance.end('flex.mesh_write_update', phase_started)
            performance.count('flex.blade_updates')
            performance.count('flex.vertices_written', len(local))
        if update_comparison and self.comparison is not None and getattr(self, 'export_turbines', None) is None:
            self.comparison.update(scene, i, alpha, t)
        from .nrel_defects import editor
        editor.after_update(scene, t)


def update(scene,depsgraph=None):
    global _ACTIVE
    active = active_for(scene)
    if active is not None:
        _ACTIVE = active
        active.update(scene)


def detach(scene=None, *, restore=True):
    """Release handlers and mesh references before scene destruction or unload."""
    import bpy
    global _ACTIVE
    if scene is None:
        actives = list(_ACTIVES.values())
    else:
        active = _ACTIVES.get(scene.as_pointer())
        actives = [active] if active is not None else []
    from .nrel_defects import editor
    editor.detach(scene, restore=restore)
    # End camera drafts before invalidating their replay and parent references.
    import sys
    custom = sys.modules.get(__package__ + '.panels.custom_cameras')
    if custom is not None and (scene is None or
            getattr(getattr(custom, '_ACTIVE', None), 'scene', None) == scene):
        custom.cancel_on_load()
    for active in actives:
        _ACTIVES.pop(active.scene.as_pointer(), None)
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
    if _ACTIVE in actives:
        _ACTIVE = next(iter(_ACTIVES.values()), None)
    if not _ACTIVES:
        if update in bpy.app.handlers.frame_change_post:
            bpy.app.handlers.frame_change_post.remove(update)
        if on_load_pre in bpy.app.handlers.load_pre:
            bpy.app.handlers.load_pre.remove(on_load_pre)


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
    try:
        # Validate before releasing the old scene runtime. In particular a
        # FULL_COPY scene contains renamed objects and must not detach an
        # already-restored scene when those required bindings are absent.
        manifest, *_ = read_package(path)
        required = [f'WFRL.Turbine.{tid}.{suffix}'
                    for tid in manifest['turbine_ids']
                    for suffix in ('YawRoot', 'Rotor', 'Tower', 'Blade1', 'Blade2', 'Blade3')]
        missing = [name for name in required if scene.objects.get(name) is None]
        if missing:
            raise ValueError('Scene lacks required FarmFlex objects: ' + ', '.join(missing))
        # Linked scenes share the same object transforms/meshes; two separate
        # timeline clocks cannot safely drive them. Keep the first owner ready.
        for other in _ACTIVES.values():
            if other.scene != scene and any(obj in scene.objects.values() for obj, *_ in other.blades):
                raise ValueError('FarmFlex objects are already driven by scene: ' + other.scene.name)
        detach(scene)
        active=FarmFlex(scene,path)
        _ACTIVES[scene.as_pointer()] = active
        _ACTIVE = active
    except Exception:
        from . import clearance_replay
        detach(scene)
        scene['wfrl_flex_active'] = False
        clearance_replay.clear(scene,'三机形变包未就绪：加载失败')
        raise
    # Tip sampling must follow mesh deformation at the same simulation time.
    if update in bpy.app.handlers.frame_change_post:bpy.app.handlers.frame_change_post.remove(update)
    if tip_tracking.update in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(tip_tracking.update)
    bpy.app.handlers.frame_change_post.append(update)
    from bpy.app.handlers import persistent
    persistent(on_load_pre)
    if on_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(on_load_pre)
    tip_tracking.register()
    from .nrel_defects import editor
    editor.restore_saved(scene)
    # Freeze editor drafts after the replay handler has sampled the new frame.
    if editor.frame_changed in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(editor.frame_changed)
    bpy.app.handlers.frame_change_post.append(editor.frame_changed)
    scene.frame_set(1)
    return active
