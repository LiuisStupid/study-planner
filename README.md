# 📚 每日学习计划推荐器

给**端到端自动驾驶 / 强化学习 / VLA** 三个方向排每天的学习计划。

解决的问题不是「不知道学什么」，而是**没有稳定的每日节奏**：这三个方向的论文
半衰期只有 3–12 个月，靠临时想起来去 arXiv 翻，容易变成追热点而没有主线。

## 它做什么

每天给你一份 **30–45 分钟**的计划，四块内容：

| 块 | 内容 |
|---|---|
| 🎯 精读 | 1 篇论文，按前置依赖解锁 |
| 🔧 小任务 | 1 个 repo/doc 或动手任务，**刻意换一条方向**，保证跨方向接触 |
| 🔁 复习 | 之前标了「没读懂」、且已过 3 天的条目 |
| 🆕 新鲜事 | arXiv / GitHub 实时抓取，**可选**，不挤占主线 |

## 两个关键设计

**1. 课程表带前置依赖。** 每条资源声明自己的 `prereq`，只有前置全部完成才会被推荐。
这样就不会出现「还没读 GAE 就被推 GRPO 论文」——那正是直接刷 arXiv 的典型翻车方式。

**2. 同一天的计划是确定的。** 算法对 `(进度, 日期)` 完全确定：反复刷新页面得到同一份计划
（不会每次刷新都换一批让你无所适从），换天才变化。打散用的是 `hashlib` 而不是内置
`hash()`——后者按进程加盐，跨进程不稳定。

## 快速开始

```bash
# 建一个独立虚拟环境（这个项目不依赖别的仓库）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 看今天的计划
.venv/bin/python cli.py today

# 启动网页面板（推荐用 restart，见下方说明）
.venv/bin/python cli.py restart     # http://127.0.0.1:8766
```

## 命令行

```bash
python cli.py today          # 生成并打印今天的计划
python cli.py next           # 预告接下来会推什么
python cli.py done <id>      # 标记完成
python cli.py skip <id>      # 跳过，不再推荐
python cli.py shaky <id>     # 标记「没读懂」，3 天后重新推
python cli.py status         # 看整体进度
python cli.py check          # 课程表自检（依赖成环 / id 重复）
python cli.py check-links    # 逐个校验课程表里的 URL 还能不能打开
python cli.py restart        # 重启网页面板（自动停掉旧的，再前台启动）
```

`<id>` 支持只写前缀，够唯一就行，例如 `python cli.py done rlvla-fpo`。

### 关于启动面板

**改了代码之后必须 `restart`** —— Flask 是以 `debug=False` 起的，不会自动重载模块。

`restart` 做三件事：找到占用 8766 端口的进程 → 停掉（普通 kill 不行就 kill -9）→
**前台**启动新的。前台是刻意的，这样 Ctrl+C 一直有效。

如果哪天你用 `&` 把它后台化了、又关掉了终端，进程会变成孤儿。这时手动停：

```bash
lsof -ti tcp:8766 | xargs kill
```

**别用 `pkill -f "python app.py"`** —— venv 里的 python 实际解析到系统 framework 的
`Python`（大写 P），按进程名匹配打不中，会以为杀掉了其实没有。按端口找才可靠。

## 网页面板

| 路由 | 内容 |
|---|---|
| `/` | 今日计划，每条可直接勾选「完成 / 没读懂 / 跳过」 |
| `/curriculum` | 课程总览，按方向分组，显示解锁状态 |
| `/progress` | 连续天数、各方向完成度、最近 30 天记录 |
| `/read/<id>` | **论文阅读器**：左栏原文 + 右栏 AI 问答 |
| `/refresh` | 清掉当日缓存重抓 arXiv / GitHub |

端口固定 `8766`，改 `config.py` 的 `DASHBOARD_PORT` 可以换。

## 论文阅读器

点课程条目的标题（今日计划页或课程总览页都行）会打开阅读器，**新标签页打开**，
这样当天的计划页不会丢。左边是原文，右边可以随时提问。

### 左栏显示什么

三种情况，取决于资源类型：

