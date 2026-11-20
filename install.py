#!/usr/bin/env python3
"""drover 安装脚本：把 drover 装进 Claude Code 和 / 或 Codex。只用 python3 标准库。

python3 install.py [--host claude|codex|both] [--mode link|copy] [--workspace DIR] [--repo-dir DIR]...
                   [--profile NAME|DIR] [--oncall|--no-oncall] [--install-deps] [--dry-run] [--check] [--uninstall] [--json]
（一键安装用包根目录的 ./install.sh：先检查 git、python3，再把参数原样交给本脚本。）

安装、--dry-run、--check 共用同一套"计划项"：每一项检查现状，算出要做什么（新建 / 替换 / 合并 / 跳过）、
会不会先备份；install 照做，--dry-run 只打印，--check 把"还要做事的项"报成没装对。--uninstall 另有一套计划项。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

PKG_ROOT = Path(__file__).resolve().parent
HOME = Path.home()
# drover 的状态目录：config.json、安装记录、任务目录、备份、基线缓存、heavy 锁都在这里（和 bin/drover 的默认值一致）。
STATE_HOME = HOME / ".drover"
# 安装布局（和 README、分享材料一致）：包放在固定位置，其他入口都经它指过去，link / copy 两种模式下路径都不变。
INSTALL_ROOT = STATE_HOME / "pkg"
BIN_LINK = STATE_HOME / "bin" / "drover"
BIN_TARGET = INSTALL_ROOT / "bin" / "drover"
CONFIG_FILE = STATE_HOME / "config.json"
# 安装记录：装了哪些宿主、哪种模式、新建了哪些文件、往 settings 里加了哪些规则（--uninstall 只撤这些）、做过哪些备份。
STATE_FILE = STATE_HOME / "drover-install.json"
# Codex 会递归扫描 skills 目录下的 SKILL.md：备份放在原地会多出一个同名 drover 技能，所以挪到这里。
BACKUP_DIR = STATE_HOME / "backups"
# copy 模式写在副本根目录的标记：有它说明是 drover 自己复制的，重装时直接替换、不备份。
COPY_MARKER = ".drover-copy"
CLAUDE_HOME = HOME / ".claude"
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")  # 和 bin/drover 一致：CODEX_HOME 优先

CLAUDE_COMMAND = CLAUDE_HOME / "commands" / "drover.md"
CLAUDE_HERDR_SKILL = CLAUDE_HOME / "skills" / "herdr" / "SKILL.md"
CLAUDE_SETTINGS = CLAUDE_HOME / "settings.json"
CODEX_SKILLS = CODEX_HOME / "skills"
CODEX_DROVER_SKILL = CODEX_SKILLS / "drover"
CODEX_HERDR_SKILL = CODEX_SKILLS / "herdr"
CODEX_CONFIG = CODEX_HOME / "config.toml"

# 主工作台没有默认值、也不给建议值：每个人的代码目录各不相同，猜错了会凭空建一个目录、往里写 AGENTS.md。
# 首次安装（config.json 里还没有 workspace）不传 --workspace 时，交互终端里问这一句，非交互时按缺参数退出（退出码 2）。
# 主工作台也不能是家目录（或它的上级目录），见 home_problem。
WORKSPACE_QUESTION = "主工作台目录（你平时放各个仓库、开主会话的目录，不能是家目录）"
# --profile 写这些值时表示去掉 config 的 profile 键（只用包默认规则）。
PROFILE_NONE = {"none", "null", ""}
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ROUTING_TEMPLATE = PKG_ROOT / "flow" / "routing.md"
BLOCK_BEGIN = "<!-- drover:routing:begin -->"
BLOCK_END = "<!-- drover:routing:end -->"
BLOCK_NOTE = ("<!-- 这一段由 drover 的 install.py 从 flow/routing.md 渲染，重装时整段替换；"
              "不要在区块里改，个人规则写在区块外。 -->")
BLOCK_BEGIN_RE = re.compile(r"^[ \t]*" + re.escape(BLOCK_BEGIN) + r"[ \t]*$", re.M)
BLOCK_END_RE = re.compile(r"^[ \t]*" + re.escape(BLOCK_END) + r"[ \t]*$", re.M)
TEMPLATE_COMMENT_RE = re.compile(r"\A\s*<!--.*?-->\s*", re.S)
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

# 权限规则：只追加缺的，不删、不重排已有的。
CLAUDE_GLOBAL_RULES = [
    "Bash(~/.drover/bin/drover:*)",
    "Read(~/.drover/**)",
    "Edit(~/.drover/**)",
    "Read(~/.herdr/worktrees/**)",
    "Edit(~/.herdr/worktrees/**)",
    "Bash(herdr agent:*)",
    "Bash(herdr pane:*)",
    "Bash(herdr worktree:*)",
    "Bash(herdr workspace list:*)",
    "Bash(herdr tab list:*)",
    "Bash(herdr status:*)",
]
CLAUDE_WORKSPACE_RULES = [
    "Skill(drover)",
    "Skill(herdr)",
    "Bash(herdr workspace *)",
    "Bash(herdr tab *)",
]
CLAUDE_MD_LINE = "@AGENTS.md"

# cd+git 拦截 hook：~/.claude/settings.json 的 hooks.PreToolUse 里加一项（matcher Bash）。先 cd 再跑 git 会触发
# Claude Code 内置的强制确认（没法加白名单、没有 "don't ask again"），子代理看不到 CLAUDE.md 里"用 git -C"的规则，
# 最爱这么写；hook 拒绝这种写法并提示改成 git -C <目录> …，agent 自己改写重试，不用人批。
# 命令指向装好的包（~/.drover/pkg，link / copy 下路径都不变），不是 clone 目录。
HOOK_SCRIPT_REL = Path("hooks") / "cd_git_guard.py"
HOOK_SCRIPT = INSTALL_ROOT / HOOK_SCRIPT_REL
HOOK_EVENT = "PreToolUse"
HOOK_MATCHER = "Bash"
HOOK_TIMEOUT = 5  # 秒

# 主会话登记 hook：hooks.SessionStart 里加一项（不设 matcher：新开、/clear、resume、compact 都跑），执行
# drover main-register --hook，把主会话在 herdr 里改名为 drover（herdr-oncall 插件按这个名字认主会话）。herdr 的名字
# 跟着 pane 里当前的 agent 走，/clear、退出重开都会清掉（实测），所以每次会话开始都登记；不是主会话（工作目录不是
# 主工作台、在编排者的 orch workspace 里）的直接跳过。它要先等 herdr 换上这次的 session（上限 8 秒），timeout 留余量。
# Codex 不装：codex 的 SessionStart 第一条消息时才跑，新 hook 还要用户手动信任，信任之前所有 codex 会话启动都卡在
# "Hooks need review"上（会弄坏 codex 派发）；Codex 主会话按路由规则在第一条消息时登记。
SESSION_HOOK_EVENT = "SessionStart"
SESSION_HOOK_ARGS = "main-register --hook"
SESSION_HOOK_TIMEOUT = 15  # 秒

# 主会话读新规则 hook：hooks.UserPromptSubmit 里加一项（不设 matcher），执行 drover rules --hook。
# 自动更新改了 AGENTS.md 里的 drover 区块之后，主会话不用 /clear：用户的下一条消息（飞书经 herdr agent prompt 打进来的也算）
# 提交时，hook 把规则的改动打到 stdout，Claude Code 把它和这条消息一起放进上下文（官方文档 hooks 的 UserPromptSubmit：
# "Plain stdout on exit 0 is also added to Claude's context"）。不是主会话（session_id 不是 ~/.drover/main-session.json 记的）、
# 规则没变时什么都不输出；本机每个会话的每条消息都跑它，常见路径不调 herdr、不跑 git。Codex 不装，理由同上。
PROMPT_HOOK_EVENT = "UserPromptSubmit"
PROMPT_HOOK_ARGS = "rules --hook"
PROMPT_HOOK_TIMEOUT = 10  # 秒

# 飞书 oncall：drover 内置的 herdr-oncall 插件（代码在独立仓库，交付上随 drover 一起装）。和插件的约定见 README
# "离开电脑：飞书 oncall"一节：主会话在 herdr 里叫 drover；drover 的配置写在插件配置目录的 drover.env（插件只读、
# 优先级最低），用户自己的 .env（飞书配对的密钥在里面）drover 不读不写。bin/drover 里有同样的几个常量（自测断言两边一样）。
ONCALL_PLUGIN_ID = "com.codreamer.herdr.oncall"
ONCALL_REPO = "fulanto/herdr-oncall"
ONCALL_REF = "stable"
ONCALL_MIN_VERSION = "0.7.0"  # 带按角色通知（只为主会话推送、编排者 / worker 卡久了才升级）的第一版
ONCALL_LEGACY_IDS = ["oncall", "oncall.telegram", "fulanto.oncall"]
ONCALL_ENV_NAME = "drover.env"
ONCALL_ENV_TEXT = """\
# 由 drover 的 install.py 写入：重装时整份覆盖，卸载时删除。
# 要改某一项，写到同目录的 .env 里（.env 优先），不要改这个文件。
MAIN_AGENT_NAME=drover
SUPERVISOR_PREFIX=orch-
SUPERVISOR_ESCALATE_SEC=300
SUPERVISOR_ESCALATE_SEC_CODEX=120
SUPERVISED_ESCALATE_SEC=600
BARE_MESSAGE_TARGET=main
MAIN_DONE_PING=relay
MAIN_RELAY_MARKER=【转达】
CHAT_IMAGE_DIR=~/.drover/oncall-images
"""
ONCALL_STATUS_SCRIPT = "src/actions/status.mjs"
ONCALL_INSTALL_TIMEOUT = 600  # 秒：装插件时要编译桌面面板、装依赖
ONCALL_NODE_MIN = 18      # 插件跑起来要的 Node
ONCALL_NODE_REPLIES = 22  # 收飞书回复要的 Node（全局 WebSocket）
ONCALL_SKIP_HINT = "不要飞书通知可以加 --no-oncall（会记住，以后重跑不用再带）"
ONCALL_OFF = "不接飞书 oncall（选装：要接时重跑 install 加 --oncall）"
NODE_INSTALL_HINT = "装 Node 22+：brew install node，或 https://nodejs.org"
# 在终端里装完、飞书还没配对时当场问一句要不要扫码配对：最多等多久、多久查一次插件 status（秒）。环境变量给自测缩短用。
PAIR_WAIT = float(os.environ.get("DROVER_PAIR_WAIT") or 300)
PAIR_POLL = float(os.environ.get("DROVER_PAIR_POLL") or 3)
PAIR_CMD = "~/.drover/bin/drover oncall pair"

# copy 模式不复制的东西（也是比较"内容是否相同"时忽略的东西）。
COPY_IGNORE = {".git", "__pycache__", ".DS_Store", ".venv", "node_modules", ".pytest_cache",
               ".code-review-graph", ".idea", ".vscode", COPY_MARKER}
REQUIRED_TOOLS = ["herdr", "git", "python3"]
OPTIONAL_TOOLS = ["code-review-graph", "uv", "bun", "pnpm"]
HOSTS = ["claude", "codex"]
HERDR_TIMEOUT = 60

# 缺依赖时的官方安装方式（出处）：
# - herdr：https://herdr.dev/agent-guide.md、https://herdr.dev/tui/docs/install/ 。安装脚本装到 ${HERDR_INSTALL_DIR:-$HOME/.local/bin}，
#   不用 sudo、不改 shell 的 rc 文件；目录不在 PATH 里时只打印一句提示。
# - Claude Code：https://code.claude.com/docs/en/setup （原生安装，官方推荐）。
# - Codex CLI：https://github.com/openai/codex 、https://learn.chatgpt.com/docs/codex/cli 。
# install.py 只会替你装 herdr（用户目录、官方脚本、要你同意或带 --install-deps）；Claude Code / Codex 只给命令。
HERDR_INSTALL_CMD = "curl -fsSL https://herdr.dev/install.sh | sh"
HERDR_INSTALL_DOCS = "https://herdr.dev/tui/docs/install/"
HERDR_BREW_CMD = "brew install herdr"
# 测试和内网镜像用：设了就用它代替官方安装命令（照样交给 sh -c 执行）。平时不用设。
HERDR_INSTALLER_ENV = "DROVER_HERDR_INSTALLER"
HERDR_INSTALL_TIMEOUT = 600
AGENT_INSTALL = {
    "claude": {"name": "Claude Code", "cmd": "curl -fsSL https://claude.ai/install.sh | bash",
               "alt": "brew install --cask claude-code", "docs": "https://code.claude.com/docs/en/setup"},
    "codex": {"name": "Codex CLI", "cmd": "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
              "alt": "npm install -g @openai/codex 或 brew install --cask codex", "docs": "https://github.com/openai/codex"},
}
GIT_HINT = {"Darwin": "xcode-select --install（或 brew install git）", "Linux": "sudo apt-get install git / sudo dnf install git"}

# 验收工具：Robert C. Martin 的 crapper（圈复杂度）和 dryer（重复代码），都是 0BSD。条目验收跑 crapper（只算复杂度，不跑测试），
# 集成检查跑 dryer（扫全仓，只留碰到改动的重复）。装在 drover 自己的目录，钉提交；装不上只记警告（不挡安装、退出码不变），
# 验收时报"测不了"。config.json 写 "quality_tools": false（或环境变量 DROVER_QUALITY_TOOLS=0）就不装、验收也不跑。
# 换版本时要核对：bin/drover 的 CRAP_SPANS_SCRIPT 直接调 crapper 的内部接口（crapper.discover.language_of / is_test_file、
# crapper.languages.functions_in_file，Function 的 name、namespace、complexity、start_line、end_line），dryer 读的是
# <root>/.metrics/dry.edn 的格式（:candidates，:left / :right 的 :file、:start-line、:end-line）。两样的格式自测里都有。
TOOLS_DIR = STATE_HOME / "tools"
QUALITY_TOOLS = {
    "crapper": ("https://github.com/unclebob/crapper.git", "c757ec79a6c15dc815238bbd9c2f4601eb54d3cc"),
    "dryer": ("https://github.com/unclebob/dryer.git", "66ff6d21a42c04afcad89c78a80066176d1294b0"),
}
QUALITY_TOOLS_ENV = "DROVER_QUALITY_TOOLS_SOURCE"  # 自测用：JSON {"工具": ["clone 地址", "提交"]} 代替上面的表，不联网
QUALITY_SWITCH_ENV = "DROVER_QUALITY_TOOLS"         # 0 / false / off：和 config 的 quality_tools: false 一样（和 bin/drover 一致）
QUALITY_OFF = "关了（config.json 的 quality_tools 是 false，或 DROVER_QUALITY_TOOLS=0）：不装、不查，验收也不跑"
# 两个工具要 Python 3.11+（它们的 pyproject 都是 requires-python >= 3.11）；macOS 自带的 /usr/bin/python3 是 3.9。
# 先找本机已有的，再问 uv（也找得到 drover 自己装进 TOOLS_PYTHON_DIR 的那份），都没有才用 uv 装一份进 drover 的目录：
# --install-dir 放在 ~/.drover/tools/python，--no-bin 不往 ~/.local/bin 放 python 可执行文件（uv python install --help）。
TOOLS_PYTHON_MIN = (3, 11)
TOOLS_PYTHON_NAMES = ["python3.14", "python3.13", "python3.12", "python3.11", "python3"]
TOOLS_PYTHON_VERSION = "3.12"
TOOLS_PYTHON_DIR = TOOLS_DIR / "python"
# 没有 uv 时装一份 drover 私有的：官方安装脚本（https://docs.astral.sh/uv/getting-started/installation/），UV_INSTALL_DIR
# 指到 ~/.drover/tools/bin（脚本把 uv 直接放进这个目录），UV_NO_MODIFY_PATH=1 不改 shell 配置
# （https://docs.astral.sh/uv/reference/installer/）。卸载时随 ~/.drover/tools 一起删。
TOOLS_UV = TOOLS_DIR / "bin" / "uv"
UV_INSTALL_CMD = "curl -LsSf https://astral.sh/uv/install.sh | sh"
UV_INSTALLER_ENV = "DROVER_UV_INSTALLER"  # 自测用：换成别的安装命令（照样交给 sh -c）
TOOLS_TIMEOUT = 600  # 秒：clone、装 uv / Python、pip install 每一步的上限
TOOLS_MANUAL = ("重跑 install（或 ~/.drover/bin/drover update）会按钉的版本补装；不要验收工具就在 ~/.drover/config.json 写 "
                "\"quality_tools\": false。没装上时验收里 crapper、dryer 报\"测不了\"，drover 其余功能不受影响")

TS = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


class InstallError(Exception):
    """计划阶段就能确定做不了的情况（参数冲突、包不完整、缺必要输入、主工作台不合适），直接退出（默认退出码 2）。
    extra 是 --json 时和 error 一起输出的字段。"""

    def __init__(self, message: str, rc: int = 2, **extra) -> None:
        super().__init__(message)
        self.rc = rc
        self.extra = extra


class StepWarning(Exception):
    """计划项没做成，但只记警告：不算失败，install 退出码不变（验收工具这类装不上也不影响 drover 本身的东西）。"""


# ---------------------------------------------------------------- 路径与内容工具


def disp(p) -> str:
    """显示用：HOME 下的路径写成 ~/…。"""
    s = str(p)
    h = str(HOME)
    if s == h:
        return "~"
    if s.startswith(h + os.sep):
        return "~" + s[len(h):]
    return s


def expand(p: str) -> Path:
    """用户给的路径 → 绝对路径（不解析软链，保持用户的写法）。"""
    return Path(os.path.abspath(os.path.expanduser(str(p))))


def tilde(p: str) -> str:
    """写进 config.json 的写法：HOME 下的写成 ~/…。"""
    return disp(expand(p))


def link_points_to(link: Path) -> Path:
    """软链字面指向的路径（相对路径按软链所在目录展开，不解析后续软链）。"""
    raw = os.readlink(link)
    return Path(os.path.normpath(os.path.join(str(link.parent), raw)))


def same_file(a: Path, b: Path) -> bool:
    try:
        return a.is_file() and b.is_file() and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def ignored(name: str) -> bool:
    return name in COPY_IGNORE or name.endswith(".pyc") or ".bak-drover-" in name


def tree_files(root: Path) -> dict:
    """目录下参与比较的文件：相对路径 → 路径（跳过 COPY_IGNORE）。"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not ignored(d))
        for name in filenames:
            if not ignored(name):
                p = Path(dirpath) / name
                out[str(p.relative_to(root))] = p
    return out


def same_tree(a: Path, b: Path) -> bool:
    if not (a.is_dir() and b.is_dir()):
        return False
    fa, fb = tree_files(a), tree_files(b)
    return fa.keys() == fb.keys() and all(same_file(fa[k], fb[k]) for k in fa)


def same_content(path: Path, source: Path) -> bool:
    """path（可能是软链）的内容和包里的 source 是否一样：文件比字节，目录逐个文件比。"""
    return same_tree(path, source) if source.is_dir() else same_file(path, source)


def is_drover_pkg(p: Path) -> bool:
    """看起来是不是一份 drover 包（clone 或 copy 安装的副本）。"""
    return (p / "bin" / "drover").is_file() and (p / "flow" / "dispatch.md").is_file()


def backup_target(path: Path) -> Path:
    """备份路径：<原名>.bak-drover-<时间>，放在原位置旁边。Codex skills 目录下的除外（见 BACKUP_DIR 的注释）。"""
    if path.parent == CODEX_SKILLS:
        base = BACKUP_DIR / "codex-skills" / f"{path.name}.bak-drover-{TS}"
    else:
        base = path.with_name(f"{path.name}.bak-drover-{TS}")
    cand, n = base, 2
    while cand.exists() or cand.is_symlink():
        cand = base.with_name(f"{base.name}-{n}")
        n += 1
    return cand


def remove_path(p: Path) -> None:
    """删软链 / 文件 / 目录（目录只在调用方确认是 drover 自己的东西时才传进来）。"""
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)


