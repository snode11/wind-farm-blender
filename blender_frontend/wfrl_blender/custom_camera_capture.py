"""Explicit, cancellable capture transactions. No background threads or file saves.

A sequence evaluates the existing FarmFlex geometry without recording telemetry
or comparison trails. Each sample is drawn synchronously; event loop yields only
between complete groups. Files stay incomplete until the entire request succeeds.
"""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import hashlib
import json
import math
import struct
import uuid
import zlib
import time

from . import custom_camera_diagnostics as diagnostics

from . import camera_projection as projection, custom_cameras as core
from . import custom_camera_preview as preview

_ACTIVE = None


def active():
    return _ACTIVE is not None and not _ACTIVE.closed


def progress():
    if not active():
        return ''
    job = _ACTIVE
    total = len(job.times)
    text = f'已完成 {job.index}/{total} 组'
    if job.in_group:
        text += f'；正在生成第 {job.index+1}/{total} 组'
    elif job.index < total:
        text += f'；下一组 {job.index+1}/{total}'
    if job.index < total:
        text += '；目标 ' + preview.clock_text(job.times[job.index], job.frame, job.subframe)
    return text + ('；取消请求已接受，将在当前组完成后停止' if job.cancel_requested else '；Esc / 取消将在当前组完成后生效')


def write_json(path, payload):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temp.replace(path)


def write_png(path, width, height, rgba_bottom_up):
    """Display transform already applied by GPU. Only row reversal, no gamma pass."""
    if len(rgba_bottom_up) != width * height * 4:
        raise ValueError('GPU 像素长度与图像尺寸不一致')
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind+data) & 0xffffffff)
    stride = width * 4
    rows = b''.join(b'\0'+rgba_bottom_up[y*stride:(y+1)*stride] for y in range(height-1,-1,-1))
    payload = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B',width,height,8,6,0,0,0))
               + chunk(b'sRGB',b'\0') + chunk(b'IDAT',zlib.compress(rows,6)) + chunk(b'IEND',b''))
    with Path(path).open('xb') as stream:
        stream.write(payload)


def _matrix(matrix):
    return [[float(value) for value in row] for row in matrix]


def current_time(scene):
    from . import clearance_replay
    value = clearance_replay.sample(scene)
    return float(value['time_s']) if value else None


def preflight(context, times=None):
    """One read-only check used by summary, directory entry and actual capture."""
    from .panels.custom_cameras import installation_block_reason
    from . import farm_flex
    scene = context.scene
    errors = []
    if active():
        errors.append('已有图像采集事务')
    if any(obj.get('wfrl_custom_draft') for obj in scene.objects):
        errors.append('请先确认或取消相机草稿')
    reason = installation_block_reason(scene)
    if reason:
        errors.append(reason)
    if scene.display_settings.display_device != 'sRGB':
        errors.append('PNG 输出要求 sRGB 显示设备')
    cameras = core.enabled_cameras(scene)
    if not cameras:
        errors.append('没有参与相机；请安装并勾选参与四路预览与采集')
    sizes = {}
    max_size = None
    try:
        import bpy
        if not bpy.app.background:
            import gpu
            max_size = gpu.capabilities.max_texture_size_get()
    except (ImportError, RuntimeError):
        pass  # Allocation and capability checks remain mandatory at draw time.
    try:
        core._root(scene)
        # Recheck installation constraints, including stale mount geometry.
        core.validate_layout(scene, core.layout_dict(scene))
        for cam in cameras:
            p = core.validate_parameters(core.parameters(cam))
            sizes[int(cam['wfrl_custom_slot'])] = projection.resolution(p.fov, p.vfov, p.output_long_edge_px, max_size)
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    farm = farm_flex._ACTIVE if farm_flex.is_active(scene) else None
    bounds = (float(farm.times[0]), float(farm.times[-1])) if farm else None
    if times is not None:
        if not times or len(times) > projection.MAX_SAMPLES:
            errors.append('无效采样清单或超过采样数量上限')
        if bounds is None:
            errors.append('时间序列需要正式 FarmFlex 回放；静态场景可导出当前时刻')
        elif any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t)
                 or t < bounds[0] or t > bounds[1] for t in times):
            errors.append(f'序列时间必须位于 {bounds[0]}–{bounds[1]} s；请重新设置范围')
    return {'errors': errors, 'sizes': sizes, 'bounds': bounds,
            'samples': 1 if times is None else len(times), 'cameras': len(cameras),
            'pixels_per_group': sum(w*h for w,h in sizes.values()), 'gpu_texture_limit': max_size}


