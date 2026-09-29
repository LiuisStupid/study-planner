"""进度状态：每条 item 的状态、每日历史、连续学习天数。

状态文件是整个工具唯一需要持久化的东西，CLI 和网页面板共用它。
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional

from . import config

# item 的状态取值
STATUS_TODO = "todo"        # 还没做（默认）
STATUS_DONE = "done"        # 已完成
STATUS_SKIPPED = "skipped"  # 主动跳过，不再推荐


def _quarantine(path, err: Exception) -> None:
    """把读不动的状态文件改名留档，然后让调用方当空进度继续。

    改名而不是删除：用户可能想自己翻一翻那份文件。失败也绝不抛异常——
    状态文件坏了不该让面板打不开，那正是这个函数存在的理由。
    """
    target = path.with_name(path.name + ".corrupt")
    try:
        if target.exists():
            # 已经留过一份了（比如连续两次启动都读到坏文件），别把上一份覆盖掉
            target = target.with_name(f"{target.name}.{int(path.stat().st_mtime)}")
        path.rename(target)
        print(f"[progress] 状态文件读不动（{type(err).__name__}），已留档到 {target}，"
              f"本次按空进度启动。", file=sys.stderr)
    except OSError:
        pass


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
        """坏条目降级成默认值，**绝不抛异常**。

        两条防线，针对的是不同的人：
        - 过滤不认识的键：以后加字段时不炸老状态文件（自己人改的）
        - 逐字段校验类型：用户手改 state.json 改坏了也不炸（外人改的）
        第二种情况原来会漏：`items` 里某个值是字符串的话，`d.items()` 直接
        AttributeError 逃出去，面板整个打不开。
        """
        if not isinstance(d, dict):
            return cls()

        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        st = cls(**known)

        if st.status not in (STATUS_TODO, STATUS_DONE, STATUS_SKIPPED):
            st.status = STATUS_TODO
        if not isinstance(st.shaky, bool):
            # 特别提防 "false" 这种字符串：它是真值，会让条目莫名其妙进复习队列
            st.shaky = False
        for f in ("first_seen", "last_seen", "done_at"):
            if getattr(st, f) is not None and not isinstance(getattr(st, f), str):
                setattr(st, f, None)
        if not isinstance(st.notes, str):
            st.notes = ""
        return st


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
            except (ValueError, TypeError):
                # TypeError 不是多余的：fromisoformat 传进非字符串（比如手改出来的
                # 数字 20260926）抛的是 TypeError，只写 ValueError 抓不住。
                # ItemState.from_dict 现在会挡掉这种值，这里是第二道。
                out.append(item_id)
                continue
            if (day - last).days >= config.REVIEW_AFTER_DAYS:
                out.append(item_id)
        return sorted(out)

    # ---------------- 统计 ----------------

    def done_count(self, track: Optional[str] = None) -> int:
        """完成数，**只数当前课程表里还存在的条目**。

        为什么必须限定：`items` 是按 id 扁平存的，换过课程表之后会留下"野 id"。
        不限定的话它们照样被数进去，于是页面上会出现「5/4」这种分子大于分母的数字
        ——分母是 `len(ITEMS)`，分子却包含已经不在课程表里的条目。
        """
        from .knowledge import ITEM_BY_ID

        if track is None:
            return sum(
                1 for item_id, st in self.items.items()
                if st.status == STATUS_DONE and item_id in ITEM_BY_ID
            )

        return sum(
            1 for item_id, st in self.items.items()
            if st.status == STATUS_DONE
            and item_id in ITEM_BY_ID
            and ITEM_BY_ID[item_id].track == track
        )

    def stale_ids(self) -> list[str]:
        """还在状态文件里、但已不在当前课程表中的 id。

        换过课程表就会有。它们**故意留着不删**：用户换回原来的课程表时，
        那些完成记录应该回来。但所有统计和渲染都要忽略它们，并且要**说出来**
        ——默默把差异藏掉，用户只会觉得数字对不上。
        """
        from .knowledge import ITEM_BY_ID

        return sorted(i for i in self.items if i not in ITEM_BY_ID)

    def stale_done_count(self) -> int:
        """其中已经标记为完成的条数，显示「另有 N 条」时用。"""
        from .knowledge import ITEM_BY_ID

        return sum(
            1 for i, st in self.items.items()
            if i not in ITEM_BY_ID and st.status == STATUS_DONE
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
        days = set()
        for d in self.active_days():
            try:
                days.add(date.fromisoformat(d))
            except (ValueError, TypeError):
                # 手改出来的 done_at（"昨天"、20260926）不该让整个首页崩掉。
                # from_dict 会挡掉非字符串，但挡不住"是字符串但不是日期"。
                continue
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
        # str() 是必需的：手改出来的 date 要是数字，和字符串混在排序里会抛 TypeError
        self.history.sort(key=lambda h: str(h.get("date") or ""))
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
        """容忍手改坏的文件：根不是对象、items 不是对象、history 不是数组，
        一律降级成默认值而不是抛异常。"""
        if not isinstance(d, dict):
            return cls()

        raw_items = d.get("items")
        items = (
            {k: ItemState.from_dict(v) for k, v in raw_items.items()
             if isinstance(k, str)}
            if isinstance(raw_items, dict) else {}
        )

        raw_hist = d.get("history")
        history = [h for h in raw_hist if isinstance(h, dict)] if isinstance(raw_hist, list) else []

        version = d.get("version")
        streak = d.get("longest_streak")

        return cls(
            version=version if isinstance(version, int) else 1,
            items=items,
            history=history,
            longest_streak=streak if isinstance(streak, int) and streak >= 0 else 0,
        )

    @classmethod
    def load(cls) -> "Progress":
        """读状态文件。不存在或坏了都返回空进度，绝不抛异常——面板不能因此打不开。

        **坏文件改名留档，不就地覆盖。** 原来只是返回空 `Progress`，可后面任何一次
        标记都会 `save()`，把那个可能还能救的文件用一份空数据盖掉——不可逆。
        用户手上这份进度可能是他唯一的一份。
        """
        path = config.STATE_PATH
        if not path.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, AttributeError) as e:
            # TypeError / AttributeError 是给类型不对的 JSON 留的：
            # json 语法错误是 ValueError，但 `"items": []` 这种合法 JSON
            # 会一路走到 `.items()` 上抛 AttributeError。
            _quarantine(path, e)
            return cls()

    def save(self) -> None:
        config.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.STATE_PATH.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
