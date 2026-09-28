"""全局配置：所有路径都从项目根目录推导，保证任何地方运行都一致。"""
from __future__ import annotations

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

# 全文喂给模型时的字符上限。超了就截断——截断这件事会同时告诉
# 用户（界面上）和模型（提示词里），不能悄悄截。
FULLTEXT_MAX_CHARS = 60_000

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
