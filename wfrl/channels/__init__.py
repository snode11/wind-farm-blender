"""通道层：传感器注册表。

在此之前，"通道"是 `rviz_app.FastFarmSource.step()` 里硬写的一串 `hub.publish(...)`
—— 想加一路数据要改数据源、改面板、改渲染三处。本层把传感器变成声明式的对象：

    SENSOR = 位姿 + 采样率 + 噪声 + 保真度标签 + 渲染器

场景 YAML 勾一个 `{type: lidar}`，注册表就实例化它；它自己产出数据、自己往 3D
场景里加 actor。开关复选框 ⇒ 通道停发 ⇒ 它的 actor 隐藏。这样"订阅开关即时反映
在 3D"是机制而不是特例。

**保真度必须标注。** 三类，UI 和汇报里都要区分开，混着讲会让整套多模态不可信：

  DIRECT   直读 —— FAST.Farm 真算出来的（叶根弯矩、功率、桨距、转矩、偏航）
  DERIVED  导出 —— 由直读量或湍流盒按明确公式推的（lidar 测风、转速、振动谱）
  SYNTH    合成 —— 我们造的模型，物理保真度未经验证（相机、声音、压电）
"""
from wfrl.channels.base import Sensor, Fidelity, SensorRegistry, registry
from wfrl.channels import sensors as _sensors      # noqa: F401  触发注册

__all__ = ["Sensor", "Fidelity", "SensorRegistry", "registry"]
