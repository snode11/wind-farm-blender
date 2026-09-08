"""Native GPU/BLF presentation overlay; offline values share the Demo sampler."""
from functools import lru_cache

_HANDLE = None


@lru_cache(maxsize=1)
def _series():
    from .state import sample_demo
    return tuple(sample_demo(i / 2) for i in range(133))


def _draw():
    import bpy
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    from .state import sample_demo, time_for_frame
    from . import runtime
    context = bpy.context
    if context.workspace.name != 'WFRL Workspace':
        return
    region = context.region
    if region is None or region.type != 'WINDOW':
        return
    width, height = region.width, region.height
    height -= 32 * context.preferences.system.pixel_size
    if runtime.get_state().connection != "LOCAL DEMO":
        from .overlays import live_overlay
        wake = None
        if runtime.latest_wake is not None:
            wake = {"source": runtime.latest_wake.source_label,
                    "fidelity": runtime.latest_wake.fidelity}
        lines = live_overlay(runtime.get_state(), runtime.kinematics, wake=wake)
        scale = max(1.0, context.preferences.system.ui_scale)
        blf.size(0, 14 * scale)
        blf.color(0, .76, .86, 1.0, 1.0)
        toolbar_width = max((r.width for r in context.area.regions if r.type == 'TOOLS'), default=0) if context.space_data.show_region_toolbar else 0
        for index, line in enumerate(lines):
            blf.position(0, toolbar_width + 20, height - (28 + index * 20) * scale, 0)
            blf.draw(0, line)
        return
    if context.space_data.use_local_camera:
        blf.size(0, 16 * max(1,context.preferences.system.ui_scale))
        blf.position(0, 20, height-40, 0)
        blf.color(0,.94,.67,.28,1)
        camera = context.space_data.camera
        label = camera.name.removeprefix('WFRL.Camera.') if camera else 'Camera'
        blf.draw(0, f'{label.upper()} / GEOMETRIC VIEW / SYNTH')
        return
    if width < 400 or height < 240:
        return
    ui_width = max((r.width for r in context.area.regions if r.type == 'UI'), default=0) if context.space_data.show_region_ui else 0
    width -= ui_width if bpy.context.preferences.system.use_region_overlap else 0
    scale = max(1.0, context.preferences.system.ui_scale) * 1.35
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    def rect(x,y,w,h,color):
        shader.bind(); shader.uniform_float('color', color)
        batch_for_shader(shader, 'TRIS', {'pos': [(x,y),(x+w,y),(x+w,y+h),(x,y),(x+w,y+h),(x,y+h)]}).draw(shader)
    def text(x,y,label,size=13,color=(.76,.82,.87,1)):
        blf.size(0, size * scale); blf.color(0,*color); blf.position(0,x,y,0); blf.draw(0,str(label))
    def line(points,color,thickness=1):
        shader.bind(); shader.uniform_float('color',color); gpu.state.line_width_set(thickness)
        batch_for_shader(shader,'LINE_STRIP',{'pos':points}).draw(shader)
    gpu.state.blend_set('ALPHA')
    scene = context.scene
    t = time_for_frame(scene.frame_current)
    if not scene.wfrl_channel_telemetry:
        t = scene.get("wfrl_telemetry_time_s", t)
    frame = sample_demo(t)
    accent=(.15,.71,.84,1); muted=(.44,.54,.63,1); amber=(.94,.67,.28,1)
    rect(0,height-65*scale,width,65*scale,(.025,.041,.061,.97))
    text(22,height-33*scale,'WFRL',22,(.94,.97,.99,1))
    text(116*scale,height-30*scale,'WIND FARM CONTROL LAB',12)
    text(116*scale,height-50*scale,'turb3_demo  /  OFFLINE PRESENTATION',10,muted)
    if width > 760:
        text(width-330*scale,height-29*scale,scene.get('wfrl_run_status','READY')+'   |   '+f'{t:04.1f} / 66 s',13,accent)
        text(width-330*scale,height-49*scale,'SYNTH   /   no backend connected',11,amber)
    if width < 650:
        gpu.state.blend_set('NONE'); return
    left=184*scale
    rect(12,height-306*scale,left,227*scale,(.035,.055,.076,.94))
    text(26,height-103*scale,'SCENE',11,muted)
    for i,(label,value) in enumerate([('TURBINES','03 / NREL 5MW'),('INFLOW','8.0 m/s  /  +X'),('ROTOR','126 m diameter'),('GEOMETRY','Local template / 1:1')]):
        y=height-(131+i*41)*scale
        text(26,y,label,9,muted); text(26,y-18*scale,value,12)
    graph_h=170*scale
    rect(12,12,width-24,graph_h,(.025,.041,.061,.96))
    text(28,graph_h-10*scale,'FARM POWER / MW',11)
    text(230*scale,graph_h-10*scale,'SYNTH  /  deterministic preview',10,amber)
    x0,x1,y0,y1=54*scale,width-30*scale,40*scale,graph_h-35*scale
    for value in (0,1,2,3,4):
        yy=y0+(y1-y0)*value/4
        line([(x0,yy),(x1,yy)],(.13,.19,.25,.7))
        text(27,yy-4,str(value),9,muted)
    for seconds in (0,15,30,45,60,66):
        xx=x0+(x1-x0)*seconds/66
        text(xx-4,22*scale,str(seconds)+'s',9,muted)
    values=_series()
    colors=[accent,(.41,.60,.93,1),(.71,.78,.85,1),(.91,.91,.86,1)]
    for index in range(4):
        pts=[]
        for f in values:
            if f.time_s > t: break
            value=sum(f.power_mw) if index==3 else f.power_mw[index]
            pts.append((x0+(x1-x0)*f.time_s/66,y0+(y1-y0)*value/4))
        if len(pts)>1: line(pts,colors[index],2 if index==3 else 1)
    cursor=x0+(x1-x0)*t/66
    line([(cursor,y0),(cursor,y1)],(*amber[:3],.65))
    if width>950:
        text(width-285*scale,graph_h-10*scale,'T1    T2    T3    Total',10)
    text(left+33,height-95*scale,'WAKE PROXY / SYNTH',10,amber)
    text(left+33,height-114*scale,'Illustrative volume; terrain does not enter physics',10,muted)
    gpu.state.line_width_set(1); gpu.state.blend_set('NONE')


