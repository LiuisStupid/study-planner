# 详细参考

`SKILL.md` 讲的是"怎么走一遍"。这份是查手册用的——出具体问题时再读，
不要整个加载进上下文。

## 凭据从哪里来（优先级从高到低）

`study_planner/llm_plan.py` 的 `_ensure_credentials()` 按这个顺序找：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | **当前环境变量** | 你 export 的，或 shell profile 里的。优先级最高，**`.env` 不会覆盖它** |
| 2 | **项目根目录的 `.env`** | 推荐。`onboard.py` 写入，权限 600，已被 `.gitignore` 忽略 |
| 3 | **`~/.claude/settings.json` 的 `env` 块** | 兜底。依次尝试 `~/.claude/settings.json`、`~/.claude.json`、`<root>/.claude/settings.json` |

**只要第 1 或第 2 条提供了任一凭据键，第 3 条就完全不会被读。** 这是
`_ensure_credentials` 的短路行为，不是 bug。

### 两个最常见的坑

**坑一：shell 里 export 过，`.env` 就不生效。**
加载器（`config.load_env_file`）刻意**不覆盖**已存在的环境变量——你在终端里
export 的值优先级最高。所以「我跑了配置脚本但没变化」通常要先 `unset`：

```bash
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL
```

`onboard.py` 检测到这种情况会主动警告。想永久生效就改 shell profile
（`~/.zshrc` / `~/.bash_profile`）。

**坑二：`.env` 只在进程启动时读一次。**
`app.py` / `cli.py` 都是启动时调一次 `load_env_file()`。改完 `.env` 必须
`cli.py restart`。这和「改了代码必须 restart」是同一类坑。

### 为什么加载点在 `study_planner/__init__.py` 而不是 `config.py`

看起来最自然的位置是 `config.py` 末尾，但那是错的，实测过：

```
$ python -c "from study_planner import llm_plan; import sys; print('config' in sys.modules)"
False
```

`from study_planner import llm_plan` 走的是 `__init__ → llm_plan → knowledge`，
而 `knowledge.py` 只 `import dataclasses`——**config 根本不会被导入**。
偏偏 `llm_plan.py` 顶层的 `MODEL_PLAN = os.environ.get("STUDY_PLANNER_MODEL", ...)`
是模块级求值的，错过这个时机就读不到 `.env` 里配的模型名。

`app.py` / `cli.py` 里恰好能用，只是因为它们把 `config` 写在了 `from ... import (...)`
的第一位。谁把那行顺序调一下就会静默失效。放在包的 `__init__` 里和导入顺序无关。

### 手动配置（不用脚本）

```bash
cp .env.example .env
$EDITOR .env
chmod 600 .env
```

格式是极简的 `KEY=VALUE`：允许 `export ` 前缀和成对引号，`#` 开头是注释。
**不**支持变量展开和转义——规则越少越不容易写错，而这里写错的后果是
「凭据静默失效」，很难查。

## `onboard.py` 的退出码和参数

```
0  就绪（凭据验证通过）        1  环境坏了
2  需要交互式终端              3  要配/重配凭据（没有，或 key 被拒）
4  凭据有效但模型名不对        5  凭据配了，但连不上端点没验成
```

分这么细是因为「下一步做什么」真的不一样：`3` 要把命令交给用户重配，
`4` 只要改一行模型名，`5` 是先别管、照常启动。

| 参数 | 作用 |
|---|---|
| `--check` | 只读体检，并**真发一次请求验证 key** |
| `--check --no-verify` | 只做静态检查，不联网（离线/CI 用） |
| `--install` | 只装依赖（pip 到手边这个 python3），不碰凭据 |
| `--provider {anthropic,deepseek,custom}` | 跳过交互式选择 |
| `--base-url` / `--model` | 同上 |
| `--no-verify` | 不联网验证（离线环境） |
| `--print-only` | 打印将要写入的内容（密钥打码），不写、不联网 |
| `--from-file` | 从文件读 key。**仅供自动化测试**，正常使用别用 |
| `--env-file` | 写到别的路径 |
| `--yes` | 所有确认都答 yes |

