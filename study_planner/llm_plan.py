"""用 Claude 给今天的计划写一段个性化点评（可选功能）。

参照 gf-companion/gf_companion/llm_analyze.py 的写法。和那边最大的区别是
**降级策略**：那个工具没配 key 就直接退出，这里不行——网页面板不能因为
没配 key 就打不开。所以三条失败路径（缺依赖 / 没凭据 / 模型拒答）全部
返回模板点评，而不是抛异常。
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from . import config
from .knowledge import TRACK_BY_ID

# 默认用 Claude Opus 4.8（官方推荐默认档位）。
# 可以用环境变量覆盖——如果你走的是第三方 Anthropic 兼容端点（比如 DeepSeek），
# 那边的模型是按名字映射的，换个名字就是换模型，不用改代码：
#     export STUDY_PLANNER_MODEL=claude-haiku-4-5
MODEL_PLAN = os.environ.get("STUDY_PLANNER_MODEL", "claude-opus-4-8")

# 点评是个简单任务，用低 effort 就够：省 token 也省时间。
# 注意 effort 和结构化输出的 format 都放在 output_config 里，不是顶层参数。
EFFORT = "low"

MAX_TOKENS = 2000

# 结构化输出：保证拿到的永远是合法 JSON，不用做字符串解析
_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "comment": {
            "type": "string",
            "description": "3-5 句中文点评：为什么今天推这两条、和已学内容的关联、一个具体提醒",
        },
        "focus_hint": {
            "type": "string",
            "description": "一句话，明天开始前该记住的事",
        },
    },
    "required": ["comment", "focus_hint"],
    "additionalProperties": False,
}

_COMMENT_RULES = """你会收到今天的计划、最近几天的完成记录、以及各方向的进度。

请写一段点评（简体中文，3-5 句），要求：
1. 说清楚**为什么今天推这两条**——它们在他整体路径里的位置是什么。
2. 如果今天的条目和最近几天学过的内容有承接关系，指出来。
3. 给一个**具体**的提醒（例如「这篇的 3.2 节和昨天那篇的公式是同一个东西」），
   不要写成「加油」「坚持」这类空话。
4. 客观、直接，不吹捧。如果进度慢了就直说。
5. 不要复述计划里已经有的标题和链接，他不知道的信息才值得写。
6. 如果结构化输出不可用（有些兼容端点会忽略它），就把点评写在正文里，
   然后在最后**单独一行**以「提醒：」开头写那一句话提醒。"""

_PLAIN_TAIL = """

