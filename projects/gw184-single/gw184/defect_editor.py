"""Independent GW184 defect editor. No main extension registration or render on edit."""
import copy
import json
import math
import time
from pathlib import Path
import bpy
from bpy.app.handlers import persistent
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from .model import PROJECT, read_config, boundaries
from .scene import build, at_time
from .reference_surface import ReferenceSurface
from .defect_schema import (KIND_DESCRIPTIONS, KIND_LABELS, PRESETS,
                            new_document, new_defect, validate_document)
from .defect_shapes import lightning_crater
from .defect_store import DefectStore
from .defect_geometry import DefectGeometry

_SESSIONS = {}
_UPDATING = False


def session(context=None):
    return _SESSIONS.get((context or bpy.context).scene.as_pointer())


def redraw_editor(context):
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class EditorSession:
    def __init__(self, document=None, *, presets=False, all_presets=False):
        g, m, c = read_config()
        self.surface = ReferenceSurface(g)
        self.rig = build(g, m, c, width=c['resolution'][0], apply_legacy_defects=False)
        self.scene = self.rig['scene']
        self.scene['blade_defect_editor'] = True
        self.scene['render_authorized'] = False
        self.geometry = DefectGeometry(self.rig, self.surface)
        self.payload = None
        self.records = None
        self.preview = None
        self.draft = None
        self.edit_state = None
        self.helpers = []
        self.commit_times = []
        self.busy = False
        self.healthy_payload = None
        self.camera_signature = self.fixed_camera_signature()
        doc = document if document is not None else new_document(self.surface)
        # Explicitly supplied configurations, including empty ones, are loaded
        # exactly as saved. Sample flags only seed a newly created document.
        if document is None and (presets or all_presets):
            kinds = PRESETS if all_presets else ('fine_crack', 'open_crack')
            doc['defects'].extend(new_defect(self.surface, kind) for kind in kinds)
        self.store = DefectStore(self.surface, prepare=self.geometry.prepare,
                                 activate=self._activate, dispose=self.geometry.dispose)
        self.commit_document(doc)
        self.scene['editor_geometry'] = json.dumps(g)
        self.scene['editor_motion'] = json.dumps(m)
        self.scene['editor_cameras'] = json.dumps(c)
        self.healthy_tree = BVHTree.FromPolygons(self.surface.vertices, self.surface.triangles, all_triangles=True)
        _SESSIONS[self.scene.as_pointer()] = self
        self.set_time(100.)
        if hasattr(self.scene, 'gw184_defects'):
            import uuid
            output = PROJECT.parents[1]/'outputs/blade-defects'/('editor-'+uuid.uuid4().hex[:8])
            self.scene.gw184_defects.config_path = str(output/'defects.json')
            self.scene.gw184_defects.output_path = str(output/'run')

    def fixed_camera_signature(self):
        return [(tuple(tuple(row) for row in camera.matrix_world), camera.data.lens,
                 camera.data.sensor_width, camera.data.sensor_height, camera.data.sensor_fit,
                 camera.data.shift_x, camera.data.shift_y) for camera in self.rig['cameras']]

    def verify_cameras(self):
        if self.fixed_camera_signature() != self.camera_signature:
            raise ValueError('固定相机安装或镜头已被修改；请重新打开场景以恢复冻结布局')

    def commit_document(self, document):
        token = self.store.prepare(document)
        try:
            return self.store.commit(token)
        except BaseException:
            if not token.consumed:
                self.store.cancel(token)
            raise

    def _activate(self, payload, document, version):
        self.geometry.activate(payload, document, version)
        self.payload = payload
        self.records = None
        self.scene['defect_document'] = json.dumps(document, allow_nan=False)
        self.scene['defect_state_version'] = version
        self.scene['defect_analysis_status'] = 'NOT_EVALUATED'
        self.update_helpers(document)

    def update_helpers(self, document=None):
        for obj in self.helpers:
            if obj.name in bpy.data.objects:
                data = obj.data if obj.type == 'MESH' else None
                bpy.data.objects.remove(obj, do_unlink=True)
                if data is not None and data.users == 0 and data.get('gw184_editor_helper'):
                    bpy.data.meshes.remove(data)
        self.helpers = []
        document = document or self.store.document
        props = getattr(self.scene, 'gw184_defects', None)
        hidden = not props or not props.show_helpers or self.healthy_payload is not None

        def add_helper(obj, defect):
            self.rig['collection'].objects.link(obj)
            obj.parent = self.rig['blades'][defect['blade_id'] - 1]
            obj.hide_render = True
            obj.show_in_front = True
            obj['gw184_defect_role'] = 'editor_marker'
            obj['defect_diagnostic'] = True
            obj['defect_id'] = defect['id']
            obj.hide_viewport = hidden
            self.helpers.append(obj)

        def add_outline(defect, shape, label):
            sampled = self.surface.sample_shape(shape)
            points = sampled['left'] + list(reversed(sampled['right']))
            mesh = bpy.data.meshes.new(f"Defect {label} outline {defect['id']}")
            mesh['gw184_editor_helper'] = True
            mesh.from_pydata(points, [(i, (i + 1) % len(points)) for i in range(len(points))], [])
            mesh.update()
            obj = bpy.data.objects.new(mesh.name, mesh)
            obj.display_type = 'WIRE'
            obj['gw184_defect_outline'] = label
            add_helper(obj, defect)

        for d in document['defects']:
            if not d['enabled']:
                continue
            obj = bpy.data.objects.new('Defect marker ' + d['id'], None)
            obj.location = self.surface.point(d['anchor'])
            obj.empty_display_type = 'SPHERE'
            obj.empty_display_size = max(.03, d['shape']['max_width_m'])
            add_helper(obj, d)
            add_outline(d, d, 'support')
            if d['morphology'] == 'lightning':
                add_outline(d, lightning_crater(d), 'crater')

    def set_time(self, t):
        global _UPDATING
        if not math.isfinite(t) or not 0 <= t <= boundaries(self.rig['motion'])[-1]:
            raise ValueError('Time outside motion timeline')
        _UPDATING = True
        try:
            f = 1 + t * self.rig['motion']['fps']
            self.scene.frame_set(math.floor(f), subframe=f - math.floor(f))
            at_time(self.rig, t)
            self.scene['defect_time_s'] = t
            self.records = None
            self.scene['defect_analysis_status'] = 'NOT_EVALUATED'
        finally:
            _UPDATING = False

    def begin(self, index=None, kind='fine_crack'):
        if self.draft is not None:
            raise ValueError('请先确认或取消当前编辑')
        if index is not None and not 0 <= index < len(self.store.document['defects']):
            raise ValueError('列表序号不存在，请选择已有缺陷或新建')
        playing = bool(bpy.context.screen and bpy.context.screen.is_animation_playing)
        if playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        self.edit_state = (float(self.scene.get('defect_time_s', 0)), playing)
        self.draft = copy.deepcopy(self.store.document)
        if index is None:
            self.draft['defects'].append(new_defect(self.surface, kind))
            index = len(self.draft['defects']) - 1
        self.draft_index = index
        return self.draft['defects'][index]

    def discard_preview(self):
        if self.preview is not None:
            self.geometry.activate(self.payload)
            self.store.cancel(self.preview)
            self.preview = None
            self.update_helpers(self.store.document)

    def preview_draft(self):
        self.discard_preview()
        self.preview = self.store.prepare(self.draft)
        self.geometry.activate(self.preview.resource)
        self.update_helpers(self.draft)

    def finish(self, commit=False):
        if self.draft is None:
            return
        if commit:
            self.discard_preview()
            started = time.perf_counter()
            self.commit_document(self.draft)
            self.commit_times.append(time.perf_counter() - started)
        else:
            self.discard_preview()
        self.draft = None
        t, playing = self.edit_state
        self.edit_state = None
        self.set_time(t)
        if playing and bpy.context.screen and not bpy.context.screen.is_animation_playing:
            bpy.ops.screen.animation_play()

    def mutate(self, operation, index):
        if self.draft is not None:
            raise ValueError('请先确认或取消编辑')
        doc = copy.deepcopy(self.store.document)
        item = doc['defects'][index]
        if operation == 'DELETE':
            del doc['defects'][index]
        elif operation == 'TOGGLE':
            item['enabled'] = not item['enabled']
            item['revision'] += 1
        elif operation == 'COPY':
            copied = copy.deepcopy(item)
            import uuid
            copied.update(id='D' + uuid.uuid4().hex[:12], revision=1, enabled=False)
            doc['defects'].append(copied)
        self.commit_document(doc)

    def save(self, path):
        if self.draft is not None:
            raise ValueError('请先确认或取消编辑')
        from .defect_outputs import write_json
        write_json(Path(path), self.store.document)

    def load(self, path):
        if self.draft is not None:
            raise ValueError('请先确认或取消编辑')
        doc = validate_document(json.loads(Path(path).read_text()), self.surface)
        self.store.restore(doc)

    def locate_ray(self, origin, direction, blade_id):
        blade = self.rig['blades'][blade_id - 1]
        inverse = blade.matrix_world.inverted()
        p, n, _, _ = self.healthy_tree.ray_cast(inverse @ Vector(origin),
                            (inverse.to_3x3() @ Vector(direction)).normalized())
        if p is None:
            raise ValueError('未命中所选叶片的健康参考表面')
        return self.surface.locate(tuple(p))

    def closeup(self, index, oblique=False):
        d = (self.draft or self.store.document)['defects'][index]
        frame = self.surface.frame(d['anchor'])
        bpy.context.view_layer.update()
        blade = self.rig['blades'][d['blade_id'] - 1]
        target = blade.matrix_world @ Vector(frame['point'])
        direction = blade.matrix_world.to_3x3() @ (Vector(frame['normal']) +
                       (Vector(frame['e_t']) * .7 if oblique else Vector((0, 0, 0))))
        for area in bpy.context.screen.areas if bpy.context.screen else []:
            if area.type == 'VIEW_3D':
                r = area.spaces.active.region_3d
                area.spaces.active.overlay.show_overlays = True
                r.view_perspective = 'PERSP'
                r.view_location = target
                r.view_distance = max(.6, d['shape']['length_m'] * 2.5)
                r.view_rotation = (-direction).to_track_quat('-Z', 'Y')


