"""Applied source settings, separate from immediate display and next-sample settings."""
from pathlib import Path

SOURCE_LABELS = dict(SIMULATION='几何仿真', REPLAY='文件回放', LIVE='实时输入')
FRAME_SOURCE_LABELS = dict(GEOMETRY_SIMULATION='几何仿真', MEASURED_REPLAY='实测文件',
                           MEASURED_LIVE='实测实时', TEST='测试数据')
FIELD_LABELS = dict(source='数据源', mount='挂载', location='安装位置', rotation='安装旋转',
                    path='回放路径', ply_source='单帧 PLY 来源', port='本地端口', stale_s='过期阈值')
MOUNT_LABELS = dict(NACELLE='T1 机舱', TOWER_BASE='T1 塔基', WORLD='世界固定')


def capture(scene, *, abspath, single_ply=False):
    mode = scene.wfrl_x100_source
    values = dict(source=mode)
    fields = dict(SIMULATION=('mount', 'location', 'rotation'),
                  REPLAY=('path', 'ply_source') if single_ply else ('path',),
                  LIVE=('port', 'stale_s'))[mode]
    for field in fields:
        value = getattr(scene, 'wfrl_x100_' + field)
        if field == 'path':
            value = str(Path(abspath(value)).expanduser())
        elif field in {'location', 'rotation'}:
            value = tuple(value)
        values[field] = value
    return values


def pending(scene, applied, *, abspath):
    if scene.wfrl_x100_source != applied['source']:
        return ('数据源',)
    edited = capture(scene, abspath=abspath, single_ply='ply_source' in applied)
    return tuple(FIELD_LABELS[field] for field, value in applied.items() if edited[field] != value)


def summary(applied):
    mode = applied['source']
    lines = ['已应用：' + SOURCE_LABELS[mode]]
    if mode == 'SIMULATION':
        lines.append('挂载：' + MOUNT_LABELS[applied['mount']])
        for field, unit in (('location', 'm'), ('rotation', '°')):
            lines.append(FIELD_LABELS[field] + '：' + ' / '.join(f'{v:.4g}' for v in applied[field]) + ' ' + unit)
    elif mode == 'REPLAY':
        lines.append('路径：' + applied['path'])
        if 'ply_source' in applied:
            lines.append('单帧声明：' + FRAME_SOURCE_LABELS[applied['ply_source']])
    else:
        lines.append(f"连接：127.0.0.1:{applied['port']} · 过期 {applied['stale_s']:.3g} s")
    return lines
