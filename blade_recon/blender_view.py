"""
blender_view.py —— 在 Blender 里播放重建结果（以及可选的真值虚影）。

    blender --python blender_view.py -- --recon out/synth/recon.json \
            [--truth data/synth/truth.json] [--cameras data/synth/cameras.json]

做法：recon.json 只存每帧的状态向量(约 13 个数)，不存网格。Blender 里用与重建器
同一个 model.Rotor 前向模型逐帧还原三片叶片网格(frame_change 回调)，所以：
  · 播放、拖动时间轴、单步都按帧号即时计算，不需要烘焙；
  · 顶点颜色：绿色 = 该截面被图像观测约束，橙色 = 模型推断(视野外/遮挡)；
  · --truth 给出时，同时显示真值叶片(灰色线框)，可直接目视比较。
坐标：Blender 世界系 = model.py 的机组系(z 上、x 指向上风)。
"""
import math
import os
import sys

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from model import Rotor, TurbineConfig, load_json  # noqa: E402

GREEN = (0.15, 0.75, 0.25, 1.0)
ORANGE = (1.0, 0.55, 0.1, 1.0)


def parse_args():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    out = {'recon': None, 'truth': None, 'cameras': None}
    i = 0
    while i < len(argv):
        k = argv[i].lstrip('-')
        if k in out and i + 1 < len(argv):
            out[k] = argv[i + 1]; i += 2
        else:
            i += 1
    return out


def clear_scene():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)


def make_material(name, use_vcol, color=(0.8, 0.8, 0.8, 1.0), alpha=1.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = color
    if use_vcol:
        attr = nt.nodes.new('ShaderNodeVertexColor')
        attr.layer_name = 'evidence'
        nt.links.new(attr.outputs['Color'], bsdf.inputs['Base Color'])
    if alpha < 1.0:
        bsdf.inputs['Alpha'].default_value = alpha
        m.blend_method = 'BLEND' if hasattr(m, 'blend_method') else m.blend_method
    return m


def make_blade_object(name, rotor, faces, mat, with_color):
    S, N = len(rotor.tpl.r), rotor.cfg.n_ring
    verts = np.zeros((S * N, 3))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts.tolist(), [], faces.tolist())
    me.update()
    if with_color:
        me.color_attributes.new('evidence', 'BYTE_COLOR', 'POINT')
    ob = bpy.data.objects.new(name, me)
    ob.data.materials.append(mat)
    bpy.context.collection.objects.link(ob)
    return ob


def add_context(rotor):
    """塔筒、机舱、轮毂：只作空间参照，不参与重建。"""
    cfg = rotor.cfg
    bpy.ops.mesh.primitive_cylinder_add(radius=2.5, depth=cfg.hub_height_m - 2,
                                        location=(0, 0, (cfg.hub_height_m - 2) / 2))
    bpy.context.object.name = 'Tower'
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-2.0, 0, cfg.hub_height_m))
    nac = bpy.context.object; nac.name = 'Nacelle'; nac.scale = (10, 3.3, 3.3)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=cfg.hub_radius_m + 0.3, location=tuple(rotor.hub))
    bpy.context.object.name = 'Hub'


def add_cameras(cam_json):
    for d in load_json(cam_json)['cameras']:
        T = np.array(d['T_cv_from_world'])
        R, t = T[:3, :3], T[:3, 3]
        C = -R.T @ t
        M = np.eye(4)
        M[:3, :3] = R.T @ np.diag([1, -1, -1])            # CV 相机 → Blender 相机(看 -Z, 上 +Y)
        M[:3, 3] = C
        cd = bpy.data.cameras.new(d['name'])
        K = np.array(d['K'])
        cd.sensor_fit = 'HORIZONTAL'
        cd.angle_x = 2 * math.atan(d['W'] / (2 * K[0, 0]))
        ob = bpy.data.objects.new(d['name'], cd)
        ob.matrix_world = [list(r) for r in M]
        bpy.context.collection.objects.link(ob)
        ob.scale = (3, 3, 3)


def main():
    a = parse_args()
    R = load_json(a['recon'])
    rotor = Rotor(TurbineConfig.from_dict(R['turbine']))
    faces = rotor.tpl.faces()
    S, N = len(rotor.tpl.r), rotor.cfg.n_ring
    rec = [np.array(f['state']) for f in R['frames']]
    seen = [np.array(f['observed_sections'], bool) for f in R['frames']]
    truth = None
    if a['truth']:
        truth = [np.array(f['state']) for f in load_json(a['truth'])['frames']]

    clear_scene()
    add_context(rotor)
    if a['cameras']:
        add_cameras(a['cameras'])
    mat = make_material('BladeEvidence', True)
    blades = [make_blade_object('Recon_Blade%d' % (b + 1), rotor, faces, mat, True) for b in range(3)]
    ghosts = []
    if truth:
        gm = make_material('Truth', False, (0.6, 0.6, 0.6, 1.0))
        for b in range(3):
            g = make_blade_object('Truth_Blade%d' % (b + 1), rotor, faces, gm, False)
            g.display_type = 'WIRE'
            g.hide_render = True
            ghosts.append(g)

    def update(scene, *_):
        k = min(max(scene.frame_current - 1, 0), len(rec) - 1)
        V, _ = rotor.forward(rec[k])
        for b, ob in enumerate(blades):
            ob.data.vertices.foreach_set('co', V[b].reshape(-1).astype(np.float32))
            col = np.where(np.repeat(seen[k][b], N)[:, None], GREEN, ORANGE).astype(np.float32)
            ob.data.color_attributes['evidence'].data.foreach_set('color', col.reshape(-1))
            ob.data.update()
        if truth and k < len(truth):
            Vt, _ = rotor.forward(truth[R['frames'][k]['frame']])
            for b, g in enumerate(ghosts):
                g.data.vertices.foreach_set('co', Vt[b].reshape(-1).astype(np.float32))
                g.data.update()

    bpy.app.handlers.frame_change_pre.clear()
    bpy.app.handlers.frame_change_pre.append(update)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, len(rec)
    sc.render.fps = int(round(R.get('fps', 10)))
    sc.frame_set(1)
    update(sc)
    print('[blade_recon] 已加载 %d 帧；绿色=图像观测，橙色=模型推断' % len(rec))


main()
