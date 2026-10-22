"""SB3 参数共享 MAPPO 训练入口（FLORIS 后端）。

实现在 `wfrl/mappo_sb3.py` —— 它同时被 `wfrl/viz/animate.py` 和
`scripts/analysis/compare_eval.py` 当库用，所以放进包里，这里只留命令行入口。

    python scripts/train/train_mappo.py                  # 试运行 500 steps
    python scripts/train/train_mappo.py --timesteps 500000
    python scripts/train/train_mappo.py --eval-only
"""
from wfrl.mappo_sb3 import main

if __name__ == "__main__":
    main()