请只输出一个 JSON 对象，不要有任何其他文字、不要用 markdown 代码块包裹，格式严格如下：
{"comment": "你的点评（3-5 句）", "focus_hint": "一句话提醒"}"""


def _who(profile: Optional[dict] = None) -> str:
    """「这个工具在为谁服务」。

    **不能写死。** 原来这里硬编码的是「帮一位做自动驾驶的工程师 / 他的背景是
    PPO / 主线是 RL 后训练 VLA」——换个人用这个仓库，他会收到满篇 PPO/VLA 的点评，
    哪怕他的课程表里一条 VLA 都没有。

    有画像就用画像。没有画像就**用当前生效的课程表来描述**——那同样能反映
    用户实际在学什么，而且手写课程表（没写画像）的人也能得到贴切的点评。
    """
    profile = profile or {}

    topics = str(profile.get("topics") or "").strip()
    level = str(profile.get("level") or "").strip()
    goal = str(profile.get("goal") or "").strip()

    if topics or level or goal:
        bits = ["你是一个学习规划助手，帮一位自学者安排每天的自学。"]
        if topics:
            bits.append(f"他的方向：{topics}")
        if level:
            bits.append(f"他的基础：{level}")
        if goal:
            bits.append(f"他的目标：{goal}")
        return "\n".join(bits)

    # 兜底：从课程表反推。这里 import TRACKS 而不是模块顶层，
    # 是因为它取决于 _resolve_curriculum() 的结果，而那个在 knowledge 里是 import 期定的。
    from .knowledge import TRACKS

    names = "、".join(t.name for t in TRACKS)
    detail = "\n".join(f"- {t.name}：{t.desc}" for t in TRACKS if t.desc)
    return (
        "你是一个学习规划助手，帮一位自学者安排每天的自学。\n"
        f"他当前的课程表覆盖这些方向：{names}\n{detail}"
    )


def _system(profile: Optional[dict] = None) -> str:
    return _who(profile) + "\n\n" + _COMMENT_RULES


def _system_plain(profile: Optional[dict] = None) -> str:
    # 不是所有 Anthropic 兼容端点都支持 output_config 的结构化输出。
    # 这条是降级路径用的：不用任何特殊参数，纯靠在提示词里要 JSON。
    return _system(profile) + _PLAIN_TAIL


# Claude Code 把凭据放在这些文件里。它注入给的是自己的进程，
# **不会**出现在你自己的终端环境里——这就是「在 Claude Code 里测着好好的，
# 你自己跑就报认证失败」的原因。所以这里主动去读一下作为补充。
_CLAUDE_SETTINGS_CANDIDATES = (
    Path.home() / ".claude" / "settings.json",
    Path.home() / ".claude.json",
    Path.cwd() / ".claude" / "settings.json",
)

_CRED_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")

# 记下凭据是从哪来的，启动时打印出来，别让用户猜
CREDENTIAL_SOURCE = ""


def _ensure_credentials() -> str:
    """确保环境里有凭据。返回来源描述（空串表示环境里本来就有）。

    只补环境里**没有**的键，绝不覆盖你已经 export 的值。
    """
    global CREDENTIAL_SOURCE

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        # 别把「项目 .env」说成「环境变量」。两者排查方向完全不同：
        # 环境变量可能是你在 shell profile 里 export 的、也可能是别人给的；
        # .env 是你自己（或配置脚本）写在这个仓库里的一个文件。
        # 启动横幅（app.py 里的「🔑 模型凭据来自：」）就靠这里报准。
        if config.ENV_APPLIED_KEYS:
            CREDENTIAL_SOURCE = CREDENTIAL_SOURCE or str(config.ENV_PATH)
            return CREDENTIAL_SOURCE
        CREDENTIAL_SOURCE = CREDENTIAL_SOURCE or "环境变量"
        return ""

    for path in _CLAUDE_SETTINGS_CANDIDATES:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        env = data.get("env")
        if not isinstance(env, dict):
            continue

        applied = []
        for key in _CRED_KEYS:
            value = env.get(key)
            if value and not os.environ.get(key):
                os.environ[key] = str(value)
                applied.append(key)

        if applied:
            CREDENTIAL_SOURCE = f"{path}"
            return CREDENTIAL_SOURCE

    return ""


def credential_hint() -> str:
    """没有任何可用凭据时，给用户看的可操作提示。"""
    return (
        "没有找到模型凭据。任选一种办法：\n"
        f"  1. 跑一次配置脚本（推荐，会写 {config.ENV_PATH}，权限 600）：\n"
        "       python3 .claude/skills/study-planner-setup/scripts/onboard.py\n"
        "  2. 在你的终端里 export 之后重启面板：\n"
        "       export ANTHROPIC_AUTH_TOKEN=<你的 key>\n"
        "       export ANTHROPIC_BASE_URL=<兼容端点，比如 https://api.deepseek.com/anthropic>\n"
        "     （用官方 Anthropic 的话只设 ANTHROPIC_API_KEY 即可）\n"
        "  3. 写进 ~/.claude/settings.json 的 env 块里 —— 本工具会自动读取\n"
        "\n"
        "注意：Claude Code 自己进程里的环境变量不会传给你手动启动的程序，\n"
        "所以「在 Claude Code 里能用」不代表这里能用。\n"
        "另外改了 .env 要重启面板才生效——它只在进程启动时读一次。"
    )


def _client():
    """建 Claude 客户端。返回 None 表示缺依赖，由调用方降级。

    注意：SDK 的构造函数在没有凭据时**不会报错**，要等到真正发请求才抛
    `Could not resolve authentication method`。所以不能靠 try/except 判断
    「有没有凭据」——必须主动检查。这个坑踩过一次。
    """
    try:
        import anthropic
    except ImportError:
        return None

    _ensure_credentials()

    try:
        return anthropic.Anthropic()
    except Exception:
        return None


def has_credentials() -> bool:
    """真的检查过环境，而不是押 SDK 的构造函数会报错。"""
    _ensure_credentials()
    return bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )


def _text_of(response) -> str:
    """从响应里取出文本（跳过 thinking 块）。"""
    for block in response.content:
        if getattr(block, "type", "") == "text":
            return block.text
    return ""


def _context(plan: dict, progress, day: date) -> str:
    """把计划 + 进度整理成给模型看的上下文。"""
    def one(item: Optional[dict]) -> Optional[dict]:
        if not item:
            return None
        return {
            "title": item["title"],
            "track": item.get("track_name", item.get("track")),
            "level": item.get("level"),
            "minutes": item.get("minutes"),
            "why": item.get("why"),
            "tags": item.get("tags", []),
        }

    # 最近 7 天的完成情况
    recent = []
    since = day - timedelta(days=7)
    for entry in progress.history:
        try:
            d = date.fromisoformat(entry.get("date", ""))
        except ValueError:
            continue
        if d >= since:
            recent.append({
                "date": entry["date"],
                "main": entry.get("main"),
                "task": entry.get("task"),
                "review": entry.get("review"),
            })

    tracks = [
        {
            "方向": TRACK_BY_ID[tid].name if tid in TRACK_BY_ID else tid,
            "已完成": st["done"],
            "总数": st["total"],
        }
        for tid, st in progress.track_stats().items()
    ]

    payload = {
        "今天": day.isoformat(),
        "今日精读": one(plan.get("main")),
        "今日小任务": one(plan.get("task")),
        "今日复习": one(plan.get("review")),
        "今日实时抓取条数": len(plan.get("fresh") or []),
        "最近7天记录": recent,
        "各方向进度": tracks,
        "连续学习天数": progress.current_streak(day),
        "总进度": f"{plan.get('done_total', 0)}/{plan.get('total_items', 0)}",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def template_comment(plan: dict, progress, day: date, reason: str = "") -> dict:
    """不依赖任何外部服务的兜底点评。信息量低，但保证页面永远有内容。

    `reason` 非空时会把失败原因一并展示。这一点很重要：
    「没配凭据」和「配了但端点不兼容」如果都显示同一句话，你根本无从判断该修什么。
    """
    main = plan.get("main")
    task = plan.get("task")
    streak = progress.current_streak(day)

    if reason:
        tail = f"（AI 点评不可用：{reason}）"
    else:
        tail = "（配置 ANTHROPIC_API_KEY 后这里会有 Claude 写的个性化点评。）"

    if main is None:
        return {
            "comment": "课程表里已解锁的条目都做完了。可以回头把标了「没读懂」的条目再看一遍，"
                       "或者跑 `python cli.py profile --regenerate` 换一份课程表。" + tail,
            "focus_hint": "把不懂的地方记下来，比继续往前赶更有价值。",
            "source": "template",
        }

    bits = [f"今天主推《{main['title']}》（{main.get('track_name')}）。"]
    if main.get("why"):
        bits.append(main["why"])
    if task:
        bits.append(f"小任务是《{task['title']}》，和精读刻意换了条方向做穿插。")
    if streak > 1:
        bits.append(f"已连续 {streak} 天。")
    bits.append(tail)

    return {
        "comment": " ".join(bits),
        "focus_hint": "读完先用自己的话写下结论，再决定要不要读第二遍。",
        "source": "template",
    }


def _extract_json(text: str) -> Optional[dict]:
    """尽量从模型回复里抠出一个 JSON 对象。

    兼容端点不保证支持结构化输出，返回的可能是一段自由文本，
    所以这里退而求其次：先直接解析，不行就从文本里找第一个 {...}。
    """
    text = (text or "").strip()
    if not text:
        return None

    # 去掉 markdown 代码块围栏（有些模型习惯包一层 ```json）
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass

    # 抠出最外层的一对花括号
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except ValueError:
            return None
    return None


def _split_plain(text: str) -> tuple[str, str]:
    """从自由文本里分出「点评」和「提醒」两段。

    约定：最后单独一行以「提醒：」开头。用于不支持结构化输出的端点
    （比如 DeepSeek 的 Anthropic 兼容层——它接受 output_config 但不执行）。
    """
    m = re.search(r"\n\s*提醒[：:]\s*(.+?)\s*$", text, re.S)
    if m:
        comment = text[:m.start()].strip()
        hint = m.group(1).strip()
        if comment:
            return comment, hint
    return text.strip(), ""


def _call(client, user_content: str, structured: bool, *,
          system: str, schema: dict, max_tokens: int = MAX_TOKENS,
          effort: str = EFFORT):
    """发一次请求。structured=True 时带上结构化输出参数。

    system / schema / max_tokens / effort 都做成参数，是为了让「写点评」和
    「生成课程表」共用这一条请求路径——两件事的降级逻辑（兼容端点不认
    output_config）一模一样，各写一份迟早会漂移。
    """
    kwargs: dict = {
        "model": MODEL_PLAN,
        "max_tokens": max_tokens,
        "system": system if structured else system + _PLAIN_TAIL,
        "messages": [{"role": "user", "content": user_content}],
    }
    if structured:
        # Opus 4.8 省略 thinking 就是「不思考」，要开就得显式写 adaptive。
        # 注意这两个参数都放在 output_config 里，不是顶层。
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        }
    return client.messages.create(**kwargs)


def _short_err(e: BaseException) -> str:
    """把异常压成一句人看得懂的话，太长的截断。"""
    msg = str(e).strip().replace("\n", " ")
    return f"{type(e).__name__}: {msg}"[:160]


# ---------------------------------------------------------------------------
# 按用户画像生成课程表
# ---------------------------------------------------------------------------
MAX_TOKENS_CURRICULUM = 8000
# 生成课程表是重活，不能用点评那个 low effort。8000 token 够放 30 条。
CURRICULUM_EFFORT = "high"

_CURRICULUM_SCHEMA = {
    "type": "object",
    "properties": {
        "tracks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "weight": {"type": "integer"},
                    "desc": {"type": "string"},
                },
                "required": ["id", "name", "weight", "desc"],
                "additionalProperties": False,
            },
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "track": {"type": "string"},
                    "kind": {"type": "string", "enum": ["paper", "repo", "doc"]},
                    "url": {"type": "string"},
                    "level": {"type": "string",
                              "enum": ["foundation", "intermediate", "advanced"]},
                    "minutes": {"type": "integer"},
                    "why": {"type": "string"},
                    "prereq": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "title", "track", "kind", "url", "level",
                             "minutes", "why", "prereq", "tags"],
                "additionalProperties": False,
            },
        },
        # 实时抓取的口味也一起生成。不让用户自己填、也不从中文 topics 里切词：
        # arXiv 的标题是英文，拿中文关键词去过滤会**一条都匹配不上**，
        # 而那个失败是静默的——「新鲜事」那一块直接变空，不报任何错。
        "arxiv_keywords": {"type": "array", "items": {"type": "string"}},
        "github_queries": {"type": "array", "items": {"type": "string"}},
        "arxiv_categories": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tracks", "items", "arxiv_keywords", "github_queries", "arxiv_categories"],
    "additionalProperties": False,
}

_SYSTEM_CURRICULUM = """你是课程设计助手。给你一份自学者的自述，你要为他生成一份**能照着执行**的课程表。