def make_link(dest: Path, target: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(target), str(dest))


def relink(dest: Path, target: Path) -> None:
    """原位置还有东西就先删掉（调用方已确认可以删：内容相同，或已经备份挪走），再建软链。"""
    if dest.exists() or dest.is_symlink():
        remove_path(dest)
    make_link(dest, target)


def read_json(path: Path) -> tuple[Optional[dict], Optional[str]]:
    """返回 (内容, 错误)。文件不存在时两个都是 None。"""
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, f"读不了或不是合法 JSON：{exc}"
    if not isinstance(data, dict):
        return None, "顶层不是 JSON 对象"
    return data, None


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 计划项


@dataclass
class Step:
    """一个计划项。run 为 None 表示不用动；level 是现状的判断：ok 正确，warn 能用但过期 / 只是提示，fail 没装对。"""
    key: str
    label: str
    path: str
    state: str
    action: str = "无需改动"
    run: Optional[Callable[[], Optional[str]]] = None
    level: str = "ok"
    fs: Optional[Path] = None          # 要备份的路径
    backup: Optional[Path] = None      # 备份到哪里；None 表示不备份
    backup_how: str = "move"           # move：整个挪走，原位置再新建；copy：复制一份，再改原文件
    note: str = ""
    result: str = ""

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "path": self.path, "state": self.state,
                "action": self.action, "changes": self.run is not None, "level": self.level,
                "backup": disp(self.backup) if self.backup else None, "note": self.note, "result": self.result}


@dataclass
class Ctx:
    hosts: list
    mode: str
    workspace: Optional[str]             # config 写法（~/…）；只有 --check / --uninstall 时可能是 None（没配置主工作台）
    repo_dirs: list
    config_update: dict                  # 要写进 config.json 的键（没有配置时的全套默认值，或显式传的参数）
    config_existing: Optional[dict]
    config_error: Optional[str]
    profile: Optional[str] = None        # 生效的团队 profile（config 写法：名字或 ~/… 路径）；None 表示没配置
    dry_run: bool = False
    checking: bool = False
    state: dict = field(default_factory=dict)       # 上次的安装记录
    created: list = field(default_factory=list)     # 本次新建的文件（绝对路径字符串）
    added_rules: dict = field(default_factory=dict)  # 本次加的规则：settings 文件绝对路径 → 规则列表
    added_hooks: dict = field(default_factory=dict)  # 本次加（或更新成）的 hook 命令：settings 文件绝对路径 → 命令列表
    dropped_hooks: dict = field(default_factory=dict)  # 本次换掉的 drover 旧 hook 命令（路径变了）：同上
    backups: list = field(default_factory=list)     # 本次做的备份
    herdr_status: Optional[dict] = None
    herdr_status_error: Optional[str] = None
    # 缺 herdr 时怎么办：yes（--install-deps，直接装）、ask（交互终端，先问一句）、no（非交互，只给命令）。
    deps_mode: str = "no"
    herdr_planned: bool = False          # 计划里有"安装 herdr"这一项（后面要用 herdr 的项先计划好，等它装完再跑）
    herdr_installed: Optional[str] = None  # 这次装上的 herdr 路径
    oncall: bool = False                 # 接不接飞书 oncall（选装，make_ctx 里按 oncall_enabled 算）
    oncall_plugins: Optional[list] = None  # herdr plugin list --json 的插件列表（缓存）；查不了时是 None
    oncall_list_error: Optional[str] = None
    oncall_installed_now: bool = False   # 这次由 drover 装上了插件（写进安装记录，--uninstall 才会卸它）
    oncall_env_path: Optional[str] = None  # drover.env 的路径（知道了就写进安装记录，--uninstall 按它删）
    node: Optional[tuple] = None         # (node 路径, 版本)；找不到是 (None, None)
    feishu: Optional[str] = None         # 交互终端装完时飞书配没配：paired / unpaired；不查或查不了是 None
    quality_tools: bool = True           # 装不装验收工具（make_ctx 里按 quality_tools_enabled 算）
    tools_python: Optional[str] = None   # 给验收工具建 .venv 用的 Python 3.11+（找到或装上之后才有）

    @property
    def workspace_path(self) -> Path:
        return expand(self.workspace)


# ---------------------------------------------------------------- herdr


def herdr_fallback_dirs() -> list:
    """herdr 官方安装脚本装到的目录：PATH 没刷新时（刚装完、或者没把这个目录加进 PATH）去这里找。
    只看安装脚本的位置，不猜 Homebrew 之类的目录（那些本来就在 PATH 里）。"""
    dirs = []
    if os.environ.get("HERDR_INSTALL_DIR"):
        dirs.append(Path(os.path.expanduser(os.environ["HERDR_INSTALL_DIR"])))
    dirs.append(HOME / ".local" / "bin")
    return dirs


def herdr_off_path() -> Optional[Path]:
    """PATH 里没有 herdr、但官方安装目录里有时，返回那个可执行文件。"""
    if shutil.which("herdr"):
        return None
    for d in herdr_fallback_dirs():
        exe = d / "herdr"
        if exe.is_file() and os.access(str(exe), os.X_OK):
            return exe
    return None


def herdr_bin() -> Optional[str]:
    found = shutil.which("herdr")
    if found:
        return found
    off = herdr_off_path()
    return str(off) if off else None


def run_herdr(args: list, timeout: int = HERDR_TIMEOUT) -> subprocess.CompletedProcess:
    exe = herdr_bin()
    if not exe:
        raise RuntimeError(f"找不到 herdr（PATH、{'、'.join(disp(d) for d in herdr_fallback_dirs())} 都没有）："
                           f"先装 herdr（{HERDR_INSTALL_CMD}，或重跑 install 时加 --install-deps）")
    return subprocess.run([exe, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=timeout)


def herdr_install_cmd() -> str:
    return os.environ.get(HERDR_INSTALLER_ENV) or HERDR_INSTALL_CMD


def confirm(question: str) -> bool:
    """交互终端里问一句 [Y/n]：直接回车算同意。"""
    try:
        answer = input(question).strip().lower()
    except EOFError:
        return False
    return answer in ("", "y", "yes", "是", "好")


def interactive(args: argparse.Namespace) -> bool:
    """交互终端：stdin、stdout 都是终端，且不是 --json。缺 herdr 时问一句 [Y/n]、首次安装问主工作台，都按它判断。"""
    return not args.json and sys.stdin.isatty() and sys.stdout.isatty()


def install_herdr(ctx: "Ctx") -> str:
    """执行 herdr 的安装命令（默认是官方安装脚本），装完在 PATH 和官方安装目录里找到它。"""
    cmd = herdr_install_cmd()
    if ctx.deps_mode == "ask":
        print(f"\n没装 herdr：drover 的编排者和 worker 都跑在 Herdr 里。官方安装命令（{HERDR_INSTALL_DOCS}）：\n  {cmd}")
        if not confirm("现在执行它安装 herdr 吗？[Y/n] "):
            raise RuntimeError(f"你选了不装。要装时手动执行 {cmd}，再重跑 install（或者加 --install-deps）")
    print(f"安装 herdr：{cmd}", file=sys.stderr, flush=True)
    try:
        res = subprocess.run(["sh", "-c", cmd], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, timeout=HERDR_INSTALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{cmd} 超过 {HERDR_INSTALL_TIMEOUT} 秒没跑完")
    tail = "；".join(line.strip() for line in res.stdout.strip().splitlines()[-4:] if line.strip())
    if res.returncode != 0:
        raise RuntimeError(f"{cmd} 退出码 {res.returncode}：{tail[:300]}")
    exe = herdr_bin()
    if not exe:
        raise RuntimeError(f"安装命令跑完了，但找不到 herdr（看过 PATH 和 {'、'.join(disp(d) for d in herdr_fallback_dirs())}）：{tail[:300]}")
    ctx.herdr_status, ctx.herdr_status_error = None, None  # 之前"找不到 herdr"的结果作废
    ctx.herdr_installed = exe
    return f"装好了：{disp(exe)}" + ("" if shutil.which("herdr") else "（这个目录不在 PATH 里，见汇总里的提示）")


def herdr_integration_status(ctx: Ctx) -> Optional[dict]:
    """解析 `herdr integration status`：每行 "<agent>: <状态> (<hook 路径>)"。只读，dry-run 和 --check 也会跑。"""
    if ctx.herdr_status is not None or ctx.herdr_status_error is not None:
        return ctx.herdr_status
    try:
        res = run_herdr(["integration", "status"])
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        ctx.herdr_status_error = str(exc)
        return None
    if res.returncode != 0:
        ctx.herdr_status_error = f"herdr integration status 退出码 {res.returncode}：{res.stderr.strip()[:200]}"
        return None
    status = {}
    for line in res.stdout.splitlines():
        name, sep, rest = line.partition(":")
        if sep:
            status[name.strip()] = rest.strip()
    ctx.herdr_status = status
    return status


def herdr_skill_text() -> str:
    res = run_herdr(["--skill"])
    if res.returncode != 0 or not res.stdout.strip():
        raise RuntimeError(f"herdr --skill 没有输出技能内容（退出码 {res.returncode}）：{res.stderr.strip()[:200]}")
    return res.stdout if res.stdout.endswith("\n") else res.stdout + "\n"


# ---------------------------------------------------------------- 宿主、配置、安装记录


def detect_hosts() -> tuple[list, str]:
    """--host 没给时：PATH 里有 claude 命令或 ~/.claude 目录存在 → claude；codex 命令或 CODEX_HOME（~/.codex）存在 → codex。"""
    found, why = [], []
    if shutil.which("claude") or CLAUDE_HOME.is_dir():
        found.append("claude")
        why.append("claude：" + ("PATH 里有 claude 命令" if shutil.which("claude") else f"{disp(CLAUDE_HOME)} 存在"))
    if shutil.which("codex") or CODEX_HOME.is_dir():
        found.append("codex")
        why.append("codex：" + ("PATH 里有 codex 命令" if shutil.which("codex") else f"{disp(CODEX_HOME)} 存在"))
    return found, "；".join(why)


def parse_hosts(value: Optional[str]) -> Optional[list]:
    if value is None:
        return None
    return list(HOSTS) if value == "both" else [value]


def load_state() -> dict:
    data, _ = read_json(STATE_FILE)
    return data or {}


def resolve_config(args: argparse.Namespace) -> tuple[Optional[str], list, dict, Optional[dict], Optional[str]]:
    """算出生效的 workspace / repo_dirs，以及要写进 config.json 的键。
    已有配置时不覆盖，除非显式传了 --workspace / --repo-dir / --profile（只改传了的那个键）。
    --profile none 在 update 里记成 profile: None，写文件时去掉这个键。
    主工作台没有默认值：config 里没有、也没传 --workspace 时 workspace 是 None（install 之前已经由 ensure_workspace
    问过或按缺参数退出了，只有 --check / --uninstall 会拿到 None），repo_dirs 跟着为空。"""
    existing, err = read_json(CONFIG_FILE)
    base = existing or {}
    update = {}
    if args.workspace is not None:
        update["workspace"] = tilde(args.workspace)
    if args.repo_dir:
        update["repo_dirs"] = [tilde(d) for d in args.repo_dir]
    profile_arg = getattr(args, "profile", None)
    if profile_arg is not None:
        update["profile"] = profile_value(profile_arg)
    if getattr(args, "no_oncall", False):
        update["oncall"] = False
    elif getattr(args, "oncall", False):
        update["oncall"] = True
    workspace = update.get("workspace") or base.get("workspace") or None
    repo_dirs = update.get("repo_dirs") or base.get("repo_dirs") or ([workspace] if workspace else [])
    if existing is None and err is None and workspace:
        update = {"workspace": workspace, "repo_dirs": repo_dirs,
                  **({"profile": update["profile"]} if update.get("profile") else {}),
                  **({"oncall": update["oncall"]} if "oncall" in update else {})}
    return workspace, list(repo_dirs), update, existing, err


def config_workspace() -> Optional[str]:
    """已有 config.json 里的 workspace；没有文件、读不了、或者没有这个键时是 None（都算首次安装）。"""
    existing, _ = read_json(CONFIG_FILE)
    value = (existing or {}).get("workspace")
    return value if isinstance(value, str) and value.strip() else None


def home_problem(workspace: str) -> Optional[str]:
    """主工作台是家目录本身或它的上级目录（含 /）时返回原因，否则 None。
    比较解析后的真实路径：~、相对路径（按当前目录）、末尾斜杠、软链都算。"""
    real = Path(os.path.realpath(expand(workspace)))
    home_real = Path(os.path.realpath(HOME))
    if real == home_real:
        what, where = "家目录", "" if workspace.rstrip("/") == "~" else "，解析后就是家目录"
        extra = "；而且 <主工作台>/.claude/settings.local.json 就是 ~/.claude/settings.local.json，会落进 Claude Code 的用户配置目录"
    elif real in home_real.parents:
        what, where, extra = "家目录的上级目录", "" if workspace == str(real) else f"，解析后是 {real}", ""
    else:
        return None
    return (f"主工作台不能是{what}（{workspace}{where}）：Claude Code 会从当前目录往上找 CLAUDE.md、Codex 会加载 AGENTS.md，"
            f"主工作台设在这里，drover 的路由规则会进到家目录下的所有会话{extra}。换一个你平时放各个仓库的目录。")


def missing_workspace() -> InstallError:
    """非交互（agent 的 shell、管道、CI、--json）时首次安装没传 --workspace：退出码 2。不给建议值。"""
    msg = "首次安装要用 --workspace 指定主工作台（你平时放各个仓库、开主会话的目录，不能是家目录）。"
    if CONFIG_FILE.exists():
        _, err = read_json(CONFIG_FILE)
        msg += f"{disp(CONFIG_FILE)} 在，但里面没有可用的 workspace（{err or '没有这个键'}），也按首次安装算。"
    msg += ("重跑时加上 --workspace <目录>。--repo-dir（按名字找仓库的目录）默认就是 workspace，"
            "仓库不都放在主工作台下时再加。在终端里直接跑 ./install.sh 会问你。")
    return InstallError(msg, missing="workspace")


def ask_workspace() -> str:
    """交互终端里问主工作台，没有默认值：空输入、不是目录、是家目录（或它的上级）时说明原因再问。
    EOF（Ctrl-D）按缺参数退出（退出码 2），Ctrl-C 退出码 130；两种都什么都没装。"""
    question = WORKSPACE_QUESTION + "："
    while True:
        try:
            answer = input(question).strip()
        except EOFError:
            print()
            raise missing_workspace()
        except KeyboardInterrupt:
            print()
            raise InstallError("取消了：没有填主工作台，什么都没装。", rc=130)
        if not answer:
            print("要填一个目录（输入支持 ~）。")
            continue
        if expand(answer).exists() and not expand(answer).is_dir():
            print(f"{disp(expand(answer))} 不是目录，换一个。")
            continue
        problem = home_problem(answer)
        if problem:
            print(problem)
            continue
        return answer


def ensure_workspace(args: argparse.Namespace) -> None:
    """安装和 --dry-run 之前：config.json 里没有 workspace、也没传 --workspace（首次安装）时，交互终端里问一句，
    答案当成 --workspace；非交互时按缺参数退出。已有 config 的 workspace 照旧沿用，不问。"""
    if args.workspace is not None or config_workspace():
        return
    if not interactive(args):
        raise missing_workspace()
    args.workspace = ask_workspace()


def check_workspace(args: argparse.Namespace) -> None:
    """安装和 --dry-run 之前：生效的主工作台（--workspace、交互输入、已有 config.json）是家目录或它的上级目录时拒绝，
    退出码 2，什么都不写。--check 只提示（plan_workspace_location），--uninstall 不查（要能撤掉以前装在家目录的区块）。"""
    if args.workspace is not None:
        problem = home_problem(args.workspace)
        if problem:
            raise InstallError(f"--workspace {args.workspace}：{problem}", invalid="workspace",
                               rejected_workspace=args.workspace)
        return
    current = config_workspace()
    problem = home_problem(current) if current else None
    if problem:
        raise InstallError(f"{disp(CONFIG_FILE)} 里的 workspace 是 {current}：{problem}"
                           "重跑时加上 --workspace <目录>（只改这个键，改之前先备份）。",
                           invalid="workspace", rejected_workspace=current)


def profile_value(raw: str) -> Optional[str]:
    """--profile 的写法 → 写进 config 的值：none 去掉（None）；名字原样；路径（带 / 或以 ~ 开头）写成 ~/… 的绝对形式。"""
    raw = raw.strip()
    if raw.lower() in PROFILE_NONE:
        return None
    return tilde(raw) if ("/" in raw or raw.startswith("~")) else raw


def profile_dir(value: str, pkg_root: Path) -> Path:
    """和 bin/drover 的 profile_info 一致：名字 → <包根>/profiles/<名>/；带 / 或以 ~ 开头 → 那个目录（相对路径按 config.json 所在目录算）。"""
    value = str(value).strip()
    if "/" in value or value.startswith("~"):
        p = Path(os.path.expanduser(value))
        return p if p.is_absolute() else CONFIG_FILE.parent / p
    return pkg_root / "profiles" / value


def effective_profile(existing: Optional[dict], update: dict) -> Optional[str]:
    if "profile" in update:
        return update["profile"]
    value = (existing or {}).get("profile")
    return str(value).strip() if value and str(value).strip() else None


def check_profile_arg(args: argparse.Namespace) -> None:
    """显式传了 --profile 时先确认它能解析到目录：写进去一个不存在的 profile，drover playbook 会报错、团队规则会丢。"""
    if args.profile is None:
        return
    value = profile_value(args.profile)
    if value is None:
        return
    if "/" not in value and not value.startswith("~") and not PROFILE_NAME_RE.match(value):
        raise InstallError(f"--profile {args.profile!r}：名字只能用字母、数字、._-（写目录时带 / 或以 ~ 开头）")
    d = profile_dir(value, PKG_ROOT)
    if not d.is_dir():
        names = sorted(p.name for p in (PKG_ROOT / "profiles").iterdir() if p.is_dir()) \
            if (PKG_ROOT / "profiles").is_dir() else []
        raise InstallError(f"--profile {args.profile!r} 解析到 {disp(d)}，目录不存在。包里有的 profile："
                           f"{'、'.join(names) or '没有'}；也可以写团队 profile 目录的路径，或 --profile none 去掉")


# ---------------------------------------------------------------- 路由区块（<workspace>/AGENTS.md）


class BlockError(Exception):
    pass


def render_routing(workspace: str, workspace_abs: str, template: Optional[str] = None) -> str:
    """flow/routing.md → 区块正文。模板开头的 HTML 注释（写给维护者的说明）去掉；占位符：
    {{workspace}} 主工作台（config 写法，如 ~/work），{{workspace_abs}} 它的绝对路径。"""
    text = ROUTING_TEMPLATE.read_text() if template is None else template
    text = TEMPLATE_COMMENT_RE.sub("", text, count=1)
    values = {"workspace": workspace, "workspace_abs": workspace_abs}
    unknown = sorted({m.group(1) for m in PLACEHOLDER_RE.finditer(text)} - values.keys())
    if unknown:
        raise BlockError(f"flow/routing.md 里有不认识的占位符：{', '.join(unknown)}")
    body = PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], text).strip()
    if BLOCK_BEGIN_RE.search(body) or BLOCK_END_RE.search(body):
        raise BlockError("flow/routing.md 里不能单独成行地出现区块标记")
    return body


def render_block(workspace: str, workspace_abs: str, template: Optional[str] = None) -> str:
    body = render_routing(workspace, workspace_abs, template)
    return "\n".join([BLOCK_BEGIN, BLOCK_NOTE, "", body, "", BLOCK_END])


def find_block(text: str) -> Optional[tuple[int, int]]:
    """区块的 [起点, 终点)：从 begin 行行首到 end 行行尾（不含换行）。没有标记返回 None，标记不成对报错。"""
    begins, ends = list(BLOCK_BEGIN_RE.finditer(text)), list(BLOCK_END_RE.finditer(text))
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0].start() < begins[0].end():
        raise BlockError(f"标记不成对（begin {len(begins)} 个、end {len(ends)} 个，或顺序反了），先手动修好再重跑")
    return begins[0].start(), ends[0].end()


