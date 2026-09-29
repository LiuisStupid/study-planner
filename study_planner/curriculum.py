"""课程表文件（`<data>/curriculum.json`）的格式、校验与读写。

内置课程表在 `knowledge.py` 里；这个模块管的是**用户自己那份**——由
`cli.py profile --regenerate` 按画像让模型生成，或者用户手写。

设计要点：

1. **校验逻辑不在这里，在 `knowledge.check_items()`。** 本模块只做「字段对不对」
   这一层，成环 / 悬空前置 / 孤立簇那些交给 `check_items`——因为内置课程表也要用
   同一套规则，两处各写一份迟早会有一边漏掉新加的检查。

2. **写盘是原子的**（先写 .tmp 再 os.replace）。半份 curriculum.json 的后果是
   下次 import 静默退回内置，而用户以为生成成功了——比直接报错难查得多。

3. **本模块不在顶层 import `knowledge`**，全部在函数内导入。原因是循环：
   `knowledge` 在执行到自己的 `_resolve_curriculum()` 时会反过来 import 本模块，
   那一刻 `knowledge` 只初始化到一半。函数内导入让谁先谁后都无所谓。
"""
from __future__ import annotations

import json
import os
import re
from hashlib import sha1
from pathlib import Path
from typing import Optional

FORMAT = "study-planner-curriculum"
SCHEMA_VERSION = 1

KINDS = ("paper", "repo", "doc")
LEVELS = ("foundation", "intermediate", "advanced")
_LEVEL_ORDER = {"foundation": 0, "intermediate": 1, "advanced": 2}

# id 形如 `qc-surface-code`：ASCII 小写 + 连字符 + 方向前缀，和内置的
# `rl-gae` / `diff-*` 风格一致。限制前缀是为了让 id 本身能读出归属。
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")

MIN_TRACKS, MAX_TRACKS = 3, 5
MIN_ITEMS, MAX_ITEMS = 10, 30
MINUTES_RANGE = (10, 120)

# planner 的「小任务」槽只从 repo/doc 里挑（见 planner.TASK_KINDS）。
# 全是论文的课程表会让那个槽每天都是空的——不是报错，是**安静地少一块**，
# 所以这里当硬性错误拦下来。
MIN_TASK_KINDS = 6


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def parse_tracks(raw) -> tuple[list, list[str]]:
    """把 JSON 里的 tracks 解析成 Track 列表。返回 (tracks, problems)。"""
    from .knowledge import Track

    problems: list[str] = []
    tracks: list = []

    if not isinstance(raw, list):
        return [], ["tracks 必须是一个数组"]

    seen: set[str] = set()
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            problems.append(f"tracks[{i}] 不是对象")
            continue
        tid = str(t.get("id") or "").strip()
        name = str(t.get("name") or "").strip()
        desc = str(t.get("desc") or "").strip()
        if not ID_RE.match(tid):
            problems.append(f"tracks[{i}] 的 id 不合法：{tid!r}（要 ASCII 小写+连字符）")
            continue
        if tid in seen:
            problems.append(f"重复的 track id：{tid}")
            continue
        if not name:
            problems.append(f"tracks[{i}]（{tid}）缺 name")
            continue
        seen.add(tid)

        try:
            weight = int(t.get("weight", 3))
        except (TypeError, ValueError):
            weight = 3
        tracks.append(Track(tid, name, min(max(weight, 1), 5), desc))

    if not (MIN_TRACKS <= len(tracks) <= MAX_TRACKS):
        problems.append(
            f"方向数 {len(tracks)} 不在 {MIN_TRACKS}–{MAX_TRACKS} 之间"
        )
    return tracks, problems


def parse_items(raw) -> tuple[list, list[str]]:
    """把 JSON 里的 items 解析成 Item 列表。返回 (items, problems)。"""
    from .knowledge import Item

    problems: list[str] = []
    items: list = []

    if not isinstance(raw, list):
        return [], ["items 必须是一个数组"]

    seen: set[str] = set()
    for i, d in enumerate(raw):
        if not isinstance(d, dict):
            problems.append(f"items[{i}] 不是对象")
            continue
        iid = str(d.get("id") or "").strip()
        if not ID_RE.match(iid):
            problems.append(f"items[{i}] 的 id 不合法：{iid!r}")
            continue
        if iid in seen:
            problems.append(f"重复的 item id：{iid}")
            continue
        seen.add(iid)

        kind = str(d.get("kind") or "").strip()
        if kind not in KINDS:
            problems.append(f"{iid} 的 kind 不合法：{kind!r}（只能是 {'/'.join(KINDS)}）")
            continue
        level = str(d.get("level") or "").strip()
        if level not in LEVELS:
            problems.append(f"{iid} 的 level 不合法：{level!r}（只能是 {'/'.join(LEVELS)}）")
            continue

        title = str(d.get("title") or "").strip()
        url = str(d.get("url") or "").strip()
        why = str(d.get("why") or "").strip()
        for field, val in (("title", title), ("url", url), ("why", why)):
            if not val:
                problems.append(f"{iid} 缺 {field}")
        if not (title and url and why):
            continue
        if not url.startswith(("http://", "https://")):
            problems.append(f"{iid} 的 url 不是 http(s)：{url[:60]}")

        try:
            minutes = int(d.get("minutes", 45))
        except (TypeError, ValueError):
            minutes = 45
        lo, hi = MINUTES_RANGE
        if not (lo <= minutes <= hi):
            problems.append(f"{iid} 的 minutes={minutes} 不在 {lo}–{hi} 之间")
            minutes = min(max(minutes, lo), hi)

        prereq = d.get("prereq") or []
        tags = d.get("tags") or []
        # 强制成 tuple：Item 是 frozen dataclass，planner 那边还会 asdict + list()。
        # 让 list 混进去的话，相等判断和哈希的行为都会变得很微妙。
        items.append(Item(
            iid, title, str(d.get("track") or "").strip(), kind, url, level, minutes, why,
            prereq=tuple(str(p) for p in prereq) if isinstance(prereq, (list, tuple)) else (),
            tags=tuple(str(t) for t in tags) if isinstance(tags, (list, tuple)) else (),
        ))

    if not (MIN_ITEMS <= len(items) <= MAX_ITEMS):
        problems.append(f"条目数 {len(items)} 不在 {MIN_ITEMS}–{MAX_ITEMS} 之间")
    return items, problems