硬性要求（不满足的会被程序拒收）：
1. 3–5 个方向（tracks），10–30 个条目（items）。别贪多——塞满 30 条但几天就停，
   不如 15 条能走完。
2. id 用「方向前缀-短名」的 ASCII 小写连字符形式，例如 qc-surface-code。
3. prereq 只能引用**本次输出里的** id，不要成环，**必须**存在若干 prereq 为空的
   入口条目，而且每一条都能从某个入口沿 prereq 走到。做不到的话有些条目永远解锁不了。
4. kind 只能是 paper / repo / doc，其中 **repo + doc 至少 6 条**。
   本工具每天固定推一条「动手任务」，只从这两类里挑；全是论文的话那个位置会天天空着。
5. level 只能是 foundation / intermediate / advanced。每条方向至少要有一个 foundation 入口。
6. 每条给一个分钟数，10–120 之间。
7. track 的 weight 取 1–5，用户目标最核心的那条给最高。

URL 规则（这一条最重要，写错就是给用户一个打不开的链接）：
- 论文用 arXiv 的 abs 页：https://arxiv.org/abs/<id>。**只写你确定存在的编号**。
  拿不准就换一篇你确信的经典工作或该领域的权威综述，**不要猜编号**。
- 仓库用 https://github.com/<owner>/<repo>；文档用官方文档地址。
- 实在不确定确切地址，就用该方向公认的经典教材/综述的稳定页面。
  宁可给一篇"人人都知道"的老文章，也不要编一个看起来很像的链接。

