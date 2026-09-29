"""每日学习计划推荐器：端到端 / RL / VLA 方向的课程表 + 进度追踪。"""
from __future__ import annotations

from .config import load_env_file

__version__ = "0.1.0"

# 读项目根目录的 .env，补进 os.environ。
#
# **必须在这里，不能挪到 config.py 末尾或 app.py 开头。** 实测：
#     python -c "from study_planner import llm_plan"
# 走的是 __init__ → llm_plan → knowledge 这条链，而 knowledge.py 只 import
# dataclasses，**config 根本不会被导入**；偏偏 llm_plan.py 顶层的
# `MODEL_PLAN = os.environ.get("STUDY_PLANNER_MODEL", ...)` 是模块级求值的，
# 错过这个时机就读不到 .env 里配的模型名。
#
# app.py / cli.py 里恰好也能用，只是因为它们把 config 写在了 fromlist 的第一位——
# 谁把那行的顺序调一下就会静默失效。放在包的 __init__ 里则和导入顺序无关。
load_env_file()
