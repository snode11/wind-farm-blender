"""Observation window layout, preview quality and view switching."""


def start(self):
    bpy = self._native['bpy']
    before={w.as_pointer() for w in bpy.context.window_manager.windows}
    bpy.ops.wm.window_new()
    self.window=next(w for w in bpy.context.window_manager.windows if w.as_pointer() not in before)
    self.window_id = self.window.as_pointer()
    self.owns_window=True
    self.workspace=self.window.workspace
    # Only the calling source area uses the temporary shading budget.
    source = next((w for w in bpy.context.window_manager.windows
                   if w.as_pointer() == self.source_window_id), None)
    for area in source.screen.areas if source else ():
        if area.as_pointer() == self.source_area_id and area.type == 'VIEW_3D':
            shading = area.spaces.active.shading
            self.source_shading.append((self.source_window_id, area.as_pointer(), shading.type))
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
        self._native['_remember_error'](self.scene, f'相机观察准备失败：{exc}')
        self._native['shutdown']()
        return None


def _prepare(self):
    bpy = self._native['bpy']
    if self._native['_ACTIVE'] is not self or self.closing:return None
    if self.alive_window() is None:
        self._native['shutdown']()
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
    self._native['_remember_error'](self.scene, '')


def change_view(self, slots, layout=None):
    bpy = self._native['bpy']
    from . import custom_camera_capture
    if custom_camera_capture.active():raise ValueError('请先完成或取消原图采集')
    if self.closing:raise ValueError('相机窗口正在恢复，请稍后重开')
    if self.preparing:raise ValueError('请等待当前相机布局完成')
    layout = layout or self.layout
    if list(slots) == list(self.slots) and layout == self.layout:return
    errors = self.clear_views()
    if errors:
        self._native['_remember_error'](self.scene, '切换相机观察失败：' + '；'.join(errors))
        self._native['shutdown']()
        raise RuntimeError('；'.join(errors))
    self.slots, self.layout = list(slots), layout
    self.merge_first = True
    self.preparing = True
    self.preparation_steps = 0
    bpy.app.timers.register(self.prepare, first_interval=.01)


def first_paint(self):
    bpy = self._native['bpy']
    # Ensure the paused first frame is swapped after all regions exist.
    # On macOS, tagging inactive regions alone can leave partial titles
    # until the first mouse event. This one-shot repaint is not a loop.
    if self._native['_ACTIVE'] is self and not self.closing and self.alive_window() is not None:
        with bpy.context.temp_override(window=self.window):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    return None
