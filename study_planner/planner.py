"""选片引擎：把课程表和实时源组合成一份「今天的计划」。

**核心约束：给定 (progress, day) 结果必须完全确定。**
同一天反复刷新页面得到同一份计划（否则你会不知道该听哪次推荐），换天才变化。

注意这里用的是 hashlib 而不是内置 hash()——Python 的字符串 hash 默认按进程加盐
（PYTHONHASHSEED），跨进程不稳定，会导致「同一天两次运行结果不同」。
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import date
from typing import Optional

from . import config
from .knowledge import ITEM_BY_ID, ITEMS, TRACK_BY_ID, Item
from .progress import Progress

# level 的排序权重：先打地基再上楼层
LEVEL_ORDER = {"foundation": 0, "intermediate": 1, "advanced": 2}

# 「小任务」只从这两类里挑——读代码比读论文轻，适合作为当天的动手部分
TASK_KINDS = ("repo", "doc")


def _stable_hash(day: date, item_id: str) -> int:
    """跨进程稳定的散列，用来做同日固定的 tie-break。"""
    h = hashlib.sha1(f"{day.isoformat()}|{item_id}".encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _to_dict(item: Item, progress: Optional[Progress] = None) -> dict:
    """Item 转成可序列化的 dict，顺带带上 track 的中文名。"""
    d = asdict(item)
    d["prereq"] = list(item.prereq)
    d["tags"] = list(item.tags)
    track = TRACK_BY_ID.get(item.track)
    d["track_name"] = track.name if track else item.track
    if progress is not None:
        st = progress.items.get(item.id)
        d["status"] = st.status if st else "todo"
        d["shaky"] = st.shaky if st else False
    return d


def _candidates(progress: Progress) -> list[Item]:
    """能推荐的条目：没完成、没跳过、前置全部满足。"""
    out = []
    for it in ITEMS:
        if it.track not in TRACK_BY_ID:
            continue
        if progress.is_done(it.id) or progress.is_skipped(it.id):
            continue
        if not progress.prereq_met(it.id):
            continue
        out.append(it)
    return out


def _track_has_pending_foundation(track: str, progress: Progress) -> bool:
    """这条 track 是否还有「前置已满足但没做」的基础条目。

    有的话就优先清基础，避免出现「地基没打完就上楼」的推荐。
    """
    for it in ITEMS:
        if it.track != track or it.level != "foundation":
            continue
        if progress.is_done(it.id) or progress.is_skipped(it.id):
            continue
        if progress.prereq_met(it.id):
            return True
    return False


def _rank(item: Item, progress: Progress, day: date) -> tuple:
    """排序键，越小越优先。"""
    # 1) 这条 track 还有基础没打完 → 优先
    foundation_pending = 0 if (
        item.level == "foundation" or not _track_has_pending_foundation(item.track, progress)
    ) else 1
    # 2) track 权重高的优先（主线 rl_x_vla 最高）
    weight = -TRACK_BY_ID[item.track].weight
    # 3) 同级里先基础后进阶
    level = LEVEL_ORDER.get(item.level, 9)
    # 4) 同日固定的稳定打散，避免每天都从同一条 track 开始
    tie = _stable_hash(day, item.id)
    return (foundation_pending, weight, level, tie)


def _pick_review(progress: Progress, day: date, exclude: set[str]) -> Optional[Item]:
    """挑一条到期的复习项。"""
    for item_id in progress.due_for_review(day):
        if item_id in exclude:
            continue
        item = ITEM_BY_ID.get(item_id)
        if item is not None:
            return item
    return None


def build_plan(
    progress: Progress,
    day: date,
    fresh_items: Optional[list[dict]] = None,
    fresh_errors: Optional[list[str]] = None,
) -> dict:
    """生成某一天的计划。不修改 progress——落盘由调用方决定。"""
    candidates = _candidates(progress)
    candidates.sort(key=lambda it: _rank(it, progress, day))

    main: Optional[Item] = candidates[0] if candidates else None

    # --- 小任务：读代码为主，且刻意换一条 track，保证每天跨方向接触 ---
    task: Optional[Item] = None
    task_pool = [
        it for it in candidates
        if it.kind in TASK_KINDS and (main is None or it.track != main.track)
    ]
    if not task_pool:
        # 换不到别的 track 就退而求其次，允许同 track
        task_pool = [it for it in candidates if it.kind in TASK_KINDS]

    over_budget = False
    if task_pool:
        budget = config.DAILY_MINUTES + config.BUDGET_SLACK
        main_min = main.minutes if main else 0
        affordable = [it for it in task_pool if main_min + it.minutes <= budget]

        if affordable:
            task = sorted(affordable, key=lambda it: _rank(it, progress, day))[0]
        else:
            # 精读本身就把预算吃完了（比如 45 分钟的综述）。
            # 这时仍然给一个小任务，但**如实标记超预算**，由渲染层提示
            # 「可以顺延到明天」——不能假装 75 分钟还是 45 分钟。
            task = sorted(task_pool, key=lambda it: (it.minutes, _rank(it, progress, day)))[0]
            over_budget = True

    # --- 复习：标记过「没读懂」且到期了的 ---
    exclude = {it.id for it in (main, task) if it is not None}
    review = _pick_review(progress, day, exclude)

    # --- 组装 ---
    total = sum(it.minutes for it in (main, task, review) if it is not None)
    # 复习是「补救」，不参与预算计算——它是额外欠下的债

    track_focus = ""
    if main is not None:
        t = TRACK_BY_ID.get(main.track)
        track_focus = t.name if t else main.track

    return {
        "date": day.isoformat(),
        "main": _to_dict(main, progress) if main else None,
        "task": _to_dict(task, progress) if task else None,
        "review": _to_dict(review, progress) if review else None,
        "fresh": fresh_items or [],
        "total_minutes": total,
        "budget_minutes": config.DAILY_MINUTES,
        "over_budget": over_budget,
        "track_focus": track_focus,
        "fresh_errors": fresh_errors or [],
        # 还剩多少条已解锁，用来提示「后面还有什么」
        "unlocked_left": len(candidates),
        "done_total": progress.done_count(),
        "total_items": len([it for it in ITEMS if it.track in TRACK_BY_ID]),
    }


def build_today(progress: Progress, day: Optional[date] = None, use_network: bool = True) -> dict:
    """生成今天的完整计划，含实时抓取。抓取失败自动降级成纯课程版。"""
    day = day or date.today()

    fresh: list[dict] = []
    errors: list[str] = []

    if use_network:
        # 延迟导入，保证不联网的调用路径完全不碰 sources
        from . import sources

        result = sources.fetch_fresh(day, limit=config.FRESH_ITEMS)
        fresh = result["items"]
        errors = result["errors"]

    return build_plan(progress, day, fresh_items=fresh, fresh_errors=errors)


def next_up(progress: Progress, day: date, count: int = 5) -> list[dict]:
    """按当前排序看「接下来会推什么」，给页面做预告用。"""
    candidates = _candidates(progress)
    candidates.sort(key=lambda it: _rank(it, progress, day))
    return [_to_dict(it, progress) for it in candidates[:count]]
