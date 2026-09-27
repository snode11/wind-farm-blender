"""Native camera viewing in an independent window; no offscreen preview loop.

Shared scene aspect is expanded to contain each calibrated rectangle. A mask
shows only that rectangle. Proxy cameras follow originals without modifying them.
"""
import math
from contextlib import contextmanager
import bpy
from bpy.app.handlers import persistent
from . import custom_cameras as core, camera_projection as projection

_ACTIVE = None


def fit_gate(hfov, vfov, scene_aspect):
    x, y = math.tan(math.radians(hfov / 2)), math.tan(math.radians(vfov / 2))
    outer_x = max(x, y * scene_aspect)
    return outer_x, x / outer_x, y / (outer_x / scene_aspect)


class NativeSession:
    def __init__(self, context, slots):
        self.window, self.scene, self.original = context.window, context.scene, context.workspace
        self.workspace = None
        self.owns_window = False
        self.entries = []
        self.handle = None
        self.slots = slots
        self.source_shading = []
        self.preview_quality = None
        self.fast = True
        shader=context.space_data.shading
        self.material_settings={key:getattr(shader,key) for key in (
            'use_scene_lights','use_scene_world','studio_light','studiolight_rotate_z',
            'studiolight_intensity','studiolight_background_alpha',
            'studiolight_background_blur','use_studiolight_view_rotation')}
        if shader.type not in {'MATERIAL','RENDERED'}:
            self.material_settings.update(use_scene_lights=True,use_scene_world=True,studio_light='forest.exr')

    def start(self):
        before={w.as_pointer() for w in bpy.context.window_manager.windows}
        bpy.ops.wm.window_new()
        self.window=next(w for w in bpy.context.window_manager.windows if w.as_pointer() not in before)
        self.owns_window=True
        self.workspace=self.window.workspace
        # The source window stays available but need not run another Eevee view.
        # Restore its exact shading when the camera window closes.
        for area in bpy.context.window.screen.areas:
            if area.type == 'VIEW_3D':
                shading = area.spaces.active.shading
                self.source_shading.append((bpy.context.window.as_pointer(), area.as_pointer(), shading.type))
                shading.type = 'SOLID'
        self.preview_quality = self.quality_settings()
        self.apply_quality()
        bpy.app.timers.register(self.prepare, first_interval=.1)

    def quality_settings(self):
        return (self.scene.render.preview_pixel_size, self.scene.eevee.taa_samples,
                self.scene.eevee.use_shadows, self.scene.eevee.use_fast_gi)

    def set_quality(self, settings):
        pixel_size, samples, shadows, gi = settings
        self.scene.render.preview_pixel_size = pixel_size
        self.scene.eevee.taa_samples = samples
        self.scene.eevee.use_shadows = shadows
        self.scene.eevee.use_fast_gi = gi

    def apply_quality(self):
        if self.preview_quality is not None:
            self.set_quality(('2', 8, False, False) if self.fast else self.preview_quality)

    def prepare(self):
        try:
            return self._prepare()
        except Exception as exc:
            print('Native camera setup failed:', str(exc))
            shutdown()
            return None

    def _prepare(self):
        if _ACTIVE is not self:return None
        if not any(w==self.window for w in bpy.context.window_manager.windows):return None
        areas = [a for a in self.window.screen.areas if a.type=='VIEW_3D']
        if len(areas)<len(self.slots):
            area=max(areas,key=lambda a:a.width*a.height)
            with bpy.context.temp_override(window=self.window,area=area):
                # Left-to-right equal columns, independent of window aspect.
                factor = 1 / (len(self.slots) - len(areas) + 1)
                bpy.ops.screen.area_split(direction='VERTICAL',factor=factor)
            return .1
        areas.sort(key=lambda a:a.x)
        for area, slot in zip(areas,self.slots):
            space = area.spaces.active
            space.show_region_ui = False
            space.show_region_toolbar = False
            space.show_region_tool_header = False
            area.show_menus = False
            space.overlay.show_overlays = False
            space.show_gizmo = False
            shader=space.shading
            shader.type='MATERIAL'
            for key,value in self.material_settings.items():setattr(shader,key,value)
            space.lock_camera = False
            self.entries.append({'area':area,'slot':slot,'proxy':None,'key':None,'view_key':None})
        self.sync()
        self.handle = bpy.types.SpaceView3D.draw_handler_add(self.draw,(), 'WINDOW','POST_PIXEL')
        bpy.app.timers.register(self.first_paint, first_interval=.5)

    def first_paint(self):
        # Ensure the paused first frame is swapped after all regions exist.
        # On macOS, tagging inactive regions alone can leave partial titles
        # until the first mouse event. This one-shot repaint is not a loop.
        if _ACTIVE is self and any(w == self.window for w in bpy.context.window_manager.windows):
            with bpy.context.temp_override(window=self.window):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        return None

    def sync(self):
        render = self.scene.render
        aspect = render.resolution_x*render.pixel_aspect_x/(render.resolution_y*render.pixel_aspect_y)
        for entry in self.entries:
            area,slot = entry['area'],entry['slot']
            if not any(a==area for a in self.window.screen.areas):continue
            space = area.spaces.active
            if space.type != 'VIEW_3D':continue
            camera = core.get_camera(self.scene,slot)
            # Participation controls grouped views; a disabled camera can still
            # be inspected individually before the user enables it again.
            if len(self.slots)>1 and camera and not camera.get('custom_enabled',True):camera=None
            key = (camera.as_pointer(),repr(core.parameters(camera)),aspect) if camera else None
            if key != entry['key'] or (camera and entry['proxy'] is None):
                if entry['proxy']:
                    data=entry['proxy'].data;bpy.data.objects.remove(entry['proxy'],do_unlink=True)
                    bpy.data.cameras.remove(data);entry['proxy']=None
                if camera:
                    params=core.parameters(camera)
                    outer,fx,fy=fit_gate(params.fov,params.vfov,aspect)
                    data=bpy.data.cameras.new(f'T1.Native.C{slot}')
                    data.sensor_fit='HORIZONTAL'
                    # Keep lens in Blender's valid range even at extreme FOV.
                    data.lens=min(5000.,max(1.,18./outer));data.sensor_width=2*data.lens*outer
                    if abs(data.sensor_width-2*data.lens*outer)>1e-4:
                        bpy.data.cameras.remove(data)
                        raise ValueError('当前视场超出原生相机范围，请使用精确预览')
                    data.clip_start=params.clip_near_m;data.clip_end=params.clip_far_m
                    data.passepartout_alpha=1.
                    proxy=bpy.data.objects.new(f'T1.Native.C{slot}',data)
                    self.scene.collection.objects.link(proxy)
                    proxy.hide_render=True
                    constraint=proxy.constraints.new('COPY_TRANSFORMS');constraint.target=camera
                    entry.update(proxy=proxy,fx=fx,fy=fy)
                entry['key']=key
            region=next((r for r in area.regions if r.type=='WINDOW'),None)
            view_key=(key, region.width, region.height) if region else None
            if entry['proxy'] and view_key != entry['view_key']:
                space.use_local_camera=True;space.camera=entry['proxy']
                space.region_3d.view_perspective='CAMERA'
                region=next((r for r in area.regions if r.type=='WINDOW'),None)
                if region:
                    outer_w=min(region.width,region.height*aspect)
                    outer_h=outer_w/aspect
                    from .custom_camera_preview import available_rectangle
                    _, bottom, _, height = available_rectangle(area, region)
                    scale = bpy.context.preferences.system.ui_scale
                    reserved = region.height-(bottom+height)+66*scale+16
                    # The camera gate stays centred. Reserve its full image
                    # below the title even when Blender overlays the header.
                    factor=min((region.width-32)/max(1,outer_w*entry['fx']),
                               max(1,region.height-2*reserved)/max(1,outer_h*entry['fy']))
                    space.region_3d.view_camera_zoom=max(-30,min(300,(math.sqrt(max(.01,4*factor))-math.sqrt(2))*50))
                space.region_3d.view_camera_offset=(0,0)
                space.lock_camera=False
                entry['view_key']=view_key
                area.tag_redraw()

    def draw(self):
        if bpy.context.window != self.window or bpy.context.workspace != self.workspace:return
        entry=next((e for e in self.entries if e['area']==bpy.context.area),None)
        if not entry:return
        import gpu,blf
        from gpu_extras.batch import batch_for_shader
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        region=bpy.context.region;rv=bpy.context.space_data.region_3d
        shader=gpu.shader.from_builtin('UNIFORM_COLOR')
        def rect(x,y,w,h):
            if w<=0 or h<=0:return
            shader.bind();shader.uniform_float('color',(.015,.02,.025,1))
            batch_for_shader(shader,'TRI_FAN',{'pos':[(x,y),(x+w,y),(x+w,y+h),(x,y+h)]}).draw(shader)
        proxy=entry['proxy']
        if proxy:
            corners=[location_3d_to_region_2d(region,rv,proxy.matrix_world@v) for v in proxy.data.view_frame(scene=self.scene)]
            if any(v is None for v in corners):return
            xs,ys=[v.x for v in corners],[v.y for v in corners]
            cx,cy=(min(xs)+max(xs))/2,(min(ys)+max(ys))/2
            w,h=(max(xs)-min(xs))*entry['fx'],(max(ys)-min(ys))*entry['fy']
            left,right,bottom,top=cx-w/2,cx+w/2,cy-h/2,cy+h/2
            rect(0,0,region.width,max(0,bottom));rect(0,top,region.width,region.height-top)
            rect(0,bottom,max(0,left),h);rect(right,bottom,region.width-right,h)
        else:rect(0,0,region.width,region.height)
        label=(f"C{entry['slot']} · " + ('叶根', '中段', '叶尖')[entry['slot']-1]) if proxy else f"C{entry['slot']} · 未安装或未参与"
        scale=bpy.context.preferences.system.ui_scale
        from .custom_camera_preview import available_rectangle
        _, bottom, _, height = available_rectangle(bpy.context.area, region)
        title_top=bottom+height
        rect(0,title_top-66*scale,region.width,66*scale)
        font=0
        blf.size(font,20*scale);blf.color(font,.88,.94,1,1)
        blf.position(font,16*scale,title_top-28*scale,0);blf.draw(font,label)
        time_s=self.scene.get('wfrl_clearance_time_s')
        clock=f'仿真时间 {time_s:.2f} s' if time_s is not None else f'帧 {self.scene.frame_current}'
        blf.size(font,13*scale);blf.color(font,.65,.73,.81,1)
        blf.position(font,16*scale,title_top-51*scale,0);blf.draw(font,clock)

    def close(self):
        if self.preview_quality is not None:
            self.set_quality(self.preview_quality)
            self.preview_quality = None
        if self.handle:
            bpy.types.SpaceView3D.draw_handler_remove(self.handle,'WINDOW');self.handle=None
        # Closed-window Area RNA may point at freed C memory. Never dereference
        # cached areas after the user closes the OS window.
        alive=next((w for w in bpy.context.window_manager.windows if w==self.window),None)
        if alive and self.owns_window:
            for area in alive.screen.areas:
                if area.type=='VIEW_3D':area.spaces.active.camera=None
        for entry in self.entries:
            proxy=entry['proxy']
            if proxy:
                data=proxy.data;bpy.data.objects.remove(proxy,do_unlink=True);bpy.data.cameras.remove(data)
        self.entries.clear()
        for window_id, area_id, shading_type in self.source_shading:
            source = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == window_id), None)
            if source:
                area = next((a for a in source.screen.areas if a.as_pointer() == area_id), None)
                if area and area.type == 'VIEW_3D':
                    area.spaces.active.shading.type = shading_type
        self.source_shading.clear()
        if alive and self.owns_window:
            with bpy.context.temp_override(window=alive):bpy.ops.wm.window_close()


