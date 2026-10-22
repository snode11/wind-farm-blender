# 第三方来源与适用范围

| 内容 | 来源与许可 |
| --- | --- |
| Blender 扩展代码 | `blender_frontend/wfrl_blender/blender_manifest.toml` 声明 `SPDX:MIT`；不覆盖第三方数据与引用资料 |
| WFCRL 依赖源码与 NREL 模板 | [Jchenhan/wfcrl-env](https://github.com/Jchenhan/wfcrl-env)，依赖快照提交 `e31bf8b1993ef1c9bdc1a711b0e7bbb7cc10b3b0`；随资源包保留 Apache-2.0 LICENSE。独立几何输入在 `projects/nrel5mw-reference/`，包含项目配置参数 |
| 地表与天空资源 | Poly Haven CC0；具体作品、来源与下载校验见 `blender_frontend/wfrl_blender/assets/landscape/SOURCES.md` |
| GW184 FFA 轮廓参考 | 固定提交的 WISDEM 参考资料；来源、摘要与适配限制见 `projects/gw184-single/sources/provenance.json`，Apache-2.0 许可见同目录 `WISDEM-LICENSE` |
| GW184 DTU 参考模型 | [DTU 官方模型页](https://www.hawc2.dk/models/dtu-10-mw)；项目持有人确认具备 `DTU_10MW_RWT.htc` 与 `DTU_10MW_RWT_ae.dat` 两份输入的公开分发授权。两份原始文件随 Git 提供；官方来源与固定摘要见 [sources/provenance.json](projects/gw184-single/sources/provenance.json)，缺失或损坏后的恢复方式见[来源说明](projects/gw184-single/sources/README.md) |
| CoTracker | [facebookresearch/co-tracker](https://github.com/facebookresearch/co-tracker)，CC BY-NC 4.0；实验固定身份保留在对应 provenance，源码克隆和权重不随资源包提供 |

根目录没有统一覆盖全部源码、研究数据、图片、视频及第三方参考资源的许可证。各项许可只适用于明确对应的文件；来源署名、下载可访问和技术可复现不自动授予其他资料的再分发权。
