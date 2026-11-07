"""Owned rigid display sampling and immutable manual assembly mesh reuse."""
from array import array
import hashlib
import re

from .turbine_geometry import MANUAL_DISPLAY_SAMPLING, MODELS, ROOT_CANT_DEG, blade_mesh

DETAIL_ITEMS = (
    ('OVERVIEW', '总览', '固定 4×48 采样，适合整机和远景；所有视口共用同一网格'),
    ('BALANCED', '标准', '固定 6×64 采样，适合中景'),
    ('CLOSEUP', '近景', '固定 8×96 采样，适合近景与静帧渲染；Down 和视频导出使用此精度'))
_DETAIL_UPDATING = False
_FITTINGS = ('RootFairing', 'RootFlange', 'PitchSeal')


def _bind_mesh(obj, mesh):
    obj.data = mesh


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


def _stamp(mesh, owner):
    mesh['wfrl_manual_mesh_owner'] = owner
    mesh['wfrl_manual_owner'] = owner
    mesh['wfrl_manual_generated_digest'] = mesh_fingerprint(mesh) or ''


def tag_generated_assembly(scene):
    """Identify fresh constructor outputs; saved or user meshes are never adopted here."""
    owner = scene['wfrl_manual_owner']
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        role = obj.name.split('.Turbine.', 1)[-1].split('.', 1)[-1]
        _stamp(obj.data, owner)
        if role == 'Tower' or re.fullmatch(r'Blade[123]', role):
            continue
        if '.Turbine.' not in obj.name:
            continue
        obj['wfrl_manual_static_role'] = re.sub(r'^Blade[123]\.', 'Blade.', role)
        obj.data['wfrl_manual_static_name'] = obj.data.name


def share_static_meshes(scene):
    """Reuse unchanged generated fittings only inside one independent manual scene.

    Object transforms and modifiers stay independent. Tower and blade meshes,
    edited/renamed data, extra users, linked data, shape keys and fake users are
    excluded. Bindings commit together, and old data is retired only afterwards.
    """
    import bpy
    from . import manual_control
    valid, reason = manual_control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    owner = scene['wfrl_manual_owner']
    consumers = {}
    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            consumers.setdefault(obj.data.as_pointer(), []).append(obj)
    groups, bindings = {}, []
    for obj in scene.objects:
        role = obj.get('wfrl_manual_static_role')
        if (obj.type != 'MESH' or not role or obj.get('wfrl_manual_owner') != owner
                or obj.vertex_groups or any(slot.link != 'DATA' for slot in obj.material_slots)):
            continue
        mesh = obj.data
        users = consumers[mesh.as_pointer()]
        if (mesh.library or mesh.use_fake_user or mesh.shape_keys or mesh.animation_data
                or mesh.users != len(users) or mesh.get('wfrl_manual_mesh_owner') != owner
                or mesh.name != mesh.get('wfrl_manual_static_name')
                or any(set(user.users_scene) != {scene}
                       or user.get('wfrl_manual_static_role') != role
                       or user.get('wfrl_manual_owner') != owner for user in users)):
            continue
        fingerprint = mesh_fingerprint(mesh)
        if not fingerprint or fingerprint != mesh.get('wfrl_manual_generated_digest'):
            continue
        key = (role, fingerprint, tuple(mat.as_pointer() if mat else 0 for mat in mesh.materials))
        canonical = groups.setdefault(key, mesh)
        if mesh != canonical:
            bindings.append((obj, mesh, canonical))
    try:
        for obj, _old, canonical in bindings:
            _bind_mesh(obj, canonical)
    except BaseException:
        for obj, old, _canonical in bindings:
            obj.data = old
        raise
    retired = {old.as_pointer(): old for _obj, old, _new in bindings}
    for mesh in retired.values():
        if mesh.users == 0 and not mesh.use_fake_user and mesh.shape_keys is None:
            bpy.data.meshes.remove(mesh)
    return len(retired)