def merge_block(text: str, block: str) -> tuple[str, str]:
    """把区块放进 AGENTS.md 的内容里。返回 (新内容, "replace" | "append")。区块外的内容一个字节都不动。"""
    span = find_block(text)
    if span is None:
        base = text.rstrip("\n")
        return (base + "\n\n" if base else "") + block + "\n", "append"
    start, end = span
    return text[:start] + block + text[end:], "replace"


def strip_block(text: str) -> str:
    """去掉区块（含标记），区块前后的空行收成一个空行。"""
    span = find_block(text)
    if span is None:
        return text
    start, end = span
    before, after = text[:start].rstrip("\n"), text[end:].lstrip("\n")
    out = before + ("\n\n" if before and after else "") + after
    return out if not out or out.endswith("\n") else out + "\n"


# ---------------------------------------------------------------- 计划项：包、软链、配置


def plan_link(ctx: Ctx, key: str, label: str, dest: Path, target: Path, source: Path,
              by_resolve: bool = False) -> Step:
    """dest 应该是指向 target 的软链；source 是包里的对应内容，用来判断原位置的东西要不要备份。
    by_resolve：软链字面写法不同、但解析后是同一个地方也算对（包目录用）。"""
    tdisp = disp(target)
    if dest.is_symlink():
        pointed = link_points_to(dest)
        good = pointed == Path(os.path.normpath(str(target)))
        if not good and by_resolve and dest.exists() and target.exists():
            good = dest.resolve() == target.resolve()
        if good:
            if ctx.checking and not dest.exists():
                return Step(key, label, disp(dest), f"软链 → {tdisp}，但目标不存在", action="重跑 install",
                            level="fail")
            return Step(key, label, disp(dest), f"已是软链 → {tdisp}")
        same = dest.exists() and same_content(dest, source)
        state = f"软链指向别处（{os.readlink(dest)}）" + ("，内容和包里相同" if same else "")
    elif dest.exists():
        same = same_content(dest, source)
        kind = "目录" if dest.is_dir() else "文件"
        state = f"实体{kind}，内容" + ("和包里相同" if same else "和包里不同")
    else:
        return Step(key, label, disp(dest), "不存在", action=f"新建软链 → {tdisp}",
                    run=lambda: make_link(dest, target), level="fail")

    def run() -> None:
        relink(dest, target)  # 有备份时原位置已经挪走；内容相同、没有备份时直接换掉

    backup = None if same else backup_target(dest)
    action = f"换成软链 → {tdisp}" + ("" if backup else "（内容相同，不备份）")
    return Step(key, label, disp(dest), state, action=action, run=run, level="fail", fs=dest, backup=backup)


def pkg_commit() -> Optional[str]:
    """包所在 git 仓库（clone 或 worktree）的 HEAD，记进副本标记：bin/drover 的 drover_version 在 copy 模式读它（副本没有 .git；
    update 先快进 clone、再重装副本，中间 clone 的 HEAD 比副本新，不能拿 clone 的）。包不是 git 仓库的根目录、git 跑不了返回 None。"""
    try:
        p = subprocess.run(["git", "-C", str(PKG_ROOT), "rev-parse", "--show-toplevel", "HEAD"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = p.stdout.split()
    return out[1] if p.returncode == 0 and len(out) == 2 and os.path.realpath(out[0]) == str(PKG_ROOT) else None


def copy_package(dest: Path) -> str:
    """把包复制到 dest：先复制到旁边的临时目录，再换上去。标记 .drover-copy 记 source（clone）、commit（clone 当时的 HEAD）。"""
    tmp = dest.with_name(f".{dest.name}.tmp-{TS}")
    if tmp.exists() or tmp.is_symlink():
        remove_path(tmp)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PKG_ROOT, tmp, symlinks=True, ignore=lambda d, names: [n for n in names if ignored(n)])
    marker = {"source": str(PKG_ROOT), "commit": pkg_commit(),
              "copied_at": datetime.datetime.now().isoformat(timespec="seconds")}
    (tmp / COPY_MARKER).write_text(json.dumps(marker, ensure_ascii=False) + "\n")
    if dest.exists() or dest.is_symlink():
        remove_path(dest)
    os.rename(tmp, dest)
    return f"复制了 {len(tree_files(dest))} 个文件"


def plan_pkg(ctx: Ctx) -> Step:
    key, label, dest = "pkg", "包目录", INSTALL_ROOT
    running_from_copy = not dest.is_symlink() and dest.is_dir() and dest.resolve() == PKG_ROOT
    if ctx.mode == "link":
        if running_from_copy:
            return Step(key, label, disp(dest), "就是当前运行的这份副本（copy 安装）",
                        action="link 模式要从 git clone 里运行 install.py；或者加 --mode copy", level="fail")
        if ctx.checking and dest.is_symlink() and is_drover_pkg(dest):
            pointed = dest.resolve()
            note = "" if pointed == PKG_ROOT else f"指向的不是当前运行的这份包（{PKG_ROOT}）"
            return Step(key, label, disp(dest), f"已是软链 → {pointed}", note=note)
        if not dest.is_symlink() and (dest / COPY_MARKER).is_file():
            # 从 copy 换成 link：旧副本是 drover 自己复制的，直接换掉，不备份。
            return Step(key, label, disp(dest), "drover 复制的旧副本（copy 安装）",
                        action=f"删掉副本，换成软链 → {PKG_ROOT}",
                        run=lambda: relink(dest, PKG_ROOT), level="fail")
        return plan_link(ctx, key, label, dest, PKG_ROOT, PKG_ROOT, by_resolve=True)

    if running_from_copy:
        return Step(key, label, disp(dest), "就是当前运行的这份副本")
    if dest.is_symlink():
        same = dest.exists() and same_tree(dest, PKG_ROOT)
        state = f"软链 → {os.readlink(dest)}（link 安装）"
        backup, level = (None if same else backup_target(dest)), "fail"
    elif dest.is_dir():
        marker = (dest / COPY_MARKER).exists()
        same = same_tree(dest, PKG_ROOT)
        if same and marker:
            return Step(key, label, disp(dest), "已是副本，和包内容一致")
        if marker:
            state, backup, level = "旧的 drover 副本，和包内容不同", None, "warn"
        else:
            state = "实体目录，" + ("内容和包相同" if same else "不是 drover 复制的")
            backup, level = (None if same else backup_target(dest)), "fail"
    elif dest.exists():
        state, backup, level = "实体文件", backup_target(dest), "fail"
    else:
        state, backup, level = "不存在", None, "fail"
    action = "复制包内容（排除 .git、__pycache__ 等）" + ("，替换旧副本" if state.startswith("旧的") else "")
    return Step(key, label, disp(dest), state, action=action, run=lambda: copy_package(dest), level=level,
                fs=dest, backup=backup)


def plan_config(ctx: Ctx) -> Step:
    key, label, path = "config", "本机配置", CONFIG_FILE
    if not ctx.workspace:  # 只有 --check 会走到这里：install 之前已经问过主工作台，或者按缺参数退出了
        state = ctx.config_error or ("不存在" if ctx.config_existing is None else "没有 workspace（主工作台）")
        return Step(key, label, disp(path), state, action="重跑 install，用 --workspace 指定主工作台", level="fail")
    if ctx.config_error:
        if not ctx.config_update:
            return Step(key, label, disp(path), ctx.config_error,
                        action="不动：先修好这个文件，或显式传 --workspace / --repo-dir 重写", level="fail")
        data = {"workspace": ctx.workspace, "repo_dirs": ctx.repo_dirs}
        if ctx.profile:
            data["profile"] = ctx.profile
        if "oncall" in ctx.config_update:
            data["oncall"] = ctx.config_update["oncall"]
        return Step(key, label, disp(path), ctx.config_error, action=f"重写成 {json.dumps(data, ensure_ascii=False)}",
                    run=lambda: write_json(path, data), level="fail", fs=path, backup=backup_target(path),
                    backup_how="copy")
    existing = ctx.config_existing
    if existing is None:
        data = dict(ctx.config_update)
        return Step(key, label, disp(path), "不存在", action=f"写入 {json.dumps(data, ensure_ascii=False)}",
                    run=lambda: write_json(path, data), level="fail")
    desired = dict(existing)
    desired.update(ctx.config_update)
    if "profile" in ctx.config_update and ctx.config_update["profile"] is None:
        desired.pop("profile", None)  # --profile none：去掉这个键
    keys = ["workspace", "repo_dirs"] + [k for k in ("profile", "oncall") if k in desired]
    shown = json.dumps({k: desired.get(k) for k in keys}, ensure_ascii=False)
    if desired == existing:
        return Step(key, label, disp(path), f"已有 {shown}（不覆盖）")
    removed = "，去掉 profile" if "profile" in existing and "profile" not in desired else ""
    return Step(key, label, disp(path), "已有，和这次显式传的参数不同",
                action=f"改成 {shown}{removed}（只改显式传的键）", run=lambda: write_json(path, desired), level="fail",
                fs=path, backup=backup_target(path), backup_how="copy")


def installed_pkg_root() -> Path:
    """装好的 bin/drover 算出的包根（它按名字解析 profile 时用的就是这个）；还没装时用这份包。"""
    return BIN_LINK.resolve().parent.parent if BIN_LINK.exists() else PKG_ROOT


def plan_profile(ctx: Ctx) -> Step:
    """团队 profile（config 的 profile 键）能不能解析到目录。解析不到时 drover playbook 报错、
    团队规则（迁移流程、编码规范）会丢，算没装对。安装时按这份包解析，--check 按装好的包解析。"""
    key, label = "profile", "团队 profile"
    if not ctx.profile:
        return Step(key, label, disp(CONFIG_FILE), "没配置 profile（只用包默认规则；要用团队规则时传 --profile <名字|目录>）")
    d = profile_dir(ctx.profile, installed_pkg_root() if ctx.checking else PKG_ROOT)
    if d.is_dir():
        return Step(key, label, disp(d), f"profile={ctx.profile}，目录在")
    return Step(key, label, disp(d),
                f"profile={ctx.profile}，目录不存在：drover playbook 会报错，团队规则（迁移流程、编码规范）读不到",
                action="python3 install.py --profile <名字|目录> 改成存在的，或 --profile none 去掉", level="fail")


# ---------------------------------------------------------------- 计划项：依赖（herdr、claude / codex 命令）


def shell_dir(d: Path) -> str:
    """写进 export PATH=… 的写法：HOME 下的写成 $HOME/…。"""
    s, h = str(d), str(HOME)
    return "$HOME" + s[len(h):] if s.startswith(h + os.sep) else s


def path_note(d: Path) -> str:
    return (f"herdr 装在 {disp(d)}，这个目录不在 PATH 里：drover CLI 按名字调用 herdr，不在 PATH 里会报找不到。"
            f"把 export PATH=\"{shell_dir(d)}:$PATH\" 加进 ~/.zshrc（bash 用 ~/.bashrc），开一个新终端再启动 herdr")


def plan_herdr(ctx: Ctx) -> Step:
    """herdr 是必需依赖。没装时按 ctx.deps_mode 决定：直接装、先问一句、还是只给命令。"""
    key, label = "herdr", "herdr 命令"
    found = shutil.which("herdr")
    if found:
        return Step(key, label, disp(found), "已装")
    off = herdr_off_path()
    if off:
        return Step(key, label, disp(off), f"已装，但 {disp(off.parent)} 不在 PATH 里", level="warn",
                    note=path_note(off.parent))
    cmd = herdr_install_cmd()
    state = "没装（PATH 和官方安装目录里都没有）"
    target = disp(herdr_fallback_dirs()[0] / "herdr")
    alt = f"或者 {HERDR_BREW_CMD}，见 {HERDR_INSTALL_DOCS}"
    if not os.environ.get(HERDR_INSTALLER_ENV):
        if platform.system() not in ("Darwin", "Linux"):
            return Step(key, label, target, state, action=f"官方安装脚本只支持 macOS / Linux，见 {HERDR_INSTALL_DOCS}",
                        level="fail")
        if not shutil.which("curl"):
            return Step(key, label, target, state, action=f"官方安装脚本要用 curl：先装 curl，{alt}", level="fail")
    if ctx.deps_mode == "no":
        return Step(key, label, target, state,
                    action=f"没自动装（不是交互终端，也没加 --install-deps）：手动执行 {cmd}，或者重跑时加 --install-deps",
                    level="fail", note=f"缺 herdr：官方安装命令 {cmd}（{alt}）；装好后重跑 install")
    ctx.herdr_planned = True
    how = "先问你一句 [Y/n]，同意就执行" if ctx.deps_mode == "ask" else "直接执行（--install-deps）"
    return Step(key, label, target, state, action=f"安装 herdr：{how} {cmd}", run=lambda: install_herdr(ctx),
                level="fail")


def plan_host_cli(ctx: Ctx, host: str) -> Step:
    """宿主的命令行本身：没有时只提示官方安装命令，不替你装（装 drover 的入口不需要它）。"""
    info = AGENT_INSTALL[host]
    key, label = f"{host}-cli", f"{info['name']} 命令"
    found = shutil.which(host)
    if found:
        return Step(key, label, disp(found), "已装")
    return Step(key, label, host, "PATH 里没有", action="只提示，不替你装", level="warn",
                note=f"没找到 {host} 命令：官方安装 {info['cmd']}（或 {info['alt']}，见 {info['docs']}）；"
                     "drover 的入口照样装，装好它之后不用重跑 install")


# ---------------------------------------------------------------- 计划项：Claude Code


def write_herdr_skill(ctx: Ctx, path: Path) -> str:
    text = herdr_skill_text()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    ctx.created.append(str(path))
    return f"写了 {len(text.splitlines())} 行"


def plan_claude_herdr_skill(ctx: Ctx) -> Step:
    key, label, path = "claude-herdr-skill", "Claude Code 的 herdr 技能", CLAUDE_HERDR_SKILL
    if path.exists():
        return Step(key, label, disp(path), "已有（不覆盖）")
    state = "坏软链" if path.is_symlink() else "不存在"
    if not herdr_bin() and not ctx.herdr_planned:
        return Step(key, label, disp(path), state, action="用 herdr --skill 的输出写一份：但 herdr 没装，装好后重跑",
                    level="fail")
    action = "用 herdr --skill 的输出写一份" if herdr_bin() else "装好 herdr 后，用 herdr --skill 的输出写一份"
    return Step(key, label, disp(path), state, action=action,
                run=lambda: write_herdr_skill(ctx, path), level="fail", fs=path,
                backup=backup_target(path) if path.is_symlink() else None)


def plan_rules(ctx: Ctx, key: str, label: str, path: Path, rules: list) -> Step:
    """permissions.allow 里只追加缺的规则，不删、不重排已有内容，JSON 其他键原样保留；改之前先备份。"""
    data, err = read_json(path)
    if err:
        return Step(key, label, disp(path), err, action="不动：先修好这个文件再重跑", level="fail")
    perms = (data or {}).get("permissions", {})
    allow = perms.get("allow", []) if isinstance(perms, dict) else None
    if not isinstance(allow, list):
        return Step(key, label, disp(path), "permissions / permissions.allow 不是预期的类型", action="不动：先手动修好",
                    level="fail")
    missing = [r for r in rules if r not in allow]
    if not missing:
        return Step(key, label, disp(path), f"{len(rules)} 条规则都在")

    def run() -> str:
        cur, cur_err = read_json(path)
        if cur_err:
            raise RuntimeError(cur_err)
        cur = cur if cur is not None else {}
        cur_perms = cur.setdefault("permissions", {})
        cur_allow = cur_perms.setdefault("allow", [])
        added = [r for r in rules if r not in cur_allow]
        cur_allow.extend(added)
        write_json(path, cur)
        if added:
            ctx.added_rules.setdefault(str(path), []).extend(added)
        if data is None:
            ctx.created.append(str(path))
        return f"加了 {len(added)} 条"

    state = "不存在" if data is None else f"缺 {len(missing)} 条（共 {len(rules)} 条）"
    return Step(key, label, disp(path), state, action="追加：" + "、".join(missing), run=run, level="fail",
                fs=path, backup=backup_target(path) if data is not None else None, backup_how="copy")


@dataclass
class HookSpec:
    """drover 往 ~/.claude/settings.json 加的一项 hook：hooks.<event> 里一组 {matcher, hooks: [{type, command, timeout}]}。
    命令一律是 python3 -I <装好的包里的脚本> [参数] || true：指向 ~/.drover/pkg（link / copy 下路径都不变），
    带 || true：脚本不在时（包删了、clone 切到了没有它的版本）python3 的退出码是 2，Claude Code 会把 2 当成"拦下"，
    PreToolUse 那项就会挡住所有 Bash；脚本自己出错也一律放行。-I：不读 PYTHONPATH 和用户 site-packages。"""
    key: str
    label: str
    event: str
    matcher: Optional[str]   # None：这一组不写 matcher（这个事件的所有情况都跑）
    script: Path             # 命令里跑的、装好的包里的文件（--check 要它在）
    args: str                # 跟在脚本后面的参数
    marker: str              # 命令里有它就认作 drover 加的（路径变了的旧项靠它认出来）
    timeout: int

    def command(self) -> str:
        return f"python3 -I {shlex.quote(str(self.script))}{' ' + self.args if self.args else ''} || true"

    def entry(self) -> dict:
        return {"type": "command", "command": self.command(), "timeout": self.timeout}

    def where(self) -> str:
        return f"{self.event}" + (f" / matcher {self.matcher}" if self.matcher else "（不设 matcher）")


CD_GIT_HOOK = HookSpec("claude-hook", "Claude Code 的 cd+git 拦截 hook", HOOK_EVENT, HOOK_MATCHER, HOOK_SCRIPT, "",
                       HOOK_SCRIPT_REL.name, HOOK_TIMEOUT)
SESSION_HOOK = HookSpec("claude-session-hook", "Claude Code 的主会话登记 hook（SessionStart）", SESSION_HOOK_EVENT, None,
                        BIN_TARGET, SESSION_HOOK_ARGS, SESSION_HOOK_ARGS, SESSION_HOOK_TIMEOUT)
PROMPT_HOOK = HookSpec("claude-prompt-hook", "Claude Code 的主会话读新规则 hook（UserPromptSubmit）", PROMPT_HOOK_EVENT, None,
                       BIN_TARGET, PROMPT_HOOK_ARGS, PROMPT_HOOK_ARGS, PROMPT_HOOK_TIMEOUT)


def hook_command() -> str:
    """cd+git 拦截 hook 的命令（--help 里和测试照着写的那一条）。"""
    return CD_GIT_HOOK.command()


def event_groups(data: dict, event: str) -> tuple[Optional[list], Optional[str]]:
    """settings 里 hooks.<event> 的各个 matcher 组（没有就是空列表），类型不对时返回错误。"""
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return None, "hooks 不是 JSON 对象"
    groups = hooks.get(event, [])
    if not isinstance(groups, list):
        return None, f"hooks.{event} 不是数组"
    return groups, None


def iter_hooks(groups: list):
    """(matcher 组, 组里的一项 hook)，跳过形状不对的。"""
    for g in groups:
        if isinstance(g, dict) and isinstance(g.get("hooks"), list):
            for h in g["hooks"]:
                if isinstance(h, dict):
                    yield g, h


def has_hook(groups: list, spec: HookSpec) -> bool:
    return any(g.get("matcher") == spec.matcher and h.get("type") == "command" and h.get("command") == spec.command()
               for g, h in iter_hooks(groups))


def recorded_hooks(state: dict, path: Path) -> list:
    return list((state.get("added_hooks") or {}).get(str(path), []))


def drover_hook(h: dict, recorded: list, spec: HookSpec) -> bool:
    """是不是 drover 加的那一项：命令就是现在要的、或者是安装记录里的，或者命令里有特征串（路径变了的旧项）。"""
    cmd = h.get("command")
    return isinstance(cmd, str) and (cmd == spec.command() or cmd in recorded or spec.marker in cmd)


def merge_hook(data: dict, recorded: list, spec: HookSpec) -> list:
    """把 drover 的 hook 合并进 data（就地改），返回换掉的旧命令。已有的 drover 旧项在 matcher 对得上的组里就地更新，
    别处的、重复的删掉（删空的组一并去掉）；都没有就在这个事件末尾追加一组。别的 hook、别的组原样保留。"""
    groups = data.setdefault("hooks", {}).setdefault(spec.event, [])
    want = spec.command()
    replaced, placed, emptied = [], False, []
    for g in groups:
        if not (isinstance(g, dict) and isinstance(g.get("hooks"), list)):
            continue
        kept = []
        for h in g["hooks"]:
            if not (isinstance(h, dict) and drover_hook(h, recorded, spec)):
                kept.append(h)
                continue
            if h.get("command") != want:
                replaced.append(h.get("command"))
            if not placed and g.get("matcher") == spec.matcher:
                h.update(spec.entry())
                kept.append(h)
                placed = True
        if len(kept) != len(g["hooks"]):
            g["hooks"] = kept
            if not kept:
                emptied.append(g)
    groups[:] = [g for g in groups if not any(g is e for e in emptied)]
    if not placed:
        groups.append({**({"matcher": spec.matcher} if spec.matcher else {}), "hooks": [spec.entry()]})
    return replaced


def plan_hook(ctx: Ctx, spec: HookSpec) -> Step:
    """~/.claude/settings.json 的 hooks.<event> 里要有 drover 的这一项 hook（命令指向装好的包）。只追加缺的这一项
    （脚本路径变了就更新那一项），其他 hook、这个事件的其他项、JSON 的其他键原样保留；改之前先备份。"""
    key, label, path = spec.key, spec.label, CLAUDE_SETTINGS
    want = spec.command()
    data, err = read_json(path)
    if err:
        return Step(key, label, disp(path), err, action="不动：先修好这个文件再重跑", level="fail")
    groups, err = event_groups(data or {}, spec.event)
    if err:
        return Step(key, label, disp(path), err, action="不动：先手动修好", level="fail")
    recorded = recorded_hooks(ctx.state, path)
    if has_hook(groups, spec):
        if ctx.checking and not spec.script.is_file():
            return Step(key, label, disp(path), f"hook 在，但脚本 {disp(spec.script)} 不存在（这时 hook 什么都不做）",
                        action="重跑 install", level="fail")
        return Step(key, label, disp(path), f"已有（{spec.where()} → {want}）")
    stale = [h.get("command") for _, h in iter_hooks(groups) if drover_hook(h, recorded, spec)]

    def run() -> str:
        cur, cur_err = read_json(path)
        if cur_err:
            raise RuntimeError(cur_err)
        cur = cur if cur is not None else {}
        _, cur_err = event_groups(cur, spec.event)
        if cur_err:
            raise RuntimeError(cur_err)
        replaced = merge_hook(cur, recorded, spec)
        write_json(path, cur)
        ctx.added_hooks.setdefault(str(path), []).append(want)
        if replaced:
            ctx.dropped_hooks.setdefault(str(path), []).extend(c for c in replaced if c != want)
        if data is None and str(path) not in ctx.created:
            ctx.created.append(str(path))
        return f"更新了 {len(replaced)} 项" if replaced else "加了 1 项"

    if stale:
        state, action = f"有 drover 加的旧项，命令不对（{'；'.join(map(str, stale))}）", f"更新那一项：{want}"
    else:
        state = "不存在" if data is None else f"{spec.event} 里没有这一项"
        action = f"追加：{spec.where()} → {want}（timeout {spec.timeout} 秒）"
    return Step(key, label, disp(path), state, action=action, run=run, level="fail",
                fs=path, backup=backup_target(path) if data is not None else None, backup_how="copy")


def share_backups(steps: list) -> None:
    """同一次运行里同一个文件只备份一次：第一项改之前备份的就是原文件（~/.claude/settings.json 的权限规则和 hook 两项）。"""
    seen = set()
    for st in steps:
        if st.run is None or st.fs is None:
            continue
        if st.fs in seen and st.backup is not None and st.backup_how == "copy":
            st.backup = None
            st.action += "（备份和前面改这个文件的那一项共用）"
        elif st.backup is not None:
            seen.add(st.fs)


def plan_claude_md(ctx: Ctx) -> Step:
    key, label = "claude-md", "主工作台 CLAUDE.md 引用 AGENTS.md"
    path = ctx.workspace_path / "CLAUDE.md"
    if not path.exists():
        def create() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(CLAUDE_MD_LINE + "\n")
            ctx.created.append(str(path))
        return Step(key, label, disp(path), "不存在", action=f"新建，只有一行 {CLAUDE_MD_LINE}", run=create, level="fail")
    text = path.read_text()
    if any(line.strip() == CLAUDE_MD_LINE for line in text.splitlines()):
        return Step(key, label, disp(path), f"已含 {CLAUDE_MD_LINE}")

    def append() -> None:
        cur = path.read_text()
        path.write_text(cur + ("" if not cur or cur.endswith("\n") else "\n") + CLAUDE_MD_LINE + "\n")

    return Step(key, label, disp(path), f"存在，没有 {CLAUDE_MD_LINE}", action=f"末尾追加一行 {CLAUDE_MD_LINE}",
                run=append, level="fail", fs=path, backup=backup_target(path), backup_how="copy")


def integration_install(agent: str) -> str:
    res = run_herdr(["integration", "install", agent])
    if res.returncode != 0:
        raise RuntimeError(f"herdr integration install {agent} 退出码 {res.returncode}：{(res.stderr or res.stdout).strip()[:200]}")
    return (res.stdout.strip().splitlines() or [""])[-1][:200]


def ensure_integration(ctx: Ctx, agent: str) -> str:
    """herdr 是这次才装上的：重新查一次状态，不是 current 再装。"""
    ctx.herdr_status, ctx.herdr_status_error = None, None
    status = herdr_integration_status(ctx)
    if status is None:
        raise RuntimeError(ctx.herdr_status_error or "查不到 herdr integration status")
    cur = status.get(agent, "没列出")
    return f"已是 {cur}" if cur.startswith("current") else integration_install(agent)


def plan_integration(ctx: Ctx, agent: str) -> Step:
    """herdr 靠 agent 状态 hook 识别 working / idle / blocked；不是 current 就装。"""
    key, label = f"{agent}-integration", f"herdr 的 {agent} 状态 hook"
    path = f"herdr integration（{agent}）"
    if ctx.herdr_planned and not herdr_bin():
        return Step(key, label, path, "herdr 还没装", level="fail",
                    action=f"装好 herdr 后：{agent} 的 hook 不是 current 就执行 herdr integration install {agent}",
                    run=lambda: ensure_integration(ctx, agent))
    status = herdr_integration_status(ctx)
    if status is None:
        return Step(key, label, path, ctx.herdr_status_error or "查不到", action=f"装好 herdr 后重跑（要执行 herdr integration install {agent}）",
                    level="fail")
    cur = status.get(agent, "没列出")
    if cur.startswith("current"):
        return Step(key, label, path, cur)
    return Step(key, label, path, cur, action=f"执行 herdr integration install {agent}",
                run=lambda: integration_install(agent), level="fail")


# ---------------------------------------------------------------- 计划项：Codex


def codex_sandbox_mode() -> Optional[str]:
    """config.toml 顶层（第一个 [表] 之前）的 sandbox_mode。不引 tomllib，3.9 也能跑。"""
    try:
        text = CODEX_CONFIG.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            break
        m = re.match(r"""sandbox_mode\s*=\s*["']([^"']+)["']""", s)
        if m:
            return m.group(1)
    return None


def plan_codex_herdr_skill(ctx: Ctx, claude_skill_planned: bool) -> Step:
    """已有就不动；没有时，有 ~/.claude/skills/herdr 就软链过去（和 Claude Code 共用一份），否则用 herdr --skill 写一份。"""
    key, label, dest = "codex-herdr-skill", "Codex 的 herdr 技能", CODEX_HERDR_SKILL
    if dest.exists():
        how = f"软链 → {disp(os.readlink(dest))}" if dest.is_symlink() else "实体目录"
        return Step(key, label, disp(dest), f"已有（{how}，不覆盖）")
    state = "坏软链" if dest.is_symlink() else "不存在"
    backup = backup_target(dest) if dest.is_symlink() else None
    claude_dir = CLAUDE_HERDR_SKILL.parent

    def run() -> str:
        if (claude_dir / "SKILL.md").is_file():
            make_link(dest, claude_dir)
            ctx.created.append(str(dest))
            return f"软链 → {disp(claude_dir)}"
        msg = write_herdr_skill(ctx, dest / "SKILL.md")
        ctx.created.append(str(dest))
        return msg

    if (claude_dir / "SKILL.md").is_file() or claude_skill_planned:
        action = f"软链 → {disp(claude_dir)}" + ("（Claude Code 那一项会先生成它）" if claude_skill_planned else "")
    elif herdr_bin():
        action = "用 herdr --skill 的输出写 SKILL.md"
    elif ctx.herdr_planned:
        action = "装好 herdr 后，用 herdr --skill 的输出写 SKILL.md"
    else:
        return Step(key, label, disp(dest), state, action="要用 herdr --skill 生成：但 herdr 没装，装好后重跑",
                    level="fail")
    return Step(key, label, disp(dest), state, action=action, run=run, level="fail", fs=dest, backup=backup)


def plan_codex_sandbox(ctx: Ctx) -> Step:
    key, label = "codex-sandbox", "Codex 默认沙箱"
    mode = codex_sandbox_mode()
    if mode == "danger-full-access":
        return Step(key, label, disp(CODEX_CONFIG), "sandbox_mode = danger-full-access")
    state = f"sandbox_mode = {mode}" if mode else "没设 sandbox_mode（默认不是 danger-full-access）"
    return Step(key, label, disp(CODEX_CONFIG), state, action="只提示，不自动改", level="warn",
                note="主会话和编排者要连 herdr 的 unix socket、写 ~/.drover 和 ~/.herdr、git 联网，workspace-write 沙箱下"
                     "连不上 herdr（Operation not permitted）。要在 Codex 里跑主会话，把 config.toml 顶层设成 "
                     "sandbox_mode = \"danger-full-access\"；编排者由 orch-start 自己带这个参数启动，不受影响。")


# ---------------------------------------------------------------- 计划项：飞书 oncall（内置的 herdr-oncall 插件）


def version_tuple(value) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(value or ""))[:3]) or (0,)


