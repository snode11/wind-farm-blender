"""X100 controls for source identity, installation, replay and recording health."""
import bpy
import time
from ..radar_x100.output import WFRL_OT_X100Output


class WFRL_OT_X100Apply(bpy.types.Operator):
    bl_idname = 'wfrl.x100_apply'
    bl_label = '应用 X100 配置'

    def execute(self, context):
        from ..radar_x100 import runtime
        try:
            runtime.start(context.scene)
        except Exception as exc:
            context.scene['wfrl_x100_error'] = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_X100Close(bpy.types.Operator):
    bl_idname = 'wfrl.x100_close'
    bl_label = '关闭 X100'

    def execute(self, context):
        from ..radar_x100 import runtime
        try:
            runtime.stop(context.scene)
        except Exception as exc:
            context.scene['wfrl_x100_error'] = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_X100MountPreset(bpy.types.Operator):
    bl_idname = 'wfrl.x100_mount_preset'
    bl_label = '切换雷达安装位置'
    bl_description = '切换同一台雷达的位置与朝向，并重新采样当前时刻的点云'
    preset: bpy.props.EnumProperty(items=(
        ('ORIGINAL', '原安装位置', '机舱侧下方，朝下观察'),
        ('NACELLE_SIDE', '机舱侧面', '机舱负 Y 侧壁，朝侧方叶片区域观察')))

    def execute(self, context):
        from ..radar_x100.runtime import apply_mount_preset
        try:
            busy = _busy(context.scene)
            if busy:
                raise ValueError(busy + '，暂时不能切换雷达安装位置')
            apply_mount_preset(context.scene, self.preset)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_X100SearchApply(bpy.types.Operator):
    bl_idname = 'wfrl.x100_search_apply'
    bl_label = '应用搜索推荐安装'
    bl_description = '核对来源后应用离线搜索结果，保留当前场景时刻'

    def execute(self, context):
        import json
        from pathlib import Path
        from ..radar_x100.runtime import apply_mount_preset
        try:
            busy = _busy(context.scene)
            if busy:
                raise ValueError(busy)
            path = Path(bpy.path.abspath(context.scene.wfrl_x100_search_path))
            with path.open('rb') as stream:
                raw = stream.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError('安装搜索报告超过大小上限')
            apply_mount_preset(context.scene, 'SEARCH', search_report=json.loads(raw))
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_X100Step(bpy.types.Operator):
    bl_idname = 'wfrl.x100_step'
    bl_label = '文件帧跳转'
    direction: bpy.props.IntProperty(default=1)

    def execute(self, context):
        from ..radar_x100.runtime import session_for
        session = session_for(context.scene)
        if session and session.mode == 'REPLAY' and session.replay:
            session.playing = False
            index = session.selected_replay_index()
            index = max(0, min(len(session.replay.rows) - 1, index + self.direction))
            return _replay_action(self, context, lambda: session.seek_frame(index))
        return {'FINISHED'}


class WFRL_OT_X100Play(bpy.types.Operator):
    bl_idname = 'wfrl.x100_play'
    bl_label = '文件播放 / 暂停'

    def execute(self, context):
        from ..radar_x100.runtime import session_for
        session = session_for(context.scene)
        if session and session.mode == 'REPLAY' and session.replay:
            return _replay_action(self, context, session.toggle_playback)
        return {'FINISHED'}


def _replay_action(operator, context, action):
    """Unapplied replay edits surface as panel errors instead of operator tracebacks."""
    try:
        action()
    except Exception as exc:
        context.scene['wfrl_x100_error'] = str(exc)
        operator.report({'ERROR'}, str(exc))
        return {'CANCELLED'}
    return {'FINISHED'}


class WFRL_OT_X100Record(bpy.types.Operator):
    bl_idname = 'wfrl.x100_record'
    bl_label = '记录 / 结束记录'

    def execute(self, context):
        from ..radar_x100.runtime import session_for
        session = session_for(context.scene)
        if session is None:
            return {'CANCELLED'}
        try:
            if session.fanout:
                session.stop_recording()
            else:
                session.start_recording(bpy.path.abspath(context.scene.wfrl_x100_record_path))
        except Exception as exc:
            context.scene['wfrl_x100_error'] = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


def _busy(scene):
    from .home import busy_reason
    return busy_reason(scene)


def _narrow(context):
    width = getattr(context.region, 'width', 320)
    return width < 240 * context.preferences.system.ui_scale


