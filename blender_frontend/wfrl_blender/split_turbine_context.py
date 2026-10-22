"""Share T1's simulated support geometry with the two reconstruction views.

Only the three blade meshes are reconstruction estimates.  Everything created
here is an explicitly labelled display copy of the current left-hand turbine.
The copies share mesh data but never share transforms or visibility ownership.
"""
from __future__ import annotations

import math


SOURCE_PREFIX = 'WFRL.Turbine.T1.'
CLASSIFICATION = 'SIMULATED_MODEL_CONTEXT'
_CORE = ('Tower', 'Nacelle', 'Hub', 'Spinner')
_LEGACY = ('Tower', 'Nacelle', 'Hub')
_SAVED_GROUPS = (
    ('split_reconstruction_structure_names', 'SplitRecon.Reference.', (0., 10000., 0.)),
    ('split_mappo_texture_structure_names', 'SplitMappoTexture.Reference.', (10000., 0., 0.)),
)
_SESSIONS = {}


def clear_runtime(scene=None):
    """Discard cached references before a file load or one scene's rebuild.

    Saved display objects and their provenance remain available for ensure()
    to bind to the replacement source rig.  Other scenes keep their clocks.
    """
    if scene is None:
        _SESSIONS.clear()
    else:
        _SESSIONS.pop(scene.as_pointer(), None)


def release(scene, names_key):
    """Forget one group's runtime when its caller rolls back its objects.

    Object deletion and saved metadata cleanup belong to that transaction;
    releasing its cache must not invalidate the other reconstruction mode.
    """
    key = scene.as_pointer()
    groups = _SESSIONS.get(key)
    if groups is not None:
        groups.pop(names_key, None)
        if not groups:
            _SESSIONS.pop(key, None)


def _is_source(obj):
    if obj.type != 'MESH' or not obj.name.startswith(SOURCE_PREFIX):
        return False
    part = obj.name[len(SOURCE_PREFIX):]
    # A blade attachment is part of the blade, even if it looks mechanical.
    # Sensor fixtures and scientific overlays are not turbine shell context.
    return bool(part) and not part.startswith((
        'Blade', 'Clearance', 'Camera', 'Sensor', 'Wake', 'Annotation',
        'Label', 'Beam', 'Ray', 'Frustum', 'TipTrail', 'Trajectory',
        'Deflection', 'Reference',
    ))


def _sources(scene):
    sources = {obj.name[len(SOURCE_PREFIX):]: obj for obj in scene.objects if _is_source(obj)}
    missing = [part for part in _CORE if part not in sources]
    yaw = scene.objects.get(SOURCE_PREFIX + 'YawRoot')
    if yaw is None:
        missing.append('YawRoot')
    if missing:
        raise ValueError('左侧 T1 精细模拟结构缺失：' + '、'.join(missing))
    return sources, yaw


def _world_from_basis(obj, cache):
    """Read freshly assigned rig channels without re-entering the depsgraph.

    These mechanical parts use ordinary object parenting.  matrix_world can
    still contain the preceding frame during a frame handler, whereas basis
    and the parent inverse are available immediately after channel writes.
    """
    key = obj.as_pointer()
    if key not in cache:
        if obj.parent is None:
            cache[key] = obj.matrix_basis.copy()
        else:
            if obj.parent_type != 'OBJECT':
                raise ValueError('模拟结构不支持非对象父级：' + obj.name)
            cache[key] = (_world_from_basis(obj.parent, cache) @
                          obj.matrix_parent_inverse @ obj.matrix_basis)
    return cache[key]


def _model_inverse(yaw, matrices):
    from mathutils import Matrix
    from .turbine_geometry import geometry_data
    flip = Matrix.Diagonal((-1., -1., 1., 1.))
    flip.translation = (0., 0., -float(geometry_data()['scalars']['TowerHt']))
    return (_world_from_basis(yaw, matrices) @ flip).inverted()


