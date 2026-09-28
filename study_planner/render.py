"""把计划渲染成 Markdown。CLI 直接打印，面板用来做纯文本预览。

另外提供 `md_to_html`，把 AI 回复和 README 渲染进网页。
"""
from __future__ import annotations

import html
import re

from .knowledge import TRACK_BY_ID


def _minutes(plan: dict) -> str:
    total = plan.get("total_minutes") or 0
    if not total:
        return "时长待估"
    budget = plan.get("budget_minutes") or 0
    text = f"约 {total} 分钟"
    if plan.get("over_budget") and budget:
        text += f"（超出 {budget} 分钟预算）"
    return text


def _budget_note(plan: dict) -> str:
    """精读吃满预算时的提示。宁可说清楚，也不假装时间够用。"""
    if not plan.get("over_budget"):
        return ""
    total = plan.get("total_minutes") or 0
    budget = plan.get("budget_minutes") or 0
    return (
        f"⚠️ 今天精读偏重，加起来约 {total} 分钟，超过你设的 {budget} 分钟预算。"
        f"小任务可以顺延到明天——它明天还会在候选里。"
    )


def _item_block(label: str, item: dict, optional: bool = False) -> str:
    """渲染计划里的一条。"""
    if not item:
        return f"### {label}\n\n_（今天没有这一项）_\n"

    tag = "（可选）" if optional else ""
    lines = [f"### {label}{tag}", ""]
    lines.append(f"**{item['title']}**  ")
    lines.append(
        f"`{item.get('track_name', item['track'])}` · "
        f"{item.get('level', '')} · {item.get('minutes', '?')} 分钟"
    )
    if item.get("tags"):
        lines.append(" · ".join(f"#{t}" for t in item["tags"]))

    lines.append("")
    lines.append(f"> {item.get('why', '')}")
    lines.append("")
    lines.append(f"🔗 {item['url']}")

    if item.get("prereq"):
        names = []
        for pid in item["prereq"]:
            from .knowledge import ITEM_BY_ID

            pre = ITEM_BY_ID.get(pid)
            names.append(pre.title if pre else pid)
        lines.append("")
        lines.append(f"_前置：{'、'.join(names)}_")

    return "\n".join(lines) + "\n"


def plan_to_markdown(plan: dict, comment: str = "", focus_hint: str = "") -> str:
    """完整的一天计划 → Markdown 文本。"""
    parts: list[str] = []

    focus = plan.get("track_focus") or "—"
    parts.append(f"# 📚 {plan['date']} 学习计划\n")
    parts.append(
        f"今日主线：**{focus}** · {_minutes(plan)} · "
        f"进度 {plan.get('done_total', 0)}/{plan.get('total_items', 0)}\n"
    )

    if comment:
        parts.append("## 💬 今日点评\n")
        parts.append(comment.strip() + "\n")
        if focus_hint:
            parts.append(f"_{focus_hint.strip()}_\n")

    note = _budget_note(plan)
    if note:
        parts.append(f"> {note}\n")

    parts.append(_item_block("🎯 精读", plan.get("main")))
    parts.append(_item_block("🔧 小任务", plan.get("task")))

    if plan.get("review"):
        parts.append(_item_block("🔁 复习（上次没读懂）", plan["review"]))
    else:
        parts.append("### 🔁 复习\n\n_（今天没有到期的复习项）_\n")

    # 新鲜事：明确标成可选，避免它挤占主线注意力
    parts.append("### 🆕 今日新鲜事（可选，扫一眼即可）\n")
    fresh = plan.get("fresh") or []
    if fresh:
        for f in fresh:
            src = f.get("source", "")
            extra = f" ⭐{f['stars']}" if f.get("stars") else ""
            parts.append(f"- **[{src}]** [{f['title']}]({f['url']}){extra}")
            if f.get("summary"):
                parts.append(f"  - {f['summary']}")
        parts.append("")
    else:
        parts.append("_（没抓到新内容——可能断网了，不影响上面的主线）_\n")

    if plan.get("fresh_errors"):
        parts.append("> ⚠️ " + "；".join(plan["fresh_errors"]) + "\n")

    left = plan.get("unlocked_left", 0)
    if left:
        parts.append(f"\n---\n\n_已解锁待读 {left} 条，完成今天的主线后会自动解锁更多。_")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Markdown → HTML（给网页用）
# ---------------------------------------------------------------------------
_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ULIST = re.compile(r"^\s*[-*+]\s+(.*)$")
_OLIST = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_SAFE_URL = re.compile(r"^(https?://|/|#|mailto:)", re.I)


