"""Read-only presentation of scene readiness, ownership and backend state."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SceneStatus:
    title: str
    next_step: str
    icon: str = 'INFO'


def scene_status(*, kind='', ready=False, playing=False, load_error='',
                 manual_error='', manual_running=False, busy=''):
    if busy:
        return SceneStatus(busy, '请先完成或取消当前操作，再切换数据或播放。', 'TIME')
    if kind == 'manual':
        if manual_error:
            return SceneStatus('手动控制已保护暂停', '在“手动”页修复原因并检查恢复；恢复后保持暂停。', 'ERROR')
        return SceneStatus('手动刚性运动中' if manual_running else '手动场景 · 已暂停',
                           '在“手动”页调整姿态与转速；此模式不计算功率、载荷或雷达读数。')
    if load_error:
        return SceneStatus('加载失败 · 当前回放仍可用' if ready else '加载失败 · 回放未就绪',
                           '根据下方原因处理路径或占用状态，再重新加载。', 'ERROR')
    if ready:
        return SceneStatus('离线回放 · 播放中' if playing else '离线回放 · 就绪',
                           '主入口：N → WFRL',
                           'PLAY' if playing else 'CHECKMARK')
    if kind == 'live':
        return SceneStatus('后端场景', '在“后端”页查看连接、加载场景与已确认的运行状态。')
    if kind in {'demo', 'clearance_replay'}:
        return SceneStatus('回放未就绪', '请加载离线结果；保存的场景标记不代表数据已恢复。')
    return SceneStatus('未加载 WFRL 场景', '点击“加载 MAPPO 离线回放”，或进入“手动”创建独立场景。')


def backend_status(ui):
    """Local display readiness never stands in for backend confirmation."""
    if ui.connection in {'LOCAL DEMO', 'OFFLINE RESULTS'}:
        return '后端：未连接 · 离线回放无需后端'
    connection = {'CONNECTED': '已连接', 'DISCONNECTED': '未连接',
                  'CONNECTING': '连接中'}.get(ui.connection, ui.connection)
    if not ui.confirmed:
        return f'后端：{connection} · 运行状态未确认'
    if ui.connection != 'CONNECTED':
        return f'后端：{connection}'
    run = {'READY': '就绪', 'STARTING': '启动中', 'RUNNING': '运行中',
           'PAUSED': '已暂停', 'DRAINING': '正在安全停止', 'STOPPED': '已停止',
           'FAILED': '失败'}.get(ui.run_status, ui.run_status)
    return f'后端：{connection} · {run}'


@dataclass(frozen=True)
class TaskStatus:
    kind: str
    active: bool = False
    owned: bool = False
    stage: str = ''
    scene_name: str = ''
    error: str = ''
    progress: str = ''
    output: str = ''


def foreground_tasks(scene):
    from . import custom_camera_capture
    from .panels import custom_cameras, stacked_camera_rig, presentation
    return tuple(module.status_for(scene) for module in
                 (custom_camera_capture, stacked_camera_rig, custom_cameras, presentation))


def can_start(action, scene):
    """Read resource conflicts for foreground actions; independent exports can coexist."""
    if action not in {'replace_scene', 'playback', 'camera_edit', 'camera_capture', 'radar_mount'}:
        return True, ''
    labels = {'capture': '相机采集中', 'mount': '相机盒体安装中',
              'camera': '相机草稿编辑中', 'recording': '窗口录制中 · Esc 停止'}
    for task in foreground_tasks(scene):
        if task.active and not (task.kind == 'camera' and task.stage in {'WATCH', 'LAYOUT'}):
            return False, labels[task.kind] if task.owned else '其他场景：' + labels[task.kind]
    return True, ''


def stage_label(stage):
    return {'CAPTURING':'采集中','CANCELLING':'取消待确认','CLOSING':'关闭待确认',
            'PLACING':'安装中','RECORDING':'录制中','AIM':'草稿待确认',
            'WATCH':'观察中','LAYOUT':'布局观察中','SUCCEEDED':'完成',
            'FAILED':'失败','CANCELLED':'已取消','SIMULATION':'几何仿真',
            'REPLAY':'文件回放','LIVE':'本地实时输入','RUNNING':'运行中'}.get(stage,stage)