`--check` 顺序检查：Python ≥3.9 → `pip` 可用 → 同一解释器里 `import flask, anthropic`
→ 持久化凭据来源并**实际验证** → 面板是否在跑（只报告 PID，**绝不 kill**）。
每行都会把解释器的全路径打出来——「装进去的」和「跑起来的」不是同一个 python
是这类问题里最难查的一种，直接摆出来。

## 不用虚拟环境（这是刻意的）

依赖只有 flask 和 anthropic 两个，不值得为它引入 venv；而 venv 恰恰是换机器时
最容易卡住的一步（Debian/Ubuntu 要 `sudo apt install python3-venv`，AI 没法 sudo，
流程就断在这儿）。所以这个项目直接跑在原生 python3 上。

装依赖一律用 `sys.executable`，即**正在跑脚本的那个解释器**——谁调用就装给谁。
没有"挑解释器"的逻辑，也就不存在"装到了 A、跑起来用的是 B"。

系统 python 被 PEP 668 挡住时（Debian 12 / Ubuntu 23 起，报
`externally-managed-environment`），`--install` 会自动依次退：

| 顺序 | 命令 | 落点 |
|---|---|---|
| 1 | `pip install -r requirements.txt` | 系统站点包 |
| 2 | `pip install --user -r ...` | `~/.local`（macOS 是 `~/Library/Python/3.x`） |
| 3 | `pip install --user --break-system-packages -r ...` | 同上，只是放行 PEP 668 的拦截 |

**任何一步都不用 sudo。** 退到 `--user` 已经足够隔离，也不会碰系统包管理器的地盘；
`sudo pip` 才是真会把系统搞乱的那个。

一个实测到的坑：开发机上 `python3` 可能解析到**另一个仓库的 .venv**
（PATH 里它在前面）。那时依赖会装进那个项目。`--check` 会专门警告这件事，
并且把解释器全路径打出来。

想强制装到某一个特定的 python：直接用那个 python 跑本脚本即可，比如
`/usr/bin/python3 .claude/skills/study-planner-setup/scripts/onboard.py --install`。

### 它验证的是哪一份凭据

**只验证持久化来源里的那份**（`.env` 或 settings.json），不看 `os.environ`——
原因和上面「凭据从哪里来」那条一样：Claude Code 会把**它自己**的凭据注入给子进程，
拿它当判据会误报。而且验证的应该是「用户下次自己起进程时会拿到的那份配置」。

一个它测不到的情况：用户自己的 shell 里也 `export` 了 `ANTHROPIC_*`。
那种情况下真实生效的是 shell 里的（优先级更高），而 `--check` 验的是持久化那份。
所以它可能报通过、实际用的是另一份。这个警告只在交互式配置时能准确给出
（那时用户就在自己的 shell 里），`--check` 里没法判断，会故意保持沉默而不是瞎猜。

**为什么判断依赖用 import 而不是比对版本号**：导入检查已经覆盖实际需求，
逐条解析 `requirements.txt` 的版本下限并和 `importlib.metadata.version` 比对，
是给一个已经满足的场景加 25 行复杂度。真怀疑版本漂移就手动
`python3 -m pip install --upgrade -r requirements.txt`。

## 验证请求是怎么分类的

`onboard.py` 写完配置前会发一次 `max_tokens=16` 的最小请求。四种结果分开处理，
**不合并成一句话**——这个仓库已经吃过一次亏：把「没配置」和「配置了但不兼容」
显示成同一个现象，用户完全不知道该修什么。