def _new_blade(model, detail, source):
    import bpy
    from .materials import BLADE_TIP_BANDS
    vertices, faces = blade_mesh(*MANUAL_DISPLAY_SAMPLING[detail], model=model)
    mesh = bpy.data.meshes.new('Manual.' + model + '.' + detail + '.Blade')
    try:
        mesh.from_pydata(vertices, [], faces)
        for material in source.materials:
            mesh.materials.append(material)
        if len(mesh.materials) != 2:
            raise ValueError('手动叶片材质槽已自定义，请使用独立的生成模型调整显示精度')
        tip = max(point[2] for point in vertices)
        for face in mesh.polygons:
            face.use_smooth = len(face.vertices) <= 4
            distance = tip - sum(vertices[i][2] for i in face.vertices)/len(face.vertices)
            face.material_index = int(any(lo <= distance <= hi for lo, hi in BLADE_TIP_BANDS))
        mesh['wfrl_tip_marking_revision'] = 1
        mesh['wfrl_tip_marking_note'] = 'Three red bands on the display mesh; white cap; cosmetic only'
        mesh.update()
        return mesh
    except BaseException:
        bpy.data.meshes.remove(mesh)
        raise


def _check_generated(mesh, owner, *, legacy_factory):
    import bpy
    if mesh.library or mesh.shape_keys or mesh.use_fake_user or mesh.animation_data:
        raise ValueError('叶片或叶根含链接、形态键、保留用户或动画；请使用独立生成模型')
    digest = mesh_fingerprint(mesh)
    known = mesh.get('wfrl_manual_generated_digest')
    if known:
        if (digest != known or mesh.get('wfrl_manual_mesh_owner') != owner):
            raise ValueError('手动生成网格已编辑或归属不符，保留当前网格；请使用独立生成模型')
    else:
        reference = legacy_factory()
        try:
            if not digest or digest != mesh_fingerprint(reference):
                raise ValueError('手动网格与可验证的生成模型不匹配，保留当前网格')
        finally:
            bpy.data.meshes.remove(reference)


