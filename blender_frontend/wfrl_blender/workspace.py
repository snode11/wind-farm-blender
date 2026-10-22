"""Dedicated, saved native workspace without touching the user's global theme."""
WORKSPACE_NAME = 'WFRL Workspace'


def ensure_workspace():
    import bpy
    workspace = bpy.data.workspaces.get(WORKSPACE_NAME)
    if workspace is None:
        if bpy.context.window is not None:
            bpy.ops.workspace.duplicate()
        workspace = bpy.context.workspace
        workspace.name = WORKSPACE_NAME
    if bpy.context.window is not None:
        bpy.context.window.workspace = workspace
    workspace['wfrl_workspace'] = True
    workspace['presentation_mode'] = True
    scene = bpy.context.scene
    if 'wfrl_run_status' not in scene:
        scene['wfrl_run_status'] = 'READY'
    scene['wfrl_fidelity'] = 'NO DATA'
    scene['wfrl_data_note'] = 'Load MAPPO recorded results to begin'
    return workspace


def configure_presentation():
    import bpy
    ensure_workspace()
    bpy.context.view_layer.objects.active = None
    screen = bpy.context.screen
    if screen is None:
        return
    main = max((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a:a.width*a.height, default=None)
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            space.show_region_toolbar = False
            space.show_region_tool_header = False
            space.show_region_ui = (area == main)
            space.show_gizmo = False
            space.overlay.show_overlays = False
            space.shading.type = 'MATERIAL'
            space.shading.use_scene_world = True
            space.shading.use_scene_lights = True
            space.clip_start = 1.0
            space.clip_end = 10000
            space.region_3d.view_perspective = 'CAMERA'
            space.region_3d.view_camera_zoom = 10
            from .cameras import fill_camera_view
            fill_camera_view(area, scene=bpy.context.scene)
        elif area.type == 'DOPESHEET_EDITOR':
            area.spaces.active.mode = 'TIMELINE'
    bpy.context.scene['wfrl_layout_ready'] = True
