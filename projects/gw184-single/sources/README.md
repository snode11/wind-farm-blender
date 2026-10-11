# GW184 参考来源

`provenance.json` 记录 DTU 10MW 参考模型的官方下载入口、原始 ZIP 摘要、使用字段和两份输入文件摘要。`ffa_reference.json` 的 FFA 轮廓来自固定提交的 WISDEM 参考资料，许可证见 [WISDEM-LICENSE](WISDEM-LICENSE)。这些参考数据不是制造商 GW184 CAD。

`DTU_10MW_RWT.htc` 与 `DTU_10MW_RWT_ae.dat` 是几何构建所需输入；当前源码分发不直接附带这两份原始文件，因为尚未确认该下载版本的公开再分发许可。使用者须从[官方模型页面](https://www.hawc2.dk/models/dtu-10-mw)取得 `provenance.json` 指定的归档，再从仓库根目录执行：

```sh
python3 projects/gw184-single/tools/restore_dtu_reference.py --archive /path/to/dtu_reference.zip
```

恢复工具只读取这两份文件，并核对各自 SHA-256；摘要不符或已有目标时明确拒绝。确认替换目标时使用 `--overwrite`。缺少这两份输入时，GW184 几何构建和对应测试不能完整运行。NREL 离线回放、扩展构建和叶片重建模块不依赖它们。
