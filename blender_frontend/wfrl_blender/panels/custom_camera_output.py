"""Explicit filesystem actions for research cameras; no automatic persistence."""
from pathlib import Path
import json
from copy import deepcopy

import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty
from .. import custom_cameras as core, custom_camera_capture as capture
from .. import camera_projection as projection
from .. import custom_camera_history as history, custom_camera_preview as preview

_PENDING_IMPORT = None
_SUMMARY_CACHE = {}


def editable(context):
    from .custom_cameras import _ACTIVE, installation_block_reason
    return (not capture.active() and not installation_block_reason(context.scene)
            and (_ACTIVE is None or _ACTIVE.stage in {'WATCH','LAYOUT'}))


class WFRL_OT_CustomLayoutExport(bpy.types.Operator):
    bl_idname='wfrl.custom_layout_export'
    bl_label='导出布局 JSON'
    filepath:StringProperty(subtype='FILE_PATH')
    filter_glob:StringProperty(default='*.json',options={'HIDDEN'})

    @classmethod
    def poll(cls,context):return editable(context) and core.is_available(context.scene)

    def invoke(self,context,event):
        self.filepath='camera-layout.json'
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self,context):
        try:
            payload=core.layout_dict(context.scene)
            target=Path(bpy.path.abspath(self.filepath))
            if target.suffix.lower()!='.json':raise ValueError('请使用 .json 文件名')
            with target.open('x',encoding='utf-8') as stream:
                json.dump(payload,stream,ensure_ascii=False,indent=2,allow_nan=False)
                stream.write('\n')
            self.report({'INFO'},'已导出布局：'+str(target))
            return {'FINISHED'}
        except (ValueError,OSError) as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}


def prepare_import(scene, payload):
    core.validate_layout(scene, payload)
    key, current, fingerprint = history.before_change(scene)
    return {'payload':deepcopy(payload), 'identity':key, 'fingerprint':fingerprint,
            'summary':core.layout_summary(current,payload)}


def commit_import(scene, review):
    key,current,fingerprint=history.before_change(scene)
    if key!=review['identity'] or fingerprint!=review['fingerprint']:
        raise ValueError('当前布局、模型或会话已变化，请重新读取文件查看摘要')
    return core.import_layout(scene,review['payload'],overwrite=True)


class WFRL_OT_CustomLayoutImport(bpy.types.Operator):
    bl_idname='wfrl.custom_layout_import'
    bl_label='读取布局并查看替换摘要'
    filepath:StringProperty(subtype='FILE_PATH')
    filter_glob:StringProperty(default='*.json',options={'HIDDEN'})

    @classmethod
    def poll(cls,context):return editable(context) and core.is_available(context.scene)

    def invoke(self,context,event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self,context):
        global _PENDING_IMPORT
        try:
            if not editable(context):raise ValueError('当前正在调整或采集，不能导入')
            with Path(bpy.path.abspath(self.filepath)).open(encoding='utf-8') as stream:
                payload=json.load(stream)
            _PENDING_IMPORT = prepare_import(context.scene,payload)
            return bpy.ops.wfrl.custom_layout_confirm('INVOKE_DEFAULT')
        except (ValueError,OSError,KeyError,TypeError,RuntimeError) as exc:
            _PENDING_IMPORT=None
            self.report({'ERROR'},str(exc));return {'CANCELLED'}


