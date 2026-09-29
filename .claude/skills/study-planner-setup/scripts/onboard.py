#!/usr/bin/env python3
"""让这个仓库在你的机器上跑起来：装依赖、配凭据、验证。

    python3 onboard.py --check     只读体检 + 验证凭据，不改任何东西（退出码见下）
    python3 onboard.py --install   只装依赖（pip 到手边这个 python3），不碰凭据
    python3 onboard.py             交互式：环境 + 凭据一次做完

退出码（给自动化用的，`--check` 靠它表态）：
    0  就绪：环境齐、凭据**验证通过**，直接启动即可
    1  环境坏了（Python 版本不够、pip 用不了、依赖装不上）
    2  需要交互式终端才能继续（本脚本没有被 TTY 连着）
    3  需要配或重配凭据（没有凭据，或 key 被端点拒绝）
    4  凭据有效，但模型名不被接受（改 STUDY_PLANNER_MODEL 即可，不用重配 key）
    5  凭据配了，但这次连不上端点，无法验证（可能是断网）

**`--check` 默认会真发一次最小请求验证 key**，不是只看"有没有配置"。
「配了但配错了」和「没配」在面板里表现得一模一样（AI 功能不工作），
只有真发一次请求才分得出来。断网或不想联网时加 `--no-verify` 退回只看配置。

## 三条设计约束，改这个文件前先读

**一、凭据只能由用户在自己的终端里输入。**
API key 一旦经过对话（哪怕只是被打印出来），就会被发送给模型提供方并落进
会话记录文件。所以本脚本**从不**提供"从 stdin/参数读 key"的入口——那正是
把 key 灌进对话的管子。没有 TTY 时直接退出（码 2），把该跑的命令原样打印出来。

**二、判断"凭据配好了没"只能看持久化的来源，不能看 os.environ。**
Claude Code 会把自己那份 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL 注入给它
启动的子进程。拿环境变量当判据，会给一个从没配过任何东西的用户报"已配置好"——
这是实测踩出来的。

**三、不建虚拟环境，就用手边这个 python3。**
这个项目的依赖只有 flask 和 anthropic 两个，不值得为它引入 venv——
而 venv 恰恰是换机器时最容易卡住的一步（Debian/Ubuntu 上要
`sudo apt install python3-venv`，AI 没法 sudo，整个流程就断在这里）。

所以装依赖一律用 `sys.executable`，即**正在跑本脚本的那个解释器**：
你在系统 python3 下跑就装进系统 python3，你自己在 venv 里跑就装进那个 venv。
没有"挑解释器"的逻辑，也就不存在"装到了 A、跑起来用的是 B"这种最难查的问题。

**绝不 sudo。** 系统 python 被 PEP 668 挡住时退到 `--user` 装进 `~/.local`
（见 PIP_ATTEMPTS），那已经足够隔离，也不碰系统包管理器管的地盘。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 装完这两个才算环境就绪。和 requirements.txt 保持对应，但**不解析版本号**：
# 导入检查已经覆盖实际需求，逐条比对版本下限是给一个已经满足的场景加复杂度。
REQUIRED_IMPORTS = ("flask", "anthropic")

# 配置文件里由本脚本托管的键。其余内容（注释、GITHUB_TOKEN 之类）原样保留。
MANAGED_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "STUDY_PLANNER_MODEL",
)

# 只认这两个算"有凭据"。单有一个 ANTHROPIC_BASE_URL 不算。
CRED_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# 和 llm_plan._CLAUDE_SETTINGS_CANDIDATES 保持一致。写成函数而不是模块常量，
# 这样测试里把 HOME 指到别处也能生效。
def _settings_candidates(root: Path):
    return (
        Path.home() / ".claude" / "settings.json",
        Path.home() / ".claude.json",
        root / ".claude" / "settings.json",
    )


DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"
DEEPSEEK_BASE = "https://api.deepseek.com/anthropic"

PROVIDERS = {
    "anthropic": {
        "label": "官方 Anthropic",
        "base": "",
        "model": "claude-opus-4-8",
        "key_env": "ANTHROPIC_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek（Anthropic 兼容端点）",
        "base": DEEPSEEK_BASE,
        "model": "claude-haiku-4-5",
        "key_env": "ANTHROPIC_AUTH_TOKEN",
    },
    "custom": {
        "label": "其它 Anthropic 兼容端点",
        "base": "",
        "model": "claude-haiku-4-5",
        "key_env": "ANTHROPIC_AUTH_TOKEN",
    },
}

# 退出码。分这么细是因为"下一步做什么"真的不一样：
# 3 要把命令交给用户重配，4 只要改一行模型名，5 是先别管、照常启动。
OK, BAD_ENV, NO_TTY, NEEDS_CRED, BAD_MODEL, UNVERIFIED = 0, 1, 2, 3, 4, 5

# 和 llm_plan.MODEL_PLAN 的默认值保持一致。这里不 import 那个模块：
# 本脚本要在还没装依赖的环境里也能跑。
DEFAULT_MODEL = "claude-opus-4-8"


# ---------------------------------------------------------------------------
# 定位项目根
# ---------------------------------------------------------------------------
def find_root() -> Path:
    """从本文件往上找同时含 requirements.txt 和 cli.py 的目录。

    不作为 `.git` 判断：脚本可能被单独复制出去。要求两个文件同时存在，
    是为了避免匹配到别处一个孤零零的 requirements.txt。
    """
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "requirements.txt").is_file() and (parent / "cli.py").is_file():
            return parent
    raise SystemExit(
        "❌ 找不到项目根目录。\n"
        "   本脚本要放在 <项目根>/.claude/skills/study-planner-setup/scripts/ 下面。"
    )


# ---------------------------------------------------------------------------
# 读配置（解析规则和 study_planner/config.py 的 load_env_file 保持一致）
# ---------------------------------------------------------------------------
def parse_env_text(text: str) -> dict:
    """解析 .env 文本。只做最小集，不做变量展开——规则越多越容易写错，
    而这里写错的后果是「凭据静默失效」，很难查。"""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and value:
            out[key] = value
    return out


def read_env_file(path: Path) -> dict:
    try:
        return parse_env_text(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def settings_env_block(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    env = data.get("env")
    return env if isinstance(env, dict) else {}


def persisted_credential_source(root: Path):
    """只在**持久化**的地方找凭据，绝不看 os.environ。

    Claude Code 会把自己那份凭据注入给子进程（实测 CLAUDECODE=1 时
    ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL 都是设好的）。用它当判据，
    会给一个从没配过任何东西的用户报「已配置」。
    """
    env_path = root / ".env"
    vals = read_env_file(env_path)
    if any(vals.get(k) for k in CRED_KEYS):
        return str(env_path)
    for path in _settings_candidates(root):
        block = settings_env_block(path)
        if any(block.get(k) for k in CRED_KEYS):
            return str(path)
    return None


def effective_credentials(src: Path):
    """从持久化来源里读出真正会被用到的 key / 端点 / 模型。没有 key 就返回 None。

    **只读持久化来源，不看 os.environ**，理由同 persisted_credential_source。
    验证的是「用户下次自己起进程时会拿到的那份配置」，不是 Claude Code
    注入给子进程的那份——后者是 Claude Code 自己的，跟这个仓库无关。
    """
    vals = settings_env_block(src) if src.suffix == ".json" else read_env_file(src)
    key_name = next((k for k in CRED_KEYS if vals.get(k)), "")
    if not key_name:
        return None
    base = vals.get("ANTHROPIC_BASE_URL", "")
    return {
        "key": vals[key_name],
        "key_name": key_name,
        # 配了 base_url 就是在走兼容端点（Bearer），否则是官方（x-api-key）。
        # 这个判断要和 SDK 的行为一致，否则验证的是一条程序不会走的路径。
        "use_bearer": bool(base),
        "base_url": base,
        "model": vals.get("STUDY_PLANNER_MODEL") or DEFAULT_MODEL,
    }


# ---------------------------------------------------------------------------
# 环境检查 / 安装
# ---------------------------------------------------------------------------
def project_python() -> Path:
    """这个项目会用哪个解释器：正在跑本脚本的这个。

    刻意不建 venv——用手边这个 python3 就行（理由见文件头的约束三）。
    谁跑本脚本就用谁装，所以在 venv 里跑 `onboard.py` 就等于装进那个 venv，
    不存在"装到了 A、跑起来用的是 B"。
    """
    return Path(sys.executable)


def foreign_venv(root: Path) -> str:
    """当前解释器如果来自**别的**虚拟环境，返回那个环境的路径，否则空串。

    不是错，但必须说一声：接着装的依赖会落进那个项目的地盘。这个警告是
    实测出来的——开发机上 `python3` 就解析到另一个仓库的 .venv 上，
    不点破的话「我明明装过了」和「怎么装到那边去了」会一起发生。
    """
    if sys.prefix == sys.base_prefix:       # 系统 python，没在 venv 里
        return ""
    if Path(sys.prefix).resolve() == (root / ".venv").resolve():
        return ""                            # 本仓库自己的 venv，正常
    return sys.prefix


def python_cmd(root: Path) -> str:
    """写给用户看的命令里该写哪个解释器。

    多数情况下就是 `python3`。唯独直接敲 `.venv/bin/python onboard.py` 而没
    activate 的时候，`python3` 会解析到别处，所以那时把 venv 的路径打全。
    """
    if sys.prefix != sys.base_prefix:      # 当前就在某个 venv 里
        return rel(root, project_python())
    return "python3"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def check_python() -> tuple:
    """返回 (解释器路径, 版本串, 是否 >= 3.9)。"""
    exe = sys.executable
    info = sys.version_info
    ver = f"{info.major}.{info.minor}.{info.micro}"
    return exe, ver, info >= (3, 9)


def deps_installed(py: Path) -> bool:
    """靠 import 判断，而不是读 requirements.txt 比对版本。"""
    code = "import " + ", ".join(REQUIRED_IMPORTS)
    return py.is_file() and _run([str(py), "-c", code]).returncode == 0


# 装依赖的候选姿势，**顺序有意义**：先试最干净的，被系统挡回来再一步步退。
# 退回 --user 只写 ~/.local，不碰系统包管理器管的地盘；仍然**不用 sudo**。
PIP_ATTEMPTS = (
    ("直接装", ("install",)),
    ("装到用户目录（--user）", ("install", "--user")),
    ("装到用户目录 + 放行外部管理环境（PEP 668，Debian 12 / Ubuntu 23 起）",
     ("install", "--user", "--break-system-packages")),
)


def ensure_pip(py: Path, say=print) -> bool:
    """没 pip 就先弄一个出来。仍然不 sudo。

    Debian 系把 ensurepip 的上游行为改掉了，可能直接失败——那时只能告诉
    用户自己装，报清楚比瞎试强。
    """
    if _run([str(py), "-m", "pip", "--version"]).returncode == 0:
        return True
    say("  → 这个 python 没有 pip，试着用 ensurepip 引导一个…")
    for flags in (("--upgrade",), ("--upgrade", "--user")):
        if _run([str(py), "-m", "ensurepip", *flags]).returncode == 0:
            if _run([str(py), "-m", "pip", "--version"]).returncode == 0:
                return True
    say("  ❌ 引导 pip 失败。手动装一个再回来跑本脚本：")
    say("       Debian / Ubuntu   sudo apt install python3-pip")
    say("       Fedora            sudo dnf install python3-pip")
    say(f"       或者直接给这个解释器装：{py} -m ensurepip --upgrade")
    return False


def ensure_deps(root: Path, py: Path, say=print) -> bool:
    """把 requirements.txt 装进 `py`。幂等：已经齐了就什么都不做。"""
    if deps_installed(py):
        say("  → 依赖已就绪，跳过安装（本脚本可以反复跑）")
        return True

    other = foreign_venv(root)
    if other:
        say(f"  ⚠️ 这个解释器来自别的虚拟环境：{other}")
        say("     依赖会装进那里，不动系统 python。")
        say("     想装到系统 python：先 deactivate，再重跑本脚本。")

    req = str(root / "requirements.txt")
    for label, flags in PIP_ATTEMPTS:
        say(f"  → 装依赖：{label} …")
        r = subprocess.run([str(py), "-m", "pip", *flags, "-r", req])
        if r.returncode == 0 and deps_installed(py):
            return True

    say("  ❌ 依赖还是装不上，上面是 pip 的输出。")
    if (root / ".venv").is_dir():
        say("     仓库里有个旧的 .venv —— 现在不再用它了，依赖是往上面这个")
        say("     解释器里装的。那个目录可以直接删掉。")
    return False


def listening_pids(port: int):
    """面板是不是已经在跑。只报告，**绝不 kill**——那可能是用户正在用的窗口。"""
    try:
        r = _run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"])
    except OSError:
        return []
    return [p for p in r.stdout.split() if p.strip()]


# ---------------------------------------------------------------------------
# 凭据验证：一次最小请求
# ---------------------------------------------------------------------------
def redact(key: str) -> str:
    if not key:
        return "(空)"
    if len(key) <= 16:
        return "***"
    return f"{key[:6]}…{key[-4:]}"


def verify(base_url: str, key: str, model: str, use_bearer: bool, timeout: float = 20.0):
    """发一次 max_tokens=16 的请求，返回 (kind, status, body)。

    用 urllib 而不是 anthropic SDK：脚本可能被系统 python3 跑（还没装依赖），
    而且这样能在**写文件之前**独立验证。

    请求头的选择要跟 SDK 一致：ANTHROPIC_API_KEY 走 x-api-key，
    ANTHROPIC_AUTH_TOKEN 走 Authorization: Bearer。两个都发就等于验证了一条
    程序根本不会走的路径。
    """
    base = (base_url or DEFAULT_ANTHROPIC_BASE).rstrip("/")
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if use_bearer:
        headers["authorization"] = f"Bearer {key}"
    else:
        headers["x-api-key"] = key

    payload = json.dumps({
        "model": model,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "reply with OK"}],
    }).encode("utf-8")

    req = urllib.request.Request(base + "/v1/messages", data=payload,
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return "ok", resp.status, resp.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return "http", e.code, e.read(2000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return "net", 0, str(e)


def classify(kind: str, status: int, body: str) -> str:
    """把响应归到四类之一。**不要把它们合并成一句话**——这个仓库已经吃过一次
    亏：把「没配置」和「配置了但不兼容」显示成同一个现象，用户完全不知道该修什么。

    返回：ok | bad_key | bad_model | throttled | net | other
    """
    if kind == "ok":
        return "ok"
    if kind == "net":
        return "net"
    if status in (401, 403):
        return "bad_key"
    if status == 429:
        return "throttled"
    low = (body or "").lower()
    mentions_model = "model" in low and any(
        s in low for s in ("not found", "unknown", "does not exist", "invalid")
    )
    if status == 404 or mentions_model:
        # 兼容端点上最常见的一种失败：key 是对的，模型名不认识。
        # 报成「key 无效」会让人往完全错误的方向查。
        return "bad_model"
    return "other"


# ---------------------------------------------------------------------------
# 写 .env
# ---------------------------------------------------------------------------
def _quote(value: str) -> str:
    if any(c in value for c in ' \t#"\'') and not (value.startswith('"') and value.endswith('"')):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def merge_env_text(existing: str, pairs) -> str:
    """保留所有非托管行（注释、空行、GITHUB_TOKEN…），只替换托管的键。

    托管键统一挪到文件末尾，所以反复运行不会攒出第二个同名键。
    """
    kept = []
    for line in existing.splitlines():
        s = line.strip()
        key = ""
        if "=" in s:
            candidate = s[len("export "):].lstrip() if s.startswith("export ") else s
            key = candidate.partition("=")[0].strip()
        if key in MANAGED_KEYS:
            continue
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()

    block = ["", "# --- 由 onboard.py 生成，可重复运行 ---"]
    block += [f"{k}={_quote(v)}" for k, v in pairs]
    return "\n".join(kept + block) + "\n"


def write_env_file(path: Path, text: str) -> None:
    # 先写临时文件再原子替换：中途失败不会留下半截 .env。
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    # 文件已存在时 O_CREAT 的 mode 不生效，必须补一次 chmod
    os.chmod(path, 0o600)


# ---------------------------------------------------------------------------
# 体检
# ---------------------------------------------------------------------------
def do_check(root: Path, do_verify: bool = True) -> int:
    print("🔍 体检（只读，不会改动任何东西）\n")
    problems = []
    cred_code = OK

    exe, ver, ok_py = check_python()
    print(f"  Python      {ver} {'✅' if ok_py else '❌ 需要 ≥ 3.9'}  {exe}")
    if not ok_py:
        problems.append("python")

    py = project_python()
    has_pip = _run([str(py), "-m", "pip", "--version"]).returncode == 0
    print(f"  pip         {'✅' if has_pip else '❌ 没有 pip'}")
    if not has_pip:
        problems.append("pip")

    has_deps = deps_installed(py)
    print(f"  依赖        {'✅ flask / anthropic 都能 import' if has_deps else '❌ 缺依赖（需要装 requirements.txt）'}")
    if not has_deps:
        problems.append("deps")
        # 这个仓库以前用 .venv，旧用户会看着一个装满依赖的 .venv 发懵：
        # 「明明装过」。点破它，否则下一步会去查错方向。
        if (root / ".venv").is_dir():
            print("              （仓库里的 .venv 已经不用了，依赖装在上面这个 python3 里）")

    other = foreign_venv(root)
    if other:
        print(f"  ⚠️ 注意     当前 python3 来自**别的**虚拟环境：{other}")
        print("             依赖会被装进它。想装到系统 python 就先 deactivate 再跑。")

    # --- 凭据：不只是"有没有配"，而是"能不能跑通" ---
    src = persisted_credential_source(root)
    if not src:
        print("  模型凭据    ❌ 没有找到（面板能开，但 AI 点评和右栏问答不可用）")
        cred_code = NEEDS_CRED
    else:
        creds = effective_credentials(Path(src))
        if creds is None:
            print(f"  模型凭据    ❌ {src} 里没有可用的 key")
            cred_code = NEEDS_CRED
        elif not do_verify:
            print(f"  模型凭据    ✅ 来自 {src}（--no-verify，没有实际验证）")
        else:
            where = creds["base_url"] or DEFAULT_ANTHROPIC_BASE
            print(f"  模型凭据    来自 {src}，验证中…", flush=True)
            kind, status, body = verify(creds["base_url"], creds["key"],
                                        creds["model"], creds["use_bearer"])
            verdict = classify(kind, status, body)
            # 端点有可能把 key 回显在错误信息里，打印前先抹掉
            safe = (body or "").replace(creds["key"], "***")[:200]
            if verdict == "ok":
                print(f"  模型凭据    ✅ 验证通过（{where} · {creds['model']}）")
            elif verdict == "throttled":
                print(f"  模型凭据    ✅ key 可用，只是被限流（HTTP 429）")
            elif verdict == "bad_model":
                print(f"  模型凭据    ⚠️ key 有效，但模型名 {creds['model']} 不被接受")
                print(f"              {safe}")
                cred_code = BAD_MODEL
            elif verdict == "bad_key":
                print(f"  模型凭据    ❌ key 被拒绝（HTTP {status}）")
                print(f"              {safe}")
                cred_code = NEEDS_CRED
            elif verdict == "net":
                print(f"  模型凭据    ⚠️ 连不上 {where}，这次没法验证")
                print(f"              {safe}")
                cred_code = UNVERIFIED
            else:
                print(f"  模型凭据    ⚠️ 端点返回意料之外的结果（HTTP {status}）")
                print(f"              {safe}")
                cred_code = UNVERIFIED

    pids = listening_pids(8766)
    print(f"  面板        {'✅ 正在运行，PID ' + ' '.join(pids) if pids else '— 没在运行'}")

    print()
    if problems:
        print("👉 环境还没就绪。跑这一条把它建起来（幂等，可以反复跑）：")
        # 用 python_cmd 而不是写死 python3：如果是用别的解释器跑的本脚本，
        # 写死 `python3` 会指向另一个 python，装完还是找不到依赖。
        print(f"     {python_cmd(root)} {rel(root, Path(__file__))} --install")
        return BAD_ENV

    if cred_code == NEEDS_CRED:
        print("👉 还没有可用的模型凭据（或者 key 被拒了）。")
        print("   **请在你自己的终端里**跑，按提示填：")
        print()
        print(f"     cd {root}")
        print(f"     {python_cmd(root)} {rel(root, Path(__file__))}")
        print()
        print("   （问三个问题，写进 .env，权限 600。")
        print("     不要在 AI 对话里粘贴 key——那会被发送给模型提供方并留在记录里。）")
        return NEEDS_CRED

    if cred_code == BAD_MODEL:
        print(f"👉 凭据是好的，只是模型名不对。改这一行就行，不用重配 key：")
        print(f"     {src}  →  STUDY_PLANNER_MODEL=<换个名字>")
        print("   或者重跑一次并指定：... onboard.py --model <名字>")
        return BAD_MODEL

    if cred_code == UNVERIFIED:
        print("👉 凭据配了，但这次没能连上端点验证（多半是网络）。")
        print("   可以照常启动；如果面板里 AI 报认证错误，再跑一次 onboard.py 重配。")
        print("   想跳过验证只做静态检查：... onboard.py --check --no-verify")
        return UNVERIFIED

    print("✅ 一切就绪（环境 + 凭据都验过了）。启动面板：")
    # 带 -d：这段输出主要是被 AI 读走的，前台模式会把它挂到超时。
    # 它自带"停掉旧的再起"，所以已经在跑也直接跑这条，不用先问。
    print(f"     {python_cmd(root)} cli.py restart -d")
    return OK


def rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# 交互式配置
# ---------------------------------------------------------------------------
NO_TTY_TEXT = """\
这个脚本需要**在你自己的终端**里运行（它要交互式读取 key，不回显）。

