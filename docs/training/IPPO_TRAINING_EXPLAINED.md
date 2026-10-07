# 参数共享 IPPO 训练全流程（WFCRL 风场）

> 对齐日期：2026-10-06。本文解释当前 `scripts/train/train_mappo.py` 委托的 `wfrl/mappo_sb3.py` 参数共享 IPPO 数据流；本轮仅静态核对，没有训练或评估。当前算法入口分流见[配置指南](MAPPO_SETUP.md)。
>
> **命名澄清**：文件叫 `train_mappo.py`，但严格分类，当前实现是**参数共享 IPPO**——所有机组共享一套网络（参数共享 ✅），但 critic 只看单台机组的局部观测、没有全局状态（集中式 critic ❌）。仓库现已另有 `wfrl/mappo_central.py` 和 `scripts/train/train_fastfarm.py` 的集中式 critic 实现；这并未改变本文件所述 SB3 路线的 IPPO 身份。详见第 10 节。

---

## 0. 一句话概括

> 不是「7 套 PPO 各跑再同步」，而是「**1 套 PPO 网络，7 台机组的经验拼成一个大 batch 一起训**」。7 台之所以能同步产出经验，是因为整个风场是**一个 FLORIS 物理系统，一次稳态求解就同时算出所有机组的新观测**——不存在「等最慢的 agent」。

---

## 1. 整体管线

```
MAWindFarmEnv (PettingZoo AEC, env_id = "Dec_Ablaincourt_Floris")
  └─ aec_to_parallel()        # AEC(轮流) → Parallel(所有 agent 同时 step)
       └─ MAParallelVecEnv    # 自定义 VecEnv：num_envs = 机组数(7)，共用一个底层 env
            └─ VecNormalize   # 观测/回报滑动归一化
                 └─ PPO("MultiInputPolicy")   # 一套网络，所有机组共享参数
```

对应源码（`wfrl/mappo_sb3.py::make_venv/build_model`）：

```python
def make_venv(norm_reward=True, training=True):
    raw  = envs.make(ENV_ID, controls=["yaw"], max_num_steps=MAX_STEPS)
    par  = aec_to_parallel(raw)                    # AEC → Parallel
    venv = MAParallelVecEnv(par)                   # 7 agent → 7 个"并行 env"
    return VecNormalize(venv, norm_obs=True, norm_reward=norm_reward,
                        clip_obs=10.0, gamma=0.99, training=training)

def build_model(venv, resume=False):
    return PPO(
        policy="MultiInputPolicy",   # Dict 观测 → 每个 key 一个特征提取分支
        env=venv,
        n_steps=1024, batch_size=128, n_epochs=10,
        gamma=0.99, gae_lambda=0.95, clip_range=0.2,
        ent_coef=0.005, vf_coef=0.5, max_grad_norm=0.5,
        policy_kwargs=dict(net_arch=[128, 128]),
        ...
    )
```

**为什么这么设计**：SB3 是单智能体框架，没有原生多智能体接口。它有一个现成的抽象叫 `VecEnv`（向量化环境：N 个环境并行采样，喂一套网络）。我们把「N 台同构机组」**伪装成**「N 个并行环境」，就能零改动复用 SB3 成熟的 PPO——这正是参数共享的落地技巧。

---

## 2. 核心技巧：把「N 台机组」伪装成「N 个并行环境」

`MAParallelVecEnv.__init__`（`wfrl/mappo_sb3.py::MAParallelVecEnv.__init__`）：

```python
def __init__(self, par_env):
    self.par_env = par_env
    self.agents  = par_env.possible_agents
    n = len(self.agents)                       # n = 7

    agent0 = self.agents[0]
    raw_obs_space = par_env.observation_space(agent0)   # 同构 → 取一台即可
    raw_act_space = par_env.action_space(agent0)

    obs_space = spaces.Dict({k: spaces.Box(v.low, v.high, dtype=np.float32)
                             for k, v in raw_obs_space.items()})
    yaw = raw_act_space["yaw"]
    act_space = spaces.Box(yaw.low, yaw.high, dtype=np.float32)

    super().__init__(n, obs_space, act_space)   # ← 关键：num_envs = n = 7
```

