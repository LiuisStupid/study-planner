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
  python cli.py profile [...]  看/改画像，并按画像生成自己的课程表（--regenerate）
  python cli.py export [路径]  把进度+画像+课程表打包（换机器/重新 clone 时用）
  python cli.py import <路径>  从包里恢复（--dry-run 只看不动）
  python cli.py restart      重启网页面板（自动停掉旧的，再前台启动）
  python cli.py restart -d   同上，但后台启动、立刻返回（给 AI / 脚本用）
  python cli.py stop         停掉网页面板
  python cli.py serve        同 restart（保留旧名字）

<id> 支持只写前缀，够唯一就行。例如 python cli.py done rlvla-fpo

改了代码之后必须 restart——Flask 是 debug=False 起的，不会自动重载。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from study_planner import (
    bundle,
    config,
    curriculum,
    knowledge,
    llm_plan,
    planner,
    progress as progress_mod,
    render,
)
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

    stale = prog.stale_done_count()
    print(f"进度 {prog.done_count()}/{len(ITEMS)}　"
          f"连续 {prog.current_streak(today)} 天　"
          f"最长 {prog.longest_streak} 天"
          # 换过课程表才有这个。说出来而不是藏起来——数字对不上时用户才知道为什么
          + (f"　（另有 {stale} 条已完成的不在当前课程表里）" if stale else "")
          + "\n")

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
            # 必须用 .get()：due_for_review 返回的是状态文件里的 id，
            # 换过课程表之后可能已经不在 ITEM_BY_ID 里了。原来这里是裸索引，
            # 那种 id 一旦到期就让整个 status 命令崩掉。
            it = ITEM_BY_ID.get(i)
            print(f"  - {it.title if it else i + '（已不在当前课程表，可忽略）'}")
    print("\n下一步：python cli.py today")


PROFILE_FIELDS = ("topics", "level", "goal", "daily_minutes",
                  "arxiv_categories", "arxiv_keywords", "github_queries")


def _print_profile(profile: dict) -> None:
    if not profile:
        print("还没有画像（用的是内置课程表：端到端自动驾驶 / RL / VLA）。\n")
        print("想换成你自己的方向：")
        print('  python cli.py profile --topics "量子计算与纠错" '
              '--level "写过 Qiskit" --regenerate')
        return

    print(f"画像文件：{config.PROFILE_PATH}")
    for k in PROFILE_FIELDS:
        v = profile.get(k)
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            v = "、".join(str(x) for x in v)
        print(f"  {k:<18} {v}")
    print()


