"""NREL defect actions, viewport picking and modal visibility scans."""
import math
import logging
import time
from copy import deepcopy
import bpy

_LOG = logging.getLogger(__name__)


def _restore_properties(target, values, error):
    for key, value in values.items():
        try:
            setattr(target, key, value)
        except Exception as exc:
            error.add_note(f'Editor property rollback failed for {key}: {exc}')
            _LOG.exception('Defect editor property rollback failed')


def action_unavailable(s, p, action):
    """One state contract for disabled controls, tooltips and operator guards."""
    if action == 'OPEN':
        return ''
    if s is None:
        return '请先启动编辑器'
    if getattr(s, 'closing', False):
        return '' if action == 'CLOSE' else '关闭未完成，请重试关闭编辑器'
    if s.busy:
        return '扫描进行中；按 Esc 取消后操作'
    if action == 'CLOSE':
        return ''
    if s.healthy_payload is not None:
        if action == 'HEALTHY':
            return ''
        if action not in ('CLOSEUP', 'OBLIQUE', 'CAMERA1', 'CAMERA2', 'CAMERA3'):
            return '当前是健康对照，请先返回缺陷效果'
    if s.draft is not None:
        return '' if action in ('PREVIEW', 'CONFIRM', 'CANCEL', 'CLOSEUP', 'OBLIQUE',
                               'CAMERA1', 'CAMERA2', 'CAMERA3') else '请先应用或取消当前编辑'
    if action in ('PREVIEW', 'CONFIRM', 'CANCEL'):
        return '请先新建或编辑缺陷'
    if action in ('EDIT', 'COPY', 'DELETE', 'TOGGLE', 'HEALTHY', 'CLOSEUP', 'OBLIQUE'):
        if not 0 <= p.selected < len(s.store.document['defects']):
            return '请先从列表选择一个缺陷'
    if action == 'UNDO' and not s.store.can_undo:
        return '没有可撤销的操作'
    if action == 'REDO' and not s.store.can_redo:
        return '没有可重做的操作'
    return ''


ACTION_MESSAGES = {
    'RETRY_CLEANUP': '旧缺陷资源已释放',
    'OPEN': '编辑器已打开；选择类型并新建，或选择已有缺陷',
    'CLOSE': '编辑器已关闭',
    'NEW': '正在新建；调整参数后预览，再应用修改',
    'EDIT': '正在编辑；调整参数后预览，再应用修改',
    'PREVIEW': '已更新预览；点击“应用修改”后生效',
    'CONFIRM': '修改已应用到场景；可在“配置文件”中保存 JSON',
    'CANCEL': '已取消编辑，恢复编辑前的缺陷状态',
    'COPY': '已复制并选中新缺陷；副本默认停用',
    'DELETE': '缺陷已删除；可撤销',
    'TOGGLE': '已更新缺陷的启用状态',
    'UNDO': '已撤销上一步缺陷修改',
    'REDO': '已重做缺陷修改',
    'SAVE': 'JSON 配置已保存；未渲染图片',
    'LOAD': 'JSON 配置已加载；可撤销',
    'TIME': '已定位到指定时刻',
    'CLOSEUP': '已定位到缺陷正面；可自由缩放和旋转视角',
    'OBLIQUE': '已定位到缺陷斜侧；可自由缩放和旋转视角',
    'ANALYZE': '当前帧几何分析已完成；不代表图片中可辨识',
    'EXPORT_G': '几何分析资料已导出；未渲染图片',
    'EXPORT_A': '三相机图片已导出；外观仍需人工审核',
}


ACTION_HELP = {
    'NEW': '按当前类型和机组创建草稿；应用前不会更改已保存的缺陷状态',
    'EDIT': '编辑列表中高亮的缺陷',
    'PREVIEW': '用当前参数更新三维预览；仍可取消并恢复原状态',
    'CONFIRM': '将当前参数应用到场景；导出 JSON 请使用“保存配置”',
    'CANCEL': '丢弃本次编辑和预览，恢复编辑前的状态',
    'COPY': '复制选中缺陷并默认停用，避免与原缺陷重叠生效',
    'HEALTHY': '临时关闭目标叶片的全部缺陷，再点一次返回；不改写配置',
    'CLOSEUP': '按当前输入参数定位正面近景；不会自动更新缺陷预览，健康对照时也可使用',
    'OBLIQUE': '按当前输入参数定位斜侧近景；不会自动更新缺陷预览，健康对照时也可使用',
    'SAVE': '将已应用的缺陷保存为 JSON；不会渲染图片',
    'LOAD': '从 JSON 替换当前缺陷配置；可撤销本次加载',
    'ANALYZE': '计算固定三相机的理想投影和几何遮挡；不判断图片辨识能力',
    'EXPORT_G': '在新的空目录中保存当前帧几何分析资料，不渲染图片',
    'EXPORT_A': '在新的空目录中渲染三相机缺陷与健康对照 PNG，可能需要较长时间',
    'CLOSE': '退出编辑器并恢复健康显示；未应用的草稿会被丢弃，已应用配置保留在场景中',
}


