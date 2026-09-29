"""本地网页面板：运行 python app.py 后浏览器打开 http://127.0.0.1:8766

页面：
- /           今日计划：精读 / 小任务 / 复习 / 新鲜事，每条可直接勾选
- /curriculum 课程总览：按方向分组，看解锁情况
- /progress   进度统计：连续天数、各方向完成度
"""
from __future__ import annotations

import json
import sys
from datetime import date

# 允许直接 `python app.py` 运行（把上级目录加进 sys.path）
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from flask import Flask, Response, redirect, request, url_for

from study_planner import (
    config,
    knowledge,
    llm_plan,
    paper,
    planner,
    progress as progress_mod,
    proxy,
    render,
)
from study_planner.knowledge import ITEM_BY_ID, ITEMS, TRACKS, TRACK_BY_ID

app = Flask(__name__)

# 点评每天只生成一次，缓存在进程内存里。
# 否则每次刷新页面都要调一次 Claude API，又慢又费钱。
_COMMENT_CACHE: dict[str, dict] = {}


def _comment(plan: dict, prog) -> dict:
    key = plan["date"]
    if key not in _COMMENT_CACHE:
        try:
            _COMMENT_CACHE[key] = llm_plan.generate_comment(plan, prog, date.fromisoformat(key))
        except Exception:
            # 兜底中的兜底：连模板都出错也不能让页面挂掉
            _COMMENT_CACHE[key] = {"comment": "", "focus_hint": "", "source": "none"}
    return _COMMENT_CACHE[key]


def _plan() -> tuple[dict, progress_mod.Progress, dict]:
    prog = progress_mod.Progress.load()
    today = date.today()
    plan = planner.build_today(prog, today)
    return plan, prog, _comment(plan, prog)


