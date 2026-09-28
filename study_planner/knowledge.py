"""课程表：端到端自动驾驶 / RL / VLA 三个方向的分层精选资源。

这是整个工具的核心数据。设计要点：

1. **每条都带 `prereq`（前置依赖）**。planner 只会在前置全部完成后才推这条，
   避免出现"还没读 GAE 就被推 GRPO 论文"这种情况——那正是直接刷 arXiv 的典型翻车方式。

2. **`why` 字段是给未来的自己看的**。半年后回头看，最想知道的是"我当时为什么读它"。

3. **URL 全部经过 `cli.py check-links` 校验**。改动这里之后请重跑一次：
       python cli.py check-links

权重设计：主线是 RL × VLA 交叉点（`rl_x_vla` 权重最高），但不让基础饿死
（`rl_core` / `diffusion` 权重也不低），工程基建权重最低——它是随时能补的。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    """一个学习方向。"""

    id: str
    name: str       # 中文名，页面上显示
    weight: int     # 权重，越大越优先被排进每日计划
    desc: str       # 一句话说明这条 track 在你整体规划里的位置


@dataclass(frozen=True)
class Item:
    """课程表里的一条资源。"""

    id: str                      # 唯一标识，prereq 引用它
    title: str
    track: str                   # 对应 Track.id
    kind: str                    # "paper" | "repo" | "doc"
    url: str
    level: str                   # "foundation" | "intermediate" | "advanced"
    minutes: int                 # 预计耗时（分钟），用于凑每日预算
    why: str                     # 为什么现在读这个
    prereq: tuple[str, ...] = ()  # 前置 item id，全部完成才解锁
    tags: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 六个方向
# ---------------------------------------------------------------------------
TRACKS: tuple[Track, ...] = (
    Track(
        "rl_core", "RL 基础", 3,
        "补到「能读懂论文」为止，不追求理论完备。你的 PPO 已经能跑，缺的是记号背后的直觉。",
    ),
    Track(
        "diffusion", "扩散与生成式策略", 3,
        "承接你已有的半端到端 diffusion 基础，打通到 flow matching——现在 VLA 的主流动作头。",
    ),
    Track(
        "e2e_ad", "端到端自动驾驶", 4,
        "你的本行。重点不是会背模型名，而是搞清楚开环指标和闭环表现之间那道鸿沟。",
    ),
    Track(
        "vla", "视觉-语言-动作", 4,
        "从 RT-1 到 π0 的主干脉络。VLA 本质是系统工程问题，概念门槛没有 RL 高。",
    ),
    Track(
        "rl_x_vla", "RL 后训练 VLA（主线）", 5,
        "你的差异化定位所在。「懂 RL + 懂自动驾驶 + 能做 VLA 后训练」目前供给远小于需求。",
    ),
    Track(
        "tooling", "工程基建", 2,
        "仿真器、数据集、benchmark。随时能补，权重最低，但一个都跑不起来就做不了实验。",
    ),
)

TRACK_BY_ID: dict[str, Track] = {t.id: t for t in TRACKS}

# ---------------------------------------------------------------------------
# 课程表
# ---------------------------------------------------------------------------
ITEMS: tuple[Item, ...] = (
    # ======================= rl_core：RL 基础 =======================
    Item(
        "rl-spinningup", "Spinning Up：PPO 的实现拆解", "rl_core", "doc",
        "https://spinningup.openai.com/en/latest/algorithms/ppo.html",
        "foundation", 45,
        "你已经在跑 PPO 了，但先把每个超参的来历搞清楚。后面读所有 RL 论文都靠这套记号。",
        tags=("ppo", "基础"),
    ),
    Item(
        "rl-ppo-repo", "CleanRL：单文件 PPO 参考实现", "rl_core", "repo",
        "https://github.com/vwxyzjn/cleanrl",
        "foundation", 30,
        "拿去和你 games/ 里的 PPO 实现对读，能立刻看出自己的实现省掉了什么。",
        tags=("ppo", "代码"),
    ),
    Item(
        "rl-gae", "GAE：广义优势估计", "rl_core", "paper",
        "https://arxiv.org/abs/1506.02438",
        "foundation", 40,
        "优势估计是一切 PPO/GRPO 类方法的公共基础。不读这篇，读不懂它们后面的对比。",
        prereq=("rl-spinningup",),
        tags=("gae", "优势函数"),
    ),
    Item(
        "rl-trpo", "TRPO：信赖域策略优化", "rl_core", "paper",
        "https://arxiv.org/abs/1502.05477",
        "foundation", 35,
        "PPO 的小标题就是「TRPO 的简化版」。知道 PPO 在简化什么，才知道它牺牲了什么。",
        prereq=("rl-gae",),
        tags=("trpo", "策略梯度"),
    ),
    Item(
        "rl-sac", "Soft Actor-Critic", "rl_core", "paper",
        "https://arxiv.org/abs/1801.01290",
        "intermediate", 40,
        "off-policy 连续控制的标准答案。VLA 后训练普遍用 off-policy，这篇是绕不开的。",
        prereq=("rl-trpo",),
        tags=("sac", "off-policy"),
    ),
    Item(
        "rl-offline-cql", "CQL：保守 Q 学习", "rl_core", "paper",
        "https://arxiv.org/abs/2006.04779",
        "intermediate", 40,
        "离线 RL 的分布偏移问题，是「拿别人的数据训自己的策略」时最核心的坑。",
        prereq=("rl-sac",),
        tags=("离线rl", "分布偏移"),
    ),
    Item(
        "rl-offline-iql", "IQL：隐式 Q 学习", "rl_core", "paper",
        "https://arxiv.org/abs/2110.06169",
        "intermediate", 35,
        "IQL 完全不用查询分布外的动作，是离线 RL 里最实用的工程解之一。",
        prereq=("rl-offline-cql",),
        tags=("离线rl",),
    ),
    Item(
        "rl-residual", "Residual Policy Learning（残差策略）", "rl_core", "paper",
        "https://arxiv.org/abs/1812.06298",
        "intermediate", 30,
        "当前 VLA 后训练最主流的做法——冻结大模型、只训一个残差小策略。PLD 和 EXPO-FT 都建立在这个思想上。",
        prereq=("rl-sac",),
        tags=("残差rl", "主线相关"),
    ),
    Item(
        "rl-dt", "Decision Transformer", "rl_core", "paper",
        "https://arxiv.org/abs/2106.01345",
        "intermediate", 30,
        "把 RL 当序列建模问题。理解了这条线，才看得懂后来 VLA 为什么能直接把动作当 token 生成。",
        prereq=("rl-offline-iql",),
        tags=("序列建模",),
    ),
    Item(
        "rl-grpo", "DeepSeekMath：GRPO 的原始出处", "rl_core", "paper",
        "https://arxiv.org/abs/2402.03300",
        "advanced", 45,
        "GRPO 去掉了 critic 改用组内相对优势，是现在 VLA 后训练用得最多的优化器。必须知道它和 PPO 差在哪。",
        prereq=("rl-gae",),
        tags=("grpo", "主线相关"),
    ),

    # ======================= diffusion：扩散与生成式策略 =======================
    Item(
        "diff-ddpm", "DDPM：去噪扩散概率模型", "diffusion", "paper",
        "https://arxiv.org/abs/2006.11239",
        "foundation", 40,
        "所有扩散策略的源头。你已经做过 diffusion 的工作，这篇是回来补理论地基的。",
        tags=("扩散",),
    ),
    Item(
        "diff-ddim", "DDIM：确定性采样", "diffusion", "paper",
        "https://arxiv.org/abs/2010.02502",
        "foundation", 25,
        "把采样步数从 1000 压到 50 的第一步。实时性对 VLA 是生死问题，从这里开始建立直觉。",
        prereq=("diff-ddpm",),
        tags=("采样加速",),
    ),
    Item(
        "diff-dp", "Diffusion Policy", "diffusion", "paper",
        "https://arxiv.org/abs/2303.04137",
        "foundation", 45,
        "你的半端到端 diffusion 工作的直接前身。action chunking 这个概念就是从这篇来的。",
        prereq=("diff-ddpm",),
        tags=("扩散策略", "action-chunking"),
    ),
    Item(
        "diff-dp-repo", "Diffusion Policy 官方实现", "diffusion", "repo",
        "https://github.com/real-stanford/diffusion_policy",
        "foundation", 30,
        "跑起来看一眼真实的 action chunking 是怎么切、怎么拼的。论文里没写清楚的部分全在代码里。",
        prereq=("diff-dp",),
        tags=("代码",),
    ),
    Item(
        "diff-flow-matching", "Flow Matching for Generative Modeling", "diffusion", "paper",
        "https://arxiv.org/abs/2210.02747",
        "intermediate", 40,
        "π0、GR00T 这些新一代 VLA 的动作头全都换成了 flow matching。这是它相对扩散的优势所在。",
        prereq=("diff-ddim",),
        tags=("flow-matching", "主线相关"),
    ),
    Item(
        "diff-rectified-flow", "Rectified Flow", "diffusion", "paper",
        "https://arxiv.org/abs/2209.03003",
        "intermediate", 30,
        "直线化的概率流，是 flow matching 能做到少步推理的关键。",
        prereq=("diff-flow-matching",),
        tags=("flow-matching",),
    ),
    Item(
        "diff-act", "ACT / ALOHA：动作分块变压器", "diffusion", "paper",
        "https://arxiv.org/abs/2304.13705",
        "intermediate", 35,
        "低成本双臂遥操作 + 动作分块，是「让模仿学习真正能用」的一个转折点。",
        prereq=("diff-dp",),
        tags=("模仿学习",),
    ),
    Item(
        "diff-consistency", "Consistency Models：一致性模型", "diffusion", "paper",
        "https://arxiv.org/abs/2303.01469",
        "advanced", 30,
        "推理延迟是 VLA 落地的最大瓶颈，一致性模型是主要解法之一。理解它才好判断哪些加速方案能用在动作头上。",
        prereq=("diff-ddim",),
        tags=("采样加速",),
    ),

    # ======================= e2e_ad：端到端自动驾驶 =======================
    Item(
        "ad-nuscenes", "nuScenes 数据集", "e2e_ad", "paper",
        "https://arxiv.org/abs/1903.11027",
        "foundation", 25,
        "这个领域的几乎所有结果都挂在 nuScenes 上。知道它的标注内容和指标定义，读论文才不会被数字牵着走。",
        tags=("数据集",),
    ),
    Item(
        "ad-carla", "CARLA 仿真器", "e2e_ad", "paper",
        "https://arxiv.org/abs/1711.03938",
        "foundation", 25,
        "闭环评测的唯一现实选项。理解它的能力边界，才知道闭环结果能信到什么程度。",
        tags=("仿真器",),
    ),
    Item(
        "ad-survey", "端到端自动驾驶：挑战与前沿（综述）", "e2e_ad", "paper",
        "https://arxiv.org/abs/2306.16927",
        "foundation", 45,
        "建立地图用。读完应该能在白纸上画出「感知一体」和「规划一体」两条路线。",
        tags=("综述",),
    ),
    Item(
        "ad-uniad", "UniAD：把感知预测规划塞进一个 Transformer", "e2e_ad", "paper",
        "https://arxiv.org/abs/2212.10156",
        "foundation", 45,
        "端到端自动驾驶的里程碑。它的核心主张是「上游任务应该按对规划的贡献来排序」，这个思路影响至今。",
        prereq=("ad-survey",),
        tags=("里程碑",),
    ),
    Item(
        "ad-vad", "VAD：向量化场景表示", "e2e_ad", "paper",
        "https://arxiv.org/abs/2303.12077",
        "foundation", 40,
        "UniAD 太重，VAD 用向量化表示把计算量砍下来还保住了性能。理解这个折中很重要。",
        prereq=("ad-uniad",),
        tags=("向量化",),
    ),
    Item(
        "ad-transfuser", "TransFuser：多模态融合", "e2e_ad", "paper",
        "https://arxiv.org/abs/2205.15997",
        "intermediate", 35,
        "相机和 LiDAR 怎么融合进同一个 Transformer，这是感知侧的经典答案。",
        tags=("多模态",),
    ),
    Item(
        "ad-ego-status", "「开环评测到底考了什么？」", "e2e_ad", "paper",
        "https://arxiv.org/abs/2312.03031",
        "intermediate", 30,
        "专门打脸开环指标的论文：光靠自车状态就能刷出很好看的开环分数。读完它对所有 L2 指标都会保持警惕。",
        prereq=("ad-vad",),
        tags=("评测陷阱", "必读"),
    ),
    Item(
        "ad-bench2drive", "Bench2Drive：闭环评测基准", "e2e_ad", "paper",
        "https://arxiv.org/abs/2406.03877",
        "intermediate", 35,
        "上一篇指出问题，这篇给答案。UniAD 在开环很漂亮，闭环成功率只有 16% 左右——这个落差是这行的核心痛点。",
        prereq=("ad-ego-status",),
        tags=("闭环评测", "必读"),
    ),
    Item(
        "ad-navsim", "NAVSIM：更轻量的闭环评测", "e2e_ad", "paper",
        "https://arxiv.org/abs/2406.15349",
        "intermediate", 35,
        "CARLA 太慢，NAVSIM 是当前性价比最高的闭环验证方案。适合你快速迭代想法。",
        prereq=("ad-bench2drive",),
        tags=("闭环评测",),
    ),
    Item(
        "ad-genad", "GenAD：生成式范式做规划", "e2e_ad", "paper",
        "https://arxiv.org/abs/2402.11502",
        "advanced", 35,
        "把轨迹生成建模成生成问题，是从端到端走向生成式范式的代表工作。",
        prereq=("ad-navsim",),
        tags=("生成式",),
    ),
    Item(
        "ad-diffusiondrive", "DiffusionDrive：截断扩散做规划", "e2e_ad", "paper",
        "https://arxiv.org/abs/2411.15139",
        "advanced", 35,
        "扩散模型直接用在规划上，而且解决了实时性问题。和你已有的 diffusion 背景直接对口。",
        prereq=("diff-dp", "ad-genad"),
        tags=("扩散", "主线相关"),
    ),
    Item(
        "ad-drivelm", "DriveLM：用语言做驾驶推理", "e2e_ad", "paper",
        "https://arxiv.org/abs/2312.14150",
        "advanced", 30,
        "从纯端到端通向 VLA 的桥梁。读完这条，就能接上 vla 那条 track。",
        prereq=("ad-navsim",),
        tags=("语言模型", "过渡"),
    ),

    # ======================= vla：视觉-语言-动作 =======================
    Item(
        "vla-survey", "VLA 综述：面向具身智能", "vla", "paper",
        "https://arxiv.org/abs/2405.14093",
        "foundation", 50,
        "建地图用。VLA 论文迭代极快，先有骨架再追细节，否则会被淹没。",
        tags=("综述",),
    ),
    Item(
        "vla-rt1", "RT-1：机器人 Transformer", "vla", "paper",
        "https://arxiv.org/abs/2212.06817",
        "foundation", 40,
        "第一次证明大规模数据 + Transformer 能把机器人策略做到实用程度。VLA 这条线的起点。",
        prereq=("vla-survey",),
        tags=("里程碑",),
    ),
    Item(
        "vla-rt2", "RT-2：把动作当成文本 token", "vla", "paper",
        "https://arxiv.org/abs/2307.15818",
        "foundation", 35,
        "「动作即语言」这个关键跳跃。理解了它，才明白为什么 VLA 能直接继承 VLM 的能力。",
        prereq=("vla-rt1",),
        tags=("动作token化", "主线相关"),
    ),
    Item(
        "vla-openx", "Open X-Embodiment：跨机器人数据集", "vla", "paper",
        "https://arxiv.org/abs/2310.08864",
        "foundation", 35,
        "多机器人数据怎么统一格式。你想做通用策略的话，这是数据侧的地基。",
        prereq=("vla-rt2",),
        tags=("数据集",),
    ),
    Item(
        "vla-octo", "Octo：开源通用策略", "vla", "paper",
        "https://arxiv.org/abs/2405.12213",
        "intermediate", 35,
        "早期开源 VLA 的代表，架构简单、容易改。适合当复现的起点而不是 OpenVLA。",
        prereq=("vla-openx",),
        tags=("开源",),
    ),
    Item(
        "vla-openvla", "OpenVLA：开源 7B VLA", "vla", "paper",
        "https://arxiv.org/abs/2406.09246",
        "intermediate", 45,
        "当前 RL 后训练 VLA 最常用的基座（VLA-RL、PLD 都拿它做实验）。要动手就得先吃透它。",
        prereq=("vla-octo",),
        tags=("开源", "主线相关"),
    ),
    Item(
        "vla-openvla-repo", "OpenVLA 官方仓库", "vla", "repo",
        "https://github.com/openvla/openvla",
        "intermediate", 30,
        "看它的数据处理和动作反 token 化怎么写的——这是最容易被论文略过的部分。",
        prereq=("vla-openvla",),
        tags=("代码",),
    ),
    Item(
        "vla-fast", "FAST：高效动作 token 化", "vla", "paper",
        "https://arxiv.org/abs/2501.09747",
        "intermediate", 35,
        "动作怎么离散化，直接决定了训练效率和推理速度。这是 VLA 里最被低估的工程细节。",
        prereq=("vla-rt2",),
        tags=("动作token化", "主线相关"),
    ),
    Item(
        "vla-oft", "OpenVLA-OFT：并行解码 + 连续动作", "vla", "paper",
        "https://arxiv.org/abs/2502.19645",
        "advanced", 35,
        "把 OpenVLA 的推理速度提了一个量级。做实时控制必须了解这一套优化。",
        prereq=("vla-openvla", "vla-fast"),
        tags=("推理加速",),
    ),
    Item(
        "vla-pi0", "π0：流匹配 VLA", "vla", "paper",
        "https://arxiv.org/abs/2410.24164",
        "advanced", 45,
        "当前最强开源 VLA 之一，动作头换成了 flow matching。EXPO-FT 和 FPO 都基于它。",
        prereq=("diff-flow-matching", "vla-openvla"),
        tags=("flow-matching", "主线相关"),
    ),
    Item(
        "vla-pi05", "π0.5：开放世界泛化", "vla", "paper",
        "https://arxiv.org/abs/2504.16054",
        "advanced", 40,
        "从「会做训练过的任务」到「会做没见过的任务」，这是 VLA 走向实用的关键一步。",
        prereq=("vla-pi0",),
        tags=("泛化",),
    ),
    Item(
        "vla-groot", "GR00T N1：人形机器人基础模型", "vla", "paper",
        "https://arxiv.org/abs/2503.14734",
        "advanced", 40,
        "双系统架构（快慢系统）的代表。和你仓库里做人形/双足 RL 的背景能直接对上。",
        prereq=("vla-pi0",),
        tags=("双系统", "主线相关"),
    ),

    # ======================= rl_x_vla：RL 后训练 VLA（主线） =======================
    Item(
        "rlvla-vla-rl", "VLA-RL：把 VLA 后训练建模成多轮对话", "rl_x_vla", "paper",
        "https://arxiv.org/abs/2505.18719",
        "advanced", 45,
        "这条主线的开山工作。用 PPO + 过程奖励模型把 OpenVLA 在 LIBERO 上刷到超过微调基线，真机 60%→90%。",
        prereq=("vla-openvla", "rl-grpo"),
        tags=("主线", "必读"),
    ),
    Item(
        "rlvla-tgrpo", "TGRPO：轨迹级组相对策略优化", "rl_x_vla", "paper",
        "https://arxiv.org/abs/2506.08440",
        "advanced", 35,
        "把 GRPO 从单步扩展到轨迹级，并做阶段级稠密奖励。长程多阶段任务的关键。",
        prereq=("rl-grpo",),
        tags=("grpo",),
    ),
    Item(
        "rlvla-simplevla", "SimpleVLA-RL：更简单的 RL 扩展配方", "rl_x_vla", "paper",
        "https://arxiv.org/abs/2509.09674",
        "advanced", 40,
        "VLA-RL 的简化与规模化版本，有真机验证。想知道「最少需要哪些组件」就读这篇。",
        prereq=("rlvla-vla-rl",),
        tags=("主线",),
    ),
    Item(
        "rlvla-dppo", "DPPO：扩散策略的策略优化", "rl_x_vla", "paper",
        "https://arxiv.org/abs/2409.00588",
        "advanced", 40,
        "在扩散/流策略上做策略梯度最早的一批工作之一。读它能理解为什么 flow 上做 RL 这么难。",
        prereq=("diff-dp", "rl-trpo"),
        tags=("扩散策略", "rl"),
    ),
    Item(
        "rlvla-fpo", "FPO：免似然的流策略优化", "rl_x_vla", "paper",
        "https://arxiv.org/abs/2510.09976",
        "advanced", 45,
        "flow matching 的 ODE 不给出显式对数概率，PPO 的比值就没法算。FPO 用 CFM 损失的变化量构造代理比值绕开了这一点。",
        prereq=("diff-flow-matching", "rl-grpo"),
        tags=("flow-matching", "rl", "主线"),
    ),
    Item(
        "rlvla-pld", "PLD：残差 RL 生成数据再蒸馏回去", "rl_x_vla", "paper",
        "https://arxiv.org/abs/2511.00091",
        "advanced", 45,
        "ICLR 2026。冻结主干训残差专家 → 采集恢复数据 → 蒸馏回通用策略。LIBERO 刷到 99%，真机 1 小时无人干预。",
        prereq=("rl-residual",),
        tags=("残差rl", "蒸馏", "主线"),
    ),
    Item(
        "rlvla-expo-ft", "EXPO-FT：样本高效的 RL 微调", "rl_x_vla", "paper",
        "https://arxiv.org/abs/2605.25477",
        "advanced", 45,
        "Stanford。平均 19 分钟在线真机数据就把 π0.5 在 8 个任务上做到满分。样本效率是这个方向的生命线。",
        prereq=("rl-residual",),
        tags=("样本效率", "主线", "必读"),
    ),
    Item(
        "rlvla-rt-expo", "Real-Time EXPO-FT：快慢解耦", "rl_x_vla", "paper",
        "https://arxiv.org/abs/2609.18207",
        "advanced", 35,
        "大 VLA 推理太慢，观测到执行时已经过期。这篇把慢的生成和快的修正拆开，10 分钟数据把成功率从 42% 拉到 97%。",
        prereq=("rlvla-expo-ft",),
        tags=("实时性", "主线"),
    ),
    Item(
        "rlvla-openpi", "openpi：π0 官方实现", "rl_x_vla", "repo",
        "https://github.com/Physical-Intelligence/openpi",
        "advanced", 30,
        "π0/π0.5 的官方代码。上面几篇 RL 后训练工作大多基于它改，是你动手时的起点。",
        prereq=("vla-pi0",),
        tags=("代码", "主线"),
    ),

    # ======================= tooling：工程基建 =======================
    Item(
        "tool-libero-repo", "LIBERO：长程操作 benchmark", "tooling", "repo",
        "https://github.com/Lifelong-Robot-Learning/LIBERO",
        "foundation", 25,
        "上面一半的 RL×VLA 论文都在 LIBERO 上做实验。跑通它，你才看得懂那些数字是怎么来的。",
        tags=("benchmark",),
    ),
    Item(
        "tool-lerobot", "LeRobot：HuggingFace 的机器人学习库", "tooling", "repo",
        "https://github.com/huggingface/lerobot",
        "foundation", 25,
        "数据集格式、预训练模型、训练脚本一站配齐。上手成本最低的起点。",
        tags=("工具链",),
    ),
    Item(
        "tool-carla-repo", "CARLA 官方仓库", "tooling", "repo",
        "https://github.com/carla-simulator/carla",
        "foundation", 25,
        "闭环仿真绕不开。先确认自己机器的显卡能不能跑得动，再决定要不要投入。",
        tags=("仿真器",),
    ),
    Item(
        "tool-simplerenv", "SimplerEnv：真机对齐的仿真评测", "tooling", "repo",
        "https://github.com/simpler-env/SimplerEnv",
        "intermediate", 25,
        "仿真里刷分和真机能不能对上，是 VLA 最大的信任问题。这个环境专门用来测这件事。",
        prereq=("tool-libero-repo",),
        tags=("sim2real",),
    ),
    Item(
        "tool-robocasa", "RoboCasa：大规模日常操作环境", "tooling", "repo",
        "https://github.com/robocasa/robocasa",
        "intermediate", 25,
        "LIBERO 已经快被刷满了，RoboCasa 是新一代更难的操作基准。",
        prereq=("tool-libero-repo",),
        tags=("benchmark",),
    ),
    Item(
        "tool-navsim-repo", "NAVSIM 官方仓库", "tooling", "repo",
        "https://github.com/autonomousvision/navsim",
        "intermediate", 25,
        "想在自动驾驶侧快速验证想法，这是当前性价比最高的闭环评测工具链。",
        prereq=("ad-navsim",),
        tags=("闭环评测",),
    ),
    Item(
        "tool-openvla-oft-repo", "OpenVLA-OFT 官方仓库", "tooling", "repo",
        "https://github.com/moojink/openvla-oft",
        "intermediate", 25,
        "并行解码 + 连续动作头的完整实现。要做实时 VLA 就直接读它。",
        prereq=("vla-oft",),
        tags=("代码", "推理加速"),
    ),
    Item(
        "tool-lerobot-dataset", "LeRobot 数据集格式说明", "tooling", "doc",
        "https://huggingface.co/docs/lerobot/main/en/lerobot-dataset-v3",
        "intermediate", 25,
        "自己的数据怎么整理成标准格式。踩过这个坑的人都知道它值得先读。",
        prereq=("tool-lerobot",),
        tags=("数据格式",),
    ),
)

ITEM_BY_ID: dict[str, Item] = {it.id: it for it in ITEMS}


def items_of(track: str) -> tuple[Item, ...]:
    """取某条 track 下的全部条目，按 level 从基础到进阶排序。"""
    order = {"foundation": 0, "intermediate": 1, "advanced": 2}
    return tuple(sorted(
        (it for it in ITEMS if it.track == track),
        key=lambda it: (order.get(it.level, 9), it.id),
    ))


def check_consistency() -> list[str]:
    """自检课程表的内部一致性，返回问题列表（空列表 = 没问题）。

    检查三件事：track 是否存在、prereq 指向的 id 是否存在、有没有重复 id。
    """
    problems: list[str] = []
    seen: set[str] = set()

    for it in ITEMS:
        if it.id in seen:
            problems.append(f"重复的 item id：{it.id}")
        seen.add(it.id)

        if it.track not in TRACK_BY_ID:
            problems.append(f"{it.id} 的 track 不存在：{it.track}")

        for pre in it.prereq:
            if pre not in ITEM_BY_ID:
                problems.append(f"{it.id} 的前置 {pre} 不存在")

    # 有向图成环检查（课程表成环会导致永远解锁不了）
    state: dict[str, int] = {}  # 0=未访问 1=访问中 2=已完成

    def visit(node: str, path: list[str]) -> None:
        if state.get(node) == 1:
            problems.append(f"前置依赖成环：{' → '.join(path + [node])}")
            return
        if state.get(node) == 2:
            return
        state[node] = 1
        for pre in ITEM_BY_ID[node].prereq:
            if pre in ITEM_BY_ID:
                visit(pre, path + [node])
        state[node] = 2

    for it in ITEMS:
        visit(it.id, [])

    return problems