def node_candidates() -> list:
    """找 node 的地方（和插件的 bin/run-node.sh 大致一样，只看不会跑别的程序的那几处）：PATH、插件缓存的 node 路径、nvm。"""
    found = []
    which = shutil.which("node")
    if which:
        found.append(which)
    state_home = Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local" / "state")
    try:
        cached = (state_home / "herdr-oncall" / "node-path").read_text().strip()
    except OSError:
        cached = ""
    if cached:
        found.append(cached)
    nvm = sorted((HOME / ".nvm" / "versions" / "node").glob("*/bin/node"), key=lambda p: version_tuple(p.parent.parent.name))
    found += [str(p) for p in reversed(nvm)]
    return found


def node_info(ctx: Ctx) -> tuple:
    """(node 路径, 版本号)；找不到时 (None, None)。结果缓存在 ctx 里。"""
    if ctx.node is not None:
        return ctx.node
    ctx.node = (None, None)
    for exe in node_candidates():
        if not os.access(exe, os.X_OK):
            continue
        try:
            res = subprocess.run([exe, "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0 and res.stdout.strip():
            ctx.node = (exe, res.stdout.strip().lstrip("v"))
            break
    return ctx.node


def node_problem(ctx: Ctx) -> Optional[str]:
    """插件跑不了的原因（没有 Node 或低于 18）；能跑时 None。"""
    exe, version = node_info(ctx)
    if not exe:
        return f"没找到 Node（插件要 Node {ONCALL_NODE_MIN}+，收飞书回复要 {ONCALL_NODE_REPLIES}+）"
    if version_tuple(version)[0] < ONCALL_NODE_MIN:
        return f"Node {version} 太旧（插件要 {ONCALL_NODE_MIN}+，收飞书回复要 {ONCALL_NODE_REPLIES}+）"
    return None


def plan_oncall_node(ctx: Ctx) -> Step:
    key, label = "oncall-node", "Node（飞书 oncall 插件要用）"
    if not ctx.oncall:
        return Step(key, label, "node", f"{ONCALL_OFF}，不检查")
    exe, version = node_info(ctx)
    problem = node_problem(ctx)
    if problem:
        return Step(key, label, disp(exe) if exe else "node", problem, action=f"只检查不安装：{NODE_INSTALL_HINT}，再重跑",
                    level="fail", note=f"{problem}：插件跑不了。{NODE_INSTALL_HINT}；{ONCALL_SKIP_HINT}")
    if version_tuple(version)[0] < ONCALL_NODE_REPLIES:
        return Step(key, label, disp(exe), f"Node {version}", level="warn",
                    note=f"Node {version} 能推送，但收不到飞书里的回复（要 {ONCALL_NODE_REPLIES}+）：{NODE_INSTALL_HINT}")
    return Step(key, label, disp(exe), f"Node {version}")


def oncall_plugins(ctx: Ctx) -> Optional[list]:
    """herdr plugin list --json 的插件列表（一次查全部：本插件和旧 id 都从这里看）。查不了时 None，原因在 ctx.oncall_list_error。"""
    if ctx.oncall_plugins is not None or ctx.oncall_list_error is not None:
        return ctx.oncall_plugins
    try:
        res = run_herdr(["plugin", "list", "--json"])
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        ctx.oncall_list_error = str(exc)
        return None
    if res.returncode != 0:
        ctx.oncall_list_error = (f"herdr plugin list 退出码 {res.returncode}：{(res.stderr or res.stdout).strip()[:200]}"
                                 "（herdr 没在运行时，先在终端里运行 herdr）")
        return None
    try:
        data = json.loads(res.stdout)
        plugins = (data.get("result") or data).get("plugins")
    except (ValueError, AttributeError):
        plugins = None
    if not isinstance(plugins, list):
        ctx.oncall_list_error = "herdr plugin list --json 的输出里没有 plugins 列表"
        return None
    ctx.oncall_plugins = [p for p in plugins if isinstance(p, dict)]
    return ctx.oncall_plugins


def oncall_plugin(ctx: Ctx) -> Optional[dict]:
    return next((p for p in oncall_plugins(ctx) or [] if p.get("plugin_id") == ONCALL_PLUGIN_ID), None)


def forget_plugins(ctx: Ctx) -> None:
    ctx.oncall_plugins, ctx.oncall_list_error = None, None


def oncall_choice(ctx: Ctx) -> Optional[bool]:
    """本机对飞书 oncall 的选择：这次传的 --oncall / --no-oncall，否则 config.json 的 oncall 键（"本机的选择"，和 profile
    一样卸载重装都保留）；都没有（或写的 null）是 None。"""
    value = {**(ctx.config_existing or {}), **ctx.config_update}.get("oncall")
    return None if value is None else bool(value)


def oncall_enabled(ctx: Ctx) -> bool:
    """接不接飞书 oncall。选装（以前不传参数就装，没用飞书的也被装上插件）：按 oncall_choice；没选过时，已经接上的照旧接
    （drover update 重跑 install 只带 --mode / --host，不能把它停掉）：安装记录里有 oncall（drover 装过插件或写过
    drover.env），或者 herdr 里装着插件（查不了算没装）。都不是就不接，新装默认不装。插件配置目录里的 .env 不看：
    插件没装时要知道目录得调会建目录的 config-dir，插件装着时上一条已经算接了。"""
    choice = oncall_choice(ctx)
    if choice is not None:
        return choice
    return bool(ctx.state.get("oncall")) or oncall_plugin(ctx) is not None


def oncall_switch_off(plugin: dict) -> bool:
    """插件自己的开关（drover oncall off）关着没有：只有带 status 脚本的版本才看得到，看不到就当开着。"""
    root = plugin.get("plugin_root") or ""
    if not root or not (Path(root) / ONCALL_STATUS_SCRIPT).is_file():
        return False
    try:
        res = subprocess.run(["/bin/bash", str(Path(root) / "bin" / "run-node.sh"), ONCALL_STATUS_SCRIPT], cwd=root,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        lines = [line for line in res.stdout.splitlines() if line.strip()]
        data = json.loads(lines[-1]) if res.returncode == 0 and lines else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return isinstance(data, dict) and data.get("enabled") is False


def herdr_plugin_cmd(*args: str) -> str:
    return "herdr " + shlex.join(["plugin", *args])


ONCALL_INSTALL_ARGS = ["install", ONCALL_REPO, "--ref", ONCALL_REF, "--yes"]
ONCALL_UPDATE_ARGS = ["update", ONCALL_PLUGIN_ID, "--yes"]


def run_plugin_cmd(args: list, what: str) -> str:
    try:
        res = run_herdr(["plugin", *args], timeout=ONCALL_INSTALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{herdr_plugin_cmd(*args)} 超过 {ONCALL_INSTALL_TIMEOUT} 秒没跑完；{ONCALL_SKIP_HINT}")
    out = "；".join(line.strip() for line in (res.stdout + "\n" + res.stderr).strip().splitlines()[-3:] if line.strip())
    if res.returncode != 0:
        raise RuntimeError(f"{herdr_plugin_cmd(*args)} 退出码 {res.returncode}：{out[:300]}。{what}；{ONCALL_SKIP_HINT}")
    return out[:200] or "完成"


def install_oncall(ctx: Ctx) -> str:
    msg = run_plugin_cmd(ONCALL_INSTALL_ARGS, "常见原因：访问不了 GitHub、herdr 没在运行、没有 Node")
    ctx.oncall_installed_now = True
    forget_plugins(ctx)
    return msg


def update_oncall(ctx: Ctx) -> str:
    msg = run_plugin_cmd(ONCALL_UPDATE_ARGS, "常见原因：访问不了 GitHub、herdr 没在运行")
    forget_plugins(ctx)
    return msg


def ensure_oncall_plugin(ctx: Ctx) -> str:
    """herdr 是这次才装上的：重新查一次，没装就装（Node 不满足时报失败）。"""
    forget_plugins(ctx)
    if oncall_plugins(ctx) is None:
        raise RuntimeError(f"{ctx.oncall_list_error}；{ONCALL_SKIP_HINT}")
    plugin = oncall_plugin(ctx)
    if plugin:
        return f"已装 {plugin.get('version')}（{(plugin.get('source') or {}).get('kind')}）"
    problem = node_problem(ctx)
    if problem:
        raise RuntimeError(f"没装插件：{problem}；{ONCALL_SKIP_HINT}")
    return install_oncall(ctx)


def plan_oncall_plugin(ctx: Ctx) -> Step:
    """herdr-oncall 插件：没装就从 GitHub 装（跟 stable，之后插件自己更新）；GitHub 装的版本不够就 update；本地链接的
    不动（版本低时提示 git pull）；在 herdr 里被禁用的、插件自己的开关关着的，不替你打开，只提示。"""
    key, label = "oncall-plugin", "herdr-oncall（drover 内置的通知插件）"
    path = f"herdr 插件 {ONCALL_PLUGIN_ID}"
    if not ctx.oncall:
        return Step(key, label, path, ONCALL_OFF)
    install_text = herdr_plugin_cmd(*ONCALL_INSTALL_ARGS)
    if ctx.herdr_planned and not herdr_bin():
        return Step(key, label, path, "herdr 还没装", level="fail", run=lambda: ensure_oncall_plugin(ctx),
                    action=f"装好 herdr 后：没装插件就执行 {install_text}")
    plugins = oncall_plugins(ctx)
    if plugins is None:
        return Step(key, label, path, f"查不了：{ctx.oncall_list_error}", action="装好 / 打开 herdr 后重跑",
                    level="fail", note=f"查不了插件：{ctx.oncall_list_error}。{ONCALL_SKIP_HINT}")
    legacy = [p.get("plugin_id") for p in plugins if p.get("plugin_id") in ONCALL_LEGACY_IDS]
    legacy_note = (f"还装着旧的插件 id（{'、'.join(legacy)}）：新插件装好后会自己解除它们；没解除的话 herdr plugin uninstall "
                   f"{legacy[0]}" if legacy else "")
    plugin = oncall_plugin(ctx)
    if plugin is None:
        problem = node_problem(ctx)
        if problem:
            return Step(key, label, path, "没装", action=f"没法装：{problem}", level="fail",
                        note=f"飞书 oncall 没装：{problem}。{NODE_INSTALL_HINT}，再重跑；{ONCALL_SKIP_HINT}")
        return Step(key, label, path, "没装", run=lambda: install_oncall(ctx), level="fail", note=legacy_note,
                    action=f"执行 {install_text}（从 GitHub 装，之后插件每几个小时自己跟 stable 更新）")
    version = plugin.get("version")
    source = (plugin.get("source") or {}).get("kind") or "未知来源"
    root = plugin.get("plugin_root") or ""
    desc = f"已装 {version}（{'本地链接 ' + disp(root) if source == 'local' else source}）"
    low = version_tuple(version) < version_tuple(ONCALL_MIN_VERSION)
    if plugin.get("enabled") is False:
        return Step(key, label, path, desc + "，在 herdr 里被禁用了", action="不替你打开（也不更新）", level="warn",
                    note="；".join(filter(None, [f"插件被禁用了，飞书和桌面面板都不会响；要用就 herdr plugin enable "
                                                f"{ONCALL_PLUGIN_ID}，再重跑 install", legacy_note])))
    notes = [legacy_note] if legacy_note else []
    if not low and oncall_switch_off(plugin):
        notes.append("插件自己的开关关着（不推送、不收飞书消息）：要用就 ~/.drover/bin/drover oncall on")
    note = "；".join(notes)
    if low and source == "local":
        return Step(key, label, path, f"{desc}，低于 {ONCALL_MIN_VERSION}", action="不动（本地链接，install 不更新它）",
                    level="warn", note="；".join(filter(None, [
                        f"本地链接的插件版本 {version} 低于 {ONCALL_MIN_VERSION}（还没有按角色通知）：到 {disp(root)} 里 git pull", note])))
    if low:
        return Step(key, label, path, f"{desc}，低于 {ONCALL_MIN_VERSION}", level="warn", note=note,
                    action=f"执行 {herdr_plugin_cmd(*ONCALL_UPDATE_ARGS)}", run=lambda: update_oncall(ctx))
    return Step(key, label, path, desc, level="warn" if notes else "ok", note=note)


def oncall_config_dir() -> Path:
    """插件配置目录（herdr plugin config-dir 的输出）。这条命令会建目录：只在插件已经装着时调。"""
    res = run_herdr(["plugin", "config-dir", ONCALL_PLUGIN_ID])
    folder = res.stdout.strip() if res.returncode == 0 else ""
    if not folder:
        raise RuntimeError(f"herdr plugin config-dir {ONCALL_PLUGIN_ID} 退出码 {res.returncode}："
                           f"{(res.stderr or res.stdout).strip()[:200]}")
    return Path(folder)


def write_oncall_env(ctx: Ctx, path: Optional[Path] = None) -> str:
    """整份写 drover.env（先写临时文件再改名）。同目录的 .env（用户的飞书配对、密钥）不读不写。"""
    if path is None:
        forget_plugins(ctx)
        if oncall_plugin(ctx) is None:
            raise RuntimeError("插件没装上，drover.env 没写（见上一项）")
        path = oncall_config_dir() / ONCALL_ENV_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{TS}")
    tmp.write_text(ONCALL_ENV_TEXT)
    os.replace(tmp, path)
    ctx.oncall_env_path = str(path)
    return f"写了 {disp(path)}"


def plan_oncall_env(ctx: Ctx, plugin_step: Step) -> Step:
    """插件配置目录里的 drover.env：drover 的那几项配置（主会话名、编排者前缀、升级阈值、不引用的消息送主会话、
    主会话的回答只推飞书提问的回答和以【转达】开头的转达）。
    插件还没装（包括这次才装）时不调 config-dir（它会建目录），路径先写成 <插件配置目录>/drover.env。"""
    key, label = "oncall-env", "drover.env（drover 给插件的配置）"
    placeholder = f"<插件配置目录>/{ONCALL_ENV_NAME}"
    if not ctx.oncall:
        return Step(key, label, placeholder, "不接飞书，不写")
    plugin = oncall_plugin(ctx) if not (ctx.herdr_planned and not herdr_bin()) else None
    if plugin is None:
        if plugin_step.run is not None:
            return Step(key, label, placeholder, "插件还没装", run=lambda: write_oncall_env(ctx), level="fail",
                        action=f"插件装好后写入（路径：herdr plugin config-dir {ONCALL_PLUGIN_ID} 的输出下）")
        return Step(key, label, placeholder, "插件没装，写不了", action="插件装好后重跑", level="fail")
    try:
        path = oncall_config_dir() / ONCALL_ENV_NAME
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return Step(key, label, placeholder, f"查不到插件配置目录：{exc}", action="不动", level="fail")
    ctx.oncall_env_path = str(path)
    try:
        current = path.read_text() if path.exists() else None
    except OSError as exc:
        return Step(key, label, disp(path), f"读不了：{exc}", action="不动", level="fail")
    if current == ONCALL_ENV_TEXT:
        return Step(key, label, disp(path), "已是这版 drover 要写的内容")
    if current is None:
        return Step(key, label, disp(path), "不存在", action="写入（整份，同目录的 .env 不碰）",
                    run=lambda: write_oncall_env(ctx, path), level="fail")
    return Step(key, label, disp(path), "和这版 drover 要写的不同", action="整份覆盖（drover.env 归 drover 管；同目录的 .env 不碰）",
                run=lambda: write_oncall_env(ctx, path), level="warn")


# ---------------------------------------------------------------- 计划项：验收工具（crapper、dryer）


def quality_tools_enabled(config: Optional[dict]) -> bool:
    """装不装验收工具：DROVER_QUALITY_TOOLS=0 / false / off，或 config.json 的 quality_tools 是 false 时不装（默认装）。"""
    if os.environ.get(QUALITY_SWITCH_ENV, "").strip().lower() in ("0", "false", "off", "no"):
        return False
    return (config or {}).get("quality_tools") is not False


def quality_tools_spec() -> dict:
    """{工具名: (clone 地址, 钉的提交)}；自测时由 DROVER_QUALITY_TOOLS_SOURCE 换成本地仓库。"""
    raw = os.environ.get(QUALITY_TOOLS_ENV)
    return {name: tuple(v) for name, v in json.loads(raw).items()} if raw else QUALITY_TOOLS


def tool_cmd(cmd: list, what: str, env: Optional[dict] = None, timeout: float = TOOLS_TIMEOUT) -> str:
    """装工具的一步：成功返回输出；失败、超时、跑不起来都抛 RuntimeError（带最后几行输出）。"""
    try:
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})})
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{what} 超过 {timeout} 秒没跑完")
    except OSError as exc:
        raise RuntimeError(f"{what} 跑不起来：{exc}")
    if res.returncode != 0:
        last = "；".join(line.strip() for line in res.stdout.strip().splitlines()[-3:] if line.strip())
        raise RuntimeError(f"{what} 退出码 {res.returncode}：{last[:300]}")
    return res.stdout