| 类型 | 左栏 |
|---|---|
| arXiv 论文 | iframe 嵌入 `arxiv.org/html/<id>`；右上角有 PDF 切换 |
| GitHub 仓库 | 服务端抓 README 渲染（GitHub 拒绝被嵌入，只能这样） |
| 其他（HuggingFace 文档等） | 信息卡 + 「在新标签页打开」按钮 |

**为什么用 `/html/` 而不是 `/abs/`**：`abs` 页发了 `frame-ancestors 'none'`，
浏览器会拒绝渲染；`/html/` 没有任何防嵌入头，而且 2015 年的老论文也有。
这个结论是实测出来的，改这块前先自己验证一遍。

### 右栏 AI 的两档上下文

| 档位 | 发给模型的内容 | 实测首字延迟 |
|---|---|---|
| 默认 | 标题 + 摘要 + 章节目录 | ~4 秒 |
| 全文 | 上面这些 + 整篇正文 | ~4 秒（正文长则更久） |

点「载入全文」切换。全文超过 `FULLTEXT_MAX_CHARS`（默认 60K 字符）会截断，
**截断这件事会同时告诉你和模型**，不会悄悄截。

**最重要的一条设计**：系统提示词里写死了「问到的细节如果超出可见范围，
直接说看不到并指出在哪一节，不许编造」。这是学习工具，一个编出来的公式
比一句「我这边看不到」有害得多。验证时专门测过这条——只给摘要时问
「4.3 节的消融数字是多少」，模型会指出第 4 节根本没有 4.3，
相关消融在附录 D，然后明说不给数字。

### 对话走哪个模型

复用「AI 点评」那套凭据（`ANTHROPIC_API_KEY` 或 `ANTHROPIC_AUTH_TOKEN` +
`ANTHROPIC_BASE_URL`），**不需要额外配置**。聊天走最朴素的流式调用，
不传 `thinking` / `output_config`——实测第三方兼容端点会接受但不执行这些参数。

对话历史只存在浏览器内存里，刷新页面就没了。服务端完全无状态。

## AI 点评（可选）

配好凭据后，每天的计划上方会多一段模型写的点评：为什么今天推这两条、
和最近几天学的内容有什么承接、一个具体提醒。

```bash
# 官方 Anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# 或者任何提供 Anthropic 兼容端点的服务（以 DeepSeek 为例）
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_AUTH_TOKEN=<你的 DeepSeek key>

# 可选：换模型（DeepSeek 按模型名映射，见下表）
export STUDY_PLANNER_MODEL=claude-haiku-4-5
```

SDK 会自动读取这些环境变量，**不需要改代码**。

### 凭据从哪来（一个很容易踩的坑）

按这个顺序找：

1. **当前环境变量** —— 你 `export` 的，或终端 profile 里的
2. **`~/.claude/settings.json` 的 `env` 块** —— 找不到就自动读这里补齐

第 2 条是必需的，因为 Claude Code 把凭据配在它自己的配置文件里，只注入给
**它自己的进程**，不会传给你手动启动的程序。于是就会出现：

> 在 Claude Code 里让 AI 测着好好的，你自己 `cli.py restart` 一跑就报
> `TypeError: Could not resolve authentication method`

所以本工具启动时会**打印凭据来源**，第一眼就能看出对不对：

```
🔑 模型凭据来自：/Users/you/.claude/settings.json
```

没找到凭据时也会给出具体该怎么办，而不是等你在浏览器里提问才炸。

另外注意：Anthropic SDK 的构造函数在**没有凭据时不报错**，要等到真正发请求
才抛异常。所以判断「有没有凭据」不能靠 `try: Anthropic() except:`，必须主动检查
环境变量——这个坑也踩过。

### 第三方端点的兼容性

实测 DeepSeek 的 Anthropic 兼容层（`api.deepseek.com/anthropic`）：

| 参数 | 表现 |
|---|---|
| 基本调用 | ✅ 正常 |
| `output_config`（结构化输出） | ⚠️ **接受但不执行** —— 不报错，但返回自由文本而非 JSON |
| `thinking` | ✅ 接受 |

所以 `llm_plan.py` 做了两层容错：

1. 拿不到合法 JSON 时，**把返回的原文直接当点评用**（而不是丢弃退回模板）
2. 靠提示词约定：让模型在最后单独一行以「提醒：」开头写那句话，
   代码再切出来填进 `focus_hint`

