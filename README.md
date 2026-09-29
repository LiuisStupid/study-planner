# 📚 每日学习计划推荐器

给一个学习方向排每天的计划：每天 1 篇精读 + 1 个小任务 + 到期的复习，
配一个可以读论文、划词问 AI 的本地面板。

解决的问题不是「不知道学什么」，而是**没有稳定的每日节奏**：这几个方向的论文
半衰期只有 3–12 个月，靠临时想起来去 arXiv 翻，容易变成追热点而没有主线。

> **方向是自己的。** 仓库里内置的那份课程表是个示例（端到端自动驾驶 / RL / VLA），
> 你可以在 Claude Code 里说一句「我想学 XX」，它会按你的画像生成一份新的。
> 见下面的「[换个方向](#换个方向)」。

## 🚀 怎么开始

> **只读这一节就够了。** 从「它做什么」往下都是参考手册，需要时再查。

### 方式一：用 Claude Code（推荐）

在这个仓库目录里打开 Claude Code，说一句「**这个仓库怎么跑**」，
或者输入 `/study-planner-setup`。它会：

1. **体检** —— 检查 Python 版本、pip、依赖，以及已有的 API key **能不能真的调通**
2. **缺什么装什么** —— 把 `requirements.txt` 里的依赖 pip 装到手边的 `python3`（幂等，重复跑不会重装）
3. **凭据已经能用的话就直接跳到启动**，不会让你再配一遍
4. 需要配的话，把一条命令交给你 —— **你在自己的终端里**填 key，
   key 不经过对话，也不会被写进会话记录
5. **直接帮你把面板起起来**（后台启动，Claude Code 关掉也不影响），告诉你地址

背后的脚本也可以单独跑，不依赖 Claude Code：

```bash
python3 .claude/skills/study-planner-setup/scripts/onboard.py --check     # 体检 + 验证 key
python3 .claude/skills/study-planner-setup/scripts/onboard.py --install   # 只装依赖
python3 .claude/skills/study-planner-setup/scripts/onboard.py             # 交互式配凭据
```

### 方式二：手动三步

```bash
# 装依赖（只有 flask 和 anthropic 两个）
python3 -m pip install -r requirements.txt

# 看今天的计划
python3 cli.py today

# 启动网页面板（推荐用 restart，见下方说明）
python3 cli.py restart     # http://127.0.0.1:8766
```

> **不用建虚拟环境。** 依赖就两个，直接装进系统 python 最省事，也省掉换机器时
> 最容易卡住的一步（Debian/Ubuntu 上建 venv 要 `sudo apt install python3-venv`）。
> 系统 python 被 PEP 668 挡住（报 `externally-managed-environment`）时，
> `onboard.py --install` 会自动退到 `--user`——**任何一步都不需要 sudo**，
> 也**不要** `sudo pip`。
>
> 想装到某个特定的 python，就用那个 python 跑 `onboard.py`，装的和跑的就一定是同一个。

配凭据（面板里「AI 点评」和阅读器问答要用，不配也能跑，只是少了这两块）：

```bash
cp .env.example .env      # 然后填 key；或者用上面那个交互式脚本
chmod 600 .env
```

### 跑起来之后

打开 http://127.0.0.1:8766

- **今日**页：每条可以直接勾「完成 / 没读懂 / 跳过」
- 点条目标题进**论文阅读器**：左边原文，右边可以随时问 AI。
  **用鼠标选中左边任意一段会浮出「问 AI」**，点一下就能就着那段话提问

---

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

## 换个方向

内置的课程表是个**示例**（端到端自动驾驶 / RL / VLA）。换成你自己的：

**用 Claude Code（推荐）**：在仓库里说「我想学量子计算纠错，写过 Qiskit，目标是能复现解码器」，
它会把答案拼成命令跑掉。**「想学什么」不是秘密，可以直接在对话里说**——
这和 API key 不一样，key 只能在你自己的终端里输。

**手动**：

```bash
python3 cli.py profile \
  --topics "量子计算与纠错" \
  --level "有量子力学基础，写过 Qiskit，没做过纠错" \
  --goal "能读懂表面码论文并跑通解码器复现" \
  --minutes 60 \
  --regenerate
```

`--regenerate` 会调一次模型，生成 3–5 个方向、10–30 个条目，然后：

1. **结构校验**（必须过，不过不写盘）：id 唯一、前置存在、**无环**、
   每条都解锁得到、repo/doc 至少 6 条（不然「小任务」那个槽会天天空着）
2. **arXiv 抽查**：一次请求问清所有编号是否真实存在。
   ⚠️ 这一步**不拦写盘**——网络抖动和编造编号在这里长得一样，
   用网络结果否决一份结构完好的课程表会让人在不存在的问题上反复重试
3. 写入 `data/curriculum.json`，并重启面板后生效

生成的课程表是 AI 写的，**建议自己过一眼**，尤其是标题和链接。
之后想手动改就直接编辑 `data/curriculum.json`，格式和内字段自解释。

```bash
python3 cli.py profile              # 看当前画像
python3 cli.py profile --reset      # 删掉生成的，回到内置那份
python3 cli.py check                # 校验当前课程表（含成环/孤立检查）
```

> ⚠️ 换课程表之后，你原来进度里那些**不在新课程表里的条目会保留在状态文件里**，
> 但不再计入进度——换回旧课程表它们就回来了。`cli.py status` 和面板都会
> 「另有 N 条…」地说明这件事，不会闷声把数字改掉。

## 备份 / 换机器

`data/` 是 gitignore 的，所以**重新 clone 一次进度就没了**。要带走就用：

```bash
python3 cli.py export               # 默认 ~/study-planner-backup/study-planner-日期.json
python3 cli.py import <包> --dry-run  # 只看会发生什么，不写
python3 cli.py import <包>            # 恢复（有 TTY 会问一次确认）
```

包里装**三样**：进度、画像、生成的课程表。课程表必须装——新机器上没有它，
只恢复进度会得到一堆指向不存在条目的完成记录。

- **包里不含任何凭据**（有测试保证）。凭据在 `.env` 里，那个不入库也不进包。
- 默认路径在**仓库外面**（`~/study-planner-backup/`）。`data/` 正是重新 clone
  会消失的东西，备份放那儿等于没备份。
- 往仓库里导会被拒绝——这个仓库是公开的，进度和画像是个人数据。
- 导入前会自动把现有文件备份到 `data/backups/<时间戳>/`。
- **导入后必须重启面板**：课程表是进程启动时读的。

## 命令行

```bash
python cli.py today          # 生成并打印今天的计划
python cli.py next           # 预告接下来会推什么
python cli.py done <id>      # 标记完成
python cli.py skip <id>      # 跳过，不再推荐
python cli.py shaky <id>     # 标记「没读懂」，3 天后重新推
python cli.py status         # 看整体进度
python cli.py check          # 课程表自检（成环 / 重复 id / 悬空前置 / 跑不到的孤岛）
python cli.py check-links    # 逐个校验课程表里的 URL 还能不能打开
python cli.py profile        # 看/改画像；--regenerate 按画像生成课程表
python cli.py export [路径]   # 打包进度+画像+课程表（换机器用）
python cli.py import <路径>   # 从包里恢复（--dry-run 只看不动）
python cli.py restart        # 重启网页面板（自动停掉旧的，再前台启动）
```

`<id>` 支持只写前缀，够唯一就行，例如 `python cli.py done rlvla-fpo`。

### 关于启动面板

**改了代码之后必须 `restart`** —— Flask 是以 `debug=False` 起的，不会自动重载模块。

`restart` 做三件事：找到占用 8766 端口的进程 → 停掉（普通 kill 不行就 kill -9）→
启动新的。**顶掉旧面板是它的语义，不用先问。**

| 命令 | 启动方式 |
|---|---|
| `python3 cli.py restart` | **前台**。Ctrl+C 一直有效，人自己用就选这个 |
| `python3 cli.py restart -d` | **后台**，起好就返回。Claude Code 之类帮你启动时用这个 |
| `python3 cli.py stop` | 停掉面板 |

`-d` 起的面板会脱离当前会话，所以 Claude Code 退出、终端关掉都不影响它；
日志写在 `data/panel.log`。它还会**摘掉 Claude Code 注入的 `ANTHROPIC_*`**，
让面板用你自己配的那份凭据（否则 AI 帮你起的面板会偷偷用 Claude Code 的身份，
而体检验的是你配的那份）。人自己敲 `restart` 时不受影响。

如果哪天你用 `&` 把它后台化了、又关掉了终端，进程会变成孤儿。这时手动停：

```bash
lsof -ti tcp:8766 | xargs kill
```

**别用 `pkill -f "python app.py"`** —— macOS 上的 `python3` 是个壳，真进程叫
`Python`（大写 P），按小写的进程名匹配打不中，会以为杀掉了其实没有。按端口找才可靠。

## 网页面板

| 路由 | 内容 |
|---|---|
| `/` | 今日计划，每条可直接勾选「完成 / 没读懂 / 跳过」 |
| `/curriculum` | 课程总览，按方向分组，显示解锁状态 |
| `/progress` | 连续天数、各方向完成度、最近 30 天记录 |
| `/read/<id>` | **论文阅读器**：左栏原文 + 右栏 AI 问答 |
| `/paper/<id>` | 阅读器左栏 iframe 的内容（arXiv 页面的同源代理副本），一般不用手点 |
| `/refresh` | 清掉当日缓存重抓 arXiv / GitHub |

端口固定 `8766`，改 `config.py` 的 `DASHBOARD_PORT` 可以换。

## 论文阅读器

点课程条目的标题（今日计划页或课程总览页都行）会打开阅读器，**新标签页打开**，
这样当天的计划页不会丢。左边是原文，右边可以随时提问。

### 左栏显示什么

三种情况，取决于资源类型：

| 类型 | 左栏 |
|---|---|
| arXiv 论文 | iframe 嵌入 `/paper/<id>`，是 `arxiv.org/html/<id>` 的**同源代理副本** |
| GitHub 仓库 | 服务端抓 README 渲染（GitHub 拒绝被嵌入，只能这样） |
| 其他（HuggingFace 文档等） | 信息卡 + 「在新标签页打开」按钮 |

**为什么用 `/html/` 而不是 `/abs/`**：`abs` 页发了 `frame-ancestors 'none'`，
浏览器会拒绝渲染；`/html/` 没有任何防嵌入头，而且 2015 年的老论文也有。
这个结论是实测出来的，改这块前先自己验证一遍。

**为什么要代理，而不是直接嵌 arxiv.org**：直接嵌是**跨域**的，跨域 iframe 的
`contentDocument` 和 `getSelection()` 一律读不到，也注入不了脚本——
「划词提问」这个功能就无从实现。代理顺便换来两件事：可以隐藏 arXiv 的侧边栏
和横幅做出干净的阅读栏，以及页面缓存到本地后断网也能看正文（图片和样式表
仍在 arXiv 上，离线时公式和文字可读、图片是 alt 文本）。

右上角「原文 ↗」指向真正的 arXiv 页面，作为代理渲染出问题时的逃生口。

### 划词提问

**用鼠标在左边选中任意一段，会浮出一个「问 AI」**，点一下：
引用条出现在输入框上方，输入框预填「这一段是什么意思？」并且**整段处于选中态**——

- 直接回车 = 问这一句
- 直接打字 = 替换成自己的问题

输入框里已经写了一半的内容**不会被覆盖**，只贴引用条。

引用会拼进消息正文（`【选中原文】（位置：3.1 Preliminaries）…【/选中原文】`），
所以它跟着对话历史一起保留，服务端完全不需要知道「引用」这个概念。

**它和上下文档位是正交的**：即使停在默认的「摘要 + 目录」档，选中的那一段
也等同于正文——系统提示词里专门开了这个例外。不然模型会照着「你只看到了摘要」
的指令拒绝回答，而这正是这个功能最常用的场景。想验证的话：停在摘要档选一段
正文提问，应该直接解释；同样的问题不带引用标记再问一次，则应该仍然拒绝给出细节数字。

选中超过 `QUOTE_MAX_CHARS`（默认 4000 字）会截断，**这件事同时告诉你和模型**。

几个实现上的坑，改这块前值得先读：

- **代理页必须注入 `<base href="https://arxiv.org/html/">`**，否则正文里的相对图片
  路径（`2406.09246v3/xxx.png`）会按我们的 origin 解析，全 404。
  base 不能带 id 段——id 已经在 src 里了。
- **`<base>` 会让 425 个文内锚点失效**。`href="#S3"` 是相对 base URL 解析的，
  点一下引用会把 iframe 导航去 `arxiv.org/html/#S3`，注入的脚本随之消失、
  划词功能静默死掉。所以代理页里必须有一个**点击拦截器**成对出现，两者缺一不可。
- **安全边界是 CSP nonce，不是自己写 HTML 清洗器**。`script-src 'nonce-…'`
  一挡，arXiv 自己的脚本和 `on*=` 处理器就都失效了，不需要正则去删 `<script>`
  （正则误伤正文里的 `<` 反而是新 bug 来源）。三个容易写错的地方写在
  `study_planner/proxy.py` 的模块 docstring 里。
- **改了 `_BRIDGE_JS` / `_HIDE_CSS` 必须把 `_PROXY_VERSION` 加一**，
  否则会从磁盘读到旧脚本，在浏览器里刷新半天看不到任何变化。

GitHub 条目的 README 渲染在父页面里，选区就是普通的本页选区，走同一套浮标逻辑；
但白名单只认 `#readmeWrap` 内的选择，所以在右侧聊天区里选字（包括选中 AI 的回答）
不会误触发。

PDF 视图不支持划词——浏览器的 PDF 阅读器是插件，选区无法观测。

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

按这个顺序找，**前一条命中就不再看后面的**：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | 当前环境变量 | 你 `export` 的，或终端 profile 里的。优先级最高，**`.env` 覆盖不了它** |
| 2 | 项目根目录的 `.env` | 推荐。配置脚本写入，权限 600，已被 `.gitignore` 忽略 |
| 3 | `~/.claude/settings.json` 的 `env` 块 | 兜底。依次尝试它、`~/.claude.json`、`<root>/.claude/settings.json` |

配置脚本会一次把该写的键写全，不用手改 `.env`：

```bash
python3 .claude/skills/study-planner-setup/scripts/onboard.py
```

它会问你用哪家模型、key、模型名，**当场发一次最小请求验证**，再写文件。
验证结果分四类报，不会混成一句话：

- **401/403** → key 无效，**不写文件**（免得把原本能用的配置搞坏）
- **404 / 模型名不认识** → 凭据是好的、只是模型名不对，照写并提示改模型名
- **连不上** → 问一次要不要照写
- **429** → 凭据可用，只是被限流

第 2 条为什么要存在：Claude Code 把凭据配在它自己的配置文件里，只注入给
**它自己的进程**，不会传给你手动启动的程序。于是就会出现：

> 在 Claude Code 里让 AI 测着好好的，你自己 `cli.py restart` 一跑就报
> `TypeError: Could not resolve authentication method`

所以本工具启动时会**打印凭据来源**（目前只有 `app.py` 打印，`cli.py` 不打印），
第一眼就能看出对不对：

```
🔑 模型凭据来自：/Users/you/study-planner/.env
```

没找到凭据时也会给出具体该怎么办，而不是等你在浏览器里提问才炸。

#### 两个最常见的坑

**坑一：shell 里 export 过，`.env` 就不生效。** 加载器刻意**不覆盖**已存在的
环境变量（你手动 export 的值优先级最高）。所以「我跑了配置脚本但没变化」，
通常要先 `unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL`，
或者改 shell profile。配置脚本检测到这种情况会主动警告。

**坑二：`.env` 只在进程启动时读一次。** 改完必须 `cli.py restart`——
和「改了代码必须 restart」是同一类坑。

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
├── requirements.txt        # 只有 flask 和 anthropic
├── .env.example            # 凭据模板（入库，无密钥）
├── .env                    # 真正的凭据（gitignored，权限 600）
├── .claude/skills/study-planner-setup/
│   │                       # ★ onboarding skill：装环境、配凭据、起面板
│   ├── SKILL.md            #   给 AI 看的流程
│   ├── reference.md        #   按需查的详细参考
│   └── scripts/onboard.py  #   体检 / 建环境 / 交互式配凭据（只用标准库）
├── data/                   # 全部本机状态，不入库；换机器用 `cli.py export`
│   ├── state.json          # 进度（CLI 和面板共用的唯一数据源）
│   ├── profile.json        # 你的画像：想做哪个方向、每天多少分钟
│   ├── curriculum.json     # 按画像生成的课程表（没有就用内置那份）
│   ├── backups/            # import 覆盖之前的自动备份
│   ├── cache/              # arXiv / GitHub 当日缓存
│   │   └── docs/           # 论文正文缓存（长期有效）
│   │       └── pages/      #   左栏 iframe 用的代理页面（改 proxy.py 后删掉可重生成）
│   └── dead_links.txt      # check-links 的产物
└── study_planner/
    ├── config.py           # 路径常量 + 可调参数（每日时长、抓取条数等）
    ├── knowledge.py        # ★ 内置课程表 + 从文件解析 + 一致性校验
    ├── curriculum.py       # ★ 用户课程表的格式、解析、校验、原子写
    ├── bundle.py           # ★ 导出/导入打包（换机器用）
    ├── progress.py         # 状态模型 + JSON 读写 + 连续天数
    ├── sources.py          # arXiv / GitHub 抓取（只用标准库）
    ├── planner.py          # 选片引擎
    ├── paper.py            # ★ 论文/仓库正文抓取与解析（阅读器用）
    ├── proxy.py            # ★ 把 arXiv 页面改写成能同源嵌入的版本（划词提问靠它）
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
| `QUOTE_MAX_CHARS` | 4000 | 一次划词最多引用多少字，超了截断并告知 |
| `DASHBOARD_PORT` | 8766 | 面板端口 |

⚠️ 有画像时，`ARXIV_KEYWORDS` / `GITHUB_QUERIES` / `ARXIV_CATEGORIES` / `DAILY_MINUTES`
会被 `data/profile.json` 覆盖（空的列表不覆盖）。改这两处之前先确认画像里有没有。

论文正文缓存在 `data/cache/docs/`，内容不会变所以长期有效（不同于按天过期的
抓取缓存）。想强制重抓就删掉这个目录；只重抓左栏的代理页面就删 `docs/pages/`，
或者直接在 iframe 的地址后面加 `?force=1`。

## 断网也能用

`sources.py` 的抓取失败只记日志、不抛异常，计划会退化成纯课程版（只少「新鲜事」
那一块）。**基础功能不依赖网络。**

论文正文也做了本地缓存，所以**断网时已经打开过的论文仍然能看**：文字和公式
（原生 MathML，不靠 JS）正常，图片和 arXiv 的样式表会加载失败，退化成无样式的
可读文本。没缓存过的条目会在左栏显示一张说明页，带「重新加载原文」和 PDF 链接，
而不是一片空白或者浏览器的网络错误页。

GitHub API 未认证时限流较严，可以设 `GITHUB_TOKEN` 提高额度（可选）。