- `super().__init__(n, ...)` 把 `num_envs` 设成机组数 7。**SB3 从此以为自己在跟 7 个独立环境打交道**。
- 观测/动作空间只从 `agent0` 取一份——**因为风场同构**（7 台同型号，观测/动作空间完全相同）。这是参数共享成立的前提：一套网络要对每台机组都适用，它们的输入输出接口必须一致。

---

## 3. 一个 step：7 台同时走，不存在等待

这是你问的核心——「难道要等最慢的 agent？」答案在 `step_wait`（`wfrl/mappo_sb3.py::MAParallelVecEnv.step_wait`）：

```python
def step_wait(self):
    actions = self._pending_actions               # 形状 (7, 1)：7 台的 yaw
    agent_actions = {                              # 打包成 {机组: {"yaw": 动作}}
        agent: {"yaw": actions[i]}
        for i, agent in enumerate(self.agents)
    }
    obs_dict, rew_dict, term_dict, trunc_dict, info_dict = \
        self.par_env.step(agent_actions)           # ★ 一次调用，7 台一起 step
    #   └─ 底层 FLORIS 一次稳态求解 → 整场 7 台新观测同时出来

    obs   = self._pack_obs(obs_dict)               # → {key: (7, ...)}
    rews  = np.array([float(rew_dict.get(a, 0.0)) for a in self.agents])  # (7,)
    dones = np.array([term_dict.get(a, False) or trunc_dict.get(a, False)
                      for a in self.agents])        # (7,)
    infos = [info_dict.get(a, {}) for a in self.agents]

    if dones.all():                                # 全部 done 才自动重置
        obs = self.reset()
    return obs, rews, dones, infos
```

**为什么没有「等最慢的 agent」**：
1. `par_env.step(agent_actions)` 是**一次调用**，7 台的动作一起传进去。
2. 底层是**同一个 FLORIS 风场**——整场是一个耦合物理系统，一次稳态求解就把 7 台的新风速/功率/湍流全解出来了。不存在「1 号机算完等 7 号机」。
3. 7 台是**同步**的（同时出动作、同时拿到新观测），但同步 ≠ 等待——它们本来就是一次计算的产物。

（PettingZoo 有两种 API：AEC 是「轮流出手」像下棋，Parallel 是「同时出手」像猜拳。`aec_to_parallel` 把前者转成后者，正是为了这种「同时 step」的同步语义。）

---

## 4. 观测打包：Dict-of-agents → 每个 key 一个批张量

`_pack_obs`（`wfrl/mappo_sb3.py::MAParallelVecEnv._pack_obs`）：

```python
def _pack_obs(self, obs_dict):
    """{agent: {key: arr}} -> {key: (n_agents, ...)} stacked array."""
    result = OrderedDict()
    for key in self._obs_keys:
        result[key] = np.stack(
            [np.asarray(obs_dict[a][key], dtype=np.float32).flatten()
             for a in self.agents]
        )
    return result
```

- 输入：`{turbine_0: {"yaw": .., "wind_speed": ..}, turbine_1: {...}, ...}`（按机组组织）。
- 输出：`{"yaw": (7,1), "wind_speed": (7,1), ...}`（按 key 组织，每个 key 把 7 台**堆叠成一个批**）。
- **为什么**：网络前向要的是「批张量」。把 7 台同一个 key 堆成 `(7, dim)`，就能一次前向同时算 7 台——批里的第 i 行就是第 i 台机组。这一步是「7 台共用一次前向」的物理形态。

> 多模态版 `MMVecEnv` 只在这里多塞两个 key：`vibration (7,6)` 和 `wake_image (7,1,48,48)`，其余流程完全一致。

---

## 5. 采样 → 训练：7 条经验流拼成大 batch

这是「参数共享」最关键的一环。SB3 PPO 的 `learn()` 内部循环（概念展开，非本仓代码）：

