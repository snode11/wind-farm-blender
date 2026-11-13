"""Scene-bound controls for an independent background animation render."""
from pathlib import Path
import logging

from . import animation_render as service

_SOURCE_SCENE = None
_LAST_DISPLAY = None
_LOG = logging.getLogger(__name__)


def _publish():
    global _LAST_DISPLAY
    current = service.job
    display = (current.state, current.completed_frames, current.total_frames, current.error, current.message)
    if display == _LAST_DISPLAY:
        return
    try:
        if _SOURCE_SCENE is not None:
            _SOURCE_SCENE['wfrl_animation_render_status'] = current.message
            _SOURCE_SCENE['wfrl_animation_render_directory'] = str(current.directory or '')
            from . import viewport
            viewport.tag_redraw((_SOURCE_SCENE,))
        _LAST_DISPLAY = display
    except ReferenceError:
        pass


def _monitor_error(exc):
    detail = f'后台渲染状态更新失败，将重试：{exc}'
    if detail not in service.job.error:
        service.job.error = '\n'.join(filter(None, (service.job.error, detail)))
        _LOG.exception('Animation render monitoring failed')


def _publish_safely():
    try:
        _publish()
        return True
    except Exception as exc:
        _monitor_error(exc)
        return False


def _ensure_poll_timer():
    import bpy
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=.1, persistent=True)


def _poll():
    global _SOURCE_SCENE
    try:
        if service.job.active and _SOURCE_SCENE is not None:
            try:
                _SOURCE_SCENE.frame_current
            except ReferenceError:
                _SOURCE_SCENE = None
                service.job.cancel()
        service.job.poll()
    except Exception as exc:
        _monitor_error(exc)
    published = _publish_safely()
    return .25 if service.job.active or not published else None


def _recover(scene):
    global _SOURCE_SCENE, _LAST_DISPLAY
    if service.job.active or scene is _SOURCE_SCENE:
        return
    directory = scene.get('wfrl_animation_render_directory')
    if directory and service.job.recover(directory):
        _SOURCE_SCENE, _LAST_DISPLAY = scene, None
        _publish_safely()
        if service.job.active:
            _ensure_poll_timer()


def restore(_unused=None):
    import bpy
    scene = bpy.context.scene
    if scene is not None:
        _recover(scene)


def start(context, directory):
    global _SOURCE_SCENE, _LAST_DISPLAY
    import bpy
    scene = context.scene
    if scene.camera is None:
        raise ValueError('请先选择用于渲染的场景相机')
    _recover(scene)
    if service.job.active:
        raise ValueError('已有动画正在后台渲染，请等待完成或取消')
    service.validate_output_nodes(scene)
    service.png_output_views(scene)
    from . import custom_camera_capture
    if custom_camera_capture.active():
        raise ValueError('请先完成或取消相机图像采集')

    def snapshot(path):
        bpy.data.libraries.write(str(path), {scene}, path_remap='ABSOLUTE', compress=True)

    previous_directory = service.job.directory
    had_timer = bpy.app.timers.is_registered(_poll)
    _ensure_poll_timer()
    try:
        from .native_camera_views import capture_quality
        with capture_quality(scene):
            result = service.job.start(bpy.path.abspath(str(directory)), blender=bpy.app.binary_path,
                addon_root=Path(__file__).resolve().parent, scene_name=scene.name,
                frame_start=scene.frame_start, frame_end=scene.frame_end, frame_step=scene.frame_step,
                snapshot_writer=snapshot, render_settings=_render_settings(scene))
    except Exception as exc:
        if service.job.directory != previous_directory:
            _SOURCE_SCENE, _LAST_DISPLAY = scene, None
            if service.job.active:
                service.job._start_error = service.job._start_error or str(exc)
                service.job.cancel()
            _publish_safely()
        if not service.job.active and not had_timer and bpy.app.timers.is_registered(_poll):
            bpy.app.timers.unregister(_poll)
        raise
    _SOURCE_SCENE, _LAST_DISPLAY = scene, None
    _publish_safely()
    return result


def _render_settings(scene):
    """Freeze output quality, using 16-bit PNG for higher precision sources."""
    groups = {
        'render': (scene.render, ('engine', 'resolution_x', 'resolution_y', 'resolution_percentage',
            'pixel_aspect_x', 'pixel_aspect_y', 'fps', 'fps_base', 'film_transparent', 'use_motion_blur',
            'preview_pixel_size')),
        'display_settings': (scene.display_settings, ('display_device',)),
        'view_settings': (scene.view_settings, ('view_transform', 'look', 'exposure', 'gamma')),
        'image_settings': (scene.render.image_settings, ('color_mode', 'color_depth', 'compression')),
        'eevee': (scene.eevee, ('taa_render_samples', 'taa_samples', 'use_shadows', 'use_fast_gi')),
    }
    if hasattr(scene, 'cycles'):
        groups['cycles'] = (scene.cycles, ('samples', 'use_denoising'))
    settings = {name: {key: getattr(target, key) for key in keys if hasattr(target, key)}
                for name, (target, keys) in groups.items()}
    image = settings['image_settings']
    if 'color_depth' in image:
        image['color_depth'] = service.png_color_depth(image['color_depth'])
    return settings


def draw(layout, context):
    from .panels.ui_helpers import message, output_directory
    if service.job.directory is None:
        return
    if context.scene != _SOURCE_SCENE:
        if service.job.active:
            layout.label(text='另一场景正在后台渲染动画', icon='INFO')
        return
    current = service.job
    message(layout, current.message, 'ERROR' if current.state == 'FAILED' else 'RENDER_ANIMATION', context=context)
    layout.label(text=f'{current.completed_frames}/{current.total_frames} 帧 · {current.state}')
    if current.active:
        layout.operator('wfrl.cancel_render_animation', text='取消后台动画渲染', icon='CANCEL')
    elif current.directory:
        message(layout, '输出：' + str(current.directory), context=context)
        output_directory(layout, current.directory, is_directory=True)
    if current.error:
        from .panels.diagnostics import draw_diagnostic
        draw_diagnostic(layout, '后台动画渲染', current.error, error=current.state == 'FAILED')


def close(_unused=None):
    global _SOURCE_SCENE
    import bpy
    try:
        service.job.close()
    finally:
        _publish_safely()
        _SOURCE_SCENE = None
        if bpy.app.timers.is_registered(_poll):
            bpy.app.timers.unregister(_poll)
        if service.job.active:
            _ensure_poll_timer()


def register():
    import bpy
    from bpy.app.handlers import persistent
    persistent(close)
    persistent(restore)
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
        if close not in handlers:
            handlers.append(close)
    if restore not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(restore)
    if not bpy.app.timers.is_registered(restore):
        bpy.app.timers.register(restore, first_interval=.1)


def unregister():
    import bpy
    try:
        close()
    finally:
        if bpy.app.timers.is_registered(restore):
            bpy.app.timers.unregister(restore)
        if restore in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(restore)
        for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
            if close in handlers:
                handlers.remove(close)
