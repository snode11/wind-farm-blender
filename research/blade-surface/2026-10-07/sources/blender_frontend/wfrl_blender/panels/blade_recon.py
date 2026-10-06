"""Independent N-sidebar tab for three-dimensional blade reconstruction."""
import bpy
from .. import blade_recon_review as review


class WFRL_OT_BladeReconLoad(bpy.types.Operator):
    bl_idname = 'wfrl.blade_recon_load'
    bl_label = '导入 recon.json'
    bl_description = '使用 blade_recon 原始前向模型还原重建结果，在独立场景显示'
    filepath: bpy.props.StringProperty(subtype='FILE_PATH',options={'SKIP_SAVE'})
    filter_glob: bpy.props.StringProperty(default='*.json',options={'HIDDEN'})
    sample: bpy.props.BoolProperty(default=False,options={'SKIP_SAVE'})

    def invoke(self,context,event):
        if self.sample:
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self,context):
        try:
            review.load(context, None if self.sample else bpy.path.abspath(self.filepath),
                        truth_path=None if self.sample else (bpy.path.abspath(context.scene.wfrl_recon_truth_path) if context.scene.wfrl_recon_truth_path else None),
                        cameras_path=None if self.sample else (bpy.path.abspath(context.scene.wfrl_recon_cameras_path) if context.scene.wfrl_recon_cameras_path else None))
        except Exception as exc:
            self.report({'ERROR'},'重建数据未加载：'+str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_BladeReconView(bpy.types.Operator):
    bl_idname = 'wfrl.blade_recon_view'
    bl_label = '重建观察视角'
    view: bpy.props.StringProperty(default='FRONT')

    @classmethod
    def poll(cls,context):
        return review.active(context.scene)

    def execute(self,context):
        review.set_view(context,self.view)
        return {'FINISHED'}


class WFRL_OT_BladeReconStep(bpy.types.Operator):
    bl_idname = 'wfrl.blade_recon_step'
    bl_label = '重建逐帧'
    delta: bpy.props.IntProperty(default=1)
    first: bpy.props.BoolProperty(default=False)

    @classmethod
    def poll(cls,context):
        return review.active(context.scene) and review.session(context.scene) is not None

    def execute(self,context):
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        scene = context.scene
        scene.frame_set(scene.frame_start if self.first else min(max(scene.frame_current+self.delta,scene.frame_start),scene.frame_end))
        review.apply(scene)
        return {'FINISHED'}


class WFRL_OT_BladeReconReturn(bpy.types.Operator):
    bl_idname = 'wfrl.blade_recon_return'
    bl_label = '返回原场景'

    @classmethod
    def poll(cls,context):
        return review.active(context.scene) and bpy.data.scenes.get(context.scene.get('blade_recon_return_scene','')) is not None

    def execute(self,context):
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        context.window.scene = bpy.data.scenes[context.scene['blade_recon_return_scene']]
        return {'FINISHED'}


class WFRL_PT_BladeRecon(bpy.types.Panel):
    bl_label = '叶片三维重建 · Blade Recon'
    bl_idname = 'WFRL_PT_blade_recon'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '叶片三维重建'

    def draw(self,context):
        layout, scene = self.layout, context.scene
        layout.label(text='三摄轮廓拟合 · 原算法 v0.1',icon='MESH_DATA')
        row = layout.row(align=True)
        row.operator('wfrl.blade_recon_load',text='加载合成样例',icon='PLAY').sample = True
        row.operator('wfrl.blade_recon_load',text='导入结果',icon='FILE_FOLDER')
        box = layout.box()
        box.label(text='导入附加数据（可选）')
        box.prop(scene,'wfrl_recon_truth_path',text='真值')
        box.prop(scene,'wfrl_recon_cameras_path',text='相机')
        if not review.active(scene):
            layout.label(text='载入后进入独立重建场景',icon='INFO')
            layout.label(text='合成样例：20 秒 · 三片叶片')
            return
        error = scene.get('blade_recon_error')
        if error:
            box = layout.box()
            box.alert = True
            box.label(text=error,icon='ERROR')
        value = review.session(scene)
        if not value:
            return
        seq = value['sequence']
        i = min(max(scene.frame_current-1,0),len(seq.states)-1)
        box = layout.box()
        box.label(text='合成数据 · SYNTHETIC' if 'SYNTHETIC' in seq.source_label.upper() else '外部重建结果',icon='INFO')
        box.label(text='绿色：轮廓图像约束截面')
        box.label(text='橙色：模型推断截面')
        box.label(text='颜色不代表独立深度测量')
        row = layout.row(align=True)
        row.operator('wfrl.blade_recon_step',text='',icon='REW').first = True
        row.operator('wfrl.blade_recon_step',text='',icon='TRIA_LEFT').delta = -1
        row.operator('screen.animation_play',text='暂停' if context.screen.is_animation_playing else '播放',icon='PAUSE' if context.screen.is_animation_playing else 'PLAY')
        row.operator('wfrl.blade_recon_step',text='',icon='TRIA_RIGHT').delta = 1
        layout.prop(scene,'frame_current',text='显示帧',slider=True)
        layout.label(text=f'源帧 {seq.source_frames[i]}  ·  {seq.times[i]:.2f} s  ·  {i+1}/{len(seq.states)}')
        layout.label(text=f'数据采样：{seq.fps:g} fps')
        row = layout.row(align=True)
        row.prop(scene,'wfrl_recon_show_truth',toggle=True)
        row.prop(scene,'wfrl_recon_show_cameras',toggle=True)
        row = layout.row(align=True)
        for key,label in (('FRONT','正面'),('SIDE','侧面'),('OVERVIEW','总览')):
            row.operator('wfrl.blade_recon_view',text=label).view = key
        box = layout.box()
        box.label(text=f'方位角 {seq.states[i,0]:.2f}°')
        row = box.row(align=True)
        for text in ('叶片','桨距 °','挥舞 m','摆振 m'):
            row.label(text=text)
        for b in range(3):
            row = box.row(align=True)
            for text in (f'B{b+1}',f'{seq.states[i,1+b]:.2f}',f'{seq.states[i,4+b]:.2f}',f'{seq.states[i,7+b]:.2f}'):
                row.label(text=text)
            box.label(text=f'图像约束截面：{int(seq.observed[i,b].sum())}/{seq.rotor.cfg.n_sections}')
        if seq.truth_states is None:
            layout.label(text='未提供真值',icon='INFO')
        elif seq.truth_geometry(i) is None:
            layout.label(text='当前源帧无匹配真值',icon='INFO')
        else:
            layout.label(text='真值按源帧匹配；未做叶片循环对齐')
        layout.operator('wfrl.blade_recon_return',icon='LOOP_BACK')


CLASSES = (WFRL_OT_BladeReconLoad,WFRL_OT_BladeReconView,WFRL_OT_BladeReconStep,
           WFRL_OT_BladeReconReturn,WFRL_PT_BladeRecon)
