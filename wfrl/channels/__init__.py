"""场景传感器注册表：按 YAML 配置创建采样器并发布带来源标签的数据。

DIRECT 为仿真直读，DERIVED 为从直读量推导，SYNTH 为未验证的合成模型。
传感器提供位姿、采样率和数据；Blender 通过 Bridge 消费快照。
"""
from wfrl.channels.base import Sensor, Fidelity, SensorRegistry, registry
from wfrl.channels import sensors as _sensors      # noqa: F401  触发注册

__all__ = ["Sensor", "Fidelity", "SensorRegistry", "registry"]
