"""Rotor analysis inside the existing recorded-result task pages."""
from .. import clearance_replay, farm_flex
from ..imbalance import METRICS, latest_turn
from .ui_helpers import message, diagnostic


def draw_physical(layout, physical):
    for kind, title in (('mass', '质量不平衡'), ('aerodynamic', '气动不平衡')):
        box = layout.box()
        box.label(text=title)
        value = physical[kind]
        if value['status'] == 'unavailable':
            box.label(text='当前数据包未提供', icon='INFO')
            continue
        box.label(text='仿真参考' if value['source_kind'] == 'simulation_reference' else '模型估计')
        if kind == 'mass':
            box.label(text=f"不平衡量：{value['magnitude_kg_m']:.3f} kg·m")
            phase = value.get('phase_deg')
            box.label(text='方向：未提供' if phase is None else f"方向：{phase:.2f}° · {value['phase_reference']}")
            box.label(text='对应叶片：' + ('、'.join(str(b) for b in value['blades']) or '未指定'))
        else:
            box.label(text={'aerodynamic_twist_offset': '气动扭角偏置', 'pitch_zero_offset': '桨距零偏'}[value['parameter']])
            for b, offset in enumerate(value['blade_offsets_deg'], 1):
                box.label(text=f'叶片 {b}：{offset:+.3f}°')
        message(box, '来源：' + value['provenance'])


def draw(layout, scene):
    active = farm_flex.active_for(scene)
    data = getattr(active, 'imbalance', None)
    if data is None:
        error = getattr(active, 'imbalance_error', '')
        layout.label(text='不平衡分析不可用' if error else '当前数据包未提供不平衡分析', icon='INFO')
        if error:
            diagnostic(layout, '不平衡分析不可用', error, error=True)
        return
    layout.row(align=True).prop(scene, 'wfrl_imbalance_turbine', expand=True)
    tid = scene.wfrl_imbalance_turbine
    row = data['turbines'].get(tid)
    if row is None:
        layout.label(text=tid + ' 未提供分析数据', icon='INFO')
        return
    box = layout.box()
    box.label(text='三叶响应对比 · 离线统计')
    box.label(text='全片段汇总 · 包含当前位置之后的数据', icon='INFO')
    start, end = data['segment_s']
    box.label(text=f'全片段 {start:g}–{end:g} s · {len(row["turns"])} 个完整对齐转')
    box.prop(scene, 'wfrl_imbalance_metric', text='分量')
    key = scene.wfrl_imbalance_metric
    _label, unit, factor = METRICS[key]
    metric = row['metrics'][key]
    box.label(text=f'三叶均值曲线差异：{metric["rms_spread"] * factor:.4f} {unit}')
    grid = box.row(align=True)
    for title, values in (('叶片', ['1', '2', '3']),
                          ('均值 / ' + unit, [f'{v * factor:+.4f}' for v in metric['blade_means']]),
                          ('均值偏差', [f'{v * factor:+.4f}' for v in metric['blade_offsets']])):
        column = grid.column(align=True)
        column.label(text=title)
        for value in values:
            column.label(text=value)
    box.prop(scene, 'wfrl_imbalance_curve', text='显示三叶均值曲线')
    box.label(text='响应差异不直接等于故障程度', icon='INFO')
    value = clearance_replay.sample(scene)
    turn = latest_turn(row, value['time_s']) if value else None
    current = layout.box()
    current.label(text='回放位置 · 最近完整对齐转')
    current.label(text='仅使用已经完整经过的转次', icon='TIME')
    if turn is None:
        current.label(text='尚无完整三叶对齐转')
    else:
        current.label(text=f"覆盖 {turn['start_s']:.2f}–{turn['end_s']:.2f} s")
        current.label(text=f"当转曲线差异：{turn['metrics'][key]['rms_spread'] * factor:.4f} {unit}")
    physical = row['physical']
    if all(physical[kind]['status'] == 'unavailable' for kind in ('mass', 'aerodynamic')):
        message(layout, '质量不平衡、气动不平衡：当前数据包均未提供；三叶响应差异不替代这两类物理指标。')
        header, body = layout.panel('wfrl_imbalance_physical', default_closed=True)
        header.label(text='质量与气动物理指标')
        if body is not None:
            draw_physical(body, physical)
    else:
        draw_physical(layout, physical)
    header, details = layout.panel('wfrl_imbalance_definition', default_closed=True)
    header.label(text='分析口径与来源')
    if details is not None:
        for text in ('逐叶按相同方位角对齐，5° 插值网格，完整转等权平均。',
                     '均值偏差 = 该叶均值 − 三叶均值；曲线差异为三叶相对共同曲线的 RMS。',
                     '全片段汇总包含回放位置之后的数据；当转读数仅使用已经完整经过的转次。',
                     '叶尖使用随变桨的根系 xyz；叶根载荷包含结构动力学作用。',
                     '风况、转速、变桨变化尚未分离；当前无故障阈值。'):
            message(details, text)
        message(details, '保存的 OpenFAST 输出：' + row['source']['file'])