def _property(layout, scene, name, *, narrow):
    if narrow:
        field = layout.column(align=True)
        field.use_property_split = False
        field.label(text=scene.bl_rna.properties[name].name)
        field.prop(scene, name, text='')
    else:
        layout.prop(scene, name)


def _notes(layout, key, lines, *, context):
    from .ui_helpers import message
    header, body = layout.panel(key, default_closed=True)
    header.label(text='说明' if _narrow(context) else '说明与限制', icon='INFO')
    if body is not None:
        for line in lines:
            message(body, line, 'NONE', context=context)


class _X100Panel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'X100'


class WFRL_PT_X100(_X100Panel, bpy.types.Panel):
    bl_label = 'X100 雷达'
    bl_idname = 'WFRL_PT_x100'

    def draw(self, context):
        from ..radar_x100.runtime import session_for
        from .ui_helpers import message
        layout, scene = self.layout, context.scene
        session, busy = session_for(scene), _busy(scene)
        if busy:
            message(layout, busy + ' · 雷达设置暂时锁定', 'LOCKED', context=context)
        controls = layout.column()
        controls.enabled = not busy
        narrow = _narrow(context)
        sources = controls.column(align=True) if narrow else controls.row()
        sources.prop(scene, 'wfrl_x100_source', expand=True)
        row = controls.column(align=True) if narrow else controls.row(align=True)
        row.scale_y = 1.2
        row.operator('wfrl.x100_apply', text='应用配置', icon='PLAY')
        row.operator('wfrl.x100_close', icon='X')
        status = layout.box().column(align=True)
        if session:
            from ..radar_x100.configuration import summary
            for line in summary(session.applied_config):
                message(status, line, 'NONE', context=context)
            pending = session.pending_configuration()
            if pending:
                message(status, '未应用：' + '、'.join(pending), 'INFO', context=context)
                message(status, '点击“应用配置”后生效', 'NONE', context=context)
        message(status, scene.get('wfrl_x100_status', '未启动'),
                'RADIOBUT_ON' if session else 'RADIOBUT_OFF', context=context)
        if session and session.frame:
            frame = session.frame
            from ..radar_x100.configuration import FRAME_SOURCE_LABELS
            declared = ' · 用户声明' if frame.provenance.get('source_declaration') == 'EXPLICIT' else ''
            message(status, '观测来源：' + FRAME_SOURCE_LABELS[frame.source_kind] + declared, 'NONE', context=context)
            message(status, f'#{frame.sequence} · {frame.validity} · {len(frame.points)} 点',
                    'NONE', context=context)
            budget = frame.provenance.get('candidate_budget') if session.mode == 'SIMULATION' else None
            if budget is not None and budget != scene.wfrl_x100_candidates:
                message(status, f'当前帧预算 {budget}；编辑值在下一新样本生效', 'INFO', context=context)
        error = scene.get('wfrl_x100_error')
        if error:
            box = layout.box()
            box.alert = True
            message(box, error, 'ERROR', context=context)