def validate(tracks, items) -> list[str]:
    """完整的校验。**写盘之前必须调用。**

    分两层：上面是字段级（parse_* 已经做过），下面是结构级（复用 knowledge.check_items），
    再补两条课程表特有的：小任务槽够不够、每条方向有没有地基。
    """
    from .knowledge import check_items

    problems = list(check_items(tracks, items))

    task_kinds = [it for it in items if it.kind in ("repo", "doc")]
    if len(task_kinds) < MIN_TASK_KINDS:
        problems.append(
            f"repo/doc 只有 {len(task_kinds)} 条，至少要 {MIN_TASK_KINDS} 条"
            "——每日计划的「小任务」槽只从这两类里挑，不够的话那个槽会天天空着"
        )

    by_track: dict[str, list] = {}
    for it in items:
        by_track.setdefault(it.track, []).append(it)
    for t in tracks:
        bucket = by_track.get(t.id, [])
        if not bucket:
            problems.append(f"方向 {t.id}（{t.name}）下面一条都没有")
        elif not any(it.level == "foundation" for it in bucket):
            problems.append(f"方向 {t.id}（{t.name}）没有 foundation 级别的入口条目")

    return problems


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------
def digest_of(payload: dict) -> str:
    """给一份 payload 算指纹，用来判断「课程表是不是比画像旧了」。"""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "sha1:" + sha1(blob.encode("utf-8")).hexdigest()[:16]


def to_payload(tracks, items, meta: Optional[dict] = None) -> dict:
    out = {
        "format": FORMAT,
        "version": SCHEMA_VERSION,
        "tracks": [
            {"id": t.id, "name": t.name, "weight": t.weight, "desc": t.desc}
            for t in tracks
        ],
        "items": [
            {
                "id": it.id, "title": it.title, "track": it.track, "kind": it.kind,
                "url": it.url, "level": it.level, "minutes": it.minutes, "why": it.why,
                "prereq": list(it.prereq), "tags": list(it.tags),
            }
            for it in items
        ],
    }
    out.update(meta or {})
    return out


def load_file(path) -> tuple[list, list, str]:
    """读并校验一份课程表。返回 (tracks, items, 错误)。**不抛异常。**

    任何一步不对都返回空列表 + 中文原因，由调用方决定是退回内置还是报给用户。
    """
    path = Path(path)
    if not path.is_file():
        return [], [], "文件不存在"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], [], f"读不动或不是合法 JSON：{e}"

    if not isinstance(data, dict):
        return [], [], "顶层不是一个对象"
    if data.get("format") != FORMAT:
        return [], [], f"format 不是 {FORMAT!r}，这可能不是课程表文件"

    ver = data.get("version")
    if not isinstance(ver, int) or ver > SCHEMA_VERSION:
        return [], [], (
            f"文件版本是 {ver!r}，本程序只认 {SCHEMA_VERSION} 及以下。"
            "升级一下代码再试。"
        )

    tracks, p1 = parse_tracks(data.get("tracks"))
    items, p2 = parse_items(data.get("items"))
    problems = p1 + p2
    if tracks and items:
        problems += validate(tracks, items)

    if problems:
        head = "；".join(problems[:3])
        more = f"（另有 {len(problems) - 3} 条）" if len(problems) > 3 else ""
        return [], [], f"校验没过：{head}{more}"
    return tracks, items, ""


def save_file(path, tracks, items, meta: Optional[dict] = None) -> None:
    """原子写。**调用方必须先 validate()。**

    半份文件比没有文件更糟：下次 import 会静默退回内置，而用户以为生成成功了。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = to_payload(tracks, items, meta)

    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def summary(tracks, items) -> str:
    """给 CLI 打印的多行摘要。"""
    lines = [f"  {len(tracks)} 个方向 / {len(items)} 条"]
    for t in tracks:
        bucket = [it for it in items if it.track == t.id]
        kinds = {}
        for it in bucket:
            kinds[it.kind] = kinds.get(it.kind, 0) + 1
        detail = "，".join(f"{k} {v}" for k, v in sorted(kinds.items()))
        lines.append(f"    [{t.weight}] {t.name}（{t.id}）{len(bucket)} 条 · {detail}")
    return "\n".join(lines)