```
n_steps = 1024, num_envs = 7

# —— 采样阶段 ——
for t in 1..1024:
    obs: {key: (7, ...)}
      → policy 一次前向(批大小7) → actions (7,1), values (7,1), log_probs (7,1)
    venv.step(actions)  → 7 条 (obs, action, reward, done) 进 RolloutBuffer

# 采样结束：buffer 里有 1024 × 7 = 7168 条 transition
#   （这就是日志里 total_timesteps 每次 +7168 的来源）

# —— 更新阶段 ——
用 GAE 算 advantage
for epoch in 1..10:
    把 7168 条打乱、切成 batch_size=128 的 minibatch
    对同一套 θ 做梯度下降（PPO clip loss）
```

**参数共享体现在两处**：

1. **前向共享**：`policy(obs)` 一次吃 `(7, ...)` 的批、吐 `(7, ...)`。7 台走的是**同一套权重 θ 的同一次前向**——天然参数共享，不是「7 个网络碰巧权重相同」。

2. **反向合并**：更新时，7168 条经验**不区分来自哪台机组**，混在一起打乱、分 minibatch、一起算梯度更新 θ。1 号机和 7 号机的经验对**同一份权重**都有贡献。

**这直接回答你的疑问**：
- ❌ 不是「7 套网络各自 PPO 再同步参数」——从头到尾只有一套 θ，没有「同步」这个动作，也就没有同步开销。
- ✅ 是「7 台的经验 = 7 倍数据量，喂同一套网络」——每个全场控制步收集七个局部样本；这些样本来自同一耦合风场，不能由条数推定统计独立或样本效率提高七倍。

---

## 6. 为什么可以这样合并？——同构假设

能把 7 台经验混在一个 batch 里训一套网络，前提是**7 台机组同构**：
- 同型号、同观测空间、同动作空间、同奖励结构；
- 于是「一套策略对谁都适用」，一条经验无论来自哪台，对网络的意义一致。

如果机组异构（不同型号、不同观测维度），就不能直接共享——要么分组共享，要么每类一套网络。风场同型号机组的场景，正是参数共享的理想土壤。

---

## 7. VecNormalize：为什么要归一化

`make_venv` 里裹了一层 `VecNormalize(norm_obs=True, norm_reward=True, clip_obs=10.0)`：

- **观测归一化**：各 key 量纲差异大（yaw 是几十度、wind_speed 是个位数、功率是 1e6 量级）。用滑动均值/方差把它们拉到相近尺度，梯度才稳。
- **回报归一化**：稳定 advantage 的尺度，让 `learning_rate` 好调。
- `training=False` 时冻结统计量（评估/部署用训练期的均值方差，不再更新）。

> 多模态版有个关键区别：`norm_obs_keys` 只归一化标量+振动，**图像不使用 VecNormalize 的逐维运行统计**；它仍由当前图像输入和特征提取路径处理。不能一般化为“图像归一化会毁掉空间结构”。

---

## 8. 评估：确定性 rollout

`evaluate`（`wfrl/mappo_sb3.py::evaluate`）：

```python
while not done.all():
    action, _ = model.predict(obs, deterministic=True)  # 取均值动作，不采样
    obs, rew, done, _ = venv.step(action)
    ep_ret += float(rew[0])
```

- `deterministic=True`：用策略分布的均值而非采样，评估性能时去掉探索噪声。
- `rew[0]`：当前 `multiagent_env.py` 明确向每台机组分配同一份团队奖励，因此取一台累计是该实现的统计口径；不是由同构本身推出奖励相同。

---

## 9. 设计取舍总结

| 设计 | 原因 | 代价/局限 |
|---|---|---|
| 参数共享（一套网络） | 同一控制步可收集各机组经验；同构接口便于共享 | 异构机组不适用 |
| 伪装成 VecEnv | 零改动复用 SB3 成熟 PPO | 受限于 SB3 单智能体抽象 |
| aec_to_parallel（同时 step） | 一次 FLORIS 求解出全场，无等待 | 需要 Parallel 语义 |
| **局部 critic V(o_i)** | SB3 限制，实现简单 | **非平稳、信用分配弱 → 这是 IPPO 而非 MAPPO** |
| 不用 supersuit | 其 cloudpickle 无法序列化 FLORIS 的 generator | 自己写 VecEnv |

---

## 10. 仓库中已经实现的集中式 MAPPO