class NREL_OT_defect_action(bpy.types.Operator):
    bl_idname = 'wfrl.nrel_defect_action'
    bl_label = '叶片缺陷操作'
    action: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        from . import editor as _editor
        p = context.scene.wfrl_nrel_defects
        return (_editor.action_unavailable(_editor.session(context), p, properties.action)
                or _editor.ACTION_HELP.get(properties.action, _editor.ACTION_MESSAGES.get(properties.action, '叶片缺陷操作')))

    def execute(self, context):
        from . import editor as _editor
        s, p = _editor.session(context), context.scene.wfrl_nrel_defects
        try:
            reason = _editor.action_unavailable(s, p, self.action)
            if reason:
                raise ValueError(reason)
            if self.action == 'OPEN':
                _editor.ensure(context.scene)
                p.status = _editor.ACTION_MESSAGES['OPEN']
                return {'FINISHED'}
            if self.action == 'CLOSE':
                _editor.detach(context.scene)
                context.scene['wfrl_nrel_defect_enabled']=False
                p.status = _editor.ACTION_MESSAGES['CLOSE']
                return {'FINISHED'}
            if self.action == 'RETRY_CLEANUP':
                s.store.retry_disposals(strict=True)
            elif self.action in ('NEW', 'EDIT'):
                before = {key: getattr(p, key) for key in (
                    'selected', 'kind', 'turbine_id', 'blade_id', 'side', 'span', 'u', 'theta',
                    'length', 'width', 'depth', 'seed', 'enabled', 'edge_roughness', 'crater_fraction')}
                try:
                    d = s.begin(None if self.action == 'NEW' else p.selected, p.kind)
                    p.selected = s.draft_index
                    _editor.props_from_defect(p, d)
                except BaseException as error:
                    if s.draft is not None:
                        try:
                            s.finish(False)
                        except Exception as exc:
                            error.add_note(f'Edit rollback failed: {exc}')
                            _LOG.exception('Defect edit rollback failed')
                    _restore_properties(p, before, error)
                    raise
            elif self.action in ('PREVIEW', 'CONFIRM'):
                draft, before = s.draft, deepcopy(s.draft)
                try:
                    _editor.draft_from_props(s, p)
                    s.preview_draft() if self.action == 'PREVIEW' else s.finish(True)
                except BaseException:
                    if s.draft is draft:
                        draft.clear()
                        draft.update(before)
                    raise
            elif self.action == 'CANCEL':
                s.finish(False)
            elif self.action in ('COPY', 'DELETE', 'TOGGLE'):
                s.mutate(self.action, p.selected)
                if self.action == 'COPY':
                    p.selected = len(p.defect_items)-1
            elif self.action in ('UNDO', 'REDO'):
                if s.draft is not None:
                    raise ValueError('请先取消编辑')
                getattr(s.store, self.action.lower())()
            elif self.action == 'SAVE':
                s.save(_editor.bpy.path.abspath(p.config_path))
            elif self.action == 'LOAD':
                s.load(_editor.bpy.path.abspath(p.config_path))
            elif self.action == 'TIME':
                if s.draft is not None:
                    raise ValueError('编辑期间时刻已冻结')
                s.set_time(p.time_s)
            elif self.action == 'HEALTHY':
                s.healthy(p.selected)
            elif self.action in ('CLOSEUP', 'OBLIQUE'):
                s.closeup(s.draft_index if s.draft is not None else p.selected, self.action == 'OBLIQUE')
            elif self.action.startswith('CAMERA'):
                camera = s.camera(int(self.action[-1]))
                previous_camera = s.scene.camera
                views = [(area.spaces.active, area.spaces.active.overlay.show_overlays,
                          area.spaces.active.region_3d.view_perspective)
                         for area in (context.screen.areas if context.screen else ())
                         if area.type == 'VIEW_3D']
                try:
                    s.scene.camera = camera
                    for space, _overlays, _perspective in views:
                        space.overlay.show_overlays = False
                        space.region_3d.view_perspective = 'CAMERA'
                except BaseException as error:
                    for target, key, value in [(s.scene, 'camera', previous_camera)] + [
                            entry for space, overlays, perspective in views for entry in (
                                (space.overlay, 'show_overlays', overlays),
                                (space.region_3d, 'view_perspective', perspective))]:
                        try:
                            setattr(target, key, value)
                        except Exception as exc:
                            error.add_note(f'Camera view rollback failed: {exc}')
                            _LOG.exception('Defect camera view rollback failed')
                    raise
            elif self.action == 'ANALYZE':
                if s.draft is not None:
                    raise ValueError('请先确认或取消编辑')
                s.records = s.analyze()
                s.scene['defect_analysis_status'] = 'EVALUATED'
            elif self.action == 'EXPORT_G':
                from .defect_outputs import create_run, save_analysis
                run = create_run(s, _editor.bpy.path.abspath(p.output_path), mode='G')
                save_analysis(s, run)
            elif self.action == 'EXPORT_A':
                from .defect_outputs import capture_images
                capture_images(s,_editor.bpy.path.abspath(p.output_path))
            if self.action == 'HEALTHY':
                p.status = '正在查看目标叶片的健康状态；点击“返回缺陷效果”继续编辑' if s.healthy_payload is not None else '已返回缺陷效果'
            elif self.action.startswith('CAMERA'):
                p.status = f'已切换到 C{self.action[-1]} 相机视角'
            else:
                p.status = _editor.ACTION_MESSAGES[self.action]
            return {'FINISHED'}
        except Exception as exc:
            p.status = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            try:
                _editor.redraw_editor(context)
            except (ReferenceError, RuntimeError):
                _LOG.warning('Defect action viewport redraw failed', exc_info=True)


