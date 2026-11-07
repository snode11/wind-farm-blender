"""NREL defect RNA properties, list rows and sidebar panels."""
import bpy
from .defect_schema import KIND_DESCRIPTIONS, KIND_LABELS, PRESETS


def redraw_editor(context):
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D': area.tag_redraw()


def helper_changed(props,context):
    from . import editor as _editor
    s=_editor.session(context)
    if s:
        for obj in s.live_helpers(): obj.hide_viewport=not props.show_helpers or s.healthy_payload is not None


class NRELDefectListItem(bpy.types.PropertyGroup):
    defect_id: bpy.props.StringProperty()
    enabled: bpy.props.BoolProperty()


class NREL_UL_defects(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon='CHECKBOX_HLT' if item.enabled else 'CHECKBOX_DEHLT')


class NRELDefectProperties(bpy.types.PropertyGroup):
    defect_items: bpy.props.CollectionProperty(type=NRELDefectListItem, options={'SKIP_SAVE'})
    selected: bpy.props.IntProperty(name='当前缺陷', default=-1, min=-1)
    kind: bpy.props.EnumProperty(name='类型', items=[
        (kind, KIND_LABELS[kind], KIND_DESCRIPTIONS[kind]) for kind in PRESETS])
    turbine_id: bpy.props.EnumProperty(name='机组', items=[(tid,tid,'') for tid in ('T1','T2','T3')], default='T1')
    blade_id: bpy.props.IntProperty(name='叶片', default=1, min=1, max=3)
    side: bpy.props.EnumProperty(name='参考侧', items=[('suction', '吸力面', ''), ('pressure', '压力面', '')])
    span: bpy.props.FloatProperty(name='展向 (m)', default=45., precision=4)
    u: bpy.props.FloatProperty(name='弦向 u', default=.4, precision=4)
    theta: bpy.props.FloatProperty(name='方向角 (°)', default=0.)
    length: bpy.props.FloatProperty(name='长度 (m)', default=.6, min=.001, precision=4)
    width: bpy.props.FloatProperty(name='宽度 (m)', default=.003, min=.00001, precision=5)
    depth: bpy.props.FloatProperty(name='深度 (m)', default=.015, min=0., precision=4)
    edge_roughness: bpy.props.FloatProperty(name='边缘不规则度', default=0., min=0., max=.5, precision=3)
    crater_fraction: bpy.props.FloatProperty(name='雷击坑口比例', default=.4, min=.15, max=.6, precision=3)
    seed: bpy.props.IntProperty(name='形态种子', default=17, min=0)
    enabled: bpy.props.BoolProperty(name='启用缺陷', default=True)
    show_helpers: bpy.props.BoolProperty(name='显示辅助标记', default=True, update=helper_changed)
    config_path: bpy.props.StringProperty(name='配置文件', subtype='FILE_PATH', default='//defects.json')
    output_path: bpy.props.StringProperty(name='新运行目录', subtype='DIR_PATH', default='//defect-run')
    time_s: bpy.props.FloatProperty(name='绝对时刻 (s)', default=117., min=0., max=100000.)
    scan_start: bpy.props.FloatProperty(name='扫描起点 (s)', default=117., min=0.)
    scan_duration: bpy.props.FloatProperty(name='扫描时长 (s)', default=.1, min=1/60)
    status: bpy.props.StringProperty(default='选择类型并新建，或从列表选择已有缺陷')


def wrapped_lines(text, available):
    """Split text into display lines using East Asian character widths."""
    import unicodedata
    lines, line, width = [], '', 0
    for char in str(text):
        size = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
        if char == '\n' or width + size > available:
            lines.append(line)
            line, width = '', 0
        if char != '\n':
            line += char
            width += size
    if line:
        lines.append(line)
    return lines


def text_columns(context):
    scale = context.preferences.system.ui_scale
    return max(20, int((context.region.width / scale - 36) / 7))


def wrapped_label(layout, text, context):
    """Show complete explanations with wrapping appropriate to the sidebar."""
    from . import editor as _editor
    for line in _editor.wrapped_lines(text, _editor.text_columns(context)):
        layout.label(text=line)


def status_box(layout, props, context):
    """Reserve two status rows and a permanent entry to the complete message."""
    from . import editor as _editor
    available = max(16, _editor.text_columns(context)-4)
    lines = _editor.wrapped_lines(props.status, available)
    shown = lines[:2]
    if len(lines) > 2:
        shown[-1] = (_editor.wrapped_lines(shown[-1], available-2) or [''])[0]+'…'
    box = layout.box().column(align=True)
    for index in range(2):
        box.label(text=shown[index] if index < len(shown) else ' ')
    box.operator('wfrl.nrel_defect_status', text='查看完整提示', icon='INFO')


