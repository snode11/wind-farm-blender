"""Transactional presentation fittings for recorded NREL and rootkink models."""
from array import array
import hashlib

from .turbine_geometry import MODELS


def mesh_fingerprint(mesh):
    """Include geometry, UVs, smoothing and face paint, independently of material IDs."""
    digest = hashlib.sha256()
    if mesh.has_custom_normals or any(vertex.groups for vertex in mesh.vertices):
        return None
    digest.update(repr((mesh.use_auto_texspace, tuple(mesh.texspace_location), tuple(mesh.texspace_size))).encode())

    def values(items, prop, count, kind):
        buffer = array(kind, [0]) * (len(items)*count)
        items.foreach_get(prop, buffer)
        digest.update(buffer.tobytes())

    values(mesh.vertices, 'co', 3, 'f')
    values(mesh.edges, 'vertices', 2, 'i')
    values(mesh.loops, 'vertex_index', 1, 'i')
    for prop in ('loop_start', 'loop_total', 'material_index'):
        values(mesh.polygons, prop, 1, 'i')
    values(mesh.polygons, 'use_smooth', 1, 'b')
    properties = {'FLOAT': ('value', 1, 'f'), 'INT': ('value', 1, 'i'),
                  'INT8': ('value', 1, 'i'), 'BOOLEAN': ('value', 1, 'b'),
                  'FLOAT_VECTOR': ('vector', 3, 'f'), 'FLOAT2': ('vector', 2, 'f'),
                  'FLOAT_COLOR': ('color', 4, 'f'), 'BYTE_COLOR': ('color', 4, 'f')}
    for attribute in sorted(mesh.attributes, key=lambda item: item.name):
        if attribute.name in {'position', '.edge_verts', '.corner_vert', '.corner_edge'}:
            continue
        layout = properties.get(attribute.data_type)
        if layout is None:
            return None
        digest.update(repr((attribute.name, attribute.data_type, attribute.domain)).encode())
        values(attribute.data, *layout)
    return digest.hexdigest()


class RootFittingBindings:
    """Prepare and bind owned fittings without changing recorded blade surfaces."""

    def __init__(self, scene, model):
        import bpy
        from .mechanical_details import blade_root_fitting_mesh

        if model not in MODELS:
            raise ValueError('无法识别叶根展示外形')
        self.created = []
        self.bindings = []
        self.applied = []
        pool = {}
        try:
            for obj in scene.objects:
                role = obj.name.rsplit('.', 1)[-1]
                if (not obj.name.startswith('WFRL.Turbine.')
                        or role not in ('RootFairing', 'RootFlange', 'PitchSeal')):
                    continue
                if (obj.type != 'MESH' or obj.library or set(obj.users_scene) != {scene}
                        or obj.parent is None or not obj.parent.name.endswith('.PitchRoot')):
                    raise ValueError('叶根展示零件的装配或场景归属不符：' + obj.name)
                source = obj.data
                previous = source.get('wfrl_root_fitting_model', 'nrel5mw')
                if previous == model:
                    continue
                if (previous not in MODELS or source.library or source.use_fake_user
                        or source.shape_keys or source.animation_data):
                    raise ValueError('叶根展示网格含保留用户或自定义数据：' + obj.name)
                digest = mesh_fingerprint(source)
                expected = source.get('wfrl_root_fitting_digest')
                if not expected:
                    reference = blade_root_fitting_mesh(role, previous)
                    try:
                        expected = mesh_fingerprint(reference)
                    finally:
                        bpy.data.meshes.remove(reference)
                if not digest or digest != expected:
                    raise ValueError('叶根展示网格已编辑，保留当前装配：' + obj.name)
                key = (source.as_pointer(), role)
                mesh = pool.get(key)
                if mesh is None:
                    mesh = blade_root_fitting_mesh(role, model)
                    self.created.append(mesh)
                    mesh.name = f'WFRL.RootFitting.{model}.{role}'
                    for material in source.materials:
                        mesh.materials.append(material)
                    mesh['wfrl_root_fitting_owned'] = True
                    mesh['wfrl_root_fitting_model'] = model
                    mesh['wfrl_root_fitting_digest'] = mesh_fingerprint(mesh)
                    pool[key] = mesh
                self.bindings.append((obj, source, mesh, obj.get('geometry_source')))
        except BaseException:
            self.rollback()
            raise

    def apply(self):
        try:
            for obj, source, mesh, provenance in self.bindings:
                self.applied.append((obj, source, mesh, provenance))
                obj.data = mesh
                obj['geometry_source'] = ('Synthetic rootkink hub neck; presentation only'
                                          if mesh['wfrl_root_fitting_model'] == 'rootkink'
                                          else 'NREL 5MW blade-root fitting; presentation only')
        except BaseException:
            self.rollback()
            raise

    @staticmethod
    def _release(mesh, *, verified=False):
        import bpy
        try:
            if ((verified or mesh.get('wfrl_root_fitting_owned')) and not mesh.users and not mesh.library
                    and not mesh.use_fake_user and mesh.shape_keys is None):
                bpy.data.meshes.remove(mesh)
        except ReferenceError:
            pass

    def rollback(self):
        for obj, source, mesh, provenance in reversed(self.applied):
            try:
                if obj.data == mesh:
                    obj.data = source
                    if provenance is None:
                        obj.pop('geometry_source', None)
                    else:
                        obj['geometry_source'] = provenance
            except ReferenceError:
                continue
        self.applied.clear()
        for mesh in self.created:
            self._release(mesh, verified=True)
        self.created.clear()
        self.bindings.clear()

    def commit(self):
        for _obj, source, _mesh, _provenance in self.applied:
            self._release(source, verified=True)
        self.applied.clear()
        self.created.clear()
        self.bindings.clear()


def refresh(scene, model):
    bindings = RootFittingBindings(scene, model)
    bindings.apply()
    bindings.commit()
