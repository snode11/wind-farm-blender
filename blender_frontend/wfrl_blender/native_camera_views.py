"""Native camera viewing in an independent window; no offscreen preview loop.

Shared scene aspect is expanded to contain each calibrated rectangle. A mask
shows only that rectangle. Proxy cameras follow originals without modifying them.
"""
import math
import uuid
from contextlib import contextmanager
import bpy
from bpy.app.handlers import persistent
from . import custom_cameras as core, camera_projection as projection
from . import installation_schematic as schematic

_ACTIVE = None
_KEYMAPS = []
_ERROR_PROPERTY = 'wfrl_native_camera_error'
_PROXY_OWNER = 'wfrl_native_camera_owner'
_HISTORY_QUALITY = None


def owns_view(window, area):
    """Reserve every area of the observation window, including layout setup."""
    session = _ACTIVE
    return bool(session is not None and session.owns_window
                and window.as_pointer() == session.window_id)


def _view_state(space):
    rv = space.region_3d
    return {name: getattr(rv, name).copy() if hasattr(getattr(rv, name), 'copy')
            else getattr(rv, name) for name in (
                'view_perspective', 'view_rotation', 'view_location',
                'view_distance', 'view_camera_zoom', 'view_camera_offset')}


def _restore_view(space, state):
    for name, value in state.items():
        setattr(space.region_3d, name, value)


def last_error(scene):
    """Keep asynchronous failures visible after their observing window closes."""
    try:
        return scene.get(_ERROR_PROPERTY, '')
    except ReferenceError:
        return ''


def _remember_error(scene, message, *, append=False):
    try:
        previous = last_error(scene)
        if append and previous:
            message = previous if message in previous else previous + '；' + message
        scene[_ERROR_PROPERTY] = message
        for window in bpy.context.window_manager.windows:
            if window.scene == scene:
                for area in window.screen.areas:
                    area.tag_redraw()
    except ReferenceError:
        # The owning scene may itself have been removed or replaced.
        pass


