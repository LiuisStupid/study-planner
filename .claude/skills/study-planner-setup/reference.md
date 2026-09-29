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
| `--install` | 只建 venv + 装依赖，不碰凭据 |
| `--provider {anthropic,deepseek,custom}` | 跳过交互式选择 |
| `--base-url` / `--model` | 同上 |
| `--no-verify` | 不联网验证（离线环境） |
| `--print-only` | 打印将要写入的内容（密钥打码），不写、不联网 |
| `--from-file` | 从文件读 key。**仅供自动化测试**，正常使用别用 |
| `--env-file` | 写到别的路径 |
| `--yes` | 所有确认都答 yes |

`--check` 顺序检查：Python ≥3.9 → `.venv` 存在且可执行 → venv 里 `import flask, anthropic`
→ 持久化凭据来源并**实际验证** → 面板是否在跑（只报告 PID，**绝不 kill**）。

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
`.venv/bin/pip install --upgrade -r requirements.txt`。

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
.venv/bin/python cli.py restart      # 自动停旧的再前台启动（Ctrl+C 有效）
```

面板变成孤儿进程时（比如用 `&` 后台化后又关了终端）：

```bash
lsof -ti tcp:8766 | xargs kill
```

**别用 `pkill -f "python app.py"`**——venv 里的 python 实际解析到系统 framework 的
`Python`（大写 P），按进程名匹配打不中，会以为杀掉了其实没有。按端口找才可靠。

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