class WFRL_PT_X100Setup(_X100Panel, bpy.types.Panel):
    bl_label = '配置'
    bl_idname = 'WFRL_PT_x100_setup'
    bl_parent_id = 'WFRL_PT_x100'

    def draw_header(self, context):
        self.layout.label(text='', icon={'SIMULATION': 'OBJECT_ORIGIN', 'REPLAY': 'FILE_FOLDER',
                                         'LIVE': 'LINKED'}[context.scene.wfrl_x100_source])

    def draw(self, context):
        scene = context.scene
        from .ui_helpers import message
        layout = self.layout
        layout.enabled = not _busy(scene)
        narrow = _narrow(context)
        layout.use_property_split, layout.use_property_decorate = not narrow, False
        if scene.wfrl_x100_source == 'SIMULATION':
            from ..radar_x100.core import schema
            from ..radar_x100.runtime import session_for
            session = session_for(scene)
            presets = layout.column(align=True)
            presets.use_property_split = False
            presets.label(text='安装位置（同一台雷达）')
            buttons = presets.column(align=True) if narrow else presets.row(align=True)
            buttons.enabled = not session or (session.mode == 'SIMULATION'
                                              and not (session.fanout or session.closing))
            buttons.operator('wfrl.x100_mount_preset', text='原安装位置').preset = 'ORIGINAL'
            buttons.operator('wfrl.x100_mount_preset', text='机舱侧面').preset = 'NACELLE_SIDE'
            if session and session.fanout:
                presets.label(text='结束记录后可切换安装位置', icon='LOCKED')
            elif session and session.mode != 'SIMULATION':
                presets.label(text='请先应用几何仿真配置', icon='INFO')
            layout.separator()
            message(layout, '挂载与安装参数：应用后生效', 'INFO', context=context)
            _property(layout, scene, 'wfrl_x100_mount', narrow=narrow)
            for name in ('wfrl_x100_location', 'wfrl_x100_rotation'):
                vector = layout.column(align=True)
                vector.use_property_split = False
                vector.label(text=scene.bl_rna.properties[name].name)
                for index, axis in enumerate('XYZ'):
                    vector.prop(scene, name, index=index, text=axis)
            layout.separator()
            _property(layout, scene, 'wfrl_x100_candidates', narrow=narrow)
            message(layout, '按表面积采样；预算在下一新样本生效', 'INFO', context=context)
            header, search = layout.panel('wfrl_x100_installation_search', default_closed=True)
            header.label(text='安装覆盖搜索')
            if search is not None:
                _property(search, scene, 'wfrl_x100_search_path', narrow=narrow)
                search.operator('wfrl.x100_search_apply', icon='IMPORT')
                message(search, '读取完整源网格的搜索报告；仅适用于同一几何来源', context=context)
                message(search, '仅应用安装姿态；采样预算保留当前设置', context=context)
            fov = layout.column(align=True)
            fov.use_property_split = False
            fov.prop(scene, 'wfrl_x100_fov')
            message(fov, f"显示长度：{schema.DISPLAY_DEFAULTS['fov_length_m']:g} m · 即时显示", 'NONE', context=context)
            _notes(layout, 'wfrl_x100_simulation_notes', (
                'T1 · 保存运动 · 几何遮挡近似',
                '安装坐标：+X 前、+Y 左、+Z 上',
                '外壳近似；相位中心未知；无 RCS/SNR 模型'), context=context)
        elif scene.wfrl_x100_source == 'REPLAY':
            message(layout, '路径与单帧声明：应用后生效', 'INFO', context=context)
            _property(layout, scene, 'wfrl_x100_path', narrow=narrow)
            _property(layout, scene, 'wfrl_x100_ply_source', narrow=narrow)
            layout.separator()
            _property(layout, scene, 'wfrl_x100_replay_s', narrow=narrow)
            if narrow:
                layout.operator('wfrl.x100_play', text='播放 / 暂停', icon='PLAY')
            row = layout.row(align=True)
            row.use_property_split = False
            row.operator('wfrl.x100_step', text='', icon='FRAME_PREV').direction = -1
            if not narrow:
                row.operator('wfrl.x100_play', text='播放 / 暂停', icon='PLAY')
            row.operator('wfrl.x100_step', text='', icon='FRAME_NEXT').direction = 1
            _notes(layout, 'wfrl_x100_replay_notes', (
                '单帧 PLY 或含 manifest 的显式索引目录',
                '仅单帧需要声明来源；记录目录保留原始来源',
                '实测未知轴/时间：原始点云，未与风机同步'), context=context)
        else:
            message(layout, '端口与过期阈值：应用后生效', 'INFO', context=context)
            _property(layout, scene, 'wfrl_x100_port', narrow=narrow)
            _property(layout, scene, 'wfrl_x100_stream', narrow=narrow)
            layout.label(text='显示流：即时选择', icon='INFO')
            _property(layout, scene, 'wfrl_x100_stale_s', narrow=narrow)
            _notes(layout, 'wfrl_x100_live_notes', (
                '仅连接 127.0.0.1 独立采集端',
                '厂家 UDP 解码需要匹配固件的协议或 SDK'), context=context)


