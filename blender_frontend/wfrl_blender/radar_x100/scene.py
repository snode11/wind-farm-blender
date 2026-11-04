"""Read-only T1 surface and mount sampling from one saved FarmFlex source."""
import hashlib
import json
import math
from copy import copy
from contextlib import contextmanager
import numpy as np
from .core import simulation, schema, analysis
from .. import performance

ORIGINAL_LOCATION = (-3.5, -1.7, -2.0)
ORIGINAL_ROTATION = (0.0, 82.5, 180.0)


def _calibration_identity(mount, matrix):
    return schema.digest(dict(mount=mount, matrix=matrix.tolist(),
                              origin='approximate housing center; phase center unknown'))


def installation_matrix(location, rotation_deg):
    from mathutils import Euler
    matrix = np.eye(4)
    matrix[:3, :3] = np.array(Euler(tuple(math.radians(x) for x in rotation_deg), 'XYZ').to_matrix())
    matrix[:3, 3] = location
    return matrix


def nacelle_side_installation(sampler):
    """Place the housing outside the negative-Y nacelle wall, looking sideways."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    sampler.validate()
    vertices = (sampler.shell_vertices @ sampler.shell_local[:3, :3].T
                + sampler.shell_local[:3, 3])
    center = vertices.mean(axis=0)
    origin = Vector((float(center[0] - 1.0), float(vertices[:, 1].min() - 1.0),
                     float(center[2])))
    tree = BVHTree.FromPolygons(vertices.tolist(),
                               sampler._triangles(sampler.shell_mesh).tolist(), all_triangles=True)
    point, normal, _, _ = tree.ray_cast(origin, Vector((0.0, 1.0, 0.0)))
    if point is None or normal.y > -0.8:
        raise ValueError('当前机舱没有可用的侧壁安装面，请检查静态外壳')
    location = point + normal * 0.04
    return tuple(location), (0.0, 0.0, -100.0)


class SceneSampler:
    """Snapshot static bindings; arbitrary source times never change Blender clocks."""
    def __init__(self, flex, *, turbine_id='T1', mount='NACELLE', mount_from_sensor=None):
        from ..turbine_geometry import geometry_data
        self.flex, self.turbine_id, self.mount = flex, turbine_id, mount
        self.scene = flex.scene
        self.k = list(flex.readers).index(turbine_id)
        self.interval = (float(flex.times[0]), float(flex.times[-1]))
        self.mount_from_sensor = simulation.rigid_matrix(mount_from_sensor)
        self.source_id = flex.scene['wfrl_farm_manifest_sha256']
        self.height = geometry_data()['scalars']['TowerHt']
        yaw = flex.scene.objects[f'WFRL.Turbine.{turbine_id}.YawRoot']
        root = yaw.parent
        if root.parent is not None or root.constraints or root.animation_data:
            raise ValueError('X100 source root must have a static recorded-layout transform')
        self.root = root
        self.root_basis = np.array(root.matrix_basis)
        self.root_world = simulation.rigid_matrix(np.array(root.matrix_world))
        if (not np.allclose(self.root_world[:3, :3], np.eye(3), atol=1e-6)
                or not np.allclose(self.root_world[:3, 3], flex.manifest['layout_m'][self.k], atol=1e-4)):
            raise ValueError('X100 source root does not match saved geometry layout')
        self.blades = [row for row in flex.blades if row[5] == self.k]
        self.model = flex.model
        self.rest_bindings = tuple((row[0].as_pointer(), id(row[1])) for row in self.blades)
        self.towers = [row for row in flex.towers if row[-1] == self.k]
        self.tower_bindings = tuple((row[0].as_pointer(), *(id(value) for value in row[1:-1]))
                                   for row in self.towers)
        shell = flex.scene.objects[f'WFRL.Turbine.{turbine_id}.Nacelle']
        if (shell.constraints or shell.animation_data or shell.parent != yaw
                or shell.mode != 'OBJECT' or shell.modifiers or shell.data.shape_keys is not None):
            raise ValueError('X100 requires a static nacelle shell mesh without deformation drivers, local to the recorded yaw root')
        self.shell, self.shell_mesh, self.shell_parent = shell, shell.data, yaw
        self.shell_local = np.array(shell.matrix_parent_inverse, dtype=np.float64) @ np.array(shell.matrix_basis, dtype=np.float64)
        self.shell_vertices = self._vertices(shell.data)
        self.shell_topology = self._topology(shell.data)
        self.parts = []
        for obj, *_ in self.blades:
            self.parts.append((obj.name, self._triangles(obj.data)))
        if self.towers:
            for obj, *_ in self.towers:
                self.parts.append((obj.name, self._triangles(obj.data)))
        else:
            tower = flex.scene.objects[f'WFRL.Turbine.{turbine_id}.Tower']
            self.tower = tower
            self.rigid_tower = (np.array([v.co[:] for v in tower.data.vertices]), np.array(tower.matrix_world))
            self.parts.append((tower.name, self._triangles(tower.data)))
        self.parts.append((shell.name, self._triangles(shell.data)))
        surfaces = [row[0] for row in (*self.blades, *self.towers)] + [shell]
        if not self.towers:
            surfaces.append(self.tower)
        dynamic = {yaw.as_pointer(), *(row[0].as_pointer() for row in self.blades),
                   flex.scene.objects[f'WFRL.Turbine.{turbine_id}.Rotor'].as_pointer()}
        self.object_bindings = {}
        self.mesh_bindings = []
        for obj in surfaces:
            self.mesh_bindings.append((obj, obj.data, self._topology(obj.data), len(obj.data.vertices)))
            node = obj
            while node is not None and node.as_pointer() not in self.object_bindings:
                self.object_bindings[node.as_pointer()] = (node, node.name, node.parent,
                    np.array(node.matrix_parent_inverse),
                    None if node.as_pointer() in dynamic else np.array(node.matrix_basis))
                node = node.parent
        rest_parts = [row[1].tobytes() for row in self.blades] + [
            self.shell_vertices.tobytes(), self.shell_local.tobytes(), self.root_world.tobytes()]
        rest_parts.extend(row[1].tobytes() for row in self.towers)
        if not self.towers:
            rest_parts.extend(array.tobytes() for array in self.rigid_tower)
        self.geometry_id = schema.digest(dict(source_id=self.source_id, model=flex.model,
            topology=[(name, triangles.tolist()) for name, triangles in self.parts],
            rest_sha256=hashlib.sha256(b''.join(rest_parts)).hexdigest()))
        counts = [len(row[1]) for row in self.blades]
        counts.extend(len(row[1]) for row in self.towers)
        if not self.towers:
            counts.append(len(self.rigid_tower[0]))
        counts.append(len(self.shell_vertices))
        offsets = np.cumsum([0, *counts[:-1]])
        self.triangles = np.concatenate([faces + offset
            for (_, faces), offset in zip(self.parts, offsets)]).astype(np.int32)
        self.triangles.flags.writeable = False
        self.triangle_offsets = np.cumsum([0, *(len(faces) for _, faces in self.parts)])
        self._material_ids, self._material_plan = None, None
        self._sampling_plans = {}
        self.latest_sample = None
        self._sampling_validated = False
        self._visibility_cache = {}
        self.calibration_id = _calibration_identity(mount, self.mount_from_sensor)
        self.validate()

    def with_installation(self, mount, matrix):
        """Share the geometry snapshot with an independent installation calibration."""
        result = copy(self)
        result.mount = mount
        result.mount_from_sensor = simulation.rigid_matrix(matrix)
        result.calibration_id = _calibration_identity(mount, result.mount_from_sensor)
        return result

    @staticmethod
    def _triangles(mesh):
        mesh.calc_loop_triangles()
        return np.array([tri.vertices[:] for tri in mesh.loop_triangles], dtype=np.int32)

    @staticmethod
    def _vertices(mesh):
        vertices = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
        mesh.vertices.foreach_get('co', vertices)
        return vertices.reshape(-1, 3)

    @staticmethod
    def _topology(mesh):
        loops = np.empty(len(mesh.loops), dtype=np.int32)
        sizes = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.loops.foreach_get('vertex_index', loops)
        mesh.polygons.foreach_get('loop_total', sizes)
        return loops, sizes

    def mount_world_at(self, time_s, *, mount=None):
        from ..tower_motion import interpolate_transform
        t = float(time_s)
        mount = self.mount if mount is None else mount
        if not self.interval[0] <= t <= self.interval[1]:
            raise ValueError('X100 sample time outside saved source')
        if mount == 'WORLD':
            return np.eye(4)
        if mount == 'TOWER_BASE':
            return self.root_world.copy()
        if mount != 'NACELLE':
            raise ValueError('Unsupported X100 mount')
        i = int(np.clip(np.searchsorted(self.flex.times, t, side='right') - 1, 0, len(self.flex.times) - 2))
        alpha = float((t - self.flex.times[i]) / (self.flex.times[i + 1] - self.flex.times[i]))
        local = np.eye(4)
        if self.flex.tower_motion is None:
            angle = math.radians(float(self.flex.poses[i, self.k, 0] * (1 - alpha) + self.flex.poses[i + 1, self.k, 0] * alpha))
            local[:3, :3] = [[math.cos(angle), -math.sin(angle), 0], [math.sin(angle), math.cos(angle), 0], [0, 0, 1]]
            local[2, 3] = self.height
        else:
            tr = interpolate_transform(self.flex.tower_motion['nacelle'][i, self.k],
                                       self.flex.tower_motion['nacelle'][i + 1, self.k], alpha)
            local[:3, :3] = tr[:, :3]
            local[:3, 3] = tr[:, :3] @ np.array([0, 0, self.height]) + tr[:, 3]
        return self.root_world @ local

    @performance.timed("x100.validate")
    def validate(self):
        flex = self.flex
        if (not flex.enabled or getattr(flex, 'frame_error', '') or flex.scene != self.scene
                or flex.scene.get('wfrl_farm_manifest_sha256') != self.source_id):
            raise ValueError('X100 scene source was released')
        if flex.model != self.model or tuple((row[0].as_pointer(), id(row[1]))
                for row in flex.blades if row[5] == self.k) != self.rest_bindings:
            raise ValueError('X100 material bindings changed; apply the installation again')
        if tuple((row[0].as_pointer(), *(id(value) for value in row[1:-1]))
                 for row in flex.towers if row[-1] == self.k) != self.tower_bindings:
            raise ValueError('X100 tower material bindings changed; apply the installation again')
        for obj, name, parent, inverse, basis in self.object_bindings.values():
            if (self.scene.objects.get(name) != obj or obj.parent != parent
                    or obj.parent_type != 'OBJECT' or obj.constraints or obj.animation_data
                    or not np.array_equal(np.array(obj.matrix_parent_inverse), inverse)
                    or (basis is not None and not np.array_equal(np.array(obj.matrix_basis), basis))):
                raise ValueError(f'X100 source object or parent binding changed: {name}; apply the installation again')
        for obj, mesh, topology, count in self.mesh_bindings:
            if (obj.data != mesh or len(mesh.vertices) != count
                    or any(not np.array_equal(actual, expected)
                           for actual, expected in zip(self._topology(mesh), topology))):
                raise ValueError(f'X100 source mesh binding changed: {obj.name}; apply the installation again')
        if (self.root.parent is not None or self.root.constraints or self.root.animation_data
                or not np.array_equal(np.array(self.root.matrix_basis), self.root_basis)):
            raise ValueError('X100 source layout changed; restore the recorded layout and apply again')
        if not self.towers and not np.array_equal(self._vertices(self.tower.data), self.rigid_tower[0]):
            raise ValueError('X100 static tower changed; apply the installation again')
        shell = self.shell
        if (flex.scene.objects.get(shell.name) != shell or shell.data != self.shell_mesh
                or shell.parent != self.shell_parent or shell.constraints or shell.animation_data
                or shell.mode != 'OBJECT' or shell.modifiers or shell.data.shape_keys is not None
                or not np.array_equal(np.array(shell.matrix_parent_inverse, dtype=np.float64)
                    @ np.array(shell.matrix_basis, dtype=np.float64), self.shell_local)
                or not np.array_equal(self._vertices(shell.data), self.shell_vertices)
                or any(not np.array_equal(actual, expected) for actual, expected
                       in zip(self._topology(shell.data), self.shell_topology))):
            raise ValueError('X100 nacelle shell changed; apply the installation again')

    def _part_vertices(self, part, time_s, indices=None):
        flex = self.flex
        def selected(array):
            return array if indices is None else array[indices]
        if part < len(self.blades):
            obj, rest, *_ = self.blades[part]
            return flex.deform_points(obj, selected(rest), time_s, world=True, high_precision=True)
        part -= len(self.blades)
        if self.towers and part < len(self.towers):
            i = int(np.clip(np.searchsorted(flex.times, time_s, side='right') - 1, 0, len(flex.times) - 2))
            alpha = float((time_s - flex.times[i]) / (flex.times[i + 1] - flex.times[i]))
            before = np.asarray(flex.tower_motion['transforms'][i],dtype=np.float64)
            after = np.asarray(flex.tower_motion['transforms'][i+1],dtype=np.float64)
            transforms = before * (1-alpha) + after * alpha
            obj, rest, index, weight, k = self.towers[part]
            rest, index, weight = (np.asarray(selected(rest),dtype=np.float64),selected(index),
                                   np.asarray(selected(weight),dtype=np.float64))
            low, high = transforms[k, index], transforms[k, index + 1]
            a = np.einsum('nij,nj->ni', low[:, :, :3], rest) + low[:, :, 3]
            b = np.einsum('nij,nj->ni', high[:, :, :3], rest) + high[:, :, 3]
            xyz = a * (1 - weight) + b * weight
            return xyz @ self.root_world[:3, :3].T + self.root_world[:3, 3]
        if not self.towers and part == 0:
            rest, matrix = self.rigid_tower
            return selected(rest) @ matrix[:3, :3].T + matrix[:3, 3]
        # The physical shell follows the same saved nacelle pose even for world-fixed radar mounts.
        yaw = self.mount_world_at(time_s, mount='NACELLE')
        shell_matrix = yaw @ self.shell_local
        return selected(self.shell_vertices) @ shell_matrix[:3, :3].T + shell_matrix[:3, 3]

    @contextmanager
    def sampling_transaction(self):
        """Reuse a binding check only during one synchronous read-only observation."""
        previous = self._sampling_validated
        if not previous:
            self.validate()
        self._sampling_validated = True
        try:
            yield
        finally:
            self._sampling_validated = previous

    def _validate_sampling(self):
        if not self._sampling_validated:
            self.validate()

    def sample(self, time_s):
        self._validate_sampling()
        vertices = [self._part_vertices(part, time_s) for part in range(len(self.parts))]
        sample = simulation.SceneSample(float(time_s), self.source_id, self.geometry_id,
            np.concatenate(vertices), self.triangles, self.mount_world_at(time_s) @ self.mount_from_sensor,
            'T1 saved blade presentation surfaces, flexible tower and nacelle shell; other structures/environment excluded')
        self.latest_sample = sample
        return sample


    def sampling_plan(self, budget=512, seed=100, method='AREA'):
        from .core import sampling
        key = budget, seed, method
        if key not in self._sampling_plans:
            self._sampling_plans[key] = sampling.make_plan(self.sample(self.interval[0]), budget, seed, method)
            if len(self._sampling_plans) > 16:
                self._sampling_plans.pop(next(iter(self._sampling_plans)))
        return self._sampling_plans[key]

    def material_sample(self, time_s, triangle_ids, *, barycentric=None):
        """Transport fixed material coordinates using only their bound rest vertices."""
        self._validate_sampling()
        ids = np.asarray(triangle_ids, dtype=np.int32)
        if ids.ndim != 1 or np.any(ids < 0) or np.any(ids >= len(self.triangles)):
            raise ValueError('Invalid material triangle identity')
        if self._material_ids is None or not np.array_equal(ids, self._material_ids):
            plan = []
            for part, (_, faces) in enumerate(self.parts):
                lo, hi = self.triangle_offsets[part:part+2]
                destinations = np.flatnonzero((ids >= lo) & (ids < hi))
                if len(destinations):
                    vertices, inverse = np.unique(faces[ids[destinations]-lo], return_inverse=True)
                    plan.append((part, destinations, vertices, inverse.reshape(-1, 3)))
            self._material_ids, self._material_plan = ids.copy(), plan
        points = np.empty((len(ids), 3), dtype=np.float64)
        weights = np.full((len(ids), 3), 1 / 3) if barycentric is None else np.asarray(barycentric, dtype=float)
        if (weights.shape != (len(ids), 3) or not np.isfinite(weights).all()
                or np.any(weights < 0) or not np.allclose(weights.sum(axis=1), 1)):
            raise ValueError('Invalid material barycentric coordinates')
        for part, destinations, indices, faces in self._material_plan:
            points[destinations] = np.einsum('nij,ni->nj',
                self._part_vertices(part, time_s, indices)[faces], weights[destinations])
        return simulation.MaterialSample(float(time_s), self.source_id, self.geometry_id, points,
                                         self.mount_world_at(time_s) @ self.mount_from_sensor)

    def visibility(self, sample, targets):
        from mathutils.bvhtree import BVHTree
        key = sample.geometry_id, sample.time_s
        if self._visibility_cache.get('key') != key:
            self._visibility_cache.update(key=key, tree=BVHTree.FromPolygons(
                sample.vertices.tolist(), sample.triangles.tolist(), all_triangles=True))
        return bvh_visibility(sample, targets, tree=self._visibility_cache['tree'])


    def analysis_context(self, frame, identity):
        """Extract only tower geometry and allowed transforms from the central sample."""
        from ..turbine_geometry import tower_side_polygon_ids
        sample = self.latest_sample
        if (sample is None or sample.time_s != frame.time.source_time_s
                or sample.source_id != frame.provenance.get('source_id')
                or sample.geometry_id != frame.provenance.get('geometry_id')
                or not np.array_equal(sample.world_from_sensor, frame.spatial['world_from_sensor'])):
            raise ValueError('No matching central geometry snapshot')
        objects = [row[0] for row in self.towers] if self.towers else [self.tower]
        ids = []
        for part, obj in enumerate(objects, start=len(self.blades)):
            sides = tower_side_polygon_ids(obj.data)
            obj.data.calc_loop_triangles()
            ids.extend(self.triangle_offsets[part] + i
                       for i, tri in enumerate(obj.data.loop_triangles) if tri.polygon_index in sides)
        faces = sample.triangles[ids]
        used, inverse = np.unique(faces, return_inverse=True)
        return analysis.Context(identity, self.mount_world_at(sample.time_s, mount='NACELLE'),
            sample.world_from_sensor, sample.vertices[used], inverse.reshape(-1,3))

    def blade_priors(self, time_s):
        """Only nominal span and same-source constrained root poses enter inference."""
        from ..deflection import interpolated_beamdyn_root_frame
        from ..turbine_geometry import geometry_data
        from .core import tracking
        self.validate()
        if not self.interval[0] <= time_s <= self.interval[1]:
            raise ValueError('Blade prior time is outside the bound source')
        flex, scalars = self.flex, geometry_data()['scalars']
        i = int(np.clip(np.searchsorted(flex.times, time_s, side='right') - 1, 0, len(flex.times) - 2))
        alpha = (time_s - flex.times[i]) / (flex.times[i+1] - flex.times[i])
        priors = []
        for blade in range(3):
            hub, axes = interpolated_beamdyn_root_frame(scalars, blade+1,
                flex.transforms[i, self.k, blade, 0], flex.transforms[i+1, self.k, blade, 0], alpha)
            matrix = np.eye(4)
            matrix[:3, :3] = axes
            matrix[:3, 3] = hub + self.root_world[:3, 3]
            priors.append(tracking.BladePrior(blade+1, matrix, float(scalars['TipRad'])))
        return priors


def _closest_on_face(p, a, b, c):
    """Float64 Voronoi-region refinement of the BVH-selected triangle."""
    ab, ac, ap = b-a, c-a, p-a
    d1, d2 = np.dot(ab,ap), np.dot(ac,ap)
    if d1 <= 0 and d2 <= 0:
        return a
    bp = p-b
    d3, d4 = np.dot(ab,bp), np.dot(ac,bp)
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1*d4-d3*d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return a + (d1/(d1-d3))*ab
    cp = p-c
    d5, d6 = np.dot(ab,cp), np.dot(ac,cp)
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5*d2-d1*d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        return a + (d2/(d2-d6))*ac
    va = d3*d6-d5*d4
    if va <= 0 and d4-d3 >= 0 and d5-d6 >= 0:
        return b + ((d4-d3)/((d4-d3)+(d5-d6)))*(c-b)
    inverse = 1/(va+vb+vc)
    return a + ab*(vb*inverse) + ac*(vc*inverse)


class SideWallQuery:
    """Dedicated anchored BVH; never constructed or queried by draw callbacks."""
    def __init__(self, context):
        from mathutils.bvhtree import BVHTree
        self.context = context
        self.anchor = context.vertices.mean(axis=0)
        self.tree = BVHTree.FromPolygons((context.vertices-self.anchor).tolist(),
                                       context.triangles.tolist(), all_triangles=True)
        self._frame_cache = None

    def query(self, sensor_points, roi, *, frame_key=None):
        from mathutils import Vector
        context = self.context
        world = analysis.transform(sensor_points, context.world_from_sensor)
        points = analysis.local(world, context.world_from_analysis)
        n = len(points)
        finite = np.isfinite(points).all(axis=1)
        lo, hi = np.asarray(roi[0]), np.asarray(roi[1])
        if not np.isfinite([lo,hi]).all() or np.any(lo>=hi):
            raise ValueError('Invalid analysis ROI bounds')
        selected = finite & (points>=lo).all(axis=1) & (points<=hi).all(axis=1)
        distance = np.full(n,np.nan)
        nearest = np.full((n,3),np.nan)
        triangle = np.full(n,-1,dtype=np.int32)
        status = np.full(n,'OUTSIDE_ROI',dtype='U16')
        status[~finite]='NONFINITE'
        cache = None
        if frame_key is not None:
            key = context.identity, frame_key
            if (self._frame_cache is None or self._frame_cache[0] != key
                    or not np.array_equal(self._frame_cache[1], sensor_points, equal_nan=True)):
                self._frame_cache = (key, analysis.readonly(sensor_points), np.zeros(n, dtype=bool),
                                     distance.copy(), nearest.copy(), triangle.copy())
            cache = self._frame_cache
            reused = selected & cache[2]
            distance[reused], nearest[reused], triangle[reused] = (
                cache[3][reused], cache[4][reused], cache[5][reused])
            status[reused] = 'VALID'
        missing = selected if cache is None else selected & ~cache[2]
        for i in np.flatnonzero(missing):
            q, _, face, _ = self.tree.find_nearest(Vector(world[i]-self.anchor))
            if q is None:
                status[i]='QUERY_FAILED'
                continue
            vertices = context.vertices[context.triangles[face]]-self.anchor
            anchored = _closest_on_face(world[i]-self.anchor,*vertices)
            distance[i]=np.linalg.norm((world[i]-self.anchor)-anchored)
            nearest[i]=anchored+self.anchor
            triangle[i]=face
            status[i]='VALID'
        if cache is not None:
            valid = missing & (status == 'VALID')
            cache[2][valid] = True
            cache[3][valid], cache[4][valid], cache[5][valid] = distance[valid], nearest[valid], triangle[valid]
        return dict(indices=analysis.readonly(np.arange(n),np.int64),
            points=analysis.readonly(points), distance=analysis.readonly(distance),
            nearest=analysis.readonly(nearest), triangle=analysis.readonly(triangle,np.int32),
            status=analysis.readonly(status,'U16'), selected=int(selected.sum()),
            finite=int(finite.sum()), valid=int(np.isfinite(distance).sum()),
            quantiles_m=tuple(float(x) for x in np.quantile(distance[np.isfinite(distance)],[0,.1,.5,.9,1])) if np.isfinite(distance).any() else None)


def bvh_visibility(sample, targets, *, tree=None):
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    if tree is None:
        tree = BVHTree.FromPolygons(sample.vertices.tolist(), sample.triangles.tolist(), all_triangles=True)
    origin = Vector(sample.world_from_sensor[:3, 3])
    result = []
    for point in targets:
        delta = Vector(point) - origin
        location, normal, index, distance = tree.ray_cast(origin, delta.normalized(), max(0., delta.length - .002))
        result.append(location is None)
    return np.array(result, dtype=bool)