def require_preflight(context, times=None):
    result = preflight(context, times)
    if result['errors']:
        raise ValueError('；'.join(result['errors']))
    return result


@contextmanager
def readonly_frame_handlers():
    """Suspend only this add-on's timeline observers during explicit evaluation.

    Third-party Blender callbacks remain registered. Their mutations are checked
    by the capture guard. Restoration happens in finally before any event yield.
    """
    import bpy
    saved = []
    for handlers in (bpy.app.handlers.frame_change_pre, bpy.app.handlers.frame_change_post):
        owned = [(i, handler) for i,handler in enumerate(handlers)
                 if getattr(handler,'__module__','').startswith(__package__+'.')]
        for _i,handler in owned:
            handlers.remove(handler)
        saved.append((handlers,owned))
    try:
        yield
    finally:
        for handlers,owned in saved:
            for i,handler in owned:
                if handler not in handlers:
                    handlers.insert(min(i,len(handlers)),handler)


class Capture:
    def __init__(self, context, directory, times=None):
        global _ACTIVE
        import bpy
        from .panels.custom_cameras import session_identity, installation_block_reason, _ACTIVE as controller
        from . import farm_flex, clearance_replay
        require_preflight(context, times)
        if controller and controller.stage not in {'WATCH','LAYOUT'}:
            raise ValueError('请先确认或取消相机草稿')
        reason = installation_block_reason(context.scene)
        if reason:
            raise ValueError(reason)
        directory = Path(directory).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError('请选择已有输出目录；采集将在其中创建唯一子目录')
        self.scene, self.window, self.area = context.scene, context.window, context.area
        self.identity = session_identity(self.scene)
        self.cameras = core.enabled_cameras(self.scene)
        if not self.cameras:
            raise ValueError('请先安装并启用至少一个自定义相机')
        for camera in self.cameras:
            core.validate_parameters(core.parameters(camera))
            core._root(self.scene)
        self.layout = core.layout_dict(self.scene)
        self.layout_hash = core.layout_hash(self.layout)
        self.frame, self.subframe = self.scene.frame_current, self.scene.frame_subframe
        self.time = current_time(self.scene)
        self.farm = farm_flex._ACTIVE if farm_flex.is_active(self.scene) else None
        self.reader = self.farm.readers['T1'] if self.farm else clearance_replay.reader_for(self.scene)
        self.times = [self.time] if times is None else list(times)
        self.sequence = times is not None
        if not self.times:
            raise ValueError('时间清单为空')
        if self.sequence:
            if self.farm is None or self.reader is None:
                raise ValueError('时间序列需要已加载的正式 FarmFlex 回放；静止场景可导出当前帧')
            if any(not isinstance(t,(int,float)) or not math.isfinite(t)
                   or t < self.farm.times[0] or t > self.farm.times[-1] for t in self.times):
                raise ValueError(f'序列时间必须位于 {self.farm.times[0]}–{self.farm.times[-1]} s')
        self.playing = bool(self.window.screen.is_animation_playing)
        self.closed, self.index, self.error = False, 0, ''
        self.in_group, self.cancel_requested = False, False
        self.cancel_accepted_at = None
        self.cancel_stopped_at = None
        self.capture_id = uuid.uuid4().hex
        self.path = directory / f'capture_{datetime.now():%Y%m%d_%H%M%S}_{self.capture_id[:10]}'
        self.original_time_property = self.scene.get('wfrl_clearance_time_s')
        self.profile = preview.render_profile(self.scene)
        self.expected_frame = (self.frame,self.subframe)
        self.source = {'source_package_id': str(self.farm.path) if self.farm and hasattr(self.farm,'path') else self.scene.get('wfrl_farm_flex_path'),
            'manifest_hash': self.scene.get('wfrl_farm_manifest_sha256'),
            'geometry_signature': self.layout['model_signature'],
            'source_status': self.scene.get('wfrl_fidelity', 'missing: static scene'),
            'replay_session_id': ':'.join(map(str,self.identity))}
        self.manifest = {'schema_version':2, 'capture_id':self.capture_id, 'status':'incomplete',
            'requested_times_s':self.times, 'completed_samples':0,
            'active_camera_ids':[f"C{cam['wfrl_custom_slot']}" for cam in self.cameras],
            'camera_layout_hash':self.layout_hash, 'render_profile':self.profile, **self.source,
            'matrix_convention':'row-major; column vectors; metres; T_destination_from_source',
            'pixel_convention':'top-left pixel centre (0,0); boundaries -.5 and W/H-.5',
            'distortion':'ideal undistorted pinhole; not calibrated physical lens'}
        self._play(False)
        try:
            self.path.mkdir()
            write_json(self.path/'layout.json',self.layout)
            write_json(self.path/'manifest.json',self.manifest)
        except Exception:
            self._play(self.playing)
            raise
        _ACTIVE = self

    def _play(self, playing):
        import bpy
        if bool(self.window.screen.is_animation_playing) != playing:
            with bpy.context.temp_override(window=self.window,screen=self.window.screen):
                if playing:bpy.ops.screen.animation_play()
                else:bpy.ops.screen.animation_cancel(restore_frame=False)

    def same_session(self):
        from .panels.custom_cameras import session_identity
        try:
            import bpy
            return (self.window in list(bpy.context.window_manager.windows)
                and self.window.scene == self.scene and self.area in list(self.window.screen.areas)
                and self.area.type == 'VIEW_3D' and session_identity(self.scene) == self.identity)
        except (ReferenceError,RuntimeError):
            return False

    def guard(self):
        from .panels.custom_cameras import installation_block_reason
        if not self.same_session():
            raise RuntimeError('场景、窗口或回放会话已变化，采集未完成')
        reason=installation_block_reason(self.scene)
        if reason:raise RuntimeError(reason)
        if self.window.screen.is_animation_playing or (self.scene.frame_current,self.scene.frame_subframe) != self.expected_frame:
            raise RuntimeError('采集时刻被外部操作改变，采集未完成')
        if core.layout_hash(core.layout_dict(self.scene)) != self.layout_hash:
            raise RuntimeError('采集期间相机布局已变化，采集未完成')
        if preview.render_profile(self.scene) != self.profile:
            raise RuntimeError('采集期间颜色设置已变化，采集未完成')

    def evaluate(self, frame, subframe, sim_time):
        if self.farm is None:
            return
        import bpy
        with readonly_frame_handlers():
            self.scene.frame_set(frame,subframe=subframe)
            self.farm.update(self.scene,sim_time_s=sim_time,record_telemetry=False,update_comparison=False)
            bpy.context.view_layer.update()
        self.expected_frame=(self.scene.frame_current,self.scene.frame_subframe)

    def request_cancel(self):
        if self.closed or self.cancel_requested:
            return
        self.cancel_requested = True
        self.cancel_accepted_at = time.perf_counter()
        diagnostics.record('cancel_accepted', completed=self.index, in_group=self.in_group)
        if not self.in_group:
            self.finish(error='用户取消：已停止')

    def step(self, context):
        if self.closed:
            return True
        if self.cancel_requested:
            self.finish(error='用户取消：已停止')
            return True
        self.in_group = True
        try:
            return self._step_group(context)
        finally:
            self.in_group = False
            if self.cancel_requested and not self.closed:
                self.finish(error='用户取消：已停止')

    def _step_group(self, context):
        self.guard()
        at=self.times[self.index]
        if self.sequence:
            timebase=float(self.scene.get('wfrl_clearance_timebase_fps',60.))
            mapped=self.scene.frame_start+(at-self.reader.start_s)*timebase
            self.evaluate(math.floor(mapped),mapped-math.floor(mapped),at)
        self.guard()
        images=preview.render_group(context,self.cameras)
        try:
            self.guard()
            records=[]
            for camera in self.cameras:
                slot=int(camera['wfrl_custom_slot']);image=images[slot];p=image.params
                folder=self.path/f'C{slot}';folder.mkdir(exist_ok=True)
                relative=f'C{slot}/frame_{self.index:06d}.png'
                write_png(self.path/relative,image.width,image.height,image.rgba())
                scales=projection.pixel_scales(p.fov,p.vfov,image.width,image.height)
                motion=None
                if self.reader is not None and at is not None:
                    motion=self.reader.at(at).get('motion')
                record={'capture_id':self.capture_id,'sample_index':self.index,'time_s':at,
                    'time_missing_reason':None if at is not None else 'static scene; no simulation clock',
                    'scene_frame':self.scene.frame_current,'scene_subframe':self.scene.frame_subframe,
                    **self.source,'camera_id':f'C{slot}','active_camera_ids':self.manifest['active_camera_ids'],
                    'camera_layout_hash':self.layout_hash,
                    'simulation_state_hash':image.state_hash,
                    'nacelle_pose':_matrix(self.scene.objects[core.ROOT_NAME].matrix_world),
                    'turbine_yaw':motion.get('yaw_deg') if motion else None,
                    'rotor_azimuth':motion.get('azimuth_deg') if motion else None,
                    'blade_pitch':motion.get('pitch_deg') if motion else None,
                    'T_world_from_camera_blender':_matrix(image.world),
                    'T_camera_blender_from_world':_matrix(image.view),
                    'T_camera_cv_from_world':projection.cv_from_world(image.view),
                    'K':projection.intrinsics(p.fov,p.vfov,image.width,image.height),
                    'P':_matrix(image.projection),'image_width':image.width,'image_height':image.height,
                    'projection_scale_x':scales[0],'projection_scale_y':scales[1],
                    'hfov_requested':p.fov,'vfov_requested':p.vfov,
                    'hfov_effective':math.degrees(2*math.atan(1/image.projection[0][0])),
                    'vfov_effective':math.degrees(2*math.atan(1/image.projection[1][1])),
                    'render_profile':self.profile,'color_management':self.profile['color_management'],
                    'image_encoding':self.profile['image_encoding'],
                    'image_relative_path':relative,'capture_status':'complete'}
                records.append(record)
            self.guard()
            with (self.path/'frames.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(''.join(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n' for r in records))
            self.index+=1
            self.manifest['completed_samples']=self.index
            write_json(self.path/'manifest.json',self.manifest)
        finally:
            for image in images.values():image.free()
        if self.index==len(self.times) and not self.cancel_requested:
            self.finish(success=True)
            return True
        return False

    def finish(self,success=False,error='',restore=True):
        global _ACTIVE
        if self.closed:return
        same=self.same_session()
        try:
            if restore and same:
                self._play(False)
                if self.sequence:
                    self.evaluate(self.frame,self.subframe,self.time)
                if self.original_time_property is None:
                    if 'wfrl_clearance_time_s' in self.scene:del self.scene['wfrl_clearance_time_s']
                else:self.scene['wfrl_clearance_time_s']=self.original_time_property
                self._play(self.playing)
        except Exception as exc:
            success=False;error=f'{error} 恢复失败：{exc}'
        finally:
            self.closed=True
            if self.cancel_requested:
                self.cancel_stopped_at = time.perf_counter()
                diagnostics.record('cancel_stopped', completed=self.index)
                self.manifest['cancel_accepted_at'] = self.cancel_accepted_at
                self.manifest['cancel_stopped_at'] = self.cancel_stopped_at
                self.manifest['cancel_accept_to_stop_s'] = self.cancel_stopped_at - self.cancel_accepted_at
            if getattr(self,'ui_operator',None):
                self.ui_operator.remove_timer()
            if _ACTIVE is self:_ACTIVE=None
            self.error=error
            self.manifest['status']='complete' if success and same else 'incomplete'
            self.manifest['error']=error or (None if success else 'cancelled / invalid session')
            write_json(self.path/'manifest.json',self.manifest)


def shutdown(restore=True):
    if active():_ACTIVE.finish(error='场景重载 / 功能关闭',restore=restore)
