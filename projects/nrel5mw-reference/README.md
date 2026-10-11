# NREL 5MW 几何导出参考输入

`template/` 保存 `scripts/blender/export_geometry.py` 实际读取的 OpenFAST 几何输入；文件摘要与正式 `assets/nrel5mw_geometry.json` 中的 `source_sha256` 对应。来源为项目使用的 WFCRL NREL 5MW 模板，许可证见 [LICENSE](LICENSE)。壳体尺寸来自 `wfrl/turbines/nrel5mw.yaml`。

从仓库根目录运行以下命令，将几何导出到独立文件进行核对：

```sh
python3 scripts/blender/export_geometry.py --output /tmp/nrel5mw_geometry.json
```

该命令只解析保存的参考几何，不启动求解器。`--template` 可显式指定其他模板；不同模板的输出不能冒用正式资产的来源身份。本目录不包含完整仿真算例或求解器，不能单独用于 FAST.Farm 训练。