**如果你用官方 Anthropic，走的是结构化输出路径，`focus_hint` 由 schema 保证。**

### 模型档位

DeepSeek 会按模型名映射，所以换个名字就是换档：

| 这里写 | DeepSeek 映射到 | 实测延迟 |
|---|---|---|
| `claude-opus-4-8`（默认） | `deepseek-v4-pro` | ~13s |
| `claude-haiku-4-5` | `deepseek-flash` | ~9s |

一段 3–5 句点评用 flash 足够。**延迟只在当天第一次加载时出现**——点评按天缓存在进程内存里，
之后刷新页面不会重复调用。

### 失败时不会静默

没配 / 调不通 / 模型拒答 —— 这几种情况都会在点评正文末尾写明**具体原因**
（例如「AI 点评不可用：AuthenticationError: invalid api key」），
而不是都显示同一句「请配置 API key」。这一条是踩过坑之后加的：
把「没配置」和「配置了但不兼容」显示成同一个现象，会让人完全不知道该修什么。

## 改课程表

课程表在 `study_planner/knowledge.py`，就是一组 `Item` 的列表：

```python
Item(
    "vla-openvla",                              # 唯一 id
    "OpenVLA：开源 7B VLA",                      # 标题
    "vla",                                      # 所属方向
    "paper",                                    # paper / repo / doc
    "https://arxiv.org/abs/2406.09246",         # 链接
    "intermediate",                             # foundation / intermediate / advanced
    45,                                         # 预计分钟数
    "为什么现在读这个",                            # why
    prereq=("vla-octo",),                       # 前置条目 id
    tags=("开源",),
),
```

改完必须跑一遍自检——**手写的 URL 很容易失效**：

```bash
python cli.py check         # 查依赖成环、id 重复、指向不存在的条目
python cli.py check-links   # 逐个 HEAD 校验，失效的写进 data/dead_links.txt
```

## 目录结构

```
study-planner/              # ← 项目根目录
├── cli.py                  # 命令行入口
├── app.py                  # Flask 网页面板
├── data/
│   ├── state.json          # 进度状态（CLI 和面板共用的唯一数据源）
│   ├── cache/              # arXiv / GitHub 当日缓存
│   └── dead_links.txt      # check-links 的产物
└── study_planner/
    ├── config.py           # 路径常量 + 可调参数（每日时长、抓取条数等）
    ├── knowledge.py        # ★ 课程表
    ├── progress.py         # 状态模型 + JSON 读写 + 连续天数
    ├── sources.py          # arXiv / GitHub 抓取（只用标准库）
    ├── planner.py          # 选片引擎
    ├── paper.py            # ★ 论文/仓库正文抓取与解析（阅读器用）
    ├── render.py           # Markdown 渲染（含 md_to_html）
    └── llm_plan.py         # AI 点评 + 模板兜底
```

## 调参

常用参数都在 `study_planner/config.py`：

| 参数 | 默认 | 说明 |
|---|---|---|
| `DAILY_MINUTES` | 45 | 每日预算，影响小任务的挑选 |
| `BUDGET_SLACK` | 10 | 预算宽容量；超出才提示「小任务可顺延」 |
| `FRESH_ITEMS` | 3 | 每天抓几条新鲜事 |
| `REVIEW_AFTER_DAYS` | 3 | 「没读懂」的条目隔几天重推 |
| `ARXIV_KEYWORDS` | — | 命中才算相关，改这里调整抓取口味 |
| `FULLTEXT_MAX_CHARS` | 60000 | 喂给模型的正文上限，超了截断并告知 |
| `DASHBOARD_PORT` | 8766 | 面板端口 |

论文正文缓存在 `data/cache/docs/`，内容不会变所以长期有效（不同于按天过期的
抓取缓存）。想强制重抓就删掉这个目录。

## 断网也能用

`sources.py` 的抓取失败只记日志、不抛异常，计划会退化成纯课程版（只少「新鲜事」
那一块）。**基础功能不依赖网络。**

GitHub API 未认证时限流较严，可以设 `GITHUB_TOKEN` 提高额度（可选）。