class WFRL_OT_CustomLayoutConfirm(bpy.types.Operator):
    bl_idname='wfrl.custom_layout_confirm'
    bl_label='确认完整布局替换'

    def invoke(self,context,event):
        self.pending = _PENDING_IMPORT
        if self.pending is None:return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self,width=640)

    def draw(self,context):
        box=self.layout.box();box.alert=True
        box.label(text='这是完整布局替换；文件未列出的槽位会清空。')
        pose = self.pending['payload'].get('rig_pose')
        if pose:
            box.label(text='盒体 XYZ (m)：' + ', '.join(f'{v:.3f}' for v in pose['center']))
            box.label(text=f"盒体水平朝向：{pose['housing_yaw_deg']:.3f}°")
        for row in self.pending['summary']:
            box=self.layout.box()
            box.label(text=f'C{row["slot"]} {row["action"]}')
            if row['enabled'] is not None:
                box.label(text='备注：'+row['label']+'；'+('参与' if row['enabled'] else '不参与')+'三路预览与采集')
            for change in row['changes']:
                for start in range(0,len(change),58):box.label(text=change[start:start+58])
        self.layout.label(text='确认仅提交当前场景；整套变化记为一步相机撤销。')

    def execute(self,context):
        global _PENDING_IMPORT
        try:
            pending=getattr(self,'pending',None)
            if pending is None or pending is not _PENDING_IMPORT:
                raise ValueError('导入摘要已失效，请重新选择文件')
            if not editable(context):raise ValueError('请先结束草稿或采集，再重新导入')
            # Deliberately submit the validated in-memory content shown above.
            # The file is not re-read behind the user's confirmation.
            commit_import(context.scene,pending)
            _PENDING_IMPORT=None
            self.report({'INFO'},'完整布局已提交到当前场景')
            return {'FINISHED'}
        except (ValueError,RuntimeError,KeyError,TypeError) as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}

    def cancel(self,context):
        global _PENDING_IMPORT
        _PENDING_IMPORT=None


class WFRL_OT_CustomCapture(bpy.types.Operator):
    bl_idname='wfrl.custom_capture'
    bl_label='导出相机原图'
    directory:StringProperty(subtype='DIR_PATH')

    @classmethod
    def poll(cls,context):return editable(context) and core.is_available(context.scene)

    def invoke(self,context,event):
        try:
            capture.require_preflight(context, requested_times(context.scene))
        except (ValueError,RuntimeError) as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
        self.area=context.area
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self,context):
        self.layout.label(text='在所选目录内创建唯一采集子目录')
        self.layout.label(text='采集参数使用 View → 采集设置；此处只选择目录')
        draw_summary(self.layout, context)

    def execute(self,context):
        try:
            area=getattr(self,'area',None) or context.area
            if area.type!='VIEW_3D':
                area=next(a for a in context.window.screen.areas if a.type=='VIEW_3D')
            self.area=area
            region=next(r for r in area.regions if r.type=='WINDOW')
            times=requested_times(context.scene)
            capture.require_preflight(context,times)
            with context.temp_override(area=area,region=region):
                self.transaction=capture.Capture(context,bpy.path.abspath(self.directory),times)
            self.transaction.ui_operator=self
            self.wm=context.window_manager
            self.timer=self.wm.event_timer_add(.03,window=context.window)
            self.wm.modal_handler_add(self)
            self.handle=bpy.types.SpaceView3D.draw_handler_add(self.draw_progress,(), 'WINDOW','POST_PIXEL')
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        except (ValueError,OSError,RuntimeError,StopIteration) as exc:
            if getattr(self,'transaction',None) and not self.transaction.closed:
                self.transaction.finish(error=str(exc))
            self.remove_timer()
            self.report({'ERROR'},str(exc));return {'CANCELLED'}

    def draw_progress(self):
        if bpy.context.area == self.area and not self.transaction.closed:
            preview.draw_controls(bpy.context, capture.progress(), external=True)

    def remove_timer(self):
        if getattr(self,'handle',None):
            bpy.types.SpaceView3D.draw_handler_remove(self.handle,'WINDOW')
            self.handle=None
        if getattr(self,'timer',None):
            self.wm.event_timer_remove(self.timer);self.timer=None

    def modal(self,context,event):
        if self.transaction.closed:
            self.remove_timer();return {'FINISHED'}
        if event.type=='ESC' and event.value=='PRESS':
            self.transaction.request_cancel();self.remove_timer()
            self.report({'INFO'},'取消请求已接受；已停止，保留已完成组')
            return {'CANCELLED'}
        if event.type=='TIMER':
            if getattr(event, 'timer', self.timer)!=self.timer:return {'PASS_THROUGH'}
            try:
                self.transaction.guard()
                region=next(r for r in self.area.regions if r.type=='WINDOW')
                with context.temp_override(area=self.area,region=region):
                    complete=self.transaction.step(context)
                self.area.tag_redraw()
                if complete:
                    self.remove_timer()
                    self.report({'INFO'},'已导出：'+str(self.transaction.path))
                    return {'FINISHED'}
            except Exception as exc:
                self.transaction.finish(error=str(exc));self.remove_timer()
                self.report({'ERROR'},str(exc));return {'CANCELLED'}
        # Pause/cancel buttons remain accessible; viewport/timeline keys cannot
        # change the sample between capture groups.
        if event.type in {'SPACE','LEFT_ARROW','RIGHT_ARROW','UP_ARROW','DOWN_ARROW'}:
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def cancel(self,context):
        if hasattr(self,'transaction'):self.transaction.finish(error='操作关闭')
        self.remove_timer()


