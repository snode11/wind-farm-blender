"""Calibrated gates, viewport snapshots and owned observation camera proxies."""


def _view_state(space):
    rv = space.region_3d
    return {name: getattr(rv, name).copy() if hasattr(getattr(rv, name), 'copy')
            else getattr(rv, name) for name in (
                'view_perspective', 'view_rotation', 'view_location',
                'view_distance', 'view_camera_zoom', 'view_camera_offset')}


def _restore_view(space, state):
    for name, value in state.items():
        setattr(space.region_3d, name, value)


def fit_gate(hfov, vfov, scene_aspect, *, math):
    from . import camera_projection as projection
    projection.validate(hfov, vfov)
    projection.finite(scene_aspect)
    if scene_aspect <= 0:
        raise ValueError('场景画幅比例须为正')
    x, y = math.tan(math.radians(hfov / 2)), math.tan(math.radians(vfov / 2))
    outer_x = max(x, y * scene_aspect)
    fx, fy = x / outer_x, y / (outer_x / scene_aspect)
    projection.finite(outer_x, fx, fy)
    if min(fx, fy) <= 0:
        raise ValueError('场景画幅比例超出可表示的投影范围')
    return outer_x, fx, fy


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


def frame_gate(scene, entry, region, space, *, bpy, math, gate_bounds):
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
    rv.view_camera_offset=(0,0)
    rv.view_camera_zoom=0
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


def release_proxy(self, entry):
    bpy = self._native['bpy']
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


def sync(self):
    bpy = self._native['bpy']
    core = self._native['core']
    schematic = self._native['schematic']
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
        camera_view = space.region_3d.view_perspective == 'CAMERA'
        camera = core.get_camera(self.scene,slot)
        # Participation controls grouped views; a disabled camera can still
        # be inspected individually before the user enables it again.
        if len(self.slots)>1 and camera and not camera.get('custom_enabled',True):camera=None
        params = core.parameters(camera) if camera else None
        # COPY_TRANSFORMS follows pose changes without replacing the proxy.
        # Only optical changes require a new lens and calibrated view fit.
        key = (camera.as_pointer(), params.fov, params.vfov,
               params.clip_near_m, params.clip_far_m, aspect) if camera else None
        if key != entry['key'] or (camera and entry['proxy'] is None):
            if entry['proxy']:
                errors = self.release_proxy(entry)
                if errors:
                    raise RuntimeError('；'.join(errors))
            if camera:
                outer,fx,fy=self._native['fit_gate'](params.fov,params.vfov,aspect)
                data=bpy.data.cameras.new(f'T1.Native.C{slot}')
                data[self._native['_PROXY_OWNER']] = self.proxy_owner
                entry['proxy_data'] = data
                data.sensor_fit='HORIZONTAL'
                # Keep lens in Blender's valid range even at extreme FOV.
                data.lens=min(5000.,max(1.,18./outer))
                data.sensor_width=2*data.lens*outer
                if abs(data.sensor_width-2*data.lens*outer)>1e-4:
                    bpy.data.cameras.remove(data)
                    raise ValueError('当前视场超出原生相机范围，请使用精确预览')
                data.clip_start=params.clip_near_m
                data.clip_end=params.clip_far_m
                data.show_passepartout=False
                data.passepartout_alpha=0.
                proxy=bpy.data.objects.new(f'T1.Native.C{slot}',data)
                proxy[self._native['_PROXY_OWNER']] = self.proxy_owner
                entry['proxy'] = proxy
                self.scene.collection.objects.link(proxy)
                proxy.hide_render=True
                constraint=proxy.constraints.new('COPY_TRANSFORMS')
                constraint.target=camera
                entry.update(proxy=proxy,proxy_data=data,fx=fx,fy=fy)
            entry['key']=key
        region=next((r for r in area.regions if r.type=='WINDOW'),None)
        view_key=(key, region.width, region.height) if region else None
        if entry['proxy'] and (not space.use_local_camera or space.camera != entry['proxy']):
            space.use_local_camera=True
            space.camera=entry['proxy']
            # Removing an active local camera can switch Blender to PERSP.
            if camera_view:
                space.region_3d.view_perspective='CAMERA'
        if entry['proxy'] and view_key != entry['view_key']:
            initial = entry['view_key'] is None
            if initial:
                space.region_3d.view_perspective='CAMERA'
            region=next((r for r in area.regions if r.type=='WINDOW'),None)
            if region and space.region_3d.view_perspective == 'CAMERA':
                with bpy.context.temp_override(window=self.window,area=area,region=region):
                    bpy.context.view_layer.update()
                    self._native['frame_gate'](self.scene,entry,region,space)
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
