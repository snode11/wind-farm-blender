"""Custom-camera parameter fields, update callbacks and property registration."""


def _parameters(wm):
    return _panel.core.CameraParameters(location=tuple(getattr(wm, 'wfrl_custom_'+k) for k in ('x', 'y', 'z')),
        **{k: getattr(wm, 'wfrl_custom_'+k) for k in ('yaw', 'pitch', 'roll', 'fov', 'vfov')},
        focal_length_mm_record=wm.wfrl_custom_focal if wm.wfrl_custom_has_focal else None,
        output_long_edge_px=wm.wfrl_custom_long_edge,
        clip_near_m=wm.wfrl_custom_near, clip_far_m=wm.wfrl_custom_far)


def sync_fields(wm, camera):
    _panel._SYNCING = True
    try:
        p = _panel.core.parameters(camera)
        for key, value in zip(('x', 'y', 'z'), p.location):
            setattr(wm, 'wfrl_custom_'+key, value)
        for key in ('yaw', 'pitch', 'roll', 'fov', 'vfov'):
            setattr(wm, 'wfrl_custom_'+key, getattr(p, key))
        wm.wfrl_custom_has_focal = p.focal_length_mm_record is not None
        wm.wfrl_custom_focal = p.focal_length_mm_record or 35.
        wm.wfrl_custom_long_edge = p.output_long_edge_px
        wm.wfrl_custom_near, wm.wfrl_custom_far = p.clip_near_m, p.clip_far_m
        wm.wfrl_custom_mount_mode = camera.get('custom_mount_mode', 'SURFACE')
        wm.wfrl_custom_research_confirmed = camera.get('custom_research_confirmed', False)
        wm.wfrl_custom_label = camera.get('custom_label', '')
        anchor = _panel.core.anchor(camera)
        if anchor:
            wm.wfrl_custom_distance = anchor.distance
    finally:
        _panel._SYNCING = False


def fields_changed(wm, context):
    if _panel._SYNCING or not _panel._ACTIVE or _panel._ACTIVE.stage in _panel._VIEWING:
        return
    op = _panel._ACTIVE
    _panel.diagnostics.record('t1', input='numeric', slot=op.slot)
    try:
        params = _panel._parameters(wm)
        if op.stage == 'INPUT':
            op.ready = False
            op.error = ''
            return
        op.camera['custom_label'] = wm.wfrl_custom_label
        if wm.wfrl_custom_mount_mode == 'RESEARCH':
            _panel.core.apply_research(op.scene, op.camera, params, wm.wfrl_custom_research_confirmed)
            op.ready, op.error = True, ''
            return
        old = _panel.core.parameters(op.camera)
        if any(abs(a-b) > 1e-7 for a,b in zip(params.location, old.location)):
            anchors = _panel.core.validate_position(op.scene, params.location)
            op.candidates = anchors
            if len(anchors) > 1:
                op.ready = False
                op.error = '多个候选安装面：请在外部视图选择锚点'
                op.stage = 'INPUT'
                op.external()
                return
            _panel.core.apply_parameters(op.camera, params, anchor=anchors[0])
        else:
            _panel.core.apply_parameters(op.camera, params)
        op.ready = _panel.core.anchor(op.camera) is not None
        op.error = '' if op.ready else '请先点击机舱外壳选择有效安装位置'
    except (ValueError, RuntimeError) as exc:
        op.ready, op.error = False, str(exc)
    finally:
        _panel._redraw_if_changed(op)


def distance_changed(wm, context):
    if _panel._SYNCING or not _panel._ACTIVE or _panel._ACTIVE.stage not in {'PLACE', 'AIM'}:
        return
    try:
        _panel.core.set_distance(_panel._ACTIVE.camera, wm.wfrl_custom_distance)
        _panel.sync_fields(wm, _panel._ACTIVE.camera)
        _panel._ACTIVE.ready, _panel._ACTIVE.error = True, ''
    except (ValueError, RuntimeError) as exc:
        _panel._ACTIVE.ready, _panel._ACTIVE.error = False, str(exc)
    finally:
        _panel._redraw_if_changed(_panel._ACTIVE)


