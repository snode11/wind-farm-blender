"""Bounded, opt-in viewport evidence and actual loaded-package identity."""
from collections import deque
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from time import monotonic
import tomllib

_HANDLE = None
_DRAWS = {}
WINDOW_SECONDS = 2.0
_ERROR = None


def _record_error(exc):
    """Keep failure type only; exception text may contain private values."""
    global _ERROR
    _ERROR = type(exc).__name__


@lru_cache(maxsize=1)
def build_information():
    root = Path(__file__).resolve().parent
    manifest = tomllib.loads((root/'blender_manifest.toml').read_text(encoding='utf-8'))
    record = root/'build-info.json'
    if record.is_file():
        identity = json.loads(record.read_text(encoding='utf-8'))
        build = identity['payload_sha256']
        packaged = True
    else:
        digest = hashlib.sha256()
        for path in sorted(root.rglob('*.py')):
            if '__pycache__' not in path.parts:
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(b'\0')
                digest.update(path.read_bytes())
                digest.update(b'\0')
        build, packaged = digest.hexdigest(), False
    return dict(version=manifest['version'], build=build, packaged=packaged,
        module=__package__, addon_root=str(root))


def observe(key, frame, *, now=None, playing=True, enabled=True):
    """Count distinct frames completed by this viewport, never redraw calls."""
    now = monotonic() if now is None else now
    if not enabled or not playing:
        _DRAWS.pop(key, None)
        return
    if key not in _DRAWS:
        if len(_DRAWS) >= 32:
            oldest = min(_DRAWS, key=lambda route: _DRAWS[route][-1][0])
            del _DRAWS[oldest]
        _DRAWS[key] = deque(maxlen=512)
    rows = _DRAWS[key]
    while rows and now-rows[0][0] > WINDOW_SECONDS:
        rows.popleft()
    if not rows or rows[-1][1] != frame:
        rows.append((now, frame))


def rate(key, *, now=None):
    now = monotonic() if now is None else now
    rows = _DRAWS.get(key)
    if not rows:
        return None
    while rows and now-rows[0][0] > WINDOW_SECONDS:
        rows.popleft()
    if not rows:
        _DRAWS.pop(key, None)
        return None
    if len(rows) < 4 or now-rows[-1][0] > .5:
        return None
    elapsed = rows[-1][0]-rows[0][0]
    return (len(rows)-1)/elapsed if elapsed >= .5 else None


def _key(context):
    return (context.window.as_pointer(), context.scene.as_pointer(), context.area.as_pointer())


def _draw():
    import bpy
    from . import playback
    try:
        context = bpy.context
        if context.window and context.area and context.scene:
            observe(_key(context), context.scene.frame_current+context.scene.frame_subframe,
                playing=playback.is_playing(context.scene),
                enabled=context.scene.wfrl_diagnostics_sampling)
    except Exception as exc:
        _record_error(exc)


def snapshot(context):
    from . import farm_flex, playback
    info = dict(version=None, build=None, packaged=None, module=__package__,
        addon_root=str(Path(__file__).resolve().parent), source_path=None, source_hz=None,
        timeline_hz=None, simulation_time_s=None, viewport_fps=None, sampling=None, playing=None,
        fps_scope='Distinct scene frames observed in this viewport POST_PIXEL over the last 2 seconds; not display scanout',
    )
    errors = []
    try:
        info.update(build_information())
    except Exception as exc:
        errors.append(type(exc).__name__)
    try:
        scene = context.scene
        flex = farm_flex.active_for(scene) if farm_flex.is_active(scene) else None
        info.update(source_path=str(farm_flex.saved_package(scene)) if flex else scene.get('wfrl_farm_flex_path'),
            source_hz=flex.manifest.get('source_fps') if flex else None,
            timeline_hz=scene.get('wfrl_clearance_timebase_fps') if flex else None,
            simulation_time_s=scene.get('wfrl_clearance_time_s'),
            viewport_fps=rate(_key(context)) if context.window and context.area
                and scene.wfrl_diagnostics_sampling and playback.is_playing(scene) else None,
            sampling=scene.wfrl_diagnostics_sampling,
            playing=playback.is_playing(scene))
    except Exception as exc:
        errors.append(type(exc).__name__)
    if errors or _ERROR:
        info['diagnostic_errors'] = errors + ([_ERROR] if _ERROR else [])
    return info


def draw_panel(layout, context):
    try:
        _draw_panel(layout, context)
    except Exception as exc:
        _record_error(exc)
        layout.label(text='诊断信息暂不可用')


def _draw_panel(layout, context):
    from .panels.ui_helpers import message
    info = snapshot(context)
    origin = '未知' if info['packaged'] is None else '安装包' if info['packaged'] else '工作区'
    layout.label(text='版本：'+(info['version'] or '不可用')+' · '+origin)
    layout.label(text='构建：'+(info['build'][:12] if info['build'] else '不可用'))
    message(layout, '模块：'+info['module'], context=context)
    if info['source_path']:
        message(layout, '数据：'+Path(info['source_path']).name, context=context)
    if info['source_hz'] is not None:
        def frequency(value):
            return f'{value:g} Hz' if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else '未知'
        layout.label(text=f"源采样 {frequency(info['source_hz'])} · 时间轴 {frequency(info['timeline_hz'])}")
    if info.get('diagnostic_errors'):
        layout.label(text='部分诊断信息不可用')
    layout.prop(context.scene, 'wfrl_diagnostics_sampling', text='采样当前视口播放帧率')
    value = info['viewport_fps']
    layout.label(text=f'当前视口：{value:.1f} FPS（近 2 秒）' if value is not None
        else '当前视口：暂停 / 未采样 / 样本不足')
    message(layout, '按视口绘制记录；不代表显示器刷新率', context=context)
    layout.operator('wfrl.copy_frontend_diagnostics', text='复制完整诊断信息', icon='COPYDOWN')


def clear():
    global _ERROR
    _DRAWS.clear()
    _ERROR = None


def _remove_draw_handler(bpy):
    global _HANDLE
    handle = getattr(bpy, '_wfrl_diagnostics_handle', None)
    if handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
        except (ReferenceError, ValueError):
            pass
        except Exception as exc:
            _record_error(exc)
            return False
        del bpy._wfrl_diagnostics_handle
    _HANDLE = None
    return True


def register():
    try:
        _register()
    except Exception as exc:
        unregister()
        _record_error(exc)


def _register():
    import bpy
    from bpy.app.handlers import persistent
    global _HANDLE
    if not _remove_draw_handler(bpy):
        return
    old_load = getattr(bpy, '_wfrl_diagnostics_load_handler', None)
    if old_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(old_load)
    _HANDLE = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
    bpy._wfrl_diagnostics_handle = _HANDLE
    persistent(_on_load)
    if _on_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_on_load)
    bpy._wfrl_diagnostics_load_handler = _on_load
    clear()


def _on_load(_unused):
    clear()


def unregister():
    import bpy
    clear()
    _remove_draw_handler(bpy)
    load = getattr(bpy, '_wfrl_diagnostics_load_handler', _on_load)
    if load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(load)
    if hasattr(bpy, '_wfrl_diagnostics_load_handler'):
        del bpy._wfrl_diagnostics_load_handler