class NREL_OT_defect_pick(bpy.types.Operator):
    bl_idname = 'wfrl.nrel_defect_pick'
    bl_label = '点击健康参考表面'

    _NAVIGATION_EVENTS = frozenset({
        'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
        'WHEELINMOUSE', 'WHEELOUTMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM',
        'MOUSEROTATE', 'MOUSESMARTZOOM', 'NDOF_MOTION',
    })

    @classmethod
    def poll(cls, context):
        from . import editor as _editor
        try:
            s = _editor.session(context)
            return (s is not None and s.draft is not None and not s.busy
                    and not getattr(s, 'closing', False) and s.healthy_payload is None
                    and context.area is not None and context.area.type == 'VIEW_3D')
        except (AttributeError, ReferenceError):
            return False

    def invoke(self, context, event):
        from . import editor as _editor
        s = _editor.session(context)
        if not self.poll(context):
            self.report({'ERROR'}, '先新建或编辑缺陷，再在三维视口中选择位置')
            return {'CANCELLED'}
        self.pick_session, self.pick_draft = s, s.draft
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set('左键选择健康参考表面；中键或滚轮调整视图；Esc 或右键取消选点')
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self.pick_session = self.pick_draft = None
        try:
            context.workspace.status_text_set(None)
        except (AttributeError, ReferenceError):
            _LOG.warning('Defect picker workspace is no longer available', exc_info=True)

    def modal(self, context, event):
        from . import editor as _editor
        from .. import viewport
        s = _editor.session(context)
        if (event.type in {'ESC', 'RIGHTMOUSE'} or not self.poll(context)
                or s is not self.pick_session or s.draft is not self.pick_draft):
            self.cancel(context)
            return {'CANCELLED'}
        # Press, release and hover outside the invoking view's drawing region
        # belong to the sidebar, header or another area; only its drawing
        # region picks.
        if event.type in self._NAVIGATION_EVENTS or not viewport.in_window_region(context.area, event):
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            from bpy_extras import view3d_utils
            region = next(r for r in context.area.regions if r.type == 'WINDOW')
            coord = (event.mouse_x - region.x, event.mouse_y - region.y)
            r3d = context.space_data.region_3d
            origin = view3d_utils.region_2d_to_origin_3d(region, r3d, coord)
            direction = view3d_utils.region_2d_to_vector_3d(region, r3d, coord)
            try:
                p = context.scene.wfrl_nrel_defects
                a = s.locate_ray(origin, direction, p.blade_id, p.turbine_id)
            except Exception as exc:
                self.report({'WARNING'}, str(exc))
                return {'RUNNING_MODAL'}
            before = {key: getattr(p, key) for key in ('span', 'u', 'side')}
            try:
                p.span, p.u, p.side = a['s_m'], a['u'], a['surface_side']
                p.status = '位置已选定；点击“更新预览”检查效果'
            except Exception as exc:
                _restore_properties(p, before, exc)
                self.report({'ERROR'}, str(exc))
                self.cancel(context)
                return {'CANCELLED'}
            self.cancel(context)
            try:
                _editor.redraw_editor(context)
            except (ReferenceError, RuntimeError):
                _LOG.warning('Defect picker viewport redraw failed', exc_info=True)
            return {'FINISHED'}
        return {'RUNNING_MODAL'}


