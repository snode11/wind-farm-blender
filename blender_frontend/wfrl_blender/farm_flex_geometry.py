"""Replay geometry, native coordinate writes and binding-aware deformation caches."""
import math

import numpy as np

from . import farm_flex as _farm
from . import performance


def _interpolate_poses(a, b, alpha):
    """Match the reader's shortest yaw arc while retaining unwrapped rotor phase."""
    pose = a * (1-alpha) + b * alpha
    if 0 < alpha < 1:
        delta = b[..., 0] - a[..., 0]
        wrapped = (delta >= 180) | (delta < -180)
        pose[..., 0] = np.where(wrapped, a[..., 0] + alpha * ((delta + 180) % 360 - 180), pose[..., 0])
    return pose


def _readonly_arrays(value):
    if isinstance(value, np.ndarray):
        value.setflags(write=False)
    elif isinstance(value, dict):
        for item in value.values():
            _farm._readonly_arrays(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _farm._readonly_arrays(item)


def _update_scene_transforms(scene):
    """Evaluate the replay scene even when another scene is current."""
    import bpy
    view_layer = bpy.context.view_layer if bpy.context.scene == scene else scene.view_layers[0]
    view_layer.update()


def _rna_identity(value):
    """Distinguish native data replacements as well as ordinary Python owners."""
    pointer = getattr(value, 'as_pointer', None)
    return pointer() if pointer is not None else id(value)


def _array_state(value):
    array = np.asarray(value)
    return array.shape, array.dtype.str, array.tobytes()


def _bound_array_state(value):
    # Bound reference arrays own read-only storage. Native mesh edits are
    # checked separately; rebind_blade replaces these arrays after rest edits.
    if value.flags.owndata and not value.flags.writeable:
        return id(value)
    return _farm._array_state(value)


def _write_coordinates(obj, coordinates, *, check_current):
    """Skip a reused write only after checking the current native coordinates."""
    values = np.asarray(coordinates, dtype=np.float32).reshape(-1)
    mesh = obj.data
    if len(mesh.vertices) * 3 != len(values):
        raise ValueError('Replay mesh topology no longer matches its binding: ' + obj.name)
    if check_current and not getattr(mesh, 'is_editmode', False):
        current = np.empty_like(values)
        mesh.vertices.foreach_get('co', current)
        if current.tobytes() == values.tobytes():
            performance.count('flex.mesh_writes_skipped')
            return False
    mesh.vertices.foreach_set('co', values)
    mesh.update()
    return True


class _EvaluatedTransformRequired(Exception):
    pass


def _world_from_basis(obj, cache):
    """Match the depsgraph's two float32 products at each object parent link."""
    key = obj.as_pointer()
    if key not in cache:
        animation = obj.animation_data
        parent = obj.parent
        if (obj.constraints or (animation is not None and animation.drivers)
                or obj.rigid_body is not None
                or (parent is not None and (obj.parent_type != 'OBJECT'
                    or (parent.type == 'CURVE' and parent.data.use_path)))):
            raise _farm._EvaluatedTransformRequired(obj.name)
        basis = np.array(obj.matrix_basis, dtype=np.float32)
        if parent is None:
            cache[key] = basis
        else:
            parent_world = _farm._world_from_basis(parent, cache)
            parent_inverse = np.array(obj.matrix_parent_inverse, dtype=np.float32)
            cache[key] = (parent_world @ parent_inverse).astype(np.float32) @ basis
    return cache[key]


class _WorldMatrices:
    """One replay update's matrices, with one evaluated fallback for the scene."""
    def __init__(self, scene, key):
        self.scene, self.key = scene, key
        self.matrices = {}
        self.evaluated = False

    def evaluate(self):
        phase_started = performance.begin()
        _farm._update_scene_transforms(self.scene)
        performance.end('flex.depsgraph_update', phase_started)
        self.matrices.clear()
        self.evaluated = True

    def world(self, obj):
        if not self.evaluated:
            try:
                from mathutils import Matrix
                return Matrix(_farm._world_from_basis(obj, self.matrices).tolist())
            except _farm._EvaluatedTransformRequired:
                self.evaluate()
        return obj.matrix_world.copy()


class _Geometry:
    def restore_support(self):
        self._world_matrices = None
        self._update_geometry = None
        for obj,rest,*_ in self.towers:
            try:
                obj.data.vertices.foreach_set('co',rest.ravel())
                obj.data.update()
            except ReferenceError: pass
        for obj,k,z,rest_location in self.fittings:
            try:
                obj.location=rest_location
                obj.rotation_euler=(0,0,0)
            except ReferenceError: pass
        if self.tower_motion is not None:
            for tid in self.readers:
                try:
                    obj=self.scene.objects.get(f'WFRL.Turbine.{tid}.YawRoot')
                    if obj:
                        obj.location=(0,0,87.6)
                        obj.rotation_euler=(0,0,0)
                except ReferenceError:
                    pass

    @performance.timed("flex.rebind_blade")
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
        self.groups[obj.name] = [(s,np.flatnonzero(index==s).copy()) for s in np.unique(index)]
        _farm._readonly_arrays((self.blades[position][1:], self.groups[obj.name]))
        self.cache.clear()
        self._world_matrices = None
        self._update_geometry = None
        self._deformation_batches = None

    def _world_cache(self):
        import bpy
        exported = self.export_turbines
        key = (self.scene.frame_current, self.scene.frame_subframe,
               self.scene == bpy.context.scene,
               frozenset(self.visible_turbines),
               None if exported is None else frozenset(exported))
        if self._world_matrices is None or self._world_matrices.key != key:
            self._world_matrices = _farm._WorldMatrices(self.scene, key)
            # Other scenes and isolated exports need evaluated data on return.
            if self.scene != bpy.context.scene or exported is not None:
                self._world_matrices.evaluate()
        return self._world_matrices

    def world_matrix(self, obj):
        """Read the rigid pose written by replay before the depsgraph evaluates."""
        return self._world_cache().world(obj)

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
            pose = _interpolate_poses(self.poses[i], self.poses[i+1], alpha)
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
        inverse = np.array(self.world_matrix(obj).inverted())
        return result@inverse[:3,:3].T+inverse[:3,3]

    def mesh_turbines(self):
        """Turbines whose blade meshes are consumed by the view or export."""
        exported = self.export_turbines
        return frozenset(self.visible_turbines if exported is None else exported)

    def _interpolate_blade(self, row, lo, hi, i, alpha, pose, nacelle):
        _, _, _, _, _, k, b, offset = row
        if self.reference is not None and 1e-8 < alpha < 1-1e-8:
            # Interpolate in the moving root frame to avoid rotation-induced
            # shortening from a world-space chord between source samples.
            from .deflection import rigid_frame
            scalars = self.comparison.data['scalars']
            h0, _, a0 = rigid_frame(scalars, self.poses[i,k], b+1, self.tower_motion['nacelle'][i,k])
            h1, _, a1 = rigid_frame(scalars, self.poses[i+1,k], b+1, self.tower_motion['nacelle'][i+1,k])
            hub, _, axes = rigid_frame(scalars, pose[k], b+1, nacelle[k])
            local_shape = ((lo-h0)@a0)*(1-alpha)+((hi-h1)@a1)*alpha
            return local_shape@axes.T+hub+offset
        return lo*(1-alpha)+hi*alpha+offset

    def _structural_tip_local(self, row, i, alpha, pose, nacelle):
        """Use identical point arithmetic for visible and hidden measurements."""
        obj, _, world, index, weight, k, b, _ = row
        section = index[-2]
        points = world[-2:-1]
        w = weight[-2:-1]
        def endpoint(frame):
            lo, hi = self.transforms[frame,k,b,section:section+2]
            a = points@lo[:,:3].T+lo[:,3]
            z = points@hi[:,:3].T+hi[:,3]
            return (a*(1-w)+z*w).astype(world.dtype)
        deformed = self._interpolate_blade(row, endpoint(i), endpoint(i+1), i, alpha, pose, nacelle)
        inverse = np.array(self.world_matrix(obj).inverted())
        return (deformed@inverse[:3,:3].T+inverse[:3,3]).astype(np.float32)[0]

    def structural_tip_world(self, blade, i, alpha):
        """Transport a hidden T1 tip with the mesh's bound rest and precision."""
        from mathutils import Vector
        row = next(row for row in self.blades if row[5:7] == (0, blade-1))
        pose = _interpolate_poses(self.poses[i], self.poses[i+1], alpha)
        nacelle = None
        if self.reference is not None and 1e-8 < alpha < 1-1e-8:
            from .tower_motion import interpolate_transform
            nacelle = interpolate_transform(self.tower_motion['nacelle'][i], self.tower_motion['nacelle'][i+1], alpha)
        local = self._structural_tip_local(row, i, alpha, pose, nacelle)
        performance.count('flex.measurement_points', 1)
        return self.world_matrix(row[0]) @ Vector(local)

    def _blade_inputs(self):
        return tuple((_farm._rna_identity(row[0]), _farm._rna_identity(row[0].data),
                      row[0].name, len(row[0].data.vertices),
                      *(_farm._bound_array_state(value) for value in row[1:5]), row[5:7],
                      _farm._bound_array_state(row[7]),
                      tuple((section, _farm._bound_array_state(ids))
                            for section, ids in self.groups[row[0].name]))
                     for row in self.blades)

    def _geometry_sample(self, reader, t, i, alpha):
        """Cache one sample's pure geometry; consumers and native state stay live."""
        blades = self._blade_inputs()
        support = self.tower_motion
        support_key = None if support is None else (
            id(support), _farm._array_state(support['heights']),
            _farm._array_state(support['transforms'][i:i+2]),
            _farm._array_state(support['nacelle'][i:i+2]))
        comparison = self.comparison
        scalars = None if comparison is None else tuple(sorted(comparison.data['scalars'].items()))
        exported = self.export_turbines
        key = (_farm._rna_identity(self.scene), id(reader),
               tuple((name, id(value)) for name, value in self.readers.items()),
               self.scene.get('wfrl_farm_flex_path'), self.scene.get('wfrl_farm_manifest_sha256'),
               self.scene.get('wfrl_blade_model'), self.model,
               t, i, alpha, id(self.times), id(self.transforms), id(self.poses),
               _farm._array_state(self.times[i:i+2]), _farm._array_state(self.transforms[i:i+2]),
               _farm._array_state(self.poses[i:i+2]), support_key,
               id(self.reference), id(comparison), scalars, blades,
               tuple((_farm._rna_identity(obj), _farm._rna_identity(obj.data), len(obj.data.vertices),
                      _farm._bound_array_state(rest), _farm._bound_array_state(index), _farm._bound_array_state(weight), k)
                     for obj, rest, index, weight, k in self.towers),
               frozenset(self.visible_turbines),
               None if exported is None else frozenset(exported))
        cached = self._update_geometry
        if cached is not None and cached[0] == key:
            performance.count('flex.update_cache_hit')
            return cached[1]
        performance.count('flex.update_cache_miss')
        sample = dict(lower=self.deformed_at(i, _bindings=blades),
                      upper=self.deformed_at(i+1, _bindings=blades),
                      pose=_interpolate_poses(self.poses[i], self.poses[i+1], alpha),
                      blades={}, towers={})
        if support is not None:
            from .tower_motion import interpolate_transform
            sample['tower'] = support['transforms'][i]*(1-alpha)+support['transforms'][i+1]*alpha
            sample['nacelle'] = interpolate_transform(support['nacelle'][i], support['nacelle'][i+1], alpha)
        self._update_geometry = key, sample
        return sample

    def _deformation_batches_for(self, bindings):
        """Group equal-sized sections without changing each matrix product's shape."""
        key = (_farm._rna_identity(self.scene), id(self.transforms), bindings)
        cached = self._deformation_batches
        if cached is not None and cached[0] == key:
            return cached[1]
        batches = []
        for obj, _rest, world, _index, weight, *_ in self.blades:
            sizes = {}
            for section, ids in self.groups[obj.name]:
                sizes.setdefault(len(ids), []).append((section, ids))
            blade = []
            for groups in sizes.values():
                sections = np.asarray([section for section, _ids in groups])
                ids = np.asarray([ids for _section, ids in groups])
                weights = weight[ids]
                blade.append((sections, ids, world[ids], weights, 1-weights))
            batches.append(blade)
        self._deformation_batches = key, batches
        return batches

    @performance.timed("flex.deformation_cache")
    def deformed_at(self,frame, *, _bindings=None):
        turbines = self.mesh_turbines()
        bindings = self._blade_inputs() if _bindings is None else _bindings
        key = (frame, turbines, _farm._rna_identity(self.scene), id(self.transforms),
               _farm._array_state(self.transforms[frame]), bindings)
        if key in self.cache:
            performance.count("flex.cache_hit")
            return self.cache[key]
        performance.count("flex.cache_miss")
        transforms=self.transforms[frame]
        batches = self._deformation_batches_for(bindings)
        result=[]
        for row, blade_batches in zip(self.blades, batches):
            obj,rest,world,index,weight,k,b,offset = row
            if k not in turbines:
                result.append(None)
                continue
            deformed=np.empty_like(world)
            for sections,ids,points,weights,inverse_weights in blade_batches:
                lo,hi=transforms[k,b,sections],transforms[k,b,sections+1]
                a=points@lo[:,:,:3].transpose(0,2,1)+lo[:,None,:,3]
                z=points@hi[:,:,:3].transpose(0,2,1)+hi[:,None,:,3]
                deformed[ids]=a*inverse_weights+z*weights
            result.append(deformed)
        self.cache[key]=result
        while len(self.cache)>3:self.cache.pop(next(iter(self.cache)))
        return result

    def _preflight_frame(self, scene, mesh_turbines, support_turbines):
        for obj, rest, _world, _index, _weight, k, _blade, _offset in self.blades:
            if k not in mesh_turbines:
                continue
            if scene.objects.get(obj.name) != obj or len(obj.data.vertices) != len(rest):
                raise ValueError('Replay mesh topology no longer matches its binding: ' + obj.name)
            if getattr(obj.data, 'is_editmode', False):
                raise ValueError('Replay mesh is in edit mode: ' + obj.name)
        for obj, rest, _index, _weight, k in self.towers:
            if k in support_turbines and (scene.objects.get(obj.name) != obj
                    or len(obj.data.vertices) != len(rest)):
                raise ValueError('Replay mesh topology no longer matches its binding: ' + obj.name)
        for k, tid in enumerate(self.readers):
            if self.export_turbines is not None and k not in self.export_turbines:
                continue
            for suffix in ('YawRoot', 'Rotor', 'Blade1', 'Blade2', 'Blade3'):
                obj = scene.objects[f'WFRL.Turbine.{tid}.{suffix}']
                tuple(getattr(obj.rotation_euler, axis) for axis in ('x', 'y', 'z'))
        for obj, k, _z, _rest in self.fittings:
            if k in support_turbines and scene.objects.get(obj.name) != obj:
                raise ValueError('Replay support binding is unavailable: ' + obj.name)

    @performance.timed("flex.update")
    def update(self,scene, *, sim_time_s=None, record_telemetry=True, update_comparison=True):
        if scene != self.scene or not self.enabled:
            return
        if self.frame_error:
            raise ValueError('Replay frame unavailable; reload the package: ' + self.frame_error)
        previous_time = scene.get('wfrl_clearance_time_s')
        try:
            self._update_frame(scene, sim_time_s=sim_time_s,
                               record_telemetry=record_telemetry, update_comparison=update_comparison)
        except (ValueError, RuntimeError, ReferenceError, KeyError, IndexError, TypeError) as exc:
            from . import clearance_replay, playback
            self.frame_error = str(exc)
            clearance_replay._READERS.pop(scene.as_pointer(), None)
            self._update_geometry = None
            scene['wfrl_flex_frame_valid'] = False
            scene['wfrl_home_load_error'] = '回放帧无效，请重新加载结果：' + str(exc)
            if previous_time is None:
                scene.pop('wfrl_clearance_time_s', None)
            else:
                scene['wfrl_clearance_time_s'] = previous_time
            reason = '回放帧无效：' + str(exc)
            for cleanup in (lambda: playback.cancel(scene),
                            lambda: clearance_replay.clear(scene, reason)):
                try:
                    cleanup()
                except Exception as cleanup_error:
                    scene['wfrl_home_load_error'] += '; 清理待确认：' + str(cleanup_error)
            raise

    def _update_frame(self,scene, *, sim_time_s=None, record_telemetry=True, update_comparison=True):
        from . import clearance_replay
        if scene!=self.scene or not self.enabled:return
        self._world_matrices = None
        reader=clearance_replay.reader_for(scene)
        if reader not in self.readers.values():
            for obj,rest,*_ in self.blades:
                obj.data.vertices.foreach_set('co',rest.ravel())
                obj.data.update()
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
        mesh_turbines = self.mesh_turbines()
        support_turbines = set(mesh_turbines)
        # Reconstruction can first share T1's support while playback is paused.
        # Keep that source current even before a shared group has been created.
        if self.export_turbines is None:
            support_turbines.add(0)
        performance.count("flex.participating_turbines", len(mesh_turbines))
        self._preflight_frame(scene, mesh_turbines, support_turbines)
        sample = self._geometry_sample(reader, t, i, alpha)
        lower, upper, pose = sample['lower'], sample['upper'], sample['pose']
        if scene.wfrl_show_wake:
            from . import wake
            wake.update_proxy_objects(scene, phase=float(t)*.9)
        for k,tid in enumerate(self.readers):
            if self.export_turbines is not None and k not in self.export_turbines:
                continue
            scene.objects[f'WFRL.Turbine.{tid}.YawRoot'].rotation_euler.z=math.radians(float(pose[k,0]))
            scene.objects[f'WFRL.Turbine.{tid}.Rotor'].rotation_euler.x=math.radians(float(pose[k,1]))
            for b in (1,2,3):
                scene.objects[f'WFRL.Turbine.{tid}.Blade{b}'].rotation_euler.z=-math.radians(float(pose[k,b+2]))
        if self.tower_motion is not None:
            from mathutils import Matrix
            from .tower_motion import interpolate_transform
            support = self.tower_motion
            tower, nacelle = sample['tower'], sample['nacelle']
            for k,tid in enumerate(self.readers):
                if self.export_turbines is not None and k not in self.export_turbines:
                    continue
                obj=scene.objects[f'WFRL.Turbine.{tid}.YawRoot']
                obj.rotation_euler=Matrix(nacelle[k,:,:3].tolist()).to_euler()
                obj.location=nacelle[k,:,:3]@np.array([0,0,87.6])+nacelle[k,:,3]
            for obj,rest,index,weight,k in self.towers:
                if k not in support_turbines:
                    continue
                phase_started = performance.begin()
                key = _farm._rna_identity(obj)
                reused = key in sample['towers']
                if not reused:
                    lo,hi=tower[k,index],tower[k,index+1]
                    a=np.einsum('nij,nj->ni',lo[:,:,:3],rest)+lo[:,:,3]
                    b=np.einsum('nij,nj->ni',hi[:,:,:3],rest)+hi[:,:,3]
                    sample['towers'][key] = ((1-weight)*a+weight*b).astype(np.float32)
                written = _farm._write_coordinates(obj, sample['towers'][key], check_current=reused)
                performance.end("flex.tower_interpolate_write", phase_started)
                if written:
                    performance.count("flex.tower_vertices_written", len(rest))
            heights=support['heights']
            for obj,k,z,rest_location in self.fittings:
                if k not in support_turbines:
                    continue
                j=int(np.clip(np.searchsorted(heights,z)-1,0,len(heights)-2))
                f=float((z-heights[j])/(heights[j+1]-heights[j]))
                tr=interpolate_transform(tower[k,j],tower[k,j+1],f)
                obj.location=tr[:,:3]@rest_location+tr[:,3]
                obj.rotation_euler=Matrix(tr[:,:3].tolist()).to_euler()
        self._world_cache()
        for row,lo,hi in zip(self.blades,lower,upper):
            if lo is None:continue
            obj = row[0]
            inverse = np.array(self.world_matrix(obj).inverted())
            key = _farm._rna_identity(obj)
            matrix_key = _farm._array_state(inverse)
            cached = sample['blades'].get(key)
            reused = cached is not None and cached[0] == matrix_key
            if not reused:
                phase_started = performance.begin()
                deformed = self._interpolate_blade(row, lo, hi, i, alpha, pose,
                                                   nacelle if self.tower_motion is not None else None)
                local=deformed@inverse[:3,:3].T+inverse[:3,3]
                if row[5] == 0 and self.comparison is not None:
                    # The loose structural vertex and hidden numerical sample use
                    # identical float32 transport, independent of mesh batch size.
                    local[-2] = self._structural_tip_local(row, i, alpha, pose,
                                                          nacelle if self.tower_motion is not None else None)
                sample['blades'][key] = matrix_key, local.astype(np.float32)
                performance.end('flex.interpolation', phase_started)
            local = sample['blades'][key][1]
            phase_started = performance.begin()
            written = _farm._write_coordinates(obj, local, check_current=reused)
            performance.end('flex.mesh_write_update', phase_started)
            if written:
                performance.count('flex.blade_updates')
                performance.count('flex.vertices_written', len(local))
        if update_comparison and self.comparison is not None and self.export_turbines is None:
            self.comparison.update(scene, i, alpha, t)
        scene['wfrl_clearance_time_s'] = float(t)
        from .nrel_defects import editor
        editor.after_update(scene, t)
        # Reuse the current source structures only after their pose and flexible
        # tower mesh have been committed. Reconstruction samples remain separate.
        from . import split_turbine_context
        split_turbine_context.sync(scene)
        scene['wfrl_flex_frame_valid'] = True
        if record_telemetry:
            self.record_telemetry(scene, pose)
