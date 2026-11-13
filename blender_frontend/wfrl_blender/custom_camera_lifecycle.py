"""Camera drafts, reversible publication and retryable resource retirement."""
from __future__ import annotations


def begin_draft(scene, slot):
    import bpy
    slot = _core._slot(slot)
    root = _core._root(scene)
    if any(obj.get('wfrl_custom_draft') for obj in scene.objects):
        raise ValueError('请先确认或取消当前相机调整')
    original = _core.get_camera(scene, slot)
    if original is not None:
        _core.migrate_camera(scene, original)
    name = _core.camera_name(slot) + '.Draft'
    camera = data = None
    try:
        if original is not None:
            camera = original.copy()
            data = original.data.copy()
            camera.data = data
            camera.name = name
        else:
            data = bpy.data.cameras.new(name + '.Data')
            camera = bpy.data.objects.new(name, data)
        (root.users_collection[0] if root.users_collection else scene.collection).objects.link(camera)
        camera.parent = root
        camera.matrix_parent_inverse.identity()
        camera['wfrl_custom_camera'] = True
        camera['wfrl_custom_slot'] = slot
        camera['wfrl_custom_draft'] = True
        camera['custom_coordinate_version'] = _core.COORDINATE_VERSION
        camera.data.type = 'PERSP'
        if original is None:
            camera.data.clip_start, camera.data.clip_end = .005, 30000
        camera.data.display_size = .15
        from .cameras import clear_camera_mask
        clear_camera_mask(camera)
        camera.hide_render = True
        camera.lock_location = (True, True, True)
        camera.lock_rotation = (True, True, True)
        camera.lock_scale = (True, True, True)
        if original is None:
            camera['custom_enabled'] = True
            camera['custom_label'] = f'C{slot}'
            camera['custom_mount_mode'] = 'SURFACE'
            camera['custom_mount_validation'] = 'unverified'
            _core.apply_parameters(camera, _core.CameraParameters((0, 0, 0)))
    except Exception:
        if camera is not None:
            camera['wfrl_custom_camera'] = False
            camera['wfrl_custom_draft'] = True
            try:
                _core._remove(camera)
            except (RuntimeError, ValueError):
                _core._remember_retired(camera, data)
        if data is not None:
            _core._remember_retired(None, data)
            _core._cleanup_retired_data()
        raise
    return camera