def _cleanup_step(errors, label, action):
    """A failed release must not prevent the remaining resources restoring."""
    try:
        result = action()
        if isinstance(result, list):
            errors.extend(result)
    except Exception as exc:
        errors.append(f'{label}：{exc}')


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
        self.window_id = self.source_window_id
        self.closing = False
        self.closed = False
        self.resume_when_closed = False
        self.resume_scheduled = False
        self.resume_generation = None
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
        self.proxy_owner = uuid.uuid4().hex
        self.save_state = None
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
        self.window_id = self.window.as_pointer()
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
            _remember_error(self.scene, f'相机观察准备失败：{exc}')
            shutdown()
            return None

    def _prepare(self):
        if _ACTIVE is not self or self.closing:return None
        if self.alive_window() is None:
            shutdown()
            return None
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
        _remember_error(self.scene, '')

    def alive_window(self):
        # Never dereference a cached Window RNA after its OS window was closed.
        return next((w for w in bpy.context.window_manager.windows
                     if w.as_pointer() == self.window_id), None)

    def release_proxy(self, entry):
        errors = []
        proxy, data = entry.get('proxy'), entry.get('proxy_data')
        if proxy is not None:
            if data is None:
                try:
                    data = proxy.data
                except ReferenceError:
                    entry['proxy'] = None
            entry['proxy_data'] = data
            if entry.get('proxy') is not None:
                try:
                    bpy.data.objects.remove(proxy, do_unlink=True)
                    entry['proxy'] = None
                except ReferenceError:
                    entry['proxy'] = None
                except Exception as exc:
                    errors.append(f"清理 C{entry['slot']} 观察相机：{exc}")
        if entry.get('proxy') is None and data is not None:
            try:
                if not getattr(data, 'users', 0):
                    bpy.data.cameras.remove(data)
                entry['proxy_data'] = None
            except ReferenceError:
                entry['proxy_data'] = None
            except Exception as exc:
                errors.append(f"清理 C{entry['slot']} 观察镜头：{exc}")
        return errors

    def clear_views(self):
        errors = []
        for name, owner in (('handle', bpy.types.SpaceView3D),
                            ('diagram_handle', bpy.types.SpaceImageEditor)):
            handle = getattr(self, name)
            if handle is None:
                continue
            try:
                owner.draw_handler_remove(handle, 'WINDOW')
                setattr(self, name, None)
            except (ReferenceError, ValueError):
                setattr(self, name, None)
            except Exception as exc:
                errors.append(f'移除相机绘制回调：{exc}')
        def detach_cameras():
            alive = self.alive_window()
            if alive and self.owns_window:
                for area in alive.screen.areas:
                    if area.type == 'VIEW_3D':
                        _cleanup_step(errors, '解除观察相机绑定',
                                      lambda area=area: setattr(area.spaces.active, 'camera', None))
        _cleanup_step(errors, '解除观察相机绑定', detach_cameras)
        for entry in self.entries:
            _cleanup_step(errors, '清理观察相机', lambda entry=entry: self.release_proxy(entry))
        # Retain only unreleased resources so a later shutdown can retry them.
        self.entries[:] = [e for e in self.entries
                           if e.get('proxy') is not None or e.get('proxy_data') is not None]
        self.diagram_area = None
        return errors

    def change_view(self, slots, layout=None):
        from . import custom_camera_capture
        if custom_camera_capture.active():raise ValueError('请先完成或取消原图采集')
        if self.closing:raise ValueError('相机窗口正在恢复，请稍后重开')
        if self.preparing:raise ValueError('请等待当前相机布局完成')
        layout = layout or self.layout
        if list(slots) == list(self.slots) and layout == self.layout:return
        errors = self.clear_views()
        if errors:
            _remember_error(self.scene, '切换相机观察失败：' + '；'.join(errors))
            shutdown()
            raise RuntimeError('；'.join(errors))
        self.slots, self.layout = list(slots), layout
        self.merge_first = True
        self.preparing = True
        self.preparation_steps = 0
        bpy.app.timers.register(self.prepare, first_interval=.01)

    def first_paint(self):
        # Ensure the paused first frame is swapped after all regions exist.
        # On macOS, tagging inactive regions alone can leave partial titles
        # until the first mouse event. This one-shot repaint is not a loop.
        if _ACTIVE is self and not self.closing and self.alive_window() is not None:
            with bpy.context.temp_override(window=self.window):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        return None

    def sync(self):
        if self.save_state is not None:
            return
        render = self.scene.render
        aspect = render.resolution_x*render.pixel_aspect_x/(render.resolution_y*render.pixel_aspect_y)
        for entry in self.entries:
            if entry['proxy'] is not None:
                # Deleted RNA still has a truth value, but accessing it raises.
                # Let watch close cleanly instead of leaving failing draw hooks.
                data = entry['proxy'].data
                if data is None or entry['proxy'].type != 'CAMERA':
                    raise ValueError(f"C{entry['slot']} 观察相机或镜头已被删除")
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
                    errors = self.release_proxy(entry)
                    if errors:
                        raise RuntimeError('；'.join(errors))
                if camera:
                    params=core.parameters(camera)
                    outer,fx,fy=fit_gate(params.fov,params.vfov,aspect)
                    data=bpy.data.cameras.new(f'T1.Native.C{slot}')
                    data[_PROXY_OWNER] = self.proxy_owner
                    entry['proxy_data'] = data
                    data.sensor_fit='HORIZONTAL'
                    # Keep lens in Blender's valid range even at extreme FOV.
                    data.lens=min(5000.,max(1.,18./outer));data.sensor_width=2*data.lens*outer
                    if abs(data.sensor_width-2*data.lens*outer)>1e-4:
                        bpy.data.cameras.remove(data)
                        raise ValueError('当前视场超出原生相机范围，请使用精确预览')
                    data.clip_start=params.clip_near_m;data.clip_end=params.clip_far_m
                    data.passepartout_alpha=1.
                    proxy=bpy.data.objects.new(f'T1.Native.C{slot}',data)
                    proxy[_PROXY_OWNER] = self.proxy_owner
                    entry['proxy'] = proxy
                    self.scene.collection.objects.link(proxy)
                    proxy.hide_render=True
                    constraint=proxy.constraints.new('COPY_TRANSFORMS');constraint.target=camera
                    entry.update(proxy=proxy,proxy_data=data,fx=fx,fy=fy)
                entry['key']=key
            region=next((r for r in area.regions if r.type=='WINDOW'),None)
            view_key=(key, region.width, region.height) if region else None
            if entry['proxy'] and (not space.use_local_camera or space.camera != entry['proxy']):
                space.use_local_camera=True;space.camera=entry['proxy']
            if entry['proxy'] and view_key != entry['view_key']:
                initial = entry['view_key'] is None
                if initial:
                    space.region_3d.view_perspective='CAMERA'
                region=next((r for r in area.regions if r.type=='WINDOW'),None)
                if region and space.region_3d.view_perspective == 'CAMERA':
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
                # Invalidate before sampling so the old diagram is not shown
                # as the current camera layout.
            elif self.diagram_stale and not self.diagram_error:
                try:
                    self.diagram = schematic.capture(self.scene)
                    self.diagram_stale = False
                except (ValueError, RuntimeError, KeyError) as exc:
                    self.diagram_error = str(exc)
                self.diagram_area.tag_redraw()

    def suspend_for_save(self):
        """Serialize ordinary cameras and original quality, retaining live views."""
        if self.save_state is not None or self.closing:
            return
        state = {'quality': self.quality_settings(), 'shading': [], 'views': []}
        self.save_state = state
        if self.preview_quality is not None:
            self.set_quality(self.preview_quality)
        for window_id, area_id, original in self.source_shading:
            window = next((w for w in bpy.context.window_manager.windows
                           if w.as_pointer() == window_id), None)
            area = next((a for a in window.screen.areas if a.as_pointer() == area_id), None) if window else None
            if area and area.type == 'VIEW_3D':
                shader = area.spaces.active.shading
                state['shading'].append((shader, shader.type))
                shader.type = original
        errors = []
        for entry in self.entries:
            space = entry['area'].spaces.active
            if space.type == 'VIEW_3D':
                state['views'].append((entry, _view_state(space)))
                space.camera = core.get_camera(self.scene, entry['slot'])
                space.use_local_camera = True
            errors.extend(self.release_proxy(entry))
            entry['key'] = entry['view_key'] = None
        if errors:
            raise RuntimeError('；'.join(errors))

    def resume_after_save(self):
        """Restore the same live inspection after either save result."""
        state, self.save_state = self.save_state, None
        if state is None:
            return
        errors = []
        _cleanup_step(errors, '恢复观察画质', lambda: self.set_quality(state['quality']))
        for shader, value in state['shading']:
            _cleanup_step(errors, '恢复主视口', lambda shader=shader, value=value: setattr(shader, 'type', value))
        _cleanup_step(errors, '恢复观察相机', self.sync)
        for entry, view in state['views']:
            _cleanup_step(errors, '恢复观察视角', lambda entry=entry, view=view:
                          _restore_view(entry['area'].spaces.active, view))
        if errors:
            raise RuntimeError('；'.join(errors))

    def draw(self):
        if bpy.context.window != self.window or bpy.context.workspace != self.workspace:return
        entry=next((e for e in self.entries if e['area']==bpy.context.area),None)
        if not entry:return
        from . import performance, playback
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
            clock += ' · 播放' if playback.is_playing(self.scene) else ' · 暂停'
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
        if self.closed:
            return True
        errors = []
        first_close = not self.closing
        self.closing = True
        if not preserve_playback:
            self.resume_when_closed = False

        def finish_capture():
            from . import custom_camera_capture
            job = custom_camera_capture._ACTIVE
            if job and job.scene == self.scene and job.window == self.window:
                job.finish(error='相机观察窗口已关闭', restore=preserve_playback)

        def transfer_playback():
            if not first_close:
                return
            from . import playback
            # is_animation_playing is global, not the timer's owner. Transfer
            # only playback owned by this scene before its window is destroyed.
            alive = self.alive_window()
            playing = bool(alive and preserve_playback and playback.is_playing(self.scene))
            self.resume_when_closed = playing
            self.lifecycle_event('close_before', preserve_playback=preserve_playback, playing=playing)
            resume_context = self.playback_context() if playing else None
            if resume_context:
                with bpy.context.temp_override(**resume_context):
                    result = bpy.ops.screen.animation_cancel(restore_frame=False)
                    self.lifecycle_event('cancel_before_close', result=list(result))
                    self.resume_generation = playback.generation()

        try:
            _cleanup_step(errors, '结束相机采集', finish_capture)
            _cleanup_step(errors, '转移共同播放', transfer_playback)
            _cleanup_step(errors, '清理相机视图', self.clear_views)
        finally:
            try:
                _cleanup_step(errors, '恢复预览画质', self.restore_quality)
                _cleanup_step(errors, '恢复主视口画质', self.restore_source_shading)
            finally:
                _cleanup_step(errors, '关闭观察窗口', self.close_window)
                self.preparing = False
                self.diagram = None
                self.diagram_batches = None
        if self.resume_when_closed and not self.owns_window and not self.resume_scheduled:
            # Window/timer destruction is finalized after this event.
            def schedule_resume():
                bpy.app.timers.register(self.resume_after_close, first_interval=.01)
                self.resume_scheduled = True
            _cleanup_step(errors, '恢复共同播放', schedule_resume)
        self.closed = not (self.entries or self.handle is not None or self.diagram_handle is not None
                           or self.preview_quality is not None or self.source_shading or self.owns_window
                           or (self.resume_when_closed and not self.resume_scheduled))
        if errors:
            _remember_error(self.scene, '相机观察恢复未完成：' + '；'.join(errors), append=True)
        return self.closed

    def restore_quality(self):
        if self.preview_quality is not None:
            try:
                self.set_quality(self.preview_quality)
            except ReferenceError:
                # A removed scene has no remaining preview settings to restore.
                self.preview_quality = None
                return
            self.preview_quality = None

    def restore_source_shading(self):
        pending, errors = [], []
        for window_id, area_id, shading_type in self.source_shading:
            try:
                source = next((w for w in bpy.context.window_manager.windows
                               if w.as_pointer() == window_id), None)
                if source:
                    area = next((a for a in source.screen.areas if a.as_pointer() == area_id), None)
                    if area and area.type == 'VIEW_3D':
                        area.spaces.active.shading.type = shading_type
            except ReferenceError:
                pass
            except Exception as exc:
                pending.append((window_id, area_id, shading_type))
                errors.append(f'恢复主视口画质：{exc}')
        self.source_shading[:] = pending
        return errors

    def close_window(self):
        alive = self.alive_window()
        if alive and self.owns_window:
            with bpy.context.temp_override(window=alive):
                if bpy.ops.wm.window_close() != {'FINISHED'}:
                    raise RuntimeError('观察窗口关闭操作未完成')
        self.owns_window = False
        self.lifecycle_event('window_closed')

    def resume_after_close(self):
        try:
            from . import playback
            self.lifecycle_event('resume_event')
            # Another scene may have started during this delayed restoration.
            # An unknown live owner is protected by the global flag as well.
            owner = playback.owner_scene()
            if (owner is not None and owner != self.scene) or playback.any_playing():
                self.lifecycle_event('resume_skipped', reason='another_or_live_player')
                return None
            if playback.generation() != self.resume_generation:
                self.lifecycle_event('resume_skipped', reason='playback_state_changed')
                return None
            resume_context=self.playback_context()
            if resume_context:
                with bpy.context.temp_override(**resume_context):
                    result=bpy.ops.screen.animation_play()
                    self.lifecycle_event('resume_result',result=list(result))
        except Exception as exc:
            self.lifecycle.append({'event':'resume_error','error':str(exc)})
            _remember_error(self.scene, f'恢复共同播放失败：{exc}', append=True)
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
        session = _ACTIVE
        # Keep ownership until every retained resource is released. A failed
        # restoration can then be retried without resuming a half-closed view.
        if session.close(preserve_playback=preserve_playback) and _ACTIVE is session:
            _ACTIVE = None