SB3 路线的 critic 输入仍为本机局部观测 `o_i`。当前另有两个集中式路径：

- [FLORIS MAPPO](../../wfrl/mappo_central.py)：共享 actor 读本机局部观测，`Critic` 读取七台观测拼成的全场状态，团队优势广播给各机组。
- [动态 MAPPO](../../scripts/train/train_fastfarm.py)：复用共享 Actor 与全场 Critic，支持 FAST.Farm，也支持 FLORIS；`--recurrent` 只将 critic 换为 GRU，actor 仍是前馈网络。

```text
IPPO：  actor(o_i)，critic(o_i)
MAPPO： actor(o_i)，critic(concat(o_1, ..., o_n))
```

本项目采用自写 actor/critic 实现不同输入，不再把“换框架”列为未完成前提。集中式输入可以提供全场信息；是否更稳定、是否增发电以及 GRU 是否有益，须通过匹配来流、约束、物理时长与统计窗口的对照验证，不能由架构推定。

---

## 11. 真实风场的尾流延时与后端选择（重要）

### 11.1 当前 FLORIS 是稳态——延时被抹掉了

每个 step，FLORIS 解的是「给定当前风况 + 所有机组 yaw，整场达到**稳态平衡**时的风速场」。它隐含假设「上游一改 yaw，下游**瞬间**感受到新尾流」，即**尾流传播延时 = 0**。这不是 bug，是稳态模型的定义。真实风场的「上下游 + 尾流平流延时」在当前实现里不存在。

### 11.2 延时有多大

```
机组间距 ~600–900 m，风速 ~8 m/s
单个间距延时 ≈ 距离 / 风速 ≈ 700 / 8 ≈ 约 90 秒
多排叠加 → 整场累积延时可达几分钟
```

延时的结构性影响：
1. **奖励延迟 + 非马尔可夫**：上游此刻偏航，对下游功率的影响几十秒~几分钟后才显现；瞬时观测 `o_i` 里没有这个信息。
2. **全场瞬时观测也不自动包含传播历史**：全局 critic 获得更丰富的当前状态，但是否改善延迟下的价值估计需对照证据。
3. **延时与记忆分别验证**：动态仿真器能提供传播时序，GRU 或历史堆叠可以作为候选；不能因此宣称任何一种记忆方案已有效。

### 11.3 WFCRL 两个后端的取舍

| 后端 | 尾流 | 延时 | 真实载荷时序 | 速度 |
|---|---|---|---|---|
| **FLORIS**（本 SB3 入口） | 稳态平衡 | **无** | 无（只能用代理量） | 快，秒级/step |
| **FAST.Farm** | 动态尾流蜿蜒(DWM) | **有，模型中的动态传播** | 有 | 慢一到几个数量级 |

> 本 SB3 入口使用 FLORIS；仓库动态路线已接入 FAST.Farm。稳态对照和动态时序须分别评价，不能将 FLORIS 代理观测当成结构响应。

### 11.4 计算与研究边界

当前 `mappo_central.py` 和 `train_fastfarm.py` 将网络设备设为 CPU；FAST.Farm 路线还涉及外部求解器、MPI、初始化和回合重启。更换 GPU 不会自动改变外部求解器的运行方式，性能瓶颈与并行方案应按目标环境计时，不把旧耗时当作当前保证。

本项目已有集中式 FLORIS、动态前馈 critic 和动态 GRU critic 的入口。下一步应先冻结对照口径，再判断需要哪些实验；当前文档更新不启动这些实验，也不认定 MAPPO 或记忆优于 IPPO。

---

## 附：关键数字速查（Ablaincourt, 7 机组）

| 量 | 值 | 来源 |
|---|---|---|
| num_envs（机组数） | 7 | `MAParallelVecEnv` |
| n_steps | 1024 | PPO 配置 |
| 每次采样 transition | 1024 × 7 = 7168 | 日志 total_timesteps 步长 |
| batch_size | 128 | PPO 配置 |
| n_epochs | 10 | PPO 配置 |
| net_arch | [128, 128] | 共享 MLP |
| 50k 探路 explained_variance | 历史文档报告 0.05 → 0.72 | 本轮未找到对应保留日志，未重新验证 |