@persistent
def frame_changed(scene, depsgraph=None):
    s = _SESSIONS.get(scene.as_pointer())
    if s is None or _UPDATING:
        return
    if s.draft is not None and s.edit_state is not None:
        s.set_time(s.edit_state[0])
        return
    if s.busy:
        s.set_time(s.freeze_time)
        return
    t = (scene.frame_current + scene.frame_subframe - 1) / s.rig['motion']['fps']
    t = min(boundaries(s.rig['motion'])[-1], max(0., t))
    at_time(s.rig, t)
    scene['defect_time_s'] = t
    s.records = None
    scene['defect_analysis_status'] = 'NOT_EVALUATED'


def helper_changed(props, context):
    s = session(context)
    if s:
        for obj in s.helpers:
            obj.hide_viewport = not props.show_helpers or s.healthy_payload is not None


class GW184DefectProperties(bpy.types.PropertyGroup):
    selected: bpy.props.IntProperty(name='列表序号', default=0, min=0)
    kind: bpy.props.EnumProperty(name='类型', items=[
        (kind, KIND_LABELS[kind], KIND_DESCRIPTIONS[kind]) for kind in PRESETS])
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
    time_s: bpy.props.FloatProperty(name='绝对时刻 (s)', default=100., min=0., max=320.)
    scan_start: bpy.props.FloatProperty(name='扫描起点 (s)', default=100., min=0.)
    scan_duration: bpy.props.FloatProperty(name='扫描时长 (s)', default=.1, min=1/30)
    status: bpy.props.StringProperty(default='G 模式；外观尚未审核')