def watch():
    if _ACTIVE:
        try:
            if _ACTIVE.closing or _ACTIVE.alive_window() is None:
                shutdown()
            elif _ACTIVE.window.scene!=_ACTIVE.scene:
                shutdown(preserve_playback=False)
            else:_ACTIVE.sync()
        except Exception as exc:
            print('Native camera view closed:',str(exc))
            _remember_error(_ACTIVE.scene, f'相机观察中断：{exc}')
            shutdown()
    return .25


def _playback_control_state(scene):
    """Protect another or unknown owner even when global screen flags disagree."""
    from . import playback
    owner = playback.owner_scene()
    if owner is not None and owner != scene:
        return False, f'播放器由场景“{owner.name}”占用，请先在对应窗口暂停'
    playing = playback.any_playing()
    if playing and owner is None:
        return False, '无法确认当前播放器归属，请先在播放窗口暂停'
    return playing, ''


def header(self,context):
    if _ACTIVE and not _ACTIVE.closing and context.window==_ACTIVE.window:
        row=self.layout.row(align=True)
        entry=next((e for e in _ACTIVE.entries if e['area']==context.area),None)
        playing, blocked = _playback_control_state(_ACTIVE.scene)
        controls_enabled = WFRL_OT_NativePlay.poll(context) and not blocked
        play = row.row(align=True)
        play.enabled = controls_enabled
        play.operator('wfrl.native_camera_play',text='播放器占用中' if blocked else '暂停' if playing else '播放',icon='PAUSE' if playing else 'PLAY')
        step=row.row(align=True)
        step.enabled=controls_enabled
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
        if _ACTIVE and _ACTIVE.closing:
            shutdown()
        if _ACTIVE and _ACTIVE.scene == context.scene:
            try:
                _ACTIVE.change_view(slots, self.layout if self.mode=='TRIPLE' else None)
                return {'FINISHED'}
            except (ValueError, RuntimeError) as exc:
                _remember_error(context.scene, f'切换相机观察失败：{exc}')
                self.report({'ERROR'},str(exc));return {'CANCELLED'}
        shutdown()
        if _ACTIVE:
            self.report({'ERROR'}, last_error(context.scene) or '相机窗口正在恢复，请稍后重开')
            return {'CANCELLED'}
        session = None
        try:
            session=NativeSession(context, slots, self.layout)
            _ACTIVE=session
            session.start()
            if not bpy.app.timers.is_registered(watch):bpy.app.timers.register(watch,first_interval=.25)
            return {'FINISHED'}
        except Exception as exc:
            _remember_error(context.scene, f'打开相机观察失败：{exc}')
            if session is not None:
                shutdown()
            self.report({'ERROR'},str(exc));return {'CANCELLED'}


