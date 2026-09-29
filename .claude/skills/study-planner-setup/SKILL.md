---
name: study-planner-setup
description: 帮新克隆 study-planner 仓库的人搞清这个仓库是干什么的、怎么装依赖、怎么配模型凭据、怎么启动网页面板和日常使用。当用户问「这个仓库是干什么的 / 怎么跑起来 / 怎么启动 / 怎么配置环境 / 缺依赖 / 面板起不来 / AI 点评不可用 / 认证失败 / 换台机器怎么装」时使用。Onboarding, environment setup, dependency install, credential configuration (.env, ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL), and first run for the study-planner repo.
---

# study-planner 上手

## 这个仓库是什么

给**端到端自动驾驶 / 强化学习 / VLA** 三个方向排每日学习计划的本地小工具。
它解决的问题不是「不知道学什么」，而是**没有稳定的每日节奏**：每天从一份带前置依赖的
课程表里挑 1 篇精读 + 1 个小任务，配一个网页面板读论文、划词问 AI。

数据全在本机（`data/`），不上传任何地方。深度说明见仓库根的 `README.md`。

## 三条铁律

这几条是硬性的，违反的代价比"做不完任务"大得多。

**1. 绝不通过对话收集 API key。**
不要 `AskUserQuestion` 问 key，不要让用户把 key 贴在聊天里，不要用 Bash 命令
（`echo $KEY`、`read`、heredoc）去接 key，也不要回显任何看起来像 key 的字符串。
key 一旦出现在对话里，就会被**发送给模型提供方**并写进会话记录文件。
`scripts/onboard.py` 已经设计成"用户在自己的终端里用 `getpass` 输"——把命令交给他就行。

> **⚠️ 这条只管密钥，不管别的信息。** 「你想学什么方向」**不是**秘密，
> 直接在对话里问就行，而且**应该**问——第三步要靠它生成课程表。
> 把这条铁律扩大成"什么都不能问"，会让整个流程走不下去。

**2. 绝不运行 `cli.py today`；启动面板要用 `python3 cli.py restart -d`。**
`today` 会调 `prog.record_day()` + `save()`，覆写 `data/state.json` 里当天的记录——
想看今天的计划就让用户自己跑。

`restart` **可以也应该由你来跑**，但必须带 `-d`：不带 `-d` 是前台启动、永远不返回，
会把你的命令挂到超时。`-d` 起的那个脱离当前会话，Claude Code 退出也带不走它。
它会顶掉占着 8766 的旧面板——**这就是它的语义，是预期行为**，不用先问用户、
也不用先 `lsof` 侦察。用户自己也希望这样：他明确说过「顶掉之前的 port 没关系」。

**3. 绝不用环境变量判断"凭据配好了没"。**
Claude Code 会把**它自己那份** `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL`
注入给子进程（实测 `CLAUDECODE=1` 时这两个都是设好的）。拿 `os.environ` 当判据，
会给一个从没配过任何东西的用户报"已配置好"。
`onboard.py` 只认**持久化来源**（`.env` 文件 或 `~/.claude/settings.json` 的 `env` 块），
直接用它，不要自己写探测。

## 四步走

**第一步：体检 —— 它同时验证 key 能不能真的用。**

```bash
python3 .claude/skills/study-planner-setup/scripts/onboard.py --check
```

只读，不改任何东西，但**会真发一次最小请求验证凭据**（最长等 20 秒）。
这一步就够了：**只要退出码是 `0`，直接跳到第四步启动，不要进第二步。**
用户如果本来就配好了、能跑通，就完全不该看到"配置凭据"这一步。

| 退出码 | 含义 | 下一步 |
|---|---|---|
| `0` | 环境齐 + 凭据**验证通过** | **直接进第四步**，不要再提配置凭据 |
| `1` | 环境坏了（缺 pip 或依赖） | 跑下面的 `--install` |
| `2` | 需要交互式终端 | 把输出里的命令原样交给用户 |
| `3` | 没有凭据，或 key 被端点拒绝 | 进第二步 |
| `4` | 凭据有效，只是模型名不对 | 改 `STUDY_PLANNER_MODEL` 那一行即可，**不要**让用户重配 key |
| `5` | 凭据配了，但这次连不上端点没验成 | 可以照常启动，提醒一句「AI 报错就重跑配置」 |

**只有退出码是 `1` 时**才装依赖（幂等，可以反复跑）：

```bash
python3 .claude/skills/study-planner-setup/scripts/onboard.py --install
```

**这个项目不用虚拟环境。** 依赖（只有 flask 和 anthropic）直接 `pip install`
到跑脚本的那个 `python3` 里，系统 python 被 PEP 668 挡住就自动退到 `--user`。
你**不需要**建 venv，也**不要** `sudo pip`——那既没必要，又会污染系统包管理器
管的地盘。换台机器时这一步不该再卡住。

退出码 `4` / `5` 都不是"凭据有问题"：`4` 是模型名，`5` 多半是网络。
别把它们当成 `3` 去让用户重新粘 key——那会让人白跑一趟。

**第二步：配凭据 —— 把命令交给用户，让他自己跑。**
只有第一步返回 `3` 才走到这里。这一步必须由用户在**自己的终端**里完成，
因为他要交互式输入 key，而 AI 这边没有终端（也不该有，原因见铁律 1）。
把下面这段**原样**给他，并说明为什么：

> 请新开一个终端窗口，粘贴这条命令，按提示填（会问你用哪家模型、key、模型名，
> 输入 key 时不会回显）：
>
> ```bash
> cd <仓库根目录绝对路径>
> python3 .claude/skills/study-planner-setup/scripts/onboard.py
> ```
>
> 脚本会当场发一次最小请求验证 key，然后把配置写进 `.env`（权限 600，已被 gitignore）。
> 不要在这个对话里粘贴 key —— 那会被发送给模型提供方并留在记录里。

