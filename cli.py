#!/usr/bin/env python3
"""命令行入口：
  python cli.py today        生成并打印今天的计划
  python cli.py next         预告接下来会推什么（按当前排序）
  python cli.py done <id>    标记某条为已完成
  python cli.py skip <id>    跳过某条，不再推荐
  python cli.py shaky <id>   标记「没读懂」，过几天会重新推
  python cli.py status       看整体进度
  python cli.py check       课程表自检（依赖成环 / id 重复 / 指向不存在的条目）
  python cli.py check-links  逐个校验课程表里的 URL 是否还能打开
  python cli.py restart      重启网页面板（自动停掉旧的，再前台启动）
  python cli.py serve        同上（保留旧名字）

<id> 支持只写前缀，够唯一就行。例如 python cli.py done rlvla-fpo

改了代码之后必须 restart——Flask 是 debug=False 起的，不会自动重载。
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date

from study_planner import config, knowledge, planner, progress as progress_mod, render
from study_planner.knowledge import ITEM_BY_ID, ITEMS, TRACKS


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def resolve(item_id: str) -> str:
    """把用户输入的 id（允许前缀）解析成完整 id。"""
    if item_id in ITEM_BY_ID:
        return item_id
    matches = [i for i in ITEM_BY_ID if i.startswith(item_id)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"找不到条目：{item_id}\n用 python cli.py status 看看有哪些。")
    raise SystemExit(
        f"「{item_id}」匹配到多条，请写得更具体：\n  " + "\n  ".join(sorted(matches))
    )


def load() -> progress_mod.Progress:
    return progress_mod.Progress.load()


def _display_width(s: str) -> int:
    """字符串的终端显示宽度。中文是双宽，直接 len() 会导致对齐错位。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s: str, width: int) -> str:
    """按显示宽度右侧补空格。"""
    return s + " " * max(0, width - _display_width(s))


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------
def cmd_today() -> None:
    prog = load()
    today = date.today()
    plan = planner.build_today(prog, today)

    result = _comment_for(plan, prog, today)
    print(render.plan_to_text(plan, result["comment"], result["focus_hint"]))

    # 记一笔当天历史（同一天重复跑会覆盖，不会重复追加）
    prog.record_day(today, plan)
    prog.save()


def cmd_next() -> None:
    prog = load()
    today = date.today()
    rows = planner.next_up(prog, today, count=8)
    if not rows:
        print("已解锁的条目都做完了。往 knowledge.py 里补新资源，或把跳过的条目改回待办。")
        return
    print("按当前进度，接下来会推：\n")
    for i, r in enumerate(rows, 1):
        print(f"  {i}. [{r['track_name']}/{r['level']}] {r['title']}")
    print("\n（每天按当天日期做稳定打散，实际顺序可能略有不同）")


def _mark(item_id: str, status: str) -> None:
    full = resolve(item_id)
    prog = load()
    today = date.today()
    prog.mark_seen(full, today)
    prog.mark(full, status, today)
    prog.save()
    item = ITEM_BY_ID[full]
    label = {"done": "已完成 ✅", "skipped": "已跳过 ⏭", "todo": "已重置为待办"}[status]
    print(f"{label}  {item.title}")


def cmd_done() -> None:
    _mark(sys.argv[2], "done")


def cmd_skip() -> None:
    _mark(sys.argv[2], "skipped")


def cmd_shaky() -> None:
    full = resolve(sys.argv[2])
    prog = load()
    today = date.today()
    prog.mark_shaky(full, today)
    prog.save()
    print(f"已标记「没读懂」😵  {ITEM_BY_ID[full].title}")
    print(f"   {config.REVIEW_AFTER_DAYS} 天后会重新推进复习队列。")


def cmd_status() -> None:
    prog = load()
    today = date.today()
    stats = prog.track_stats()

    print(f"进度 {prog.done_count()}/{len(ITEMS)}　"
          f"连续 {prog.current_streak(today)} 天　"
          f"最长 {prog.longest_streak} 天\n")

    for t in TRACKS:
        st = stats.get(t.id, {"done": 0, "total": 0, "unlocked": 0})
        total = st["total"] or 1
        filled = int(st["done"] / total * 20)
        bar = "█" * filled + "░" * (20 - filled)
        print(f"  {_pad(t.name, 22)} {bar} {st['done']:>2}/{st['total']:<2}  已解锁 {st['unlocked']}")

    due = prog.due_for_review(today)
    if due:
        print(f"\n待复习 {len(due)} 条：")
        for i in due[:10]:
            print(f"  - {ITEM_BY_ID[i].title}")
    print("\n下一步：python cli.py today")


def cmd_check() -> None:
    problems = knowledge.check_consistency()
    if not problems:
        print(f"✅ 课程表自检通过：{len(ITEMS)} 条，{len(TRACKS)} 个方向，无依赖成环。")
        return
    print(f"❌ 发现 {len(problems)} 个问题：")
    for p in problems:
        print(f"  - {p}")
    raise SystemExit(1)