def _legacy_valid(obj, part, prefix):
    if obj.type != 'MESH' or obj.name != prefix + part:
        return False
    if any(key.startswith('split_turbine_context_') for key in obj.keys()):
        return False
    if prefix == 'SplitRecon.Reference.':
        return (obj.get('blade_recon_owned') is True and
                isinstance(obj.get('split_display_offset_y'), (int, float)))
    if prefix == 'SplitMappoTexture.Reference.':
        return (obj.get('split_mappo_texture_structure') == CLASSIFICATION and
                obj.get('split_mappo_texture_structure_component') == part and
                obj.get('split_mappo_texture_structure_source') == 'SplitRecon.Reference.' + part and
                bool(obj.get('split_mappo_texture_owned')) and bool(obj.get('split_texture_owned')))
    return False


def _existing(scene, prefix, names_key, sources):
    saved = scene.get(names_key)
    discovered = [obj for obj in scene.objects if obj.name.startswith(prefix)]
    if saved is None:
        if not discovered:
            return {}, []
        names = [prefix + part for part in _LEGACY]
        legacy = [scene.objects.get(name) for name in names]
        if (len(discovered) == len(_LEGACY) and
                all(obj is not None and _legacy_valid(obj, part, prefix)
                    for part, obj in zip(_LEGACY, legacy))):
            return {}, legacy
        raise ValueError('模拟展示结构对象已存在但保存的来源清单缺失：' + prefix)
    if isinstance(saved, str):
        raise ValueError('保存的模拟展示结构清单格式无效：' + names_key)
    names = list(saved)
    if not names or any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
        raise ValueError('保存的模拟展示结构清单为空、重复或格式无效：' + names_key)
    objects = [scene.objects.get(name) for name in names]
    if any(obj is None for obj in objects):
        raise ValueError('保存的模拟展示结构对象缺失：' + names_key)
    if set(names) != {obj.name for obj in discovered}:
        raise ValueError('模拟展示结构对象与保存清单不一致：' + names_key)
    legacy_parts = [name[len(prefix):] if name.startswith(prefix) else '' for name in names]
    if (len(names) == len(_LEGACY) and set(legacy_parts) == set(_LEGACY) and
            all(_legacy_valid(obj, part, prefix) for part, obj in zip(legacy_parts, objects))):
        return {}, objects
    existing = {}
    for obj in objects:
        part = obj.name[len(prefix):] if obj.name.startswith(prefix) else ''
        source = sources.get(part)
        if (source is None or obj.type != 'MESH' or
                obj.get('split_turbine_context_classification') != CLASSIFICATION or
                obj.get('split_turbine_context_component') != part or
                obj.get('split_turbine_context_source') != source.name):
            raise ValueError('保存的模拟展示结构来源或分类无效：' + obj.name)
        if prefix.startswith('SplitMappoTexture.') and (
                obj.get('split_mappo_texture_structure') != CLASSIFICATION or
                obj.get('split_mappo_texture_structure_component') != part or
                obj.get('split_mappo_texture_structure_source') != source.name or
                not obj.get('split_mappo_texture_owned') or not obj.get('split_texture_owned')):
            raise ValueError('保存的同源纹理模拟结构来源或分类无效：' + obj.name)
        existing[part] = obj
    if any(part not in existing for part in _CORE):
        raise ValueError('保存的模拟展示结构清单缺少核心部件：' + names_key)
    return existing, []


def _tag(obj, source, part, prefix):
    # Object.copy copies custom properties as well.  In particular, do not
    # retain blade_recon_owned or WFRL defect/rig ownership on a display copy.
    geometry_source = source.get('geometry_source')
    for key in list(obj.keys()):
        del obj[key]
    if geometry_source is not None:
        obj['geometry_source'] = geometry_source
    else:
        obj['geometry_source'] = 'Shared current left T1 simulated mesh: ' + source.name
    obj['split_turbine_context_source'] = source.name
    obj['split_turbine_context_component'] = part
    obj['split_turbine_context_classification'] = CLASSIFICATION
    if prefix.startswith('SplitMappoTexture.'):
        obj['split_mappo_texture_owned'] = True
        obj['split_texture_owned'] = True
        obj['split_mappo_texture_structure'] = CLASSIFICATION
        obj['split_mappo_texture_structure_component'] = part
        obj['split_mappo_texture_structure_source'] = source.name


