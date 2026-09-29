"""把「进度 + 画像 + 生成的课程表」打包成一个文件，供换机器/重新 clone 时恢复。

`data/` 是 gitignore 的——这是对的（那是本机状态），但代价是**重新 clone 一次
进度就没了**。这个模块就是补这个洞。

包里**故意装三样东西**，不是只装进度：新 clone 上没有生成的课程表，
只恢复 `state.json` 会得到一堆完成记录指向不存在的 id——正是 `done_count()`
那个「5/4」的老问题，外加满屏找不到的条目。

包里**不含任何凭据**。这一条用测试保证，不是靠承诺：凭据在 `.env` 或
`~/.claude/settings.json` 里，本模块从头到尾没读过它们。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config

FORMAT = "study-planner-bundle"
BUNDLE_VERSION = 1

# 备份默认落在用户家目录，**不能落在 data/ 里**：
# data/ 正是重新 clone 会消失的东西，把备份放那儿等于没备份。
DEFAULT_DIR = Path.home() / "study-planner-backup"


def default_path() -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d")
    return DEFAULT_DIR / f"study-planner-{stamp}.json"


def build(prog, profile: Optional[dict] = None,
          curriculum_payload: Optional[dict] = None) -> dict:
    """把当前状态打包成 dict。"""
    done = prog.done_count()
    return {
        "format": FORMAT,
        "version": BUNDLE_VERSION,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "curriculum": curriculum_payload,
        "profile": profile or {},
        "progress": prog.to_dict(),
        "counts": {
            "items": len(prog.items),
            "done": done,
            "stale_done": prog.stale_done_count(),
        },
    }


def read_curriculum_file(path=None) -> Optional[dict]:
    """把生成的课程表原样读出来（不校验——校验是 import 时的事）。"""
    p = config.CURRICULUM_PATH if path is None else Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def inspect(path) -> tuple:
    """读一个包，返回 (bundle, error)。**什么都不写。**"""
    path = Path(path)
    if not path.is_file():
        return {}, f"文件不存在：{path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, f"读不动或不是合法 JSON：{e}"

    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return {}, f"这不是一个 study-planner 备份包（format 不是 {FORMAT!r}）"

    ver = data.get("version")
    if not isinstance(ver, int) or ver > BUNDLE_VERSION:
        return {}, (
            f"这个包来自更新的版本（{ver!r}，本程序只认 {BUNDLE_VERSION}）。"
            "先更新代码再导入。"
        )
    if not isinstance(data.get("progress"), dict):
        return {}, "包里没有可用的进度数据"
    return data, ""


def describe(bundle: dict, current) -> str:
    """导入前给用户看的对比摘要。**先讲清楚会发生什么，再动手。**"""
    from .progress import Progress

    def counts(b):
        c = b.get("counts") or {}
        return c

    inc = bundle.get("curriculum") or {}
    inc_items = len(inc.get("items") or []) if isinstance(inc, dict) else 0
    inc_tracks = len(inc.get("tracks") or []) if isinstance(inc, dict) else 0

    lines = [
        f"  导出时间   {bundle.get('exported_at', '?')}",
        f"  课程表     {'包里自带 ' + str(inc_items) + ' 条 / ' + str(inc_tracks) + ' 方向' if inc_items else '包里没有（将沿用当前的）'}",
        f"  进度       已完成 {counts(bundle).get('done', '?')}",
        f"  当前       已完成 {current.done_count()}",
    ]

    # 包里的完成记录有多少条不在包内课程表里——这些会保留但**不计入**进度
    if inc_items:
        from .knowledge import Item
        known = {i.get("id") for i in inc.get("items") or [] if isinstance(i, dict)}
        prog_items = (bundle.get("progress") or {}).get("items") or {}
        orphan = sum(1 for i, st in prog_items.items()
                     if isinstance(st, dict) and st.get("status") == "done"
                     and i not in known)
        if orphan:
            lines.append(f"  其中       {orphan} 条已完成的 id 不在包内课程表里"
                         " → 会保留在状态文件，但不计入进度")

    prof = bundle.get("profile") or {}
    if prof.get("topics"):
        lines.append(f"  profile    topics={prof['topics']}")

    return "\n".join(lines)


def apply(bundle: dict, backup_dir=None) -> list:
    """写入进度/画像/课程表，返回写过的文件路径列表。

    **覆盖之前先把现有的备份走。** 覆盖是不可逆的，而用户手上这份可能
    是他唯一的一份。
    """
    from .progress import Progress

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = Path(backup_dir) if backup_dir else config.BACKUP_DIR / stamp
    written = []

    def backup(path: Path) -> None:
        if not path.is_file():
            return
        try:
            bdir.mkdir(parents=True, exist_ok=True)
            (bdir / path.name).write_bytes(path.read_bytes())
        except OSError:
            pass        # 备份失败不该拦住导入，但下面会告诉用户目录在哪

    # 1) 课程表
    inc = bundle.get("curriculum")
    if isinstance(inc, dict) and inc.get("items"):
        backup(config.CURRICULUM_PATH)
        config.CURRICULUM_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.CURRICULUM_PATH.write_text(
            json.dumps(inc, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(str(config.CURRICULUM_PATH))

    # 2) 画像
    prof = bundle.get("profile")
    if isinstance(prof, dict) and prof:
        backup(config.PROFILE_PATH)
        config.PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.PROFILE_PATH.write_text(
            json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(str(config.PROFILE_PATH))

    # 3) 进度
    backup(config.STATE_PATH)
    prog = Progress.from_dict(bundle.get("progress") or {})
    prog.save()
    written.append(str(config.STATE_PATH))

    return written
