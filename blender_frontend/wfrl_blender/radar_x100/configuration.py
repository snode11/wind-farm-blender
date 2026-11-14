"""Applied source settings, separate from immediate display and next-sample settings."""
import json
import math
from pathlib import Path

SOURCE_LABELS = dict(SIMULATION='仿真', REPLAY='文件回放', LIVE='实时输入')
OBSERVATION_LABELS = dict(GEOMETRY='GEOMETRY · 几何可见性', DETECTIONS='DETECTIONS · 工程检测')
DETECTION_INTEGRATION_BUDGET = 16384
# X100 frame rate (measured vendor sequence); simulated samples use this source-time grid.
SAMPLE_RATE_HZ = 33
RATE_LABEL = f'{SAMPLE_RATE_HZ} Hz 瞬时采样假设'
FRAME_SOURCE_LABELS = dict(GEOMETRY_SIMULATION='几何仿真', ENGINEERING_SIMULATION='工程检测仿真', MEASURED_REPLAY='实测文件',
                           MEASURED_LIVE='实测实时', TEST='测试数据')
FIELD_LABELS = dict(source='数据源', observation_mode='观测模式', mount='挂载', location='安装位置', rotation='安装旋转', field_of_view='视场', field_of_view_shape='视场形状',
                    path='回放路径', ply_source='单帧 PLY 来源', port='本地端口', stale_s='过期阈值')
MOUNT_LABELS = dict(NACELLE='T1 机舱', TOWER_BASE='T1 塔基', WORLD='世界固定')
FOV_SHAPE_LABELS = dict(ANGULAR='方位／俯仰角', PROJECTIVE='相机矩形')
SAVED_KEY = 'wfrl_x100_applied_config'
_APPLIED_SCHEMA = 'x100-applied-config.v1'
_FIELDS = dict(SIMULATION=('observation_mode', 'mount', 'location', 'rotation', 'field_of_view', 'field_of_view_shape'),
               REPLAY=('path',), LIVE=('port', 'stale_s'))
_VECTOR_LENGTHS = dict(location=3, rotation=3, field_of_view=2)


def resolve_path(path, *, abspath):
    """Resolve a replay reference against the currently open Blender file."""
    return Path(abspath(path)).expanduser()


def capture(scene, *, abspath, single_ply=False):
    mode = scene.wfrl_x100_source
    values = dict(source=mode)
    fields = _FIELDS[mode] + (('ply_source',) if mode == 'REPLAY' and single_ply else ())
    for field in fields:
        value = getattr(scene, 'wfrl_x100_' + field)
        if field == 'path' and not value.startswith('//'):
            value = str(resolve_path(value, abspath=abspath))
        elif field in _VECTOR_LENGTHS:
            value = tuple(value)
        values[field] = value
    return values


def encode(applied):
    """Save only settings committed by a source session or an installation preset."""
    return json.dumps(dict(schema=_APPLIED_SCHEMA, **applied), allow_nan=False)


def saved(scene):
    """Read the applied snapshot. A missing property identifies a legacy scene."""
    if SAVED_KEY not in scene:
        return None
    try:
        values = json.loads(scene[SAVED_KEY])
        if not isinstance(values, dict) or values.pop('schema') != _APPLIED_SCHEMA:
            raise ValueError
        mode = values['source']
        fields = _FIELDS[mode] + (('ply_source',) if mode == 'REPLAY' and 'ply_source' in values else ())
        if values.keys() != {'source', *fields}:
            raise ValueError
        if mode == 'SIMULATION':
            if (values['mount'] not in MOUNT_LABELS or values['observation_mode'] not in OBSERVATION_LABELS
                    or values['field_of_view_shape'] not in FOV_SHAPE_LABELS):
                raise ValueError
            for key, length in _VECTOR_LENGTHS.items():
                vector = values[key]
                if (not isinstance(vector, list) or len(vector) != length
                        or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
                    raise ValueError
                values[key] = tuple(vector)
        elif mode == 'REPLAY':
            if not isinstance(values['path'], str) or values.get('ply_source', 'TEST') not in FRAME_SOURCE_LABELS:
                raise ValueError
        elif (type(values['port']) is not int or not 1024 <= values['port'] <= 65535
              or type(values['stale_s']) not in (int, float)
              or not math.isfinite(values['stale_s']) or values['stale_s'] <= 0):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError('保存的 X100 已应用配置无效，请重新应用配置') from None
    return values


def pending(scene, applied, *, abspath):
    if scene.wfrl_x100_source != applied['source']:
        return ('数据源',)
    edited = capture(scene, abspath=abspath, single_ply='ply_source' in applied)
    if 'path' in applied:
        draft_path, applied_path = edited['path'], applied['path']
        if (draft_path.startswith('//') == applied_path.startswith('//')
                and resolve_path(draft_path, abspath=abspath) == resolve_path(applied_path, abspath=abspath)):
            edited['path'] = applied_path
    return tuple(FIELD_LABELS[field] for field, value in applied.items() if edited[field] != value)


def summary(applied):
    mode = applied['source']
    lines = ['已应用：' + SOURCE_LABELS[mode]]
    if mode == 'SIMULATION':
        lines.append('已应用模式：' + OBSERVATION_LABELS[applied['observation_mode']])
        lines.append('挂载：' + MOUNT_LABELS[applied['mount']])
        for field, unit in (('location', 'm'), ('rotation', '°')):
            lines.append(FIELD_LABELS[field] + '：' + ' / '.join(f'{v:.4g}' for v in applied[field]) + ' ' + unit)
        if 'field_of_view' in applied:
            shape = FOV_SHAPE_LABELS[applied.get('field_of_view_shape', 'ANGULAR')]
            lines.append('视场：方位 ±{:.4g}° / 俯仰 ±{:.4g}° · {}'.format(*applied['field_of_view'], shape))
    elif mode == 'REPLAY':
        lines.append('路径：' + applied['path'])
        if 'ply_source' in applied:
            lines.append('单帧声明：' + FRAME_SOURCE_LABELS[applied['ply_source']])
    else:
        lines.append(f"连接：127.0.0.1:{applied['port']} · 过期 {applied['stale_s']:.3g} s")
    return lines