def _apply(group, matrices, inverse):
    from mathutils import Matrix
    display_from_world = Matrix.Translation(group['offset']) @ inverse
    for source, obj in group['pairs']:
        # Farm attachment replaces Tower.data on reopen.  Rebinding is cheap
        # and also makes every subsequent source mesh deformation visible.
        if obj.data != source.data:
            obj.data = source.data
        world = display_from_world @ _world_from_basis(source, matrices)
        if obj.matrix_world != world:
            obj.matrix_world = world
        # Selecting T2/T3 hides the source T1, but this separate display must
        # keep its support structure. Reset flags copied or saved by an older
        # version without changing the right pane's hide_set/local-view state.
        if obj.hide_viewport:
            obj.hide_viewport = False
        if obj.hide_render:
            obj.hide_render = False


def _binding_valid(scene, group):
    """Check only bound IDs; frame-channel and vertex writes keep the binding.

    Membership checks also reject unlinked objects which still exist in bpy.
    Saved provenance is checked here rather than trusting a Python cache after
    external edits. New/renamed source objects are invalidated by notice_updates.
    """
    if (group.get('dirty') or len(scene.objects) != group['object_count'] or
            tuple(scene.get(group['names_key'], ())) != group['names'] or
            tuple(scene.get(group['names_key'] + '_offset', ())) != group['offset'] or
            scene.objects.get(SOURCE_PREFIX + 'YawRoot') != group['yaw']):
        return False
    for source, obj, source_name, name, mesh in group['bindings']:
        part = source_name[len(SOURCE_PREFIX):]
        if (source.name != source_name or scene.objects.get(source_name) != source or
                not _is_source(source) or source.data != mesh or
                obj.name != name or scene.objects.get(name) != obj or obj.type != 'MESH' or
                obj.data != mesh or obj.parent is not None or
                obj.get('split_turbine_context_classification') != CLASSIFICATION or
                obj.get('split_turbine_context_component') != part or
                obj.get('split_turbine_context_source') != source_name):
            return False
        if group['prefix'].startswith('SplitMappoTexture.') and (
                obj.get('split_mappo_texture_structure') != CLASSIFICATION or
                obj.get('split_mappo_texture_structure_component') != part or
                obj.get('split_mappo_texture_structure_source') != source_name or
                not obj.get('split_mappo_texture_owned') or not obj.get('split_texture_owned')):
            return False
    return True


def refresh(scene, *, prefix, offset, names_key):
    """Reuse a validated binding; perform full validation after an ID change."""
    group = _SESSIONS.get(scene.as_pointer(), {}).get(names_key)
    try:
        valid = (group is not None and group['prefix'] == prefix and
                 group['offset'] == tuple(offset) and _binding_valid(scene, group))
    except (ReferenceError, TypeError):
        valid = False
    if not valid:
        return ensure(scene, prefix=prefix, offset=offset, names_key=names_key)
    # Global visibility is independent of T1 selection. Do not touch hide_set,
    # local view, or matrices here: FarmFlex owns current-frame synchronization.
    for _, obj in group['pairs']:
        if obj.hide_viewport:
            obj.hide_viewport = False
        if obj.hide_render:
            obj.hide_render = False
    return [obj for _, obj in group['pairs']]


def notice_updates(scene, depsgraph):
    """Notice source/display additions even when the total ID count is equal.

    Ordinary FarmFlex transforms and shared mesh deformations do not force a
    rebuild. Cached membership and mesh checks handle changes to known IDs.
    """
    groups = _SESSIONS.get(scene.as_pointer())
    if not groups:
        return
    try:
        for update in depsgraph.updates:
            obj = update.id
            if not hasattr(obj, 'type'):
                continue
            original = getattr(obj, 'original', obj)
            name = original.name
            for group in groups.values():
                if (name.startswith(SOURCE_PREFIX) or name.startswith(group['prefix'])) and (
                        group['known'].get(name) != original and
                        (_is_source(original) or name.startswith(group['prefix']))):
                    group['dirty'] = True
    except ReferenceError:
        # Deletion/undo may invalidate an update's RNA before this handler.
        # Defer the full saved provenance checks to refresh; never use it.
        for group in groups.values():
            group['dirty'] = True


