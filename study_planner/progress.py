"""进度状态：每条 item 的状态、每日历史、连续学习天数。

状态文件是整个工具唯一需要持久化的东西，CLI 和网页面板共用它。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional

from . import config

# item 的状态取值
STATUS_TODO = "todo"        # 还没做（默认）
STATUS_DONE = "done"        # 已完成
STATUS_SKIPPED = "skipped"  # 主动跳过，不再推荐


@dataclass
class ItemState:
    """单条课程的进度。"""

    status: str = STATUS_TODO
    first_seen: Optional[str] = None    # 第一次被推荐是哪天（ISO 格式）
    last_seen: Optional[str] = None     # 最近一次被推荐是哪天
    done_at: Optional[str] = None       # 完成日期
    shaky: bool = False                 # 标记「没读懂」，会进复习队列
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ItemState":
        # 过滤掉不认识的键，方便以后加字段时不炸老状态文件
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Progress:
    """全部进度。"""

    version: int = 1
    items: dict[str, ItemState] = field(default_factory=dict)
    # 每日历史，每条形如 {"date": "2026-09-26", "main": "...", "task": "...", "review": "...", "fresh": [...]}
    history: list[dict] = field(default_factory=list)
    longest_streak: int = 0

    # ---------------- 单条 item 的读写 ----------------

    def state_of(self, item_id: str) -> ItemState:
        """取某条的进度，没有就现建一个（不落盘）。"""
        st = self.items.get(item_id)
        if st is None:
            st = ItemState()
            self.items[item_id] = st
        return st

    def is_done(self, item_id: str) -> bool:
        st = self.items.get(item_id)
        return st is not None and st.status == STATUS_DONE

    def is_skipped(self, item_id: str) -> bool:
        st = self.items.get(item_id)
        return st is not None and st.status == STATUS_SKIPPED

    def prereq_met(self, item_id: str) -> bool:
        """前置是否全部完成。被跳过的前置不算完成，避免推荐链断裂。"""
        from .knowledge import ITEM_BY_ID

        item = ITEM_BY_ID.get(item_id)
        if item is None:
            return False
        return all(self.is_done(p) for p in item.prereq)

    def mark_seen(self, item_id: str, day: date) -> None:
        """记一笔「今天推荐过它」。"""
        st = self.state_of(item_id)
        iso = day.isoformat()
        if st.first_seen is None:
            st.first_seen = iso
        st.last_seen = iso

    def mark(self, item_id: str, status: str, day: date) -> None:
        """把某条标记成完成 / 跳过 / 待办。"""
        st = self.state_of(item_id)
        st.status = status
        if status == STATUS_DONE:
            st.done_at = day.isoformat()
            st.shaky = False  # 完成了就不再复习
        elif status != STATUS_DONE:
            st.done_at = None

    def mark_shaky(self, item_id: str, day: date) -> None:
        """标记「没读懂」：保持未完成，但进入复习队列。"""
        st = self.state_of(item_id)
        st.status = STATUS_TODO
        st.done_at = None
        st.shaky = True
        st.last_seen = day.isoformat()

    def due_for_review(self, day: date) -> list[str]:
        """到期待复习的 item id：标了 shaky，且距上次看到已经过了若干天。"""
        out: list[str] = []
        for item_id, st in self.items.items():
            if not st.shaky or st.status == STATUS_DONE:
                continue
            if st.last_seen is None:
                out.append(item_id)
                continue
            try:
                last = date.fromisoformat(st.last_seen)
            except ValueError:
                out.append(item_id)
                continue
            if (day - last).days >= config.REVIEW_AFTER_DAYS:
                out.append(item_id)
        return sorted(out)

    # ---------------- 统计 ----------------

    def done_count(self, track: Optional[str] = None) -> int:
        """完成数。给定 track 就只数那条 track 的。"""
        if track is None:
            return sum(1 for st in self.items.values() if st.status == STATUS_DONE)

        from .knowledge import ITEM_BY_ID

        return sum(
            1 for item_id, st in self.items.items()
            if st.status == STATUS_DONE
            and item_id in ITEM_BY_ID
            and ITEM_BY_ID[item_id].track == track
        )

    def track_stats(self) -> dict[str, dict[str, int]]:
        """每条 track 的 {完成, 总数, 已解锁}，给页面画进度条用。"""
        from .knowledge import ITEMS

        stats: dict[str, dict[str, int]] = {}
        for it in ITEMS:
            bucket = stats.setdefault(it.track, {"done": 0, "total": 0, "unlocked": 0})
            bucket["total"] += 1
            if self.is_done(it.id):
                bucket["done"] += 1
            if self.prereq_met(it.id):
                bucket["unlocked"] += 1
        return stats

    def active_days(self) -> list[str]:
        """有完成记录的日期列表（升序）。"""
        days = {
            st.done_at for st in self.items.values()
            if st.status == STATUS_DONE and st.done_at
        }
        return sorted(days)

    def current_streak(self, today: date) -> int:
        """当前连续学习天数。今天还没完成不算断——从昨天往前数。"""
        days = {date.fromisoformat(d) for d in self.active_days()}
        if not days:
            return 0

        # 今天没完成就从昨天开始数，避免「早上打开就显示断了」
        cursor = today if today in days else today - timedelta(days=1)
        streak = 0
        while cursor in days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def record_day(self, day: date, plan: dict) -> None:
        """把今天的计划记进历史（同一天重复调用会覆盖，不重复追加）。"""
        iso = day.isoformat()
        main = (plan.get("main") or {}).get("id")
        task = (plan.get("task") or {}).get("id")
        review = (plan.get("review") or {}).get("id")
        entry = {
            "date": iso,
            "main": main,
            "task": task,
            "review": review,
            "fresh": [f.get("id") for f in plan.get("fresh", [])],
        }
        self.history = [h for h in self.history if h.get("date") != iso]
        self.history.append(entry)
        self.history.sort(key=lambda h: h.get("date", ""))
        # 历史只留最近 180 天，避免文件无限膨胀
        self.history = self.history[-180:]

        self.longest_streak = max(self.longest_streak, self.current_streak(day))

    # ---------------- 落盘 ----------------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "items": {k: v.to_dict() for k, v in self.items.items()},
            "history": self.history,
            "longest_streak": self.longest_streak,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Progress":
        return cls(
            version=d.get("version", 1),
            items={k: ItemState.from_dict(v) for k, v in (d.get("items") or {}).items()},
            history=d.get("history") or [],
            longest_streak=d.get("longest_streak", 0),
        )

    @classmethod
    def load(cls) -> "Progress":
        """读状态文件。不存在或坏了都返回空进度，绝不抛异常——面板不能因此打不开。"""
        path = config.STATE_PATH
        if not path.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls()

    def save(self) -> None:
        config.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.STATE_PATH.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