文字要求：
- title / why / desc 全部用简体中文，**第二人称「你」**。
- why 写「为什么是现在读它、读完能接上什么」，不要写「很重要」「必读」这种空话。
- 结合自述里提到的已有基础，明确说哪一条在补哪块缺口。
- 用户的目标（goal）决定主线方向，把它排在权重最高的 track 里。

另外还要给三个**英文**抓取参数，用来从 arXiv / GitHub 实时捞新内容：
- arxiv_keywords：6–12 个小写英文关键词或短语，命中才算相关。用这个领域的通行说法
  （例如 "surface code" 而不是 "量子纠错"）——arXiv 的标题是英文，中文关键词一条都匹配不上。
- github_queries：2–4 个英文搜索词，用来找相关仓库。
- arxiv_categories：1–3 个 arXiv 分类号（例如 quant-ph、cs.LG、cs.RO）。"""


def _curriculum_user_content(profile: dict) -> str:
    """给模型的输入：画像 + 一份字段样例。

    **不要把内置课程表的内容整份塞进去**——那是 59 条另一个领域的条目，
    会把模型锚到那个领域上去。只给两三条样例让它看清字段写法和行文风格。
    """
    from dataclasses import asdict

    from .knowledge import ITEMS

    sample = []
    for it in ITEMS[:2]:
        d = asdict(it)
        d["prereq"] = list(it.prereq)
        d["tags"] = list(it.tags)
        sample.append(d)

    payload = {
        "自述": {
            "想学的方向": profile.get("topics") or "",
            "已有的基础": profile.get("level") or "",
            "想达到的目标": profile.get("goal") or "",
            "每天能投入的分钟数": profile.get("daily_minutes") or config.DAILY_MINUTES,
        },
        "字段样例（只示范字段写法和行文风格，不要照抄内容）": sample,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def generate_curriculum(profile: dict) -> dict:
    """按画像生成一份课程表。**永远返回 dict，不抛异常。**

    返回 `{"ok": bool, "tracks": [...], "items": [...], "error": str}`，
    tracks/items 是**原始 JSON 里的 dict 列表**，还没经过 parse/validate。

    刻意不在这里读写文件：调用方拿到结果先校验，通过了再决定落盘。
    生成失败绝不能留下半份 curriculum.json——那份文件下次 import 会被判为
    不可用然后**静默退回内置课程表**，用户还以为生成成功了。
    """
    def fail(msg: str) -> dict:
        return {"ok": False, "tracks": [], "items": [], "error": msg}

    client = _client()
    if client is None:
        return fail("缺少 anthropic 依赖，跑一下 pip install -r requirements.txt")
    if not has_credentials():
        return fail("没有配置模型凭据，先跑一次 onboard.py")

    user_content = _curriculum_user_content(profile)

    try:
        resp = _call(client, user_content, structured=True,
                     system=_SYSTEM_CURRICULUM, schema=_CURRICULUM_SCHEMA,
                     max_tokens=MAX_TOKENS_CURRICULUM, effort=CURRICULUM_EFFORT)
    except Exception:
        try:
            # 兼容端点（DeepSeek 之类）接受 output_config 但不执行，退回纯提示词约束
            resp = _call(client, user_content, structured=False,
                         system=_SYSTEM_CURRICULUM, schema=_CURRICULUM_SCHEMA,
                         max_tokens=MAX_TOKENS_CURRICULUM, effort=CURRICULUM_EFFORT)
        except Exception as e:
            return fail(_short_err(e))

    if getattr(resp, "stop_reason", None) == "refusal":
        return fail("模型拒答")

    # 被 max_tokens 截断的话 JSON 一定解析不出来，而这和"模型乱答"是两回事——
    # 报成后者会让人去改提示词，其实只要少要几条。
    if getattr(resp, "stop_reason", None) == "max_tokens":
        return fail(
            f"回复被 max_tokens={MAX_TOKENS_CURRICULUM} 截断了，"
            "生成的条目太多。重试一次，或者把方向描述写具体一点。"
        )

    text = _text_of(resp).strip()
    data = _extract_json(text)

    if not isinstance(data, dict):
        head = text[:200].replace("\n", " ") if text else "(空回复)"
        return fail(f"没能从回复里解析出 JSON。回复开头：{head}")

    tracks = data.get("tracks")
    items = data.get("items")
    if not isinstance(tracks, list) or not isinstance(items, list):
        return fail("回复里的 tracks / items 不是数组")

    def strs(key: str) -> list:
        v = data.get(key)
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []

    return {
        "ok": True, "tracks": tracks, "items": items, "error": "",
        "arxiv_keywords": strs("arxiv_keywords"),
        "github_queries": strs("github_queries"),
        "arxiv_categories": strs("arxiv_categories"),
    }


def generate_comment(plan: dict, progress, day: date) -> dict:
    """生成点评。永远返回一个可用的 dict，不会抛异常。"""
    client = _client()
    if client is None:
        return template_comment(plan, progress, day, reason="缺少 anthropic 依赖")
    if not has_credentials():
        return template_comment(plan, progress, day, reason="没有配置模型凭据")

    user_content = _context(plan, progress, day)
    system = _system(config.PROFILE)

    # 第一步：带结构化输出（Anthropic 原生支持，能保证拿到合法 JSON）
    try:
        resp = _call(client, user_content, structured=True,
                     system=system, schema=_PLAN_SCHEMA)
    except Exception:
        # 降级 2：有些 Anthropic 兼容端点（比如 DeepSeek）不认 output_config，
        # 换成不带任何特殊参数的普通请求重试一次，改由提示词约束 JSON 格式。
        try:
            resp = _call(client, user_content, structured=False,
                         system=system, schema=_PLAN_SCHEMA)
        except Exception as e:
            return template_comment(plan, progress, day, reason=_short_err(e))

    # 降级 3：模型拒答。必须先判 stop_reason 再读 content，
    # 否则拒答时 content 可能是空数组，直接索引会抛 IndexError。
    if getattr(resp, "stop_reason", None) == "refusal":
        return template_comment(plan, progress, day, reason="模型拒答")

    text = _text_of(resp).strip()
    data = _extract_json(text)

    if data:
        comment = (data.get("comment") or "").strip()
        if comment:
            return {
                "comment": comment,
                "focus_hint": (data.get("focus_hint") or "").strip(),
                "source": "llm",
            }

    # 拿不到 JSON，但确实拿到了文本 —— 说明这个端点忽略了 JSON 约束。
    # 直接把原文当点评用，别白白浪费这次调用。顺带按约定切出「提醒」那一行。
    if text and not text.lstrip().startswith("{"):
        comment, hint = _split_plain(text)
        return {"comment": comment, "focus_hint": hint, "source": "llm"}

    return template_comment(plan, progress, day, reason="模型返回内容无法解析")


