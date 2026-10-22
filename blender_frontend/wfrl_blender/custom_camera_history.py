"""Session-only camera history. Never invokes Blender's global undo or saves."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass


@dataclass
class Entry:
    before: dict
    after: str
    description: str


class History:
    def __init__(self, limit=20):
        self.entries = deque(maxlen=limit)
        self.identity = None
        self.notice = ''

    def clear(self, reason=''):
        self.entries.clear()
        self.identity = None
        self.notice = reason

    def check(self, identity, current):
        if self.entries and identity != self.identity:
            self.clear('场景、会话或模型已变化，相机撤销历史已清理')
        elif self.entries and current != self.entries[-1].after:
            self.clear('布局被外部修改或全局撤销，相机历史已清理')
        return bool(self.entries)

    def record(self, identity, before, before_hash, after_hash, description):
        self.check(identity, before_hash)
        if before_hash != after_hash:
            self.identity = identity
            self.entries.append(Entry(deepcopy(before), after_hash, description))
            self.notice = ''


HISTORY = History()


def identity(scene):
    from . import custom_cameras as core
    from .panels.custom_cameras import session_identity
    return session_identity(scene), core.model_identity(scene)


def state(scene):
    from . import custom_cameras as core
    layout = core.layout_dict(scene)
    return layout, core.live_layout_hash(scene, layout)


def before_change(scene):
    layout, signature = state(scene)
    key = identity(scene)
    HISTORY.check(key, signature)
    return key, layout, signature


def committed(scene, before, description):
    key, layout, signature = before
    _, after = state(scene)
    HISTORY.record(key, layout, signature, after, description)


def status(scene):
    try:
        if HISTORY.entries:
            _, signature = state(scene)
            HISTORY.check(identity(scene), signature)
    except (ValueError, RuntimeError, ReferenceError, KeyError):
        HISTORY.clear('模型或场景失效，相机撤销历史已清理')
    return ('撤销：' + HISTORY.entries[-1].description) if HISTORY.entries else HISTORY.notice or '暂无可撤销的相机修改'


def undo(scene):
    from . import custom_cameras as core, custom_camera_capture as capture
    from .panels.custom_cameras import installation_block_reason
    if capture.active():
        raise ValueError('请先结束采集，再撤销相机修改')
    if any(obj.get('wfrl_custom_draft') for obj in scene.objects):
        raise ValueError('请先确认或取消草稿，再撤销相机修改')
    reason = installation_block_reason(scene)
    if reason:
        raise ValueError(reason)
    status(scene)
    if not HISTORY.entries:
        raise ValueError(HISTORY.notice or '暂无可撤销的相机修改')
    entry = HISTORY.entries[-1]
    # Revalidation and publication are atomic; ordinary failure leaves the entry.
    core.restore_layout(scene, entry.before)
    HISTORY.entries.pop()
    # Rebuilt object IDs are not part of the expected layout fingerprint.
    return entry.description