def _link_repl(m: re.Match) -> str:
    """把 [文字](地址) 换成 <a>。地址协议不认识就原样返回，挡掉 javascript: 之类。"""
    text, url = m.group(1), m.group(2)
    if not _SAFE_URL.match(url):
        return m.group(0)
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{text}</a>'


def _inline(escaped: str) -> str:
    """行内格式。**入参必须已经过 html.escape**——顺序反了就是 XSS 口子。"""
    # 行内代码先抠出来存好，否则里面的 `**` 会被当成粗体处理
    stash: list[str] = []

    def _stash(m: re.Match) -> str:
        stash.append(m.group(1))
        return f"\x00{len(stash) - 1}\x00"

    out = re.sub(r"`([^`]+)`", _stash, escaped)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _link_repl, out)

    def _unstash(m: re.Match) -> str:
        return f"<code>{stash[int(m.group(1))]}</code>"

    return re.sub(r"\x00(\d+)\x00", _unstash, out)


def md_to_html(text: str) -> str:
    """极简 Markdown 渲染：标题、列表、引用、代码块、粗斜体、行内代码、链接。

    不引第三方库。够渲染 AI 回复和 README 用。
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    code_buf: list[str] = []
    in_code = False
    list_kind = ""

    def flush_para() -> None:
        if para:
            out.append(f"<p>{_inline(html.escape(' '.join(para)))}</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = ""

    def flush_code() -> None:
        out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
        code_buf.clear()

    for line in lines:
        if _FENCE.match(line):
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_para()
                close_list()
                in_code = True
            continue

        if in_code:
            code_buf.append(line)
            continue

        if not line.strip():
            flush_para()
            close_list()
            continue

        if m := _HEADING.match(line):
            flush_para()
            close_list()
            # 页面里 h1/h2 已被占用，往下压一级免得压过页面标题
            lvl = min(len(m.group(1)) + 2, 6)
            out.append(f"<h{lvl}>{_inline(html.escape(m.group(2)))}</h{lvl}>")
            continue

        if m := _ULIST.match(line):
            flush_para()
            if list_kind != "ul":
                close_list()
                out.append("<ul>")
                list_kind = "ul"
            out.append(f"<li>{_inline(html.escape(m.group(1)))}</li>")
            continue

        if m := _OLIST.match(line):
            flush_para()
            if list_kind != "ol":
                close_list()
                out.append("<ol>")
                list_kind = "ol"
            out.append(f"<li>{_inline(html.escape(m.group(1)))}</li>")
            continue

        if m := _QUOTE.match(line):
            flush_para()
            close_list()
            out.append(f"<blockquote>{_inline(html.escape(m.group(1)))}</blockquote>")
            continue

        # 普通文本先攒着，连续几行合成一个段落
        para.append(line.strip())

    if in_code:
        flush_code()
    flush_para()
    close_list()
    return "\n".join(out)


def plan_to_text(plan: dict, comment: str = "", focus_hint: str = "") -> str:
    """给终端用的紧凑版本（不带 Markdown 记号）。"""
    out: list[str] = []
    out.append(f"📚 {plan['date']} 学习计划 —— {plan.get('track_focus') or '—'}（{_minutes(plan)}）")
    out.append("")

    note = _budget_note(plan)
    if note:
        out.append(note)
        out.append("")

    if comment:
        out.append("💬 " + comment.strip().replace("\n", "\n   "))
        out.append("")

    for label, key in (("🎯 精读", "main"), ("🔧 小任务", "task"), ("🔁 复习", "review")):
        item = plan.get(key)
        if not item:
            continue
        out.append(f"{label}  {item['title']}")
        out.append(f"    [{item.get('track_name')}/{item.get('level')}] {item.get('minutes')} 分钟")
        out.append(f"    {item['why']}")
        out.append(f"    {item['url']}")
        out.append("")

    fresh = plan.get("fresh") or []
    if fresh:
        out.append("🆕 新鲜事（可选）")
        for f in fresh:
            out.append(f"    [{f.get('source')}] {f['title']}  {f['url']}")
        out.append("")

    if plan.get("fresh_errors"):
        out.append("⚠️  " + "；".join(plan["fresh_errors"]))
        out.append("")

    if focus_hint:
        out.append(f"📌 {focus_hint.strip()}")
        out.append("")

    left = plan.get("unlocked_left", 0)
    out.append(f"进度 {plan.get('done_total', 0)}/{plan.get('total_items', 0)}　已解锁待读 {left} 条")
    return "\n".join(out)
