"""Native camera viewing in an independent window; no offscreen preview loop.

Shared scene aspect is expanded to contain each calibrated rectangle. A mask
shows only that rectangle. Proxy cameras follow originals without modifying them.
"""
import math
from contextlib import contextmanager
import bpy
from bpy.app.handlers import persistent
from . import custom_cameras as core, camera_projection as projection
from . import installation_schematic as schematic

_ACTIVE = None
_KEYMAPS = []


def fit_gate(hfov, vfov, scene_aspect):
    x, y = math.tan(math.radians(hfov / 2)), math.tan(math.radians(vfov / 2))
    outer_x = max(x, y * scene_aspect)
    return outer_x, x / outer_x, y / (outer_x / scene_aspect)


def gate_bounds(scene, entry, region, rv):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    proxy = entry['proxy']
    if not proxy:return None
    corners=[location_3d_to_region_2d(region,rv,proxy.matrix_world@v) for v in proxy.data.view_frame(scene=scene)]
    if any(v is None for v in corners):return None
    xs,ys=[v.x for v in corners],[v.y for v in corners]
    cx,cy=(min(xs)+max(xs))/2,(min(ys)+max(ys))/2
    width,height=(max(xs)-min(xs))*entry['fx'],(max(ys)-min(ys))*entry['fy']
    return cx-width/2,cy-height/2,width,height


def frame_gate(scene, entry, region, space):
    """Fit the calibrated gate under its title without symmetrical wasted space.

    Use Blender's current projected gate to calibrate pan/zoom, which also
    handles UI scaling, wide/tall windows and non-square render pixels.
    """
    from .custom_camera_preview import available_rectangle
    left, bottom, width, height = available_rectangle(entry['area'], region)
    scale = bpy.context.preferences.system.ui_scale
    margin = 10*scale
    target = (left+margin, bottom+margin, max(1,width-2*margin),
              max(1,height-28*scale-2*margin))
    rv=space.region_3d
    rv.view_camera_offset=(0,0);rv.view_camera_zoom=0
    rv.update()
    bounds=gate_bounds(scene,entry,region,rv)
    if not bounds or min(bounds[2:]) <= 0:return
    factor=min(target[2]/bounds[2],target[3]/bounds[3])
    rv.view_camera_zoom=max(-30,min(300,(math.sqrt(max(.001,2*factor))-math.sqrt(2))*50))
    rv.update()
    bounds=gate_bounds(scene,entry,region,rv)
    center=(bounds[0]+bounds[2]/2,bounds[1]+bounds[3]/2)
    rv.view_camera_offset=(.01,.01)
    rv.update()
    moved=gate_bounds(scene,entry,region,rv)
    shift=(moved[0]+moved[2]/2-center[0],moved[1]+moved[3]/2-center[1])
    target_center=(target[0]+target[2]/2,target[1]+target[3]/2)
    rv.view_camera_offset=tuple(.01*(target_center[i]-center[i])/shift[i] if abs(shift[i])>1e-6 else 0. for i in (0,1))
    rv.update()