class WFRL_OT_NativeFocus(bpy.types.Operator):
    bl_idname='wfrl.native_camera_focus'
    bl_label='放大此路相机'
    bl_description='保留相机、共同时间与播放状态；返回后仍显示当前时刻'
    slot:bpy.props.IntProperty(min=1,max=3,default=1)
    @classmethod
    def poll(cls,context):
        from . import custom_camera_capture
        return bool(_ACTIVE and context.scene == _ACTIVE.scene and not _ACTIVE.closing and not _ACTIVE.preparing and not custom_camera_capture.active())
    def execute(self,context):
        if not self.poll(context):return {'CANCELLED'}
        try:
            _ACTIVE.change_view([self.slot])
            return {'FINISHED'}
        except (ValueError,RuntimeError) as exc:
            _remember_error(context.scene, f'切换相机观察失败：{exc}')
            self.report({'ERROR'},str(exc));return {'CANCELLED'}


class WFRL_OT_NativePlay(bpy.types.Operator):
    bl_idname='wfrl.native_camera_play'
    bl_label='播放 / 暂停共同场景'
    bl_description='由主窗口维护共同回放时钟，关闭观察窗口后继续保持播放状态'
    @classmethod
    def poll(cls,context):
        from . import custom_camera_capture
        # Keep Space dispatch here so an occupied player cannot fall through
        # to Blender's global playback shortcut. The header disables its button.
        return bool(_ACTIVE and not _ACTIVE.closing and context.window==_ACTIVE.window and not custom_camera_capture.active())
    def execute(self,context):
        if not WFRL_OT_NativePlay.poll(context):return {'CANCELLED'}
        session = _ACTIVE
        override=session.playback_context()
        if override is None:return {'CANCELLED'}
        with context.temp_override(**override):
            playing, blocked = _playback_control_state(session.scene)
            if blocked:
                self.report({'WARNING'}, blocked)
                return {'CANCELLED'}
            if playing:
                bpy.ops.screen.animation_cancel(restore_frame=False)
            else:
                from . import clearance_replay
                if clearance_replay.reader_for(session.scene) is not None:
                    return bpy.ops.wfrl.clearance_playback()
                bpy.ops.screen.animation_play()
        return {'FINISHED'}


