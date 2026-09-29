"""全局配置：所有路径都从项目根目录推导，保证任何地方运行都一致。"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录 = 本文件的上两级（也就是放着 app.py / cli.py 的那层）
ROOT = Path(__file__).resolve().parent.parent

# 数据目录
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"          # arXiv / GitHub 抓取缓存（按日期分文件）
STATE_PATH = DATA_DIR / "state.json"    # 进度状态，CLI 和网页面板的唯一数据源
DEAD_LINKS_PATH = DATA_DIR / "dead_links.txt"   # check-links 的产物

# 论文/仓库正文的缓存。和抓取缓存不同：论文内容不会变，可以长期保留，
# 不像 arXiv 列表那样按天过期。
DOC_CACHE_DIR = CACHE_DIR / "docs"

# --- 项目本地凭据文件 -----------------------------------------------------
# 由 .claude/skills/study-planner-setup/scripts/onboard.py 写入，权限 600，
# .gitignore 已忽略。路径挂在项目根上而不是 CWD 上——从任何目录运行都指向同一个文件。
ENV_PATH = ROOT / ".env"

# 记录 .env 实际补进来的键。唯一用途是把「凭据来自哪」说准：
# 来自项目 .env 和来自环境变量，出问题时的排查方向完全不同。
ENV_APPLIED_KEYS: list[str] = []


def load_env_file(path=None) -> int:
    """把 .env 里的键值对补进 os.environ，返回补进去的条数。

    **必须在 study_planner/__init__.py 里调用，不能放在本模块末尾当副作用。**
    原因是实测出来的：`from study_planner import llm_plan` 这条路径会走
    __init__ → llm_plan → knowledge，而 knowledge.py 只 import dataclasses，
    **config 根本不会被导入**。偏偏 llm_plan.py 顶层的
    `MODEL_PLAN = os.environ.get(...)` 是模块级求值的，晚一步就没用了。
    放在包的 __init__ 里，任何 import 形式都必然先经过它。

    解析只做最小集：KEY=VALUE，允许 `export ` 前缀、允许成对引号，跳过空行和
    # 注释。刻意**不做**变量展开、不做转义处理——解析规则越多越容易写错，
    而这里写错的后果是「凭据静默失效」，很难查。

    **已存在于 os.environ 的键一律不覆盖**：你在终端里 export 的值优先级最高。
    这条规则和 llm_plan._ensure_credentials 保持一致，也和 onboard.py 写入时的
    提示一致——它会在检测到 shell 里已经 export 了同名变量时给出警告，
    因为那种情况下改 .env 是看不到效果的。
    """
    target = ENV_PATH if path is None else Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, ValueError):   # ValueError 覆盖 UnicodeDecodeError
        return 0

    applied = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        # 只剥**成对**的引号，不是把首尾引号一律删掉
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key or not value:
            continue
        if os.environ.get(key):
            continue
        os.environ[key] = value
        applied += 1
        if key not in ENV_APPLIED_KEYS:
            ENV_APPLIED_KEYS.append(key)

    return applied

# 全文喂给模型时的字符上限。超了就截断——截断这件事会同时告诉
# 用户（界面上）和模型（提示词里），不能悄悄截。
FULLTEXT_MAX_CHARS = 60_000

# --- 划词提问 -------------------------------------------------------------

# 一次选中最多引用多少字符。超了截断，同样要同时告诉人和模型。
# 这个值有三个消费方：阅读器页面的 DOC.quote_max、代理页面里桥接脚本的
# @@QUOTE_MAX@@、以及 app._chat_system 的提示词。都从这里出，别各写一份。
QUOTE_MAX_CHARS = 4000

# 引用块的起止标记。**必须**用常量而不是字面量：前端按它拼消息，
# 提示词按它告诉模型「夹在这里面的算正文」，两边差一个字符就是静默失效。
QUOTE_OPEN = "【选中原文】"
QUOTE_CLOSE = "【/选中原文】"

# --- 每日计划的可调参数 ---------------------------------------------------

# 每天打算投入的分钟数。用来控制推荐总量（精读 1 篇 + 小任务 1 个）。
DAILY_MINUTES = 45

# 预算宽容量（分钟）。分钟数是估算，超一点很正常；只有超出这个宽容量
# 才会提示「今天偏重、小任务可以顺延」，否则提示太吵没人会看。
BUDGET_SLACK = 10

# 每天抓几条"今日新鲜事"。这部分是补充，不是主线，页面上会明确标成可选。
FRESH_ITEMS = 3

# 标记"没读懂"的条目，隔几天重新推一次（间隔天数）
REVIEW_AFTER_DAYS = 3

# --- 实时抓取的参数 -------------------------------------------------------

# arXiv 分类与单次抓取上限（拉回来再按关键词过滤）
ARXIV_CATEGORIES = ("cs.RO", "cs.CV", "cs.AI")
ARXIV_FETCH_LIMIT = 80

# 命中任一关键词才算和你的方向相关（小写匹配）
ARXIV_KEYWORDS = (
    "end-to-end driving",
    "autonomous driving",
    "vision-language-action",
    "vla",
    "diffusion policy",
    "reinforcement learning",
    "imitation learning",
    "world model",
    "closed-loop",
    "robot manipulation",
    "flow matching",
)

# GitHub 仓库搜索词
GITHUB_QUERIES = (
    "vision-language-action",
    "end-to-end autonomous driving",
    "reinforcement learning robot",
)

# 网络超时（秒）。抓不到就降级成纯课程计划，绝不阻塞。
HTTP_TIMEOUT = 12

# 抓取失败时最多重试几次
HTTP_RETRIES = 2

# --- 网页面板 -------------------------------------------------------------

# 默认端口避开 gf-companion 的 8765
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8766