def props_from_defect(p, d):
    p.blade_id, p.side = d['blade_id'], d['anchor']['surface_side']
    p.span, p.u, p.theta = d['anchor']['s_m'], d['anchor']['u'], d['anchor']['theta_deg']
    p.length, p.width = d['shape']['length_m'], d['shape']['max_width_m']
    p.depth, p.seed, p.enabled = d['shape']['depth_m'], d['shape']['seed'], d['enabled']
    p.edge_roughness = d['shape'].get('edge_roughness', 0.)
    p.crater_fraction = d['shape'].get('crater_fraction', PRESETS['lightning']['crater_fraction'])
    p.kind = d['morphology']


def draft_from_props(s, p):
    d = s.draft['defects'][s.draft_index]
    preset = PRESETS[d['morphology']]
    d['blade_id'], d['enabled'] = p.blade_id, p.enabled
    d['anchor'].update(s_m=p.span, u=p.u, surface_side=p.side, theta_deg=p.theta % 360)
    d['shape'].update(length_m=p.length, max_width_m=p.width,
                      depth_m=0. if preset['representation'] == 'appearance' else p.depth, seed=p.seed)
    if 'edge_roughness' in preset:
        d['shape']['edge_roughness'] = p.edge_roughness
    if 'crater_fraction' in preset:
        d['shape']['crater_fraction'] = p.crater_fraction
    old = next((i for i in s.store.document['defects'] if i['id'] == d['id']), None)
    if old:
        d['revision'] = old['revision'] + 1


