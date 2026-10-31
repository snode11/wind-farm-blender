"""Read-only T1 surface and mount sampling from one saved FarmFlex source."""
import hashlib
import json
import math
import numpy as np
from .core import simulation, schema


def installation_matrix(location, rotation_deg):
    from mathutils import Euler
    matrix = np.eye(4)
    matrix[:3, :3] = np.array(Euler(tuple(math.radians(x) for x in rotation_deg), 'XYZ').to_matrix())
    matrix[:3, 3] = location
    return matrix


class SceneSampler:
    """Snapshot static bindings; arbitrary source times never change Blender clocks."""
    def __init__(self, flex, *, turbine_id='T1', mount='NACELLE', mount_from_sensor=None):
        from ..turbine_geometry import geometry_data
        self.flex, self.turbine_id, self.mount = flex, turbine_id, mount
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
        shell = flex.scene.objects[f'WFRL.Turbine.{turbine_id}.Nacelle']
        if shell.constraints or shell.animation_data or shell.parent != yaw:
            raise ValueError('X100 requires a static nacelle shell local to the recorded yaw root')
        self.shell_local = np.array(shell.matrix_parent_inverse, dtype=np.float64) @ np.array(shell.matrix_basis, dtype=np.float64)
        self.shell_vertices = np.array([v.co[:] for v in shell.data.vertices])
        self.parts = []
        for obj, *_ in self.blades:
            self.parts.append((obj.name, self._triangles(obj.data)))
        if self.towers:
            for obj, *_ in self.towers:
                self.parts.append((obj.name, self._triangles(obj.data)))
        else:
            tower = flex.scene.objects[f'WFRL.Turbine.{turbine_id}.Tower']
            self.rigid_tower = (np.array([v.co[:] for v in tower.data.vertices]), np.array(tower.matrix_world))
            self.parts.append((tower.name, self._triangles(tower.data)))
        self.parts.append((shell.name, self._triangles(shell.data)))
        rest_parts = [row[1].tobytes() for row in self.blades] + [
            self.shell_vertices.tobytes(), self.shell_local.tobytes(), self.root_world.tobytes()]
        rest_parts.extend(row[1].tobytes() for row in self.towers)
        if not self.towers:
            rest_parts.extend(array.tobytes() for array in self.rigid_tower)
        self.geometry_id = schema.digest(dict(source_id=self.source_id, model=flex.model,
            topology=[(name, triangles.tolist()) for name, triangles in self.parts],
            rest_sha256=hashlib.sha256(b''.join(rest_parts)).hexdigest()))
        self.calibration_id = schema.digest(dict(mount=mount, matrix=self.mount_from_sensor.tolist(),
                                                 origin='approximate housing center; phase center unknown'))

    @staticmethod
    def _triangles(mesh):
        mesh.calc_loop_triangles()
        return np.array([tri.vertices[:] for tri in mesh.loop_triangles], dtype=np.int32)

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

    def validate(self):
        flex = self.flex
        if not flex.enabled or flex.scene.objects.get(self.blades[0][0].name) != self.blades[0][0]:
            raise ValueError('X100 scene source was released')
        if flex.model != self.model or tuple((row[0].as_pointer(), id(row[1]))
                for row in flex.blades if row[5] == self.k) != self.rest_bindings:
            raise ValueError('X100 material bindings changed; apply the installation again')
        if (self.root.parent is not None or self.root.constraints or self.root.animation_data
                or not np.array_equal(np.array(self.root.matrix_basis), self.root_basis)):
            raise ValueError('X100 source layout changed; restore the recorded layout and apply again')

    def sample(self, time_s):
        self.validate()
        flex = self.flex
        vertices = [flex.deform_points(row[0], row[1], time_s, world=True, high_precision=True)
                    for row in self.blades]
        if self.towers:
            i = int(np.clip(np.searchsorted(flex.times, time_s, side='right') - 1, 0, len(flex.times) - 2))
            alpha = float((time_s - flex.times[i]) / (flex.times[i + 1] - flex.times[i]))
            transforms = flex.tower_motion['transforms'][i] * (1 - alpha) + flex.tower_motion['transforms'][i + 1] * alpha
            for obj, rest, index, weight, k in self.towers:
                low, high = transforms[k, index], transforms[k, index + 1]
                a = np.einsum('nij,nj->ni', low[:, :, :3], rest) + low[:, :, 3]
                b = np.einsum('nij,nj->ni', high[:, :, :3], rest) + high[:, :, 3]
                xyz = a * (1 - weight) + b * weight
                vertices.append(xyz @ self.root_world[:3, :3].T + self.root_world[:3, 3])
        else:
            rest, matrix = self.rigid_tower
            vertices.append(rest @ matrix[:3, :3].T + matrix[:3, 3])
        # The physical shell follows the same saved nacelle pose even for world-fixed radar mounts.
        yaw = self.mount_world_at(time_s, mount='NACELLE')
        shell_matrix = yaw @ self.shell_local
        vertices.append(self.shell_vertices @ shell_matrix[:3, :3].T + shell_matrix[:3, 3])
        triangles, offset = [], 0
        for xyz, (_, faces) in zip(vertices, self.parts):
            triangles.append(faces + offset)
            offset += len(xyz)
        return simulation.SceneSample(float(time_s), self.source_id, self.geometry_id,
            np.concatenate(vertices), np.concatenate(triangles), self.mount_world_at(time_s) @ self.mount_from_sensor,
            'T1 saved blade presentation surfaces, flexible tower and nacelle shell; other structures/environment excluded')


def bvh_visibility(sample, targets):
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    tree = BVHTree.FromPolygons(sample.vertices.tolist(), sample.triangles.tolist(), all_triangles=True)
    origin = Vector(sample.world_from_sensor[:3, 3])
    result = []
    for point in targets:
        delta = Vector(point) - origin
        location, normal, index, distance = tree.ray_cast(origin, delta.normalized(), max(0., delta.length - .002))
        result.append(location is None)
    return np.array(result, dtype=bool)