@contextmanager
def capture_quality(scene):
    """Keep PNG sampling independent of the temporary live preview budget."""
    session = _ACTIVE
    if session is None or session.scene != scene or session.preview_quality is None:
        yield
        return
    current = session.quality_settings()
    try:
        session.set_quality(session.preview_quality)
        yield
    finally:
        session.set_quality(current)


def shutdown():
    global _ACTIVE
    if _ACTIVE:
        session,_ACTIVE=_ACTIVE,None
        session.close()


def watch():
    if _ACTIVE:
        try:
            if (not any(w==_ACTIVE.window for w in bpy.context.window_manager.windows)
                    or _ACTIVE.window.scene!=_ACTIVE.scene):
                shutdown()
            else:_ACTIVE.sync()
        except (ReferenceError,RuntimeError,ValueError) as exc:
            print('Native camera view closed:',str(exc))
            shutdown()
    return .25


def header(self,context):
    if _ACTIVE and context.window==_ACTIVE.window:
        row=self.layout.row(align=True)
        entry=next((e for e in _ACTIVE.entries if e['area']==context.area),None)
        if entry:row.label(text=f"C{entry['slot']}")
        row.operator('screen.animation_play',text='暂停' if context.screen.is_animation_playing else '播放',icon='PAUSE' if context.screen.is_animation_playing else 'PLAY')
        row.operator('wfrl.native_camera_quality',text='流畅' if _ACTIVE.fast else '高清')
        row.operator('wfrl.native_camera_exit',text='退出',icon='X')