class WFRL_PT_X100Display(_X100Panel, bpy.types.Panel):
    bl_label = '显示'
    bl_idname = 'WFRL_PT_x100_display'
    bl_parent_id = 'WFRL_PT_x100'

    def draw(self, context):
        from ..radar_x100.runtime import session_for
        from .ui_helpers import message
        scene, session = context.scene, session_for(context.scene)
        layout = self.layout
        layout.enabled = not _busy(scene)
        narrow = _narrow(context)
        layout.use_property_split, layout.use_property_decorate = not narrow, False
        message(layout, '显示设置即时生效', 'INFO', context=context)
        _property(layout, scene, 'wfrl_x100_color', narrow=narrow)
        if scene.wfrl_x100_color == 'VELOCITY':
            _property(layout, scene, 'wfrl_x100_color_scale', narrow=narrow)
            if scene.wfrl_x100_color_scale == 'FIXED':
                _property(layout, scene, 'wfrl_x100_velocity_limit', narrow=narrow)
        if (session and session.frame and session.frame.radial_velocity_m_s is None
                and scene.wfrl_x100_color == 'VELOCITY'):
            message(layout, '此来源没有径向速度；灰色表示未知', context=context)
        layout.operator('wfrl.x100_output', text='打开点云窗口', icon='WINDOW')
        output_error = scene.get('wfrl_x100_output_error')
        if output_error:
            message(layout, '点云窗口：' + output_error, 'ERROR', context=context)

        layout.separator()
        layout.label(text='机舱XZ：点至同刻塔筒侧壁三维距离')
        layout.prop(scene, 'wfrl_x100_analysis_roi_min')
        layout.prop(scene, 'wfrl_x100_analysis_roi_max')
        layout.prop(scene, 'wfrl_x100_analysis_limit')
        layout.label(text='窗口按 5 分析 / R 零载荷参考；目标未关联')
        if session:
            if session.analysis_error:
                message(layout, '分析：'+session.analysis_error, context=context)
            result=session.analysis_result
            if result is not None:
                layout.label(text=f"原始 {len(result['indices'])} / 有限 {result['finite']} / ROI {result['selected']} / 有效 {result['valid']}")
                q=result['quantiles_m']
                layout.label(text=('距离分位数：N/A' if q is None else
                    '距离 min / p10 / p50 / p90 / max (m)：'+ ' / '.join(f'{v:.3g}' for v in q)))
            if session.render_error:
                message(layout, 'GPU：'+session.render_error, context=context)



class WFRL_PT_X100Blades(_X100Panel, bpy.types.Panel):
    bl_label = '叶片关联与估计'
    bl_idname = 'WFRL_PT_x100_blades'
    bl_parent_id = 'WFRL_PT_x100'

    def draw(self, context):
        from ..radar_x100.runtime import session_for
        from .ui_helpers import message
        scene, layout = context.scene, self.layout
        layout.prop(scene, 'wfrl_x100_blade_tracking')
        session = session_for(scene)
        message(layout, '研究模型：已知根部姿态与名义叶尖半径；机舱XZ显示拟合段', context=context)
        if not session or not scene.wfrl_x100_blade_tracking:
            return
        if session.blade_error:
            message(layout, session.blade_error, 'INFO', context=context)
        result = session.blade_result
        if result is None:
            return
        labels = dict(INSUFFICIENT_POINTS='点数不足', INSUFFICIENT_SPAN='覆盖长度不足',
            INSUFFICIENT_BINS='跨度分布不足', ILL_CONDITIONED='拟合不稳定',
            EXTRAPOLATION_TOO_FAR='外推过远', UNSTABLE_FIT='拟合误差过大', SUPPORTED='观测支持')
        for row in result['blades']:
            box = layout.box().column(align=True)
            box.label(text=f"B{row['blade']} · {labels[row['status']]} · {row['points']} 点")
            if row['tip_world'] is not None:
                value = row.get('estimated_clearance_m')
                box.label(text='模型估计塔距：' + (f'{value:.3f} m' if value is not None else '未知'))
                box.label(text=f"连续有效估计 {row['consecutive_frames']} 次 · 拟合残差 {row['fit_residual_m']:.3f} m")
            if row['extrapolation_m'] is not None:
                box.label(text=f"叶长方向外推 {row['extrapolation_m']:.2f} m")
        message(layout, '拟合离散度不代表实机精度；缺测时不沿用旧净空', context=context)
        message(layout, '估计序列允许相邻有效估计间有短间隔，计数不代表逐帧覆盖', context=context)


