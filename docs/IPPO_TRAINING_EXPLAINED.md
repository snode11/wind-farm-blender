# 参数共享 IPPO 训练全流程（WFCRL 风场）

> 本文档解释 `train_mappo.py` 里这套「参数共享 PPO」的**数据流转**与**设计原因**，配真实源码片段。
>
> **命名澄清**：文件叫 `train_mappo.py`，但严格分类，当前实现是**参数共享 IPPO**——所有机组共享一套网络（参数共享 ✅），但 critic 只看单台机组的局部观测、没有全局状态（集中式 critic ❌）。要升级成教科书 MAPPO，需给 critic 额外喂全局状态，SB3 做不了、需换框架。详见文末「如何变成真 MAPPO」。

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

对应源码（`train_mappo.py:137-165`）：

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

`MAParallelVecEnv.__init__`（`train_mappo.py:48-70`）：

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

这是你问的核心——「难道要等最慢的 agent？」答案在 `step_wait`（`train_mappo.py:81-103`）：

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

`_pack_obs`（`train_mappo.py:105-113`）：

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
- ✅ 是「7 台的经验 = 7 倍数据量，喂同一套网络」——**数据效率是单 agent 的 7 倍**，这是参数共享最大的好处。

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

> 多模态版有个关键区别：`norm_obs_keys` 只归一化标量+振动，**图像不归一化**（归一化会毁掉 CNN 依赖的空间结构）。

---

## 8. 评估：确定性 rollout

`evaluate`（`train_mappo.py:168-182`）：

```python
while not done.all():
    action, _ = model.predict(obs, deterministic=True)  # 取均值动作，不采样
    obs, rew, done, _ = venv.step(action)
    ep_ret += float(rew[0])
```

- `deterministic=True`：用策略分布的均值而非采样，评估性能时去掉探索噪声。
- `rew[0]`：因 7 台同构、共享奖励结构，取一台的回报做代表即可。

---

## 9. 设计取舍总结

| 设计 | 原因 | 代价/局限 |
|---|---|---|
| 参数共享（一套网络） | 数据效率 ×7；同构机组天然适用 | 异构机组不适用 |
| 伪装成 VecEnv | 零改动复用 SB3 成熟 PPO | 受限于 SB3 单智能体抽象 |
| aec_to_parallel（同时 step） | 一次 FLORIS 求解出全场，无等待 | 需要 Parallel 语义 |
| **局部 critic V(o_i)** | SB3 限制，实现简单 | **非平稳、信用分配弱 → 这是 IPPO 而非 MAPPO** |
| 不用 supersuit | 其 cloudpickle 无法序列化 FLORIS 的 generator | 自己写 VecEnv |

---

## 10. 如何变成真 MAPPO（集中式 critic）

当前 critic 输入 = 单台机组的局部观测 `o_i`，看不到别人在干嘛 → 多智能体**非平稳**（别人也在学，回报在漂移）→ 价值估计噪声大。

真 MAPPO 的改法：**actor 不变**（仍只吃局部 `o_i`，保证去中心化执行），但 **critic 额外吃一个「全局状态」**（如 7 台的 yaw + 上游尾流 + 总功率拼成的 global obs）：

```
IPPO :  V_i(o_i)                 只看自己
MAPPO:  V(s) 或 V(o_1..o_n)       训练时看全局；执行时 critic 丢弃，仍去中心化
```

对风场特别有意义：wake steering 是**强耦合**的（上游偏航影响下游功率），全局 critic 能更准地评估「上游这一偏航对整场总功率的贡献」，信用分配更准 → 训练更稳。

**代价**：SB3 的 PPO 不支持 actor/critic 用不同输入，做不了。需换框架（MAPPO 官方实现 / MARLlib / EPyMARL），或自写 actor-critic 让 value 网络吃 global state。

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
2. **IPPO 最吃亏、MAPPO 最受益**：局部 critic `V(o_i)` 看不到「尾流正在路上」，全局 critic 能看到 → 信用分配准得多。
3. **光靠 MAPPO 不够**：延时使系统对瞬时观测非马尔可夫，还需 **时序记忆**（RNN/LSTM critic 或 frame-stacking）+ **动态仿真器**。

### 11.3 WFCRL 两个后端的取舍

| 后端 | 尾流 | 延时 | 真实载荷时序 | 速度 |
|---|---|---|---|---|
| **FLORIS**（当前） | 稳态平衡 | **无** | 无（只能用代理量） | 快，秒级/step |
| **FAST.Farm** | 动态尾流蜿蜒(DWM) | **有，真实平流延时** | 有 | 慢一到几个数量级 |

> 之前选「FLORIS 代理量」而非 FAST.Farm，代价就是这里：无延时、无真实振动时序。要研究「延时下的多智能体协同」必须切 FAST.Farm。

### 11.4 一个常见误区：GPU 不加速 FAST.Farm

FAST.Farm 是 NREL 的 CPU 气弹仿真器（OpenFAST 家族，Fortran），**不吃 GPU**。GPU 只能加速神经网络训练，而这里的网络（[128,128] MLP，即使加 CNN）算力微不足道、根本不是瓶颈。瓶颈永远是**仿真器的 CPU 串行推进**。所以「上 GPU 跑 FAST.Farm」是误解——要提速只能靠 **CPU 多核 + 多环境并行采样**（多个 FAST.Farm 实例并行 rollout）。

### 11.5 真实风场用 IPPO 还是 MAPPO

- **工业界实机**：基本不用 RL；主流是离线稳态 FLORIS 优化出的 yaw 查找表 + MPC + 现场反馈。RL 实时控制仍属研究阶段。
- **研究（仿真训练）**：IPPO 和 MAPPO 都有人用，无唯一标准。延时/强耦合场景理论上偏向 MAPPO（集中式 critic 信用分配好），但 IPPO 是出了名的强基线。合理配方常是 **MAPPO + 时序记忆 + 动态仿真器** 三者一起。

### 11.6 本项目建议路线

1. **先在 FLORIS 上做真 MAPPO（集中式 critic）对照 IPPO**：即使无延时，wake steering 的空间耦合仍在，先低成本验证集中式 critic 有没有用。
2. **再切 FAST.Farm 动态后端 + 加时序记忆**：这才真正体现延时物理，但要付出算力代价（CPU 多核 + 多实例并行，非 GPU）。

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
| 50k 探路 explained_variance | 0.05 → 0.72 | 训练日志 |