def cmd_profile() -> None:
    """看 / 改 / 按画像重新生成课程表。

    带参数时**完全不交互**——这是刻意的：「想学什么方向」不是秘密，可以在对话里
    问用户，AI 拿到答案就能直接跑这条命令。对比 onboard.py 收 API key 那条路径，
    那边必须由用户在自己的终端里输，因为 key 是秘密。
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="python cli.py profile",
        description="看/改画像，并按画像生成课程表。不带参数就是查看现状。",
    )
    ap.add_argument("--topics", help="想学的方向，一句话")
    ap.add_argument("--level", help="已有的基础")
    ap.add_argument("--goal", help="想达到什么")
    ap.add_argument("--minutes", type=int, help="每天能投入多少分钟")
    ap.add_argument("--arxiv-cats", dest="cats", help="arXiv 分类，逗号分隔，如 quant-ph,cs.LG")
    ap.add_argument("--keywords", help="arXiv 关键词，逗号分隔，**要用英文**")
    ap.add_argument("--repos", help="GitHub 搜索词，逗号分隔")
    ap.add_argument("--regenerate", action="store_true", help="调模型按画像生成课程表")
    ap.add_argument("--accept-dead-links", action="store_true",
                    help="arXiv 抽查有查不到的编号时也照样写入")
    ap.add_argument("--reset", action="store_true", help="删掉生成的课程表，回到内置那份")
    ap.add_argument("--yes", action="store_true", help="跳过确认")
    args = ap.parse_args(sys.argv[2:])

    def split(v):
        return [x.strip() for x in re.split(r"[,，、;；]", v or "") if x.strip()] or None

    if args.reset:
        if config.CURRICULUM_PATH.exists():
            config.CURRICULUM_PATH.unlink()
            print(f"已删除 {config.CURRICULUM_PATH}，回到内置课程表。")
            print("重启面板后生效：python cli.py restart")
        else:
            print("本来就没有生成过课程表，用的是内置那份。")
        return

    # ---- 合并画像 ----
    profile = {}
    if config.PROFILE_PATH.exists():
        try:
            profile = json.loads(config.PROFILE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            profile = {}

    changed = {}
    if args.topics:
        changed["topics"] = args.topics
    if args.level:
        changed["level"] = args.level
    if args.goal:
        changed["goal"] = args.goal
    if args.minutes:
        changed["daily_minutes"] = args.minutes
    if args.cats:
        changed["arxiv_categories"] = split(args.cats)
    if args.keywords:
        changed["arxiv_keywords"] = split(args.keywords)
    if args.repos:
        changed["github_queries"] = split(args.repos)

    if not changed and not args.regenerate:
        _print_profile(profile)
        return

    if changed:
        profile.update({k: v for k, v in changed.items() if v})
        profile["format"] = config.PROFILE_FORMAT
        profile["version"] = config.PROFILE_VERSION
        config.PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.PROFILE_PATH.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已更新画像：{config.PROFILE_PATH}")
        for k, v in changed.items():
            print(f"  {k} = {v if not isinstance(v, list) else '、'.join(v)}")
        applied = config.apply_profile(profile)
        print(f"  本次进程生效的项：{'、'.join(applied) if applied else '（无）'}")

    if not args.regenerate:
        print("\n重启面板后生效：python cli.py restart")
        print("想按这份画像生成课程表：加 --regenerate")
        return

    # ---- 生成课程表 ----
    if not profile.get("topics"):
        print("\n❌ 先生成画像：--topics 是必填的（生成课程表要靠它）。")
        raise SystemExit(1)

    print("\n调模型生成课程表…（这一下会花你的 API 额度，10–60 秒）")
    result = llm_plan.generate_curriculum(profile)
    if not result["ok"]:
        print(f"❌ 生成失败：{result['error']}")
        print("   没有写入任何文件——原来的课程表原封不动。")
        raise SystemExit(1)

    tracks, p1 = curriculum.parse_tracks(result["tracks"])
    items, p2 = curriculum.parse_items(result["items"])
    problems = p1 + p2
    if tracks and items:
        problems += curriculum.validate(tracks, items)

    if problems:
        print(f"❌ 生成的课程表没通过校验（{len(problems)} 个问题），**没有写入**：")
        for p in problems[:8]:
            print(f"   - {p}")
        if len(problems) > 8:
            print(f"   …另有 {len(problems) - 8} 个")
        print("\n再跑一次通常会好一些（模型每次结果不一样）。")
        raise SystemExit(1)

    print("✅ 结构校验通过")
    print(curriculum.summary(tracks, items))
    print()

    # arXiv 抽查：一次请求问清所有编号是否存在。**不拦写盘**——
    # 网络抖动和编造编号在这里长得一样，用网络结果否决一份结构完好的课程表
    # 会让用户在一个其实没问题的东西上反复重试。
    arxiv_ids = []
    for it in items:
        m = re.search(r"arxiv\.org/abs/(\d{4}\.\d{4,5})", it.url)
        if m:
            arxiv_ids.append((it.id, m.group(1)))

    if arxiv_ids:
        from study_planner import sources

        print(f"arXiv 抽查（1 次请求，{len(arxiv_ids)} 条）…")
        found = sources.verify_arxiv_ids([a for _, a in arxiv_ids])

        if not found:
            # **整批查不成 ≠ 编号是假的。** 网络不通、被限流（实测遇到过一次 429）
            # 都会走到这里。把它当成"全是编的"会拒绝一份其实没问题的课程表，
            # 还让用户在一个不存在的问题上反复重试。所以只提示，不拦。
            print("  ⚠️ 这次没查成（网络或限流），跳过抽查。")
            print("     链接对不对请自己过一眼，或者之后跑 python cli.py check-links。")
        else:
            missing = [(iid, aid) for iid, aid in arxiv_ids if not found.get(aid)]
            print(f"  ✅ {len(arxiv_ids) - len(missing)} 条编号真实存在")
            if missing:
                print(f"  ⚠️ {len(missing)} 条查不到（可能是编的）：")
                for iid, aid in missing[:5]:
                    print(f"       {iid} → https://arxiv.org/abs/{aid}")
                if not args.accept_dead_links and not args.yes:
                    print("\n这些编号在 arXiv 上不存在。**没有写入。**")
                    print("  要么再跑一次生成（模型每次不一样），")
                    print("  要么确认可以接受后加 --accept-dead-links。")
                    raise SystemExit(1)
                print("  （--accept-dead-links，照样写入）")

    # 模型给的抓取口味只在用户没自己指定过时才写进画像
    for key in ("arxiv_keywords", "github_queries", "arxiv_categories"):
        if result.get(key) and not profile.get(key):
            profile[key] = result[key]
    profile["format"] = config.PROFILE_FORMAT
    profile["version"] = config.PROFILE_VERSION
    config.PROFILE_PATH.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    curriculum.save_file(config.CURRICULUM_PATH, tracks, items, meta={
        "generated_by": llm_plan.MODEL_PLAN,
        "topics": profile.get("topics", ""),
    })

    print(f"\n✅ 已写入 {config.CURRICULUM_PATH}")
    print("   这份课程表是 AI 生成的，建议自己过一眼——尤其是标题和链接对不对。")
    stale = progress_mod.Progress.load().stale_ids()
    if stale:
        print(f"\n⚠️ 你原来的进度里有 {len(stale)} 条不在新课程表里。"
              "它们**保留在状态文件里**，只是不再计入进度；换回旧的课程表就会回来。")
    print("\n重启面板后生效：python cli.py restart")


_NO_TTY_IMPORT = """\
导入会**覆盖**你现在的进度、画像和课程表，所以需要你确认一下。