class WFRL_PT_X100Frame(_X100Panel, bpy.types.Panel):
    bl_label = '帧与数据流'
    bl_idname = 'WFRL_PT_x100_frame'
    bl_parent_id = 'WFRL_PT_x100'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        from ..radar_x100.runtime import session_for
        session = session_for(context.scene)
        return bool(session and (session.frame or session.client))

    def draw(self, context):
        from ..radar_x100.runtime import session_for
        from ..radar_x100.display import source_label
        session = session_for(context.scene)
        layout = self.layout.column(align=True)
        if session.frame:
            frame = session.frame
            layout.label(text=f'{source_label(frame)} / {frame.stream} / #{frame.sequence}')
            layout.label(text=f'{frame.validity} · {len(frame.points)} 点 · 非有限坐标 {int((~frame.finite_mask).sum())}')
            layout.label(text=f'时间质量：{frame.time.quality} / {frame.time.timestamp_reference}')
            layout.label(text=(f'源时间：{frame.time.source_time_s:.3f} s'
                               if frame.time.source_time_s is not None else '源时间：未知'))
            layout.label(text='时钟域：' + frame.time.clock_domain[:50])
        if session.client:
            for name, stream in session.client.state.streams.items():
                age = time.monotonic() - stream.received_monotonic
                box = self.layout.box().column(align=True)
                box.label(text=f'{name} {session.client.state.status(name)} · 年龄 {age:.2f}s')
                box.label(text=f'显示替换 {stream.display_drops} · 重复 {stream.duplicates} · 序号缺口 {stream.gaps}')


class WFRL_PT_X100Record(_X100Panel, bpy.types.Panel):
    bl_label = '记录'
    bl_idname = 'WFRL_PT_x100_record'
    bl_parent_id = 'WFRL_PT_x100'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        from ..radar_x100.runtime import session_for
        session = session_for(context.scene)
        return bool((session and session.mode != 'LIVE') or context.scene.get('wfrl_x100_record_state'))

    def draw_header(self, context):
        from ..radar_x100.runtime import session_for
        session = session_for(context.scene)
        self.layout.label(text='', icon='REC' if session and session.fanout else 'NONE')

    def draw(self, context):
        from ..radar_x100.runtime import session_for
        from .ui_helpers import message
        scene, session = context.scene, session_for(context.scene)
        layout = self.layout
        layout.enabled = not _busy(scene)
        narrow = _narrow(context)
        layout.use_property_split, layout.use_property_decorate = not narrow, False
        controls = layout.column()
        controls.enabled = bool(session and session.mode != 'LIVE'
                                and not (session.closing or session.recording_stopping))
        _property(controls, scene, 'wfrl_x100_record_path', narrow=narrow)
        active = session is not None and session.fanout is not None
        controls.operator('wfrl.x100_record', text='结束记录' if active else '开始记录',
                          icon='CANCEL' if active else 'REC')
        if active and session.recording_stopping:
            message(layout, '正在结束记录，等待写盘完成', 'TIME', context=context)
        if active:
            message(layout, '正在记录到：' + str(session.fanout.recorder.path), 'NONE', context=context)
        state = session.fanout.recorder.state if active else scene.get('wfrl_x100_record_state')
        if state:
            message(layout, ('记录：' if active else '上次记录：') + state, context=context)
        if scene.get('wfrl_x100_record_error'):
            message(layout, scene['wfrl_x100_record_error'], 'ERROR', context=context)
        layout.label(text='仅记录实际采样帧；非完整 30 Hz 导出', icon='INFO')


PROPERTIES = ('source', 'mount', 'location', 'rotation', 'candidates', 'fov', 'path', 'ply_source', 'replay_s',
              'port', 'stream', 'stale_s', 'color', 'color_scale', 'velocity_limit', 'record_path', 'enabled', 'analysis_roi_min', 'analysis_roi_max', 'analysis_limit', 'search_path', 'blade_tracking')


def replay_seek(scene, context=None):
    from ..radar_x100.runtime import session_for
    session = session_for(scene)
    if session is not None and not getattr(session, '_advancing_replay', False):
        session.playing = False
        session.replay_cursor = None
        session.replay_time_s = float(scene.wfrl_x100_replay_s)


def ply_path_changed(scene, context=None):
    scene.wfrl_x100_ply_source = 'UNDECLARED'