class WFRL_OT_NativeCamera(bpy.types.Operator):
    bl_idname='wfrl.native_camera_view'
    bl_label='原生相机观察'
    mode:bpy.props.EnumProperty(items=[('WATCH','单路',''),('TRIPLE','三路','')])
    @classmethod
    def poll(cls,context):
        return bool(context.window and context.area and context.area.type=='VIEW_3D'
                    and core.is_available(context.scene))

    def execute(self,context):
        global _ACTIVE
        from .panels import custom_cameras as panel
        from . import custom_camera_capture
        if custom_camera_capture.active() or (panel._ACTIVE and panel._ACTIVE.stage not in panel._VIEWING):
            self.report({'WARNING'},'请先完成采集或确认相机草稿');return {'CANCELLED'}
        if not core.is_available(context.scene):return {'CANCELLED'}
        slot=context.window_manager.wfrl_custom_slot
        if self.mode=='WATCH' and not core.get_camera(context.scene,slot):return {'CANCELLED'}
        if panel._ACTIVE:panel._ACTIVE.finish()
        shutdown()
        slots = list(core.SLOTS) if self.mode == 'TRIPLE' else [slot]
        session=NativeSession(context, slots)
        try:
            session.start();_ACTIVE=session
            if not bpy.app.timers.is_registered(watch):bpy.app.timers.register(watch,first_interval=.25)
            return {'FINISHED'}
        except Exception as exc:
            session.close();self.report({'ERROR'},str(exc));return {'CANCELLED'}


class WFRL_OT_NativeQuality(bpy.types.Operator):
    bl_idname='wfrl.native_camera_quality'
    bl_label='切换预览画质'
    bl_description='切换流畅材质预览与原始预览画质；原图采集保持原始画质'
    def execute(self,context):
        if _ACTIVE:
            _ACTIVE.fast = not _ACTIVE.fast
            _ACTIVE.apply_quality()
        return {'FINISHED'}


class WFRL_OT_NativeExit(bpy.types.Operator):
    bl_idname='wfrl.native_camera_exit'
    bl_label='退出原生相机观察'
    def execute(self,context):
        shutdown();return {'FINISHED'}


def register():
    bpy.types.VIEW3D_HT_header.prepend(header)
    bpy.app.handlers.load_pre.append(on_load)


@persistent
def on_load(_):shutdown()


def unregister():
    shutdown()
    if bpy.app.timers.is_registered(watch):bpy.app.timers.unregister(watch)
    bpy.types.VIEW3D_HT_header.remove(header)
    if on_load in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.remove(on_load)


CLASSES=(WFRL_OT_NativeCamera,WFRL_OT_NativeQuality,WFRL_OT_NativeExit)