AI 这边没有终端，也读不到你的确认，所以不能替你按这个键。
请在你自己的终端里跑下面这条（把 <包> 换成实际路径）：

    cd {root}
    {python} cli.py import <包> --yes

想先看看会发生什么、不写入任何东西：

    {python} cli.py import <包> --dry-run
"""


def _repo_guard(path) -> bool:
    """包不能落在仓库里——这个仓库是公开的，进度属于个人数据。

    返回 True 表示可以继续写。
    """
    try:
        p = Path(path).resolve()
    except OSError:
        return True
    root = config.ROOT.resolve()
    if root == p or root in p.parents:
        print(f"❌ 拒绝写到仓库里：{p}")
        print(f"   这个仓库是公开的，进度和画像属于你的个人数据，别推进去。")
        print(f"   默认路径是 {bundle.DEFAULT_DIR}，在仓库外面。")
        return False
    return True


def cmd_export() -> None:
    """把进度 + 画像 + 课程表打包成一个文件，供换机器/重新 clone 时恢复。

    默认落在 ~/study-planner-backup/，**不在 data/ 里**——data/ 正是重新
    clone 会消失的东西，备份放那儿等于没备份。
    """
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    path = Path(arg).expanduser() if arg else bundle.default_path()

    if not _repo_guard(path):
        raise SystemExit(1)

    prog = load()
    profile = {}
    if config.PROFILE_PATH.exists():
        try:
            profile = json.loads(config.PROFILE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            profile = {}

    payload = bundle.build(prog, profile, bundle.read_curriculum_file())

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as e:
        print(f"❌ 写不了 {path}：{e}")
        raise SystemExit(1)

    c = payload["counts"]
    print(f"✅ 已导出到 {path}")
    print(f"   课程表  {'自带' if payload.get('curriculum') else '无（当前也没生成过）'}"
          f"　画像  {'有' if profile else '无'}"
          f"　进度  已完成 {c['done']}"
          + (f"（另有 {c['stale_done']} 条不在当前课程表里）" if c.get("stale_done") else ""))
    print()
    print("   这个文件**不在 git 里**，重新 clone 时不会跟着走。")
    print("   想换机器用，就把它复制到网盘/私有仓；到新机器上跑：")
    print("       python cli.py import <包的路径>")


def cmd_import() -> None:
    """从备份包恢复。**会覆盖**现有进度——没确认过就不动手。"""
    if len(sys.argv) < 3:
        raise SystemExit("用法：python cli.py import <包路径> [--dry-run] [--yes]")

    path = Path(sys.argv[2]).expanduser()
    dry_run = "--dry-run" in sys.argv[3:]
    assume_yes = "--yes" in sys.argv[3:]

    data, err = bundle.inspect(path)
    if err:
        print(f"❌ {err}")
        raise SystemExit(1)

    print(f"将要导入 {path}")
    print(bundle.describe(data, load()))
    print()

    if dry_run:
        print("--dry-run：什么都没写。")
        return

    # 没有 TTY 又没有 --yes 就停手并把命令打印出来。
    # 和 onboard.py 收凭据那条路径同一个纪律：不能让「我检查一下」
    # 变成「我把你的进度覆盖了」。
    if not assume_yes and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(_NO_TTY_IMPORT.format(root=config.ROOT, python=sys.executable))
        raise SystemExit(2)

    if not assume_yes:
        if input("确认覆盖？[y/N] ").strip().lower() not in ("y", "yes"):
            print("未做任何修改。")
            return

    written = bundle.apply(data)
    print("✅ 已恢复：")
    for w in written:
        print(f"   {w}")
    print(f"   覆盖前的旧文件备份在 {config.BACKUP_DIR}")
    print()
    print("⚠️ 必须重启面板才生效——课程表是进程启动时读的，")
    print("   正在跑的面板会把旧课程表一直用下去。")
    print("       python cli.py restart")


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

    用 lsof 而不是 `pkill -f "python app.py"`——macOS 上的 `python3` 是个壳，
    真进程叫 `Python`（大写 P），按小写的进程名匹配根本打不中。
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


def _stop_panel(port: int) -> list[str]:
    """停掉占着端口的旧面板。返回被停掉的 PID（空列表表示本来就没有）。

    只报告 PID、**不擅自判断该不该停**——叫这个名字的命令（restart / stop）
    语义就是"顶掉旧的"，所以停是它该做的事。
    """
    # 下面这些 print 都带 flush——重定向到文件时 stdout 是块缓冲的，
    # 不加的话提示要等进程退出才刷出来，看起来像卡住了。
    pids = _port_pids(port)
    if not pids:
        return []

    print(f"发现旧面板在运行（PID {', '.join(pids)}），正在停止…", flush=True)
    for pid in pids:
        subprocess.run(["kill", pid], check=False)

    # 等端口真正释放，别急着启动（否则新进程会 bind 失败然后静默退出）
    for _ in range(20):
        if not _port_pids(port):
            return pids
        time.sleep(0.25)

    still = _port_pids(port)
    print(f"  普通 kill 没停掉，改用 kill -9（PID {', '.join(still)}）", flush=True)
    for pid in still:
        subprocess.run(["kill", "-9", pid], check=False)
    time.sleep(0.5)

    if _port_pids(port):
        raise SystemExit(f"❌ 端口 {port} 仍被占用，请手动检查：lsof -i tcp:{port}")
    return pids


def _detached_env() -> dict:
    """后台面板用的环境变量。

    Claude Code 会把它**自己那份** ANTHROPIC_* 注入给子进程（实测 CLAUDECODE=1
    时是设好的）。不摘掉的话，AI 帮忙起的这个面板会拿 Claude Code 的凭据，
    而不是用户配在 .env 里的那份——而体检验的恰恰是后者，两边就对不上了：
    体检说"验过了"，跑起来的其实是另一个身份。

    用户的终端里不会有 CLAUDECODE，所以这只影响"由 AI 起面板"这一种情况，
    人自己敲 `cli.py restart` 时环境原样保留。
    """
    env = dict(os.environ)
    if env.get("CLAUDECODE"):
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                  "ANTHROPIC_BASE_URL", "STUDY_PLANNER_MODEL"):
            env.pop(k, None)
    return env


def _start_detached(port: int) -> None:
    """后台启动面板，并确认它真的起来了再返回。

    两件事让它能活过调用者：
      · start_new_session=True —— 另起一个会话，调它的那个 shell（终端、
        或者 AI 那边一次性执行的命令）退出时不会顺带把它 kill 掉；
      · stdout/stderr 重定向到文件 —— 不然它继承的管道一关，往里写日志
        就会收到 SIGPIPE。
    """
    log = config.ROOT / "data" / "panel.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    with open(log, "ab") as fh:
        proc = subprocess.Popen(
            [sys.executable, str(config.ROOT / "app.py")],
            cwd=str(config.ROOT), env=_detached_env(),
            stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
            start_new_session=True,
        )

    for _ in range(40):                      # 最多等 10 秒
        pids = _port_pids(port)
        if pids:
            print(f"✅ 面板已在后台运行（PID {', '.join(pids)}）", flush=True)
            print(f"   http://{config.DASHBOARD_HOST}:{port}", flush=True)
            print(f"   日志 {log}", flush=True)
            print("   停止 python3 cli.py stop", flush=True)
            return
        if proc.poll() is not None:
            raise SystemExit(
                f"❌ 面板起来就退了（退出码 {proc.returncode}）。日志：\n   {log}")
        time.sleep(0.25)

    raise SystemExit(f"❌ 等了 10 秒，{port} 还是没人监听。日志：\n   {log}")


def cmd_restart() -> None:
    """重启网页面板：先停掉占着端口的旧进程，再启动。

    默认**前台**启动——这样 Ctrl+C 一直有效，是给人用的。
    `--detach`（简写 `-d`）则后台起、确认起来了就返回，是给 AI 和脚本用的：
    前台模式在那种场景下永远不返回，会把整条命令挂到超时。
    """
    detach = "--detach" in sys.argv[2:] or "-d" in sys.argv[2:]
    port = config.DASHBOARD_PORT

    if not _stop_panel(port):
        print(f"端口 {port} 空闲，直接启动。", flush=True)

    if detach:
        _start_detached(port)
        return

    print(f"→ 打开 http://{config.DASHBOARD_HOST}:{port}   （按 Ctrl+C 停止）\n", flush=True)

    # 前台运行。用 call 而不是 Popen，这样 Ctrl+C 能传到子进程。
    subprocess.call([sys.executable, str(config.ROOT / "app.py")])


def cmd_stop() -> None:
    """停掉面板。后台（--detach）起来的那个尤其需要它——它没有终端可 Ctrl+C。"""
    port = config.DASHBOARD_PORT
    if not _stop_panel(port):
        print(f"端口 {port} 上没有面板在跑。")
        return
    print(f"已停止（端口 {port} 现在空着）。")


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
    "profile": cmd_profile,
    "export": cmd_export,
    "import": cmd_import,
    "restart": cmd_restart,
    "stop": cmd_stop,
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