class WFRL_OT_CustomCaptureCancel(bpy.types.Operator):
    bl_idname='wfrl.custom_capture_cancel'
    bl_label='取消图像采集'
    @classmethod
    def poll(cls,context):return capture.active()

    def execute(self,context):
        if capture.active():capture._ACTIVE.request_cancel()
        self.report({'INFO'},'取消请求已接受；已停止，保留已完成组')
        return {'FINISHED'}


def requested_times(scene):
    if scene.wfrl_capture_mode == 'CURRENT':
        return None
    return projection.sample_times(scene.wfrl_capture_start,scene.wfrl_capture_end,scene.wfrl_capture_step)


def summary_check(context, times):
    """UI-only memo; actual export always calls require_preflight afresh."""
    from .custom_cameras import session_identity, installation_block_reason
    scene = context.scene
    cameras = tuple((slot, cam.as_pointer(), repr(core.parameters(cam)), bool(cam.get('custom_enabled',True)))
                    for slot in core.SLOTS if (cam := core.get_camera(scene,slot)) is not None)
    key = (session_identity(scene), preview._REVISION, cameras, repr(preview.render_profile(scene)),
           scene.wfrl_capture_mode, scene.wfrl_capture_start, scene.wfrl_capture_end,
           scene.wfrl_capture_step, capture.active(), installation_block_reason(scene),
           any(obj.get('wfrl_custom_draft') for obj in scene.objects))
    if _SUMMARY_CACHE.get('key') != key:
        _SUMMARY_CACHE.update(key=key, value=capture.preflight(context, times))
    return _SUMMARY_CACHE['value']


def draw_summary(layout,context):
    try:
        times=requested_times(context.scene)
        result=summary_check(context,times)
        if times:
            layout.label(text=f'实际首末：{times[0]:.4f}–{times[-1]:.4f} s；{len(times)} 个采样点')
        n,m=result['samples'],result['cameras']
        layout.label(text=f'{n} 个采样点 × {m} 台参与相机 = {n*m} 张 PNG')
        layout.label(text=f'每组 {result["pixels_per_group"]:,} 像素 · sRGB PNG')
        if result.get('gpu_texture_limit'):
            layout.label(text=f'当前 GPU 单边上限：{result["gpu_texture_limit"]} px')
        if result['bounds']:
            layout.label(text=f'来源可求值范围：{result["bounds"][0]:.4f}–{result["bounds"][1]:.4f} s')
        for reason in result['errors']:
            for start in range(0,len(reason),24):layout.label(text=reason[start:start+24],icon='ERROR' if start==0 else 'NONE')
        if not result['errors']:layout.label(text='预检查通过；实际开始前重新检查',icon='CHECKMARK')
        return not result['errors']
    except (ValueError,RuntimeError) as exc:
        layout.label(text=str(exc),icon='ERROR')
        return False


