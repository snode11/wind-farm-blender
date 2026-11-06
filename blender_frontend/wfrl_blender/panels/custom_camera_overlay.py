"""Custom-camera overlay state, redraw invalidation and viewport drawing."""


def _display_state(op):
    """Immutable values used by the overlay; no mesh or image readback."""
    from .. import custom_camera_capture
    cameras = [op.camera]
    if op.stage not in {'AIM', 'WATCH'}:
        cameras = [_panel.core.get_camera(op.scene, slot) for slot in _panel.core.SLOTS]
        if op.stage != 'LAYOUT':
            cameras[op.slot - 1] = op.camera if op.ready else None
    camera_state = tuple((cam.as_pointer(), _panel.core.parameters(cam),
                          tuple(v for row in cam.matrix_world for v in row),
                          cam.get('custom_label', ''), cam.get('custom_enabled', True),
                          _panel.core.anchor(cam)) for cam in cameras if cam is not None)
    parent = op.scene.objects.get(_panel.core.ROOT_NAME)
    parent_state = (tuple(v for row in parent.matrix_world for v in row)
                    if parent is not None and op.stage not in {'AIM', 'WATCH'} else None)
    request = op.preview.request or {}
    system = _panel.bpy.context.preferences.system
    return (op.stage, op.slot, op.ready, op.error, op.drag, op.stick,
            op.hover, tuple(op.candidates), camera_state, parent_state,
            op.scene.frame_current, op.scene.frame_subframe,
            bool(op.window.screen.is_animation_playing), custom_camera_capture.active(),
            tuple(request.get(key) for key in ('time_s', 'frame', 'subframe')),
            op.preview.version, bool(op.preview.images), op.preview.status(),
            tuple((r.type, r.x, r.y, r.width, r.height) for r in op.area.regions),
            system.ui_scale, system.use_region_overlap,
            tuple(getattr(op.wm, 'wfrl_custom_' + key) for key in
                  ('engineering', 'show_axis', 'show_frustum')))


def _redraw_if_changed(op):
    if op.closed:
        return False
    state = _panel._display_state(op)
    if state == getattr(op, '_redraw_state', None):
        return False
    op._redraw_state = state
    op.area.tag_redraw()
    _panel.diagnostics.record('overlay_redraw', slot=op.slot, stage=op.stage,
                       version=op.preview.version)
    return True