class NativeSession:
    def __init__(self, context, slots, layout='GRID'):
        self.window, self.scene, self.original = context.window, context.scene, context.workspace
        self.source_window_id = context.window.as_pointer()
        self.lifecycle = []
        self.workspace = None
        self.owns_window = False
        self.entries = []
        self.handle = None
        self.diagram_handle = None
        self.diagram_area = None
        self.diagram_key = None
        self.diagram = None
        self.diagram_stale = True
        self.diagram_error = ''
        self.diagram_batches = None
        self.layout = layout
        self.preparing = False
        self.preparation_steps = 0
        self.merge_first = False
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
        self.preparing = True
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
        # Single-camera inspection defaults to Workbench shading, including
        # when focusing from a high-quality triple view. Keep the two choices
        # separate so returning to the triple view restores its own quality.
        single = len(self.slots) == 1
        fast = True if single else self.fast
        if self.preview_quality is not None:
            self.set_quality(('2', 8, False, False) if fast else self.preview_quality)
        for entry in self.entries:
            shader = entry['area'].spaces.active.shading
            if single:
                shader.type = 'SOLID'
                shader.light = 'STUDIO'
                shader.studio_light = 'studio.sl'
                shader.color_type = 'MATERIAL'
                shader.show_shadows = False
                shader.show_cavity = False
                shader.show_specular_highlight = False
            else:
                shader.type = 'MATERIAL'
                for key, value in self.material_settings.items():
                    setattr(shader, key, value)
            entry['area'].tag_redraw()

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
        self.preparation_steps += 1
        if self.preparation_steps > 30:
            raise RuntimeError('相机窗口分区操作未完成')
        areas = [a for a in self.window.screen.areas if a.type in {'VIEW_3D', 'IMAGE_EDITOR'}]
        if self.merge_first and len(areas) > 1:
            # Joining/splitting the existing screen preserves its playback timer.
            # Closing the old window and opening another can destroy the owner.
            tolerance = max(2, 8*bpy.context.preferences.system.ui_scale)
            for first in areas:
                for second in areas:
                    if first == second:continue
                    horizontal = (abs(first.y-second.y) <= tolerance and abs(first.height-second.height) <= tolerance
                                  and (abs(first.x+first.width-second.x) <= tolerance or abs(second.x+second.width-first.x) <= tolerance))
                    vertical = (abs(first.x-second.x) <= tolerance and abs(first.width-second.width) <= tolerance
                                and (abs(first.y+first.height-second.y) <= tolerance or abs(second.y+second.height-first.y) <= tolerance))
                    if horizontal or vertical:
                        with bpy.context.temp_override(window=self.window, area=first):
                            result = bpy.ops.screen.area_join(
                                source_xy=(first.x+first.width//2,first.y+first.height//2),
                                target_xy=(second.x+second.width//2,second.y+second.height//2))
                        if result != {'FINISHED'}:raise RuntimeError('无法合并原生相机分区')
                        return .1
            raise RuntimeError('无法找到可合并的相机分区')
        self.merge_first = False
        count = 4 if len(self.slots) > 1 and self.layout == 'GRID' else len(self.slots)
        if len(areas) < count:
            area=max(areas,key=lambda a:a.width*a.height)
            with bpy.context.temp_override(window=self.window,area=area):
                direction = 'VERTICAL' if self.layout == 'STRIP' or len(areas) == 1 else 'HORIZONTAL'
                factor = 1 / (count-len(areas)+1) if self.layout == 'STRIP' else .5
                if bpy.ops.screen.area_split(direction=direction,factor=factor) != {'FINISHED'}:
                    raise RuntimeError('无法建立原生相机分区')
            return .1
        if count == 4:
            areas.sort(key=lambda a:(-a.y, a.x))
            self.diagram_area = areas[3]
            self.diagram_area.type = 'IMAGE_EDITOR'
            space = self.diagram_area.spaces.active
            space.show_region_ui = False
            space.show_region_toolbar = False
            self.diagram_handle = bpy.types.SpaceImageEditor.draw_handler_add(self.draw_diagram,(), 'WINDOW','POST_PIXEL')
            self.diagram_stale = True
        else:
            areas.sort(key=lambda a:a.x)
        for area, slot in zip(areas,self.slots):
            area.type = 'VIEW_3D'
            space = area.spaces.active
            space.show_region_ui = False
            space.show_region_toolbar = False
            space.show_region_tool_header = False
            area.show_menus = False
            space.overlay.show_overlays = False
            space.show_gizmo = False
            space.lock_camera = False
            self.entries.append({'area':area,'slot':slot,'proxy':None,'key':None,'view_key':None})
        self.apply_quality()
        self.sync()
        self.handle = bpy.types.SpaceView3D.draw_handler_add(self.draw,(), 'WINDOW','POST_PIXEL')
        self.preparing = False
        bpy.app.timers.register(self.first_paint, first_interval=.5)

    def clear_views(self):
        if self.handle:
            bpy.types.SpaceView3D.draw_handler_remove(self.handle,'WINDOW');self.handle=None
        if self.diagram_handle:
            bpy.types.SpaceImageEditor.draw_handler_remove(self.diagram_handle,'WINDOW');self.diagram_handle=None
        alive=next((w for w in bpy.context.window_manager.windows if w==self.window),None)
        if alive and self.owns_window:
            for area in alive.screen.areas:
                if area.type=='VIEW_3D':area.spaces.active.camera=None
        for entry in self.entries:
            proxy=entry['proxy']
            if proxy:
                data=proxy.data;bpy.data.objects.remove(proxy,do_unlink=True);bpy.data.cameras.remove(data)
        self.entries.clear()
        self.diagram_area = None

    def change_view(self, slots, layout=None):
        from . import custom_camera_capture
        if custom_camera_capture.active():raise ValueError('请先完成或取消原图采集')
        if self.preparing:raise ValueError('请等待当前相机布局完成')
        layout = layout or self.layout
        if list(slots) == list(self.slots) and layout == self.layout:return
        self.clear_views()
        self.slots, self.layout = list(slots), layout
        self.merge_first = True
        self.preparing = True
        self.preparation_steps = 0
        bpy.app.timers.register(self.prepare, first_interval=.01)

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
                    with bpy.context.temp_override(window=self.window,area=area,region=region):
                        bpy.context.view_layer.update()
                        frame_gate(self.scene,entry,region,space)
                space.lock_camera=False
                entry['view_key']=view_key
                area.tag_redraw()
        if self.diagram_area:
            key = schematic.fingerprint(self.scene)
            if key != self.diagram_key:
                self.diagram_key = key
                self.diagram_stale = True
                self.diagram_error = ''
                self.diagram_area.tag_redraw()
                # First expose invalidation. Snapshot on the following watch
                # pass, so an old image never appears to be the new layout.
            elif self.diagram_stale and not self.diagram_error:
                try:
                    self.diagram = schematic.capture(self.scene)
                    self.diagram_stale = False
                except (ValueError, RuntimeError, KeyError) as exc:
                    self.diagram_error = str(exc)
                self.diagram_area.tag_redraw()

    def draw(self):
        if bpy.context.window != self.window or bpy.context.workspace != self.workspace:return
        entry=next((e for e in self.entries if e['area']==bpy.context.area),None)
        if not entry:return
        from . import performance
        performance.count('native.draw')
        import gpu,blf
        from gpu_extras.batch import batch_for_shader
        region=bpy.context.region;rv=bpy.context.space_data.region_3d
        shader=gpu.shader.from_builtin('UNIFORM_COLOR')
        def rect(x,y,w,h):
            if w<=0 or h<=0:return
            shader.bind();shader.uniform_float('color',(.015,.02,.025,1))
            batch_for_shader(shader,'TRI_FAN',{'pos':[(x,y),(x+w,y),(x+w,y+h),(x,y+h)]}).draw(shader)
        proxy=entry['proxy']
        if proxy:
            bounds=gate_bounds(self.scene,entry,region,rv)
            if not bounds:return
            left,bottom,w,h=bounds;right,top=left+w,bottom+h
            rect(0,0,region.width,max(0,bottom));rect(0,top,region.width,region.height-top)
            rect(0,bottom,max(0,left),h);rect(right,bottom,region.width-right,h)
        else:rect(0,0,region.width,region.height)
        label=(f"C{entry['slot']} · " + ('叶根为主', '中段为主', '叶尖为主')[entry['slot']-1]) if proxy else f"C{entry['slot']} · 未安装或未参与"
        scale=bpy.context.preferences.system.ui_scale
        from .custom_camera_preview import available_rectangle
        _, bottom, _, height = available_rectangle(bpy.context.area, region)
        title_top=bottom+height
        rect(0,title_top-28*scale,region.width,28*scale)
        font=0
        blf.size(font,15*scale);blf.color(font,.88,.94,1,1)
        blf.position(font,10*scale,title_top-21*scale,0);blf.draw(font,label)
        if entry is self.entries[0]:
            label_width,_=blf.dimensions(font,label)
            time_s=self.scene.get('wfrl_clearance_time_s')
            clock=f'共同 {time_s:.2f} s' if time_s is not None else f'共同帧 {self.scene.frame_current}'
            clock += ' · 播放' if self.window.screen.is_animation_playing else ' · 暂停'
            blf.size(font,11*scale);blf.color(font,.65,.73,.81,1)
            width,_=blf.dimensions(font,clock)
            available=max(1,region.width-label_width-30*scale)
            if width>available:
                blf.size(font,11*scale*available/width)
                width,_=blf.dimensions(font,clock)
            blf.position(font,region.width-width-10*scale,title_top-20*scale,0);blf.draw(font,clock)

    def playback_context(self):
        """Own user playback in a surviving source area, including its region."""
        windows=[w for w in bpy.context.window_manager.windows if w.scene==self.scene]
        source=next((w for w in windows if w.as_pointer()==self.source_window_id),None)
        if source is None:source=next((w for w in windows if w!=self.window),None)
        if source is None:source=next((w for w in windows if w==self.window),None)
        if source is None:return None
        area=next((a for a in source.screen.areas if a.type=='VIEW_3D'),None)
        if area is None:return None
        region=next((r for r in area.regions if r.type=='WINDOW'),None)
        return {'window':source,'area':area,'region':region}

    def lifecycle_event(self, event, **details):
        import time
        self.lifecycle.append({'event':event,'wall_s':time.monotonic(),'frame':self.scene.frame_current,
                               'windows':[(w.as_pointer(),w.screen.is_animation_playing,w.scene.frame_current)
                                          for w in bpy.context.window_manager.windows],**details})

    def close(self, preserve_playback=True):
        from . import custom_camera_capture
        job = custom_camera_capture._ACTIVE
        if job and job.scene == self.scene and job.window == self.window:
            job.finish(error='相机观察窗口已关闭', restore=preserve_playback)
        # is_animation_playing is global, not the timer's owner. Transfer an
        # observed live playback explicitly; never use stale state after an OS
        # close, which is indistinguishable from a user pausing immediately.
        # Product play controls already put the timer in the source window.
        alive=next((w for w in bpy.context.window_manager.windows if w==self.window),None)
        playing=bool(alive and alive.screen.is_animation_playing and preserve_playback)
        self.lifecycle_event('close_before', preserve_playback=preserve_playback, playing=playing)
        resume_context=self.playback_context() if playing else None
        if playing and resume_context:
            with bpy.context.temp_override(**resume_context):
                result=bpy.ops.screen.animation_cancel(restore_frame=False)
                self.lifecycle_event('cancel_before_close',result=list(result))
        if self.preview_quality is not None:
            self.set_quality(self.preview_quality)
            self.preview_quality = None
        self.clear_views()
        # Closed-window Area RNA may point at freed C memory. Never dereference
        # cached areas after the user closes the OS window.
        alive=next((w for w in bpy.context.window_manager.windows if w==self.window),None)
        self.diagram = None
        self.diagram_batches = None
        for window_id, area_id, shading_type in self.source_shading:
            source = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == window_id), None)
            if source:
                area = next((a for a in source.screen.areas if a.as_pointer() == area_id), None)
                if area and area.type == 'VIEW_3D':
                    area.spaces.active.shading.type = shading_type
        self.source_shading.clear()
        if alive and self.owns_window:
            with bpy.context.temp_override(window=alive):bpy.ops.wm.window_close()
        self.lifecycle_event('window_closed')
        if playing:
            # Window/timer destruction is finalized after this event. Reading
            # the global flag in this callback can still see the old timer.
            bpy.app.timers.register(self.resume_after_close, first_interval=.01)

    def resume_after_close(self):
        try:
            self.lifecycle_event('resume_event')
            resume_context=self.playback_context()
            if resume_context:
                with bpy.context.temp_override(**resume_context):
                    if not bpy.context.screen.is_animation_playing:
                        result=bpy.ops.screen.animation_play()
                        self.lifecycle_event('resume_result',result=list(result))
        except (ReferenceError,RuntimeError) as exc:
            self.lifecycle.append({'event':'resume_error','error':str(exc)})
        return None

    def draw_diagram(self):
        if bpy.context.window != self.window or bpy.context.area != self.diagram_area:return
        import gpu, blf
        from gpu_extras.batch import batch_for_shader
        region=bpy.context.region
        scale=min(bpy.context.preferences.system.ui_scale,region.width/400,region.height/230)
        shader=gpu.shader.from_builtin('UNIFORM_COLOR')
        shader.bind();shader.uniform_float('color',(.018,.025,.034,1.))
        batch_for_shader(shader,'TRI_FAN',{'pos':[(0,0),(region.width,0),(region.width,region.height),(0,region.height)]}).draw(shader)
        def text(value,x,y,size=13,color=(.78,.85,.92,1.)):
            blf.size(0,size*scale);blf.color(0,*color);blf.position(0,x,y,0);blf.draw(0,value)
        text('安装位置示意 · 静态',16*scale,region.height-28*scale,18)
        if self.diagram_stale or self.diagram is None:
            text('旧图已失效 · '+(self.diagram_error or '正在更新当前安装配置'),16*scale,region.height-57*scale,12,(1.,.66,.3,1.))
            return
        text('当前实际几何 · 非第四路实时画面',16*scale,region.height-49*scale,11)
        xgap=12*scale
        available=max(12.,region.width-3*xgap)
        panels=[(xgap,45*scale,available*.45,max(10.,region.height-122*scale)),
                (2*xgap+available*.45,45*scale,available*.55,max(10.,region.height-122*scale))]
        batch_key=(self.diagram_key,region.width,region.height,scale)
        if self.diagram_batches is None or self.diagram_batches[0] != batch_key:
            vertices,colors,transforms=[],[],[]
            for bounds,rect in zip((self.diagram['overview'],self.diagram['detail']),panels):
                factor,tx,ty=schematic.fit_transform(bounds,rect)
                transforms.append((factor,tx,ty))
                for _,triangle,color in self.diagram['triangles']:
                    points=schematic.clipped_polygon([(p[0]*factor+tx,p[1]*factor+ty) for p in triangle],rect)
                    for i in range(1,len(points)-1):
                        vertices.extend((points[0],points[i],points[i+1]));colors.extend((color,)*3)
            colored=gpu.shader.from_builtin('FLAT_COLOR')
            batch=batch_for_shader(colored,'TRIS',{'pos':vertices,'color':colors}) if vertices else None
            self.diagram_batches=(batch_key,colored,batch,transforms)
        _,colored,batch,transforms=self.diagram_batches
        if batch:
            colored.bind();batch.draw(colored)
        text('机舱与安装位置',panels[0][0],region.height-70*scale,11)
        text('盒体 / 支架局部',panels[1][0],region.height-70*scale,11)
        factor,tx,ty=transforms[1]
        for label,point in self.diagram['labels']:
            x,y=point[0]*factor+tx,point[1]*factor+ty
            if panels[1][0] <= x <= region.width-24*scale and 45*scale <= y <= region.height-80*scale:
                text(label,x+4*scale,y,10,(1.,.86,.5,1.))
        text('机舱局部坐标 · 安装调整后自动更新',16*scale,23*scale,10)


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


def shutdown(preserve_playback=True):
    global _ACTIVE
    if _ACTIVE:
        session,_ACTIVE=_ACTIVE,None
        session.close(preserve_playback=preserve_playback)


def watch():
    if _ACTIVE:
        try:
            if not any(w==_ACTIVE.window for w in bpy.context.window_manager.windows):
                shutdown()
            elif _ACTIVE.window.scene!=_ACTIVE.scene:
                shutdown(preserve_playback=False)
            else:_ACTIVE.sync()
        except (ReferenceError,RuntimeError,ValueError) as exc:
            print('Native camera view closed:',str(exc))
            shutdown()
    return .25


def header(self,context):
    if _ACTIVE and context.window==_ACTIVE.window:
        row=self.layout.row(align=True)
        entry=next((e for e in _ACTIVE.entries if e['area']==context.area),None)
        row.operator('wfrl.native_camera_play',text='暂停' if context.screen.is_animation_playing else '播放',icon='PAUSE' if context.screen.is_animation_playing else 'PLAY')
        step=row.row(align=True)
        step.enabled=WFRL_OT_NativePlay.poll(context)
        step.operator('wfrl.farm_transport',text='单步',icon='NEXT_KEYFRAME').action='STEP'
        if entry and len(_ACTIVE.slots)>1:
            op=row.operator('wfrl.native_camera_focus',text=f"放大 C{entry['slot']}",icon='FULLSCREEN_ENTER')
            op.slot=entry['slot']
        elif len(_ACTIVE.slots)==1:
            op=row.operator('wfrl.native_camera_view',text='返回三路',icon='FULLSCREEN_EXIT')
            op.mode='TRIPLE';op.layout=_ACTIVE.layout
        op=row.operator('wfrl.native_camera_view',text='2×2' if _ACTIVE.layout=='STRIP' else '三列')
        op.mode='TRIPLE';op.layout='GRID' if _ACTIVE.layout=='STRIP' else 'STRIP'
        if len(_ACTIVE.slots)>1:
            row.operator('wfrl.native_camera_quality',text='流畅' if _ACTIVE.fast else '高清')
        row.operator('wfrl.native_camera_exit',text='',icon='X')


class WFRL_OT_NativeCamera(bpy.types.Operator):
    bl_idname='wfrl.native_camera_view'
    bl_label='原生相机观察'
    mode:bpy.props.EnumProperty(items=[('WATCH','单路',''),('TRIPLE','三路','')])
    layout:bpy.props.EnumProperty(items=[('GRID','2×2 展示','三路相机与静态安装示意'),
                                         ('STRIP','三列对照','从叶根到叶尖横向比较')],default='GRID')
    @classmethod
    def poll(cls,context):
        return bool(context.window and context.area
                    and (context.area.type=='VIEW_3D' or (_ACTIVE and context.window==_ACTIVE.window))
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
        slots = list(core.SLOTS) if self.mode == 'TRIPLE' else [slot]
        if _ACTIVE and _ACTIVE.scene == context.scene:
            try:
                _ACTIVE.change_view(slots, self.layout if self.mode=='TRIPLE' else None)
                return {'FINISHED'}
            except (ValueError, RuntimeError) as exc:
                self.report({'ERROR'},str(exc));return {'CANCELLED'}
        shutdown()
        session=NativeSession(context, slots, self.layout)
        try:
            session.start();_ACTIVE=session
            if not bpy.app.timers.is_registered(watch):bpy.app.timers.register(watch,first_interval=.25)
            return {'FINISHED'}
        except Exception as exc:
            session.close();self.report({'ERROR'},str(exc));return {'CANCELLED'}


class WFRL_OT_NativeFocus(bpy.types.Operator):
    bl_idname='wfrl.native_camera_focus'
    bl_label='放大此路相机'
    bl_description='保留相机、共同时间与播放状态；返回后仍显示当前时刻'
    slot:bpy.props.IntProperty(min=1,max=3,default=1)
    @classmethod
    def poll(cls,context):
        from . import custom_camera_capture
        return bool(_ACTIVE and not _ACTIVE.preparing and not custom_camera_capture.active())
    def execute(self,context):
        try:
            _ACTIVE.change_view([self.slot])
            return {'FINISHED'}
        except (ValueError,RuntimeError) as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}


class WFRL_OT_NativePlay(bpy.types.Operator):
    bl_idname='wfrl.native_camera_play'
    bl_label='播放 / 暂停共同场景'
    bl_description='由主窗口维护共同回放时钟，关闭观察窗口后继续保持播放状态'
    @classmethod
    def poll(cls,context):
        from . import custom_camera_capture
        return bool(_ACTIVE and context.window==_ACTIVE.window and not custom_camera_capture.active())
    def execute(self,context):
        override=_ACTIVE.playback_context()
        if override is None:return {'CANCELLED'}
        with context.temp_override(**override):
            if context.screen.is_animation_playing:bpy.ops.screen.animation_cancel(restore_frame=False)
            else:bpy.ops.screen.animation_play()
        return {'FINISHED'}


class WFRL_OT_NativeQuality(bpy.types.Operator):
    bl_idname='wfrl.native_camera_quality'
    bl_label='切换预览画质'
    bl_description='三路切换流畅与高清；原图采集保持原始画质'
    @classmethod
    def poll(cls,context):
        return bool(_ACTIVE and len(_ACTIVE.slots)>1 and not _ACTIVE.preparing)
    def execute(self,context):
        if not self.poll(context):return {'CANCELLED'}
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
    bpy.types.IMAGE_HT_header.prepend(header)
    bpy.app.handlers.load_pre.append(on_load)
    config=bpy.context.window_manager.keyconfigs.addon
    if config:
        keymap=config.keymaps.new(name='Frames',space_type='EMPTY')
        item=keymap.keymap_items.new('wfrl.native_camera_play','SPACE','PRESS',head=True)
        _KEYMAPS.append((keymap,item))


@persistent
def on_load(_):shutdown(preserve_playback=False)


def unregister():
    shutdown(preserve_playback=False)
    if bpy.app.timers.is_registered(watch):bpy.app.timers.unregister(watch)
    bpy.types.VIEW3D_HT_header.remove(header)
    bpy.types.IMAGE_HT_header.remove(header)
    if on_load in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.remove(on_load)
    for keymap,item in _KEYMAPS:keymap.keymap_items.remove(item)
    _KEYMAPS.clear()


CLASSES=(WFRL_OT_NativeCamera,WFRL_OT_NativeFocus,WFRL_OT_NativePlay,WFRL_OT_NativeQuality,WFRL_OT_NativeExit)