def register_properties():
    from ..radar_x100.scene import ORIGINAL_LOCATION, ORIGINAL_ROTATION
    from ..radar_x100.core import schema
    p = bpy.props
    definitions = dict(
        source=p.EnumProperty(name='数据源', items=(('SIMULATION','几何仿真','保存运动的几何观测'),
            ('REPLAY','文件回放','实测或仿真文件保留原始来源'),('LIVE','实时输入','独立本地雷达通道'))),
        mount=p.EnumProperty(name='挂载', items=(('NACELLE','T1 机舱','跟随同源机舱'),
            ('TOWER_BASE','T1 塔基','相对塔基固定'),('WORLD','世界固定','世界坐标'))),
        location=p.FloatVectorProperty(name='安装位置 (m)', default=ORIGINAL_LOCATION, size=3),
        rotation=p.FloatVectorProperty(name='安装旋转 (°)', description='安装坐标系的 XYZ 欧拉角，单位为度（°）',
            default=ORIGINAL_ROTATION, size=3),
        candidates=p.IntProperty(name='采样预算', description='下一新样本读取的材料表面候选预算；不等于输出点数', default=512, min=1, max=schema.PROFILE['max_points']),
        fov=p.BoolProperty(name='名义视场', description=f"即时显示名义视场，长度 {schema.DISPLAY_DEFAULTS['fov_length_m']:g} m，不代表探测结果或量程", default=True),
        path=p.StringProperty(name='PLY / 记录目录', subtype='FILE_PATH', update=ply_path_changed),
        ply_source=p.EnumProperty(name='单帧 PLY 来源', default='UNDECLARED', items=(
            ('UNDECLARED', '请选择来源', 'PLY 本身不包含来源信息，需根据实际生成方式声明'),
            ('GEOMETRY_SIMULATION', '几何仿真', '来自几何仿真的单帧文件'),
            ('MEASURED_REPLAY', '实测文件', '用户声明来自设备测量，不代表已经验证设备兼容或标定'),
            ('TEST', '测试数据', '合成测试输入，不作为实测'))),
        replay_s=p.FloatProperty(name='回放时间 (s)', default=0, min=0, update=replay_seek),
        port=p.IntProperty(name='本地端口', default=9877, min=1024, max=65535),
        stream=p.EnumProperty(name='显示流', items=tuple((v,v,'独立流') for v in ('UNKNOWN','NEAR','FAR','COMBINED','TRACK','GEOMETRY'))),
        stale_s=p.FloatProperty(name='过期阈值 (s)', default=.5, min=.01),
        color=p.EnumProperty(name='着色', items=tuple((key,label,'未知量显示灰色') for key,label in
            (('RANGE','距离'),('HEIGHT','高度'),('RCS','RCS'),('SNR','SNR'),('MS','动静'),('VELOCITY','径向速度')))),
        color_scale=p.EnumProperty(name='速度色标', default='FIXED', items=(
            ('FIXED', '固定对称', '固定以零为中心的显示范围，超出范围的数值使用端点颜色'),
            ('FRAME', '当前帧', '按当前帧的最小值与最大值显示'))),
        velocity_limit=p.FloatProperty(name='速度色标 ± (m/s)', default=10., min=.001,
            description='仅用于显示的速度范围，不代表硬件速度限制'),
        record_path=p.StringProperty(name='新记录目录', description='开始下一次记录时使用；已有记录继续写入其原目录', subtype='DIR_PATH'),
        analysis_roi_min=p.FloatVectorProperty(name='机舱 ROI 最小 (m)',size=3,default=(-100,-100,-150)),
        analysis_roi_max=p.FloatVectorProperty(name='机舱 ROI 最大 (m)',size=3,default=(100,100,50)),
        analysis_limit=p.FloatProperty(name='塔距色标上限 (m)',default=10,min=.001,
            description='研究显示配置，不是安全阈值'),
        search_path=p.StringProperty(name='安装搜索报告', subtype='FILE_PATH'),
        blade_tracking=p.BoolProperty(name='启用叶片关联与估计', default=True,
            description='从观测拟合叶片段；点数、覆盖或外推条件不满足时不输出叶尖估计'),
        enabled=p.BoolProperty(default=False))
    for key, definition in definitions.items():
        setattr(bpy.types.Scene, 'wfrl_x100_' + key, definition)


def unregister_properties():
    for name in PROPERTIES:
        if hasattr(bpy.types.Scene, 'wfrl_x100_' + name):
            delattr(bpy.types.Scene, 'wfrl_x100_' + name)


CLASSES = (WFRL_OT_X100Apply, WFRL_OT_X100Close, WFRL_OT_X100MountPreset, WFRL_OT_X100SearchApply, WFRL_OT_X100Step, WFRL_OT_X100Play, WFRL_OT_X100Record, WFRL_OT_X100Output,
           WFRL_PT_X100, WFRL_PT_X100Setup, WFRL_PT_X100Display, WFRL_PT_X100Blades, WFRL_PT_X100Frame, WFRL_PT_X100Record)