class WFRL_OT_NativeQuality(bpy.types.Operator):
    bl_idname='wfrl.native_camera_quality'
    bl_label='切换预览画质'
    bl_description='三路切换流畅与高清；原图采集保持原始画质'
    @classmethod
    def poll(cls,context):
        return bool(_ACTIVE and context.scene == _ACTIVE.scene and not _ACTIVE.closing and len(_ACTIVE.slots)>1 and not _ACTIVE.preparing)
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
    bpy.app.handlers.load_post.append(on_loaded)
    bpy.app.handlers.save_pre.append(on_save)
    bpy.app.handlers.save_post.append(on_saved)
    bpy.app.handlers.save_post_fail.append(on_saved)
    config=bpy.context.window_manager.keyconfigs.addon
    if config:
        keymap=config.keymaps.new(name='Frames',space_type='EMPTY')
        item=keymap.keymap_items.new('wfrl.native_camera_play','SPACE','PRESS',head=True)
        _KEYMAPS.append((keymap,item))


@persistent
def on_load(_):shutdown(preserve_playback=False)


@persistent
def on_loaded(_):
    """Discard only explicitly owned observation resources from loaded files."""
    for obj in list(bpy.data.objects):
        owner = obj.get(_PROXY_OWNER)
        if owner and obj.type == 'CAMERA' and obj.data.get(_PROXY_OWNER) == owner:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data.users == 0:
                bpy.data.cameras.remove(data)
    for data in list(bpy.data.cameras):
        if data.get(_PROXY_OWNER) and data.users == 0:
            bpy.data.cameras.remove(data)