def python_version(exe: str) -> Optional[tuple]:
    try:
        res = subprocess.run([exe, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.fullmatch(r"(\d+)\.(\d+)", res.stdout.strip()) if res.returncode == 0 else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def uv_bin() -> Optional[str]:
    """drover 自己装的那份 uv 优先，再找 PATH。"""
    return str(TOOLS_UV) if os.access(str(TOOLS_UV), os.X_OK) else shutil.which("uv")


def uv_python_env() -> dict:
    return {"UV_PYTHON_INSTALL_DIR": str(TOOLS_PYTHON_DIR)}


def find_tools_python() -> Optional[tuple]:
    """(路径, (主, 次)) of 第一个 3.11+ 的 python：先 PATH 上的 python3.14…python3.11、python3，再问 uv（uv python find，
    它也找 drover 装进 ~/.drover/tools/python 的那份）。都没有返回 None。"""
    candidates = [shutil.which(name) for name in TOOLS_PYTHON_NAMES]
    uv = uv_bin()
    if uv:
        with contextlib.suppress(RuntimeError):
            candidates.append(tool_cmd([uv, "python", "find", ">=3.11"], "uv python find", env=uv_python_env(),
                                       timeout=30).strip())
    for exe in filter(None, candidates):
        ver = python_version(exe)
        if ver and ver >= TOOLS_PYTHON_MIN:
            return exe, ver
    return None


def uv_install_cmd() -> str:
    return os.environ.get(UV_INSTALLER_ENV) or UV_INSTALL_CMD


def install_tools_python(ctx: Ctx) -> str:
    """本机没有 3.11+：没有 uv 先装 drover 私有的一份，再 uv python install 进 drover 的目录。失败只记警告。"""
    try:
        uv = uv_bin()
        if not uv:
            tool_cmd(["sh", "-c", uv_install_cmd()], f"装 uv（{uv_install_cmd()}）",
                     env={"UV_INSTALL_DIR": str(TOOLS_UV.parent), "UV_NO_MODIFY_PATH": "1"})
            uv = uv_bin()
            if not uv:
                raise RuntimeError(f"uv 的安装脚本跑完了，但 {disp(TOOLS_UV)} 不在")
        tool_cmd([uv, "python", "install", TOOLS_PYTHON_VERSION, "--install-dir", str(TOOLS_PYTHON_DIR), "--no-bin"],
                 f"uv python install {TOOLS_PYTHON_VERSION}", env=uv_python_env())
        found = find_tools_python()
        if not found:
            raise RuntimeError(f"uv python install 跑完了，但还是找不到 3.11+ 的 python（{disp(TOOLS_PYTHON_DIR)}）")
    except RuntimeError as exc:
        raise StepWarning(f"{exc}。也可以自己装一个 Python 3.11+（brew install python@3.12，或 python.org）；{TOOLS_MANUAL}")
    ctx.tools_python = found[0]
    return f"Python {found[1][0]}.{found[1][1]}：{disp(found[0])}"


def tool_head(d: Path) -> str:
    with contextlib.suppress(RuntimeError):
        return tool_cmd(["git", "-C", str(d), "rev-parse", "HEAD"], "git rev-parse", timeout=30).strip()
    return ""


def has_commit(d: Path, pin: str) -> bool:
    with contextlib.suppress(RuntimeError):
        return tool_cmd(["git", "-C", str(d), "cat-file", "-t", pin], "git cat-file", timeout=30).strip() == "commit"
    return False


def tool_state(name: str, pin: str) -> tuple:
    """(现状, 能不能用)：目录在、HEAD 是钉的提交、.venv 里 python -m <工具> --help 跑得通才算能用。"""
    d = TOOLS_DIR / name
    if not d.exists():
        return "没装", False
    head = tool_head(d)
    if head != pin:
        return f"版本不对（{head[:12] or '不是 git 仓库'}，要 {pin[:12]}）", False
    try:
        tool_cmd([str(d / ".venv" / "bin" / "python"), "-m", name, "--help"], f"{name} --help", timeout=60)
    except RuntimeError as exc:
        return f".venv 用不了（{exc}）", False
    return f"已装 {pin[:12]}", True


def ensure_tool(ctx: Ctx, name: str, url: str, pin: str) -> str:
    """clone（或 fetch）→ 切到钉的提交 → 用 3.11+ 的 python 重建 .venv、pip install -e（同工具自带入口脚本的装法）→ --help。
    哪一步失败都删掉半截的 .venv（工具的入口脚本只看 .venv/bin/python 在不在，留着就一直坏），只记警告。"""
    d, venv = TOOLS_DIR / name, TOOLS_DIR / name / ".venv"
    try:
        if not ctx.tools_python:
            raise RuntimeError("没有 Python 3.11+（见上一项）")
        if not (d / ".git").exists():
            shutil.rmtree(d, ignore_errors=True)
            tool_cmd(["git", "clone", "--quiet", url, str(d)], f"git clone {url}")
        if not has_commit(d, pin):
            tool_cmd(["git", "-C", str(d), "fetch", "--quiet", "origin"], "git fetch")
        tool_cmd(["git", "-C", str(d), "checkout", "--quiet", "--detach", pin], f"git checkout {pin[:12]}")
        shutil.rmtree(venv, ignore_errors=True)
        tool_cmd([ctx.tools_python, "-m", "venv", str(venv)], f"{ctx.tools_python} -m venv")
        tool_cmd([str(venv / "bin" / "python"), "-m", "pip", "install", "--quiet", "-e", str(d)], "pip install -e")
        tool_cmd([str(venv / "bin" / "python"), "-m", name, "--help"], f"{name} --help", timeout=60)
    except RuntimeError as exc:
        shutil.rmtree(venv, ignore_errors=True)
        raise StepWarning(f"{name} 没装上：{exc}；{TOOLS_MANUAL}")
    return f"装好了 {pin[:12]}"


def plan_tools_python(ctx: Ctx, needed: bool) -> Step:
    """验收工具用的 Python 3.11+。needed：有工具要（重）装。"""
    key, label = "tools-python", "验收工具用的 Python 3.11+"
    if not ctx.quality_tools:
        return Step(key, label, "python3.11+", QUALITY_OFF)
    if not needed:
        return Step(key, label, "python3.11+", "用不着（两个工具都装好了）")
    found = find_tools_python()
    if found:
        ctx.tools_python = found[0]
        return Step(key, label, disp(found[0]), f"Python {found[1][0]}.{found[1][1]}")
    uv = uv_bin()
    if not uv and not os.environ.get(UV_INSTALLER_ENV) and not shutil.which("curl"):
        return Step(key, label, disp(TOOLS_PYTHON_DIR), "没找到 3.11+，也没有 uv、curl", level="warn",
                    action="只提示", note=f"验收工具要 Python 3.11+：装一个（brew install python@3.12，或 python.org）；{TOOLS_MANUAL}")
    first = "" if uv else (f"先装 uv：{uv_install_cmd()}（UV_INSTALL_DIR={disp(TOOLS_UV.parent)}、UV_NO_MODIFY_PATH=1："
                           "drover 私有的一份，不改 shell 配置），再 ")
    return Step(key, label, disp(TOOLS_PYTHON_DIR), "没找到 3.11+（PATH 上的版本不够，uv 也没找到）",
                action=f"{first}uv python install {TOOLS_PYTHON_VERSION} --install-dir {disp(TOOLS_PYTHON_DIR)} --no-bin"
                       "（只装进 drover 的目录，不改 PATH）；装不上只记警告",
                run=lambda: install_tools_python(ctx), level="warn")


def plan_quality_tools(ctx: Ctx) -> list:
    """验收工具：先 Python 3.11+，再每个工具一项。装不上都只记警告；--check 里缺了只算提示。"""
    if not ctx.quality_tools:
        return [plan_tools_python(ctx, False)] + [Step(f"tool-{name}", f"验收工具 {name}", disp(TOOLS_DIR / name), QUALITY_OFF)
                                                  for name in quality_tools_spec()]
    tools = []
    for name, (url, pin) in quality_tools_spec().items():
        state, ok = tool_state(name, pin)
        d = TOOLS_DIR / name
        step = Step(f"tool-{name}", f"验收工具 {name}", disp(d), state)
        if not ok:
            step.action = (f"{'' if d.exists() else f'clone {url}，'}切到 {pin[:12]}，用 Python 3.11+ 重建 .venv、"
                           "pip install -e，跑一次 --help；装不上只记警告")
            step.run, step.level = (lambda n=name, u=url, p=pin: ensure_tool(ctx, n, u, p)), "warn"
        tools.append(step)
    return [plan_tools_python(ctx, any(s.run for s in tools))] + tools


def unplan_tools() -> Step:
    """卸载：~/.drover/tools 整个是 drover 的（两个工具、它们用的 Python / uv、语法包），一起删。"""
    key, label = "quality-tools", "验收工具（crapper、dryer）"
    if not TOOLS_DIR.exists():
        return Step(key, label, disp(TOOLS_DIR), "不存在")
    return Step(key, label, disp(TOOLS_DIR), "在", action="删除整个目录（两个工具、它们用的 Python / uv、语法包）",
                run=lambda: shutil.rmtree(TOOLS_DIR))


# ---------------------------------------------------------------- 装完当场引导配对飞书（只在交互终端里）


def drover_cli(*args: str, timeout: int = 120) -> dict:
    """跑这份包里的 bin/drover（和刚装好的是同一份代码），读它输出的 JSON。"""
    try:
        res = subprocess.run([sys.executable, str(PKG_ROOT / "bin" / "drover"), *args], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=timeout)
        data = json.loads(res.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"ok": False, "error": f"drover {' '.join(args)} 没给出结果：{exc}"}
    return data if isinstance(data, dict) else {"ok": False, "error": f"drover {' '.join(args)} 的输出不是 JSON 对象"}


def feishu_state() -> Optional[str]:
    """飞书配没配（drover oncall status）：paired / unpaired。插件没装、版本不够（没有 status 脚本）、在 herdr 里被禁用、
    查不了时 None（不引导，汇总里照常提示 drover oncall pair）。"""
    out = drover_cli("oncall", "status")
    plugin = (out.get("drover") or {}).get("plugin") or {}
    if not out.get("ok") or not plugin.get("version_ok") or plugin.get("herdr_enabled") is False:
        return None
    paired = (((out.get("plugin_status") or {}).get("channels") or {}).get("feishu") or {}).get("paired")
    return "paired" if paired is True else "unpaired" if paired is False else None


def should_check_feishu(ctx: Ctx, args: argparse.Namespace, steps: list) -> bool:
    """当场引导配对的前提：交互终端（stdin、stdout 都是终端，不是 --json）、正式安装（不是 dry-run）、接飞书、
    插件这一项没失败。agent 帮装（非交互）时不问，配对由 docs/agent-install.md 第 7 步做。"""
    if args.json or ctx.dry_run or not ctx.oncall or not interactive(args):
        return False
    st = next((s for s in steps if s.key == "oncall-plugin"), None)
    return st is not None and not st.result.startswith(("失败", "没法处理"))


def draw_qr(link: str) -> None:
    """终端里画二维码：有 qrencode 就用它，没有就只给链接（不新增依赖）。"""
    exe = shutil.which("qrencode")
    if exe:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run([exe, "-t", "UTF8", link], timeout=15)


def wait_until_paired(seconds: float) -> bool:
    deadline = time.time() + seconds
    while True:
        if feishu_state() == "paired":
            return True
        if time.time() >= deadline:
            return False
        time.sleep(min(PAIR_POLL, max(0.1, deadline - time.time())))


def offer_pairing() -> None:
    """装完、飞书还没配对：问一句，默认是。发起配对 → 打印链接（能画就画二维码）→ 等扫码确认 → 发一条测试消息。"""
    print()
    print("飞书 oncall：配好以后，离开电脑时主会话需要你（等你确认、编排者要你拍板或做完了）会推到飞书，"
          "你在飞书里直接发消息就是在跟主会话说话。")
    try:
        yes = confirm("现在用飞书扫码配对吗？[Y/n] ")
    except KeyboardInterrupt:
        print()
        yes = False
    if not yes:
        print(f"先不配。之后想配：{PAIR_CMD}")
        return
    print("发起配对，等插件给出配对链接（可能要二三十秒）……", flush=True)
    out = drover_cli("oncall", "pair")  # 等插件这次的动作跑完再读链接（bin/drover 里最多等 45 秒）
    if not out.get("ok"):
        print(f"没发起配对：{out.get('error')}。之后可以跑 {PAIR_CMD} 重来")
        return
    if out.get("already_paired"):
        print("飞书已配对。")
        return
    link = out.get("link")
    if link:
        print("Mac 桌面也会弹出二维码；没弹的话，用手机飞书扫下面的二维码，或者在手机飞书里打开这个链接：")
        print(f"  {link}", flush=True)
        draw_qr(link)
    else:
        print(f"Mac 桌面会弹出二维码，用手机飞书扫一下。没读到配对链接：{out.get('hint')}")
    print(f"等你在飞书里扫码、点确认（最多 {max(1, int(PAIR_WAIT // 60))} 分钟，Ctrl-C 跳过）……", flush=True)
    try:
        paired = wait_until_paired(PAIR_WAIT)
    except KeyboardInterrupt:
        print()
        print(f"跳过了等待。扫完后跑 ~/.drover/bin/drover oncall status 确认已配对，再 ~/.drover/bin/drover oncall test "
              f"发一条试试；没扫就重跑 {PAIR_CMD}")
        return
    if not paired:
        print(f"没等到配对（二维码可能过期了）。之后跑 {PAIR_CMD} 重来。")
        return
    test = drover_cli("oncall", "test")
    if test.get("ok"):
        print("飞书配对好了，刚发了一条测试消息，飞书里应该收到了。")
    else:
        print(f"飞书配对好了，但测试消息没发出去：{test.get('error')}。之后可以跑 ~/.drover/bin/drover oncall test")


# ---------------------------------------------------------------- 计划项：路由区块、安装记录


def plan_routing(ctx: Ctx) -> Step:
    key, label = "routing", "主工作台路由区块"
    path = ctx.workspace_path / "AGENTS.md"
    try:
        block = render_block(ctx.workspace, str(ctx.workspace_path))
    except (OSError, BlockError) as exc:
        return Step(key, label, disp(path), f"渲染 flow/routing.md 失败：{exc}", action="不动", level="fail")
    if not path.exists():
        def create() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(block + "\n")
            ctx.created.append(str(path))
        note = "" if ctx.workspace_path.is_dir() else f"主工作台目录 {disp(ctx.workspace_path)} 还不存在，会一起新建"
        return Step(key, label, disp(path), "不存在", action="新建，只含 drover 路由区块", run=create, level="fail",
                    note=note)
    text = path.read_text()
    try:
        new, how = merge_block(text, block)
    except BlockError as exc:
        return Step(key, label, disp(path), str(exc), action="不动", level="fail")
    if new == text:
        return Step(key, label, disp(path), "区块已是最新")
    if how == "replace":
        return Step(key, label, disp(path), "有区块，和 flow/routing.md 当前的渲染结果不同",
                    action="整段替换区块（区块外内容不动）", run=lambda: path.write_text(new), level="warn")
    return Step(key, label, disp(path), "存在，没有 drover 区块标记", action="在末尾追加区块（原有内容不动）",
                run=lambda: path.write_text(new), level="fail", fs=path, backup=backup_target(path), backup_how="copy",
                note="已有内容可能和路由规则重复，请检查：通用规则删掉、只留个人内容（install 不去猜哪段是通用规则）")


def merged_state(ctx: Ctx) -> dict:
    """新的安装记录 = 上次的记录 + 这次的宿主、模式、新建文件、加的规则和 hook、备份。"""
    old = ctx.state
    rules = {k: list(v) for k, v in (old.get("added_rules") or {}).items()}
    for path, added in ctx.added_rules.items():
        rules[path] = rules.get(path, []) + [r for r in added if r not in rules.get(path, [])]
    hooks = {k: list(v) for k, v in (old.get("added_hooks") or {}).items()}
    for path, dropped in ctx.dropped_hooks.items():
        hooks[path] = [c for c in hooks.get(path, []) if c not in dropped]
    for path, added in ctx.added_hooks.items():
        hooks[path] = hooks.get(path, []) + [c for c in added if c not in hooks.get(path, [])]
    hooks = {k: v for k, v in hooks.items() if v}
    created = list(old.get("created") or [])
    created += [p for p in ctx.created if p not in created]
    workspaces = list(old.get("workspaces") or [])
    if ctx.workspace not in workspaces:
        workspaces.append(ctx.workspace)
    data = {
        "source": str(PKG_ROOT),
        "mode": ctx.mode,
        "hosts": [h for h in HOSTS if h in set(old.get("hosts") or []) | set(ctx.hosts)],
        "workspaces": workspaces,
        "created": created,
        "added_rules": rules,
        "added_hooks": hooks,
        "backups": list(old.get("backups") or []) + [str(p) for p in ctx.backups],
    }
    # 飞书 oncall：插件是不是 drover 装的（--uninstall 只卸 drover 装的）、drover.env 在哪（--uninstall 按它删）。
    old_oncall = old.get("oncall") or {}
    oncall = {"installed_by_drover": bool(old_oncall.get("installed_by_drover") or ctx.oncall_installed_now),
              "env": ctx.oncall_env_path or old_oncall.get("env")}
    if oncall["installed_by_drover"] or oncall["env"]:
        data["oncall"] = oncall
    return data


def plan_state(ctx: Ctx) -> Step:
    """安装记录放在最后一项：run 时前面各项已经执行完，ctx 里有这次新建的文件和加的规则。"""
    def run() -> None:
        data = merged_state(ctx)
        data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        write_json(STATE_FILE, data)
    return Step("state", "安装记录", disp(STATE_FILE), "已有" if STATE_FILE.exists() else "不存在",
                action="写入（宿主、模式、新建的文件、加的规则和 hook、备份；--uninstall 按它撤）", run=run)


def plan_chain() -> Step:
    """只在 --check 里：从 ~/.drover/bin/drover 一路解析下去，确认 bin/drover 算出的 PKG_ROOT 是一份完整的包。"""
    key, label = "chain", "drover 链路"
    if not BIN_LINK.exists():
        return Step(key, label, disp(BIN_LINK), "解析不到脚本", action="重跑 install", level="fail")
    real = BIN_LINK.resolve()
    root = real.parent.parent
    if not os.access(str(real), os.X_OK):
        return Step(key, label, disp(BIN_LINK), f"{real} 没有执行权限", action="chmod +x 或重跑 install", level="fail")
    if not (root / "flow" / "dispatch.md").is_file():
        return Step(key, label, disp(BIN_LINK), f"PKG_ROOT={root} 下没有 flow/dispatch.md", action="重跑 install",
                    level="fail")
    return Step(key, label, disp(BIN_LINK), f"→ {real}；PKG_ROOT={root}，flow/dispatch.md 在")


# ---------------------------------------------------------------- 组装计划、执行、输出


def plan_no_workspace(key: str, label: str, name: str) -> Step:
    """--check 时没有主工作台（config.json 里没有 workspace）：主工作台里的文件查不了，算没装对。"""
    return Step(key, label, f"<主工作台>/{name}", "没有配置主工作台（config.json 里没有 workspace），查不了",
                action="重跑 install，用 --workspace 指定主工作台", level="fail")


def plan_workspace_location(ctx: Ctx) -> Step:
    """只在 --check 里：主工作台是家目录（或它的上级目录）时只提示，不算没装对（以前装的照样能用；install 会拒绝）。"""
    key, label = "workspace", "主工作台位置"
    problem = home_problem(ctx.workspace)
    if problem:
        return Step(key, label, ctx.workspace, "是家目录（或它的上级目录）", level="warn",
                    note=problem + "重跑 install 时加上 --workspace <目录>。")
    return Step(key, label, ctx.workspace, "不是家目录")


def build_plan(ctx: Ctx) -> list:
    # 依赖放最前面：缺 herdr 时先装（或问你），后面用到 herdr 的项等它装完再跑。--check 的依赖另外列。
    steps = [] if ctx.checking else [plan_herdr(ctx), *[plan_host_cli(ctx, h) for h in ctx.hosts]]
    has_ws = bool(ctx.workspace)
    steps += [
        plan_pkg(ctx),
        plan_link(ctx, "bin", "drover CLI 入口", BIN_LINK, BIN_TARGET, PKG_ROOT / "bin" / "drover"),
        plan_config(ctx),
        plan_profile(ctx),
    ]
    if ctx.checking and has_ws:
        steps.append(plan_workspace_location(ctx))
    if "claude" in ctx.hosts:
        steps += [
            plan_link(ctx, "claude-command", "Claude Code 的 /drover 命令", CLAUDE_COMMAND,
                      INSTALL_ROOT / "flow" / "dispatch.md", PKG_ROOT / "flow" / "dispatch.md"),
            plan_claude_herdr_skill(ctx),
            plan_rules(ctx, "claude-settings", "Claude Code 全局权限规则", CLAUDE_SETTINGS, CLAUDE_GLOBAL_RULES),
            plan_hook(ctx, CD_GIT_HOOK),
            plan_hook(ctx, SESSION_HOOK),
            plan_hook(ctx, PROMPT_HOOK),
            plan_rules(ctx, "workspace-settings", "主工作台权限规则",
                       ctx.workspace_path / ".claude" / "settings.local.json", CLAUDE_WORKSPACE_RULES) if has_ws else
            plan_no_workspace("workspace-settings", "主工作台权限规则", ".claude/settings.local.json"),
            plan_claude_md(ctx) if has_ws else plan_no_workspace("claude-md", "主工作台 CLAUDE.md 引用 AGENTS.md",
                                                                  "CLAUDE.md"),
            plan_integration(ctx, "claude"),
        ]
    if "codex" in ctx.hosts:
        claude_planned = any(s.key == "claude-herdr-skill" and s.run for s in steps)
        steps += [
            plan_link(ctx, "codex-skill", "Codex 的 drover 技能", CODEX_DROVER_SKILL,
                      INSTALL_ROOT / "codex" / "skills" / "drover", PKG_ROOT / "codex" / "skills" / "drover"),
            plan_codex_herdr_skill(ctx, claude_planned),
            plan_integration(ctx, "codex"),
            plan_codex_sandbox(ctx),
        ]
    # 飞书 oncall（宿主无关）：插件失败只记这一项失败，其余照装（和没装 herdr 时一样）。
    plugin_step = plan_oncall_plugin(ctx)
    steps += [plan_oncall_node(ctx), plugin_step, plan_oncall_env(ctx, plugin_step)]
    # 验收工具（宿主无关）：装不上只记警告（StepWarning），不影响退出码。
    steps += plan_quality_tools(ctx)
    steps.append(plan_routing(ctx) if has_ws else plan_no_workspace("routing", "主工作台路由区块", "AGENTS.md"))
    share_backups(steps)
    if ctx.checking:
        steps.append(plan_chain())
        return steps
    state_step = plan_state(ctx)
    old = {k: v for k, v in ctx.state.items() if k != "updated_at"}
    if not any(s.run for s in steps) and merged_state(ctx) == old:
        state_step.state, state_step.action, state_step.run = "已是最新", "无需改动", None
    steps.append(state_step)
    return steps


def do_backup(ctx: Ctx, st: Step) -> None:
    st.backup.parent.mkdir(parents=True, exist_ok=True)
    if st.backup_how == "copy":
        shutil.copy2(str(st.fs), str(st.backup))
    else:
        shutil.move(str(st.fs), str(st.backup))
    ctx.backups.append(st.backup)


def run_step(ctx: Ctx, st: Step) -> bool:
    """执行一项（先备份）：成功、只记警告都返回 True，失败返回 False。一项失败不影响后面的项，最后整体报失败。"""
    try:
        if st.backup:
            do_backup(ctx, st)
        msg = st.run()
        st.result = "完成" + (f"：{msg}" if isinstance(msg, str) and msg else "")
    except StepWarning as exc:  # 没做成、只记警告：不算失败，原因和手动补法进提示（--json 的 notes、update 都读它）
        st.result, st.level = "警告：没做成，不影响安装（原因和手动补法见提示）", "warn"
        st.note = "；".join(filter(None, [st.note, str(exc)]))
    except Exception as exc:
        st.result = f"失败：{exc}"
        return False
    return True


def execute(ctx: Ctx, steps: list, quiet: bool) -> bool:
    ok = True
    for i, st in enumerate(steps, 1):
        if st.run is None:
            st.result = "没法处理" if st.level == "fail" else "跳过"
            ok = ok and st.level != "fail"
        elif ctx.dry_run:
            st.result = "（dry-run，没执行）"
        else:
            ok = run_step(ctx, st) and ok
        if not quiet:
            print_step(i, len(steps), st, show_result=not ctx.dry_run)
    return ok


def print_step(i: int, n: int, st: Step, show_result: bool = True, verdict: Optional[str] = None) -> None:
    width = len(str(n))
    head = f"[{i:>{width}}/{n}]" if verdict is None else f"[{verdict}]"
    pad = " " * (len(head) + 1)
    print(f"{head} {st.label}  {st.path}")
    print(f"{pad}现状：{st.state}")
    if verdict is None or st.run is not None:
        print(f"{pad}{'动作' if verdict is None else '要做'}：{st.action}")
    if verdict is None:
        print(f"{pad}备份：{disp(st.backup) if st.backup else '不备份'}")
    if show_result and st.result:
        print(f"{pad}结果：{st.result}")
    if st.note:
        print(f"{pad}提示：{st.note}")


def print_header(title: str, ctx: Ctx, host_why: str) -> None:
    print(f"{title}（{ctx.mode} 模式，宿主：{' + '.join(ctx.hosts)}）")
    print(f"  包：{PKG_ROOT}")
    print(f"  主工作台：{ctx.workspace or '没配置'}    仓库目录：{', '.join(ctx.repo_dirs) or '没配置'}    "
          f"团队 profile：{ctx.profile or '没配置'}    飞书 oncall：{'接' if ctx.oncall else '不接（要接加 --oncall）'}")
    print(f"  宿主：{host_why}")
    if ctx.dry_run and not ctx.checking:
        print("  这是 dry-run：只打印计划，不写任何文件，不安装 herdr，不执行 herdr integration install、"
              "herdr plugin install / update。")
    print()


def outcome_counts(steps: list) -> str:
    """汇总行末尾：失败几项、只记警告几项（都没有是空串）。"""
    failed = sum(1 for s in steps if s.result.startswith(("失败", "没法处理")))
    warned = sum(1 for s in steps if s.result.startswith("警告"))
    return (f"，失败 {failed} 项" if failed else "") + (f"，只记警告 {warned} 项" if warned else "")


def summarize(ctx: Ctx, steps: list, ok: bool) -> None:
    changed = [s for s in steps if s.run is not None]
    backups = [s.backup for s in steps if s.run is not None and s.backup]
    notes = [f"{s.label}：{s.note}" for s in steps if s.note]
    print()
    verb = "将改动" if ctx.dry_run else "改动"
    print(f"汇总：{verb} {len(changed)} 项（其中 {len(backups)} 项先备份），无需改动 {len(steps) - len(changed)} 项"
          + outcome_counts(steps))
    for b in backups:
        print(f"  备份：{disp(b)}")
    for n in notes:
        print(f"  提示：{n}")
    if ctx.dry_run:
        print("没有写任何文件。确认后去掉 --dry-run 再跑一次。")
    elif ok:
        for line in next_steps(ctx):
            print(line)
    else:
        print("有失败项，按上面的结果处理后重跑（重跑是幂等的：装对的项会跳过，不会重复备份）。")


def next_steps(ctx: Ctx) -> list:
    """装好之后要做的事：drover 只能在 Herdr 的 pane 里用，所以第一件是在 Herdr 里开主会话。"""
    lines = ["下一步："]
    n = 1
    off = herdr_off_path()
    if off:
        lines.append(f"  {n}. 把 herdr 所在目录加进 PATH：export PATH=\"{shell_dir(off.parent)}:$PATH\" 写进 ~/.zshrc"
                     "（bash 用 ~/.bashrc），开一个新终端")
        n += 1
    agents = " 或 ".join({"claude": "claude", "codex": "codex -s danger-full-access"}[h] for h in ctx.hosts)
    lines.append(f"  {n}. 在 Herdr 里开主会话：终端里运行 herdr，在它的 pane 里 cd {ctx.workspace}，再启动 {agents}"
                 "（drover 只能在 Herdr 的 pane 里用）")
    lines.append(f"  {n + 1}. python3 install.py --check（或 ./install.sh --check）检查安装状态和依赖")
    if ctx.oncall and ctx.feishu == "paired":
        lines.append(f"  {n + 2}. 飞书已配对：离开电脑时主会话需要你会推到飞书（~/.drover/bin/drover oncall test 可以发一条试试）")
    elif ctx.oncall and ctx.feishu != "unpaired":  # unpaired：汇总之后当场问要不要配对
        lines.append(f"  {n + 2}. 想离开电脑也能收到主会话的消息、在飞书里跟它说话：{PAIR_CMD} 扫码配对飞书"
                     "（Mac 桌面会弹二维码，没弹就用它给的链接），再 ~/.drover/bin/drover oncall test 发一条试试"
                     "（不配也行，macOS 上桌面面板照样提醒）")
    elif not ctx.oncall and oncall_choice(ctx) is None:  # 没选过（--no-oncall 过的不再提）
        lines.append(f"  {n + 2}. 飞书 oncall 没装（选装）：想离开电脑时在飞书里收主会话的提问、给它下命令，重跑 install 加 "
                     f"--oncall（要 Node {ONCALL_NODE_MIN}+，见 README 4.9）")
    return lines


def emit_json(command: str, ctx: Optional[Ctx], steps: list, ok: bool, **extra) -> None:
    out = {"ok": ok, "command": command}
    if ctx is not None:
        out.update({"mode": ctx.mode, "hosts": ctx.hosts, "workspace": ctx.workspace, "repo_dirs": ctx.repo_dirs,
                    "profile": ctx.profile, "oncall": ctx.oncall})
    out["steps"] = [s.as_dict() for s in steps]
    out.update(extra)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def deps_mode_of(args: argparse.Namespace) -> str:
    """缺 herdr 时：--install-deps 直接装；交互终端（stdin、stdout 都是终端，且不是 --json）先问；否则只给命令。"""
    if getattr(args, "install_deps", False):
        return "yes"
    return "ask" if interactive(args) else "no"


def make_ctx(args: argparse.Namespace, hosts: list, mode: str, checking: bool = False) -> Ctx:
    workspace, repo_dirs, update, existing, err = resolve_config(args)
    if checking and existing is not None:
        update = {}  # --check 只看现状，不拿参数去比 config.json
    ctx = Ctx(hosts=hosts, mode=mode, workspace=workspace, repo_dirs=repo_dirs, config_update=update,
              config_existing=existing, config_error=err, profile=effective_profile(existing, update),
              dry_run=args.dry_run or checking, checking=checking, state=load_state(),
              deps_mode="no" if checking else deps_mode_of(args))
    ctx.oncall = oncall_enabled(ctx)
    ctx.quality_tools = quality_tools_enabled(existing)
    return ctx


def agent_install_hint() -> str:
    return "；".join(f"{v['name']}：{v['cmd']}（或 {v['alt']}，见 {v['docs']}）" for v in AGENT_INSTALL.values())


def pick_hosts(args: argparse.Namespace) -> tuple[list, str]:
    hosts = parse_hosts(args.host)
    if hosts:
        return hosts, f"--host {args.host}"
    found, why = detect_hosts()
    if not found:
        raise InstallError("没检测到 Claude Code 或 Codex（PATH 里没有 claude / codex 命令，也没有 ~/.claude、~/.codex）。"
                           f"先装其中一个（install 不替你装），官方安装命令——{agent_install_hint()}。"
                           "装好后重跑；或者用 --host 指定先装好 drover 这边的入口")
    return found, f"自动判断（{why}）"


def cmd_install(args: argparse.Namespace) -> int:
    hosts, why = pick_hosts(args)
    mode = args.mode or "link"
    for need in [PKG_ROOT / "bin" / "drover", PKG_ROOT / "flow" / "dispatch.md", ROUTING_TEMPLATE,
                 PKG_ROOT / "codex" / "skills" / "drover" / "SKILL.md", PKG_ROOT / HOOK_SCRIPT_REL]:
        if not need.is_file():
            raise InstallError(f"包不完整：缺 {need}")
    check_profile_arg(args)
    ensure_workspace(args)
    check_workspace(args)
    ctx = make_ctx(args, hosts, mode)
    steps = build_plan(ctx)
    if not args.json:
        print_header("drover install" + (" --dry-run" if ctx.dry_run else ""), ctx, why)
    ok = execute(ctx, steps, quiet=args.json)
    if args.json:
        emit_json("dry-run" if ctx.dry_run else "install", ctx, steps, ok,
                  backups=[disp(s.backup) for s in steps if s.run is not None and s.backup],
                  notes=[f"{s.label}：{s.note}" for s in steps if s.note],
                  deps_mode=ctx.deps_mode, herdr_installed=ctx.herdr_installed,
                  next_steps=next_steps(ctx) if ok and not ctx.dry_run else [])
    else:
        if should_check_feishu(ctx, args, steps):
            ctx.feishu = feishu_state()
        summarize(ctx, steps, ok)
        if ctx.feishu == "unpaired":
            offer_pairing()
    return 0 if ok else 1


# ---------------------------------------------------------------- --check


def check_deps() -> list:
    """依赖一览。缺必需项时 hint 里是官方的安装方式（install.py 只会替你装 herdr）。"""
    hints = {"herdr": f"{HERDR_INSTALL_CMD}（或 {HERDR_BREW_CMD}；重跑 install 加 --install-deps 会替你装）",
             "git": GIT_HINT.get(platform.system(), "用系统的包管理器装 git"),
             "python3": "xcode-select --install、brew install python，或 https://www.python.org/downloads/"}
    rows = []
    for t in REQUIRED_TOOLS:
        row = {"name": t, "required": True, "path": shutil.which(t), "hint": None}
        if not row["path"]:
            off = herdr_off_path() if t == "herdr" else None
            row["hint"] = path_note(off.parent) if off else hints[t]
        rows.append(row)
    agents = [f"{t}（{shutil.which(t)}）" for t in HOSTS if shutil.which(t)]
    rows.append({"name": "claude / codex（至少一个）", "required": True, "path": "，".join(agents) or None,
                 "hint": None if agents else agent_install_hint()})
    rows += [{"name": t, "required": False, "path": shutil.which(t), "hint": None} for t in OPTIONAL_TOOLS]
    return rows


def verdict_of(st: Step) -> str:
    if st.level == "fail":
        return "fail"
    if st.level == "warn" or st.run is not None:
        return "warn"
    return "ok"


def cmd_check(args: argparse.Namespace) -> int:
    state = load_state()
    hosts = parse_hosts(args.host) or [h for h in HOSTS if h in (state.get("hosts") or [])]
    why = f"--host {args.host}" if args.host else f"安装记录 {disp(STATE_FILE)}"
    if not hosts:
        hosts, why = pick_hosts(args)
    mode = args.mode or state.get("mode") or ("copy" if (INSTALL_ROOT / COPY_MARKER).is_file() else "link")
    ctx = make_ctx(args, hosts, mode, checking=True)
    steps = build_plan(ctx)
    deps = check_deps()
    marks = {"ok": "✓", "warn": "!", "fail": "✗"}
    verdicts = [verdict_of(s) for s in steps]
    deps_ok = all(d["path"] for d in deps if d["required"])
    ok = deps_ok and "fail" not in verdicts
    if args.json:
        emit_json("check", ctx, steps, ok, verdicts=verdicts, deps=deps)
        return 0 if ok else 1
    print_header("drover install --check", ctx, why)
    print("依赖：")
    for d in deps:
        mark = "✓" if d["path"] else ("✗" if d["required"] else "-")
        kind = "必需" if d["required"] else "可选"
        print(f"  [{mark}] {d['name']}（{kind}）：{d['path'] or '没找到'}")
        if d["hint"]:
            print(f"      装法：{d['hint']}")
    print()
    print("安装状态：")
    for st, v in zip(steps, verdicts):
        print_step(0, 0, st, show_result=False, verdict=marks[v])
    bad = [s.label for s, v in zip(steps, verdicts) if v == "fail"]
    missing = [d["name"] for d in deps if d["required"] and not d["path"]]
    print()
    if ok:
        warn = [s.label for s, v in zip(steps, verdicts) if v == "warn"]
        print("结果：安装完整" + (f"；有提示：{'、'.join(warn)}" if warn else "") + "。")
    else:
        parts = ([f"缺必需依赖：{'、'.join(missing)}"] if missing else []) + ([f"没装对：{'、'.join(bad)}"] if bad else [])
        print("结果：" + "；".join(parts) + "。按上面每项的「要做」处理，或重跑 python3 install.py。")
    return 0 if ok else 1


# ---------------------------------------------------------------- --uninstall


def drover_link(p: Path, state: dict) -> bool:
    """p 是不是指向 drover 的软链（指向安装位置、当前包、安装记录里的源，或者指向的就是一份 drover 包）。"""
    if not p.is_symlink():
        return False
    roots = {INSTALL_ROOT, PKG_ROOT}
    if state.get("source"):
        roots.add(Path(state["source"]))
    pointed = link_points_to(p)
    if any(pointed == r or r in pointed.parents for r in roots):
        return True
    if p.exists():
        real = p.resolve()
        resolved = {r.resolve() for r in roots if r.exists()}
        return any(real == r or r in real.parents for r in resolved) or is_drover_pkg(real)
    return False


def unplan_link(key: str, label: str, path: Path, state: dict) -> Step:
    if not (path.exists() or path.is_symlink()):
        return Step(key, label, disp(path), "不存在")
    if drover_link(path, state):
        return Step(key, label, disp(path), f"软链 → {disp(os.readlink(path))}", action="删除软链", run=path.unlink)
    return Step(key, label, disp(path), "不是指向 drover 的软链", action="不动（不是 install 装的）", level="warn")


def unplan_bin(state: dict) -> Step:
    """CLI 入口：删软链；~/.drover/bin 删完是空目录时一并删掉（里面还有别的文件就留着）。"""
    st = unplan_link("bin", "drover CLI 入口", BIN_LINK, state)
    if st.run is None:
        return st

    def run() -> None:
        BIN_LINK.unlink()
        parent = BIN_LINK.parent
        if parent.is_dir() and not os.listdir(parent):
            parent.rmdir()
    st.action, st.run = f"删除软链（{disp(BIN_LINK.parent)} 空了就一并删）", run
    return st


def unplan_created(key: str, label: str, path: Path, state: dict) -> Step:
    """install 新建的文件 / 软链 / 目录（安装记录里有）才删；原来就有的不动。"""
    if not (path.exists() or path.is_symlink()):
        return Step(key, label, disp(path), "不存在")
    if str(path) not in (state.get("created") or []):
        return Step(key, label, disp(path), "原来就有（不是 install 新建的）", action="不动")
    if path.is_dir() and not path.is_symlink():
        if sorted(os.listdir(path)) != ["SKILL.md"]:
            return Step(key, label, disp(path), "install 新建的目录，但里面多了别的文件", action="不动", level="warn")
        return Step(key, label, disp(path), "install 新建的目录", action="删除", run=lambda: shutil.rmtree(path))

    def run() -> None:
        path.unlink()
        parent = path.parent
        if path.name == "SKILL.md" and parent.is_dir() and not os.listdir(parent):
            parent.rmdir()
    return Step(key, label, disp(path), "install 新建的", action="删除", run=run)


def unplan_rules(key: str, label: str, path: Path, state: dict) -> Step:
    recorded = (state.get("added_rules") or {}).get(str(path), [])
    data, err = read_json(path)
    if data is None:
        return Step(key, label, disp(path), err or "不存在", action="不动", level="warn" if err else "ok")
    allow = (data.get("permissions") or {}).get("allow") if isinstance(data.get("permissions"), dict) else None
    present = [r for r in recorded if isinstance(allow, list) and r in allow]
    if not present:
        state_text = "安装记录里没有 install 加过的规则（原来就有的不删）" if not recorded else "install 加的规则已经不在"
        return Step(key, label, disp(path), state_text)
    created = str(path) in (state.get("created") or [])

    def run() -> str:
        cur, cur_err = read_json(path)
        if cur_err or cur is None:
            raise RuntimeError(cur_err or "文件不见了")
        cur_allow = cur["permissions"]["allow"]
        cur["permissions"]["allow"] = [r for r in cur_allow if r not in present]
        if created and only_skeleton(cur):
            path.unlink()
            return "删完规则后文件是空的（install 新建的），一并删除"
        write_json(path, cur)
        return f"删了 {len(present)} 条"

    return Step(key, label, disp(path), f"install 加过 {len(present)} 条", action="删掉这几条：" + "、".join(present),
                run=run, fs=path, backup=None if created else backup_target(path), backup_how="copy")


def only_skeleton(data: dict) -> bool:
    """撤完之后只剩空架子（空的 permissions.allow、空的 hooks）：install 新建的 settings 文件可以整个删掉。"""
    rest = dict(data)
    if rest.get("permissions") == {"allow": []}:
        rest.pop("permissions")
    if rest.get("hooks") == {}:
        rest.pop("hooks")
    return rest == {}


def hook_commands_in(path: Path, events: Optional[list] = None) -> set:
    """settings 文件里 hooks.<event>（events 为 None 时所有事件）下的全部命令。"""
    data, _ = read_json(path)
    hooks = (data or {}).get("hooks")
    if not isinstance(hooks, dict):
        return set()
    have = set()
    for event, groups in hooks.items():
        if events is None or event in events:
            have |= {h.get("command") for _, h in iter_hooks(groups if isinstance(groups, list) else [])}
    return have


def present_hooks(path: Path, commands: list) -> list:
    """commands 里还在 path 的 hooks（任何事件）里的那些。"""
    have = hook_commands_in(path)
    return [c for c in commands if c in have]


def unplan_hook(state: dict, spec: HookSpec) -> Step:
    """只撤安装记录里 install 加的那一项 hook（原来就有的、别的 hook 不动）；这个事件、hooks 撤空了就去掉，
    不留空结构；文件是 install 新建的、撤完只剩空架子时整个删掉。"""
    key, label, path = spec.key, spec.label, CLAUDE_SETTINGS
    recorded = recorded_hooks(state, path)
    data, err = read_json(path)
    if data is None:
        return Step(key, label, disp(path), err or "不存在", action="不动", level="warn" if err else "ok")
    groups, err = event_groups(data, spec.event)
    if err:
        return Step(key, label, disp(path), err, action="不动", level="warn")
    have = hook_commands_in(path, [spec.event])
    present = [c for c in recorded if c in have]
    if not present:
        state_text = "安装记录里没有 install 加过的 hook（原来就有的不删）" if not recorded else "install 加的 hook 已经不在"
        return Step(key, label, disp(path), state_text)
    created = str(path) in (state.get("created") or [])

    def run() -> str:
        cur, cur_err = read_json(path)
        if cur_err or cur is None:
            raise RuntimeError(cur_err or "文件不见了")
        cur_groups, cur_err = event_groups(cur, spec.event)
        if cur_err:
            raise RuntimeError(cur_err)
        if spec.event not in cur.get("hooks", {}):
            return "已经不在了，不用删"
        removed = 0
        kept_groups = []
        for g in cur_groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                kept = [h for h in g["hooks"] if not (isinstance(h, dict) and h.get("command") in present)]
                removed += len(g["hooks"]) - len(kept)
                if not kept and g["hooks"]:
                    continue  # 组里只有 drover 这一项：整组去掉
                g["hooks"] = kept
            kept_groups.append(g)
        if kept_groups:
            cur["hooks"][spec.event] = kept_groups
        else:
            cur["hooks"].pop(spec.event, None)
            if not cur["hooks"]:
                cur.pop("hooks")
        if created and only_skeleton(cur):
            path.unlink()
            return "删完 hook 后文件是空的（install 新建的），一并删除"
        write_json(path, cur)
        return f"删了 {removed} 项"

    return Step(key, label, disp(path), f"install 加过这一项（{spec.where()}）",
                action="删掉这一项：" + "、".join(present), run=run, fs=path,
                backup=None if created else backup_target(path), backup_how="copy")


def unplan_routing(ws: Path, state: dict) -> Step:
    key, label, path = "routing", "主工作台路由区块", ws / "AGENTS.md"
    if not path.exists():
        return Step(key, label, disp(path), "不存在")
    text = path.read_text()
    try:
        new = strip_block(text)
    except BlockError as exc:
        return Step(key, label, disp(path), str(exc), action="不动", level="fail")
    if new == text:
        return Step(key, label, disp(path), "没有 drover 区块")
    if not new.strip() and str(path) in (state.get("created") or []):
        return Step(key, label, disp(path), "有区块；文件是 install 新建的", action="删除区块后文件为空，一并删除",
                    run=path.unlink)
    return Step(key, label, disp(path), "有区块", action="删除区块（区块外内容不动）", run=lambda: path.write_text(new))


def unplan_claude_md(ws: Path, state: dict) -> Step:
    key, label, path = "claude-md", "主工作台 CLAUDE.md", ws / "CLAUDE.md"
    if not path.exists():
        return Step(key, label, disp(path), "不存在")
    if str(path) in (state.get("created") or []) and path.read_text().strip() == CLAUDE_MD_LINE:
        return Step(key, label, disp(path), f"install 新建的，只有 {CLAUDE_MD_LINE}", action="删除", run=path.unlink)
    return Step(key, label, disp(path), "原来就有或改过", action="不动（@AGENTS.md 这行留着无害）")


def unplan_pkg(state: dict) -> Step:
    key, label, path = "pkg", "包目录", INSTALL_ROOT
    if not (path.exists() or path.is_symlink()):
        return Step(key, label, disp(path), "不存在")
    if path.is_symlink():
        if drover_link(path, state):
            return Step(key, label, disp(path), f"软链 → {disp(os.readlink(path))}", action="删除软链（clone 本身不动）",
                        run=path.unlink)
        return Step(key, label, disp(path), "软链指向的不是 drover", action="不动", level="warn")
    if (path / COPY_MARKER).is_file() and path.resolve() != PKG_ROOT:
        return Step(key, label, disp(path), "drover 复制的副本（copy 安装）", action="删除副本",
                    run=lambda: shutil.rmtree(path))
    if (path / COPY_MARKER).is_file():
        return Step(key, label, disp(path), "就是当前运行的这份副本", action="不动：从 clone 运行 --uninstall，或手动删除",
                    level="warn")
    return Step(key, label, disp(path), "实体目录，不是 drover 复制的", action="不动", level="warn")


def unplan_oncall_env(ctx: Ctx, state: dict) -> Step:
    """删 drover.env：路径取安装记录；没有记录时，插件装着才去问 config-dir（它会建目录）。同目录的 .env 不碰。"""
    key, label = "oncall-env", "drover.env（drover 给插件的配置）"
    recorded = (state.get("oncall") or {}).get("env")
    path = Path(recorded) if recorded else None
    if path is None and oncall_plugin(ctx):
        with contextlib.suppress(RuntimeError, OSError, subprocess.SubprocessError):
            path = oncall_config_dir() / ONCALL_ENV_NAME
    if path is None or not path.exists():
        return Step(key, label, disp(path) if path else f"<插件配置目录>/{ONCALL_ENV_NAME}", "不存在")
    return Step(key, label, disp(path), "drover 写的", action="删除（同目录的 .env、飞书配对不碰）", run=path.unlink)


def unplan_oncall_plugin(ctx: Ctx, state: dict) -> Step:
    """插件本身：安装记录里记着是 drover 装的才卸；原来就有的（用户自己装的、本地链接的）不动。"""
    key, label = "oncall-plugin", "herdr-oncall（drover 内置的通知插件）"
    path = f"herdr 插件 {ONCALL_PLUGIN_ID}"
    ours = bool((state.get("oncall") or {}).get("installed_by_drover"))
    plugins = oncall_plugins(ctx)
    if plugins is None:
        if not ours:
            return Step(key, label, path, "安装记录里不是 drover 装的", action="不动")
        return Step(key, label, path, f"查不了：{ctx.oncall_list_error}", level="warn",
                    action=f"不动：herdr 能用后手动 herdr plugin uninstall {ONCALL_PLUGIN_ID}")
    plugin = oncall_plugin(ctx)
    if plugin is None:
        return Step(key, label, path, "没装")
    if not ours:
        return Step(key, label, path, f"已装 {plugin.get('version')}，原来就有（不是 drover 装的）", action="不动")

    def run() -> str:
        res = run_herdr(["plugin", "uninstall", ONCALL_PLUGIN_ID], timeout=ONCALL_INSTALL_TIMEOUT)
        if res.returncode != 0:
            raise RuntimeError(f"herdr plugin uninstall {ONCALL_PLUGIN_ID} 退出码 {res.returncode}："
                               f"{(res.stderr or res.stdout).strip()[:200]}")
        return "卸了（插件配置目录里的 .env、飞书配对 herdr 会留着，以后重装不用重新扫码）"

    return Step(key, label, path, f"已装 {plugin.get('version')}，是 drover 装的",
                action=f"执行 herdr plugin uninstall {ONCALL_PLUGIN_ID}", run=run)


def find_backups(state: dict, workspaces: list) -> list:
    dirs = [CLAUDE_HOME, CLAUDE_HOME / "commands", CLAUDE_HOME / "skills" / "herdr", STATE_HOME,
            STATE_HOME / "bin", BACKUP_DIR / "codex-skills", CODEX_SKILLS]
    for ws in workspaces:
        dirs += [ws, ws / ".claude"]
    found = {Path(p) for p in (state.get("backups") or []) if Path(p).exists() or Path(p).is_symlink()}
    for d in dirs:
        if d.is_dir():
            found.update(d.glob("*.bak-drover-*"))
    return sorted(found)


def pruned_state(state: dict, hosts: list) -> dict:
    """部分卸载后剩下的安装记录：只留还在的新建文件和还在的规则。"""
    out = dict(state)
    out["hosts"] = hosts
    out["created"] = [p for p in state.get("created") or [] if Path(p).exists() or Path(p).is_symlink()]
    rules = {}
    for path, added in (state.get("added_rules") or {}).items():
        data, _ = read_json(Path(path))
        allow = ((data or {}).get("permissions") or {}).get("allow") or []
        kept = [r for r in added if r in allow]
        if kept:
            rules[path] = kept
    out["added_rules"] = rules
    hooks = {}
    for path, cmds in (state.get("added_hooks") or {}).items():
        kept = present_hooks(Path(path), cmds)
        if kept:
            hooks[path] = kept
    out["added_hooks"] = hooks
    out["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    return out


def cmd_uninstall(args: argparse.Namespace) -> int:
    state = load_state()
    installed = [h for h in HOSTS if h in (state.get("hosts") or [])] or list(HOSTS)
    remove = parse_hosts(args.host) or list(installed)
    remaining = [h for h in installed if h not in remove]
    workspace, repo_dirs, _, existing, err = resolve_config(args)
    workspaces = []
    # 没有配置主工作台时只看安装记录里的（不去猜哪个目录是主工作台）
    for w in list(state.get("workspaces") or []) + ([workspace] if workspace else []):
        if expand(w) not in workspaces:
            workspaces.append(expand(w))
    ctx = Ctx(hosts=remove, mode=state.get("mode") or "link", workspace=workspace, repo_dirs=repo_dirs,
              config_update={}, config_existing=existing, config_error=err, dry_run=args.dry_run, state=state)

    steps = []
    if "claude" in remove:
        steps.append(unplan_link("claude-command", "Claude Code 的 /drover 命令", CLAUDE_COMMAND, state))
        codex_uses_it = ("codex" in remaining and CODEX_HERDR_SKILL.is_symlink()
                         and link_points_to(CODEX_HERDR_SKILL) == CLAUDE_HERDR_SKILL.parent)
        if codex_uses_it:
            steps.append(Step("claude-herdr-skill", "Claude Code 的 herdr 技能", disp(CLAUDE_HERDR_SKILL),
                              "Codex 的 herdr 技能软链到它", action="保留"))
        else:
            steps.append(unplan_created("claude-herdr-skill", "Claude Code 的 herdr 技能", CLAUDE_HERDR_SKILL, state))
        steps.append(unplan_rules("claude-settings", "Claude Code 全局权限规则", CLAUDE_SETTINGS, state))
        steps.append(unplan_hook(state, CD_GIT_HOOK))
        steps.append(unplan_hook(state, SESSION_HOOK))
        steps.append(unplan_hook(state, PROMPT_HOOK))
        for ws in workspaces:
            steps.append(unplan_rules("workspace-settings", "主工作台权限规则", ws / ".claude" / "settings.local.json",
                                      state))
            steps.append(unplan_claude_md(ws, state))
    if "codex" in remove:
        steps.append(unplan_link("codex-skill", "Codex 的 drover 技能", CODEX_DROVER_SKILL, state))
        steps.append(unplan_created("codex-herdr-skill", "Codex 的 herdr 技能", CODEX_HERDR_SKILL, state))
    if not remaining:
        steps += [unplan_routing(ws, state) for ws in workspaces]
        steps += [unplan_oncall_env(ctx, state), unplan_oncall_plugin(ctx, state)]
        steps.append(unplan_tools())
        steps.append(unplan_bin(state))
        steps.append(unplan_pkg(state))
        steps.append(Step("config", "本机配置", disp(CONFIG_FILE), "存在" if CONFIG_FILE.exists() else "不存在",
                          action="保留（重装时沿用；不要了可以手动删）"))
        if STATE_FILE.exists():
            steps.append(Step("state", "安装记录", disp(STATE_FILE), "存在", action="删除", run=STATE_FILE.unlink))
    elif STATE_FILE.exists():
        steps.append(Step("state", "安装记录", disp(STATE_FILE), f"装着 {' + '.join(installed)}",
                          action=f"改成只记 {' + '.join(remaining)}（共用的包、CLI 入口、路由区块留给它）",
                          run=lambda: write_json(STATE_FILE, pruned_state(state, remaining))))

    if not args.json:
        print(f"drover install --uninstall{' --dry-run' if args.dry_run else ''}（撤掉：{' + '.join(remove)}）")
        print(f"  安装记录：{disp(STATE_FILE) if state else '没有（按默认位置找 drover 的软链和区块）'}")
        if args.dry_run:
            print("  这是 dry-run：只打印计划，不删任何东西。")
        print()
    share_backups(steps)
    ok = execute(ctx, steps, quiet=args.json)
    backups = find_backups(state, workspaces)
    kept = [disp(CONFIG_FILE), f"任务记录 {disp(STATE_HOME)}/<slug>/", "worktree ~/.herdr/worktrees/",
            "herdr 的 agent 状态 hook（herdr integration）", "herdr-oncall 插件的 .env（飞书配对）",
            f"飞书发来的图片 {disp(STATE_HOME)}/oncall-images/"]
    if args.json:
        emit_json("uninstall-dry-run" if args.dry_run else "uninstall", ctx, steps, ok,
                  backups=[disp(b) for b in backups], kept=kept)
        return 0 if ok else 1
    print()
    print("保留不动：" + "、".join(kept))
    if backups:
        print("备份（不自动还原；要还原就自己挪回原名）：")
        for b in backups:
            print(f"  {disp(b)}")
    print("没有删任何东西（dry-run）。" if args.dry_run else ("卸载完成。" if ok else "有失败项，见上面的结果。"))
    return 0 if ok else 1


# ---------------------------------------------------------------- 命令行

USAGE = ("python3 install.py [--host claude|codex|both] [--mode link|copy] [--workspace DIR] [--repo-dir DIR]... "
         "[--profile NAME|DIR] [--oncall|--no-oncall] [--install-deps] [--dry-run] [--check] [--uninstall] [--json]")

EPILOG = """\
一键安装：clone 之后在包根目录跑 ./install.sh [同样的参数]。它先检查 macOS / Linux 和 git、python3（3.9+），
  缺了就给出官方安装命令并停下；都在就把参数原样交给 install.py。

主工作台（--workspace）：你平时放各个仓库、开主会话的目录，没有默认值，也不给建议值。首次安装（config.json 里还没有
  workspace）不传 --workspace 时：交互终端里问一句（空输入再问；Ctrl-D 退出码 2，Ctrl-C 退出码 130，都是什么都不装）；
  非交互（agent 的 shell、管道、CI、--json）不问，退出码 2，提示里给出 --workspace 的写法（--json 时输出带 error）。
  已有 config.json 的 workspace 照旧沿用、不问；--check、--uninstall 不问。--repo-dir 默认就是 workspace。
  不能是家目录或它的上级目录（含 /；按解析后的真实路径比，~、相对路径、末尾斜杠、软链都算）：Claude Code 会从当前目录
  往上找 CLAUDE.md、Codex 会加载 AGENTS.md，设在这里 drover 的路由规则会进到家目录下的所有会话，
  <workspace>/.claude/settings.local.json 也会落进 ~/.claude/。--workspace、已有 config.json 是这样时安装和 --dry-run
  都拒绝（退出码 2，什么都不写），交互输入这样的目录时说明原因再问；--check 只提示（!）；--uninstall 照常撤。

缺依赖时：
  herdr                没装时计划的第一项是"安装 herdr"，用官方安装脚本 curl -fsSL https://herdr.dev/install.sh | sh
                       （装到 ~/.local/bin，或 $HERDR_INSTALL_DIR；不用 sudo、不改 shell 配置）。交互终端里先问一句 [Y/n]；
                       加 --install-deps 不问直接装；非交互（管道、CI、--json）不装，只给出命令，退出码 1。
                       装完在同一次运行里接着生成 herdr 技能、装状态 hook。装好的目录不在 PATH 里时会提示怎么加。
                       也可以自己装：brew install herdr，见 https://herdr.dev/tui/docs/install/
                       （测试或内网镜像可以用环境变量 DROVER_HERDR_INSTALLER 换成别的安装命令）
  claude / codex       不替你装，只给官方命令：Claude Code  curl -fsSL https://claude.ai/install.sh | bash；
                       Codex  curl -fsSL https://chatgpt.com/codex/install.sh | sh（或 npm install -g @openai/codex）。
                       两个都没检测到、又没传 --host 时退出码 2；传了 --host 但命令没装时只提示，入口照样装。
  git / python3        install.sh 检查并给出装法；--check 也会列出来。
  装完之后：在 Herdr 里开主会话（终端里运行 herdr，在 pane 里 cd 到主工作台，再启动 claude 或 codex），drover 才能用。

安装布局（link / copy 只有第一项不同）：
  ~/.drover/pkg                          包根目录。link：软链到这份 clone；copy：复制一份（排除 .git、__pycache__ 等）
  ~/.drover/bin/drover                   drover CLI，软链到 ~/.drover/pkg/bin/drover
  ~/.drover/config.json                  workspace、repo_dirs、profile、oncall（--oncall 记 true、--no-oncall 记 false）。首次安装时写入（workspace 见上文）；已有时
                                         不覆盖，除非显式传 --workspace / --repo-dir / --profile / --oncall / --no-oncall（只改传了的那个键，
                                         改之前备份）
  ~/.drover/drover-install.json          安装记录（装了哪些宿主、新建的文件、加的规则和 hook、备份；--uninstall 按它撤）
  ~/.drover/<slug>/                      每个任务一个目录（drover CLI 写，install 不碰）；baselines/、locks/ 同理
  Claude Code（--host 含 claude）：
    ~/.claude/commands/drover.md         软链到 ~/.drover/pkg/flow/dispatch.md（/drover 命令）
    ~/.claude/skills/herdr/SKILL.md      没有时用 herdr --skill 的输出写一份
    ~/.claude/settings.json              permissions.allow 追加 drover CLI、~/.drover 和 ~/.herdr/worktrees 读写、
                                         herdr agent / pane / worktree / workspace list / tab list / status；
                                         hooks.PreToolUse 加一项 cd+git 拦截 hook（matcher Bash，跑装好的包里的
                                         ~/.drover/pkg/hooks/cd_git_guard.py，timeout 5 秒）：拒绝 cd <目录> && git …，
                                         提示改成 git -C <目录> …。先 cd 再跑 git 会触发 Claude Code 内置的强制确认
                                         （没法加白名单、每次都要人批），子代理最爱这么写。已有的其他 hook 原样保留；
                                         hooks.SessionStart 加一项主会话登记 hook（不设 matcher，跑
                                         ~/.drover/pkg/bin/drover main-register --hook，timeout 15 秒）：把主会话在
                                         herdr 里改名为 drover（飞书 oncall 按这个名字认主会话；/clear、退出重开会被
                                         herdr 清掉，所以每次会话开始都登记），不是主会话的直接跳过；
                                         hooks.UserPromptSubmit 加一项主会话读新规则 hook（不设 matcher，跑
                                         ~/.drover/pkg/bin/drover rules --hook，timeout 10 秒）：自动更新改了 AGENTS.md
                                         里的 drover 规则时，把改动跟着用户的下一条消息送进主会话（不用 /clear）；
                                         不是主会话、规则没变时什么都不输出
    <workspace>/.claude/settings.local.json  追加 Skill(drover)、Skill(herdr)、Bash(herdr workspace *)、Bash(herdr tab *)
    <workspace>/CLAUDE.md                含一行 @AGENTS.md（没有就新建 / 追加）
    herdr integration install claude     claude 的状态 hook 不是 current 时执行
  Codex（--host 含 codex；设了 CODEX_HOME 时用它代替 ~/.codex）：
    ~/.codex/skills/drover               软链到 ~/.drover/pkg/codex/skills/drover（$drover 技能）
    ~/.codex/skills/herdr                没有时：有 ~/.claude/skills/herdr 就软链过去，否则用 herdr --skill 写一份
    herdr integration install codex      codex 的状态 hook 不是 current 时执行
    ~/.codex/config.toml                 sandbox_mode 不是 danger-full-access 时只提示，不改
                                         （Codex 不装主会话登记 hook 和读新规则 hook：codex 的 SessionStart 第一条消息时
                                         才跑，新 hook 还要手动信任；Codex 主会话按路由规则在
                                         第一条消息时跑 drover main-register，每条消息看 board --json 的 drover 字段）
  两边共用：
    <workspace>/AGENTS.md                <!-- drover:routing:begin --> … <!-- drover:routing:end --> 区块，由
                                         flow/routing.md 渲染。已有区块就整段替换，区块外内容不动；文件不存在就新建；
                                         有内容但没有标记时追加到末尾，并提示检查重复
  飞书 oncall（drover 内置的 herdr-oncall 插件，选装：--oncall 装并记进 config.json，--no-oncall 不装（已装的插件不卸）；
  都没传过时，已经接上的照旧（安装记录里有 oncall、或者 herdr 里装着插件：drover update 照旧保留、照旧更新），其余不装）：
    Node                                 只检查：没有或低于 18 插件跑不了（算失败），18～21 收不到飞书回复（提示）
    herdr plugin com.codreamer.herdr.oncall
                                         没装：herdr plugin install fulanto/herdr-oncall --ref stable --yes（从 GitHub 装，
                                         之后插件自己跟 stable 更新）；GitHub 装的低于 0.7.0：herdr plugin update
                                         com.codreamer.herdr.oncall --yes；本地链接的不动（版本低时提示 git pull）；
                                         在 herdr 里被禁用、插件自己的开关关着：不替你打开，只提示；旧 id 只提示
    <插件配置目录>/drover.env            herdr plugin config-dir 给的目录；整份覆盖（主会话名、编排者前缀、升级阈值、
                                         不引用的飞书消息送主会话、主会话只推飞书提问的回答和以【转达】开头的转达）。
                                         同目录的 .env（飞书配对、密钥）不读不写
    插件这一项失败（访问不了 GitHub、没有 Node、herdr 没在运行）只记这一项失败，其余照装，退出码 1
    在交互终端里装、接了飞书、插件装好且版本够、飞书还没配对时：装完问一句"现在用飞书扫码配对吗？[Y/n]"（默认是）：发起配对，
    打印配对链接（有 qrencode 就在终端里画二维码；Mac 桌面也会弹二维码），等你扫码确认（最多 5 分钟，Ctrl-C 跳过），
    配上后发一条测试消息。已经配对过的只说一句"飞书已配对"。非交互（agent 帮装、管道、--json）不问，只在"下一步"里
    提示 ~/.drover/bin/drover oncall pair；没接飞书、也没选过的，"下一步"里提示可以加 --oncall
  ~/.claude/commands/、~/.codex/skills/ 下别的命令和技能（包括你自己写的）install 和 --uninstall 都不碰，也不备份。
  验收工具（默认装；config.json 写 "quality_tools": false 或环境变量 DROVER_QUALITY_TOOLS=0 就不装、验收也不跑）：
    ~/.drover/tools/crapper、dryer       Robert C. Martin 的 crapper（圈复杂度）、dryer（重复代码），0BSD，钉提交：git clone 后切到
                                         钉的提交，用 Python 3.11+ 建 .venv、pip install -e，跑一次 --help。条目验收跑 crapper，
                                         集成检查（accept --integration）跑 dryer。每个约 18MB；第一次分析某种语言时再下载它的
                                         tree-sitter 语法包到 ~/.drover/tools/grammars
    Python 3.11+                         先找 PATH 上的 python3.14…python3.11、python3，再 uv python find；都没有就
                                         uv python install 3.12 --install-dir ~/.drover/tools/python --no-bin（不改 PATH）；
                                         连 uv 也没有就用官方脚本装一份到 ~/.drover/tools/bin（UV_NO_MODIFY_PATH=1，不改 shell 配置）
    装不上（没网、没 Python、pip 失败）只记警告：删掉半截的 .venv，install 退出码不变，验收时报"测不了"；重跑 install 或
    drover update 会按钉的版本补装。

--host 不给时自动判断：PATH 里有 claude 命令、或 ~/.claude 存在 → claude；PATH 里有 codex 命令、或 $CODEX_HOME
  （默认 ~/.codex）存在 → codex；都满足就都装。--check 不给 --host 时按安装记录里的宿主查。
--mode 默认 link：clone 留在本地，git pull 就是更新（flow/routing.md 变了要重跑 install 刷新区块）；
  copy 给不想保留 clone 的人，更新要拉新版本后重跑 install。
--profile 团队 profile（团队规则、编码规范、各仓库的 playbook，见包里的 profiles/README.md）：写名字用包里的
  profiles/<名>/，写路径（带 / 或以 ~ 开头）用那个目录；解析不到目录时直接报错、不写。--profile none 去掉这个键。
  不传时不动已有的 profile。个人覆盖目录（config 的 playbook_dirs）没有参数，手动编辑 config.json。

备份：原位置是实体文件 / 目录（或指向别处的软链）、内容和要装的不同时，先备份成 <原名>.bak-drover-<YYYYmmdd-HHMMSS>
  再换；内容相同的直接换成软链；已经装对的跳过（重跑幂等，不产生新备份）。改 settings、CLAUDE.md、往 AGENTS.md
  追加区块之前先复制一份备份（同一个文件一次运行只备份一份）；替换已有区块不备份（区块归 drover 管）。
  例外：~/.codex/skills 下的备份放到 ~/.drover/backups/codex-skills/（Codex 会递归扫描 skills 目录，
  放在原地会多出一个同名的 drover 技能）。

--dry-run：只打印每一项的路径、现状、要做什么、会不会备份；不写文件、不安装 herdr、不执行 herdr integration install、
  herdr plugin install / update（会执行只读的 herdr integration status、herdr plugin list；插件没装时不调会建目录的
  herdr plugin config-dir）。可以和 --uninstall 一起用。
--check：检查每一项装没装对，以及依赖（必需 herdr、git、python3，claude / codex 至少一个；可选 code-review-graph、
  uv、bun、pnpm）、herdr integration 状态、codex sandbox_mode、配置的 profile 能不能解析到目录（解析不到算没装对：
  团队规则会丢）、cd+git 拦截 hook、主会话登记 hook、读新规则 hook（没有、命令指向不对、脚本不在都算没装对）、飞书 oncall（接了的才查：没有
  Node、插件没装、drover.env 不在算没装对；Node 低于 22、插件版本低、被禁用、开关关着、drover.env 过期只提示；没接的不查、不算失败）、
  验收工具（没装、版本不对、.venv 坏了、没有 Python 3.11+ 只提示）。
  必需依赖缺失或有项没装对时退出码 1；
  没有 config.json（没装过）时主工作台里的几项报"查不了"、算没装对；
  路由区块过期、copy 副本过期、沙箱设置、主工作台是家目录只算提示。
--uninstall：删指向 drover 的软链（指向别处的不动）、copy 模式的副本、AGENTS.md 里的区块；install 新建的 herdr 技能、
  CLAUDE.md（内容没改过时）一并删；~/.drover/bin 删完是空目录时一并删；权限规则只删安装记录里 install 加的那几条，
  原来就有的不删；cd+git 拦截 hook、主会话登记 hook、读新规则 hook 也只删安装记录里的那一项（别的 hook 不动，PreToolUse、
  SessionStart、UserPromptSubmit、hooks 撤空了就去掉）。飞书 oncall：删 drover.env；插件只在安装记录里记着是 drover 装的才
  herdr plugin uninstall，原来就有的不动；插件的 .env（飞书配对）不删。验收工具：删整个 ~/.drover/tools。
  主工作台按安装记录和 config.json 找，都没有时不碰任何主工作台。保留：~/.drover/config.json、
  任务记录 ~/.drover/<slug>/、worktree、herdr 的 agent 状态 hook。.bak-drover-* 备份不自动还原，只列出来。
  带 --host 时只撤那个宿主的项；另一个宿主还装着时，共用的包、CLI 入口、路由区块保留。
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="install.py", usage=USAGE, epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
        description="把 drover 装进 Claude Code 和 / 或 Codex：/drover 命令、$drover 技能、drover CLI。"
                    "只用 python3 标准库。")
    p.add_argument("--host", choices=["claude", "codex", "both"],
                   help="装到哪个宿主；不给时按本机装了什么自动判断（规则见下文）")
    p.add_argument("--mode", choices=["link", "copy"], help="link（默认）：软链到这份 clone；copy：复制一份")
    p.add_argument("--workspace", metavar="DIR",
                   help="主工作台目录（你平时放各个仓库、开主会话的目录；主会话和编排者的工作目录，也是可信仓库根目录）。"
                        "没有默认值，不能是家目录或它的上级目录：首次安装不传时，交互终端里会问你，非交互时退出码 2；"
                        "已有 config.json 时沿用")
    p.add_argument("--repo-dir", metavar="DIR", action="append", default=[],
                   help="按名字找仓库的目录，可重复，按顺序找。默认就是 workspace；已有 config.json 时沿用")
    p.add_argument("--profile", metavar="NAME|DIR",
                   help="团队 profile：名字用包里的 profiles/<名>/，路径用那个目录；none 去掉。不传时不动已有配置")
    p.add_argument("--install-deps", action="store_true",
                   help="缺 herdr 时不问、直接用官方安装脚本装（只对安装有效；claude / codex 不替你装）")
    oncall = p.add_mutually_exclusive_group()
    oncall.add_argument("--oncall", action="store_true",
                        help="接飞书 oncall（选装）：从 GitHub 装 herdr-oncall 插件（要 Node 18+）、写 drover.env，终端里装完引导扫码"
                             "配对。记在 config.json 里，以后不带参数重跑、drover update 都照旧")
    oncall.add_argument("--no-oncall", action="store_true",
                        help="不接飞书 oncall（不装 herdr-oncall 插件、不写 drover.env；已经装上的插件不卸）。记在 config.json 里，"
                             "以后不带参数重跑也不装")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不写任何东西")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="检查安装状态和依赖；有问题时退出码 1")
    mode.add_argument("--uninstall", action="store_true", help="撤掉 install 装的东西（处理规则见下文）")
    p.add_argument("--json", action="store_true", help="输出一个 JSON 对象（给脚本和测试用），不打印文字")
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.check:
            return cmd_check(args)
        if args.uninstall:
            return cmd_uninstall(args)
        return cmd_install(args)
    except InstallError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc), **exc.extra}, ensure_ascii=False))
        else:
            print(f"install.py：{exc}", file=sys.stderr)
        return exc.rc


if __name__ == "__main__":
    sys.exit(main())