def register_properties():
    from . import stacked_camera_rig as box_panel
    box_panel.register_settings()
    from .. import native_camera_views
    native_camera_views.register()
    if _panel.migrate_loaded not in _panel.bpy.app.handlers.load_post:
        _panel.bpy.app.handlers.load_post.append(_panel.migrate_loaded)
    # Blender restricts scene access while enabling an extension. Migrate once
    # registration has finished; load_post continues to handle later file loads.
    if not _panel.bpy.app.timers.is_registered(_panel.migrate_loaded):
        _panel.bpy.app.timers.register(_panel.migrate_loaded, first_interval=0.)
    if _panel.cancel_on_load not in _panel.bpy.app.handlers.load_pre:
        _panel.bpy.app.handlers.load_pre.insert(0,_panel.cancel_on_load)
    _panel.bpy.types.WindowManager.wfrl_custom_slot=_panel.IntProperty(default=1,min=1,max=_panel.core.MAX_CUSTOM_CAMERAS,options={'SKIP_SAVE'})
    for key in _panel._FIELDS:
        default={'fov':75.,'vfov':_panel.projection.DEFAULT_VFOV,'distance':.1,'focal':35.,'near':.005,'far':30000.}.get(key,0.)
        prop=_panel.FloatProperty(default=default,precision=4 if key in {'x','y','z','distance'} else 3,
            options={'SKIP_SAVE'},update=_panel.distance_changed if key=='distance' else _panel.fields_changed)
        setattr(_panel.bpy.types.WindowManager,'wfrl_custom_'+key,prop)
    for key,prop in {
        'engineering':_panel.BoolProperty(default=False,options={'SKIP_SAVE'}),
        'linked_zoom':_panel.BoolProperty(default=False,options={'SKIP_SAVE'}),
        'has_focal':_panel.BoolProperty(default=False,options={'SKIP_SAVE'},update=_panel.fields_changed),
        'show_axis':_panel.BoolProperty(default=True,options={'SKIP_SAVE'}),
        'show_frustum':_panel.BoolProperty(default=True,options={'SKIP_SAVE'}),
        'research_confirmed':_panel.BoolProperty(default=False,options={'SKIP_SAVE'},update=_panel.fields_changed),
        'label':_panel.StringProperty(default='',options={'SKIP_SAVE'},update=_panel.fields_changed),
        'mount_mode':_panel.EnumProperty(items=[('SURFACE','表面安装',''),('RESEARCH','研发坐标','')],options={'SKIP_SAVE'},update=_panel.fields_changed),
        'long_edge':_panel.IntProperty(default=1920,min=16,options={'SKIP_SAVE'},update=_panel.fields_changed),
    }.items():setattr(_panel.bpy.types.WindowManager,'wfrl_custom_'+key,prop)
    from .custom_camera_output import register_settings
    register_settings()
    _panel.preview.register_change_handler()


def unregister_properties():
    from . import stacked_camera_rig as box_panel
    box_panel.unregister_settings()
    from .. import native_camera_views
    native_camera_views.unregister()
    if _panel.bpy.app.timers.is_registered(_panel.migrate_loaded):
        _panel.bpy.app.timers.unregister(_panel.migrate_loaded)
    if _panel.bpy.app.timers.is_registered(_panel.lifecycle_watchdog):
        _panel.bpy.app.timers.unregister(_panel.lifecycle_watchdog)
    if _panel._ACTIVE:
        _panel._ACTIVE.finish(restore=True,resume=True)
    if _panel.cancel_on_load in _panel.bpy.app.handlers.load_pre:
        _panel.bpy.app.handlers.load_pre.remove(_panel.cancel_on_load)
    if _panel.migrate_loaded in _panel.bpy.app.handlers.load_post:
        _panel.bpy.app.handlers.load_post.remove(_panel.migrate_loaded)
    from .. import custom_camera_capture
    custom_camera_capture.shutdown()
    _panel.preview.shutdown()
    _panel.preview.unregister_change_handler()
    from .custom_camera_output import unregister_settings
    unregister_settings()
    _panel.history.HISTORY.clear()
    _panel.core.cleanup_retired()
    for key in (*_panel._FIELDS,'engineering','slot','linked_zoom','has_focal','show_axis','show_frustum','research_confirmed','label','mount_mode','long_edge'):
        name='wfrl_custom_'+key
        if hasattr(_panel.bpy.types.WindowManager,name):delattr(_panel.bpy.types.WindowManager,name)


# Runtime callbacks share the public namespace; definitions are available first
# so importing either this module or the public entry point is supported.
from . import custom_cameras as _panel