在 AI 对话里跑不了，也不该跑：那边没有终端，而且任何出现在对话里的 key
都会被发送给模型提供方、并写进会话记录文件。

请新开一个终端窗口，执行：

    cd {root}
    {python} {script}

（会问你三个问题，然后把配置写进 {env}，权限 600。）
"""


def do_configure(root: Path, args) -> int:
    env_path = Path(args.env_file).resolve() if args.env_file else root / ".env"
    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    if not interactive and not args.from_file and not args.print_only:
        print(NO_TTY_TEXT.format(
            root=root, python=python_cmd(root),
            script=rel(root, Path(__file__)), env=rel(root, env_path)))
        return NO_TTY

    existing = read_env_file(env_path)

    # ---- 收集三个问题的答案 ----
    provider = args.provider
    base_url = args.base_url
    model = args.model
    key = None

    if args.from_file:
        key = Path(args.from_file).read_text(encoding="utf-8").strip()
    elif interactive:
        if existing:
            cur = existing.get("ANTHROPIC_BASE_URL") or DEFAULT_ANTHROPIC_BASE
            print(f"当前 .env：{rel(root, env_path)}")
            cur_key = (existing.get("ANTHROPIC_API_KEY")
                       or existing.get("ANTHROPIC_AUTH_TOKEN", ""))
            print(f"  端点  {cur}")
            print(f"  模型  {existing.get('STUDY_PLANNER_MODEL', '(默认)')}")
            print(f"  密钥  {redact(cur_key)}")
            if not args.yes and input("重新配置？[y/N] ").strip().lower() not in ("y", "yes"):
                print("未做任何修改。")
                return OK
            print()

        if not provider:
            print("用哪家的模型？")
            for i, (pid, p) in enumerate(PROVIDERS.items(), 1):
                print(f"  {i}) {p['label']}")
            choice = input("选一个 [1]: ").strip() or "1"
            provider = list(PROVIDERS)[int(choice) - 1] if choice.isdigit() else choice
        spec = PROVIDERS.get(provider, PROVIDERS["custom"])

        if provider == "custom" and not base_url:
            base_url = input(f"兼容端点地址（形如 {DEEPSEEK_BASE}）: ").strip()

        key = getpass.getpass("粘贴 API key（输入不回显）: ")
        key = key.strip().strip('"').strip("'")
        if not key:
            print("❌ 没有输入 key，未做任何修改。")
            return NEEDS_CRED   # 凭据问题，不是环境问题

        if not model:
            default_model = spec["model"]
            got = input(f"模型名 [{default_model}]: ").strip()
            model = got or default_model

    provider = provider or "deepseek"
    spec = PROVIDERS.get(provider, PROVIDERS["custom"])
    base_url = base_url if base_url is not None else spec["base"]
    model = model or spec["model"]

    if not key:
        print("❌ 没有拿到 key（--from-file 没读到内容？），未做任何修改。")
        return NEEDS_CRED

    use_bearer = provider != "anthropic"
    display_base = base_url or DEFAULT_ANTHROPIC_BASE

    print()
    print(f"  服务商  {spec['label']}")
    print(f"  端点    {display_base}")
    print(f"  模型    {model}")
    print(f"  密钥    {redact(key)}")
    print()

    # ---- 验证 ----
    # --print-only 是"给我看看会写成什么样"的干跑模式，不发任何网络请求：
    # 它不会写文件，验证结果也没有用处，白白让人等 20 秒。
    verdict = "skipped"
    if not args.no_verify and not args.print_only:
        print("  → 发一次最小请求验证…（最长等 20 秒）")
        kind, status, body = verify(base_url, key, model, use_bearer)
        verdict = classify(kind, status, body)
        body_clean = (body or "").replace(key, "***")[:300]

        if verdict == "ok":
            print("  ✅ 验证通过。")
        elif verdict == "bad_key":
            print(f"  ❌ 密钥无效（HTTP {status}）。没有写入任何文件。")
            print(f"     {body_clean}")
            print("     检查一下是不是粘错了，或者用的是不是这家的 key。")
            # 环境本身没问题，是凭据没配成——退回 1（环境坏了）会把调用方
            # 引去重装依赖，方向完全错。
            return NEEDS_CRED
        elif verdict == "bad_model":
            print(f"  ⚠️ 密钥是好的，但模型名不被接受（HTTP {status}）。")
            print(f"     {body_clean}")
            print(f"     换一个模型名：--model <名字>，或改用 {spec['model']}。")
            print("     配置照常写入，改模型名不用重跑本脚本。")
        elif verdict == "throttled":
            print(f"  ⚠️ 密钥可用，但被限流了（HTTP 429）。配置照常写入。")
        elif verdict == "net":
            print(f"  ⚠️ 没能连上 {display_base}：{body_clean}")
            if not args.yes:
                if input("  仍然写入配置吗？[y/N] ").strip().lower() not in ("y", "yes"):
                    print("未做任何修改。")
                    return NEEDS_CRED
        else:
            print(f"  ⚠️ 端点返回了意料之外的结果（HTTP {status}）：{body_clean}")
            if not args.yes:
                if input("  仍然写入配置吗？[y/N] ").strip().lower() not in ("y", "yes"):
                    print("未做任何修改。")
                    return NEEDS_CRED
    elif args.print_only:
        print("  → 干跑模式，不联网验证。")
    else:
        print("  → --no-verify，跳过验证。")

    # ---- 写 .env ----
    # 官方 Anthropic 就**不能**留 ANTHROPIC_BASE_URL：显式空值和一个不存在的键，
    # 在 SDK 和加载器里行为不同，而且「A 家的 key 配 B 家的地址」会 401 且看不出原因。
    pairs = []
    if provider == "anthropic":
        pairs.append(("ANTHROPIC_API_KEY", key))
    else:
        pairs.append(("ANTHROPIC_AUTH_TOKEN", key))
        pairs.append(("ANTHROPIC_BASE_URL", base_url))
    pairs.append(("STUDY_PLANNER_MODEL", model))

    try:
        before = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    except (OSError, ValueError):
        before = ""
    merged = merge_env_text(before, pairs)

    if args.print_only:
        print("\n--print-only：将要写入的内容如下（**没有真的写**）\n")
        # 预览里也要打码。这个脚本对外的硬性保证是「从不打印 key」——
        # 一旦有例外，那条保证就没法靠读代码确认了。
        # 预览的作用是看清文件长什么样，真值在这里没有信息量。
        print(merged.replace(key, redact(key)))
        print("（密钥已打码；真正写入的是完整值）")
        return OK

    write_env_file(env_path, merged)
    print(f"  ✅ 已写入 {env_path}（权限 600，已被 .gitignore 忽略）")

    # ---- 收尾提醒 ----
    stale = [k for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
             if os.environ.get(k)]
    if stale:
        print()
        print(f"  ⚠️ 你的 shell 里已经 export 了 {'、'.join(stale)}。")
        print("     环境变量优先级高于 .env，所以上面这份配置**不会生效**。")
        print("     先 unset 掉（或从 shell 配置里删掉），再重启面板。")

    print()
    print("下一步：")
    print(f"     {python_cmd(root)} cli.py restart     # 重启面板，.env 只在启动时读一次")
    return OK


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="把 study-planner 跑起来：装依赖、配凭据。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 就绪 / 1 环境坏了 / 2 需要终端 / 3 要配凭据 / "
               "4 模型名不对 / 5 端点连不上没验成",
    )
    ap.add_argument("--check", action="store_true",
                    help="只读体检，并真发一次请求验证 key")
    ap.add_argument("--install", action="store_true", help="只装依赖，不碰凭据")
    ap.add_argument("--provider", choices=list(PROVIDERS), help="服务商")
    ap.add_argument("--base-url", dest="base_url", help="兼容端点地址")
    ap.add_argument("--model", help="模型名")
    ap.add_argument("--env-file", dest="env_file", help="写到别的路径（默认 <root>/.env）")
    ap.add_argument("--no-verify", action="store_true",
                    help="跳过联网验证（--check 时退回只看配置有没有）")
    ap.add_argument("--print-only", action="store_true", help="只打印将要写入的内容")
    ap.add_argument("--from-file", dest="from_file",
                    help="从文件读 key（仅供自动化测试，正常使用别用）")
    ap.add_argument("--yes", action="store_true", help="所有确认都回答 yes")
    args = ap.parse_args()

    root = find_root()

    if args.check:
        return do_check(root, do_verify=not args.no_verify)

    # --print-only / --from-file 是"只做凭据、别碰环境"的模式：它们要么用于测试、
    # 要么只是预览，没有理由因此去联网装包。
    if args.print_only or args.from_file:
        return do_configure(root, args)

    print(f"📦 项目根：{root}\n")
    print("[1/2] 环境")
    exe, ver, ok_py = check_python()
    if not ok_py:
        print(f"  ❌ Python {ver} 太旧，需要 ≥ 3.9。")
        return BAD_ENV
    py = project_python()
    print(f"  → 解释器 {exe}")
    if not ensure_pip(py):
        return BAD_ENV
    if not ensure_deps(root, py):
        return BAD_ENV

    if args.install:
        print("\n✅ 依赖就绪。还要配凭据才能用 AI 功能——")
        print(f"   在你自己的终端里跑：{python_cmd(root)} {rel(root, Path(__file__))}")
        return NEEDS_CRED

    print("\n[2/2] 凭据")
    return do_configure(root, args)


if __name__ == "__main__":
    raise SystemExit(main())