def ensure_model(scene, *, detail=None):
    """Commit blade sampling and model-specific root fittings as one binding transaction."""
    import bpy
    from . import manual_control
    from .mechanical_details import blade_root_fitting_mesh
    model = getattr(scene, 'wfrl_manual_blade_shape', scene.get('wfrl_manual_blade_model', 'rootkink'))
    detail = detail or scene.wfrl_manual_display_detail
    if model not in MODELS or detail not in MANUAL_DISPLAY_SAMPLING:
        raise ValueError('无法识别手动叶片外形或显示精度')
    previous_model = scene.get('wfrl_manual_blade_model', 'nrel5mw')
    previous_detail = scene.get('wfrl_manual_display_applied', 'CLOSEUP')
    if manual_control.trial_active(scene) and previous_model != model:
        raise ValueError('请先结束 T1 冻结试验，再切换叶片外形')
    valid, reason = manual_control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    owner = scene['wfrl_manual_owner']
    sampling = MANUAL_DISPLAY_SAMPLING[detail]
    signature = f'{model}:cant:{ROOT_CANT_DEG.get(model, 0.0):g}:sampling:{sampling[0]}x{sampling[1]}'
    blades = [getattr(entry, f'blade{i}_object') for entry in scene.wfrl_manual_turbines for i in (1, 2, 3)]
    plans, replacements, bindings, fitting_pool = [], {}, [], {}
    for blade in blades:
        if blade.data.get('wfrl_manual_geometry') != signature:
            plans.append((blade, 'Blade', signature))
        for role in _FITTINGS:
            candidates = [obj for obj in blade.parent.children if obj.type == 'MESH' and (
                obj.get('wfrl_manual_static_role') == 'Blade.' + role
                or re.search(r'\.' + role + r'(?:\.\d+)*$', obj.name))]
            if len(candidates) != 1:
                raise ValueError('手动叶根装配缺失或重复：' + role)
            obj = candidates[0]
            if obj.get('wfrl_manual_owner') != owner or set(obj.users_scene) != {scene}:
                raise ValueError('手动叶根装配不属于此独立场景')
            target = model + ':root-neck.v1:' + role
            if obj.data.get('wfrl_manual_fitting') != target:
                plans.append((obj, role, target))
    try:
        for obj, role, target in plans:
            source = obj.data
            key = (source.as_pointer(), role)
            if key in replacements:
                continue
            factory = (lambda: _new_blade(previous_model, previous_detail, source)) if role == 'Blade' else (
                lambda: blade_root_fitting_mesh(role, previous_model if source.get('wfrl_manual_fitting')
                                                else 'nrel5mw'))
            _check_generated(source, owner, legacy_factory=factory)
            pool_key = (role, tuple(mat.as_pointer() if mat else 0 for mat in source.materials))
            if role != 'Blade' and pool_key in fitting_pool:
                replacements[key] = fitting_pool[pool_key]
                continue
            mesh = (_new_blade(model, detail, source) if role == 'Blade'
                    else blade_root_fitting_mesh(role, model))
            replacements[key] = mesh
            if role != 'Blade':
                for material in source.materials:
                    mesh.materials.append(material)
                mesh['wfrl_manual_fitting'] = target
            else:
                mesh['wfrl_manual_geometry'] = target
                mesh['wfrl_manual_blade_model'] = model
            mesh.name = owner + '.' + model + '.' + detail + '.' + role
            _stamp(mesh, owner)
            if role != 'Blade':
                mesh['wfrl_manual_static_name'] = mesh.name
                fitting_pool[pool_key] = mesh
        for obj, role, _target in plans:
            bindings.append((obj, obj.data, obj.get('geometry_source')))
            _bind_mesh(obj, replacements[(obj.data.as_pointer(), role)])
            if role != 'Blade':
                obj['geometry_source'] = ('Synthetic rootkink hub neck; presentation only' if model == 'rootkink'
                                          else 'NREL 5MW blade-root fitting; presentation only')
        scene['wfrl_manual_blade_model'] = model
        scene['wfrl_manual_display_applied'] = detail
    except BaseException:
        for obj, mesh, provenance in bindings:
            obj.data = mesh
            if provenance is not None:
                obj['geometry_source'] = provenance
            else:
                obj.pop('geometry_source', None)
        for mesh in {item.as_pointer(): item for item in replacements.values()}.values():
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise
    sources = {mesh.as_pointer(): mesh for _obj, mesh, _provenance in bindings}
    for mesh in sources.values():
        if (mesh.users == 0 and not mesh.use_fake_user and mesh.shape_keys is None
                and mesh.get('wfrl_manual_mesh_owner') == owner):
            bpy.data.meshes.remove(mesh)
    from . import manual_down
    manual_down._STATIONS.clear()
    return detail


def set_detail(scene, detail):
    """Apply a fixed detail selection without touching poses, clocks or camera optics."""
    global _DETAIL_UPDATING
    ensure_model(scene, detail=detail)
    _DETAIL_UPDATING = True
    try:
        scene.wfrl_manual_display_detail = detail
    finally:
        _DETAIL_UPDATING = False
    scene.pop('wfrl_manual_geometry_error', None)


def detail_update(scene, _context):
    global _DETAIL_UPDATING
    if _DETAIL_UPDATING or scene.get('wfrl_scene_kind') != 'manual' or not scene.wfrl_manual_turbines:
        return
    previous = scene.get('wfrl_manual_display_applied', 'CLOSEUP')
    try:
        ensure_model(scene)
        scene.pop('wfrl_manual_geometry_error', None)
    except (ValueError, RuntimeError, ReferenceError, AttributeError) as exc:
        _DETAIL_UPDATING = True
        try:
            scene.wfrl_manual_display_detail = previous
        finally:
            _DETAIL_UPDATING = False
        scene['wfrl_manual_geometry_error'] = str(exc)
        print('WFRL manual display:', exc)