| 响应 | 判断 | 行为 |
|---|---|---|
| `200` | 凭据可用 | 写文件 |
| `401` / `403` | key 无效 | **不写文件**（免得把原本能用的配置搞坏），退出 1 |
| `404`，或响应体里提到 model 不认识 | **凭据是好的，模型名不对** | 照写，并大声提示改模型名 |
| `429` | 凭据可用，被限流 | 照写，提示一下 |
| 连不上 | 网络/地址问题 | 默认不写，问一次；`--no-verify` 可跳过 |

第三行是重点：兼容端点上「模型名不认识」是最常见的失败，报成「key 无效」
会让人往完全错误的方向查。

请求头也刻意和 SDK 保持一致——`ANTHROPIC_API_KEY` 走 `x-api-key`，
`ANTHROPIC_AUTH_TOKEN` 走 `Authorization: Bearer`。两个都发就等于验证了一条
程序根本不会走的路径。

## 模型档位

DeepSeek 的 Anthropic 兼容层是按模型名映射的，换个名字就是换档，不用改代码：

| `.env` 里写 | DeepSeek 映射到 | 实测延迟 |
|---|---|---|
| `claude-opus-4-8`（默认） | `deepseek-v4-pro` | ~13s |
| `claude-haiku-4-5` | `deepseek-flash` | ~9s |

官方 Anthropic 就直接用真实模型名。

## 手动启动 / 停止面板

```bash
python3 cli.py restart      # 自动停旧的，再**前台**启动（Ctrl+C 有效）
python3 cli.py restart -d   # 同上但后台启动、起好就返回（AI / 脚本用这个）
python3 cli.py stop         # 停掉
```

两种模式都会先停掉占着 8766 的旧面板（普通 kill 不行就 kill -9），
**这是命令语义本身，不需要额外确认**。

`-d`（`--detach`）做三件事：

1. `start_new_session=True` —— 另起会话，调它的那个 shell 退出时带不走它；
   所以 Claude Code 结束、终端关掉，面板都还在。
2. stdout/stderr 落到 `data/panel.log`（这个目录本来就 gitignore）。
3. **等端口真的监听了才返回**，最多 10 秒；起不来就打印日志路径并报错退出，
   不会假装成功。

### `-d` 会摘掉 AI 注入的凭据，这是刻意的

Claude Code 会把它**自己那份** `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL`
注入给子进程。不摘掉的话，AI 帮忙起的面板会拿 Claude Code 的凭据，而不是用户
配在 `.env` 里的那份——**而 `--check` 验的恰恰是后者**，于是「体检说验过了」和
「实际跑的是谁」就对不上。

所以 `_detached_env()` 在 `CLAUDECODE=1` 时把 `ANTHROPIC_*` 和
`STUDY_PLANNER_MODEL` 摘掉，让面板走用户自己的持久化配置。判断依据是
`CLAUDECODE`：用户自己的终端里不会有它，所以**人自己敲 `restart` 时环境原样保留**，
只影响"由 AI 起面板"这一种情况。

想看面板实际用的是哪份凭据，看日志第一行：`🔑 模型凭据来自：…`。

面板变成孤儿进程时（很久以前用 `&` 后台化、又关了终端的那种）：

```bash
lsof -ti tcp:8766 | xargs kill
```

**别用 `pkill -f "python app.py"`**——macOS 上的 `python3` 是个壳，真进程叫
`Python`（大写 P），按小写的进程名匹配打不中，会以为杀掉了其实没有。
按端口找才可靠。

## 个性化：画像与课程表

| 命令 | 作用 |
|---|---|
| `cli.py profile` | 看当前画像（不写任何东西） |
| `cli.py profile --topics "..." --level "..." --goal "..." --minutes 60` | 写/改画像 |
| `cli.py profile --topics "..." --regenerate` | 改画像 + 调模型生成课程表 |
| `cli.py profile --reset` | 删掉生成的课程表，回到内置那份 |
| `cli.py profile --accept-dead-links` | arXiv 抽查有查不到的编号时也照样写入 |

两个文件，都在 `data/` 下（所以天然 gitignore）：