class NREL_PT_defects(bpy.types.Panel):
    bl_label = 'NREL 5MW · 叶片缺陷'
    bl_idname = 'NREL_PT_defects'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MAPPO'

    @classmethod
    def poll(cls, context):
        from .. import farm_flex
        return farm_flex.is_active(context.scene)

    def draw(self, context):
        from . import editor as _editor
        s, p, layout = _editor.session(context), context.scene.wfrl_nrel_defects, self.layout
        if s is None:
            self._draw_inactive(layout, p, context)
            return

        self._draw_session_status(layout, s, p, context)
        self._draw_defect_list(layout, s, p)
        self._draw_view(layout, s, p)
        self._draw_pending_disposals(layout, s, p)
        self._draw_config(layout, s, p, context)
        self._draw_analysis(layout, s, p, context)
        self._draw_notes(layout, context)
        self._draw_buttons(layout, [('CLOSE', '关闭编辑器')], s, p)

    def _draw_buttons(self, parent, items, s, p):
        from . import editor as _editor
        row = parent.row(align=True)
        for code, label in items:
            cell = row.row(align=True)
            cell.enabled = not _editor.action_unavailable(s, p, code)
            cell.operator('wfrl.nrel_defect_action', text=label).action = code

    def _section(self, layout, key, title, closed=True):
        header, body = layout.panel('nrel_defects_' + key, default_closed=closed)
        header.label(text=title)
        return body

    def _draw_inactive(self, layout, p, context):
        from . import editor as _editor
        layout.operator('wfrl.nrel_defect_action',text='启动缺陷编辑器').action='OPEN'
        _editor.status_box(layout, p, context)

    def _draw_session_status(self, layout, s, p, context):
        from . import editor as _editor
        layout.label(text='合成视觉缺陷 · 未耦合物理', icon='INFO')
        if s.busy:
            layout.label(text='扫描中 · 按 Esc 取消', icon='TIME')
        elif s.healthy_payload is not None:
            layout.label(text='健康对照中 · 返回后可继续编辑', icon='HIDE_OFF')
        elif s.draft is not None:
            layout.label(text='编辑草稿 · 回放时刻已冻结', icon='GREASEPENCIL')
        else:
            layout.label(text='待编辑 · 选择缺陷或新建', icon='GREASEPENCIL')

        actions = layout.column(align=True)
        self._draw_buttons(actions, [('PREVIEW', '更新预览')], s, p)
        self._draw_buttons(actions, [('CONFIRM', '应用修改'), ('CANCEL', '取消编辑')], s, p)
        if s.draft is not None:
            if _editor.preview_is_current(s, p):
                layout.label(text='参数与当前显示一致', icon='CHECKMARK')
            else:
                layout.label(text='参数已改，预览未更新', icon='INFO')
        else:
            layout.label(text=' ')
        _editor.status_box(layout, p, context)

    def _draw_defect_list(self, layout, s, p):
        from . import editor as _editor
        listing = layout.column()
        listing.label(text=f'缺陷列表 · {len(p.defect_items)} 项')
        if p.defect_items:
            rows = listing.column()
            rows.enabled = s.draft is None and s.healthy_payload is None and not s.busy
            rows.template_list('NREL_UL_defects', '', p, 'defect_items', p, 'selected', rows=3, maxrows=5)
        else:
            listing.label(text='暂无缺陷，选择类型后新建')

        if s.draft is None:
            selected = s.store.document['defects'][p.selected] if 0 <= p.selected < len(p.defect_items) else None
            self._draw_buttons(listing, [('EDIT', '编辑选中缺陷'), ('TOGGLE', '停用' if selected and selected['enabled'] else '启用')], s, p)
            self._draw_buttons(listing, [('COPY', '复制（默认停用）'), ('DELETE', '删除')], s, p)
            self._draw_buttons(listing, [('UNDO', '撤销'), ('REDO', '重做')], s, p)
            create = layout.box()
            create.enabled = not _editor.action_unavailable(s, p, 'NEW')
            create.label(text='新建缺陷')
            row = create.row(align=True)
            row.prop(p, 'turbine_id', text='')
            row.prop(p, 'kind', text='')
            self._draw_buttons(create, [('NEW', '新建所选类型')], s, p)
        else:
            self._draw_draft(layout, s, p)

    def _draw_draft(self, layout, s, p):
        kind = s.draft['defects'][s.draft_index]['morphology']
        preset = PRESETS[kind]
        edit = layout.column()
        edit.use_property_split = True
        edit.use_property_decorate = False
        edit.label(text='编辑 · ' + KIND_LABELS[kind])
        for name in ('enabled', 'turbine_id', 'blade_id', 'side'):
            edit.prop(p, name)
        edit.operator('wfrl.nrel_defect_pick', text='在所选叶片上点选位置', icon='EYEDROPPER')
        for name in ('span', 'u', 'length', 'width'):
            edit.prop(p, name)
        if preset['representation'] != 'appearance':
            edit.prop(p, 'depth')
        else:
            edit.label(text='材质外观缺陷，无几何深度')
        advanced = self._section(layout, 'shape', '高级形态参数')
        if advanced is not None:
            advanced.use_property_split = True
            advanced.use_property_decorate = False
            advanced.prop(p, 'theta')
            for name in ('edge_roughness', 'crater_fraction'):
                if name in preset:
                    advanced.prop(p, name)
            advanced.prop(p, 'seed')

    def _draw_view(self, layout, s, p):
        view = self._section(layout, 'view', '观察效果', closed=False)
        if view is not None:
            self._draw_buttons(view, [('CLOSEUP', '正面近景'), ('OBLIQUE', '斜侧近景')], s, p)
            self._draw_buttons(view, [('HEALTHY', '返回缺陷效果' if s.healthy_payload is not None else '对比健康叶片')], s, p)
            helper = view.row()
            helper.enabled = not s.busy and s.healthy_payload is None
            helper.prop(p, 'show_helpers')
            self._draw_buttons(view, [('CAMERA1', 'C1'), ('CAMERA2', 'C2'), ('CAMERA3', 'C3')], s, p)

    def _draw_pending_disposals(self, layout, s, p):
        if s.store.pending_disposals:
            layout.label(text='缺陷已应用；旧资源待释放', icon='ERROR')
            self._draw_buttons(layout, [('RETRY_CLEANUP', '重试资源释放')], s, p)

    def _draw_config(self, layout, s, p, context):
        from . import editor as _editor
        config = self._section(layout, 'config', '配置文件')
        if config is not None:
            config.enabled = not _editor.action_unavailable(s, p, 'SAVE')
            config.prop(p, 'config_path', text='JSON 文件')
            self._draw_buttons(config, [('SAVE', '保存配置'), ('LOAD', '加载配置')], s, p)
            _editor.wrapped_label(config, '保存已应用的缺陷参数，不会渲染图片', context)

    def _draw_analysis(self, layout, s, p, context):
        from . import editor as _editor
        analysis = self._section(layout, 'analysis', '几何分析与导出（G / A）')
        if analysis is not None:
            analysis.enabled = not _editor.action_unavailable(s, p, 'ANALYZE')
            _editor.wrapped_label(analysis, 'G 仅分析投影与遮挡，不代表图片中可辨识', context)
            analysis.prop(p, 'time_s')
            self._draw_buttons(analysis, [('TIME', '定位时刻'), ('ANALYZE', '分析当前帧（G）')], s, p)
            status = str(context.scene.get('defect_analysis_status', 'NOT_EVALUATED'))
            _editor.wrapped_label(analysis, {'NOT_EVALUATED': '当前帧尚未分析', 'EVALUATED': '当前帧已分析'}.get(status, status), context)
            for row in s.records or []:
                if row.get('primary', True):
                    _editor.wrapped_label(analysis, f"{row.get('camera_id', '')} {row.get('defect_id', '')[:8]}: {row.get('status', row.get('state', '?'))}", context)
                    width = row.get('width_px') or {}
                    if width.get('max') is not None:
                        _editor.wrapped_label(analysis, f"投影宽 {width['min']:.3f}–{width['max']:.3f} px；非辨识结论", context)
            analysis.separator()
            analysis.prop(p, 'output_path')
            self._draw_buttons(analysis, [('EXPORT_G', '导出当前帧分析资料（G）')], s, p)
            analysis.prop(p, 'scan_start')
            analysis.prop(p, 'scan_duration')
            scan = analysis.row()
            scan.enabled = not _editor.action_unavailable(s, p, 'SCAN')
            scan.operator('wfrl.nrel_defect_scan', text='逐帧扫描（Esc 取消）')
            self._draw_buttons(analysis, [('EXPORT_A', '渲染三相机对照图片（A）')], s, p)
            _editor.wrapped_label(analysis, '每次导出或扫描请选择新的空目录', context)

    def _draw_notes(self, layout, context):
        from . import editor as _editor
        notes = self._section(layout, 'notes', '使用说明与限制')
        if notes is not None:
            for note in ('勾选图标表示启用，空框表示停用；列表高亮表示当前选择。',
                         '辅助轮廓不等于真实外观；请使用材质预览检查。',
                         '缺陷跟随柔性回放；雷达读数仍来自原仿真几何。',
                         '合成缺陷未耦合物理，材质外观尚未审核。'):
                _editor.wrapped_label(notes, note, context)