# ---------------------------------------------------------------------------
# 页面骨架
# ---------------------------------------------------------------------------
_LAYOUT = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}学习计划{% endblock %}</title>
<style>
  :root { --accent:#3b7ea1; --accent-dark:#2c6280; --bg:#f5f8fa; --card:#fff;
          --ink:#25333d; --muted:#7d8f9c; --line:#e2ebf0; --ok:#2e8b57; --warn:#c9862b; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         background:var(--bg); color:var(--ink); }
  header { background:linear-gradient(135deg,#5a9cbd,#3b7ea1); color:#fff;
           padding:18px 28px; display:flex; align-items:center; justify-content:space-between;
           flex-wrap:wrap; gap:10px; }
  header h1 { margin:0; font-size:19px; font-weight:600; letter-spacing:.5px; }
  nav a { color:#fff; margin-left:16px; text-decoration:none; font-size:14px; opacity:.9; }
  nav a:hover { opacity:1; text-decoration:underline; }
  main { max-width:1000px; margin:22px auto; padding:0 20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:18px 20px; margin-bottom:16px; box-shadow:0 1px 3px rgba(59,126,161,.07); }
  h2 { font-size:15px; margin:0 0 12px; color:var(--accent-dark); }
  h3 { font-size:14px; margin:16px 0 8px; color:var(--accent-dark); }
  .item { border-left:3px solid var(--line); padding:10px 0 10px 14px; margin-bottom:10px; }
  .item.main { border-left-color:var(--accent); }
  .item.done { opacity:.5; }
  .item.done .title { text-decoration:line-through; }
  .title { font-size:15px; font-weight:600; margin-bottom:4px; }
  .meta { font-size:12px; color:var(--muted); margin-bottom:6px; }
  .why { font-size:13px; color:#4a5b66; line-height:1.6; margin-bottom:6px; }
  .tag { display:inline-block; background:#eaf2f7; color:var(--accent-dark);
         border-radius:12px; padding:1px 9px; font-size:11px; margin-right:5px; }
  .tag.warn { background:#fdf3e3; color:var(--warn); }
  a.link { font-size:12px; color:var(--accent); word-break:break-all; }
  .actions { margin-top:8px; display:flex; gap:8px; flex-wrap:wrap; }
  button { font-size:12px; padding:4px 12px; border-radius:14px; border:1px solid var(--line);
           background:#fff; cursor:pointer; color:var(--ink); }
  button:hover { background:#f0f5f8; }
  button.ok { border-color:#bfe0cd; color:var(--ok); }
  button.warn { border-color:#f0dcc0; color:var(--warn); }
  .empty { color:var(--muted); font-size:13px; padding:8px 0; }
  .comment { background:#f0f6fa; border-radius:10px; padding:14px 16px;
             font-size:13.5px; line-height:1.75; }
  .hint { font-size:12.5px; color:var(--muted); margin-top:8px; font-style:italic; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  @media (max-width:760px){ .grid{grid-template-columns:1fr;} }
  .bar { background:#e8eff3; border-radius:8px; height:9px; overflow:hidden; margin-top:4px; }
  .bar > i { display:block; height:100%; background:var(--accent); }
  table { width:100%; border-collapse:collapse; font-size:13.5px; }
  th,td { text-align:left; padding:7px 9px; border-bottom:1px dashed var(--line); }
  th { color:var(--muted); font-weight:500; }
  .row { display:flex; justify-content:space-between; gap:12px; align-items:baseline; }
  .foot { text-align:center; color:var(--muted); font-size:12px; padding:18px 0 30px; }
  code { background:#eef3f6; padding:1px 5px; border-radius:4px; font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>📚 学习计划 · 端到端 / RL / VLA</h1>
  <nav>
    <a href="/">今日</a>
    <a href="/curriculum">课程总览</a>
    <a href="/progress">进度</a>
  </nav>
</header>
<main>
{% block body %}{% endblock %}
</main>
<div class="foot">所有数据只存在你电脑上 · 课程表在 study_planner/knowledge.py</div>
</body>
</html>
"""


def _page(body: str) -> str:
    return _LAYOUT.replace("{% block body %}{% endblock %}", body)


def _esc(s: str) -> str:
    import html

    return html.escape(str(s or ""))


def _render_item(item: dict, cls: str = "") -> str:
    """渲染一条课程，带三个操作按钮。"""
    if not item:
        return '<div class="empty">（今天没有这一项）</div>'

    iid = _esc(item["id"])
    done = item.get("status") == "done"
    classes = f"item {cls}" + (" done" if done else "")

    tags = "".join(f'<span class="tag">{_esc(t)}</span>' for t in item.get("tags", []))
    if item.get("shaky"):
        tags += '<span class="tag warn">没读懂</span>'

    return f"""
    <div class="{classes}">
      <div class="title"><a href="/read/{iid}" target="_blank" rel="noopener">{_esc(item['title'])}</a></div>
      <div class="meta">{_esc(item.get('track_name',''))} · {_esc(item.get('level',''))}
        · {item.get('minutes','?')} 分钟</div>
      <div class="why">{_esc(item.get('why',''))}</div>
      <div>{tags}</div>
      <div class="actions">
        <a class="link" href="{_esc(item['url'])}" target="_blank" rel="noopener">原文 ↗</a>
        <form method="post" action="/mark" style="display:inline">
          <input type="hidden" name="item_id" value="{iid}">
          <input type="hidden" name="action" value="done">
          <button class="ok" type="submit">✅ 完成</button>
        </form>
        <form method="post" action="/mark" style="display:inline">
          <input type="hidden" name="item_id" value="{iid}">
          <input type="hidden" name="action" value="shaky">
          <button class="warn" type="submit">😵 没读懂</button>
        </form>
        <form method="post" action="/mark" style="display:inline">
          <input type="hidden" name="item_id" value="{iid}">
          <input type="hidden" name="action" value="skip">
          <button type="submit">⏭ 跳过</button>
        </form>
      </div>
    </div>"""


@app.route("/")
def home():
    plan, prog, comment = _plan()
    today = date.today()

    fresh_rows = ""
    for f in plan.get("fresh") or []:
        stars = f' ⭐{f["stars"]}' if f.get("stars") else ""
        fresh_rows += (
            f'<div class="item"><div class="title">'
            f'<a class="link" href="{_esc(f["url"])}" target="_blank" rel="noopener">'
            f'{_esc(f["title"])}</a>{stars}</div>'
            f'<div class="meta">{_esc(f.get("source",""))}'
            f'{" · " + _esc(f["published"]) if f.get("published") else ""}</div>'
            f'<div class="why">{_esc(f.get("summary",""))}</div></div>'
        )
    if not fresh_rows:
        fresh_rows = '<div class="empty">没抓到新内容（可能断网了，不影响上面的主线）</div>'

    budget_warn = ""
    if plan.get("over_budget"):
        budget_warn = (
            f'<div class="hint">⚠️ 今天精读偏重，加起来约 {plan.get("total_minutes", 0)} 分钟，'
            f'超过你设的 {plan.get("budget_minutes", 0)} 分钟预算。'
            f'小任务可以顺延到明天——它明天还会在候选里。</div>'
        )

    note = ""
    if plan.get("fresh_errors"):
        note = f'<div class="hint">⚠️ {"；".join(_esc(e) for e in plan["fresh_errors"])}</div>'

    body = f"""
    <div class="card">
      <div class="row">
        <h2>📅 {plan['date']} · 主线 {_esc(plan.get('track_focus') or '—')}</h2>
        <span class="meta">约 {plan.get('total_minutes', 0)} 分钟 ·
          连续 {prog.current_streak(today)} 天 ·
          进度 {plan.get('done_total', 0)}/{plan.get('total_items', 0)}</span>
      </div>
      <div class="comment">{_esc(comment.get('comment','')).replace(chr(10), '<br>')}</div>
      {f'<div class="hint">📌 {_esc(comment["focus_hint"])}</div>' if comment.get('focus_hint') else ''}
      {budget_warn}
    </div>

    <div class="grid">
      <div>
        <div class="card"><h2>🎯 精读</h2>{_render_item(plan.get('main'), 'main')}</div>
        <div class="card"><h2>🔧 小任务</h2>{_render_item(plan.get('task'))}</div>
      </div>
      <div>
        <div class="card"><h2>🔁 复习（上次没读懂）</h2>
          {_render_item(plan.get('review')) if plan.get('review')
           else '<div class="empty">今天没有到期的复习项</div>'}
        </div>
        <div class="card"><h2>🆕 新鲜事（可选）</h2>{fresh_rows}{note}</div>
      </div>
    </div>
    """
    return _page(body)


@app.route("/mark", methods=["POST"])
def mark():
    item_id = request.form.get("item_id", "")
    action = request.form.get("action", "")

    if item_id in ITEM_BY_ID:
        prog = progress_mod.Progress.load()
        today = date.today()
        prog.mark_seen(item_id, today)

        if action == "done":
            prog.mark(item_id, "done", today)
        elif action == "skip":
            prog.mark(item_id, "skipped", today)
        elif action == "shaky":
            prog.mark_shaky(item_id, today)
        elif action == "reset":
            prog.mark(item_id, "todo", today)

        prog.save()
        # 状态变了，当天的计划排序可能也变，把点评缓存清掉重算
        _COMMENT_CACHE.pop(today.isoformat(), None)

    return redirect(request.form.get("next") or url_for("home"))


@app.route("/curriculum")
def curriculum():
    prog = progress_mod.Progress.load()
    stats = prog.track_stats()

    blocks = ""
    for track in TRACKS:
        st = stats.get(track.id, {"done": 0, "total": 0, "unlocked": 0})
        total = st["total"] or 1
        pct = int(st["done"] / total * 100)

        rows = ""
        for item in knowledge.items_of(track.id):
            if prog.is_done(item.id):
                state, label = "done", "✅"
            elif prog.is_skipped(item.id):
                state, label = "done", "⏭"
            elif prog.prereq_met(item.id):
                state, label = "", "○"
            else:
                state, label = "done", "🔒"

            rows += (
                f'<div class="item {state}">'
                f'<div class="title">{label} '
                f'<a class="link" href="/read/{_esc(item.id)}" target="_blank" rel="noopener">'
                f'{_esc(item.title)}</a>'
                f' <a class="link" href="{_esc(item.url)}" target="_blank" rel="noopener"'
                f' style="font-weight:400;opacity:.6">原文 ↗</a></div>'
                f'<div class="meta">{_esc(item.level)} · {item.minutes} 分钟 · '
                f'<code>{_esc(item.id)}</code></div></div>'
            )

        blocks += f"""
        <div class="card">
          <div class="row">
            <h2>{_esc(track.name)}</h2>
            <span class="meta">{st['done']}/{st['total']} · 已解锁 {st['unlocked']}</span>
          </div>
          <div class="bar"><i style="width:{pct}%"></i></div>
          <div class="hint">{_esc(track.desc)}</div>
          <h3>条目</h3>
          {rows}
        </div>"""

    return _page(blocks)


@app.route("/progress")
def progress_page():
    prog = progress_mod.Progress.load()
    today = date.today()
    stats = prog.track_stats()

    rows = ""
    for track in TRACKS:
        st = stats.get(track.id, {"done": 0, "total": 0, "unlocked": 0})
        total = st["total"] or 1
        pct = int(st["done"] / total * 100)
        rows += (
            f'<tr><td>{_esc(track.name)}</td><td>{st["done"]}/{st["total"]}</td>'
            f'<td>{st["unlocked"]}</td>'
            f'<td style="width:40%"><div class="bar"><i style="width:{pct}%"></i></div></td></tr>'
        )

    # 最近 30 天
    hist = ""
    for entry in reversed(prog.history[-30:]):
        m = ITEM_BY_ID.get(entry.get("main") or "")
        t = ITEM_BY_ID.get(entry.get("task") or "")
        hist += (
            f'<tr><td>{_esc(entry.get("date",""))}</td>'
            f'<td>{_esc(m.title) if m else "—"}</td>'
            f'<td>{_esc(t.title) if t else "—"}</td></tr>'
        )
    if not hist:
        hist = '<tr><td colspan="3" class="empty">还没有历史记录</td></tr>'

    due = prog.due_for_review(today)
    due_rows = "".join(
        f'<li>{_esc(ITEM_BY_ID[i].title)}</li>' for i in due if i in ITEM_BY_ID
    ) or '<li class="empty">没有待复习的条目</li>'

    body = f"""
    <div class="grid">
      <div>
        <div class="card">
          <h2>📊 方向完成度</h2>
          <table>
            <tr><th>方向</th><th>完成</th><th>已解锁</th><th>进度</th></tr>
            {rows}
          </table>
        </div>
        <div class="card">
          <h2>🔥 连续学习</h2>
          <p style="font-size:24px;margin:6px 0;color:var(--accent-dark)">
            {prog.current_streak(today)} 天</p>
          <div class="meta">历史最长 {prog.longest_streak} 天 ·
            总完成 {prog.done_count()}/{len(ITEMS)}</div>
        </div>
      </div>
      <div>
        <div class="card"><h2>🔁 待复习</h2><ul style="padding-left:18px;font-size:13.5px">{due_rows}</ul></div>
        <div class="card">
          <h2>🗓 最近 30 天</h2>
          <table><tr><th>日期</th><th>精读</th><th>小任务</th></tr>{hist}</table>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>🔄 刷新实时内容</h2>
      <form method="post" action="/refresh">
        <button type="submit">清掉今天的缓存，重新抓一次 arXiv / GitHub</button>
      </form>
      <div class="hint">课程表本身不受影响，只重新抓「新鲜事」那一块。</div>
    </div>
    """
    return _page(body)


@app.route("/refresh", methods=["POST"])
def refresh():
    from study_planner import sources

    sources.clear_cache(date.today())
    _COMMENT_CACHE.clear()
    return redirect(url_for("home"))


# ===========================================================================
# 阅读器：左栏原文 + 右栏 AI 问答
# ===========================================================================

_READER_LAYOUT = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<style>
  :root { --accent:#3b7ea1; --accent-dark:#2c6280; --bg:#f5f8fa; --card:#fff;
          --ink:#25333d; --muted:#7d8f9c; --line:#e2ebf0; --ok:#2e8b57; --warn:#c9862b; }
  * { box-sizing:border-box; }
  html, body { height:100%; margin:0; overflow:hidden;
    font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
    background:var(--bg); color:var(--ink); }
  body.dragging { cursor:col-resize; user-select:none; }

  header.rbar { height:46px; display:flex; align-items:center; justify-content:space-between;
    gap:12px; padding:0 14px; background:linear-gradient(135deg,#5a9cbd,#3b7ea1);
    color:#fff; flex-wrap:nowrap; }
  .rleft { display:flex; align-items:baseline; gap:12px; min-width:0; }
  .rleft .rtitle { font-size:15px; font-weight:600; white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; }
  .rleft .rmeta { font-size:12px; opacity:.85; white-space:nowrap; }
  a.back { color:#fff; text-decoration:none; font-size:13px; opacity:.9; flex:0 0 auto; }
  a.back:hover { text-decoration:underline; }
  .rright { display:flex; gap:8px; flex:0 0 auto; }
  .rbtn { font-size:12px; padding:4px 10px; border-radius:13px; border:1px solid rgba(255,255,255,.5);
    background:transparent; color:#fff; cursor:pointer; text-decoration:none; white-space:nowrap; }
  .rbtn:hover { background:rgba(255,255,255,.18); }

  #split { display:flex; height:calc(100vh - 46px); }
  #pane-left { flex:1 1 auto; min-width:0; position:relative; background:#fff; }
  #paperFrame { width:100%; height:100%; border:0; display:block; }
  #readmeWrap { display:none; height:100%; overflow-y:auto; padding:28px 40px; line-height:1.75;
    font-size:14px; }
  #readmeWrap h3, #readmeWrap h4, #readmeWrap h5 { color:var(--accent-dark);
    margin:20px 0 8px; line-height:1.4; }
  #readmeWrap pre { background:#f2f6f8; padding:12px; border-radius:8px; overflow-x:auto;
    font-size:12.5px; }
  #readmeWrap code { background:#eef3f6; padding:1px 5px; border-radius:4px; font-size:12.5px; }
  #readmeWrap pre code { background:none; padding:0; }
  #readmeWrap img { max-width:100%; }
  #readmeWrap table { border-collapse:collapse; }
  #readmeWrap td, #readmeWrap th { border:1px solid var(--line); padding:5px 8px; }
  #fallbackCard { display:none; height:100%; align-items:center; justify-content:center; }
  .fcard { text-align:center; max-width:420px; color:var(--muted); line-height:1.8; }
  .fcard a.rbtn2 { display:inline-block; margin-top:12px; padding:8px 18px; border-radius:16px;
    background:var(--accent); color:#fff; text-decoration:none; font-size:13px; }

  #selPill { position:fixed; z-index:60; display:none; background:var(--accent); color:#fff;
    border:none; border-radius:14px; padding:4px 11px; font-size:12px; cursor:pointer;
    box-shadow:0 2px 10px rgba(59,126,161,.4); white-space:nowrap; }
  #selPill:hover { background:var(--accent-dark); }

  #gutter { flex:0 0 5px; background:var(--line); cursor:col-resize; }
  #gutter:hover { background:#c4d6e0; }

  #pane-right { flex:0 0 400px; display:flex; flex-direction:column; background:#fff;
    border-left:1px solid var(--line); min-width:300px; }
  #ctxBar { display:flex; align-items:center; justify-content:space-between; gap:8px;
    padding:9px 12px; border-bottom:1px solid var(--line); background:#fafcfd; }
  #ctxLabel { font-size:12px; color:var(--muted); overflow:hidden; text-overflow:ellipsis;
    white-space:nowrap; }
  #ctxBar .rbtn { border-color:var(--line); color:var(--accent-dark); flex:0 0 auto; }
  #ctxBar .rbtn:hover { background:#eef4f8; }

  #msgs { flex:1 1 auto; overflow-y:auto; padding:14px 12px; }
  .bubble { max-width:94%; margin-bottom:12px; padding:9px 13px; border-radius:12px;
    font-size:13.5px; line-height:1.75; white-space:pre-wrap; word-wrap:break-word;
    overflow-wrap:anywhere; }
  .bubble.user { margin-left:auto; background:var(--accent); color:#fff;
    border-bottom-right-radius:4px; }
  .bubble.assistant { background:#f2f6f8; border-bottom-left-radius:4px; }
  .bubble.assistant p { margin:0 0 8px; white-space:pre-wrap; }
  .bubble.assistant p:last-child { margin-bottom:0; }
  .bubble.assistant ul, .bubble.assistant ol { margin:6px 0; padding-left:20px; }
  .bubble.assistant li { margin:3px 0; white-space:normal; }
  .bubble.assistant code { background:#e4ecf1; padding:1px 5px; border-radius:4px;
    font-size:12px; }
  .bubble.assistant pre { background:#e9eff3; padding:10px; border-radius:8px;
    overflow-x:auto; margin:8px 0; }
  .bubble.assistant pre code { background:none; padding:0; }
  .bubble.assistant h4, .bubble.assistant h5, .bubble.assistant h6 {
    margin:10px 0 6px; font-size:13.5px; color:var(--accent-dark); }
  .bubble.assistant blockquote { margin:6px 0; padding-left:10px;
    border-left:3px solid var(--line); color:var(--muted); }
  .bubble.err { background:#fdf0ee; color:#a33; }
  .bubble.sys { background:transparent; color:var(--muted); font-size:12px;
    text-align:center; max-width:100%; margin:6px 0; }

  #quoteBar { display:none; padding:9px 10px 0; }
  #quoteBar.on { display:block; }
  .qchip { position:relative; background:#eef4f8; border:1px solid #d8e6ee; border-radius:10px;
    padding:7px 26px 7px 10px; font-size:12px; color:#4a5b66; line-height:1.55; }
  .qchip .qtext { display:block; max-height:52px; overflow:hidden; color:var(--ink);
    font-size:12.5px; }
  .qchip .qmeta { display:block; margin-top:3px; color:var(--muted); font-size:11px; }
  .qchip .qmeta.warn { color:var(--warn); }
  .qchip .qx { position:absolute; top:4px; right:6px; border:none; background:none;
    cursor:pointer; color:var(--muted); font-size:14px; line-height:1; padding:2px 4px; }
  .qchip .qx:hover { color:#a33; }

  /* 用户气泡里的引用：只露一小段，不然一次几千字会把对话区刷屏 */
  .bubble.user .uquote { display:block; border-left:3px solid rgba(255,255,255,.55);
    padding-left:8px; margin-bottom:6px; opacity:.85; font-size:12.5px;
    max-height:110px; overflow:hidden; }

  #composer { border-top:1px solid var(--line); padding:10px; display:flex; gap:8px;
    align-items:flex-end; }
  #input { flex:1 1 auto; resize:none; border:1px solid var(--line); border-radius:10px;
    padding:8px 10px; font-size:13.5px; font-family:inherit; line-height:1.6;
    outline:none; max-height:140px; }
  #input:focus { border-color:var(--accent); }
  #sendBtn { flex:0 0 auto; padding:9px 16px; border:none; border-radius:10px;
    background:var(--accent); color:#fff; font-size:13px; cursor:pointer; }
  #sendBtn:hover { background:var(--accent-dark); }
  #sendBtn:disabled { opacity:.5; cursor:default; }
</style>
</head>
<body>
<header class="rbar">
  <div class="rleft">
    <a class="back" href="/">← 返回计划</a>
    <span class="rtitle" id="docTitle">__PAGE_TITLE__</span>
    <span class="rmeta" id="docMeta"></span>
  </div>
  <div class="rright">
    <a class="rbtn" id="linkSource" target="_blank" rel="noopener">原文 ↗</a>
    <a class="rbtn" id="linkPdf" target="_blank" rel="noopener" style="display:none">PDF</a>
  </div>
</header>

<div id="split">
  <section id="pane-left">
    <iframe id="paperFrame" title="原文"></iframe>
    <div id="readmeWrap"></div>
    <div id="fallbackCard"><div class="fcard" id="fallbackInner"></div></div>
    <button id="selPill" type="button">❓ 问 AI</button>
  </section>

  <div id="gutter" title="拖动调整宽度"></div>

  <section id="pane-right">
    <div id="ctxBar">
      <span id="ctxLabel">上下文：摘要 + 目录</span>
      <button class="rbtn" id="fullBtn">载入全文</button>
    </div>
    <div id="msgs"></div>
    <div id="quoteBar"></div>
    <div id="composer">
      <textarea id="input" rows="1" placeholder="问点什么…（选中左边一段可以直接问，Enter 发送）"></textarea>
      <button id="sendBtn">发送</button>
    </div>
  </section>
</div>

<script>
const DOC = __DOC_JSON__;

const msgsEl  = document.getElementById('msgs');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('sendBtn');
const fullBtn = document.getElementById('fullBtn');
const ctxLbl  = document.getElementById('ctxLabel');

let history = [];        // [{role, content}]
let useFull = false;
let busy = false;

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

/* ---------------- 左栏 ---------------- */
(function initLeft() {
  /* arxiv 条目指向代理页（同源）而不是 arxiv.org——跨域 iframe 读不到选区，
     划词功能就无从实现。详见 study_planner/proxy.py 的模块说明。 */
  document.getElementById('linkSource').href = DOC.source_url || DOC.url || '#';
  document.getElementById('docMeta').textContent = DOC.track || '';

  if (DOC.frame_url) {
    document.getElementById('paperFrame').src = DOC.frame_url;
    if (DOC.pdf_url) {
      const p = document.getElementById('linkPdf');
      p.href = DOC.pdf_url;
      p.style.display = '';
    }
    return;
  }

  document.getElementById('paperFrame').style.display = 'none';

  if (DOC.readme_html) {
    const w = document.getElementById('readmeWrap');
    w.style.display = 'block';
    w.innerHTML = DOC.readme_html;
    return;
  }

  document.getElementById('fallbackInner').innerHTML =
    '<p>' + esc(DOC.error || '这类资源不支持内嵌。') + '</p>' +
    '<a class="rbtn2" target="_blank" rel="noopener" href="' + esc(DOC.url) + '">在新标签页打开 ↗</a>';
  document.getElementById('fallbackCard').style.display = 'flex';
})();

/* ---------------- 划词提问 ----------------
   两条来源：左栏 iframe（arXiv 论文，脚本由 proxy.py 注入、postMessage 上来）
   和父文档里的 README（GitHub 条目，选区就是普通的本页选区）。 */
const pill = document.getElementById('selPill');
const paneLeft = document.getElementById('pane-left');
const quoteBar = document.getElementById('quoteBar');
let pendingQuote = null;

function squash(s) { return (s || '').replace(/\s+/g, ' ').trim(); }

/* 取「文档顺序里最后一个位于选区之前的标题」。
   render.md_to_html 输出的是扁平的 h3–h6，这一招照样通吃。 */
function headingBeforeIn(scope, node) {
  const hs = scope.querySelectorAll('h1,h2,h3,h4,h5,h6');
  let best = '';
  for (const h of hs) {
    if (h.contains(node)) return squash(h.textContent);
    if (h.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING) {
      best = squash(h.textContent);
    }
  }
  return best;
}

function hidePill() { pill.style.display = 'none'; pill.onclick = null; }

function showPill(sel) {
  const pr = paneLeft.getBoundingClientRect();
  const top = sel.rect.bottom + 6;
  const left = Math.min(Math.max(sel.rect.left, pr.left + 8), pr.right - 76);
  /* 选中那行已经滚出左栏了就不显示，而不是夹到一个会误导人的位置 */
  if (top < pr.top + 4 || top > pr.bottom - 30) return hidePill();
  pill.style.top = top + 'px';
  pill.style.left = left + 'px';
  pill.style.display = 'block';
  pill.onclick = () => { attachQuote(sel); hidePill(); };
}

function clearQuote() {
  pendingQuote = null;
  quoteBar.textContent = '';
  quoteBar.className = '';
}

function attachQuote(sel) {
  pendingQuote = sel;
  quoteBar.textContent = '';
  quoteBar.className = 'on';

  /* 整条引用栏都用 createElement + textContent 构建。
     **绝不能用 innerHTML**：这段文字来自 arXiv 的页面，是外部输入。 */
  const box = document.createElement('div');
  box.className = 'qchip';

  const t = document.createElement('span');
  t.className = 'qtext';
  t.textContent = '❝ ' + (sel.text.length > 90 ? sel.text.slice(0, 90) + '…' : sel.text) + ' ❞';
  box.appendChild(t);

  const meta = document.createElement('span');
  meta.className = 'qmeta';
  meta.textContent = (sel.heading ? '选自 ' + sel.heading + ' · ' : '') + sel.text.length + ' 字';
  box.appendChild(meta);

  if (sel.truncated) {
    const w = document.createElement('span');
    w.className = 'qmeta warn';
    w.textContent = '已截断，只发送前 ' + DOC.quote_max + ' 字';
    box.appendChild(w);
  }

  const x = document.createElement('button');
  x.className = 'qx'; x.type = 'button'; x.textContent = '×'; x.title = '取消引用';
  x.onclick = clearQuote;
  box.appendChild(x);

  quoteBar.appendChild(box);

  /* 预填「这一段是什么意思？」并整段选中：直接回车就是问这句，直接打字就替换成
     自己的问题，两种都顺手。但绝不覆盖已经写了一半的内容——那很讨厌。 */
  if (!inputEl.value.trim()) {
    inputEl.value = '这一段是什么意思？';
    inputEl.focus();
    inputEl.setSelectionRange(0, inputEl.value.length);
    inputEl.dispatchEvent(new Event('input'));   // 触发已有的自动撑高
  }
}

window.addEventListener('message', e => {
  const frame = document.getElementById('paperFrame');
  if (e.origin !== window.location.origin) return;
  if (e.source !== frame.contentWindow) return;      // 只认左栏那个 iframe
  const d = e.data || {};
  if (d.from !== 'paper-bridge') return;

  if (!d.sel) return hidePill();
  /* 子文档里的坐标是它自己视口的，加上 iframe 在父页面里的偏移才是父页面坐标 */
  const fr = frame.getBoundingClientRect();
  showPill({
    text: d.sel.text, truncated: d.sel.truncated, heading: d.sel.heading,
    rect: {
      top: d.sel.rect.top + fr.top, bottom: d.sel.rect.bottom + fr.top,
      left: d.sel.rect.left + fr.left, right: d.sel.rect.right + fr.left
    }
  });
});

document.addEventListener('mouseup', e => setTimeout(() => {
  /* 点浮标自己也算一次 mouseup。不排掉的话，在 README 上点「问 AI」之后，
     如果浏览器没把原选区清掉，浮标会立刻又冒出来。 */
  if (e.target === pill) return;

  /* 白名单只认 README 里面的选区：右侧聊天区、输入框里的选择天然不触发，
     不用去逐个排除 #pane-right / #msgs / #input。 */
  const wrap = document.getElementById('readmeWrap');
  if (!wrap || wrap.style.display === 'none') return hidePill();
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return hidePill();
  const range = sel.getRangeAt(0);
  if (!wrap.contains(range.commonAncestorContainer)) return hidePill();
  const raw = range.toString();
  if (!raw.trim()) return hidePill();

  const rects = range.getClientRects();
  const r = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();
  const node = range.startContainer.nodeType === 3
    ? range.startContainer.parentNode : range.startContainer;
  showPill({
    text: squash(raw).slice(0, DOC.quote_max),
    truncated: raw.length > DOC.quote_max,
    heading: headingBeforeIn(wrap, node),
    rect: { top: r.top, bottom: r.bottom, left: r.left, right: r.right }
  });
}), 0);

/* 固定定位的浮标锚在算好的坐标上，窗口一变大或分栏一拖就指向错的地方——
   与其显示一个错的，不如收起来 */
window.addEventListener('resize', hidePill);
document.getElementById('gutter').addEventListener('mousedown', hidePill);

/* ---------------- 上下文档位 ---------------- */
function updateCtx(announce) {
  if (useFull) {
    ctxLbl.textContent = '上下文：全文（' + DOC.full_chars.toLocaleString() + ' 字符'
      + (DOC.full_truncated ? '，已截断' : '') + '）';
    fullBtn.textContent = '退回摘要';
  } else {
    ctxLbl.textContent = '上下文：摘要 + 目录（约 ' + DOC.abstract_chars + ' 字）';
    fullBtn.textContent = '载入全文';
  }
  if (announce) {
    addBubble('sys').textContent = useFull
      ? '已切到全文。后面的问题会基于整篇论文回答。'
      : '已退回摘要 + 目录。回答会更快，但看不到正文细节。';
  }
}

fullBtn.addEventListener('click', () => { useFull = !useFull; updateCtx(true); });

/* ---------------- 对话 ---------------- */
function addBubble(role) {
  const d = document.createElement('div');
  d.className = 'bubble ' + role;
  msgsEl.appendChild(d);
  msgsEl.scrollTop = msgsEl.scrollHeight;
  return d;
}

function scrollDown() { msgsEl.scrollTop = msgsEl.scrollHeight; }

async function renderMd(text) {
  try {
    const r = await fetch('/api/md', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text })
    });
    const j = await r.json();
    return j.html || '';
  } catch (e) { return ''; }
}

async function send() {
  const text = inputEl.value.trim();
  const q = pendingQuote;
  if ((!text && !q) || busy) return;   // 只贴了引用、没打字也允许发送

  const question = text || '这一段是什么意思？';

  /* 引用拼进 user 消息正文，跟着 history 一起发——服务端因此不需要知道
     「引用」这个概念，接口形状零变化。
     截断要同时告诉人和模型（引用栏上一处、这里一处），不能只告诉一边。 */
  let content = question;
  if (q) {
    content = (q.heading ? DOC.quote_open + '（位置：' + q.heading + '）' : DOC.quote_open)
      + '\n' + q.text
      + (q.truncated ? '\n（原文过长，以上只是前 ' + DOC.quote_max + ' 字）' : '')
      + '\n' + DOC.quote_close + '\n\n' + question;
  }

  inputEl.value = '';
  busy = true;
  sendBtn.disabled = true;
  clearQuote();

  history.push({ role: 'user', content: content });

  // 气泡里显示压缩版：几千字的引用全铺出来会把对话区刷屏
  const ub = addBubble('user');
  if (q) {
    const qt = document.createElement('span');
    qt.className = 'uquote';
    qt.textContent = '❝ ' + (q.text.length > 80 ? q.text.slice(0, 80) + '…' : q.text) + ' ❞'
      + (q.heading ? '（' + q.heading + '）' : '');
    ub.appendChild(qt);
  }
  ub.appendChild(document.createTextNode(question));

  const out = addBubble('assistant');
  out.textContent = '思考中…';

  let raw = '';
  let failed = false;

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_id: DOC.item_id, messages: history, use_full: useFull })
    });
    if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let started = false;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });

      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const line = chunk.split('\n').find(l => l.indexOf('data:') === 0);
        if (!line) continue;

        let ev;
        try { ev = JSON.parse(line.slice(5)); } catch (e) { continue; }

        if (ev.delta) {
          if (!started) { out.textContent = ''; started = true; }
          raw += ev.delta;
          out.textContent = raw;   // 流式阶段用 textContent，天然防注入
          scrollDown();
        } else if (ev.error) {
          failed = true;
          out.textContent = '⚠️ ' + ev.error;
          out.classList.add('err');
        }
      }
    }
  } catch (e) {
    failed = true;
    out.textContent = '⚠️ 请求失败：' + e.message;
    out.classList.add('err');
  }

  if (!failed && raw) {
    // 结束后换成渲染好的 markdown（渲染在服务端，复用它已经过测试的实现）
    const html = await renderMd(raw);
    if (html) out.innerHTML = html;
    history.push({ role: 'assistant', content: raw });
  } else if (!failed && !raw) {
    out.textContent = '（模型没有返回内容）';
  }

  busy = false;
  sendBtn.disabled = false;
  inputEl.focus();
  scrollDown();
}

sendBtn.addEventListener('click', send);
inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + 'px';
});

/* ---------------- 拖动分栏 ---------------- */
(function initGutter() {
  const g = document.getElementById('gutter');
  const right = document.getElementById('pane-right');
  let dragging = false;
  g.addEventListener('mousedown', e => {
    dragging = true; e.preventDefault(); document.body.classList.add('dragging');
  });
  window.addEventListener('mousemove', e => {
    if (!dragging) return;
    const w = Math.min(Math.max(window.innerWidth - e.clientX, 300), window.innerWidth - 360);
    right.style.flexBasis = w + 'px';
  });
  window.addEventListener('mouseup', () => {
    dragging = false; document.body.classList.remove('dragging');
  });
})();

/* ---------------- 开场提示 ---------------- */
updateCtx(false);
(function greet() {
  const b = addBubble('assistant');
  let s = '我在看《' + DOC.title + '》';
  if (DOC.kind === 'arxiv') {
    s += '。刚开始我只能看到**摘要和章节目录**，问具体公式或实验数字答不上来——'
       + '点上面的「载入全文」我就能读整篇了。'
       + '\n\n看到不懂的一段，**直接用鼠标在左边选中它**，会浮出一个「问 AI」，'
       + '点一下就能就着那段话提问。';
  } else if (DOC.readme_html) {
    s += ' 的 README。有不懂的地方直接选中一段，浮出「问 AI」就能就着它提问。';
  } else {
    s += '。左侧内容没能内嵌，但你可以照常问我。';
  }
  const r = renderMd(s).then(html => { if (html) b.innerHTML = html; });
})();
</script>
</body>
</html>
"""


def _json_for_script(obj) -> str:
    """把对象塞进 <script> 里。转义 </ 防止提前闭合脚本标签。"""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def _reader_page(doc) -> str:
    item = ITEM_BY_ID.get(doc.item_id)
    track = ""
    if item is not None:
        t = TRACK_BY_ID.get(item.track)
        track = t.name if t else item.track

    payload = {
        "item_id": doc.item_id,
        "kind": doc.kind,
        "title": doc.title or doc.item_id,
        "track": track,
        "url": doc.url,
        # 「原文 ↗」指向真正的 arXiv 页面，作为代理渲染出问题时的逃生口
        "source_url": (doc.embed_url or doc.url) if doc.kind == "arxiv" else doc.url,
        # 左栏 iframe 的地址。arxiv 一律走同源代理，抓不到 HTML 时由代理自己
        # 在 iframe 内部显示说明页——**不能**按 doc.error 把 iframe 关掉，
        # 那样一次瞬时网络故障会把条目永久降级成摘要模式。
        "frame_url": (
            url_for("paper_frame", item_id=doc.item_id) if doc.kind == "arxiv" else ""
        ),
        "pdf_url": doc.pdf_url,
        "readme_html": doc.readme_html,
        "error": doc.error,
        "full_chars": len(doc.full_text),
        "full_truncated": doc.full_truncated,
        "abstract_chars": len(doc.abstract),
        # 引用的标记和上限都由服务端下发，保证前端拼出来的东西和
        # _chat_system 里告诉模型的完全一致——两边各写一份迟早漂移。
        "quote_open": config.QUOTE_OPEN,
        "quote_close": config.QUOTE_CLOSE,
        "quote_max": config.QUOTE_MAX_CHARS,
    }

    out = _READER_LAYOUT.replace("__DOC_JSON__", _json_for_script(payload))
    return out.replace("__PAGE_TITLE__", _esc(payload["title"]))


def _chat_system(doc, use_full: bool) -> str:
    """拼系统提示词。

    最关键的是那条「看不到就说看不到」——这是学习工具，一个编出来的公式
    比一句「我这边看不到」有害得多。
    """
    parts = [
        "你是一个论文阅读助手，帮一位做自动驾驶 / 强化学习 / VLA 的工程师"
        "读懂他正在看的材料。用简体中文回答，简洁直接，不要客套话。",
        "",
        f"【材料标题】{doc.title or doc.item_id}",
    ]

    if doc.sections:
        parts.append("【章节结构】")
        parts.extend(f"  {s}" for s in doc.sections[:40])

    if use_full and doc.full_text:
        parts += ["", "【正文】", doc.full_text]
        if doc.full_truncated:
            parts.append(
                f"\n注意：正文太长，上面只给了前 {config.FULLTEXT_MAX_CHARS} 字符，"
                "更靠后的章节你看不到。涉及后面内容的细节时，直说看不到。"
            )
    else:
        if doc.abstract:
            parts += ["", "【摘要】", doc.abstract]
        parts.append(
            "\n注意：你目前**只看到了摘要和章节目录**，没有正文。"
            "所以公式编号、实验数字、表格数值这类细节你并不知道。"
            f"\n**唯一的例外**：用户消息里被 {config.QUOTE_OPEN} 和 {config.QUOTE_CLOSE} "
            "夹住的那一段，是他用鼠标从正文里选出来给你看的，等同于正文。"
            "他多半就是在问这一段——基于它正常回答，不要说「我看不到正文」。"
        )

    parts += [
        "",
        "【回答要求】",
        "1. 严格基于上面给你的内容 + 你自己的知识，不要脑补这篇论文里没有的东西。",
        "2. 涉及你看不到的具体细节（公式编号、实验数字、表格数值）时，"
        "**直接说「这个我这边看不到」，并指出大概在哪一节**。绝对不要编造数字或公式。"
        f"但**只要那段内容出现在 {config.QUOTE_OPEN} 里，它就是你看得到的**，以它为准。",
        "3. 他正在左边读原文，回答时可以直接指路，例如「见 3.2 节」。",
        "4. 如果他的理解有偏差，直接指出来，不用顺着说。",
    ]
    return "\n".join(parts)


def _sse(obj: dict) -> str:
    """Server-Sent Events 的一条消息。"""
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


@app.route("/read/<item_id>")
def reader(item_id: str):
    if item_id not in ITEM_BY_ID:
        return _page(
            '<div class="card"><h2>找不到这个条目</h2>'
            f'<p class="empty">{_esc(item_id)}</p>'
            '<p><a class="link" href="/">← 回到今日计划</a></p></div>'
        ), 404

    return _reader_page(paper.load(item_id))


@app.route("/paper/<item_id>")
def paper_frame(item_id: str):
    """左栏 iframe 的内容：arXiv 页面经 `proxy` 改写后的同源副本。

    **函数名不能叫 `paper`**——模块顶部已经 `import paper` 了，同名的视图函数
    会把它盖掉，后面所有 `paper.load()` 都会炸。

    这个路由只会在 iframe 里被加载，所以「失败」的呈现方式是一张看得懂的说明页，
    而不是 Flask 的错误页或者浏览器的网络错误页——那样用户只会看到一片空白。
    """
    if item_id not in ITEM_BY_ID:
        return Response(
            proxy.fallback_page(item_id, "找不到这个条目", "", "",
                                "课程表里没有这个 id。"),
            status=404, mimetype="text/html")

    doc = paper.load(item_id)
    if doc.kind != "arxiv":
        return Response(
            proxy.fallback_page(item_id, doc.title or item_id, doc.pdf_url, doc.url,
                                "这个条目的左栏不是论文原文，看右侧问答就行。"),
            mimetype="text/html")

    arxiv_id = paper.arxiv_id_of(ITEM_BY_ID[item_id])
    served = (
        proxy.serve(item_id, arxiv_id, force=request.args.get("force") == "1")
        if arxiv_id else None
    )
    if served is None:
        return Response(
            proxy.fallback_page(
                item_id, doc.title or item_id, doc.pdf_url, doc.embed_url or doc.url,
                doc.error or "没能抓到 arXiv 的 HTML 版，可能是网络不通。",
            ),
            mimetype="text/html")

    html, csp = served
    # mimetype 只给 "text/html"：Flask 会自己补 charset，写成
    # "text/html; charset=utf-8" 会得到重复的 charset 段。
    return Response(html, mimetype="text/html", headers={
        "Content-Security-Policy": csp,
        # 页面里带着本次响应现生成的 nonce，不能进浏览器缓存
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


@app.route("/api/doc/<item_id>")
def api_doc(item_id: str):
    doc = paper.load(item_id)
    payload = doc.to_dict(with_full=False)
    payload["sections"] = doc.sections[:60]
    return Response(
        json.dumps(payload, ensure_ascii=False), mimetype="application/json"
    )


@app.route("/api/md", methods=["POST"])
def api_md():
    body = request.get_json(silent=True) or {}
    html_out = render.md_to_html(str(body.get("text") or ""))
    return Response(json.dumps({"html": html_out}, ensure_ascii=False),
                    mimetype="application/json")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    body = request.get_json(silent=True) or {}
    item_id = str(body.get("item_id") or "")
    use_full = bool(body.get("use_full"))

    raw_history = body.get("messages") or []
    messages = [
        # 上限给到 12000 而不是 8000：一条消息里可能带着最长 QUOTE_MAX_CHARS
        # 的引用片段，留出余量。卡在 8000 会把「引用 + 较长的问题」从中间静默
        # 截断，而引用块自带的截断告知在更靠后的位置，正好被切掉——
        # 模型就会以为它拿到的是完整段落。
        {"role": m.get("role"), "content": str(m.get("content"))[:12000]}
        for m in raw_history
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and m.get("content")
    ]

    def gen():
        if not messages:
            yield _sse({"error": "没有收到消息"})
            return

        client = llm_plan._client()
        if client is None:
            yield _sse({"error": "缺少 anthropic 依赖，跑一下 pip install anthropic"})
            return
        if not llm_plan.has_credentials():
            yield _sse({"error": llm_plan.credential_hint()})
            return

        doc = paper.load(item_id)
        system = _chat_system(doc, use_full)

        try:
            # 聊天走最朴素的流式调用：不传 thinking / output_config。
            # 实测第三方兼容端点会接受但不执行这些参数，不如不用。
            with client.messages.stream(
                model=llm_plan.MODEL_PLAN,
                max_tokens=4000,
                system=system,
                messages=messages,
            ) as stream:
                for piece in stream.text_stream:
                    yield _sse({"delta": piece})
        except Exception as e:
            yield _sse({"error": f"{type(e).__name__}: {str(e)[:200]}"})
            return

        yield _sse({"done": True})

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    # 启动时就把凭据情况说清楚，别等用户在浏览器里提问才发现认证失败
    source = llm_plan._ensure_credentials()
    if llm_plan.has_credentials():
        print(f"🔑 模型凭据来自：{source or '环境变量'}")
    else:
        print("⚠️  没有找到模型凭据 —— AI 点评和右栏问答会不可用。")
        print(llm_plan.credential_hint())

    print(f"📚 打开浏览器访问 http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}  （Ctrl+C 退出）")
    # load_dotenv=False：Flask 自己也会去找 .env，但没装 python-dotenv 时会打印
    # 「Install python-dotenv to use them」，暗示 .env 没被读取——而实际上
    # study_planner/__init__.py 已经读了。两套加载器只会互相打脸，关掉 Flask 这套。
    app.run(host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT,
            debug=False, load_dotenv=False)