class GW184_OT_defect_action(bpy.types.Operator):
    bl_idname = 'gw184.defect_action'
    bl_label = '叶片缺陷操作'
    action: bpy.props.StringProperty()

    def execute(self, context):
        s, p = session(context), context.scene.gw184_defects
        try:
            if s is None:
                raise ValueError('请通过独立编辑脚本打开场景')
            if s.busy:
                raise ValueError('扫描进行中；按 Esc 取消后操作')
            if s.healthy_payload is not None and self.action != 'HEALTHY':
                raise ValueError('当前是健康对照，请先返回损伤状态')
            if self.action == 'RETRY_CLEANUP':
                s.store.retry_disposals(strict=True)
            elif self.action in ('NEW', 'EDIT'):
                d = s.begin(None if self.action == 'NEW' else p.selected, p.kind)
                p.selected = s.draft_index
                props_from_defect(p, d)
            elif self.action in ('PREVIEW', 'CONFIRM'):
                draft_from_props(s, p)
                s.preview_draft() if self.action == 'PREVIEW' else s.finish(True)
            elif self.action == 'CANCEL':
                s.finish(False)
            elif self.action in ('COPY', 'DELETE', 'TOGGLE'):
                s.mutate(self.action, p.selected)
            elif self.action in ('UNDO', 'REDO'):
                if s.draft is not None:
                    raise ValueError('请先取消编辑')
                getattr(s.store, self.action.lower())()
            elif self.action == 'SAVE':
                s.save(bpy.path.abspath(p.config_path))
            elif self.action == 'LOAD':
                s.load(bpy.path.abspath(p.config_path))
            elif self.action == 'TIME':
                if s.draft is not None:
                    raise ValueError('编辑期间时刻已冻结')
                s.set_time(p.time_s)
            elif self.action == 'HEALTHY':
                if s.draft is not None:
                    raise ValueError('请先确认或取消编辑')
                if s.healthy_payload is not None:
                    s.geometry.activate(s.payload)
                    s.geometry.dispose(s.healthy_payload)
                    s.healthy_payload = None
                    s.update_helpers()
                else:
                    doc = s.store.document
                    blade_id = doc['defects'][p.selected]['blade_id']
                    for d in doc['defects']:
                        if d['blade_id'] == blade_id:
                            d['enabled'] = False
                    s.healthy_payload = s.geometry.prepare(doc)
                    s.geometry.activate(s.healthy_payload)
                    for marker in s.helpers:
                        marker.hide_viewport = True
            elif self.action in ('CLOSEUP', 'OBLIQUE'):
                s.closeup(p.selected, self.action == 'OBLIQUE')
            elif self.action.startswith('CAMERA'):
                s.scene.camera = s.rig['cameras'][int(self.action[-1]) - 1]
                for area in context.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.spaces.active.overlay.show_overlays = False
                        area.spaces.active.region_3d.view_perspective = 'CAMERA'
            elif self.action == 'ANALYZE':
                if s.draft is not None:
                    raise ValueError('请先确认或取消编辑')
                from .defect_visibility import analyze
                s.verify_cameras()
                s.records = analyze(s.rig, s.payload['supports'], float(s.scene['defect_time_s']),
                                    state_version=s.store.version)
                s.scene['defect_analysis_status'] = 'EVALUATED'
            elif self.action == 'EXPORT_G':
                from .defect_outputs import create_run, save_analysis
                run = create_run(s, bpy.path.abspath(p.output_path), mode='G')
                save_analysis(s, run)
            p.status = '已完成：' + self.action
            return {'FINISHED'}
        except Exception as exc:
            p.status = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            redraw_editor(context)


