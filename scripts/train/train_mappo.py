"""SB3 参数共享 MAPPO 训练入口（FLORIS 后端）。

训练与 `scripts/analysis/compare_eval.py` 共享 `wfrl/mappo_sb3.py` 的模型、
归一化和评估接口；本模块提供命令行入口。

    python scripts/train/train_mappo.py                  # 试运行 500 steps
    python scripts/train/train_mappo.py --timesteps 500000
    python scripts/train/train_mappo.py --eval-only
"""
from wfrl.mappo_sb3 import main

if __name__ == "__main__":
    main()
