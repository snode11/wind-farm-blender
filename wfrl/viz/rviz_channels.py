"""RViz 通道发布、订阅与后台线程缓冲。"""
import threading


# ---------------------------------------------------------------------------
# Channel Hub —— 极简发布/订阅（等价 ROS topic）
# ---------------------------------------------------------------------------
class ChannelHub:
    def __init__(self):
        self._subs = {}          # topic -> [callbacks]
        self.enabled = {}        # topic -> bool（面板可随时开关某路流）

    def topic(self, name):
        self._subs.setdefault(name, [])
        self.enabled.setdefault(name, True)
        return name

    def subscribe(self, name, cb):
        self.topic(name)
        self._subs[name].append(cb)

    def publish(self, name, data):
        if not self.enabled.get(name, True):
            return
        for cb in self._subs.get(name, []):
            cb(data)


class BufferHub:
    """后台线程专用的发布缓冲，接口和 ChannelHub 兼容。

    为什么要它：`ChannelHub.publish` 会同步回调到 Qt 控件（遥测表格 setText），
    而在工作线程里碰 Qt 控件是未定义行为。所以后台的控制步往这里发，主线程在
    渲染回调里 `drain()` 出来再转发给真 hub。
    """

    def __init__(self, hub):
        self._hub = hub
        self._q = []
        self._lock = threading.Lock()

    def __getattr__(self, name):        # topic/subscribe/enabled 一律转给真 hub
        return getattr(self._hub, name)

    def publish(self, name, data):
        with self._lock:
            self._q.append((name, data))

    def drain(self):
        with self._lock:
            out, self._q = self._q, []
        return out