class GW184_OT_defect_pick(bpy.types.Operator):
    bl_idname = 'gw184.defect_pick'
    bl_label = '点击健康参考表面'

    def invoke(self, context, event):
        s = session(context)
        if s is None or s.draft is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, '先新建或编辑缺陷，再在三维视口中选择位置')
            return {'CANCELLED'}
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set('左键选择健康参考表面；Esc 取消选点')
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            context.workspace.status_text_set(None)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            from bpy_extras import view3d_utils
            region = next(r for r in context.area.regions if r.type == 'WINDOW')
            coord = (event.mouse_x - region.x, event.mouse_y - region.y)
            r3d = context.space_data.region_3d
            origin = view3d_utils.region_2d_to_origin_3d(region, r3d, coord)
            direction = view3d_utils.region_2d_to_vector_3d(region, r3d, coord)
            try:
                p = context.scene.gw184_defects
                a = session(context).locate_ray(origin, direction, p.blade_id)
                p.span, p.u, p.side = a['s_m'], a['u'], a['surface_side']
                context.workspace.status_text_set(None)
                redraw_editor(context)
                return {'FINISHED'}
            except Exception as exc:
                self.report({'WARNING'}, str(exc))
        return {'RUNNING_MODAL'}


class GW184_PT_defects(bpy.types.Panel):
    bl_label = 'GW184 叶片缺陷 · 第一阶段'
    bl_idname = 'GW184_PT_defects'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '叶片缺陷'

    @classmethod
    def poll(cls, context):
        return session(context) is not None

    def draw(self, context):
        s, p, layout = session(context), context.scene.gw184_defects, self.layout
        layout.label(text='实体视口轮廓为辅助；材质外观待审核')
        layout.label(text='合成刚性模型；不代表实机损伤')
        def buttons(items):
            row = layout.row(align=True)
            for code, label in items:
                row.operator('gw184.defect_action', text=label).action = code
        for i, d in enumerate(s.store.document['defects']):
            kind_label = KIND_LABELS[d['morphology']]
            layout.label(text=f"{i}: B{d['blade_id']} {kind_label} {'启用' if d['enabled'] else '禁用'} · r{d['revision']}")
        layout.prop(p, 'selected')
        layout.prop(p, 'show_helpers')
        if s.draft is None:
            layout.prop(p, 'kind')
            buttons([('NEW', '新建'), ('EDIT', '修改'), ('COPY', '复制为禁用')])
            buttons([('TOGGLE', '启用/禁用缺陷'), ('DELETE', '删除')])
            buttons([('UNDO', '撤销'), ('REDO', '重做')])
        else:
            kind = s.draft['defects'][s.draft_index]['morphology']
            preset = PRESETS[kind]
            layout.label(text=KIND_LABELS[kind])
            for name in ('enabled', 'blade_id', 'side', 'span', 'u', 'theta', 'length', 'width'):
                layout.prop(p, name)
            if preset['representation'] != 'appearance':
                layout.prop(p, 'depth')
            else:
                layout.label(text='材质外观缺陷：无几何深度')
            if 'edge_roughness' in preset:
                layout.prop(p, 'edge_roughness')
            if 'crater_fraction' in preset:
                layout.prop(p, 'crater_fraction')
            layout.prop(p, 'seed')
            layout.operator('gw184.defect_pick')
            buttons([('PREVIEW', '预览'), ('CONFIRM', '确认'), ('CANCEL', '取消')])
        buttons([('CLOSEUP', '正面近景'), ('OBLIQUE', '斜侧近景')])
        buttons([('HEALTHY', '返回损伤状态' if s.healthy_payload else '健康对照（目标叶片）')])
        buttons([('CAMERA1', 'C1'), ('CAMERA2', 'C2'), ('CAMERA3', 'C3')])
        layout.prop(p, 'time_s')
        buttons([('TIME', '定位时刻'), ('ANALYZE', 'G 分析当前帧')])
        if s.records:
            for row in s.records:
                if row.get('primary', True):
                    layout.label(text=f"{row.get('camera_id', '')} {row.get('defect_id', '')[:8]}: {row.get('status', row.get('state', '?'))}")
                    width = row.get('width_px') or {}
                    if width.get('max') is not None:
                        layout.label(text=f"投影宽 {width['min']:.3f}–{width['max']:.3f} px；非辨识结论")
        layout.label(text=str(context.scene.get('defect_analysis_status', 'NOT_EVALUATED')))
        layout.prop(p, 'config_path')
        buttons([('SAVE', '保存配置'), ('LOAD', '加载配置')])
        layout.prop(p, 'output_path')
        buttons([('EXPORT_G', '保存 G 验收资料')])
        layout.prop(p, 'scan_start')
        layout.prop(p, 'scan_duration')
        layout.operator('gw184.defect_scan', text='逐帧 G 扫描（Esc 可取消）')
        layout.label(text='按需 A 图：命令行入口')
        layout.label(text='渲染默认关闭；保存配置不会出图')
        if s.store.pending_disposals:
            layout.label(text='缺陷已应用；旧资源待释放', icon='ERROR')
            buttons([('RETRY_CLEANUP', '重试资源释放')])
        layout.label(text=p.status[:60])


