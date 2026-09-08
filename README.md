# wfrl — 风电场尾流偏航的多智能体强化学习控制

在 [WFCRL](https://github.com/ifpen/wfcrl-env) 基准上做风电场协同尾流偏航（wake steering）控制。
上游机组主动偏航把尾流推离下游机组，牺牲自身功率换全场功率净增；每台机组是一个 agent，
只看得到局部观测，耦合只出现在奖励里。

本仓库同时挂两个物理后端 —— 稳态的 **FLORIS** 与动态气弹的 **FAST.Farm** ——
共用同一套训练管线，使"后端"可以作为唯一变量做受控对照。

---

## 1. 最终目标

> 学到一套**在动态气弹仿真里也成立**的多机组协同偏航策略，并说清楚它为什么需要记忆、
> 需要多少训练量、以及从稳态模型迁移到动态仿真损失多少。

拆成三个可判定的问题：

| | 问题 | 判据 |
|:--:|---|---|
| Q1 | 策略在动态后端上能不能超过零偏航基线？ | 相对各自零偏航基线的增益 > seed 标准差，≥3 seed |
| Q2 | 动态场景是否**必须**有记忆？ | 同后端、同训练量下 recurrent actor 显著优于 MLP |
| Q3 | FLORIS 上训练的策略迁到 FAST.Farm 掉多少？ | zero-shot 增益 vs 原地训练增益之比 |

Q1/Q2/Q3 目前都还没有被回答（见 §6）。

---

## 2. 目录结构

```
wfrl/                       可复用的包（pip install -e . 之后任何 cwd 都能 import）
├── paths.py                所有输出目录的唯一定义处
├── fastfarm_driver.py      FLORIS / FAST.Farm 统一驱动，支持钉死来流
├── mappo_central.py        集中式 critic 的 MAPPO（CTDE），含 GRU critic
├── mappo_sb3.py            基于 SB3 的参数共享 MAPPO（PettingZoo → VecEnv）
├── sb3_wrapper.py          单智能体 PPO 的 Gymnasium 包装
├── multimodal.py           Dict 观测（标量 + 尾流图像）与 CNN 分支
└── viz/
    ├── field.py            从 FLORIS 求解器取水平截面速度场
    ├── animate.py          PyVista 机组建模与逐帧更新
    └── rviz_app.py         RViz 风格交互界面：拖拽重定位机组、实时遥测、双后端切换

scripts/                    入口，全部可从任意目录运行
├── train/                  train_fastfarm / train_mappo / train_mappo_mm / train_ppo*
├── experiments/            exp_*（对照实验）、smoke_fastfarm、probes/
├── analysis/               compare_*（聚合与判据）、eval_visualize_ppo
└── figures/                make_*_figures、capture_rviz

results/                    跑出来的东西
├── runs/                   训练历史 json（实验记录本身，进版本库）+ 曲线 png
├── checkpoints/            模型权重
├── logs/                   tensorboard / monitor，legacy/ 放早期文本日志
└── eval_outputs/           评估期流场图

slides/                     汇报材料：introduction_slides.tex + figs/
                            整个目录可直接打包上传 Overleaf（编译器选 XeLaTeX）
docs/                       setup_local.md（本地部署 step-by-step）、算法笔记、reports/ 日报
refs/                       论文 PDF 与技术要求

wfcrl-env/                  上游基准（editable 安装指向此处，含本地补丁）
simulators/                 FAST.Farm 可执行文件与算例模板
__simul__/                  FAST.Farm 运行时工作目录（每次 make() 新建一个算例，会涨到 GB 级）
```

`wfcrl` 按 **CWD** 相对路径解析 `simulators/` 与 `__simul__/`（`wfcrl/interface.py:342,420,555`），
所以 `wfrl/fastfarm_driver.py` 在 import 时 `os.chdir(paths.ROOT)`。后三个目录因此不能挪位置。

---

## 3. 快速开始

环境搭建（conda 环境、FLORIS 依赖、FAST.Farm + MS-MPI）见 **`docs/setup_local.md`**。
之后在仓库根：

```bash
pip install -e . --no-deps          # 只是让 scripts/ 能 import wfrl，不装任何依赖
```

```bash
# FLORIS：快，秒级起步
python scripts/train/train_fastfarm.py --backend floris --wind-speed 8 --wind-direction 270

# FAST.Farm：必须走 mpiexec（MPI_Comm_spawn 需要进程管理器）
mpiexec -n 1 python scripts/train/train_fastfarm.py --backend fastfarm --wind-speed 8

# macOS/Linux 需要原生 FAST.Farm 可执行文件（仓库内 .exe 仅适用于 Windows）：
export WFCRL_FASTFARM_EXECUTABLE=/absolute/path/to/FAST.Farm

# macOS 桌面窗口要用 pythonw，避免 Qt 由无应用标识的终端 Python 承载：
conda install -c conda-forge python.app
scripts/macos/run_desktop.sh studio --scene scenes/turb3_row.yaml
scripts/macos/run_desktop.sh rviz --backend fastfarm --max-steps 200 --wind-speed 8

# 对照实验与作图
python scripts/experiments/exp_null_distribution.py     # reset 抽签的零假设分布
mpiexec -n 1 python scripts/experiments/exp_matched_inflow.py   # 钉死来流的双后端对照
python scripts/analysis/compare_pinned_seeds.py         # 多 seed 聚合 + 判据
python scripts/figures/make_control_figures.py          # 重画 slides/figs/

# 交互式 3D 界面（RViz 风格；以下是 Windows/Linux 命令）
python -m wfrl.viz.rviz_app                          # FLORIS 稳态，可拖拽重定位机组
mpiexec -n 1 python -m wfrl.viz.rviz_app --backend fastfarm --max-steps 200
python -m wfrl.viz.rviz_app --backend fastfarm \
    --model mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_u8.pt   # 挂已训练策略

# 合成截图（Qt 控件层 + VTK 视口层，绕开 OpenGL 截屏发黑）→ slides/figs/
python scripts/figures/capture_rviz.py
```

`rviz_app` 是模块不是脚本（`wfrl/viz/` 下），用 `python -m` 起。
`--model` 给裸文件名即可，会在 `results/checkpoints/` 下找；给错会把可用的 ckpt 列出来。
`--backend fastfarm` 那一路单步 ~0.6 s 阻塞主线程，因此界面里禁用拖拽并把定时器放慢到 200 ms。

---

## 4. 两个物理后端：FLORIS 与 FAST.Farm

### 4.1 相同的部分

这是整个对照设计的前提 —— 除了物理，其它一切都一样：

- **同一套 PettingZoo AEC 接口**，同样的 `reset / step / observation_space / action_space`；
- **同一个布局**：`Turb3_Row1`，三台 NREL 5 MW（$D=126$ m）沿 $x$ 排成一列，
  $x = 0 / 504 / 1008$ m，即间距 $4D$ 与 $8D$；
- **同一组观测量**（偏航角、功率、载荷、转子处风速风向）与**同一个动作**（每步偏航增量，±5°）；
- **同一套训练代码**（`scripts/train/train_fastfarm.py`）、同一组超参、同一个奖励整形器。

### 4.2 不同的部分

| | FLORIS | FAST.Farm |
|---|---|---|
| 物理模型 | 稳态解析尾流工程模型 | OpenFAST 气弹 + 动态尾流蜿蜒（DWM） |
| 时间维 | **没有**。每步重解一次稳态 | 真实时间推进，有惯性、有历史 |
| 控制步 $dt$ | **60 s** | **3 s** |
| 预热 $t_{\rm init}$ | 0 | 25 s（`start_iter = \lceil 25/3 \rceil = 9`，reset 内预跑 10 步 ≈ 30 s 仿真时间） |
| 单控制步墙钟 | **19.9 ms** | **~600 ms**（**30×**） |
| $10^5$ 步 / $10^6$ 步 | 0.55 h / 5.5 h | 17 h / 7 天 |
| 起动 | $t=0$ 即稳态 | 尾流要几十秒才建立（见 4.3） |
| 载荷 | 无真实气弹载荷 | 有（塔基/叶根，可进奖励做功率–载荷权衡） |
| 风向可否钉死 | 可，`reset(options={"wind_direction": ...})` | **不可**，接口只写 InflowWind 的 `HWindSpeed`（风速） |
| reset 是否重采风向 | **会**（`floris_3t` 未设 `set_wind_direction`） | **不会**（`fastfarm_3t` 里 `set_wind_direction=True`） |
| 启动方式 | 普通 python 进程 | 必须 `mpiexec -n 1 python ...` + MS-MPI |
| 进程风险 | 无 | SC_DLL 在 $t=0$ 领固定迭代预算，回合不走完会留孤儿进程卡在 `MPI_RECV` |

**$dt$ 差 20× 是个容易漏掉的坑。** 同样"64 个控制步"，FLORIS 覆盖 3840 s 物理时间、
每步都已是稳态；FAST.Farm 只覆盖 192 s，比尾流建立所需的 ~160 s 长不了多少。
跨后端比"训练了多少步"并不等于比"看到了多少物理"。

### 4.3 实测对照（钉死 $u_\infty$、270°、零偏航，无策略）

数据源 `results/runs/matched_inflow.json`、`settle_check.json`；复现见 §3。

**全场功率**：

| $u_\infty$ | FLORIS | FAST.Farm | FLORIS / FF |
|:--:|---:|---:|---:|
| 6 m/s | 0.8971 MW | 1.0907 MW | 0.82× |
| 7 m/s | 1.5346 MW | 1.7928 MW | 0.86× |
| 8 m/s | 2.3767 MW | 2.7488 MW | 0.86× |
| 9 m/s | 3.4385 MW | 3.9438 MW | 0.87× |

**同一来流下 FLORIS 比 FAST.Farm 低 13–18%**，差异全部集中在下游。$u=8$ 逐台：

| | T1（迎风） | T2（$4D$） | T3（$8D$） |
|---|---:|---:|---:|
| FLORIS | 1.6913 MW | 0.3627 MW | 0.3227 MW |
| FAST.Farm | 1.7070 MW | 0.5645 MW | 0.4774 MW |
| 相对 T1 的亏损（FLORIS） | — | −78.6% | −80.9% |
| 相对 T1 的亏损（FAST.Farm） | — | −66.9% | −72.0% |

T1 两边差 0.9%（$u=9$ 时 0.3%）—— T1 不受尾流影响，它的一致性是"来流确实钉齐了"的内部效度检查。
**FLORIS 的稳态尾流模型把亏损估计得比气弹仿真更深**，气弹里有尾流蜿蜒与湍流掺混带来的动量恢复。

**真正的静/动差别在时延，不在功率量级。** $u=8$ 零偏航，FAST.Farm 的下游亏损分两级建立：

| step | T1 | T2 | T3 | |
|---:|---:|---:|---:|---|
| 0–14 | 1.7070 | 1.7070 | 1.7070 | 三机逐位相同 |
| 15–19 | 1.7070 | 1.175→0.575 | 1.174→0.575 | T2/T3 同步跌落 |
| 20–43 | 1.7070 | 0.5680 | 0.5676 | 两者相差 0.05% |
| 44–52 | 1.7070 | 0.5642 | 0.509→0.461 | T3 单独二次跌落 |
| ≥53 | 1.7070 | 0.5645 | 0.4797 | 稳态（末 380 s 漂移 −0.01%） |

FLORIS 在 $t=0$ 就是最后一行。**动作对 T3 的完整影响滞后约 46 个控制步，而控制步只有 3 s**
—— 这是 recurrent policy 的直接物理依据，也决定了回合长度必须取 150–200 步并丢弃开头约 46 步。
两次跌落的归因见 `docs/reports/`。

### 4.4 该用哪个

按 §4.2 的 30× 速度差和 §4.3 的时延结构分工：

- **FLORIS 做大规模训练**：覆盖二维风况空间所需的样本量下，FAST.Farm 不可行；
- **FAST.Farm 做受控对照与最终评估**：钉死来流比 MLP vs recurrent（FLORIS 无时延，
  这个假设在其上根本无法检验），以及 zero-shot transfer 量化 sim-to-sim gap。

---

## 5. 路线图

| 阶段 | 内容 | 判据 | 状态 |
|:--:|---|---|:--:|
| 0 | 双后端打通：WFCRL 本地部署；FAST.Farm bring-up（v3.5.1 + MS-MPI ABI 修正 + mpiexec）；`FastFarmDriver` 统一 API | 两后端同一份训练脚本跑通 | ✅ |
| 1 | 算法基线：单智能体 PPO → 参数共享 MAPPO → 集中式 critic MAPPO（CTDE）；多模态 Dict 观测 + CNN 分支；3D / RViz 可视化 | 管线端到端跑通、曲线可读 | ✅ |
| 2 | **实验方法学**：把对照做对 —— reset 抽签的零假设分布、钉死来流的双后端对照、稳态检验、多 seed 判据 | 每个结论都有受控实验支撑，不可信的数字撤回 | ✅ |
| 3 | **回合结构与训练规模**：真回合（每回合重起进程 + warmup 丢弃 + 时间截断 GAE）✅、奖励整形器换掉 `StepPercentage` ✅、训练量 ⬜ | 训练曲线出现可辨认的收敛段 | ← **在这里** |
| 4 | **记忆的必要性**（Q2）：修 recurrent 路径（有状态 rollout + BPTT + recurrent actor），FAST.Farm 钉死来流下 MLP vs recurrent，≥3 seed | 增益差 > seed 标准差 | ⬜ |
| 5 | **泛化与迁移**（Q1、Q3）：变风况训练（每回合重采 / `wind_time_series`），FLORIS 训练 → FAST.Farm zero-shot 评估 | 量化 sim-to-sim gap 与微调所需样本量 | ⬜ |
| 6 | **结构化多智能体**：尾流 DAG → 奖励分解 → 时间尺度分配（NetworkMQL）；从 3 机组扩到 Ablaincourt / HornsRev2 规模 | $\bar M < M$ 带来的学习率/并行度收益可测 | ⬜ |
| 7 | 功率–载荷权衡：把 FAST.Farm 的真实气弹载荷写进奖励 | 等功率下载荷下降 | ⬜ |

阶段 6 的理论背景（TI-Dec-POMDP、多尺度 Q-学习收敛定理、NetworkMQL 的条件 (A)(B)）
见 `slides/introduction_slides.tex` 与 `refs/Multi_scale_Q_learning.pdf`。
需要说清楚的是：那套保证是给**表格型 Q-学习**的，我们用的是深度 MAPPO，
理论提供的是结构洞察（奖励可分解、DAG 决定耦合、分尺度可解非平稳），不是收敛证明。

### 5.1 应用层（与上表并行的另一条线）

上表是**研究**问题的推进；下表是把这堆脚本收成一个能用的**应用**。
两条线共用 `wfrl/` 下的物理与算法，互不阻塞。

| | 内容 | 判据 | 验收脚本 | 状态 |
|:--:|---|---|---|:--:|
| D1 | 场景层 + 通道层：一份 YAML 决定算例与画面；传感器即订阅源 | 改 YAML 坐标画面与仿真一起变；取消订阅采样计数停住且像素变少 | `probe_scene*.py` | ✅ |
| D2 | Studio 外壳 + 安全约束层：四面板围一个 3D 视图，训练跑后台线程 | 约束层预测与环境执行逐位一致；训练中事件循环最大间隔 < 0.3 s | `probe_safety.py` / `probe_trainer.py` / `probe_studio.py` | ✅ |
| D3 | 汇报与复现：slides 补系统架构与四阶段路线图、界面截图取自真训练 | 截图里的功率与约束事件来自实跑，不是摆拍 | `scripts/figures/capture_studio.py` | ✅ |
| D4 | 场景编辑器（拖拽建场）、多切面与等值面、尾流 DAG 叠加层 | — | — | ⬜ |
| D5 | 约束进入**学习回路**（当前只事后记录）；载荷（雨流计数 DEL）入奖励 | — | — | ⬜ |

D2 里值得单独记的两条：

- **仿真器会静默改写动作**，且不止是限幅。`multiagent_env.py:196-207` 在执行机构占空比
  ≥ 10% 时把**整步指令置零**；`DISCON.F90:523` 的 `MAX(PitchRef, PitComT)` 让外部桨距
  只是**下界**；`DISCON.F90:440` 的 `GenTrq = TorqueRef` 让外部转矩**完全顶替**基线，
  而动作上界 2e4 N·m 只有额定 43529 N·m 的 45.9%，直接下发即超速。
  偏航的可持续动作是 0.1 × 0.3 °/s × 3 s = **0.09 °/步**，动作空间却是 ±5° —— 差 55.6×。
  不建模的后果不是学得慢，是学到一串**从未执行过**的动作，而训练曲线上什么都看不出来。
- **界面卡顿要分解测量，不要猜。** 最差间隔 3144 ms ≈ 恰好一个控制步，直觉是工作线程
  攥住 GIL；把间隔拆成 `sleep` 超时（GIL 被占）与 `processEvents`（主线程自己在算）
  两段之后，前者始终 < 100 ms —— GIL 无辜。真正的两处是 FLORIS 稳态解（0.24~0.36 s）
  与叶片柔性变形求解（中位 192 ms），都搬进求解线程后降到 103 ms。
  诊断脚本 `scripts/experiments/_stall_probe.py` 保留。

---

## 6. 当前进度

**阶段 2 已闭合**，产出是一组受控结论和一组被撤回的结论：

- ✅ 同一来流下 FLORIS 比 FAST.Farm **低 13–18%**，差异全在下游机组（§4.3）；
- ✅ 静/动的本质差别是**尾流建立的时延与分级**，不是功率量级；
- ❌ 撤回「静/动功率差 2.43×、FLORIS 系统性高估功率」—— 该数字来自 reset 的**一次风况抽签**，
  既不显著（纯抽签下 $P(\text{比值} \ge 2.43) = 0.26$）方向还是反的；
- ❌ 撤回对第一次功率跌落的机理归因，见 `docs/reports/`。

**阶段 3 的三个阻塞项已经拆掉两个（回合结构、奖励整形器），第三个（训练量）待跑：**

1. ✅ **回合结构**：`FastFarmSampler` 改成自己管回合生命周期 ——
   `--episode-steps E` 每回合重起一个 FAST.Farm 进程（约 25 s）并按 `--wind-sched`
   换来流，`--warmup-steps W` 把尾流建立瞬态挡在训练数据和统计之外，
   回合结束 `close(purge=True)` 排空迭代预算并删掉算例目录（不删的话每回合 ~37 MB，
   `__simul__/` 已到 3.4 GB）。GAE 同时改成**时间截断**版：偏航控制没有真正的终止状态，
   回合结束只是预算走完，δ 里必须照常 bootstrap $V(s_{t+1})$（用截断**前**那个观测），
   只把 GAE 递推链在边界断开。旧的 `mappo_central.compute_gae` 把 mask 同时乘进两处，
   会系统性低估回合末端的价值。
   验收：`scripts/experiments/probe_episodes.py` → `EPISODES_OK`。
2. ⬜ **训练量**：入口是 `scripts/train/run_stage3.py`，逐 seed 成对跑
   「零偏航基线 / 学习策略」。基线是同批次的 `--policy zero`（同样的回合长度、warmup、
   来流调度、奖励口径，只把动作置 0 且不更新网络）—— 不能再从别的实验里搬一个常数当基线，
   否则回合结构的改动会被算进"策略增益"。默认 40×128 学习 + 5×128 基线 × 3 seed
   ≈ **3.8 h**（`--dry-run` 先看预算）。聚合用 `scripts/analysis/compare_stage3.py`。
3. ✅ **奖励整形器**：默认从 `StepPercentage` 换成 `level`（`wfrl/rewards.py`）。
   判据见 `scripts/experiments/exp_reward_shaper.py`（纯数值，解析复现环境的奖励管线）。

**关于奖励整形器，此前的说法要更正。** 逐行读 `multiagent_env.py:220-228` 后确认：
环境交给整形器的原始 reward **本来就已经除过 $u^3$**，且分母是
`interface.py:327` `argmax(speeds)` 取的**逐步**上游测速，不是算例设定的常数 ——
所以"来流一变，$u^3$ 就直接进奖励"不成立。真实的缺陷是另外两条：

- **分母滞后一个控制步**：reward 在 226 行算、`self._state = next_state` 在 228 行，
  分子已是 $u_t^3$ 的功率、分母还是 $u_{t-1}^3$。湍流（TI=9%）下这一项每步注入的噪声
  std 达 1.40，而策略 +5% 效率带来的信号只有 0.165 —— **每步 SNR 仅 0.118**。
- **差分型奖励对持续的改进只给一次性回报**：策略把偏航保持在更好的位置，
  `StepPercentage` 的折现回报 $\Sigma\gamma^t$ 只有 **0.027**，而 `level` 是 **7.549**，
  差两个量级。这才是"气弹后端上等于没学到"更可能的原因。

顺带否掉了一个看似合理的指标：**污染比**（来流情景与策略情景的折现和之比）对所有
整形器都是 0.035–0.042，因为两条路径经过同一个非线性、整形器同比例缩放二者。
`centered(α=0.05)` 则把增益吃掉大半（0.022 vs level 的 0.165）—— EMA 必须比策略
改变功率的时间尺度慢，否则基线追着信号跑。

另外，GRU 那一路是原型：采样阶段 `h0=None` 隐状态不跨步传递、更新阶段却整段序列前向，
采样与更新口径不一致，递归的优势从未被行使（`explained_var` 末 5 轮仅 0.056）。
**当前实验回答不了 Q2。**

**新增的第四个阻塞项：回合长度不足以让偏航走到最优点。** 阶段 3 单 seed 跑完
（40×128，`results/runs/*_mappo_s0_level_E128_pin8_stage3_hist.json`）critic 修好了
（`explained_var` 0.003 → 0.8，`vloss` 80.8 → 0.003），策略仍 −0.19%。此前把它记成
"三机一列正对来流没有偏航增益可拿"，但这个解释**没有被排除，也没有被验证** ——
量完占空比约束后有了另一个同样成立的解释：

占空比（`multiagent_env.py:196-207`）不是限幅，是**累计预算**。`_actuation_accumulator`
逐步累加 $|\Delta|$ 且回合内永不衰减，判据 $\mathrm{acc}/(\mathrm{rate}\cdot k\cdot dt)\ge 0.1$
约束的是"历史平均动作速率"。于是 $K$ 步回合内的**可达偏航被封顶**：

$$\text{reachable} \approx 0.1\cdot\mathrm{rate}\cdot dt\cdot K + \text{step bound}$$

末项来自 $k=1$ 时 $\mathrm{acc}=0$ ⇒ **首步永远免费**。$dt=3$ s 下即 $0.09K+5$：
`episode_steps=128` 只能走到 **16.5°**，而尾流偏航的增益要到 20° 上下才显现；
`n_steps=64` 更只有 5.8°。**回合内到不了最优点，策略就拿不到"偏航有增益"的证据。**
要给它机会，$dt=3$ s 需要 `episode_steps` ≳ 170。

`scripts/experiments/probe_duty_env.py` 在 FLORIS 算例上与环境**逐位零偏差**复现了
这套时序，并确认清零占比 $=1-\text{可持续量}/\text{动作幅度}$、生效间隔 $=$ 动作幅度/可持续量 步。
过程中撞出两条朴素推导会算错的机制：

- **acc 累加的是动作空间裁剪后的量，不含状态边界**（`mdp.py:299-318` 先
  `clip(Δ, ±5°)` 累加、再 `clip(state+Δ, ±40°)` 写状态）。偏航贴到 ±40° 之后指令
  不再改变状态，acc 却照涨 —— 实测末端偏航 40° 时 acc 已累到 115°，
  **饱和后每一步都在为未发生的动作付占空比**。
- **accumulator 对非末位 agent 是先读后写**：`multiagent_env.py:246-249` 的回抄在
  `is_last()` 分支之后，而 mdp 的累计值只在那一支更新，于是遍历序靠前的 agent
  抄到的值滞后一步、约束更松。占空比在机组之间**不等价**，而 obs 里又没有 duty ——
  策略在学一个自己看不见、且逐机组不一致的门控。这是 obs 该不该加 duty 的直接理由。

---

## 7. 实验记录

| 记录 | 脚本 | 数据 ||---|---|---|
| reset 抽签的零假设分布（200 次） | `scripts/experiments/exp_null_distribution.py` | `results/runs/null_distribution.json` |
| 钉死来流的双后端对照（$u=6\ldots9$） | `scripts/experiments/exp_matched_inflow.py` | `results/runs/matched_inflow.json` |
| 稳态检验 / 尾流建立时序（180 步） | `scripts/experiments/exp_settle_check.py` | `results/runs/settle_check.json` |
| 钉死来流的 RL 对照（3 seed） | `scripts/analysis/compare_pinned_seeds.py` | `results/runs/*_s{seed}_u8_hist.json` |
| 奖励整形器的来流污染 / 信噪比 | `scripts/experiments/exp_reward_shaper.py` | `results/runs/reward_shaper_sensitivity.json` |
| 回合结构在线验收 | `scripts/experiments/probe_episodes.py` | 末行 `EPISODES_OK` |
| 阶段 3 成对批次（基线 / 策略 × seed） | `scripts/train/run_stage3.py` → `scripts/analysis/compare_stage3.py` | `results/runs/*_stage3_hist.json` |
| 占空比可达量（闭式递推） | `scripts/experiments/probe_duty_dynamics.py` | 末行 `DUTY_PROBE_OK` |
| 占空比在真环境上的逐位对拍 | `scripts/experiments/probe_duty_env.py` | 末行 `DUTY_ENV_OK` |

日报在 `docs/reports/`，汇报 slides 在 `slides/`。

---

## 8. 已知的坑

1. **`env.reset()` 每次重采风况。** `wfcrl/mdp.py:233-262` 抽 `wind_speed = 8·rng.weibull(8)`
   与 `wind_direction ~ N(270, 20°)`，而功率 $\propto u^3$ —— 200 次 reset 的全场功率跨度 **70×**。
   任何对照实验都必须 `reset(options={"wind_speed": U, "wind_direction": D})` 钉死。
   只有 `set_wind_speed` / `set_wind_direction` / `wind_time_series` 能关掉抽样，
   而**两个后端的算例在这一点上并不对称**（见 §4.2 最后两行）。
2. **归一化功率 $P/u_\infty^3$ 的分母必须用自由来流**，不能用 `obs["wind_speed"]` ——
   后者是转子处测量值，下游机组测到的是尾流内速度，拿它做分母会把亏损同时算进分子分母。
3. **多 seed 的产物文件名必须带 seed**，否则静默互相覆盖。
4. **FAST.Farm 回合必须走完或 `driver.close()` 排空迭代预算**，否则子进程卡在 `MPI_RECV` 变孤儿。
5. **`__simul__/` 会增长**（每次 `envs.make()` 都新建一个算例目录）。
   训练和 RViz 路径上的 `FastFarmDriver.close(purge=True)` 会自己收（FAST.Farm 刚退出时
   `ServoData/DISCON_WT*.dll` 句柄还没释放，会吃 WinError 5，所以带重试 + 待删队列）；
   明确保留案例的一次性实验仍需自行管理产物。
6. **`n=3` 下"seed 区间恰好不重叠"不是证据。** 判据写成"间隙 > max(seed 标准差)"，
   已固化在 `compare_pinned_seeds.py` 里。
7. **占空比会把整步指令清零，而这在训练曲线上完全看不见。**
   `multiagent_env.py:206-207` 判定 `actuating_frac >= 0.1` 时执行的是
   `action[control][:] = 0.0` —— 不是限幅，是清零；策略以为发了 5° 偏航、环境执行 0°、
   回报照常算。$dt=3$ s 下 yaw 的可持续量只有 **0.09 °/步**（动作空间 ±5° 的 1/56），
   持续满幅动作 98% 会被吞掉。约束层（`wfrl/safety.py`）把每一次清零记成 `Event`
   并显示在 Studio 安全面板上，就是为了让它不再是隐形的。详见 §6 第四个阻塞项。