def on_history_pre(_):
    """End transient observation before Blender replaces scene RNA."""
    global _HISTORY_QUALITY
    _HISTORY_QUALITY = None
    if _ACTIVE is not None:
        try:
            _HISTORY_QUALITY = (_ACTIVE.scene.session_uid, _ACTIVE.preview_quality)
        except ReferenceError:
            pass
        shutdown(preserve_playback=False)


def on_history_post(_):
    """Restore quality only to the same scene identity in the undo result."""
    global _HISTORY_QUALITY
    snapshot, _HISTORY_QUALITY = _HISTORY_QUALITY, None
    on_loaded(None)
    if snapshot is not None and snapshot[1] is not None:
        scene = next((s for s in bpy.data.scenes if s.session_uid == snapshot[0]), None)
        if scene is not None:
            pixel_size, samples, shadows, gi = snapshot[1]
            scene.render.preview_pixel_size = pixel_size
            scene.eevee.taa_samples = samples
            scene.eevee.use_shadows = shadows
            scene.eevee.use_fast_gi = gi


@persistent
def on_save(_):
    if _ACTIVE:
        try:
            _ACTIVE.suspend_for_save()
        except Exception as exc:
            _remember_error(_ACTIVE.scene, f'相机观察保存准备失败：{exc}')
            # Blender continues writing after handler exceptions. End the
            # failed observation so ordinary cleanup can retry every resource.
            shutdown()


@persistent
def on_saved(_):
    if _ACTIVE:
        try:
            _ACTIVE.resume_after_save()
        except Exception as exc:
            _remember_error(_ACTIVE.scene, f'相机观察保存恢复失败：{exc}')
            shutdown()


def unregister():
    shutdown(preserve_playback=False)
    if bpy.app.timers.is_registered(watch):bpy.app.timers.unregister(watch)
    bpy.types.VIEW3D_HT_header.remove(header)
    bpy.types.IMAGE_HT_header.remove(header)
    if on_load in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.remove(on_load)
    for handlers, callback in ((bpy.app.handlers.load_post, on_loaded),
                               (bpy.app.handlers.save_pre, on_save),
                               (bpy.app.handlers.save_post, on_saved),
                               (bpy.app.handlers.save_post_fail, on_saved)):
        if callback in handlers:
            handlers.remove(callback)
    for keymap,item in _KEYMAPS:keymap.keymap_items.remove(item)
    _KEYMAPS.clear()


CLASSES=(WFRL_OT_NativeCamera,WFRL_OT_NativeFocus,WFRL_OT_NativePlay,WFRL_OT_NativeQuality,WFRL_OT_NativeExit)