def register_overlay():
    global _HANDLE
    import bpy
    if _HANDLE is None:
        _HANDLE = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
        bpy._wfrl_overlay_handle = _HANDLE


def unregister_overlay():
    global _HANDLE
    import bpy
    handle = getattr(bpy, '_wfrl_overlay_handle', _HANDLE)
    if handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    if hasattr(bpy, '_wfrl_overlay_handle'):
        del bpy._wfrl_overlay_handle
    _HANDLE = None


def recording_schedule(fps, duration):
    """Capture wall-clock frames; never seek or advance simulation time."""
    import math
    fps, duration = float(fps), float(duration)
    if not math.isfinite(fps) or not 1 <= fps <= 30:
        raise ValueError('Capture frame rate must be between 1 and 30')
    if not math.isfinite(duration) or not 0 < duration <= 3600:
        raise ValueError('Capture duration must be between 0 and 3600 seconds')
    return 1 / fps, max(1, math.ceil(fps * duration))


def set_presentation_mode(context, enabled):
    """Change workspace chrome without touching runtime or scientific overlays."""
    context.workspace['presentation_mode'] = bool(enabled)
    context.scene['wfrl_presentation_mode'] = bool(enabled)
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                space.show_region_toolbar = not enabled
                space.show_region_tool_header = not enabled
                space.show_gizmo = not enabled
                area.tag_redraw()