- `profile.json` —— 你的画像。它是**抓取口味的来源**：`arxiv_keywords` /
  `github_queries` / `arxiv_categories` / `daily_minutes` 会覆盖 `config.py` 里的同名常量。
  **空列表不覆盖**（空关键词会让「新鲜事」整块永久变空，而且不报错）。
- `curriculum.json` —— 按画像生成的课程表。存在就用它，不存在/读坏了就用内置那份，
  页脚和 `cli.py check` 会说清用的是哪份。

### 生成时的校验

**拦写盘**（确定性、离线可判的）：
字段合法性、id 唯一、track 存在、前置存在、**无环**、**每条都从入口可达**
（成环和悬空前置都查不出「孤岛」——一簇条目前置互相引用、谁也没有空前置，
一致、无环、**永远解锁不了**，页面上表现为凭空消失）、
repo/doc ≥ 6（每日计划的「小任务」槽只从这两类里挑）。

**不拦写盘**：arXiv 编号抽查。它是网络调用，而网络抖动和编造编号在这里长得一样——
用网络结果否决一份结构完好的课程表，会让人在一个不存在的问题上反复重试。
抽查还会把真实标题列出来给你自己核（**不能自动比对**：本工具里的标题是中文，
arXiv 返回的是英文）。全量 URL 校验请之后手动跑 `cli.py check-links`。

## 备份 / 换机器

```bash
cli.py export [路径]          # 默认 ~/study-planner-backup/study-planner-日期.json
cli.py import <包> --dry-run  # 只显示会发生什么
cli.py import <包>            # 恢复；有 TTY 会问一次确认
```

包里装**进度 + 画像 + 课程表**三样。课程表必须装：新机器上没有它，只恢复进度
会得到一堆完成记录指向不存在的 id。

- 默认路径在**仓库外面**。`data/` 正是重新 clone 会消失的东西，备份放那儿等于没备份。
- 往仓库里导会被拒绝（要 `--force`），因为这个仓库是公开的。
- **包里没有凭据**——有测试保证，不是靠承诺。
- 导入前自动备份到 `data/backups/<时间戳>/`。
- **没有 TTY 又没有 `--yes` 就退出 2**，只打印命令。理由是「我检查一下」
  不能变成「我把你的进度覆盖了」。
- **导入后必须重启面板**：课程表是进程启动时读的。

## 目录与数据

```
data/state.json      学习进度（CLI 和面板共用的唯一数据源）
data/cache/          arXiv / GitHub 抓取缓存（按天过期）
data/cache/docs/     论文正文缓存（长期有效）
data/cache/docs/pages/   左栏 iframe 用的代理页面（改了 proxy.py 就删掉重生成）
.env                 凭据（gitignored，权限 600）
```

想从头开始就删 `data/`。想强制重抓某篇论文就删 `data/cache/docs/<id>.json`。

## 其它

- **课程表**在 `study_planner/knowledge.py`，改完跑 `cli.py check`（查依赖成环）
  和 `cli.py check-links`（逐个校验 URL）。
- **常用调参**在 `study_planner/config.py`：`DAILY_MINUTES`（每日预算）、
  `REVIEW_AFTER_DAYS`（没读懂的间隔几天重推）、`ARXIV_KEYWORDS`（抓取口味）、
  `FULLTEXT_MAX_CHARS` / `QUOTE_MAX_CHARS`（喂给模型的长度上限）、`DASHBOARD_PORT`。
- **断网也能用**：抓取失败只记日志不抛异常，计划退化成纯课程版。
  已经打开过的论文正文有本地缓存，文字和公式仍可读，图片是 alt 文本。
- **平台**：只在 macOS / Linux 上验证过。`cli.py` 起面板依赖 `lsof` 和 `kill`，
  Windows 原生环境跑不了（WSL 可以）。
- `GITHUB_TOKEN`（可选）可以提高 GitHub API 限额，写在 `.env` 里即可，
  `onboard.py` 不会动它。
