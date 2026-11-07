"""Custom-camera sidebar selection, installation controls and parameter display."""
import bpy


class WFRL_PT_CustomCameras(bpy.types.Panel):
    bl_label = '三相机 · T1 · 研发'
    bl_idname = 'WFRL_PT_custom_cameras'
    bl_space_type,bl_region_type,bl_category = 'VIEW_3D','UI','View'
    def draw(self,context):
        state = _panel.WFRL_PT_CustomCameras._draw_selection(self, context)
        if state is None:
            return
        layout, wm, scene, op, editing, stacked, message, diagnostic, draw_installation = state
        _panel.WFRL_PT_CustomCameras._draw_observation(self, layout, scene, op, editing, stacked, draw_installation)
        block_reason, camera, action = _panel.WFRL_PT_CustomCameras._draw_camera_controls(self,
            context, layout, wm, scene, op, editing, stacked, message, diagnostic)
        _panel.WFRL_PT_CustomCameras._draw_parameters(self, context, layout, wm, op, editing, stacked, camera, action, message, diagnostic)
        _panel.WFRL_PT_CustomCameras._draw_history(self, context, layout, editing, block_reason, message)

    def _draw_selection(self, context):
        from .ui_helpers import message, diagnostic
        from . import stacked_camera_rig as box_panel
        layout,wm,scene = self.layout,context.window_manager,context.scene
        op = _panel._ACTIVE if _panel._ACTIVE and _panel._ACTIVE.scene == scene else None
        editing = op is not None and op.stage not in _panel._VIEWING
        stacked = bool(scene.get('wfrl_stacked_camera_rig'))
        placement = box_panel._ACTIVE
        if placement is not None and placement.scene != scene:
            message(layout, '其他场景正在安装相机盒体，请在该场景完成或取消。', context=context)
            return
        if _panel._ACTIVE and _panel._ACTIVE.scene != scene and _panel._ACTIVE.stage not in _panel._VIEWING:
            message(layout, '其他场景正在编辑相机草稿，请在该场景完成或取消。', context=context)
            return

        def draw_installation(box):
            _panel.WFRL_PT_CustomCameras._draw_installation(self, box, editing, placement, wm, diagnostic, context)
        if stacked and placement:
            draw_installation(layout.box())
            return
        layout.label(text='相机观察', icon='CAMERA_DATA')
        row = layout.row(align=True)
        row.enabled = not editing
        for slot in _panel.core.SLOTS:
            installed = _panel.core.get_camera(scene, slot)
            button=row.operator('wfrl.custom_camera_action',text=f'C{slot}',
                icon='CAMERA_DATA' if installed else 'ADD',depress=wm.wfrl_custom_slot==slot)
            button.action,button.slot='SLOT',slot
        if not _panel.core.is_available(scene):
            layout.label(text='请先加载包含 T1 的演示场景',icon='INFO')
            return
        from .. import custom_camera_capture, native_camera_views
        native_error = native_camera_views.last_error(scene)
        if native_error:
            error_box = layout.box()
            error_box.alert = True
            diagnostic(error_box, '相机观察', native_error, error=True, context=context)
        if custom_camera_capture.active():
            if custom_camera_capture._ACTIVE.scene != scene:
                message(layout, '其他场景正在采集相机图像，请等待完成或在所属场景取消。', context=context)
                return
            message(layout, custom_camera_capture.progress(), context=context)
            layout.label(text='撤销不可用：请先结束采集', icon='INFO')
            layout.operator('wfrl.custom_capture_cancel', text='取消采集')
            return
        return layout, wm, scene, op, editing, stacked, message, diagnostic, draw_installation

    def _draw_installation(self, box, editing, placement, wm, diagnostic, context):
        box.enabled = not editing
        if placement:
            box.label(text='点击机舱表面，支架安装端贴合')
            box.label(text='盒体位于支架外端，保留离舱间距')
            box.label(text='可重复选点；中键转动观察视角')
            box.prop(wm,'wfrl_box_spin',text='贴面旋转 (°)')
            row=box.row(align=True)
            row.operator('wfrl.stacked_box_action',text='↶ 15°').action='LEFT'
            row.operator('wfrl.stacked_box_action',text='↷ 15°').action='RIGHT'
            box.label(text='Shift + 滚轮也可旋转整个盒体')
            box.prop(wm,'wfrl_box_aim',text='盒体转向 (°)')
            box.label(text='支架保持贴合，盒体在外端水平转向')
            row=box.row(align=True)
            row.operator('wfrl.stacked_box_action',text='盒体 ↶ 15°').action='AIM_LEFT'
            row.operator('wfrl.stacked_box_action',text='盒体 ↷ 15°').action='AIM_RIGHT'
            row=box.row(align=True)
            confirm=row.row()
            confirm.enabled=placement.transaction.ready and not placement.transaction.error
            confirm.operator('wfrl.stacked_box_action',text='确认安装',icon='CHECKMARK').action='CONFIRM'
            row.operator('wfrl.stacked_box_action',text='取消',icon='X').action='CANCEL'
            if placement.transaction.error:
                diagnostic(box, '相机盒体安装', placement.transaction.error, error=True, context=context)
            return
        box.operator('wfrl.stacked_box_pick', text='贴合安装 / 调整整个盒体', icon='PIVOT_CURSOR')
        box.operator('wfrl.stacked_box_reset_default', text='恢复默认三相机', icon='LOOP_BACK')
        box.label(text='选位置、调方向、确认，在同一步完成')
        header, body = box.panel('wfrl_box_advanced',default_closed=True)
        header.label(text='高级设置 · 自由坐标')
        if body is not None:
            body.label(text='自由调整会解除表面贴合')
            body.operator('wfrl.stacked_box_position', text='整体 XYZ / 水平转向')

    def _draw_observation(self, layout, scene, op, editing, stacked, draw_installation):
        if editing:
            layout.label(text='撤销不可用：请先确认或取消草稿', icon='INFO')
        if not editing:
            views=layout.row(align=True)
            views.operator('wfrl.native_camera_view',text='查看单路',icon='VIEW_CAMERA').mode='WATCH'
            native_button=views.operator('wfrl.native_camera_view',text='2×2 展示')
            native_button.mode='TRIPLE'
            native_button.layout='GRID'
            native_button=views.operator('wfrl.native_camera_view',text='三列对照')
            native_button.mode='TRIPLE'
            native_button.layout='STRIP'
            layout.operator('wfrl.custom_camera',text='查看外部安装布局').mode='LAYOUT'
            if scene.get('wfrl_stacked_camera_rig'):
                layout.label(text='共用盒体 · C1 / C2 / C3 随盒体排列')
            if op:
                layout.operator('wfrl.custom_camera_action',text='退出相机视图 · Esc',icon='X').action='EXIT'
        if stacked:
            header, body = layout.panel('wfrl_camera_box_installation', default_closed=True)
            header.label(text='盒体安装与复位')
            if body is not None:
                draw_installation(body)

    def _draw_camera_controls(self, context, layout, wm, scene, op, editing, stacked, message, diagnostic):
        block_reason = _panel.installation_block_reason(scene)
        if block_reason and not editing:
            message(layout, block_reason, context=context)
        camera=op.camera if editing else _panel.core.get_camera(scene,wm.wfrl_custom_slot)
        layout.label(text=f'C{wm.wfrl_custom_slot} · '+('正在调整' if editing else '已安装' if camera else '待设置位置'))
        if op and op.stage in {'AIM', 'WATCH'} and getattr(op, 'frame_fits', True) is False:
            warning = layout.box()
            warning.alert = True
            warning.label(text='视口空间不足，请扩大视图', icon='INFO')
            warning.label(text='或收起侧栏以显示完整取景框')
        def action(text,key,container=layout):
            container.operator('wfrl.custom_camera_action',text=text).action=key
        if camera and not editing:
            action('参与三路预览与采集 ✓' if camera.get('custom_enabled', True) else '未参与三路预览与采集', 'ENABLE')
            message(layout, '标签：'+camera.get('custom_label', ''), context=context)
        if editing:
            layout.prop(wm,'wfrl_custom_label',text='标签')
            if op.stage == 'INPUT':
                layout.prop(wm,'wfrl_custom_mount_mode',text='安装模式')
                if wm.wfrl_custom_mount_mode == 'RESEARCH':
                    layout.prop(wm,'wfrl_custom_research_confirmed',text='确认支架偏置 / 未验证安装')
            layout.label(text={'PLACE':'1 · 外部安装位置','INPUT':'1 · 输入镜头参数','AIM':'2 · 调整方向'}[op.stage])
            if op.stage=='INPUT':
                action('校验并预览','VALIDATE')
                for i,anchor in enumerate(op.candidates):
                    b=layout.operator('wfrl.custom_camera_action',text=f'选择锚点 {i+1} · {anchor.surface_name}')
                    b.action,b.index='ANCHOR',i
            elif op.stage=='PLACE':
                layout.prop(wm,'wfrl_custom_distance',text='离壳距离 (m)')
                row=layout.row()
                row.enabled=op.ready
                action('下一步：调整方向','NEXT',row)
            elif not stacked:
                action('上一步：安装位置','BACK')
            row=layout.row(align=True)
            confirm=row.row()
            confirm.enabled=op.ready and op.stage=='AIM'
            action('确认修改','CONFIRM',confirm)
            action('取消修改','CANCEL',row)
            if op.error:
                box=layout.box()
                box.alert=True
                diagnostic(box, '相机编辑', op.error, error=True, context=context)
        elif camera:
            if not stacked:
                layout.operator('wfrl.custom_camera',text=f'更改 C{wm.wfrl_custom_slot} 安装位置',icon='PIVOT_CURSOR').mode='PLACE'
                layout.operator('wfrl.custom_camera',text='输入 XYZ 位置 / 参数',icon='DRIVER_DISTANCE').mode='INPUT'
            layout.operator('wfrl.custom_camera',text='调整俯仰角 / 方向与视场',icon='ORIENTATION_GIMBAL').mode='EDIT'
            if not stacked:action(f'清除 C{wm.wfrl_custom_slot}', 'CLEAR')
        else:
            layout.label(text='各槽位分别选点、分别保存',icon='INFO')
            layout.operator('wfrl.custom_camera',text=f'为 C{wm.wfrl_custom_slot} 选安装位置',icon='PIVOT_CURSOR').mode='PLACE'
            layout.operator('wfrl.custom_camera',text='输入 XYZ 位置 / 参数',icon='DRIVER_DISTANCE').mode='INPUT'
        return block_reason, camera, action

    def _draw_parameters(self, context, layout, wm, op, editing, stacked, camera, action, message, diagnostic):
        if editing:
            box = layout.box()
            box.label(text='安装参数 · 相对 T1 机舱')
        else:
            header, box = layout.panel('wfrl_camera_engineering_parameters', default_closed=True)
            header.label(text='工程参数与显示')
        if editing:
            for key,label in [('x','镜头 X (m)'),('y','镜头 Y (m)'),('z','镜头 Z (m)'),('pitch','俯仰角 (°)'),('yaw','水平角 (°)'),('roll','画面旋转角 (°)'),('fov','HFOV (°)'),('vfov','VFOV (°)')]:
                if stacked and key in {'x','y','z'}:continue
                row=box.row()
                row.enabled=not (op.stage=='AIM' and key in {'x','y','z'})
                row.prop(wm,'wfrl_custom_'+key,text=label)
        elif box is not None and camera:
            box.label(text='安装参数 · 相对 T1 机舱')
            p=_panel.core.parameters(camera)
            for key,value in zip(('X','Y','Z'),p.location):box.label(text=f'镜头 {key}：{value:.4f} m')
            for label,key in [('水平角','yaw'),('俯仰角','pitch'),('画面旋转角','roll'),('HFOV','fov'),('VFOV','vfov')]:box.label(text=f'{label}：{getattr(p,key):.3f}°')
        elif box is not None:
            box.label(text='未安装')
        if editing:
            box.prop(wm,'wfrl_custom_linked_zoom',text='联动视场调整（滚轮）')
            box.prop(wm,'wfrl_custom_has_focal',text='记录焦距（不影响画面）')
            if wm.wfrl_custom_has_focal:box.prop(wm,'wfrl_custom_focal',text='记录焦距 (mm)')
            box.prop(wm,'wfrl_custom_long_edge',text='输出长边 (px)')
            box.prop(wm,'wfrl_custom_near',text='近裁剪 (m)')
            box.prop(wm,'wfrl_custom_far',text='远裁剪 (m)')
        if camera:
            p=_panel.core.parameters(camera)
            try:
                w,h=_panel.projection.resolution(p.fov,p.vfov,p.output_long_edge_px)
                if box is not None:
                    box.label(text=f'独立输出：{w} × {h} px')
                    axis=_panel.projection.optical_axis(p.yaw,p.pitch)
                    box.label(text='光轴：'+', '.join(f'{v:.3f}' for v in axis))
                    box.label(text=f'记录焦距：{p.focal_length_mm_record or "未填写"} mm')
                if min(p.fov,p.vfov)<5 or max(p.fov,p.vfov)>150 or max(w,h)/min(w,h)>8:
                    layout.label(text='极端视场 / 画幅：请核对研发用途',icon='INFO')
                warning=camera.get('custom_mount_warning','')
                if warning:message(layout, warning, context=context)
            except ValueError as exc:diagnostic(layout, '相机参数', str(exc), error=True, context=context)
        if box is not None:
            box.prop(wm, 'wfrl_custom_engineering', text='卡片显示工程参数')
            row=box.row(align=True)
            row.prop(wm,'wfrl_custom_show_axis',text='光轴')
            row.prop(wm,'wfrl_custom_show_frustum',text='视锥')
            row=box.row()
            row.enabled=camera is not None and (not editing or op.ready)
            action('复制参数','COPY',row)
            box.operator('wfrl.custom_camera_coordinates',text='坐标说明')

    def _draw_history(self, context, layout, editing, block_reason, message):
        undo_row = layout.row()
        undo_row.enabled = not editing and not block_reason and bool(_panel.history.HISTORY.entries)
        undo_row.operator('wfrl.custom_camera_action', text='撤销上次相机修改', icon='LOOP_BACK').action='UNDO'
        notice = ('撤销：' + _panel.history.HISTORY.entries[-1].description) if _panel.history.HISTORY.entries else _panel.history.HISTORY.notice or '暂无可撤销的相机修改'
        message(layout, notice, context=context)


# Runtime callbacks share the public namespace; definitions are available first
# so importing either this module or the public entry point is supported.
from . import custom_cameras as _panel