def ensure(scene, *, prefix, offset, names_key):
    """Validate/migrate a saved group, then share the current left T1 meshes."""
    import bpy
    offset = tuple(float(value) for value in offset)
    if len(offset) != 3 or not all(math.isfinite(value) for value in offset):
        raise ValueError('模拟展示结构位移必须为三个有限数值')
    sources, yaw = _sources(scene)
    existing, legacy = _existing(scene, prefix, names_key, sources)
    matrices = {}
    inverse = _model_inverse(yaw, matrices)
    # Resolve the full transform chain before mutating or deleting objects.
    for source in sources.values():
        _world_from_basis(source, matrices)
    created = []
    legacy_names = {obj.name for obj in legacy}
    try:
        for part, source in sorted(sources.items()):
            if part in existing:
                continue
            name = prefix + part
            collision = bpy.data.objects.get(name)
            if collision is not None and name not in legacy_names:
                raise ValueError('模拟展示结构名称已被其他对象占用：' + name)
            obj = source.copy()
            created.append(obj)
            obj.name = name + '.Preparing'
            obj.parent = None
            obj.animation_data_clear()
            obj.constraints.clear()
            obj.hide_select = True
            _tag(obj, source, part, prefix)
            scene.collection.objects.link(obj)
            previous = next((old for old in legacy if old.name == name), None)
            obj.hide_set(previous.hide_get() if previous is not None else False)
            existing[part] = obj
        parts = list(_CORE) + sorted(set(sources) - set(_CORE))
        group = dict(scene=scene, prefix=prefix, names_key=names_key, offset=offset, yaw=yaw,
                     pairs=[(sources[part], existing[part]) for part in parts])
        _apply(group, matrices, inverse)
    except Exception:
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        raise
    # No fallible source lookup follows the removal of the validated legacy
    # placeholders.  Other split objects, especially blades, are untouched.
    for obj in legacy:
        bpy.data.objects.remove(obj, do_unlink=True)
    for source, obj in group['pairs']:
        obj.name = prefix + source.name[len(SOURCE_PREFIX):]
    result = [obj for _, obj in group['pairs']]
    scene[names_key] = [obj.name for obj in result]
    scene[names_key + '_offset'] = offset
    scene[names_key + '_notice'] = (
        'Shared current left T1 simulated tower, nacelle, hub and mechanical context; '
        'only the three blades are reconstructed estimates.')
    group['names'] = tuple(obj.name for obj in result)
    group['bindings'] = [(source, obj, source.name, obj.name, source.data)
                         for source, obj in group['pairs']]
    group['known'] = {obj.name: obj for pair in group['pairs'] for obj in pair}
    group['known'][yaw.name] = yaw
    group['object_count'] = len(scene.objects)
    _SESSIONS.setdefault(scene.as_pointer(), {})[names_key] = group
    return result


def sync(scene):
    """Update cached context after the farm's current-frame source writes."""
    groups = _SESSIONS.get(scene.as_pointer())
    if groups is None:
        # Saved files can restore the farm before the comparison's load hook.
        # This path scans once per load; the normal per-frame path never scans.
        for names_key, prefix, fallback in _SAVED_GROUPS:
            if scene.get(names_key) is not None:
                offset = scene.get(names_key + '_offset', fallback)
                ensure(scene, prefix=prefix, offset=offset, names_key=names_key)
        groups = _SESSIONS.get(scene.as_pointer())
    if not groups:
        return
    # A source rig/mesh can be replaced without going through FarmFlex detach.
    # Resolve all changed bindings before applying the current source pose.
    for names_key, group in list(groups.items()):
        refresh(scene, prefix=group['prefix'], offset=group['offset'], names_key=names_key)
    groups = _SESSIONS.get(scene.as_pointer(), {})
    matrices = {}
    inverses = {}
    try:
        for group in groups.values():
            key = group['yaw'].as_pointer()
            if key not in inverses:
                inverses[key] = _model_inverse(group['yaw'], matrices)
            _apply(group, matrices, inverses[key])
    except ReferenceError as exc:
        raise ValueError('模拟展示结构或左侧来源对象已删除，请重新载入重建示例') from exc