class WFRL_PT_CustomCapture(bpy.types.Panel):
    bl_label='采集设置'
    bl_idname='WFRL_PT_custom_capture'
    bl_space_type,bl_region_type,bl_category='VIEW_3D','UI','View'
    bl_options={'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls,context):return core.is_available(context.scene)

    def draw(self,context):
        layout,scene=self.layout,context.scene
        if capture.active():
            for part in capture.progress().split('；'):layout.label(text=part)
            layout.operator('wfrl.custom_capture_cancel',text='取消采集（当前组完成后生效）')
            return
        for slot in core.SLOTS:
            cam=core.get_camera(scene,slot)
            box=layout.box()
            box.label(text=f'C{slot} · '+('未安装' if cam is None else '参与三路预览与采集' if cam.get('custom_enabled',True) else '已安装，未参与'))
            if cam:
                try:
                    p=core.parameters(cam);w,h=projection.resolution(p.fov,p.vfov,p.output_long_edge_px)
                    box.label(text=f'长边 {p.output_long_edge_px} px → {w} × {h} px')
                except ValueError as exc:box.label(text=str(exc),icon='ERROR')
                row=box.row();row.enabled=editable(context)
                row.operator('wfrl.custom_output_edit',text=f'编辑 C{slot} 输出参数').slot=str(slot)
        layout.prop(scene,'wfrl_capture_mode',text='采集方式')
        if scene.wfrl_capture_mode=='SEQUENCE':
            for key in ('start','end','step'):layout.prop(scene,'wfrl_capture_'+key)
        valid=draw_summary(layout,context)
        row=layout.row();row.enabled=valid
        row.operator('wfrl.custom_capture',text='导出原图 → 选择目录')
        row=layout.row(align=True)
        row.operator('wfrl.custom_layout_export',text='导出布局 JSON')
        row.operator('wfrl.custom_layout_import',text='导入布局 JSON')


class WFRL_OT_CustomOutputEdit(bpy.types.Operator):
    bl_idname='wfrl.custom_output_edit'
    bl_label='编辑相机输出参数（草稿）'
    slot:StringProperty()
    def execute(self,context):
        context.window_manager.wfrl_custom_slot=int(self.slot)
        return bpy.ops.wfrl.custom_camera('INVOKE_DEFAULT',mode='EDIT')


def register_settings():
    bpy.types.Scene.wfrl_capture_mode=EnumProperty(name='采集方式',items=[('CURRENT','当前时刻',''),('SEQUENCE','时间序列','')])
    bpy.types.Scene.wfrl_capture_start=FloatProperty(name='开始时间 (s)',precision=4)
    bpy.types.Scene.wfrl_capture_end=FloatProperty(name='结束时间 (s)',precision=4)
    bpy.types.Scene.wfrl_capture_step=FloatProperty(name='采样间隔 (s)',default=.1,precision=4)


def unregister_settings():
    global _PENDING_IMPORT
    _PENDING_IMPORT=None
    _SUMMARY_CACHE.clear()
    for key in ('mode','start','end','step'):
        if hasattr(bpy.types.Scene,'wfrl_capture_'+key):delattr(bpy.types.Scene,'wfrl_capture_'+key)


# Backwards internal draw entry; settings are rendered by the dedicated panel.
def draw_output(layout,context):
    layout.label(text='原图与布局导出见 View → 采集设置')


CLASSES=(WFRL_OT_CustomLayoutExport,WFRL_OT_CustomLayoutImport,WFRL_OT_CustomLayoutConfirm,
         WFRL_OT_CustomCapture,WFRL_OT_CustomCaptureCancel,WFRL_OT_CustomOutputEdit,WFRL_PT_CustomCapture)
