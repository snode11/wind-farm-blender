"""Full-turbine geometry and same-source texture comparison controls."""
import bpy

from .. import split_reconstruction, split_reconstruction_ui


class WFRL_PT_BladeRecon(bpy.types.Panel):
    bl_label = '完整风机重建对照'
    bl_idname = 'WFRL_PT_blade_recon'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '叶片三维重建'

    def draw(self, context):
        layout = self.layout
        layout.operator('wfrl.split_recon_open_example', icon='FILE_BLEND')
        layout.label(text='几何与同源纹理共用 601 个保存样本')
        layout.label(text='两侧均显示叶片、塔筒、机舱和轮毂')
        layout.label(text='叶片为重建估计；支撑结构为仿真展示')
        if split_reconstruction.available(context.scene):
            split_reconstruction_ui.draw_controls(layout, context)


CLASSES = (WFRL_PT_BladeRecon,)