def draw_overlay(self):
    if self.closed or _panel.bpy.context.area != self.area or _panel.preview._DRAWING:
        return
    from .. import custom_camera_capture
    if custom_camera_capture.active():
        return
    if self.stage in {'AIM', 'WATCH'}:
        cams = [self.camera]
        hint = self.state_text() if self.stage == 'AIM' else ''
        self.preview.draw(_panel.bpy.context, cams, slot=self.slot, hint=hint,
                          closable=self.stage != 'AIM')
        if self.stage in _panel._VIEWING:
            return
    _panel.preview.draw_controls(_panel.bpy.context, self.state_text(), editing=self.stage not in _panel._VIEWING,
        confirmable=self.ready and self.stage == 'AIM', reason=self.error or (
            '请先确定位置并进入方向阶段' if self.stage != 'AIM' else ''),
        external=self.stage not in {'AIM', 'WATCH'})
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    def lines(points, color):
        batch = batch_for_shader(shader, 'LINES', {'pos': points})
        shader.bind()
        shader.uniform_float('color', color)
        batch.draw(shader)
    def circle(x,y,r,color):
        points = [(x+r*_panel.math.cos(i*_panel.math.tau/48), y+r*_panel.math.sin(i*_panel.math.tau/48)) for i in range(49)]
        lines([p for a,b in zip(points,points[1:]) for p in (a,b)], color)
    def label(x,y,text,color=(1,1,1,1)):
        blf.size(0,13 * max(1., _panel.bpy.context.preferences.system.ui_scale))
        blf.color(0,*color)
        blf.position(0,x,y,0)
        blf.draw(0,text)
    def project(local):
        parent = self.scene.objects.get('WFRL.Turbine.T1.YawRoot')
        return location_3d_to_region_2d(self.region,self.area.spaces.active.region_3d,parent.matrix_world @ _panel.Vector(local))
    gpu.state.blend_set('ALPHA')
    try:
        if self.stage == 'AIM':
            cx,cy,r = self.geometry()
            circle(cx,cy,r,(.2,.8,1,1))
            circle(cx+self.stick[0]*r*.7,cy+self.stick[1]*r*.7,12,(1,.6,.1,1))
            # Direction stick stays in the reserved action strip.
        else:
            origin = project((0,0,0))
            if origin:
                label(*origin,'T1.YawRoot 原点')
                for axis,text,color in [((3,0,0),'+X 尾部',(1,.3,.3,1)),((0,3,0),'+Y 右侧',(.3,1,.3,1)),((0,0,3),'+Z 上方',(.3,.6,1,1))]:
                    end = project(axis)
                    if end:
                        lines([origin,end],color)
                        label(*end,text,color)
            anchors = list(self.candidates)
            if self.hover:
                anchors.append(self.hover)
            anchor = _panel.core.anchor(self.camera) if self.camera else None
            if anchor and self.ready:
                anchors.append(anchor)
            for i,a in enumerate(anchors):
                p = project(a.point)
                end = project(_panel.Vector(a.point)+_panel.Vector(a.normal)*.8)
                if p:
                    circle(*p,7,(1,.6,.1,1))
                    if end: lines([p,end],(1,.6,.1,1))
                    label(p.x+10,p.y+10, '锚点 '+str(i+1) if self.candidates else '安装落点')
            if self.ready and self.camera:
                p = project(_panel.core.parameters(self.camera).location)
                if p:
                    circle(*p,4,(.2,1,1,1))
                    label(p.x+8,p.y-18,'镜头中心')
        if self.stage in {'LAYOUT', 'PLACE', 'INPUT'}:
            cameras = [_panel.core.get_camera(self.scene, slot) for slot in _panel.core.SLOTS]
            if self.stage != 'LAYOUT':
                cameras[self.slot-1] = self.camera if self.ready else None
            for cam in filter(None, cameras):
                matrix = cam.matrix_world
                p = _panel.core.parameters(cam)
                selected = int(cam['wfrl_custom_slot']) == self.slot
                color = (.3,.85,1,1) if selected else (.8,.66,.35,.8)
                def world_project(xyz):
                    return location_3d_to_region_2d(self.region, self.area.spaces.active.region_3d, matrix @ _panel.Vector(xyz))
                center = world_project((0,0,0))
                if center:
                    circle(*center,8 if selected else 5,color)
                    label(center.x+12,center.y+12,f'C{cam["wfrl_custom_slot"]}',color)
                if self.wm.wfrl_custom_show_axis:
                    end = world_project((0,0,-8))
                    if center and end: lines([center,end],color)
                if self.wm.wfrl_custom_show_frustum and (self.stage == 'LAYOUT' or selected):
                    corners = [world_project(v) for v in _panel.projection.frustum_corners(p.fov,p.vfov,8)]
                    for a,b in zip(corners,corners[1:]+corners[:1]):
                        if a and b: lines([a,b],color)
                    for end in corners:
                        if center and end: lines([center,end],(*color[:3],.4))
        hint = {'AIM':f'C{self.slot} · 调整方向：拖动 / 摇杆；Shift 细调；Esc 取消',
                'PLACE':f'C{self.slot} · 为此相机单击机舱选点；中键旋转；Esc 取消',
                'INPUT':f'C{self.slot} · 在 View 面板输入此相机的位置；Esc 取消',
                'LAYOUT':f'当前选择 C{self.slot} · 在 View 面板单独设置位置；Esc 退出'}
        if self.stage != 'AIM':
            label(20,35,hint[self.stage])
    except (ReferenceError, RuntimeError):
        pass
    finally:
        gpu.state.blend_set('NONE')


# Runtime callbacks share the public namespace; definitions are available first
# so importing either this module or the public entry point is supported.
from . import custom_cameras as _panel
