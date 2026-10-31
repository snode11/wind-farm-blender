"""X100 controls for source identity, installation, replay and recording health."""
import bpy
import time


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
            session.seek_frame(index)
        return {'FINISHED'}


class WFRL_OT_X100Play(bpy.types.Operator):
    bl_idname = 'wfrl.x100_play'
    bl_label = '文件播放 / 暂停'

    def execute(self, context):
        from ..radar_x100.runtime import session_for
        session = session_for(context.scene)
        if session and session.mode == 'REPLAY' and session.replay:
            start_playing = not getattr(session, 'playing', False)
            session.playing = False
            if start_playing:
                session.replay_cursor = None
                if context.scene.wfrl_x100_replay_s >= session.replay.duration_s:
                    context.scene.wfrl_x100_replay_s = session.replay.times[0]
                session.play_anchor = time.monotonic() - context.scene.wfrl_x100_replay_s
                session.playing = True
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


def draw(layout, context):
    from ..radar_x100.runtime import session_for
    scene, session = context.scene, session_for(context.scene)
    layout.prop(scene, 'wfrl_x100_source')
    if scene.wfrl_x100_source == 'SIMULATION':
        layout.label(text='T1 · 保存运动 · 几何遮挡近似')
        layout.prop(scene, 'wfrl_x100_mount')
        layout.prop(scene, 'wfrl_x100_location')
        layout.prop(scene, 'wfrl_x100_rotation')
        layout.prop(scene, 'wfrl_x100_candidates')
        layout.prop(scene, 'wfrl_x100_fov')
        layout.label(text='安装坐标：+X 前、+Y 左、+Z 上')
        layout.label(text='外壳近似；相位中心未知；无 RCS/SNR 模型')
    elif scene.wfrl_x100_source == 'REPLAY':
        layout.prop(scene, 'wfrl_x100_path')
        layout.label(text='单帧 PLY 或含 manifest 的显式索引目录')
        layout.prop(scene, 'wfrl_x100_replay_s')
        row = layout.row(align=True)
        row.operator('wfrl.x100_step', text='上一帧').direction = -1
        row.operator('wfrl.x100_play', text='播放 / 暂停')
        row.operator('wfrl.x100_step', text='下一帧').direction = 1
        layout.label(text='实测未知轴/时间：原始点云，未与风机同步')
    else:
        layout.prop(scene, 'wfrl_x100_port')
        layout.prop(scene, 'wfrl_x100_stream')
        layout.prop(scene, 'wfrl_x100_stale_s')
        layout.label(text='仅连接 127.0.0.1 独立采集端')
        layout.label(text='厂家 UDP 解码需要匹配固件的协议或 SDK')
    row = layout.row(align=True)
    row.operator('wfrl.x100_apply')
    row.operator('wfrl.x100_close')
    layout.prop(scene, 'wfrl_x100_color')
    layout.label(text=scene.get('wfrl_x100_status', '未启动'))
    if session and session.frame:
        frame = session.frame
        layout.label(text=f'{frame.source_kind} / {frame.stream} / #{frame.sequence}')
        layout.label(text=f'{frame.validity} · {len(frame.points)} 点 · 非有限坐标 {int((~frame.finite_mask).sum())}')
        layout.label(text=f'时间质量：{frame.time.quality} / {frame.time.timestamp_reference}')
        layout.label(text=(f'源时间：{frame.time.source_time_s:.3f} s'
                           if frame.time.source_time_s is not None else '源时间：未知'))
        layout.label(text='时钟域：' + frame.time.clock_domain[:50])
        if frame.radial_velocity_m_s is None and scene.wfrl_x100_color == 'VELOCITY':
            layout.label(text='此来源没有径向速度；灰色表示未知', icon='INFO')
    if session and session.client:
        for name, stream in session.client.state.streams.items():
            age = time.monotonic() - stream.received_monotonic
            layout.label(text=f'{name} {session.client.state.status(name)} · 年龄 {age:.2f}s')
            layout.label(text=f'显示替换 {stream.display_drops} · 重复 {stream.duplicates} · 序号缺口 {stream.gaps}')
    if session and session.mode != 'LIVE':
        layout.prop(scene, 'wfrl_x100_record_path')
        layout.operator('wfrl.x100_record', text='结束记录' if session.fanout else '开始记录')
        layout.label(text='仅记录实际采样帧；非完整 30 Hz 导出')
        if session.fanout:
            layout.label(text='记录：' + session.fanout.recorder.state)
    error = scene.get('wfrl_x100_error')
    if error:
        box = layout.box()
        box.alert = True
        for start in range(0, len(error), 46):
            box.label(text=error[start:start + 46])


PROPERTIES = ('source', 'mount', 'location', 'rotation', 'candidates', 'fov', 'path', 'replay_s',
              'port', 'stream', 'stale_s', 'color', 'record_path', 'enabled')


def replay_seek(scene, context=None):
    from ..radar_x100.runtime import session_for
    session = session_for(scene)
    if session is not None and not getattr(session, '_advancing_replay', False):
        session.playing = False
        session.replay_cursor = None


def register_properties():
    p = bpy.props
    definitions = dict(
        source=p.EnumProperty(name='数据源', items=(('SIMULATION','几何仿真','保存运动的几何观测'),
            ('REPLAY','文件回放','实测或仿真文件保留原始来源'),('LIVE','实时输入','独立本地雷达通道'))),
        mount=p.EnumProperty(name='挂载', items=(('NACELLE','T1 机舱','跟随同源机舱'),
            ('TOWER_BASE','T1 塔基','相对塔基固定'),('WORLD','世界固定','世界坐标'))),
        location=p.FloatVectorProperty(name='安装位置 (m)', default=(-3.5, -1.7, -2.0), size=3),
        rotation=p.FloatVectorProperty(name='安装旋转 (度 XYZ)', default=(0, 82.5, 180), size=3),
        candidates=p.IntProperty(name='材料采样预算', default=512, min=1, max=4096),
        fov=p.BoolProperty(name='名义视场（显示长度 60 m）', default=True),
        path=p.StringProperty(name='PLY / 记录目录', subtype='FILE_PATH'),
        replay_s=p.FloatProperty(name='回放时间 (s)', default=0, min=0, update=replay_seek),
        port=p.IntProperty(name='本地端口', default=9877, min=1024, max=65535),
        stream=p.EnumProperty(name='显示流', items=tuple((v,v,'独立流') for v in ('UNKNOWN','NEAR','FAR','COMBINED','TRACK','GEOMETRY'))),
        stale_s=p.FloatProperty(name='过期阈值 (s)', default=.5, min=.01),
        color=p.EnumProperty(name='着色', items=tuple((key,label,'未知量显示灰色') for key,label in
            (('RANGE','距离'),('HEIGHT','高度'),('RCS','RCS'),('SNR','SNR'),('MS','动静'),('VELOCITY','径向速度')))),
        record_path=p.StringProperty(name='新记录目录', subtype='DIR_PATH'),
        enabled=p.BoolProperty(default=False))
    for key, definition in definitions.items():
        setattr(bpy.types.Scene, 'wfrl_x100_' + key, definition)


def unregister_properties():
    for name in PROPERTIES:
        if hasattr(bpy.types.Scene, 'wfrl_x100_' + name):
            delattr(bpy.types.Scene, 'wfrl_x100_' + name)


CLASSES = (WFRL_OT_X100Apply, WFRL_OT_X100Close, WFRL_OT_X100Step, WFRL_OT_X100Play, WFRL_OT_X100Record)