class GW184_OT_defect_scan(bpy.types.Operator):
    bl_idname = 'gw184.defect_scan'
    bl_label = '扫描固定三相机'

    def invoke(self, context, event):
        from .defect_timeline import FrameMapping
        from .defect_outputs import create_run
        s, p = session(context), context.scene.gw184_defects
        try:
            if s.busy or s.draft is not None or s.healthy_payload is not None:
                raise ValueError('请先完成编辑、对照或当前扫描')
            self.mapping = FrameMapping(p.scan_start, p.scan_duration, s.rig['motion']['fps'], s.rig['motion']['fps'])
            if p.scan_start+p.scan_duration > 320.+1e-9:
                raise ValueError('扫描超出时间线')
            self.run = create_run(s, bpy.path.abspath(p.output_path), mapping=self.mapping)
            self.saved_time = float(s.scene['defect_time_s'])
            s.freeze_time = self.saved_time
            self.playing = context.screen.is_animation_playing
            if self.playing:
                bpy.ops.screen.animation_cancel(restore_frame=False)
            self.rows, self.index, self.version = [], 0, s.store.version
            self.started = time.perf_counter()
            s.busy = True
            self.timer = context.window_manager.event_timer_add(.05, window=context.window)
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def finish_scan(self, context, cancelled=False, error=None):
        from .defect_outputs import save_records, write_json, update_run, save_review
        from .defect_timeline import summarize_intervals
        s = session(context)
        context.window_manager.event_timer_remove(self.timer)
        s.busy = False
        s.set_time(self.saved_time)
        if self.playing:
            bpy.ops.screen.animation_play()
        complete = not cancelled and error is None and self.index == self.mapping.frame_count
        save_records(self.run, self.rows)
        write_json(self.run['output']/'visible_intervals.json', dict(
            completed=complete, completed_frames=self.index, next_frame_index=None if complete else self.index,
            frame_mapping=self.mapping.to_dict(), error=error,
            intervals=summarize_intervals(self.rows, self.mapping, complete)))
        self.run['manifest']['state'] = 'FAILED' if error else 'G_SCAN_COMPLETE' if complete else 'G_SCAN_PARTIAL'
        self.run['manifest']['products']['visible_intervals'] = dict(state='GENERATED', complete=complete)
        update_run(self.run)
        save_review(s, self.run)
        context.scene.gw184_defects.status = f'扫描 {self.index}/{self.mapping.frame_count} 帧；' + self.run['manifest']['state']
        return {'FINISHED'} if complete else {'CANCELLED'}

    def modal(self, context, event):
        if event.type == 'ESC':
            return self.finish_scan(context, True)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        from .defect_visibility import analyze
        s = session(context)
        try:
            s.verify_cameras()
            if not s.store.accepts_version(self.version):
                return self.finish_scan(context, True, 'STALE_STATE')
            sample = self.mapping.sample(self.index)
            self.rows.extend(analyze(s.rig, s.payload['supports'], sample['time_s'], self.index,
                                     self.run['manifest']['run_id'], self.version))
            self.index += 1
            context.scene.gw184_defects.status = f'扫描 {self.index}/{self.mapping.frame_count}；Esc 取消'
            if self.index == self.mapping.frame_count:
                return self.finish_scan(context)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            return self.finish_scan(context, True, repr(exc))


_CLASSES = (GW184DefectProperties, GW184_OT_defect_action, GW184_OT_defect_pick, GW184_OT_defect_scan, GW184_PT_defects)


def register():
    for cls in _CLASSES:
        if not hasattr(bpy.types, cls.__name__):
            bpy.utils.register_class(cls)
    if not hasattr(bpy.types.Scene, 'gw184_defects'):
        bpy.types.Scene.gw184_defects = bpy.props.PointerProperty(type=GW184DefectProperties)
    if frame_changed not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(frame_changed)