**第三步：问方向，按画像生成课程表（可选，但强烈建议做）。**

仓库内置的课程表是一份**示例**（端到端自动驾驶 / RL / VLA）。如果用户不是做这个的，
现在就该问清楚——**直接在对话里问，这不是秘密**：

> 你主要想学什么方向？现在的基础是什么？想达到什么目标？每天大概能投入多少分钟？

拿到答案后拼成一条命令跑（**先告诉他这一下会花他的 API 额度**）：

```bash
python3 cli.py profile \
  --topics "量子计算与纠错" \
  --level "有量子力学基础，写过 Qiskit，没做过纠错" \
  --goal "能读懂表面码论文并跑通解码器复现" \
  --minutes 60 \
  --regenerate
```

它会：结构校验（成环 / 孤立 / 小任务槽够不够）→ arXiv 抽查编号真假 → 写
`data/curriculum.json`。**失败了不会写任何文件**，原来的课程表原封不动。

几点要注意：

- **跳过是合法结局。** 用户说"就用内置的"或者不想花额度，就直接进第四步，
  内置课程表照样能用。不要把它说成必须做的事。
- 报错时把 `cli.py profile` 的原话念给他，别自己改写。
- 生成完提醒他：**这份课程表是 AI 写的，建议自己过一眼**，尤其是链接。
- 用户已经有画像/课程表时（`--check` 会报），别自作主张重新生成。

**第四步：启动。** 环境齐、凭据也验过了，**你来起，别推给用户**：

```bash
python3 cli.py restart -d     # 后台启动，起好就返回 → http://127.0.0.1:8766
```

`-d` 会后台启动并确认端口真的起来了才返回，同时打印 PID 和日志路径。
它自动停掉占着 8766 的旧面板——**这是预期行为，不用问**。
`.env` 只在进程启动时读一次，所以**改完凭据必须 restart 才生效**。

起好之后告诉用户地址和 PID 就行，不要让他再把同一条命令敲一遍。

## 日常使用

| 命令 | 作用 |
|---|---|
| `python3 cli.py today` | 今天的计划（精读 / 小任务 / 复习 / 新鲜事） |
| `python3 cli.py next` | 预告接下来推什么 |
| `python3 cli.py done <id>` | 标记完成（`<id>` 支持只写前缀） |
| `python3 cli.py shaky <id>` | 标记「没读懂」，3 天后重推 |
| `python3 cli.py status` | 总体进度 + 连续天数 |
| `python3 cli.py restart` | 起/重启网页面板（8766），前台；加 `-d` 则后台 |
| `python3 cli.py stop` | 停掉网页面板 |
| `python3 cli.py check` | 课程表自检（成环 / 重复 id / 孤立条目） |
| `python3 cli.py profile` | 看/改画像，`--regenerate` 生成自己的课程表 |
| `python3 cli.py export` | 打包进度+画像+课程表（换机器用） |

用 `python3` 而不是 `python`：macOS 上 `python` 可能根本不存在。

面板里 `/read/<id>` 是**论文阅读器**：左边原文，右边问 AI。
**用鼠标选中左边任意一段会浮出「问 AI」**，点一下就能就着那段话提问。
右上角「原文 ↗」指向真正的 arXiv 页面。

AI 功能（每日点评 + 阅读器问答）是可选的——没配凭据也能用，只是少了这两块。

## 出问题了

| 现象 | 先看这里 |
|---|---|
| `Could not resolve authentication method` | `.env` 没生效：是不是 shell 里也 export 了同名变量（环境变量优先）？改完 restart 了吗？ |
| 面板起来了但点评是模板文字 | 没配凭据，或者 key 不对。跑 `onboard.py` 重配一次 |
| 端口 8766 被占 | 不用管——`cli.py restart -d` 会自己顶掉旧的。只想停不想起用 `cli.py stop` |
| `restart` 跑起来不返回 | 漏了 `-d`。前台模式是给人的，AI 这边必须 `--detach` |
| 依赖装不上 | 看 `--install` 的 pip 输出。没有 pip 就先 `apt install python3-pip`（Debian/Ubuntu）；**不要**退回建 venv，也不用 sudo pip |
| 装完还是 import 不到 | 装进去的和你跑的不是同一个 python3。`onboard.py --check` 会把解释器全路径打出来，对着看 |
| 论文左栏空白 | 断网或 arXiv 没有 HTML 版；左栏会显示说明页，不是白屏 |
| 改了 `.env` 但没变化 | 它在进程启动时读一次，必须 restart |

更细的对照表、凭据优先级矩阵、手动配置方式和调参说明在 `reference.md`，
需要时再读，不要一次全加载。

## 不要做的事

- 不要往 `~/.claude/settings.json` 写凭据——那是 Claude Code **自己**的配置，
  写坏会连带影响用户的 Claude Code。（工具会自动读它，但只读不写。）
- 不要直接编辑 `.env`，交给 `onboard.py`，它会保留文件里的其它内容（比如 `GITHUB_TOKEN`）。
- 不要在输出里回显 key 的任何完整形式。
- 不要在用户没要求时跑 `pip install`——`--check` 说没问题就别动。
- **不要建 venv，不要 `sudo pip install`。** 这个项目刻意跑在原生 python3 上，
  理由见上面第一步那段。
- **不要跑 `cli.py import`**：它会覆盖用户现有的进度。要导入让他自己来。
- **不要在没告诉用户会花额度的情况下跑 `cli.py profile --regenerate`**。
- 也不要自己编辑 `data/curriculum.json` 或 `data/profile.json`——走 `cli.py profile`。