def _remove(camera):
    import bpy
    try:
        data = camera.data
        bpy.data.objects.remove(camera, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.cameras.remove(data)
    except ReferenceError:
        pass


def cancel_draft(camera, *, data=None):
    try:
        if camera is not None and camera.get('wfrl_custom_draft'):
            if data is None:
                data = camera.data
            _core._remove(camera)
    except ReferenceError:
        pass
    if data is not None:
        import bpy
        try:
            if data.users == 0:
                bpy.data.cameras.remove(data)
        except ReferenceError:
            pass


def commit_draft(scene, slot, camera):
    slot = _core._slot(slot)
    if not camera.get('wfrl_custom_draft') or camera.get('wfrl_custom_slot') != slot or camera.parent != _core._root(scene):
        raise ValueError('当前草稿或 T1 安装参考已失效')
    _core.validate_parameters(_core.parameters(camera))
    from . import stacked_camera_rig
    stacked_camera_rig.validate_pose(scene, {}, [({'slot_id': slot}, _core.parameters(camera), None)])
    if camera.get('custom_mount_mode') == 'RESEARCH':
        _core.validate_research_position(scene, _core.parameters(camera).location,
                                   confirmed=bool(camera.get('custom_research_confirmed')))
    else:
        candidates = _core.validate_position(scene, _core.parameters(camera).location)
        stored = _core.anchor(camera)
        if stored is None or not any(a.surface_name == stored.surface_name
                and sum((a.point[i] - stored.point[i]) ** 2 for i in range(3)) < 1e-7
                and sum(a.normal[i] * stored.normal[i] for i in range(3)) > .9999
                and abs(a.distance - stored.distance) < 5e-5 for a in candidates):
            raise ValueError('所选安装锚点已失效，请重新选择表面')
        camera['custom_mount_validation'] = 'surface_validated'
    from . import custom_camera_history as history
    before = history.before_change(scene)
    camera['custom_model_signature'] = _core.model_signature(scene)
    _core._publish(scene, {slot: camera})
    history.committed(scene, before, f'确认 C{slot} 修改')
    return camera


def clear_slot(scene, slot):
    slot = _core._slot(slot)
    if any(obj.get('wfrl_custom_draft') for obj in scene.objects):
        raise ValueError('请先确认或取消当前相机调整')
    if _core.get_camera(scene, slot) is not None:
        from . import custom_camera_history as history
        before = history.before_change(scene)
        _core._publish(scene, {slot: None})
        history.committed(scene, before, f'清除 C{slot}')


def set_enabled(scene, slot, enabled):
    from . import custom_camera_history as history
    camera = _core.get_camera(scene, slot)
    if camera is None or bool(camera.get('custom_enabled', True)) == bool(enabled):
        return
    if any(obj.get('wfrl_custom_draft') for obj in scene.objects):
        raise ValueError('请先确认或取消当前相机调整')
    before = history.before_change(scene)
    camera['custom_enabled'] = bool(enabled)
    history.committed(scene, before, f'C{slot} ' + ('参与' if enabled else '不参与') + '三路预览与采集')
    _core._refresh_references(scene)


def _publication_checkpoint(stage, slot=None):
    """Fault-injection seam; no irreversible object deletion before commit."""


def _refresh_references(scene):
    if scene.get('wfrl_stacked_camera_rig'):
        from . import stacked_camera_rig
        stacked_camera_rig.sync_modules(scene)
    from . import custom_camera_preview as preview
    from .panels import custom_cameras as panel
    preview.shutdown()
    preview.changed()
    op = panel._ACTIVE
    if op and op.scene == scene and op.stage in panel._VIEWING:
        op.select_slot(op.slot)
        if op.stage == 'WATCH' and op.camera is None:
            # Keep publication reversible until its commit checkpoint.
            # An empty slot can remain selected in the external layout.
            op.stage = 'LAYOUT'
        op.area.tag_redraw()


def _remember_retired(obj, data):
    """Keep each failed release independent of the other resource's RNA lifetime."""
    if obj is not None and obj not in _core._RETIRED:
        _core._RETIRED.append(obj)
    if data is not None and data not in _core._RETIRED_DATA:
        _core._RETIRED_DATA.append(data)


def _cleanup_retired_data():
    import bpy
    for data in tuple(_core._RETIRED_DATA):
        try:
            if data.users:
                continue
            bpy.data.cameras.remove(data)
        except ReferenceError:
            pass
        except (RuntimeError, ValueError):
            continue
        _core._RETIRED_DATA.remove(data)


def cleanup_retired():
    for obj in tuple(_core._RETIRED):
        try:
            if (not obj.get('wfrl_custom_camera')
                    and (not obj.users_collection or obj.get('wfrl_custom_draft'))):
                _core._remove(obj)
            _core._RETIRED.remove(obj)
        except ReferenceError:
            _core._RETIRED.remove(obj)
        except (RuntimeError, ValueError):
            pass
    _core._cleanup_retired_data()


def _publish(scene, replacements):
    """Publish/detach reversibly, then garbage-collect obsolete objects.

    Originals stay alive and linked until replacement objects and references are
    ready. A failure at any pre-commit stage restores object identity and links.
    Deletion is post-commit garbage collection, never part of layout visibility.
    Objects used by another scene require independent scene ownership first.
    """
    import bpy
    originals = {slot: _core.get_camera(scene, slot) for slot in replacements}
    objects = [obj for obj in (*originals.values(), *replacements.values()) if obj is not None]
    for obj in objects:
        owners = sorted(other.name for other in bpy.data.scenes if other != scene
                        and (other.user_of_id(obj) > 0 or other.objects.get(obj.name) == obj))
        if owners:
            raise ValueError(f'相机对象“{obj.name}”被其他场景共享：{", ".join(owners)}；'
                             '请先解除这些场景对此对象的关联，或为当前场景创建独立相机对象后再修改或清除')
        for screen in bpy.data.screens:
            if not any(area.type == 'VIEW_3D' and area.spaces.active.use_local_camera
                       and area.spaces.active.camera == obj for area in screen.areas):
                continue
            windows = [window for manager in bpy.data.window_managers for window in manager.windows
                       if window.screen == screen]
            if not windows or any(window.scene != scene for window in windows):
                raise ValueError(f'相机对象“{obj.name}”被未归属于当前场景的局部相机视口引用（屏幕“{screen.name}”）；'
                                 '请先解除该视口的局部相机关联，或为当前场景创建独立相机对象后再修改或清除')
    _core.cleanup_retired()
    from .panels import custom_cameras as panel
    controller = panel._ACTIVE
    controller_stage = controller.stage if controller and controller.scene == scene else None
    saved = [(obj, obj.name, obj.data.name, bool(obj.get('wfrl_custom_camera')),
              bool(obj.get('wfrl_custom_draft')), tuple(obj.users_collection)) for obj in objects]
    refs = [(scene, 'camera', scene.camera)]
    refs.extend((marker, 'camera', marker.camera) for marker in scene.timeline_markers)
    if controller_stage is not None and not controller.closed:
        # Confirmation publishes while editing; every restore path needs a live camera.
        snapshots = [getattr(controller, name, None)
                     for name in ('snapshot', 'external_snapshot')]
        session = getattr(controller, 'session', None)
        if session is not None:
            snapshots.append(session.snapshot)
        for snapshot in snapshots:
            if snapshot is not None:
                refs.append((snapshot, 'camera', snapshot.camera))
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D' and area.spaces.active.use_local_camera:
                refs.append((area.spaces.active, 'camera', area.spaces.active.camera))
    try:
        for slot, original in originals.items():
            if original is not None:
                original['wfrl_custom_camera'] = False
                original.name += '.Retired'
                if original.data.users == 1:
                    original.data.name += '.Retired'
            _core._publication_checkpoint('retire', slot)
        for slot, cam in replacements.items():
            if cam is not None:
                cam.name = _core.camera_name(slot)
                cam.data.name = cam.name + '.Data'
                cam['wfrl_custom_camera'], cam['wfrl_custom_draft'] = True, False
            _core._publication_checkpoint('publish', slot)
        for owner, attr, old in refs:
            for slot, original in originals.items():
                if original is not None and old == original:
                    setattr(owner, attr, replacements[slot])
        _core._publication_checkpoint('references')
        for slot, original in originals.items():
            if original is not None:
                for collection in tuple(original.users_collection):
                    collection.objects.unlink(original)
            _core._publication_checkpoint('detach', slot)
        _core._refresh_references(scene)
        _core._publication_checkpoint('commit')
    except Exception:
        # First remove naming collisions, then restore exact original names.
        for obj, *_rest in saved:
            obj.name += '.Rollback'
            obj.data.name += '.Rollback'
        for obj, name, data_name, flag, draft, collections in saved:
            obj.name, obj.data.name = name, data_name
            obj['wfrl_custom_camera'], obj['wfrl_custom_draft'] = flag, draft
            for collection in collections:
                if obj.name not in collection.objects:
                    collection.objects.link(obj)
        for owner, attr, old in refs:
            setattr(owner, attr, old)
        try:
            if controller_stage is not None and not controller.closed:
                controller.stage = controller_stage
            _core._refresh_references(scene)
        except (RuntimeError, ReferenceError):
            pass
        raise
    # Unlinked, unflagged leftovers cannot appear in a slot even if Blender
    # refuses garbage collection. Retry on the next transaction.
    for original in originals.values():
        if original is not None:
            data = original.data
            try:
                _core._remove(original)
            except (RuntimeError, ValueError):
                _core._remember_retired(original, data)


# Shared state and replaceable helpers are resolved through the public namespace.
from . import custom_cameras as _core