class NREL_OT_defect_status(bpy.types.Operator):
    bl_idname = 'wfrl.nrel_defect_status'
    bl_label = '完整状态提示'
    bl_options = {'INTERNAL'}

    message: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return context.scene.wfrl_nrel_defects.status

    def invoke(self, context, event):
        self.message = context.scene.wfrl_nrel_defects.status
        return context.window_manager.invoke_popup(self, width=500)

    def draw(self, context):
        from . import editor as _editor
        self.layout.label(text='完整状态提示', icon='INFO')
        for line in _editor.wrapped_lines(self.message, 64):
            self.layout.label(text=line)

    def execute(self, context):
        return {'FINISHED'}


class NREL_OT_defect_scan(bpy.types.Operator):
    bl_idname = 'wfrl.nrel_defect_scan'
    bl_label = '扫描固定三相机'

    @classmethod
    def poll(cls, context):
        from . import editor as _editor
        try:
            return (context.window is not None and context.window_manager is not None
                    and context.view_layer is not None and not _editor.action_unavailable(
                        _editor.session(context), context.scene.wfrl_nrel_defects, 'SCAN'))
        except (AttributeError, ReferenceError):
            return False

    def invoke(self, context, event):
        from . import editor as _editor
        from .defect_timeline import FrameMapping
        from .defect_outputs import create_run
        from .. import playback
        self.scan_session = None
        self.timer = self.run = None
        self.finished = False
        self.finish_result = {'CANCELLED'}
        self.playing = False
        s, p = _editor.session(context), context.scene.wfrl_nrel_defects
        try:
            reason = _editor.action_unavailable(s, p, 'SCAN')
            if reason:
                raise ValueError(reason)
            if not self.poll(context):
                raise ValueError('扫描需要有效的编辑器窗口和视图层')
            # RNA FloatProperty stores float32; recover an intended integral
            # frame count before strict timing validation (e.g. 2/60 seconds).
            duration = float(p.scan_duration)
            frames = duration*60.
            nearest = round(frames)
            if math.isclose(frames,nearest,rel_tol=1e-7,abs_tol=1e-7):
                duration = nearest/60.
            self.mapping = FrameMapping(p.scan_start,duration,60.,60.,float(s.flex.times[0]))
            if p.scan_start < s.flex.times[0] or p.scan_start+duration > s.flex.times[-1]+1e-9:
                raise ValueError('扫描超出时间线')
            self.scan_session, self.scan_scene = s, s.scene
            self.scene_key = s.scene.as_pointer()
            self.scan_window, self.scan_wm = context.window, context.window_manager
            self.scan_view_layer = context.view_layer
            self.saved_time = float(s.scene['defect_time_s'])
            self.rows, self.index, self.version = [], 0, s.store.version
            self.run = create_run(s, _editor.bpy.path.abspath(p.output_path), mapping=self.mapping)
            s.freeze_time = self.saved_time
            if playback.is_playing(s.scene):
                playback.cancel(s.scene)
                if playback.is_playing(s.scene):
                    raise RuntimeError('无法暂停当前场景播放')
                self.playing = True
            self.started = time.perf_counter()
            s.busy = True
            s.scan_operator = self
            self.timer = self.scan_wm.event_timer_add(.05, window=self.scan_window)
            self.scan_wm.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            if self.run is not None:
                return self.finish_scan(context, True, repr(exc))
            return {'CANCELLED'}

    def bound_session_valid(self):
        from . import editor as _editor
        try:
            return (_editor._SESSIONS.get(self.scene_key) is self.scan_session
                    and self.scan_scene.as_pointer() == self.scene_key)
        except (AttributeError, ReferenceError):
            return False

    def finish_scan(self, context, cancelled=False, error=None, *, restore_time=True, resume_playback=True):
        from . import editor as _editor
        from .defect_outputs import save_records, write_json, update_run, save_review, _staged_run
        from .defect_timeline import summarize_intervals
        from .. import playback
        if self.finished:
            return self.finish_result
        self.finished = True
        s = self.scan_session
        s.busy = False
        if getattr(s, 'scan_operator', None) is self:
            s.scan_operator = None
        timer, self.timer = self.timer, None
        if timer is not None:
            try:
                self.scan_wm.event_timer_remove(timer)
            except (ReferenceError, RuntimeError):
                _LOG.warning('Defect scan timer could not be removed', exc_info=True)
        valid = self.bound_session_valid()
        restored = False
        if restore_time and valid:
            try:
                # Restore the originating scene without borrowing the new scene's depsgraph.
                with _editor.bpy.context.temp_override(scene=self.scan_scene, view_layer=self.scan_view_layer):
                    s.set_time(self.saved_time)
                restored = True
            except Exception as exc:
                error = '; '.join(filter(None, (error, 'RESTORE_FAILED: '+repr(exc))))
        if resume_playback and self.playing and restored:
            try:
                def can_resume():
                    owner = playback.owner_scene()
                    return (self.bound_session_valid() and self.scan_window in tuple(self.scan_wm.windows)
                            and self.scan_window.scene == self.scan_scene
                            and (owner is None or owner == self.scan_scene)
                            and not playback.any_playing())
                if can_resume():
                    with _editor.bpy.context.temp_override(window=self.scan_window):
                        if can_resume():
                            _editor.bpy.ops.screen.animation_play()
            except (ReferenceError, RuntimeError):
                _LOG.warning('Defect scan playback could not resume', exc_info=True)
        complete = not cancelled and error is None and self.index == self.mapping.frame_count
        try:
            with _staged_run(self.run) as staged:
                save_records(staged, self.rows)
                write_json(staged['output']/'visible_intervals.json', dict(
                    completed=complete, completed_frames=self.index, next_frame_index=None if complete else self.index,
                    frame_mapping=self.mapping.to_dict(), error=error,
                    intervals=summarize_intervals(self.rows, self.mapping, complete)))
                staged['manifest']['state'] = 'FAILED' if error else 'G_SCAN_COMPLETE' if complete else 'G_SCAN_PARTIAL'
                staged['manifest']['products']['visible_intervals'] = dict(state='GENERATED', complete=complete)
                update_run(staged)
                save_review(s, staged)
        except Exception as exc:
            complete = False
            error = '; '.join(filter(None, (error, 'OUTPUT_FAILED: '+repr(exc))))
            self.run['manifest'].update(state='FAILED', error=error)
            try:
                update_run(self.run)
            except Exception:
                _LOG.exception('Defect scan failure manifest could not be written')
        if valid:
            try:
                self.scan_scene.wfrl_nrel_defects.status = f'扫描 {self.index}/{self.mapping.frame_count} 帧；' + self.run['manifest']['state']
            except ReferenceError:
                pass
        if error:
            try:
                self.report({'ERROR'}, error)
            except ReferenceError:
                pass
        self.finish_result = {'FINISHED'} if complete else {'CANCELLED'}
        return self.finish_result

    def cancel(self, context):
        if getattr(self, 'run', None) is not None:
            self.finish_scan(context, True)

    def modal(self, context, event):
        if self.finished:
            return self.finish_result
        try:
            current = (self.bound_session_valid() and context.scene == self.scan_scene
                       and context.window == self.scan_window and context.window_manager == self.scan_wm)
        except ReferenceError:
            current = False
        if not current:
            return self.finish_scan(context, True)
        if event.type == 'ESC':
            return self.finish_scan(context, True)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        s = self.scan_session
        try:
            s.verify_cameras()
            if not s.store.accepts_version(self.version):
                return self.finish_scan(context, True, 'STALE_STATE')
            sample = self.mapping.sample(self.index)
            self.rows.extend(s.analyze(sample['time_s'],self.index,self.run['manifest']['run_id'],self.version))
            self.index += 1
            self.scan_scene.wfrl_nrel_defects.status = f'扫描 {self.index}/{self.mapping.frame_count}；Esc 取消'
            if self.index == self.mapping.frame_count:
                return self.finish_scan(context)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            return self.finish_scan(context, True, repr(exc))