def _head_ok(url: str) -> tuple[bool, str]:
    """检查一个 URL 是否能打开。有些站点拒绝 HEAD，就退回 GET 只取 1 字节。"""
    ua = {"User-Agent": "Mozilla/5.0 (compatible; study-planner link checker)"}

    for method in ("HEAD", "GET"):
        try:
            headers = dict(ua)
            if method == "GET":
                headers["Range"] = "bytes=0-0"
            req = urllib.request.Request(url, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
                if 200 <= resp.status < 400:
                    return True, str(resp.status)
                return False, f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            # 405/403 说明这个方法是通的，只是不让用；换 GET 再试
            if method == "HEAD" and e.code in (403, 405, 501):
                continue
            return False, f"HTTP {e.code}"
        except (urllib.error.URLError, OSError, ValueError) as e:
            if method == "HEAD":
                continue
            return False, str(e)

    return False, "无响应"


def cmd_check_links() -> None:
    print(f"正在校验 {len(ITEMS)} 个链接…（每个最多 {config.HTTP_TIMEOUT} 秒）\n")
    dead: list[tuple[str, str, str]] = []

    for i, item in enumerate(ITEMS, 1):
        ok, info = _head_ok(item.url)
        mark = "✅" if ok else "❌"
        print(f"  {mark} [{i:>2}/{len(ITEMS)}] {item.id:<24} {info}")
        if not ok:
            dead.append((item.id, item.url, info))

    print()
    if not dead:
        print("✅ 全部链接可访问。")
        try:
            config.DEAD_LINKS_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return

    print(f"❌ {len(dead)} 个链接有问题：\n")
    lines = []
    for item_id, url, info in dead:
        line = f"{item_id}\t{url}\t{info}"
        lines.append(line)
        print(f"  {ITEM_BY_ID[item_id].title}")
        print(f"    {url}  ({info})")

    try:
        config.DEAD_LINKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.DEAD_LINKS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n已写入 {config.DEAD_LINKS_PATH}，去 knowledge.py 里改掉这些 URL。")
    except OSError as e:
        print(f"\n（写文件失败：{e}）")


def _port_pids(port: int) -> list[str]:
    """找出正在监听某个端口的进程 PID。

    用 lsof 而不是 `pkill -f "python app.py"`——venv 里的 python 实际解析到
    系统 framework 的 `Python`（大写 P），按进程名匹配根本打不中。
    按端口找才可靠。
    """
    try:
        out = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.split() if line.strip()]


def cmd_restart() -> None:
    """重启网页面板：先停掉占着端口的旧进程，再前台启动。

    前台启动是刻意的——这样 Ctrl+C 一直有效。如果后台化（结尾加 `&`），
    终端一关你就只能靠 `lsof -ti tcp:8766 | xargs kill` 才能停掉它。
    """
    port = config.DASHBOARD_PORT

    # 下面这些 print 都带 flush——重定向到文件时 stdout 是块缓冲的，
    # 不加的话提示要等进程退出才刷出来，看起来像卡住了。
    pids = _port_pids(port)
    if pids:
        print(f"发现旧面板在运行（PID {', '.join(pids)}），正在停止…", flush=True)
        for pid in pids:
            subprocess.run(["kill", pid], check=False)

        # 等端口真正释放，别急着启动（否则新进程会 bind 失败然后静默退出）
        for _ in range(20):
            if not _port_pids(port):
                break
            time.sleep(0.25)

        still = _port_pids(port)
        if still:
            print(f"  普通 kill 没停掉，改用 kill -9（PID {', '.join(still)}）", flush=True)
            for pid in still:
                subprocess.run(["kill", "-9", pid], check=False)
            time.sleep(0.5)

        if _port_pids(port):
            raise SystemExit(f"❌ 端口 {port} 仍被占用，请手动检查：lsof -i tcp:{port}")
        print("  已停止", flush=True)
    else:
        print(f"端口 {port} 空闲，直接启动。", flush=True)

    print(f"→ 打开 http://{config.DASHBOARD_HOST}:{port}   （按 Ctrl+C 停止）\n", flush=True)

    # 前台运行。用 call 而不是 Popen，这样 Ctrl+C 能传到子进程。
    subprocess.call([sys.executable, str(config.ROOT / "app.py")])


def cmd_serve() -> None:
    """和 restart 是一回事，保留这个名字是为了兼容旧习惯。"""
    cmd_restart()


def _comment_for(plan: dict, prog, today: date) -> dict:
    """拿点评。CLI 路径下失败也不该中断，所以单独包一层。"""
    try:
        from study_planner.llm_plan import generate_comment

        return generate_comment(plan, prog, today)
    except Exception:
        from study_planner.llm_plan import template_comment

        return template_comment(plan, prog, today)


# ---------------------------------------------------------------------------
# 分发
# ---------------------------------------------------------------------------
_COMMANDS = {
    "today": cmd_today,
    "next": cmd_next,
    "done": cmd_done,
    "skip": cmd_skip,
    "shaky": cmd_shaky,
    "status": cmd_status,
    "check": cmd_check,
    "check-links": cmd_check_links,
    "restart": cmd_restart,
    "serve": cmd_serve,
}

_NEEDS_ARG = {"done", "skip", "shaky"}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print(__doc__)
        return

    cmd = sys.argv[1]
    if cmd in _NEEDS_ARG and len(sys.argv) < 3:
        raise SystemExit(f"用法：python cli.py {cmd} <item-id>\n\n{__doc__}")

    _COMMANDS[cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
