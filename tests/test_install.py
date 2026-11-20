#!/usr/bin/env python3
"""install.py 的测试（标准库 unittest）：python3 tests/test_install.py

每个用例建一个临时目录当 HOME（主工作台也放在里面），用 subprocess 跑 install.py。PATH 最前面放一个假 herdr：
--skill 打印一段假 SKILL.md，integration status 打印可控的状态，integration install 只记日志、把状态改成 current。
绝不调用真 herdr（真 herdr 会连本机 server、往真实 HOME 写 hook），也不碰真实 HOME。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
INSTALL = PKG / "install.py"
INSTALL_SH = PKG / "install.sh"
ROUTING = PKG / "flow" / "routing.md"
INSTALLER_ENV = "DROVER_HERDR_INSTALLER"
UV_INSTALLER_ENV = "DROVER_UV_INSTALLER"
# 其余外部依赖（选定宿主的命令行、接飞书时的 Node）的安装命令：和 herdr 一样能用环境变量换掉。
DEP_INSTALLER_ENVS = {"claude": "DROVER_CLAUDE_INSTALLER", "codex": "DROVER_CODEX_INSTALLER", "node": "DROVER_NODE_INSTALLER"}
QUALITY_SOURCE_ENV = "DROVER_QUALITY_TOOLS_SOURCE"
QUALITY_SWITCH_ENV = "DROVER_QUALITY_TOOLS"
HERDR_OFFICIAL = "curl -fsSL https://herdr.dev/install.sh | sh"
CLAUDE_OFFICIAL = "curl -fsSL https://claude.ai/install.sh | bash"
CODEX_OFFICIAL = "curl -fsSL https://chatgpt.com/codex/install.sh | sh"
BEGIN = "<!-- drover:routing:begin -->"
END = "<!-- drover:routing:end -->"
FAKE_SKILL = "---\nname: herdr\ndescription: fake herdr skill for tests\n---\n\n# herdr（测试用的假技能）\n"
PLUGIN_ID = "com.codreamer.herdr.oncall"
ONCALL_INSTALL = "plugin install fulanto/herdr-oncall --ref stable --yes"
ONCALL_UPDATE = f"plugin update {PLUGIN_ID} --yes"
# contract.md 第 3 节：drover 写的 drover.env 一字不差就是这些。
DROVER_ENV = """\
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

FAKE_HERDR = '''#!{python}
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "herdr-state.json"
LOG = HERE / "herdr-calls.log"
PLUGIN = "com.codreamer.herdr.oncall"
PAIR_URL = "https://example.invalid/pair?user_code=TEST-0001"
args = sys.argv[1:]
with LOG.open("a") as f:
    f.write(" ".join(args) + "\\n")
state = json.loads(STATE.read_text())
plugins = state.setdefault("plugins", [])


def save():
    STATE.write_text(json.dumps(state))


def config_dir(plugin_id):
    return Path(os.environ["HOME"]) / ".config" / "herdr" / "plugins" / "config" / plugin_id


if args == ["--skill"]:
    sys.stdout.write({skill!r})
elif args[:2] == ["integration", "status"]:
    for name in ("pi", "claude", "codex", "letta (experimental)"):
        print(name + ": " + state.get(name, "not installed") + " (/fake/hooks/" + name + ".sh)")
elif args[:2] == ["integration", "install"] and len(args) == 3:
    state[args[2]] = "current (v99)"
    save()
    print("installed " + args[2] + " hook (fake)")
elif args[:2] == ["plugin", "list"] and "--json" in args:
    if state.get("plugin_list_fail"):
        print(json.dumps({"error": {"code": "server_unavailable", "message": "fake: server not running"}}), file=sys.stderr)
        sys.exit(1)
    want = args[args.index("--plugin") + 1] if "--plugin" in args else None
    print(json.dumps({"id": "cli:plugin", "result": {"type": "plugin_list",
                      "plugins": [p for p in plugins if want is None or p.get("plugin_id") == want]}}))
elif args[:2] == ["plugin", "install"]:
    if state.get("plugin_install_fail"):
        print("fake: could not reach github.com", file=sys.stderr)
        sys.exit(1)
    plugins.append({"plugin_id": PLUGIN, "version": state.get("install_version", "0.7.0"), "enabled": True,
                    "plugin_root": str(HERE / "plugin-root"),
                    "source": {"kind": "github", "owner": "fulanto", "repo": "herdr-oncall", "requested_ref": "stable"}})
    config_dir(PLUGIN).mkdir(parents=True, exist_ok=True)
    save()
    print("installed " + PLUGIN + " (fake)")
elif args[:2] == ["plugin", "update"] and len(args) >= 3:
    for p in plugins:
        if p.get("plugin_id") == args[2]:
            p["version"] = state.get("update_version", "0.7.0")
    save()
    print("updated " + args[2] + " (fake)")
elif args[:2] == ["plugin", "uninstall"] and len(args) >= 3:
    state["plugins"] = [p for p in plugins if p.get("plugin_id") != args[2]]
    save()
    print("uninstalled " + args[2] + " (fake)")
elif args[:2] == ["plugin", "config-dir"] and len(args) == 3:
    config_dir(args[2]).mkdir(parents=True, exist_ok=True)  # 和真的 herdr 一样：会建目录
    print(config_dir(args[2]))
elif args[:3] == ["plugin", "action", "invoke"] and len(args) >= 4:
    action = args[3]
    if action == "pair-feishu":
        out = ("feishu: already paired · app cli_fake · open_id ou_fake-paired-secret\\n" if state.get("feishu_paired")
               else "scan with the Feishu app, expires in 10 min:\\nopen: " + PAIR_URL + "\\nwaiting for the scan\\n")
        if state.get("pair_succeeds"):
            state["feishu_paired"] = True  # 模拟用户扫码确认
    else:
        out = action + " (fake)\\n"
    now = int(time.time() * 1000)
    logs = state.setdefault("plugin_logs", [])
    logs.append({"log_id": "plugin-log-%d" % len(logs), "plugin_id": PLUGIN, "status": "succeeded", "exit_code": 0,
                 "action_id": action, "command": ["/bin/bash", "bin/run-node.sh", "src/actions/" + action + ".mjs"],
                 "started_unix_ms": now, "finished_unix_ms": now, "stdout": out, "stderr": "",
                 "_running": state.get("action_running_polls", 0)})
    save()
    print(json.dumps({"id": "cli:plugin", "result": {"type": "ok"}}))
elif args[:3] == ["plugin", "log", "list"]:
    # 和真的 herdr 一样：动作没跑完时条目是 status: running，没有 stdout / stderr / exit_code / finished_unix_ms
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 50
    shown = []
    for entry in state.get("plugin_logs", [])[-limit:]:
        if entry.get("_running"):
            shown.append(dict({k: v for k, v in entry.items()
                               if k not in ("_running", "stdout", "stderr", "exit_code", "finished_unix_ms")},
                              status="running"))
            entry["_running"] -= 1
        else:
            shown.append({k: v for k, v in entry.items() if k != "_running"})
    save()
    print(json.dumps({"id": "cli:plugin", "result": {"type": "plugin_log_list", "logs": shown}}))
else:
    print("fake herdr: unsupported " + " ".join(args), file=sys.stderr)
    sys.exit(2)
'''

# 假 node：--version 打印同目录（软链进来的按真实位置）node-version.txt 里的版本（默认 v22.11.0）；测试"没有 Node"时整个删掉、
# PATH 里也去掉别的 node。
FAKE_NODE = '''#!{python}
from pathlib import Path
print((Path(__file__).resolve().parent / "node-version.txt").read_text(), end="")
'''
_shared_fakes: dict = {}  # 脚本内容 -> 这个进程里写好的那一份
_shared_dir = None  # 放这些脚本的进程级临时目录（Path），第一次用时建、进程退出时删


def place_fake(path: Path, body: str) -> None:
    """把内容是 body 的可执行脚本放到 path：每个进程只真正写一次，之后各用例的 path 是它的硬链接（链不上时复制）。
    macOS 上新写出来的可执行文件第一次执行要多花 0.07～0.3 秒（实测），每个用例都新写一份就每个用例都付一次；
    硬链接不改路径，假脚本按自己所在的目录找状态文件，各用例互不影响。和 selftest/selftest_fakes.py 的 place 同一个做法
    （两套测试互不导入）。用例要换内容时先 unlink 再写（直接写会改到同一个 inode）。"""
    global _shared_dir
    src = _shared_fakes.get(body)
    if src is None:
        if _shared_dir is None:
            _shared_dir = Path(tempfile.mkdtemp(prefix="drover-install-fakes-"))
            atexit.register(shutil.rmtree, _shared_dir, True)
        src = _shared_dir / f"fake-{len(_shared_fakes)}"
        src.write_text(body)
        src.chmod(0o755)
        _shared_fakes[body] = src
    try:
        os.link(src, path)
    except OSError:
        shutil.copy2(src, path)

GLOBAL_RULES = [
    "Bash(~/.drover/bin/drover:*)", "Read(~/.drover/**)", "Edit(~/.drover/**)",
    "Read(~/.herdr/worktrees/**)", "Edit(~/.herdr/worktrees/**)", "Bash(herdr agent:*)", "Bash(herdr pane:*)",
    "Bash(herdr worktree:*)", "Bash(herdr workspace list:*)", "Bash(herdr tab list:*)", "Bash(herdr status:*)",
]
WORKSPACE_RULES = ["Skill(drover)", "Skill(herdr)", "Bash(herdr workspace *)", "Bash(herdr tab *)"]
# 改名前的名字：新装后不能出现在任何入口、规则、计划路径里（用户自己写的同名命令 / 技能除外，install 不碰它们）。
OLD_NAMES = [".herdr/dispatch", "commands/dispatch", "skills/dispatch", "Skill(dispatch)", "bin/dispatch"]


class InstallTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="drover-install-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.ws = self.home / "ws"
        self.ws.mkdir(parents=True)
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()
        place_fake(self.fakebin / "herdr",
                   FAKE_HERDR.replace("{python}", sys.executable).replace("{skill!r}", repr(FAKE_SKILL)))
        # 假 claude：--check 要求 claude / codex 至少有一个；不会被调用。
        place_fake(self.fakebin / "claude", "#!/bin/sh\nexit 0\n")
        # 假 node：飞书 oncall 插件要 Node；默认 22（收得到飞书回复）。不让测试用上机器上真实的 node。
        place_fake(self.fakebin / "node", FAKE_NODE.replace("{python}", sys.executable))
        self.set_node("v22.11.0")
        self.set_herdr(claude="not installed", codex="not installed")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERDR_INSTALL_DIR", INSTALLER_ENV,
                                 "XDG_STATE_HOME", "XDG_CONFIG_HOME", "DROVER_STATE_DIR", QUALITY_SOURCE_ENV,
                                 UV_INSTALLER_ENV, "CODEX_INSTALL_DIR", "NVM_DIR", *DEP_INSTALLER_ENVS.values())}
        self.env.update(HOME=str(self.home), PATH=f"{self.fakebin}{os.pathsep}{os.environ.get('PATH', '')}",
                        PYTHONDONTWRITEBYTECODE="1")
        # 保险：默认把 herdr、uv、claude / codex、Node 的安装命令换成"禁止安装"，哪个用例意外触发安装都会记下来、用例报错，
        # 绝不联网。
        self.forbidden_log = self.tmp / "forbidden-installer.log"
        self.env[INSTALLER_ENV] = f"echo forbidden >> '{self.forbidden_log}'; exit 97"
        self.env[UV_INSTALLER_ENV] = f"echo forbidden-uv >> '{self.forbidden_log}'; exit 97"
        for name, var in DEP_INSTALLER_ENVS.items():
            self.env[var] = f"echo forbidden-{name} >> '{self.forbidden_log}'; exit 97"
        # 验收工具（crapper、dryer）默认不装：要从 GitHub clone、建 .venv。测它们的用例（QualityToolsTests）自己打开，
        # 工具源换成本地仓库、python 换成假的。
        self.env[QUALITY_SWITCH_ENV] = "0"
        self.addCleanup(self.assert_no_forbidden_install)
        d = self.home / ".drover"
        self.state_home = d
        self.pkg_dir = d / "pkg"
        self.bin = d / "bin" / "drover"
        self.config = d / "config.json"
        self.state = d / "drover-install.json"
        self.claude = self.home / ".claude"
        self.command = self.claude / "commands" / "drover.md"
        self.claude_skill = self.claude / "skills" / "herdr" / "SKILL.md"
        self.settings = self.claude / "settings.json"
        self.ws_settings = self.ws / ".claude" / "settings.local.json"
        self.codex = self.home / ".codex"
        self.skill = self.codex / "skills" / "drover"
        self.agents = self.ws / "AGENTS.md"
        self.claude_md = self.ws / "CLAUDE.md"
        self.oncall_dir = self.home / ".config" / "herdr" / "plugins" / "config" / PLUGIN_ID
        self.drover_env = self.oncall_dir / "drover.env"

    # ---- 工具

    def assert_no_forbidden_install(self) -> None:
        self.assertFalse(self.forbidden_log.exists(), "有用例意外执行了 herdr / uv / claude / codex / Node 的安装命令")

    def set_herdr(self, **states) -> None:
        """改假 herdr 的状态（合并进已有的状态：插件列表等不会被冲掉）。"""
        path = self.fakebin / "herdr-state.json"
        cur = json.loads(path.read_text()) if path.exists() else {}
        cur.update(states)
        path.write_text(json.dumps(cur))

    def herdr_state(self) -> dict:
        return json.loads((self.fakebin / "herdr-state.json").read_text())

    def set_node(self, version) -> None:
        (self.fakebin / "node-version.txt").write_text(version + "\n")

    def plugin_entry(self, version: str = "0.7.0", source: str = "github", enabled: bool = True) -> dict:
        return {"plugin_id": PLUGIN_ID, "version": version, "enabled": enabled,
                "plugin_root": str(self.tmp / "plugin-root"), "source": {"kind": source}}

    def set_plugin(self, **kw) -> None:
        """假 herdr 里"已经装着"插件（用户自己装的）：配置目录照真的一样已经在。"""
        self.set_herdr(plugins=[self.plugin_entry(**kw)])
        self.oncall_dir.mkdir(parents=True, exist_ok=True)

    def plugin_calls(self) -> list:
        return [c for c in self.herdr_calls() if c.startswith(("plugin install", "plugin update", "plugin uninstall"))]

    def herdr_calls(self) -> list:
        log = self.fakebin / "herdr-calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def run_install(self, *args: str, rc: int = 0, env: dict = None) -> subprocess.CompletedProcess:
        p = subprocess.run([sys.executable, str(INSTALL), *args], env=env or self.env, cwd=str(self.tmp),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        if rc is not None and p.returncode != rc:
            self.fail(f"install.py {' '.join(args)} 退出码 {p.returncode}（期望 {rc}）\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
        return p

    def install_json(self, *args: str, rc: int = 0, env: dict = None) -> dict:
        return json.loads(self.run_install(*args, "--json", rc=rc, env=env).stdout)

    def pty_run(self, argv: list, env: dict, answers: list) -> tuple:
        """在伪终端里跑（stdin / stdout 都是终端，走"问一句"的分支）。answers 是按顺序的 [(提问里的字节, 回答)]：
        看到提问就回答，下一问只在这之后的输出里找。回答是 str 时发一行；bytes 原样发（b"\\x04" 是 Ctrl-D）；
        信号（signal.SIGINT 当 Ctrl-C）直接发给子进程（它不以这个伪终端为控制终端，按 Ctrl-C 键不会产生信号）。
        返回 (退出码, 输出, 回答了几个)。"""
        import pty
        import select
        master, slave = pty.openpty()
        p = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=str(self.tmp), close_fds=True)
        os.close(slave)
        out, pos, answered, deadline = b"", 0, 0, time.time() + 90
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break  # 子进程关掉了终端
                if not chunk:
                    break
                out += chunk
                if answered < len(answers) and answers[answered][0] in out[pos:]:
                    reply = answers[answered][1]
                    if isinstance(reply, str):
                        os.write(master, reply.encode() + b"\n")
                    elif isinstance(reply, bytes):
                        os.write(master, reply)
                    else:
                        time.sleep(0.5)  # 等子进程进到读输入那一步：信号早到了会被错过
                        p.send_signal(reply)
                    pos, answered = len(out), answered + 1
            elif p.poll() is not None:
                break
        else:
            p.kill()
            self.fail(f"在伪终端里没跑完：{out.decode(errors='replace')[-500:]}")
        rc = p.wait(timeout=30)
        os.close(master)
        return rc, out.decode(errors="replace"), answered

    def ws_args(self) -> list:
        return ["--workspace", str(self.ws)]

    @staticmethod
    def write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def write_json(self, path: Path, data: dict) -> None:
        self.write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def load(path: Path) -> dict:
        return json.loads(path.read_text())

    @staticmethod
    def backups(directory: Path, name: str) -> list:
        return sorted(directory.glob(f"{name}.bak-drover-*")) if directory.is_dir() else []

    def all_backups(self) -> list:
        return sorted(p for p in self.home.rglob("*.bak-drover-*"))

    def snapshot(self) -> dict:
        """临时 HOME 下每个路径的类型、内容哈希、软链指向和 mtime。"""
        snap = {}
        for dirpath, dirnames, filenames in os.walk(self.home):
            for name in dirnames + filenames:
                p = Path(dirpath) / name
                st = p.lstat()
                if p.is_symlink():
                    snap[str(p)] = ("link", os.readlink(p), st.st_mtime_ns)
                elif p.is_dir():
                    snap[str(p)] = ("dir",)
                else:
                    snap[str(p)] = ("file", hashlib.sha256(p.read_bytes()).hexdigest(), st.st_mtime_ns)
        return snap

    def snapshot_of(self, *roots: Path) -> dict:
        """snapshot() 里落在 roots（含它们自己）下面的那部分。"""
        prefixes = [str(r) for r in roots]
        return {k: v for k, v in self.snapshot().items()
                if any(k == p or k.startswith(p + os.sep) for p in prefixes)}

    def home_entries(self) -> list:
        """临时 HOME 下所有路径（不跟进软链目录）。"""
        out = []
        for dirpath, dirnames, filenames in os.walk(self.home):
            out += [Path(dirpath) / n for n in dirnames + filenames]
        return out

    def step(self, out: dict, key: str) -> dict:
        found = [s for s in out["steps"] if s["key"] == key]
        self.assertEqual(len(found), 1, f"计划里应该正好有一项 {key}：{out['steps']}")
        return found[0]

    def assert_link(self, link: Path, target: Path) -> None:
        self.assertTrue(link.is_symlink(), f"{link} 应该是软链")
        self.assertEqual(os.readlink(link), str(target))

    def hook_command(self) -> str:
        """drover 往 ~/.claude/settings.json 加的 cd+git 拦截 hook 命令：指向装好的包（~/.drover/pkg），不是 clone。"""
        return f"python3 -I {self.pkg_dir / 'hooks' / 'cd_git_guard.py'} || true"

    def hook_entry(self) -> dict:
        return {"type": "command", "command": self.hook_command(), "timeout": 5}

    def session_command(self) -> str:
        """主会话登记 hook（SessionStart）的命令：同样指向装好的包。"""
        return f"python3 -I {self.pkg_dir / 'bin' / 'drover'} main-register --hook || true"

    def session_entry(self) -> dict:
        return {"type": "command", "command": self.session_command(), "timeout": 15}

    def prompt_command(self) -> str:
        """主会话读新规则 hook（UserPromptSubmit）的命令：同样指向装好的包。"""
        return f"python3 -I {self.pkg_dir / 'bin' / 'drover'} rules --hook || true"

    def prompt_entry(self) -> dict:
        return {"type": "command", "command": self.prompt_command(), "timeout": 10}

    def expected_routing_body(self) -> str:
        """按 flow/routing.md 当前内容算出的区块正文：去掉开头的维护者注释，占位符换成这个用例的主工作台。
        不写死模板里的文字（模板会改），只验证区块标记和占位符渲染。"""
        text = re.sub(r"\A\s*<!--.*?-->\s*", "", ROUTING.read_text(), count=1, flags=re.S)
        text = re.sub(r"\{\{\s*workspace_abs\s*\}\}", str(self.ws), text)
        return re.sub(r"\{\{\s*workspace\s*\}\}", "~/ws", text).strip()

    def assert_block(self, text: str) -> None:
        self.assertEqual(text.count(BEGIN), 1)
        self.assertEqual(text.count(END), 1)
        block = text[text.index(BEGIN) + len(BEGIN):text.index(END)]
        self.assertIn(self.expected_routing_body(), block)
        self.assertNotIn("{{", block)


# ---------------------------------------------------------------- link 模式


class LinkInstallTests(InstallTestBase):
    def test_link_claude(self) -> None:
        self.write_json(self.settings, {"model": "opus", "permissions": {
            "allow": ["Bash(git:*)", "Read(~/.drover/**)"], "deny": ["Bash(rm:*)"]}, "hooks": {"Stop": []}})
        self.write_json(self.ws_settings, {"permissions": {"allow": ["Skill(code-review)"]},
                                           "enabledMcpjsonServers": ["code-review-graph"]})
        before_settings = self.settings.read_text()
        out = self.install_json("--host", "claude", "--mode", "link", *self.ws_args())
        self.assertTrue(out["ok"])
        self.assertEqual(out["hosts"], ["claude"])

        self.assert_link(self.pkg_dir, PKG)
        self.assert_link(self.bin, self.pkg_dir / "bin" / "drover")
        self.assertEqual(self.bin.resolve(), PKG / "bin" / "drover")
        self.assert_link(self.command, self.pkg_dir / "flow" / "dispatch.md")
        self.assertEqual(self.claude_skill.read_text(), FAKE_SKILL)
        self.assertEqual(self.load(self.config), {"workspace": "~/ws", "repo_dirs": ["~/ws"]})

        settings = self.load(self.settings)
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(settings["hooks"]["Stop"], [])  # 已有的 hook 键原样保留，只多了 PreToolUse
        self.assertEqual(settings["hooks"]["PreToolUse"], [{"matcher": "Bash", "hooks": [self.hook_entry()]}])
        self.assertEqual(settings["permissions"]["deny"], ["Bash(rm:*)"])
        allow = settings["permissions"]["allow"]
        self.assertEqual(allow[:2], ["Bash(git:*)", "Read(~/.drover/**)"])  # 已有的不动、不重排
        for rule in GLOBAL_RULES:
            self.assertEqual(allow.count(rule), 1, rule)
        bak = self.backups(self.claude, "settings.json")
        self.assertEqual(len(bak), 1)
        self.assertEqual(bak[0].read_text(), before_settings)

        ws_settings = self.load(self.ws_settings)
        self.assertEqual(ws_settings["enabledMcpjsonServers"], ["code-review-graph"])
        self.assertEqual(ws_settings["permissions"]["allow"], ["Skill(code-review)", *WORKSPACE_RULES])
        self.assertEqual(len(self.backups(self.ws / ".claude", "settings.local.json")), 1)

        self.assertEqual(self.claude_md.read_text(), "@AGENTS.md\n")
        text = self.agents.read_text()
        self.assertTrue(text.startswith(BEGIN))  # 文件不存在 → 新建，只有区块
        self.assert_block(text)

        calls = self.herdr_calls()
        self.assertIn("integration install claude", calls)
        self.assertNotIn("integration install codex", calls)
        self.assertFalse(self.codex.exists())
        self.assertEqual(self.load(self.state)["hosts"], ["claude"])

    def test_link_codex_with_codex_home(self) -> None:
        codex_home = self.home / "custom-codex"
        self.write(codex_home / "config.toml", 'model = "x"\nsandbox_mode = "workspace-write"\n\n[tui]\nfoo = 1\n')
        env = dict(self.env, CODEX_HOME=str(codex_home))
        p = self.run_install("--host", "codex", *self.ws_args(), env=env)
        self.assertIn("danger-full-access", p.stdout)  # 沙箱只提示
        self.assertIn("sandbox_mode = workspace-write", p.stdout)

        self.assert_link(codex_home / "skills" / "drover", self.pkg_dir / "codex" / "skills" / "drover")
        self.assertTrue((codex_home / "skills" / "drover" / "SKILL.md").is_file())
        herdr_skill = codex_home / "skills" / "herdr"
        self.assertFalse(herdr_skill.is_symlink())  # 没有 ~/.claude/skills/herdr → 用 herdr --skill 写一份
        self.assertEqual((herdr_skill / "SKILL.md").read_text(), FAKE_SKILL)
        self.assertFalse(self.codex.exists())  # CODEX_HOME 优先，不碰 ~/.codex
        self.assertFalse(self.claude.exists())
        self.assertFalse(self.claude_md.exists())
        self.assertIn("integration install codex", self.herdr_calls())
        self.assertNotIn("integration install claude", self.herdr_calls())
        self.assert_block(self.agents.read_text())
        self.assertIn("sandbox_mode", (codex_home / "config.toml").read_text())  # 不改 config.toml
        self.assertIn('sandbox_mode = "workspace-write"', (codex_home / "config.toml").read_text())

    def test_link_both(self) -> None:
        self.set_herdr(claude="current (v11)", codex="current (v10)")
        out = self.install_json("--host", "both", *self.ws_args(), "--repo-dir", str(self.ws / "team"),
                                "--repo-dir", str(self.ws))
        self.assertTrue(out["ok"])
        self.assertEqual(out["hosts"], ["claude", "codex"])
        self.assert_link(self.command, self.pkg_dir / "flow" / "dispatch.md")
        self.assert_link(self.skill, self.pkg_dir / "codex" / "skills" / "drover")
        # Codex 的 herdr 技能软链到 Claude Code 那份（那份是同一次安装里先生成的）
        self.assert_link(self.codex / "skills" / "herdr", self.claude / "skills" / "herdr")
        self.assertEqual((self.codex / "skills" / "herdr" / "SKILL.md").read_text(), FAKE_SKILL)
        self.assertEqual(self.load(self.config), {"workspace": "~/ws", "repo_dirs": ["~/ws/team", "~/ws"]})
        # 两个 hook 都已经是 current：不执行 integration install
        self.assertFalse([c for c in self.herdr_calls() if c.startswith("integration install")])
        self.assertEqual(self.step(out, "claude-integration")["changes"], False)
        self.assertEqual(self.load(self.state)["hosts"], ["claude", "codex"])


# ---------------------------------------------------------------- copy 模式


class CopyInstallTests(InstallTestBase):
    def test_copy_both(self) -> None:
        out = self.install_json("--host", "both", "--mode", "copy", *self.ws_args())
        self.assertTrue(out["ok"])
        self.assertFalse(self.pkg_dir.is_symlink())
        self.assertTrue(self.pkg_dir.is_dir())
        self.assertTrue((self.pkg_dir / ".drover-copy").is_file())
        self.assertFalse((self.pkg_dir / ".git").exists())  # worktree 的 .git 文件不复制
        # 标记记下复制自哪个 clone、它当时的 HEAD（bin/drover 的 drover_version 在 copy 模式读 commit）
        head = subprocess.run(["git", "-C", str(PKG), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        marker = json.loads((self.pkg_dir / ".drover-copy").read_text())
        self.assertEqual((marker["source"], marker["commit"]), (str(PKG), head))
        for rel in ["bin/drover", "flow/dispatch.md", "flow/routing.md", "codex/skills/drover/SKILL.md",
                    "install.py"]:
            self.assertEqual((self.pkg_dir / rel).read_bytes(), (PKG / rel).read_bytes(), rel)
        self.assertTrue(os.access(self.pkg_dir / "bin" / "drover", os.X_OK))
        # 入口都经 ~/.drover/pkg 指过去，解析后落在副本里
        self.assert_link(self.bin, self.pkg_dir / "bin" / "drover")
        self.assertEqual(self.bin.resolve(), (self.pkg_dir / "bin" / "drover").resolve())
        self.assert_link(self.command, self.pkg_dir / "flow" / "dispatch.md")
        self.assertEqual(self.command.resolve(), (self.pkg_dir / "flow" / "dispatch.md").resolve())
        self.assert_link(self.skill, self.pkg_dir / "codex" / "skills" / "drover")
        self.assertEqual(self.skill.resolve(), (self.pkg_dir / "codex" / "skills" / "drover").resolve())
        self.assertEqual(self.load(self.state)["mode"], "copy")

        # 重跑：副本和包一致 → 什么都不动
        again = self.install_json("--host", "both", "--mode", "copy", *self.ws_args())
        self.assertEqual([s["key"] for s in again["steps"] if s["changes"]], [])

        # 副本过期（模拟包更新）→ 重跑时直接替换旧副本，不备份
        (self.pkg_dir / "flow" / "dispatch.md").write_text("旧版本\n")
        third = self.install_json("--host", "both", "--mode", "copy", *self.ws_args())
        self.assertTrue(self.step(third, "pkg")["changes"])
        self.assertIsNone(self.step(third, "pkg")["backup"])
        self.assertEqual((self.pkg_dir / "flow" / "dispatch.md").read_bytes(),
                         (PKG / "flow/dispatch.md").read_bytes())
        self.assertEqual(self.backups(self.state_home, "pkg"), [])

    def test_switch_link_to_copy_and_back(self) -> None:
        self.install_json("--host", "claude", *self.ws_args())
        self.assertTrue(self.pkg_dir.is_symlink())
        self.install_json("--host", "claude", "--mode", "copy", *self.ws_args())
        self.assertFalse(self.pkg_dir.is_symlink())
        self.install_json("--host", "claude", "--mode", "link", *self.ws_args())
        self.assert_link(self.pkg_dir, PKG)
        self.assertEqual(self.backups(self.state_home, "pkg"), [])  # 两次切换都不留备份
        self.assertEqual(self.all_backups(), [])


# ---------------------------------------------------------------- 备份与幂等


class BackupIdempotencyTests(InstallTestBase):
    def test_backup_then_idempotent(self) -> None:
        self.write(self.command, "旧的 drover 命令\n")
        self.write(self.bin, "#!/bin/sh\necho old\n")
        old_skill = self.skill / "SKILL.md"
        self.write(old_skill, "---\nname: drover\n---\n旧的技能\n")
        self.write_json(self.settings, {"permissions": {"allow": ["Bash(git:*)"]}})
        self.set_herdr(claude="current (v11)", codex="outdated (v9)")

        first = self.install_json("--host", "both", *self.ws_args())
        self.assertTrue(first["ok"])
        cmd_bak = self.backups(self.command.parent, "drover.md")
        self.assertEqual(len(cmd_bak), 1)
        self.assertEqual(cmd_bak[0].read_text(), "旧的 drover 命令\n")
        bin_bak = self.backups(self.bin.parent, "drover")
        self.assertEqual(len(bin_bak), 1)
        self.assertEqual(bin_bak[0].read_text(), "#!/bin/sh\necho old\n")
        # Codex 会扫描 skills 目录：技能目录的备份不放在原地，放到 ~/.drover/backups/codex-skills/
        self.assertEqual(self.backups(self.codex / "skills", "drover"), [])
        skill_bak = self.backups(self.state_home / "backups" / "codex-skills", "drover")
        self.assertEqual(len(skill_bak), 1)
        self.assertEqual((skill_bak[0] / "SKILL.md").read_text(), "---\nname: drover\n---\n旧的技能\n")
        self.assert_link(self.command, self.pkg_dir / "flow" / "dispatch.md")
        self.assert_link(self.bin, self.pkg_dir / "bin" / "drover")
        self.assertIn("integration install codex", self.herdr_calls())  # 不是 current 就装
        self.assertNotIn("integration install claude", self.herdr_calls())

        backups_before = self.all_backups()
        agents_before = self.agents.read_bytes()
        second = self.install_json("--host", "both", *self.ws_args())
        self.assertTrue(second["ok"])
        self.assertEqual([s["key"] for s in second["steps"] if s["changes"]], [])  # 全部跳过
        self.assertEqual(self.all_backups(), backups_before)  # 不产生新备份
        self.assertEqual(self.agents.read_bytes(), agents_before)
        for path, rules in [(self.settings, GLOBAL_RULES), (self.ws_settings, WORKSPACE_RULES)]:
            allow = self.load(path)["permissions"]["allow"]
            for rule in rules:
                self.assertEqual(allow.count(rule), 1, f"{path.name} 里 {rule} 重复了")

    def test_same_content_replaced_without_backup(self) -> None:
        self.write(self.command, (PKG / "flow" / "dispatch.md").read_text())
        out = self.install_json("--host", "claude", *self.ws_args())
        step = self.step(out, "claude-command")
        self.assertTrue(step["changes"])
        self.assertIsNone(step["backup"])
        self.assert_link(self.command, self.pkg_dir / "flow" / "dispatch.md")
        self.assertEqual(self.backups(self.command.parent, "drover.md"), [])

    def test_existing_config_kept_unless_explicit(self) -> None:
        self.write_json(self.config, {"workspace": "~/ws", "repo_dirs": ["~/ws/a"], "extra": 1})
        self.install_json("--host", "claude")  # 不传 --workspace / --repo-dir：沿用
        self.assertEqual(self.load(self.config), {"workspace": "~/ws", "repo_dirs": ["~/ws/a"], "extra": 1})
        self.assert_block(self.agents.read_text())  # workspace 取自已有配置
        self.install_json("--host", "claude", "--repo-dir", str(self.ws / "b"))  # 只改显式传的键
        self.assertEqual(self.load(self.config), {"workspace": "~/ws", "repo_dirs": ["~/ws/b"], "extra": 1})
        self.assertEqual(len(self.backups(self.state_home, "config.json")), 1)


# ---------------------------------------------------------------- AGENTS.md 区块


class AgentsMdTests(InstallTestBase):
    def test_personal_without_markers_appends_block(self) -> None:
        personal = "# 我的工作台\n\n个人规则：仓库在 ~/ws/company 下。\n"
        self.write(self.agents, personal)
        p = self.run_install("--host", "claude", *self.ws_args())
        self.assertIn("已有内容可能和路由规则重复，请检查", p.stdout)
        text = self.agents.read_text()
        self.assertTrue(text.startswith(personal.rstrip("\n") + "\n\n" + BEGIN))  # 个人内容一字不改
        self.assert_block(text)
        bak = self.backups(self.ws, "AGENTS.md")
        self.assertEqual(len(bak), 1)
        self.assertEqual(bak[0].read_text(), personal)

    def test_existing_block_only_block_replaced(self) -> None:
        head = "# 我的工作台\n\n上面的个人内容\n\n"
        tail = "\n\n## 本机补充\n\n- 下面的个人内容\n"
        self.write(self.agents, head + BEGIN + "\n旧的路由规则\n" + END + tail)
        out = self.install_json("--host", "claude", *self.ws_args())
        self.assertIsNone(self.step(out, "routing")["backup"])  # 替换区块不备份
        text = self.agents.read_text()
        self.assertTrue(text.startswith(head + BEGIN))
        self.assertTrue(text.endswith(END + tail))
        self.assertNotIn("旧的路由规则", text)
        self.assert_block(text)
        self.assertEqual(self.backups(self.ws, "AGENTS.md"), [])

    def test_unpaired_markers_left_alone(self) -> None:
        broken = "# 工作台\n" + BEGIN + "\n没有结束标记\n"
        self.write(self.agents, broken)
        out = self.install_json("--host", "claude", *self.ws_args(), rc=1)
        self.assertFalse(out["ok"])
        self.assertEqual(self.step(out, "routing")["level"], "fail")
        self.assertEqual(self.agents.read_text(), broken)

    def test_claude_md_appended(self) -> None:
        self.write(self.claude_md, "# 个人 CLAUDE.md\n不以换行结尾")
        self.install_json("--host", "claude", *self.ws_args())
        self.assertEqual(self.claude_md.read_text(), "# 个人 CLAUDE.md\n不以换行结尾\n@AGENTS.md\n")
        self.assertEqual(len(self.backups(self.ws, "CLAUDE.md")), 1)


# ---------------------------------------------------------------- --check


class CheckTests(InstallTestBase):
    def test_check_ok_then_missing_link(self) -> None:
        self.install_json("--host", "both", *self.ws_args())
        ok = self.install_json("--check")  # 不传 --host：按安装记录查两个宿主
        self.assertTrue(ok["ok"], json.dumps(ok, ensure_ascii=False, indent=1))
        self.assertEqual(ok["hosts"], ["claude", "codex"])
        self.assertNotIn("fail", ok["verdicts"])
        self.assertEqual(self.step(ok, "chain")["level"], "ok")
        self.assertEqual(self.run_install("--check").returncode, 0)

        self.command.unlink()
        bad = self.install_json("--check", rc=1)
        self.assertFalse(bad["ok"])
        failed = [s["key"] for s, v in zip(bad["steps"], bad["verdicts"]) if v == "fail"]
        self.assertEqual(failed, ["claude-command"])
        p = self.run_install("--check", rc=1)
        self.assertIn("[✗] Claude Code 的 /drover 命令  ~/.claude/commands/drover.md", p.stdout)
        self.assertIn("没装对：Claude Code 的 /drover 命令", p.stdout)

    def test_check_reports_integration_and_stale_block(self) -> None:
        self.install_json("--host", "claude", *self.ws_args(), "--oncall")
        self.set_herdr(claude="outdated (v3)")
        text = self.agents.read_text().replace(END, "区块里被手改的一行\n" + END)
        self.agents.write_text(text)
        calls_before = len(self.herdr_calls())
        out = self.install_json("--check", rc=1)
        verdicts = dict(zip([s["key"] for s in out["steps"]], out["verdicts"]))
        self.assertEqual(verdicts["claude-integration"], "fail")
        self.assertEqual(verdicts["routing"], "warn")  # 区块过期只提示
        # --check 只查不装：只有只读的 integration status、plugin list、config-dir（插件装着时目录本来就在）
        self.assertEqual(sorted(set(self.herdr_calls()[calls_before:])),
                         ["integration status", "plugin config-dir " + PLUGIN_ID, "plugin list --json"])


# ---------------------------------------------------------------- --profile


class ProfileTests(InstallTestBase):
    def test_profile_written_on_fresh_install(self) -> None:
        out = self.install_json("--host", "claude", *self.ws_args(), "--profile", "example")
        self.assertTrue(out["ok"])
        self.assertEqual(out["profile"], "example")
        self.assertEqual(self.load(self.config), {"workspace": "~/ws", "repo_dirs": ["~/ws"], "profile": "example"})
        step = self.step(out, "profile")
        self.assertEqual(step["level"], "ok")
        self.assertEqual(step["path"], str(PKG / "profiles" / "example"))
        # 包里的 profile 解析得到，--check 也认（按装好的包解析）
        check = self.install_json("--check")
        self.assertEqual(dict(zip([s["key"] for s in check["steps"]], check["verdicts"]))["profile"], "ok")

    def test_profile_only_changes_that_key(self) -> None:
        self.write_json(self.config, {"workspace": "~/ws", "repo_dirs": ["~/ws/a"], "extra": 1})
        out = self.install_json("--host", "claude", "--profile", "example")
        self.assertIn("只改显式传的键", self.step(out, "config")["action"])
        self.assertEqual(self.load(self.config),
                         {"workspace": "~/ws", "repo_dirs": ["~/ws/a"], "extra": 1, "profile": "example"})
        self.assertEqual(len(self.backups(self.state_home, "config.json")), 1)  # 改之前备份
        # 再装一次不传 --profile：沿用，不再备份
        self.install_json("--host", "claude")
        self.assertEqual(self.load(self.config)["profile"], "example")
        self.assertEqual(len(self.backups(self.state_home, "config.json")), 1)
        # 其他项都已装好时，--dry-run --profile 只计划改 config（外加安装记录），不写
        team = self.home / "team-profile"
        (team / "repos").mkdir(parents=True)
        before = self.snapshot()
        dry = self.install_json("--host", "claude", "--profile", str(team), "--dry-run")
        self.assertEqual([s["key"] for s in dry["steps"] if s["changes"]], ["config", "state"])
        self.assertEqual(self.snapshot(), before)
        # 写路径：存成 ~/… 的绝对形式，其他键不动
        self.install_json("--host", "claude", "--profile", str(team))
        self.assertEqual(self.load(self.config),
                         {"workspace": "~/ws", "repo_dirs": ["~/ws/a"], "extra": 1, "profile": "~/team-profile"})
        self.assertEqual(len(self.backups(self.state_home, "config.json")), 2)

    def test_profile_none_clears_key(self) -> None:
        self.write_json(self.config, {"workspace": "~/ws", "repo_dirs": ["~/ws"], "profile": "example", "extra": 1})
        dry = self.install_json("--host", "claude", "--profile", "none", "--dry-run")
        self.assertIn("去掉 profile", self.step(dry, "config")["action"])
        self.assertEqual(self.load(self.config)["profile"], "example")  # dry-run 不写
        out = self.install_json("--host", "claude", "--profile", "none")
        self.assertIsNone(out["profile"])
        self.assertEqual(self.load(self.config), {"workspace": "~/ws", "repo_dirs": ["~/ws"], "extra": 1})
        self.assertEqual(len(self.backups(self.state_home, "config.json")), 1)
        self.assertEqual(self.step(out, "profile")["level"], "ok")  # 没配置 profile 不算错

    def test_profile_not_found(self) -> None:
        self.install_json("--host", "claude", *self.ws_args())
        before = self.config.read_text()
        # 显式传一个解析不到的 profile：直接报错，不写
        p = self.run_install("--host", "claude", "--profile", "no-such-team", rc=2)
        self.assertIn("no-such-team", p.stderr)
        self.assertIn("包里有的 profile：example；", p.stderr)  # 列出包里有的 profile
        self.assertEqual(self.config.read_text(), before)
        # config 里手写了一个不存在的 profile：--check 报 ✗
        self.write_json(self.config, {"workspace": "~/ws", "repo_dirs": ["~/ws"], "profile": "no-such-team"})
        out = self.install_json("--check", rc=1)
        self.assertFalse(out["ok"])
        verdicts = dict(zip([s["key"] for s in out["steps"]], out["verdicts"]))
        self.assertEqual(verdicts["profile"], "fail")
        self.assertEqual([k for k, v in verdicts.items() if v == "fail"], ["profile"])
        text = self.run_install("--check", rc=1).stdout
        self.assertIn("[✗] 团队 profile", text)
        self.assertIn("没装对：团队 profile", text)
        # 路径形式的 profile 目录不存在也一样
        self.write_json(self.config, {"workspace": "~/ws", "repo_dirs": ["~/ws"], "profile": "~/gone-profile"})
        out = self.install_json("--check", rc=1)
        self.assertEqual(self.step(out, "profile")["level"], "fail")
        self.assertIn("gone-profile", self.step(out, "profile")["path"])


# ---------------------------------------------------------------- --uninstall


class UninstallTests(InstallTestBase):
    def test_uninstall_link_both(self) -> None:
        personal = "# 我的工作台\n\n个人内容\n"
        self.write(self.agents, personal)
        self.write_json(self.settings, {"model": "opus", "permissions": {"allow": ["Read(~/.drover/**)"]}})
        ws_settings = {"permissions": {"allow": ["Skill(code-review)"]}, "enabledMcpjsonServers": ["x"]}
        self.write_json(self.ws_settings, ws_settings)
        task = self.state_home / "some-task" / "dispatch.md"
        self.write(task, "# dispatch: some-task\n")
        wt_file = self.home / ".herdr" / "worktrees" / "repo" / "task-x" / "a.txt"
        self.write(wt_file, "worktree 里的改动\n")
        self.install_json("--host", "both", *self.ws_args())
        self.assertTrue(self.pkg_dir.is_symlink())

        p = self.run_install("--uninstall")
        for gone in [self.pkg_dir, self.bin, self.command, self.skill, self.claude_skill,
                     self.codex / "skills" / "herdr", self.claude_md, self.state]:
            self.assertFalse(gone.exists() or gone.is_symlink(), f"{gone} 应该被撤掉")
        self.assertTrue(PKG.is_dir())  # clone 本身不动
        self.assertEqual(self.agents.read_text(), personal)  # 区块外内容不变，区块去掉
        settings = self.load(self.settings)
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(settings["permissions"]["allow"], ["Read(~/.drover/**)"])  # 原来就有的规则保留
        self.assertEqual(self.load(self.ws_settings), ws_settings)
        self.assertTrue(self.config.is_file())  # config.json 保留
        self.assertEqual(task.read_text(), "# dispatch: some-task\n")  # 任务记录不动
        self.assertEqual(wt_file.read_text(), "worktree 里的改动\n")
        self.assertIn("备份（不自动还原", p.stdout)
        self.assertIn("settings.json.bak-drover-", p.stdout)
        self.assertIn("AGENTS.md.bak-drover-", p.stdout)
        self.assertEqual(self.backups(self.claude, "settings.json")[0].exists(), True)  # 备份留着

    def test_uninstall_copy_and_new_files(self) -> None:
        self.install_json("--host", "claude", "--mode", "copy", *self.ws_args())
        self.assertTrue((self.pkg_dir / ".drover-copy").is_file())
        self.assertTrue(self.ws_settings.exists())
        out = self.install_json("--uninstall")
        self.assertTrue(out["ok"])
        self.assertFalse(self.pkg_dir.exists())  # copy 的副本删掉
        self.assertFalse(self.bin.is_symlink())
        self.assertFalse(self.agents.exists())  # install 新建的 AGENTS.md，去掉区块后为空 → 删
        self.assertFalse(self.ws_settings.exists())  # install 新建的 settings.local.json，规则删完为空 → 删
        self.assertFalse(self.settings.exists())  # ~/.claude/settings.json 同理

    def test_uninstall_one_host_keeps_shared(self) -> None:
        self.install_json("--host", "both", *self.ws_args())
        self.install_json("--uninstall", "--host", "claude")
        self.assertFalse(self.command.is_symlink())
        # Codex 还装着：包、CLI 入口、路由区块、被 Codex 软链引用的 herdr 技能都留着
        self.assertTrue(self.pkg_dir.is_symlink())
        self.assertTrue(self.bin.is_symlink())
        self.assertTrue(self.claude_skill.is_file())
        self.assertIn(BEGIN, self.agents.read_text())
        self.assertEqual(self.load(self.state)["hosts"], ["codex"])
        self.assertEqual(self.install_json("--check")["hosts"], ["codex"])

    def test_uninstall_leaves_foreign_links(self) -> None:
        other = self.home / "elsewhere.md"
        self.write(other, "别人的命令\n")
        self.command.parent.mkdir(parents=True)
        os.symlink(str(other), str(self.command))
        out = self.install_json("--uninstall", "--host", "claude")
        self.assertEqual(self.step(out, "claude-command")["changes"], False)
        self.assertTrue(self.command.is_symlink())
        self.assertFalse(self.state.exists())  # 没有安装记录时不凭空写一份


# ---------------------------------------------------------------- --dry-run


class DryRunTests(InstallTestBase):
    def test_dry_run_writes_nothing(self) -> None:
        self.write(self.agents, "# 个人\n")
        self.write(self.command, "旧命令\n")
        self.write_json(self.settings, {"permissions": {"allow": []}})
        before = self.snapshot()
        out = self.install_json("--dry-run", "--host", "both", "--mode", "link", *self.ws_args(),
                                "--repo-dir", str(self.ws))
        self.assertEqual(out["command"], "dry-run")
        self.assertTrue(self.step(out, "claude-command")["backup"])  # 计划里写明会备份
        self.assertIn("追加", self.step(out, "routing")["action"])
        self.assertEqual(self.snapshot(), before)
        calls = self.herdr_calls()
        self.assertIn("integration status", calls)  # 只读查询可以跑
        self.assertFalse([c for c in calls if c.startswith("integration install") or c == "--skill"])

        p = self.run_install("--dry-run", "--host", "both", *self.ws_args())
        for word in ["现状：", "动作：", "备份：", "dry-run"]:
            self.assertIn(word, p.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_uninstall_dry_run_writes_nothing(self) -> None:
        self.install_json("--host", "both", *self.ws_args())
        before = self.snapshot()
        out = self.install_json("--uninstall", "--dry-run")
        self.assertTrue([s for s in out["steps"] if s["changes"]])
        self.assertEqual(self.snapshot(), before)


# ---------------------------------------------------------------- cd+git 拦截 hook（~/.claude/settings.json 的 PreToolUse）


# 照本机 ~/.claude/settings.json 现在的形状造的 herdr hook（SessionStart），和一项用户自己的 PreToolUse。
HERDR_SESSION_START = [{"matcher": "^(startup|resume|clear|compact|fork)$", "hooks": [
    {"type": "command", "command": "bash '/Users/someone/.claude/hooks/herdr-agent-state.sh' session", "timeout": 10}]}]
USER_PRE_TOOL = {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "~/.claude/hooks/fmt.sh"}]}


def hook_stdin(command: str) -> bytes:
    return json.dumps({"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": "/tmp",
                       "tool_input": {"command": command}, "agent_id": "a1", "agent_type": "Explore"}).encode()


class CdGitHookTests(InstallTestBase):
    def user_settings(self) -> dict:
        return {"model": "opus", "hooks": {"SessionStart": HERDR_SESSION_START, "PreToolUse": [USER_PRE_TOOL]},
                "statusLine": {"type": "command", "command": "sh ~/.claude/statusline-command.sh"}}

    def ours(self, settings: dict) -> list:
        return [h for g in settings.get("hooks", {}).get("PreToolUse", []) for h in g.get("hooks", [])
                if "cd_git_guard.py" in h.get("command", "")]

    def run_hook_command(self, command: str) -> subprocess.CompletedProcess:
        """照 Claude Code 的做法用 shell 跑 settings 里写的 hook 命令，stdin 是真实格式的 PreToolUse JSON。"""
        cmd = self.load(self.settings)["hooks"]["PreToolUse"][-1]["hooks"][0]["command"]
        return subprocess.run(["sh", "-c", cmd], input=hook_stdin(command), env=self.env, cwd=str(self.tmp),
                              capture_output=True, timeout=30)

    def assert_hook_works(self) -> None:
        deny = self.run_hook_command("cd /tmp/x && git log")
        self.assertEqual(deny.returncode, 0, deny.stderr)
        spec = json.loads(deny.stdout.decode())["hookSpecificOutput"]
        self.assertEqual(spec["permissionDecision"], "deny")
        self.assertIn("git -C /tmp/x log", spec["permissionDecisionReason"])
        allow = self.run_hook_command("git -C /tmp/x log")
        self.assertEqual((allow.returncode, allow.stdout), (0, b""))

    def test_hook_first_install_adds_it(self) -> None:
        out = self.install_json("--host", "claude", *self.ws_args())
        step = self.step(out, "claude-hook")
        self.assertTrue(step["changes"])
        self.assertEqual(step["state"], "不存在")
        settings = self.load(self.settings)
        self.assertEqual(settings["hooks"], {"PreToolUse": [{"matcher": "Bash", "hooks": [self.hook_entry()]}],
                                             "SessionStart": [{"hooks": [self.session_entry()]}],
                                             "UserPromptSubmit": [{"hooks": [self.prompt_entry()]}]})
        self.assertNotIn(str(PKG), self.hook_command())  # 指向装好的包，不是 clone 目录
        self.assertEqual(self.load(self.state)["added_hooks"],
                         {str(self.settings): [self.hook_command(), self.session_command(), self.prompt_command()]})
        self.assert_hook_works()
        check = self.install_json("--check")
        self.assertEqual(check["verdicts"][[s["key"] for s in check["steps"]].index("claude-hook")], "ok")

    def test_hook_keeps_existing_hooks_and_keys(self) -> None:
        original = self.user_settings()
        self.write_json(self.settings, original)
        before_text = self.settings.read_text()
        self.install_json("--host", "claude", *self.ws_args())
        settings = self.load(self.settings)
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(settings["statusLine"], original["statusLine"])
        self.assertEqual(settings["hooks"]["SessionStart"],  # herdr 的 hook 原样保留，主会话登记 hook 追加在后面
                         HERDR_SESSION_START + [{"hooks": [self.session_entry()]}])
        self.assertEqual(settings["hooks"]["PreToolUse"],
                         [USER_PRE_TOOL, {"matcher": "Bash", "hooks": [self.hook_entry()]}])  # 别的项在前、不动
        # 权限规则和 hook 两项改的是同一个文件：只备份一份，内容是改之前的原文件
        bak = self.backups(self.claude, "settings.json")
        self.assertEqual(len(bak), 1)
        self.assertEqual(bak[0].read_text(), before_text)
        self.assert_hook_works()

    def test_hook_rerun_idempotent(self) -> None:
        self.write_json(self.settings, self.user_settings())
        self.install_json("--host", "claude", *self.ws_args())
        backups_before = self.all_backups()
        again = self.install_json("--host", "claude", *self.ws_args())
        self.assertEqual([s["key"] for s in again["steps"] if s["changes"]], [])
        self.assertIn("已有", self.step(again, "claude-hook")["state"])
        self.assertEqual(len(self.ours(self.load(self.settings))), 1)  # 不重复追加
        self.assertEqual(self.all_backups(), backups_before)

    def test_hook_stale_path_updated_in_place(self) -> None:
        """settings 里是 drover 的旧项（脚本路径变了）：就地更新那一项，同组里用户的 hook 不动，不重复追加。"""
        old_cmd = "python3 /old/clone/hooks/cd_git_guard.py"
        mine = {"type": "command", "command": "~/.claude/hooks/my-bash-guard.sh"}
        settings = self.user_settings()
        settings["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [mine, {"type": "command", "command": old_cmd}]})
        self.write_json(self.settings, settings)
        dry = self.install_json("--host", "claude", *self.ws_args(), "--dry-run")
        self.assertIn("旧项", self.step(dry, "claude-hook")["state"])
        self.assertIn("更新那一项", self.step(dry, "claude-hook")["action"])
        self.install_json("--host", "claude", *self.ws_args())
        pre = self.load(self.settings)["hooks"]["PreToolUse"]
        self.assertEqual(pre, [USER_PRE_TOOL, {"matcher": "Bash", "hooks": [mine, self.hook_entry()]}])
        self.assertEqual(self.load(self.state)["added_hooks"],
                         {str(self.settings): [self.hook_command(), self.session_command(), self.prompt_command()]})
        # check 也认旧项是"没装对"
        pre[1]["hooks"][1]["command"] = old_cmd
        self.write_json(self.settings, dict(self.load(self.settings), hooks={"PreToolUse": pre}))
        bad = self.install_json("--check", rc=1)
        self.assertEqual(self.step(bad, "claude-hook")["level"], "fail")

    def test_hook_dry_run_writes_nothing(self) -> None:
        self.write_json(self.settings, self.user_settings())
        before = self.snapshot()
        out = self.install_json("--host", "claude", *self.ws_args(), "--dry-run")
        step = self.step(out, "claude-hook")
        self.assertTrue(step["changes"])
        self.assertIn(self.hook_command(), step["action"])
        self.assertIn("matcher Bash", step["action"])
        self.assertEqual(self.snapshot(), before)
        text = self.run_install("--host", "claude", *self.ws_args(), "--dry-run").stdout
        self.assertIn("Claude Code 的 cd+git 拦截 hook  ~/.claude/settings.json", text)
        self.assertEqual(self.snapshot(), before)

    def test_hook_check_missing_then_ok(self) -> None:
        self.install_json("--host", "claude", *self.ws_args())
        self.assertTrue(self.install_json("--check")["ok"])
        settings = self.load(self.settings)
        del settings["hooks"]
        self.write_json(self.settings, settings)
        bad = self.install_json("--check", rc=1)
        failed = [s["key"] for s, v in zip(bad["steps"], bad["verdicts"]) if v == "fail"]
        self.assertEqual(failed, ["claude-hook", "claude-session-hook", "claude-prompt-hook"])
        self.assertIn("没装对：Claude Code 的 cd+git 拦截 hook", self.run_install("--check", rc=1).stdout)
        self.install_json("--host", "claude")  # 重跑补上
        self.assertTrue(self.install_json("--check")["ok"])

    def test_hook_uninstall_only_ours(self) -> None:
        original = self.user_settings()
        self.write_json(self.settings, original)
        self.install_json("--host", "claude", *self.ws_args())
        out = self.install_json("--uninstall")
        self.assertTrue(out["ok"])
        self.assertTrue(self.step(out, "claude-hook")["changes"])
        settings = self.load(self.settings)
        self.assertEqual(settings["hooks"], original["hooks"])  # herdr 的 SessionStart、用户的 PreToolUse 都在
        self.assertEqual((settings["model"], settings["statusLine"]), (original["model"], original["statusLine"]))

    def test_hook_uninstall_leaves_no_empty_structure(self) -> None:
        # 原来只有 herdr 的 SessionStart：撤完不留空的 PreToolUse
        self.write_json(self.settings, {"hooks": {"SessionStart": HERDR_SESSION_START}})
        self.install_json("--host", "claude", *self.ws_args())
        self.install_json("--uninstall")
        self.assertEqual(self.load(self.settings)["hooks"], {"SessionStart": HERDR_SESSION_START})
        # 原来没有 hooks：撤完也没有 hooks 键
        self.write_json(self.settings, {"model": "opus"})
        self.install_json("--host", "claude", *self.ws_args())
        self.install_json("--uninstall")
        self.assertNotIn("hooks", self.load(self.settings))
        # 原来没有 settings.json：install 新建的，撤完整个删掉
        self.settings.unlink()
        self.install_json("--host", "claude", *self.ws_args())
        self.install_json("--uninstall")
        self.assertFalse(self.settings.exists())

    def test_hook_uninstall_keeps_hand_added_copy(self) -> None:
        """安装记录里没有的不删：install 之前用户自己就加了一模一样的一项。"""
        settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [self.hook_entry()]}]}}
        self.write_json(self.settings, settings)
        out = self.install_json("--host", "claude", *self.ws_args())
        self.assertFalse(self.step(out, "claude-hook")["changes"])
        # 记录里只有这次真加的主会话登记 hook、读新规则 hook，没有用户自己加的那一项
        self.assertEqual(self.load(self.state)["added_hooks"],
                         {str(self.settings): [self.session_command(), self.prompt_command()]})
        self.install_json("--uninstall")
        self.assertEqual(self.load(self.settings)["hooks"], settings["hooks"])

    def test_hook_uninstall_codex_keeps_it(self) -> None:
        self.install_json("--host", "both", *self.ws_args())
        self.install_json("--uninstall", "--host", "codex")
        self.assertEqual(len(self.ours(self.load(self.settings))), 1)
        self.assertEqual(self.load(self.state)["added_hooks"],
                         {str(self.settings): [self.hook_command(), self.session_command(), self.prompt_command()]})
        self.install_json("--uninstall", "--host", "claude")
        self.assertFalse(self.settings.exists())

    def test_hook_copy_mode(self) -> None:
        self.write_json(self.settings, self.user_settings())
        out = self.install_json("--host", "claude", "--mode", "copy", *self.ws_args())
        self.assertTrue(out["ok"])
        script = self.pkg_dir / "hooks" / "cd_git_guard.py"
        self.assertFalse(self.pkg_dir.is_symlink())
        self.assertEqual(script.read_bytes(), (PKG / "hooks" / "cd_git_guard.py").read_bytes())  # 随包复制
        self.assertEqual(self.ours(self.load(self.settings)), [self.hook_entry()])
        self.assert_hook_works()
        self.assertTrue(self.install_json("--check")["ok"])
        # 副本里的脚本没了：--check 报没装对；hook 命令本身放行（不挡住所有 Bash）
        script.unlink()
        bad = self.install_json("--check", rc=1)
        self.assertIn("脚本", self.step(bad, "claude-hook")["state"])
        gone = self.run_hook_command("cd /tmp/x && git log")
        self.assertEqual((gone.returncode, gone.stdout), (0, b""))
        self.assertTrue(self.install_json("--uninstall")["ok"])
        self.assertEqual(self.load(self.settings)["hooks"], self.user_settings()["hooks"])

    def test_hook_link_to_copy_and_back(self) -> None:
        self.install_json("--host", "claude", *self.ws_args())
        self.install_json("--host", "claude", "--mode", "copy", *self.ws_args())
        self.install_json("--host", "claude", "--mode", "link", *self.ws_args())
        self.assertEqual(self.ours(self.load(self.settings)), [self.hook_entry()])  # 路径不变，不重复
        self.assert_hook_works()

    def test_hook_in_help(self) -> None:
        text = self.run_install("--help").stdout
        self.assertIn("hooks.PreToolUse", text)
        self.assertIn("cd_git_guard.py", text)
        self.assertIn("git -C", text)


# ---------------------------------------------------------------- 装好的 drover CLI 能跑


PKG_ROOT_PROBE = '''import importlib.machinery, importlib.util, json, sys
loader = importlib.machinery.SourceFileLoader("drover_probe", sys.argv[1])
spec = importlib.util.spec_from_loader("drover_probe", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
print(json.dumps({"pkg_root": str(mod.PKG_ROOT), "doc": str(mod.DISPATCH_DOC), "doc_exists": mod.DISPATCH_DOC.is_file(),
                  "workspace": str(mod.TRUSTED_ROOT), "repo_dirs": [str(d) for d in mod.REPO_DIRS]}))
'''


class DroverChainTests(InstallTestBase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.bin), *args], env=self.env, cwd=str(self.tmp), capture_output=True,
                              text=True, timeout=60)

    def probe(self) -> dict:
        probe = self.tmp / "probe.py"
        probe.write_text(PKG_ROOT_PROBE)
        p = subprocess.run([sys.executable, str(probe), str(self.bin)], env=self.env, cwd=str(self.tmp),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def run_chain(self, mode: str, expected_root: Path) -> None:
        """~/.drover/bin/drover 能跑，算出的 PKG_ROOT 是装好的那份包。"""
        self.install_json("--host", "claude", "--mode", mode, *self.ws_args())
        p = self.run_cli("--help")
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.run_cli("history", "--repo", "x")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(json.loads(p.stdout)["ok"])
        info = self.probe()
        self.assertEqual(Path(info["pkg_root"]), expected_root.resolve())
        self.assertTrue(info["doc_exists"], info)  # PKG_ROOT/flow/dispatch.md 存在

    def test_link_chain(self) -> None:
        self.run_chain("link", PKG)

    def test_copy_chain(self) -> None:
        self.run_chain("copy", self.pkg_dir)

    def test_cli_uses_new_names(self) -> None:
        """装好的 CLI 是改名后的：子命令 orch-start / orch-status，config 从 ~/.drover/config.json 读。"""
        self.install_json("--host", "claude", *self.ws_args())
        p = self.run_cli("--help")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("orch-start", p.stdout)
        self.assertIn("orch-status", p.stdout)
        info = self.probe()
        self.assertEqual(Path(info["workspace"]), self.ws)  # ~/.drover/config.json 生效
        self.assertEqual([Path(d) for d in info["repo_dirs"]], [self.ws])


# ---------------------------------------------------------------- 改名后的布局：只有 drover 这个名字


class NewLayoutTests(InstallTestBase):
    """新装只有新名字；用户自己写的 dispatch 命令 / 技能，install 和 --uninstall 都不碰、不备份。"""

    def assert_no_old_names(self, text: str, where: str) -> None:
        for old in OLD_NAMES:
            self.assertNotIn(old, text, f"{where} 里还有旧名字 {old}")

    def test_fresh_install_only_new_names(self) -> None:
        out = self.install_json("--host", "both", *self.ws_args())
        self.assertTrue(out["ok"])
        self.assert_link(self.pkg_dir, PKG)
        self.assert_link(self.bin, self.pkg_dir / "bin" / "drover")
        self.assert_link(self.command, self.pkg_dir / "flow" / "dispatch.md")
        self.assert_link(self.skill, self.pkg_dir / "codex" / "skills" / "drover")
        self.assertEqual(self.bin.resolve(), (PKG / "bin" / "drover").resolve())
        self.assertEqual((self.skill / "SKILL.md").resolve(), (PKG / "codex" / "skills" / "drover" / "SKILL.md").resolve())
        self.assertIn("name: drover", (self.skill / "SKILL.md").read_text())
        # ~/.herdr 下什么都不写，没有任何叫 dispatch 的入口
        self.assertFalse((self.home / ".herdr").exists())
        self.assertEqual([p for p in self.home_entries() if "dispatch" in p.name], [])
        self.assertEqual(sorted(os.listdir(self.state_home)), ["bin", "config.json", "drover-install.json", "pkg"])
        self.assertEqual(os.listdir(self.state_home / "bin"), ["drover"])
        for s in out["steps"]:
            self.assertNotIn("dispatch", s["path"], s)
        self.assertEqual(self.step(out, "pkg")["path"], "~/.drover/pkg")
        self.assertEqual(self.step(out, "bin")["path"], "~/.drover/bin/drover")
        self.assertEqual(self.step(out, "config")["path"], "~/.drover/config.json")
        self.assertEqual(self.step(out, "state")["path"], "~/.drover/drover-install.json")
        for path, rules in ((self.settings, GLOBAL_RULES), (self.ws_settings, WORKSPACE_RULES)):
            allow = self.load(path)["permissions"]["allow"]
            self.assertEqual(allow, rules)
            self.assert_no_old_names(json.dumps(allow), path.name)
        record = self.load(self.state)
        self.assert_no_old_names(json.dumps(record), "安装记录")

    def test_dry_run_and_help_show_only_new_layout(self) -> None:
        before = self.snapshot()
        text = self.run_install("--dry-run", "--host", "both", *self.ws_args()).stdout
        for new in ["~/.drover/pkg", "~/.drover/bin/drover", "~/.drover/config.json", "~/.claude/commands/drover.md",
                    "~/.codex/skills/drover", "Skill(drover)", "Bash(~/.drover/bin/drover:*)", "Read(~/.drover/**)"]:
            self.assertIn(new, text)
        self.assert_no_old_names(text, "--dry-run 输出")
        self.assertEqual(self.snapshot(), before)
        help_text = self.run_install("--help").stdout
        for new in ["~/.drover/pkg", "~/.drover/bin/drover", "/drover 命令", "~/.codex/skills/drover", "$drover",
                    "Skill(drover)", "~/.drover/backups/codex-skills/"]:
            self.assertIn(new, help_text)
        self.assert_no_old_names(help_text, "--help")
        self.assertNotIn("协调者", help_text)

    def user_owned_case(self, own: list) -> None:
        """own：用户自己的 dispatch 命令 / 技能（以及它们指向的东西）。装、查、卸都不碰它们，也不为它们备份。"""
        before = self.snapshot_of(*own)
        self.assertTrue(before)
        out = self.install_json("--host", "both", *self.ws_args())
        self.assertTrue(out["ok"])
        self.assertFalse([s for s in out["steps"] if "dispatch" in s["path"]])  # 计划里没有它们
        self.assertEqual(self.snapshot_of(*own), before)
        self.assertTrue(self.install_json("--check")["ok"])
        self.assertTrue(self.install_json("--uninstall")["ok"])
        self.assertEqual(self.snapshot_of(*own), before)
        self.assertFalse(self.command.exists() or self.command.is_symlink())
        self.assertFalse(self.skill.exists() or self.skill.is_symlink())
        self.assertEqual([b for b in self.all_backups() if "dispatch" in b.name], [])

    def test_user_dispatch_files_untouched(self) -> None:
        own_cmd = self.claude / "commands" / "dispatch.md"
        own_skill = self.codex / "skills" / "dispatch"
        self.write(own_cmd, "# 用户自己写的 dispatch 命令\n")
        self.write(own_skill / "SKILL.md", "---\nname: dispatch\n---\n用户自己写的技能\n")
        self.user_owned_case([own_cmd, own_skill])

    def test_user_dispatch_symlinks_untouched(self) -> None:
        tools = self.home / "my-tools"
        self.write(tools / "dispatch.md", "# 软链到别处的命令\n")
        self.write(tools / "dispatch-skill" / "SKILL.md", "---\nname: dispatch\n---\n软链到别处的技能\n")
        own_cmd = self.claude / "commands" / "dispatch.md"
        own_skill = self.codex / "skills" / "dispatch"
        own_cmd.parent.mkdir(parents=True)
        own_skill.parent.mkdir(parents=True)
        os.symlink(str(tools / "dispatch.md"), str(own_cmd))
        os.symlink(str(tools / "dispatch-skill"), str(own_skill))
        self.user_owned_case([own_cmd, own_skill, tools])

    def test_uninstall_cleans_new_layout(self) -> None:
        self.install_json("--host", "both", *self.ws_args())
        out = self.install_json("--uninstall")
        self.assertTrue(out["ok"])
        for gone in [self.pkg_dir, self.bin, self.bin.parent, self.state, self.command, self.skill,
                     self.claude_skill.parent, self.codex / "skills" / "herdr", self.claude_md, self.agents,
                     self.ws_settings, self.settings]:
            self.assertFalse(gone.exists() or gone.is_symlink(), f"{gone} 应该被撤掉")
        self.assertEqual(os.listdir(self.state_home), ["config.json"])  # 只留 config.json
        self.assertTrue((PKG / "bin" / "drover").is_file())  # clone 本身不动
        self.assertFalse((self.home / ".herdr").exists())

    def test_copy_new_layout(self) -> None:
        self.write(self.codex / "config.toml", 'sandbox_mode = "danger-full-access"\n')
        out = self.install_json("--host", "both", "--mode", "copy", *self.ws_args())
        self.assertTrue(out["ok"])
        self.assertFalse(self.pkg_dir.is_symlink())
        self.assertTrue(is_pkg(self.pkg_dir))
        self.assertFalse((self.pkg_dir / "bin" / "dispatch").exists())
        self.assertFalse((self.pkg_dir / "codex" / "skills" / "dispatch").exists())
        real = self.pkg_dir.resolve()
        self.assertEqual(self.bin.resolve(), real / "bin" / "drover")
        self.assertEqual(self.command.resolve(), real / "flow" / "dispatch.md")
        self.assertEqual(self.skill.resolve(), real / "codex" / "skills" / "drover")
        check = self.install_json("--check")
        self.assertEqual(set(check["verdicts"]), {"ok"}, json.dumps(check, ensure_ascii=False, indent=1))
        self.assertTrue(self.install_json("--uninstall")["ok"])
        self.assertFalse(self.pkg_dir.exists())
        self.assertEqual(os.listdir(self.state_home), ["config.json"])

    def test_check_all_ok_after_install(self) -> None:
        self.write(self.codex / "config.toml", 'sandbox_mode = "danger-full-access"\n')
        self.install_json("--host", "both", *self.ws_args())
        out = self.install_json("--check")
        self.assertTrue(out["ok"])
        self.assertEqual(set(out["verdicts"]), {"ok"}, json.dumps(out, ensure_ascii=False, indent=1))
        text = self.run_install("--check").stdout
        for line in ["[✓] 包目录  ~/.drover/pkg", "[✓] drover CLI 入口  ~/.drover/bin/drover",
                     "[✓] 本机配置  ~/.drover/config.json", "[✓] Claude Code 的 /drover 命令  ~/.claude/commands/drover.md",
                     "[✓] Codex 的 drover 技能  ~/.codex/skills/drover", "[✓] drover 链路  ~/.drover/bin/drover"]:
            self.assertIn(line, text)
        self.assertNotIn("[✗]", text)
        self.assertNotIn("[!]", text)
        self.assertIn("结果：安装完整。", text)
        self.assert_no_old_names(text, "--check 输出")


def is_pkg(p: Path) -> bool:
    return (p / "bin" / "drover").is_file() and (p / "flow" / "dispatch.md").is_file()


# ---------------------------------------------------------------- --help


# ---------------------------------------------------------------- 缺依赖：herdr 没装


FAKE_INSTALLER = '''#!/bin/sh
# 假的 herdr 安装器：照官方脚本的行为把 herdr 放进 ${HERDR_INSTALL_DIR:-$HOME/.local/bin}（软链到测试的假 herdr），不联网。
echo "fake-installer HERDR_INSTALL_DIR=${HERDR_INSTALL_DIR:-}" >> "{log}"
[ "{fail}" = 1 ] && { echo "download failed (fake)"; exit 3; }
dir="${HERDR_INSTALL_DIR:-$HOME/.local/bin}"
mkdir -p "$dir"
ln -sf "{herdr}" "$dir/herdr"
echo "installed herdr to $dir/herdr"
'''


class MissingHerdrTests(InstallTestBase):
    """PATH 里没有 herdr 的环境：PATH = 只有假 claude / 假 curl 的目录 + 去掉所有含 herdr 的目录后的真实 PATH。"""

    def setUp(self) -> None:
        super().setUp()
        self.nobin = self.tmp / "nobin"
        self.nobin.mkdir()
        self.curl_log = self.tmp / "curl.log"
        self.installer_log = self.tmp / "installer.log"
        for name, body in (("claude", "#!/bin/sh\nexit 0\n"), ("codex", "#!/bin/sh\nexit 0\n"),
                           ("curl", f"#!/bin/sh\necho \"curl $*\" >> '{self.curl_log}'\nexit 97\n")):
            (self.nobin / name).write_text(body)
            (self.nobin / name).chmod(0o755)
        os.symlink(self.fakebin / "node", self.nobin / "node")  # 假 node 放最前面，不用机器上的
        dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep)
                if d and not os.path.exists(os.path.join(d, "herdr"))]
        self.noherdr_env = dict(self.env, PATH=os.pathsep.join([str(self.nobin), *dirs]))
        self.assertIsNone(shutil.which("herdr", path=self.noherdr_env["PATH"]), "测试环境的 PATH 里不能有 herdr")
        self.local_herdr = self.home / ".local" / "bin" / "herdr"
        self.addCleanup(lambda: self.assertFalse(self.curl_log.exists(), "不能调用 curl（不联网）"))

    def env_with_installer(self, fail: bool = False, **extra: str) -> dict:
        script = self.tmp / "fake-herdr-installer.sh"
        script.write_text(FAKE_INSTALLER.replace("{log}", str(self.installer_log))
                          .replace("{herdr}", str(self.fakebin / "herdr")).replace("{fail}", "1" if fail else "0"))
        return dict(self.noherdr_env, **{INSTALLER_ENV: f"sh '{script}'"}, **extra)

    def official_env(self) -> dict:
        """不设替换命令：计划里显示的是官方命令。只在不会执行安装的场景（dry-run、非交互不带参数）用。"""
        env = dict(self.noherdr_env)
        env.pop(INSTALLER_ENV)
        return env

    def test_dry_run_lists_official_command(self) -> None:
        before = self.snapshot()
        out = self.install_json("--dry-run", "--install-deps", "--host", "claude", *self.ws_args(),
                                env=self.official_env())
        herdr = self.step(out, "herdr")
        self.assertEqual(out["steps"][0]["key"], "herdr")  # 依赖放在计划的第一项
        self.assertTrue(herdr["changes"])
        self.assertIn(f"安装 herdr：直接执行（--install-deps） {HERDR_OFFICIAL}", herdr["action"])
        for key in ("claude-herdr-skill", "claude-integration"):  # 后面要用 herdr 的项先排上，等装完再跑
            self.assertTrue(self.step(out, key)["changes"], key)
            self.assertIn("装好 herdr 后", self.step(out, key)["action"])
        self.assertEqual(self.snapshot(), before)  # dry-run 什么都不写，也不装
        self.assertFalse(self.installer_log.exists())
        self.assertFalse(self.local_herdr.exists())

    def test_non_interactive_without_flag_does_not_install(self) -> None:
        out = self.install_json("--host", "claude", *self.ws_args(), rc=1, env=self.official_env())
        self.assertFalse(out["ok"])
        self.assertEqual(out["deps_mode"], "no")
        herdr = self.step(out, "herdr")
        self.assertFalse(herdr["changes"])
        self.assertEqual(herdr["level"], "fail")
        self.assertIn(HERDR_OFFICIAL, herdr["action"])
        self.assertIn("--install-deps", herdr["action"])
        self.assertEqual(self.step(out, "claude-herdr-skill")["level"], "fail")
        self.assertEqual(self.step(out, "claude-integration")["level"], "fail")
        self.assertTrue(self.pkg_dir.is_symlink())  # 不需要 herdr 的项照常装好
        self.assertFalse(self.local_herdr.exists())
        p = self.run_install("--host", "claude", *self.ws_args(), rc=1, env=self.official_env())
        self.assertIn(f"缺 herdr：官方安装命令 {HERDR_OFFICIAL}", p.stdout)  # 文字输出的提示里有命令

    def test_install_deps_runs_installer_then_continues(self) -> None:
        out = self.install_json("--install-deps", "--host", "both", *self.ws_args(), env=self.env_with_installer())
        self.assertTrue(out["ok"], json.dumps(out, ensure_ascii=False, indent=1))
        self.assertEqual(self.installer_log.read_text().count("fake-installer"), 1)
        self.assertTrue(self.local_herdr.is_symlink())
        self.assertEqual(out["herdr_installed"], str(self.local_herdr))  # PATH 没刷新也找到了
        self.assertIn("装好了", self.step(out, "herdr")["result"])
        calls = self.herdr_calls()
        for call in ("--skill", "integration status", "integration install claude", "integration install codex"):
            self.assertIn(call, calls)
        self.assertEqual(self.claude_skill.read_text(), FAKE_SKILL)
        self.assert_link(self.codex / "skills" / "herdr", self.claude / "skills" / "herdr")
        joined = "\n".join(out["next_steps"])
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', joined)  # ~/.local/bin 不在 PATH 里
        self.assertIn("在 Herdr 里开主会话", joined)
        # 再跑一次：herdr 已经在官方安装目录里，不再装
        again = self.install_json("--host", "both", *self.ws_args(), env=self.env_with_installer())
        self.assertEqual(self.step(again, "herdr")["level"], "warn")  # 只提示 PATH
        self.assertEqual(self.installer_log.read_text().count("fake-installer"), 1)

    def test_herdr_install_dir_respected(self) -> None:
        custom = self.tmp / "custom-bin"
        env = self.env_with_installer(HERDR_INSTALL_DIR=str(custom))
        out = self.install_json("--install-deps", "--host", "claude", *self.ws_args(), env=env)
        self.assertTrue(out["ok"])
        self.assertEqual(out["herdr_installed"], str(custom / "herdr"))
        self.assertIn(f"HERDR_INSTALL_DIR={custom}", self.installer_log.read_text())
        self.assertIn("integration install claude", self.herdr_calls())

    def test_installer_failure_reported(self) -> None:
        out = self.install_json("--install-deps", "--host", "claude", *self.ws_args(), rc=1,
                                env=self.env_with_installer(fail=True))
        self.assertIn("退出码 3", self.step(out, "herdr")["result"])
        self.assertIn("download failed", self.step(out, "herdr")["result"])
        self.assertTrue(self.step(out, "claude-herdr-skill")["result"].startswith("失败"))
        self.assertTrue(self.pkg_dir.is_symlink())

    def test_herdr_off_path_is_used_and_reported(self) -> None:
        self.local_herdr.parent.mkdir(parents=True)
        os.symlink(str(self.fakebin / "herdr"), str(self.local_herdr))
        out = self.install_json("--host", "claude", *self.ws_args(), env=self.noherdr_env)
        self.assertTrue(out["ok"])
        herdr = self.step(out, "herdr")
        self.assertEqual(herdr["level"], "warn")
        self.assertIn("不在 PATH 里", herdr["state"])
        self.assertIn("--skill", self.herdr_calls())  # 后面的项用官方安装目录里的 herdr
        check = self.install_json("--check", rc=1, env=self.noherdr_env)  # drover CLI 按名字调 herdr：不在 PATH 算缺
        row = [d for d in check["deps"] if d["name"] == "herdr"][0]
        self.assertIsNone(row["path"])
        self.assertIn("export PATH=", row["hint"])

    def test_check_gives_install_hint(self) -> None:
        out = self.install_json("--check", "--host", "claude", rc=1, env=self.official_env())
        row = [d for d in out["deps"] if d["name"] == "herdr"][0]
        self.assertIn(HERDR_OFFICIAL, row["hint"])
        text = self.run_install("--check", "--host", "claude", rc=1, env=self.official_env()).stdout
        self.assertIn(f"装法：{HERDR_OFFICIAL}", text)

    def run_pty(self, args: list, answer: str, env: dict) -> tuple:
        """在伪终端里跑 install.py（stdin / stdout 都是终端，走"问一句"），看到 [Y/n] 就回答。"""
        rc, text, answered = self.pty_run([sys.executable, str(INSTALL), *args], env, [(b"[Y/n]", answer)])
        self.assertTrue(answered, f"没看到提问：{text[-500:]}")
        return rc, text

    def test_interactive_asks_first(self) -> None:
        env = self.env_with_installer()
        rc, text = self.run_pty(["--host", "claude", *self.ws_args()], "n", env)
        self.assertEqual(rc, 1)
        self.assertIn("这次要往 ~/.drover 以外装这几样", text)
        self.assertIn("  - herdr：sh '", text)  # 列出要跑的命令（这里是换掉的假安装器）
        self.assertIn("现在装吗？[Y/n]", text)
        self.assertIn("你选了不装", text)
        self.assertFalse(self.installer_log.exists())
        rc, text = self.run_pty(["--host", "claude", *self.ws_args()], "", env)  # 直接回车 = 同意
        self.assertEqual(rc, 0, text[-800:])
        self.assertEqual(self.installer_log.read_text().count("fake-installer"), 1)
        self.assertIn("integration install claude", self.herdr_calls())
        self.assertIn("在 Herdr 里开主会话", text)


# ---------------------------------------------------------------- 缺依赖：claude / codex 没装


class MissingAgentCliTests(InstallTestBase):
    def path_without(self, *names: str) -> str:
        dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep)
                if d and not any(os.path.exists(os.path.join(d, n)) for n in names)]
        return os.pathsep.join([str(self.fakebin), *dirs])

    def test_host_cli_missing_only_warns(self) -> None:
        env = dict(self.env, PATH=self.path_without("codex", "herdr"))  # fakebin 里有假 herdr、假 claude，没有 codex
        self.assertIsNone(shutil.which("codex", path=env["PATH"]))
        out = self.install_json("--host", "codex", *self.ws_args(), env=env)
        self.assertTrue(out["ok"])  # 入口照样装，只提示
        step = self.step(out, "codex-cli")
        self.assertEqual(step["level"], "warn")
        self.assertFalse(step["changes"])
        self.assertIn(CODEX_OFFICIAL, step["note"])
        self.assert_link(self.skill, self.pkg_dir / "codex" / "skills" / "drover")
        self.assertEqual(self.step(out, "herdr")["level"], "ok")

    def test_no_agents_detected_lists_official_commands(self) -> None:
        env = dict(self.env, PATH=str(self.tmp / "empty-bin"))
        p = self.run_install("--dry-run", rc=2, env=env)
        self.assertIn(CLAUDE_OFFICIAL, p.stderr)
        self.assertIn(CODEX_OFFICIAL, p.stderr)
        self.assertIn("--host claude|codex|both", p.stderr)  # 选了哪个，install 就把它一起装上
        self.assertFalse(self.state_home.exists())


# ---------------------------------------------------------------- 外部依赖一次装齐：herdr、选定宿主的命令行、Node（nvm）


# 假的 claude / codex / Node 安装器：照官方脚本的行为把可执行文件放进家目录（claude、codex 到 ~/.local/bin，codex 认
# CODEX_INSTALL_DIR；Node 到 $NVM_DIR/versions/node/v22.11.0/bin），不联网。调用和收到的环境变量记进 {log}。
FAKE_DEP_INSTALLER = '''#!/bin/sh
echo "fake-{name} CODEX_NON_INTERACTIVE=${CODEX_NON_INTERACTIVE:-} NVM_DIR=${NVM_DIR:-} XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-unset}" >> "{log}"
[ "{fail}" = 1 ] && { echo "download failed (fake {name})"; exit 4; }
case "{name}" in
  node) dir="$NVM_DIR/versions/node/v22.11.0/bin"; mkdir -p "$dir"; printf '#!/bin/sh\\necho v22.11.0\\n' > "$dir/node"; chmod +x "$dir/node" ;;
  codex) dir="${CODEX_INSTALL_DIR:-$HOME/.local/bin}"; mkdir -p "$dir"; printf '#!/bin/sh\\nexit 0\\n' > "$dir/codex"; chmod +x "$dir/codex" ;;
  *) dir="$HOME/.local/bin"; mkdir -p "$dir"; printf '#!/bin/sh\\nexit 0\\n' > "$dir/{name}"; chmod +x "$dir/{name}" ;;
esac
echo "installed {name} (fake)"
'''
NVM_OFFICIAL = "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | PROFILE=/dev/null bash"


class ExternalDepsTests(InstallTestBase):
    """全新机器：PATH 里没有 herdr、claude、codex、node（只有一个只记日志的假 curl），安装命令都换成假的安装器。"""

    def setUp(self) -> None:
        super().setUp()
        nobin = self.tmp / "nobin"
        nobin.mkdir()
        self.curl_log = self.tmp / "curl.log"
        (nobin / "curl").write_text(f"#!/bin/sh\necho \"curl $*\" >> '{self.curl_log}'\nexit 97\n")
        (nobin / "curl").chmod(0o755)
        dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep)
                if d and not any(os.path.exists(os.path.join(d, n)) for n in ("herdr", "claude", "codex", "node"))]
        self.bare_env = dict(self.env, PATH=os.pathsep.join([str(nobin), *dirs]), SHELL="/bin/zsh")
        for tool in ("herdr", "claude", "codex", "node"):
            self.assertIsNone(shutil.which(tool, path=self.bare_env["PATH"]), tool)
        self.dep_log = self.tmp / "deps-installer.log"
        self.local_bin = self.home / ".local" / "bin"
        self.nvm_node = self.home / ".nvm" / "versions" / "node" / "v22.11.0" / "bin" / "node"
        self.addCleanup(lambda: self.assertFalse(self.curl_log.exists(), "不能调用 curl（不联网）"))

    def env_with(self, fail: tuple = (), **extra: str) -> dict:
        """四样的安装命令都换成假安装器；fail 里列的那几样装失败。"""
        env = dict(self.bare_env, **extra)
        herdr = self.tmp / "fake-herdr-installer.sh"
        herdr.write_text(FAKE_INSTALLER.replace("{log}", str(self.dep_log)).replace("{herdr}", str(self.fakebin / "herdr"))
                         .replace("{fail}", "1" if "herdr" in fail else "0"))
        env[INSTALLER_ENV] = f"sh '{herdr}'"
        for name, var in DEP_INSTALLER_ENVS.items():
            script = self.tmp / f"fake-{name}-installer.sh"
            script.write_text(FAKE_DEP_INSTALLER.replace("{name}", name).replace("{log}", str(self.dep_log))
                              .replace("{fail}", "1" if name in fail else "0"))
            env[var] = f"sh '{script}'"
        return env

    def official_env(self) -> dict:
        """不换安装命令：计划里显示的是官方命令。只在不会执行安装的场景（dry-run、非交互不带 --install-deps）用。"""
        return {k: v for k, v in self.bare_env.items() if k not in (INSTALLER_ENV, *DEP_INSTALLER_ENVS.values())}

    def installed(self) -> list:
        """按顺序：哪几个假安装器跑过（herdr 的假安装器记的是 fake-installer）。"""
        lines = self.dep_log.read_text().splitlines() if self.dep_log.exists() else []
        return [line.split()[0].replace("fake-installer", "herdr").replace("fake-", "") for line in lines]

    def test_dry_run_lists_command_location_profile_sudo(self) -> None:
        """dry-run：每样外部依赖写清要跑的官方命令、装到哪、改不改 shell 配置、不用 sudo；什么都不装、不写"""
        before = self.snapshot()
        out = self.install_json("--dry-run", "--install-deps", "--host", "both", "--oncall", *self.ws_args(),
                                env=self.official_env())
        self.assertTrue(out["ok"], json.dumps(out, ensure_ascii=False, indent=1))
        self.assertEqual([s["key"] for s in out["steps"]][:3], ["herdr", "claude-cli", "codex-cli"])
        profile = "~/.zprofile" if platform.system() == "Darwin" else "~/.zshrc"
        expect = {"herdr": (HERDR_OFFICIAL, "装到 ~/.local/bin/herdr", "不改 shell 配置"),
                  "claude-cli": (CLAUDE_OFFICIAL, "装到 ~/.local/bin/claude", "不改 shell 配置"),
                  "codex-cli": (CODEX_OFFICIAL, "装到 ~/.local/bin/codex", f"会往 {profile} 末尾追加一段带标记的 PATH"),
                  "oncall-node": (NVM_OFFICIAL, "装到 ~/.nvm", "不改 shell 配置（PROFILE=/dev/null")}
        for key, parts in expect.items():
            step = self.step(out, key)
            self.assertTrue(step["changes"], key)
            self.assertIn("直接执行（--install-deps）", step["action"])
            for part in (*parts, "不用 sudo"):
                self.assertIn(part, step["action"], key)
        self.assertIn("nvm install 22", self.step(out, "oncall-node")["action"])
        keys = [s["key"] for s in out["steps"]]
        self.assertLess(keys.index("oncall-node"), keys.index("oncall-plugin"))  # Node 先装，插件等它
        self.assertIn("装好 herdr 后", self.step(out, "oncall-plugin")["action"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.dep_log.exists())

    def test_non_interactive_without_flag_only_gives_commands(self) -> None:
        """非交互、不带 --install-deps（agent 的 shell、drover update）：一样都不装，只给命令；herdr 和 Node 照旧算失败
        （退出码 1，和以前一样），宿主命令行只提示"""
        out = self.install_json("--host", "both", "--oncall", *self.ws_args(), rc=1, env=self.official_env())
        self.assertEqual(out["deps_mode"], "no")
        for key, cmd, level in (("herdr", HERDR_OFFICIAL, "fail"), ("claude-cli", CLAUDE_OFFICIAL, "warn"),
                                ("codex-cli", CODEX_OFFICIAL, "warn"), ("oncall-node", NVM_OFFICIAL, "fail")):
            step = self.step(out, key)
            self.assertEqual((step["changes"], step["level"]), (False, level), key)
            self.assertIn(cmd, step["action"], key)
            self.assertIn("--install-deps", step["action"], key)
        self.assertEqual(out["deps_installed"], {})
        self.assertFalse(self.local_bin.exists())
        self.assertFalse((self.home / ".nvm").exists())
        self.assertTrue(self.pkg_dir.is_symlink())  # drover 自己的各项照装

    def test_install_deps_installs_all_then_skips(self) -> None:
        """--install-deps（非交互）：按顺序装 herdr → claude → codex → Node，插件等 Node 装好再装；装过的记进安装记录。
        重跑：装过的都跳过（不在 PATH 里只提示）"""
        env = self.env_with(XDG_CONFIG_HOME=str(self.tmp / "xdg"))
        out = self.install_json("--install-deps", "--host", "both", "--oncall", *self.ws_args(), env=env)
        self.assertTrue(out["ok"], json.dumps(out, ensure_ascii=False, indent=1))
        self.assertEqual(self.installed(), ["herdr", "claude", "codex", "node"])
        log = self.dep_log.read_text()
        self.assertIn("fake-codex CODEX_NON_INTERACTIVE=1", log)  # 不在终端里问要不要马上启动 Codex
        self.assertIn(f"fake-node CODEX_NON_INTERACTIVE= NVM_DIR={self.home / '.nvm'} XDG_CONFIG_HOME=unset", log)
        self.assertIn("Node 22.11.0", self.step(out, "oncall-node")["result"])
        self.assertTrue(self.step(out, "oncall-plugin")["result"].startswith("完成"))
        self.assertEqual(self.plugin_calls(), [ONCALL_INSTALL])
        self.assertIn("登录要你自己来", self.step(out, "codex-cli")["result"])
        want = {"herdr": str(self.local_bin / "herdr"), "claude": str(self.local_bin / "claude"),
                "codex": str(self.local_bin / "codex"), "nvm": str(self.nvm_node)}
        self.assertEqual(out["deps_installed"], want)
        self.assertEqual(self.load(self.state)["deps_installed"], want)
        joined = "\n".join(out["next_steps"])
        self.assertIn("终端里运行 ~/.local/bin/herdr", joined)  # herdr 不在 PATH 上：给完整路径
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', joined)
        self.assertIn("先 codex login", joined)
        self.assertIn("ctrl+b 再按 q", joined)
        again = self.install_json("--install-deps", "--host", "both", "--oncall", env=env)
        self.assertTrue(again["ok"])
        self.assertEqual(len(self.installed()), 4)  # 一样都没重装
        for key in ("herdr", "claude-cli", "codex-cli"):
            self.assertEqual((self.step(again, key)["changes"], self.step(again, key)["level"]), (False, "warn"), key)
            self.assertIn("不在 PATH 里", self.step(again, key)["state"])
        self.assertEqual(self.step(again, "oncall-node")["level"], "ok")
        self.assertEqual(self.load(self.state)["deps_installed"], want)

    def run_pty(self, answer: str) -> tuple:
        env = self.env_with()
        rc, text, answered = self.pty_run([sys.executable, str(INSTALL), "--host", "both", *self.ws_args()], env,
                                          [(b"[Y/n]", answer)])
        self.assertTrue(answered, f"没看到提问：{text[-500:]}")
        self.assertEqual(text.count("[Y/n]"), 1, text[-2000:])  # 一句问完
        for line in ("  - herdr：sh '", "  - Claude Code：sh '", "  - Codex CLI：sh '"):
            self.assertIn(line, text)
        return rc, text

    def test_interactive_one_question_no(self) -> None:
        """交互终端里答 n：一样都不装，只给命令；herdr 算失败（退出码 1），宿主命令行只提示"""
        rc, text = self.run_pty("n")
        self.assertEqual(rc, 1, text[-1500:])
        self.assertEqual(self.installed(), [])
        self.assertIn("你选了不装", text)
        self.assertIn("警告：没做成", text)  # claude / codex：只记警告
        self.assertFalse(self.local_bin.exists())

    def test_interactive_one_question_yes(self) -> None:
        """交互终端里直接回车：三样一起装上，只问这一次"""
        rc, text = self.run_pty("")
        self.assertEqual(rc, 0, text[-1500:])
        self.assertEqual(self.installed(), ["herdr", "claude", "codex"])
        self.assertIn("在 Herdr 里开主会话", text)

    def test_only_selected_host_cli(self) -> None:
        """--host claude：claude、codex 都没装，只装 claude"""
        out = self.install_json("--install-deps", "--host", "claude", *self.ws_args(), env=self.env_with())
        self.assertTrue(out["ok"])
        self.assertEqual(self.installed(), ["herdr", "claude"])
        self.assertNotIn("codex-cli", [s["key"] for s in out["steps"]])
        self.assertFalse((self.local_bin / "codex").exists())

    def test_node_only_with_oncall_and_no_node(self) -> None:
        """Node 只在接飞书、本机一个 node 都没有时装：不接飞书不装；本机有旧 node 不装（只提示升级，退出码 1）"""
        env = self.env_with()
        out = self.install_json("--install-deps", "--host", "claude", *self.ws_args(), env=env)
        self.assertNotIn("node", self.installed())
        self.assertIn("不检查", self.step(out, "oncall-node")["state"])
        old = self.tmp / "old-node"
        old.mkdir()
        (old / "node").write_text("#!/bin/sh\necho v16.20.0\n")
        (old / "node").chmod(0o755)
        env_old = dict(env, PATH=os.pathsep.join([str(old), env["PATH"]]))
        out = self.install_json("--install-deps", "--host", "claude", "--oncall", rc=1, env=env_old)
        step = self.step(out, "oncall-node")
        self.assertEqual((step["changes"], step["level"]), (False, "fail"))
        self.assertIn("Node 16.20.0 太旧", step["state"])
        self.assertNotIn("node", self.installed())
        out = self.install_json("--install-deps", "--host", "claude", "--oncall", env=env)  # 一个 node 都没有：装
        self.assertTrue(out["ok"], json.dumps(out, ensure_ascii=False, indent=1))
        self.assertEqual(self.installed().count("node"), 1)

    def test_failures(self) -> None:
        """herdr 装失败：这一项失败、退出码 1，其余照装（claude 照样装上）；宿主命令行装失败只记警告，退出码 0"""
        out = self.install_json("--install-deps", "--host", "claude", *self.ws_args(), rc=1, env=self.env_with(fail=("herdr",)))
        self.assertTrue(self.step(out, "herdr")["result"].startswith("失败"))
        self.assertIn("download failed", self.step(out, "herdr")["result"])
        self.assertTrue(self.step(out, "claude-cli")["result"].startswith("完成"))
        self.assertTrue(self.step(out, "routing")["result"].startswith("完成"))
        self.dep_log.unlink()
        shutil.rmtree(self.local_bin)
        out = self.install_json("--install-deps", "--host", "claude", rc=0, env=self.env_with(fail=("claude",)))
        step = self.step(out, "claude-cli")
        self.assertTrue(step["result"].startswith("警告"), step)
        self.assertIn("download failed (fake claude)", step["note"])
        self.assertIn("不用重跑 install", step["note"])
        self.assertTrue(self.step(out, "herdr")["result"].startswith("完成"))
        self.assertNotIn("claude", out["deps_installed"])

    def test_uninstall_keeps_deps_and_lists_how_to_remove(self) -> None:
        """--uninstall 不删 drover 装过的 herdr、claude、codex、nvm，只列出来和手动删法"""
        self.install_json("--install-deps", "--host", "both", "--oncall", *self.ws_args(), env=self.env_with())
        text = self.run_install("--uninstall", "--dry-run", env=self.bare_env).stdout
        self.assertIn("drover 替你装过、没删的工具", text)
        self.assertIn("herdr（~/.local/bin/herdr）：先 herdr server stop", text)
        out = self.install_json("--uninstall", env=self.bare_env)
        self.assertTrue(out["ok"])
        kept = {d["name"]: d["remove"] for d in out["kept_deps"]}
        self.assertEqual(sorted(kept), ["claude", "codex", "herdr", "nvm"])
        self.assertIn("rm -rf ~/.local/share/claude", kept["claude"])
        self.assertIn("# >>> Codex installer >>>", kept["codex"])
        self.assertIn("rm -rf ~/.nvm", kept["nvm"])
        for path in (self.local_bin / "herdr", self.local_bin / "claude", self.local_bin / "codex", self.nvm_node):
            self.assertTrue(os.path.lexists(path), path)
        self.assertFalse(self.state.exists())


# ---------------------------------------------------------------- 首次安装：主工作台没有默认值，不能是家目录


FIRST_INSTALL_HINT = "首次安装要用 --workspace 指定主工作台（你平时放各个仓库、开主会话的目录，不能是家目录）"
WORKSPACE_QUESTION = "主工作台目录（你平时放各个仓库、开主会话的目录，不能是家目录）："
HOME_REASONS = ("Claude Code 会从当前目录往上找 CLAUDE.md", "Codex 会加载 AGENTS.md")


class FirstInstallWorkspaceTests(InstallTestBase):
    """没有 config.json（或 config 里没有 workspace）、又没传 --workspace：交互终端里问（没有默认值），非交互退出码 2、
    不给建议值。主工作台是家目录或它的上级目录时拒绝（--check 只提示，--uninstall 照常）。"""

    def assert_no_suggestion(self, text: str) -> None:
        self.assertIn("--workspace <目录>", text)
        self.assertNotIn("--workspace ~", text)
        self.assertNotIn(str(PKG.parent), text)  # 不拿 clone 的上一级当建议值

    def assert_nothing_written(self, before: dict) -> None:
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.state_home.exists())
        self.assertEqual(self.herdr_calls(), [])  # 在计划之前就停下

    def install_pty(self, answers: list) -> tuple:
        return self.pty_run([sys.executable, str(INSTALL), "--host", "claude"], self.env, answers)

    def test_non_interactive_exits_2_without_suggestion(self) -> None:
        before = self.snapshot()
        p = self.run_install("--host", "claude", rc=2)
        self.assertIn(FIRST_INSTALL_HINT, p.stderr)
        self.assertIn("--repo-dir（按名字找仓库的目录）默认就是 workspace", p.stderr)
        self.assert_no_suggestion(p.stderr)
        self.assertEqual(p.stdout, "")
        self.assert_nothing_written(before)
        self.assertFalse((self.home / "Git-Repo").exists())

    def test_json_exits_2_without_suggestion(self) -> None:
        before = self.snapshot()
        out = self.install_json("--host", "claude", rc=2)
        self.assertFalse(out["ok"])
        self.assertIn(FIRST_INSTALL_HINT, out["error"])
        self.assertEqual(out["missing"], "workspace")
        self.assertNotIn("suggested_workspace", out)
        self.assert_no_suggestion(out["error"])
        self.assert_nothing_written(before)

    def test_dry_run_exits_2_too(self) -> None:
        before = self.snapshot()
        p = self.run_install("--dry-run", "--host", "both", rc=2)
        self.assertIn(FIRST_INSTALL_HINT, p.stderr)
        self.assert_no_suggestion(p.stderr)
        out = self.install_json("--dry-run", "--host", "both", rc=2)
        self.assertIn(FIRST_INSTALL_HINT, out["error"])
        self.assert_nothing_written(before)

    def test_interactive_empty_input_asks_again(self) -> None:
        q = WORKSPACE_QUESTION.encode()
        rc, text, answered = self.install_pty([(q, ""), (q, "~/elsewhere")])
        self.assertEqual((rc, answered), (0, 2), text[-800:])
        self.assertNotIn("不能是家目录）[", text)  # 没有默认值
        self.assertIn("要填一个目录", text)
        self.assertEqual(self.load(self.config), {"workspace": "~/elsewhere", "repo_dirs": ["~/elsewhere"]})
        self.assertIn(BEGIN, (self.home / "elsewhere" / "AGENTS.md").read_text())  # ~ 按 HOME 展开，目录一起新建
        self.assertEqual((self.home / "elsewhere" / "CLAUDE.md").read_text(), "@AGENTS.md\n")

    def test_interactive_home_rejected_then_asks_again(self) -> None:
        q = WORKSPACE_QUESTION.encode()
        rc, text, answered = self.install_pty([(q, "~"), (q, str(self.ws))])
        self.assertEqual((rc, answered), (0, 2), text[-800:])
        self.assertIn("主工作台不能是家目录", text)
        for reason in HOME_REASONS:
            self.assertIn(reason, text)
        self.assertEqual(self.load(self.config)["workspace"], "~/ws")
        self.assert_block(self.agents.read_text())
        for path in (self.home / "AGENTS.md", self.home / "CLAUDE.md", self.claude / "settings.local.json"):
            self.assertFalse(path.exists(), path)

    def test_interactive_eof_and_ctrl_c(self) -> None:
        q = WORKSPACE_QUESTION.encode()
        before = self.snapshot()
        rc, text, answered = self.install_pty([(q, b"\x04")])  # Ctrl-D：按缺参数退出
        self.assertEqual((rc, answered), (2, 1), text[-800:])
        self.assertIn(FIRST_INSTALL_HINT, text)
        rc, text, answered = self.install_pty([(q, signal.SIGINT)])  # Ctrl-C
        self.assertEqual((rc, answered), (130, 1), text[-800:])
        self.assertIn("取消了：没有填主工作台，什么都没装", text)
        self.assertNotIn("Traceback", text)
        self.assert_nothing_written(before)

    def test_workspace_home_or_above_rejected(self) -> None:
        """--workspace 是家目录或它的上级（~、$HOME、$HOME/、相对路径、软链、HOME 的上级、/）：安装、--dry-run、--json
        都退出码 2，原因里提到 CLAUDE.md / AGENTS.md，什么都不写"""
        link = self.tmp / "home-link"
        os.symlink(str(self.home), str(link))
        before = self.snapshot()
        home_cases = ["~", str(self.home), str(self.home) + "/", "home", str(link)]  # "home" 按当前目录 self.tmp 解析
        for ws in home_cases + [str(self.home.parent), "/"]:
            for extra in ([], ["--dry-run"]):
                p = self.run_install("--host", "claude", "--workspace", ws, *extra, rc=2)
                self.assertIn(f"--workspace {ws}：主工作台不能是", p.stderr)
                for reason in HOME_REASONS:
                    self.assertIn(reason, p.stderr)
                if ws in home_cases:
                    self.assertIn("~/.claude/settings.local.json", p.stderr)
                else:
                    self.assertIn("家目录的上级目录", p.stderr)
            out = self.install_json("--host", "claude", "--workspace", ws, rc=2)
            self.assertEqual((out["invalid"], out["rejected_workspace"]), ("workspace", ws))
        self.assert_nothing_written(before)

    def test_existing_config_home_rejected_check_warns(self) -> None:
        """已有 config 的 workspace 是家目录（以前这样装过）：安装、--dry-run 拒绝；--check 只提示（!），退出码不变；
        --uninstall 照常撤掉家目录里的区块"""
        self.install_json("--host", "claude", *self.ws_args())
        # 模拟以前把家目录当主工作台装过：主工作台的三份文件放进家目录，config 改成 ~
        shutil.copy(str(self.agents), str(self.home / "AGENTS.md"))
        shutil.copy(str(self.claude_md), str(self.home / "CLAUDE.md"))
        shutil.copy(str(self.ws_settings), str(self.claude / "settings.local.json"))
        self.write_json(self.config, {"workspace": "~", "repo_dirs": ["~"]})
        before = self.snapshot()
        for extra in ([], ["--dry-run"]):
            p = self.run_install("--host", "claude", *extra, rc=2)
            self.assertIn("config.json 里的 workspace 是 ~：主工作台不能是家目录（~）", p.stderr)
            self.assertIn("重跑时加上 --workspace <目录>", p.stderr)
        self.assertEqual(self.snapshot(), before)
        out = self.install_json("--check")
        self.assertTrue(out["ok"], json.dumps(out, ensure_ascii=False, indent=1))
        verdicts = dict(zip([s["key"] for s in out["steps"]], out["verdicts"]))
        self.assertEqual(verdicts["workspace"], "warn")
        self.assertNotIn("fail", verdicts.values())
        self.assertIn("CLAUDE.md", self.step(out, "workspace")["note"])
        text = self.run_install("--check").stdout  # 退出码 0
        self.assertIn("[!] 主工作台位置  ~", text)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.install_json("--uninstall")["ok"])
        self.assertNotIn(BEGIN, (self.home / "AGENTS.md").read_text())

    def test_existing_config_not_asked(self) -> None:
        self.write_json(self.config, {"workspace": "~/ws", "repo_dirs": ["~/ws"]})
        rc, text, _ = self.install_pty([])
        self.assertEqual(rc, 0, text[-800:])
        self.assertNotIn(WORKSPACE_QUESTION, text)  # 交互终端里也不问，照旧沿用
        self.assertEqual(self.load(self.config), {"workspace": "~/ws", "repo_dirs": ["~/ws"]})
        self.assert_block(self.agents.read_text())

    def test_config_without_workspace_counts_as_first_install(self) -> None:
        self.write_json(self.config, {"repo_dirs": ["~/ws"], "extra": 1})
        before = self.config.read_text()
        out = self.install_json("--host", "claude", rc=2)
        self.assertIn(FIRST_INSTALL_HINT, out["error"])
        self.assertIn("在，但里面没有可用的 workspace（没有这个键）", out["error"])
        self.assertEqual(self.config.read_text(), before)
        self.install_json("--host", "claude", *self.ws_args())  # 传了 --workspace：只补这个键
        self.assertEqual(self.load(self.config), {"repo_dirs": ["~/ws"], "extra": 1, "workspace": "~/ws"})

    def test_check_and_uninstall_without_config(self) -> None:
        """没有 config.json：--check、--uninstall 不问、不按缺参数退出，也不再去碰 ~/Git-Repo"""
        old = self.home / "Git-Repo" / "AGENTS.md"
        self.write(old, "# 个人\n\n" + BEGIN + "\n旧区块\n" + END + "\n")
        before = self.snapshot()
        out = self.install_json("--check", "--host", "claude", rc=1)
        self.assertNotIn("error", out)
        self.assertEqual((out["workspace"], out["repo_dirs"]), (None, []))
        for key in ("workspace-settings", "claude-md", "routing"):
            self.assertEqual(self.step(out, key)["level"], "fail")
            self.assertIn("没有配置主工作台", self.step(out, key)["state"])
        self.assertIn("--workspace", self.step(out, "config")["action"])
        text = self.run_install("--check", "--host", "claude", rc=1).stdout
        self.assertIn("主工作台：没配置", text)
        self.assertNotIn(WORKSPACE_QUESTION, text)
        self.assertTrue(self.install_json("--uninstall", "--dry-run")["ok"])
        out = self.install_json("--uninstall")
        self.assertTrue(out["ok"])
        self.assertFalse([s for s in out["steps"] if "Git-Repo" in s["path"]])
        self.assertEqual(self.snapshot(), before)  # ~/Git-Repo/AGENTS.md 里的区块没被撤


# ---------------------------------------------------------------- install.sh


class InstallShTests(InstallTestBase):
    def minimal_bin(self, uname: str = None, link: tuple = ()) -> Path:
        """只放 install.sh 自己要用的 uname、dirname（可以换成假 uname），外加 link 里列的真实命令。"""
        d = self.tmp / "minbin"
        d.mkdir(exist_ok=True)
        for name in ("dirname", *link):
            os.symlink(shutil.which(name), str(d / name))
        if uname:
            (d / "uname").write_text(f"#!/bin/sh\necho {uname}\n")
            (d / "uname").chmod(0o755)
        else:
            os.symlink(shutil.which("uname"), str(d / "uname"))
        return d

    def run_sh(self, *args: str, path: str) -> subprocess.CompletedProcess:
        return subprocess.run(["/bin/sh", str(INSTALL_SH), *args], env=dict(self.env, PATH=path), cwd=str(self.tmp),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)

    def test_syntax(self) -> None:
        p = subprocess.run(["sh", "-n", str(INSTALL_SH)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.access(INSTALL_SH, os.X_OK), "install.sh 要有执行权限")

    def test_missing_git_and_python(self) -> None:
        p = self.run_sh("--dry-run", path=str(self.minimal_bin()))
        self.assertEqual(p.returncode, 1)
        self.assertIn("缺 git", p.stderr)
        self.assertIn("缺 python3", p.stderr)
        if sys.platform == "darwin":
            self.assertIn("xcode-select --install", p.stderr)
        else:
            self.assertIn("sudo apt-get install git", p.stderr)
        self.assertIn("再重跑 ./install.sh", p.stderr)
        self.assertFalse(self.state_home.exists())  # 没进 install.py

    def test_linux_hints(self) -> None:
        p = self.run_sh(path=str(self.minimal_bin(uname="Linux")))
        self.assertEqual(p.returncode, 1)
        self.assertIn("Linux 上能装，但这一版没有实测过", p.stderr)
        self.assertIn("sudo apt-get install git", p.stderr)
        self.assertIn("sudo dnf install python3", p.stderr)
        self.assertNotIn("xcode-select", p.stderr)

    def test_unsupported_os(self) -> None:
        p = self.run_sh(path=str(self.minimal_bin(uname="MINGW64_NT-10.0")))
        self.assertEqual(p.returncode, 1)
        self.assertIn("只支持 macOS（Linux 能装，但未实测），当前系统是 MINGW64_NT-10.0。", p.stderr)
        self.assertIn("Windows 暂不支持", p.stderr)
        self.assertIn("WSL2 的 Ubuntu", p.stderr)

    def test_python_too_old(self) -> None:
        d = self.minimal_bin(link=("git",))
        (d / "python3").write_text("#!/bin/sh\n[ \"$1\" = --version ] && { echo 'Python 3.8.10'; exit 0; }\nexit 1\n")
        (d / "python3").chmod(0o755)
        p = self.run_sh(path=str(d))
        self.assertEqual(p.returncode, 1)
        self.assertIn("版本太旧：Python 3.8.10", p.stderr)
        self.assertNotIn("缺 git", p.stderr)

    def test_passes_args_through(self) -> None:
        p = self.run_sh("--help", path=self.env["PATH"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("usage: python3 install.py", p.stdout)
        p = self.run_sh("--dry-run", "--host", "claude", "--workspace", str(self.ws), "--json", path=self.env["PATH"])
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual((out["command"], out["hosts"], out["workspace"]), ("dry-run", ["claude"], "~/ws"))
        self.assertFalse(self.state_home.exists())


class HelpTests(InstallTestBase):
    def test_help_matches_contract(self) -> None:
        out = self.run_install("--help").stdout
        self.assertIn("[--host claude|codex|both] [--mode link|copy] [--workspace DIR] [--repo-dir DIR]... "
                      "[--profile NAME|DIR] [--oncall|--no-oncall] [--install-deps] [--dry-run] [--check] "
                      "[--uninstall]", out)
        for flag in ["--host", "--mode", "--workspace", "--repo-dir", "--profile", "--install-deps", "--dry-run",
                     "--check", "--uninstall", "--oncall", "--no-oncall"]:
            self.assertIn(flag, out)
        self.assertIn("自动判断", out)
        self.assertIn(".bak-drover-", out)
        self.assertIn("--profile none", out)
        self.assertIn("playbook_dirs", out)

    def test_no_host_detected(self) -> None:
        env = dict(self.env, PATH=str(self.fakebin.parent / "empty-bin"))  # 没有 claude / codex，也没有两个目录
        p = subprocess.run([sys.executable, str(INSTALL), "--dry-run"], env=env, cwd=str(self.tmp),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 2)
        self.assertIn("--host", p.stderr)


# ---------------------------------------------------------------- 飞书 oncall（内置的 herdr-oncall 插件）


ENV_SECRET = "FEISHU_APP_SECRET=envfile-secret-91c2"
STATUS_OFF_RUN_NODE = """#!/bin/bash
# 假的 run-node.sh：插件自己的开关关着（status 脚本报 enabled: false）
echo '{"ok": true, "version": "0.7.0", "enabled": false}'
"""


class OncallPluginTests(InstallTestBase):
    def write_user_env(self) -> bytes:
        """插件配置目录里用户自己的 .env（飞书配对、密钥）：drover 不能碰它。"""
        self.write(self.oncall_dir / ".env", f"CHANNEL=feishu\n{ENV_SECRET}\n")
        return (self.oncall_dir / ".env").read_bytes()

    def env_without_node(self) -> dict:
        """PATH 里没有任何 node（假的删掉，真实 PATH 里有 node 的目录也去掉）。"""
        (self.fakebin / "node").unlink()
        dirs = [d for d in self.env["PATH"].split(os.pathsep) if d and not os.path.exists(os.path.join(d, "node"))]
        env = dict(self.env, PATH=os.pathsep.join(dirs))
        self.assertIsNone(shutil.which("node", path=env["PATH"]))
        return env

    def test_not_installed_installs_from_github(self) -> None:
        """--oncall、没装：执行 herdr plugin install fulanto/herdr-oncall --ref stable --yes，写 drover.env，安装记录记
        "是 drover 装的"，config.json 记 oncall: true"""
        out = self.install_json("--host", "claude", *self.ws_args(), "--oncall")
        self.assertTrue(out["oncall"])
        self.assertIs(self.load(self.config)["oncall"], True)
        step = self.step(out, "oncall-plugin")
        self.assertIn("herdr " + ONCALL_INSTALL, step["action"])
        self.assertTrue(step["result"].startswith("完成"))
        self.assertEqual(self.plugin_calls(), [ONCALL_INSTALL])
        self.assertEqual(self.drover_env.read_text(), DROVER_ENV)
        self.assertEqual(self.load(self.state)["oncall"], {"installed_by_drover": True, "env": str(self.drover_env)})
        self.assertEqual(self.step(out, "oncall-node")["state"], "Node 22.11.0")
        check = self.install_json("--check")
        verdicts = dict(zip([s["key"] for s in check["steps"]], check["verdicts"]))
        self.assertEqual([verdicts[k] for k in ("oncall-node", "oncall-plugin", "oncall-env")], ["ok", "ok", "ok"])
        # 再跑一次：插件已经够版本，drover.env 也对，什么都不做（不会重装、不会重复写）
        again = self.install_json("--host", "claude")
        self.assertEqual([s["key"] for s in again["steps"] if s["changes"]], [])
        self.assertEqual(self.plugin_calls(), [ONCALL_INSTALL])
        self.assertTrue(self.load(self.state)["oncall"]["installed_by_drover"])  # 重跑不会把"drover 装的"冲掉

    def test_github_old_version_updates(self) -> None:
        """GitHub 装的、低于 0.7.0：执行 herdr plugin update，不重装；不算 drover 装的"""
        self.set_plugin(version="0.6.6", source="github")
        dry = self.install_json("--host", "claude", *self.ws_args(), "--dry-run")
        self.assertIn("herdr " + ONCALL_UPDATE, self.step(dry, "oncall-plugin")["action"])
        self.assertEqual(self.plugin_calls(), [])
        out = self.install_json("--host", "claude", *self.ws_args())
        step = self.step(out, "oncall-plugin")
        self.assertEqual(step["level"], "warn")
        self.assertIn("低于 0.7.0", step["state"])
        self.assertEqual(self.plugin_calls(), [ONCALL_UPDATE])
        self.assertEqual(self.herdr_state()["plugins"][0]["version"], "0.7.0")
        self.assertEqual(self.drover_env.read_text(), DROVER_ENV)
        self.assertFalse(self.load(self.state)["oncall"]["installed_by_drover"])

    def test_github_current_only_writes_env(self) -> None:
        """GitHub 装的、版本够：只写 drover.env（整份），同目录的 .env 一个字节都不动"""
        self.set_plugin(version="0.7.0", source="github")
        user_env = self.write_user_env()
        mtime = (self.oncall_dir / ".env").stat().st_mtime_ns
        self.write(self.drover_env, "MAIN_AGENT_NAME=someone-else\n")  # 旧的 / 被手改过的 drover.env：整份覆盖
        out = self.install_json("--host", "claude", *self.ws_args())
        self.assertEqual(self.plugin_calls(), [])
        self.assertEqual(self.step(out, "oncall-plugin")["level"], "ok")
        env_step = self.step(out, "oncall-env")
        self.assertEqual((env_step["level"], env_step["path"]), ("warn", disp_home(self.home, self.drover_env)))
        self.assertEqual(self.drover_env.read_text(), DROVER_ENV)
        self.assertEqual((self.oncall_dir / ".env").read_bytes(), user_env)
        self.assertEqual((self.oncall_dir / ".env").stat().st_mtime_ns, mtime)
        self.assertNotIn("envfile-secret", json.dumps(out, ensure_ascii=False))
        self.assertEqual(sorted(p.name for p in self.oncall_dir.iterdir()), [".env", "drover.env"])  # 没留临时文件

    def test_local_link_left_alone(self) -> None:
        """本地链接（开发机）：不动；版本低时提示去链接的目录 git pull；drover.env 照写"""
        self.set_plugin(version="0.6.6", source="local")
        out = self.install_json("--host", "claude", *self.ws_args())
        step = self.step(out, "oncall-plugin")
        self.assertEqual((step["level"], step["changes"]), ("warn", False))
        self.assertIn("git pull", step["note"])
        self.assertIn(str(self.tmp / "plugin-root"), step["note"])
        self.assertEqual(self.plugin_calls(), [])
        self.assertEqual(self.drover_env.read_text(), DROVER_ENV)
        self.set_plugin(version="0.7.0", source="local")
        self.assertEqual(self.step(self.install_json("--check"), "oncall-plugin")["level"], "ok")

    def test_disabled_only_hints(self) -> None:
        """在 herdr 里被禁用：不替用户打开，也不更新，只提示"""
        self.set_plugin(version="0.6.6", source="github", enabled=False)
        out = self.install_json("--host", "claude", *self.ws_args())
        step = self.step(out, "oncall-plugin")
        self.assertEqual((step["level"], step["changes"]), ("warn", False))
        self.assertIn(f"herdr plugin enable {PLUGIN_ID}", step["note"])
        self.assertEqual(self.plugin_calls(), [])
        self.assertFalse([c for c in self.herdr_calls() if c.startswith("plugin enable")])

    def test_switch_off_only_hints(self) -> None:
        """插件自己的开关关着（status 脚本报 enabled: false）：只提示 drover oncall on"""
        self.set_plugin(version="0.7.0", source="github")
        root = self.tmp / "plugin-root"
        self.write(root / "bin" / "run-node.sh", STATUS_OFF_RUN_NODE)
        self.write(root / "src" / "actions" / "status.mjs", "// fake\n")
        out = self.install_json("--host", "claude", *self.ws_args())
        step = self.step(out, "oncall-plugin")
        self.assertEqual((step["level"], step["changes"]), ("warn", False))
        self.assertIn("drover oncall on", step["note"])
        self.assertFalse([c for c in self.herdr_calls() if c.startswith("plugin action")])

    def test_legacy_ids_hinted(self) -> None:
        self.set_herdr(plugins=[{"plugin_id": "fulanto.oncall", "version": "0.3.0", "enabled": True,
                                 "source": {"kind": "github"}}])
        out = self.install_json("--host", "claude", *self.ws_args(), "--oncall")
        step = self.step(out, "oncall-plugin")
        self.assertIn("fulanto.oncall", step["note"])
        self.assertEqual(self.plugin_calls(), [ONCALL_INSTALL])

    def test_no_oncall_skips_and_is_remembered(self) -> None:
        """--no-oncall 照样认：跳过插件和 drover.env（连 plugin list 都不调），记进 config.json；之后不带参数重跑也跳过、
        "下一步"里也不再提 --oncall；--oncall 改回来。主会话登记 hook 照装（登记主会话是 drover 自己的事）。"""
        out = self.install_json("--host", "claude", *self.ws_args(), "--no-oncall")
        self.assertFalse(out["oncall"])
        self.assertEqual(self.load(self.config)["oncall"], False)
        for key in ("oncall-node", "oncall-plugin", "oncall-env"):
            self.assertEqual((self.step(out, key)["changes"], self.step(out, key)["level"]), (False, "ok"))
        self.assertFalse([c for c in self.herdr_calls() if c.startswith("plugin")])
        self.assertFalse(self.oncall_dir.exists())
        self.assertIn(self.session_entry(), self.load(self.settings)["hooks"]["SessionStart"][0]["hooks"])
        self.assertNotIn("--oncall", "\n".join(out["next_steps"]))
        again = self.install_json("--host", "claude")
        self.assertFalse(again["oncall"])
        self.assertFalse([c for c in self.herdr_calls() if c.startswith("plugin")])
        self.assertTrue(self.install_json("--check")["ok"])
        self.assertIn("不接（要接加 --oncall）", self.run_install("--host", "claude", "--dry-run").stdout)
        back = self.install_json("--host", "claude", "--oncall")
        self.assertTrue(back["oncall"])
        self.assertIn("只改显式传的键", self.step(back, "config")["action"])
        self.assertEqual(self.load(self.config)["oncall"], True)
        self.assertEqual(self.plugin_calls(), [ONCALL_INSTALL])
        self.assertEqual(self.drover_env.read_text(), DROVER_ENV)
        self.assertEqual(len(self.backups(self.state_home, "config.json")), 1)  # 改 config.json 之前备份
        p = self.run_install("--no-oncall", "--oncall", rc=2)
        self.assertIn("not allowed with", p.stderr)

    def test_fresh_install_skips_by_default(self) -> None:
        """新装不带参数：不装插件、不写 drover.env、不配对，config.json 里不记 oncall（没选过），--check 不算失败；
        "下一步"提示可以加 --oncall。只读查一次 plugin list（看是不是以前就装着插件）"""
        out = self.install_json("--host", "claude", *self.ws_args())
        self.assertFalse(out["oncall"])
        self.assertNotIn("oncall", self.load(self.config))
        for key in ("oncall-node", "oncall-plugin", "oncall-env"):
            step = self.step(out, key)
            self.assertEqual((step["changes"], step["level"]), (False, "ok"), key)
        self.assertIn("--oncall", self.step(out, "oncall-plugin")["state"])
        self.assertEqual([c for c in self.herdr_calls() if c.startswith("plugin")], ["plugin list --json"])
        self.assertFalse(self.oncall_dir.exists())
        self.assertNotIn("oncall", self.load(self.state))
        self.assertIn("重跑 install 加 --oncall", "\n".join(out["next_steps"]))
        self.assertNotIn("drover oncall pair", "\n".join(out["next_steps"]))
        check = self.install_json("--check")
        self.assertTrue(check["ok"])
        verdicts = dict(zip([s["key"] for s in check["steps"]], check["verdicts"]))
        self.assertEqual([verdicts[k] for k in ("oncall-node", "oncall-plugin", "oncall-env")], ["ok", "ok", "ok"])
        # 没有 Node、herdr 没在运行也不影响：没选装的不查
        self.set_herdr(plugin_list_fail=True)
        self.assertTrue(self.install_json("--check", env=self.env_without_node())["ok"])
        self.assertEqual(self.plugin_calls(), [])

    def test_already_installed_kept_on_rerun(self) -> None:
        """以前默认装的那批（config.json 里没有 oncall 键）：drover update 那样只带 --mode / --host 重跑，照旧保留、照旧更新，
        --check 照旧查。认"已经接上了"：安装记录里有 oncall，或者 herdr 里装着插件"""
        self.install_json("--host", "claude", *self.ws_args(), "--oncall")
        config = self.load(self.config)
        del config["oncall"]  # 改默认之前装的：config.json 里没有这个键
        self.write_json(self.config, config)
        # 1) 安装记录里有 oncall：插件在 GitHub 上出了新版、本机的低于最低版本 → update，不卸、不停
        self.set_herdr(plugins=[dict(self.herdr_state()["plugins"][0], version="0.6.6")])
        self.drover_env.write_text("MAIN_AGENT_NAME=old\n")
        out = self.install_json("--mode", "link", "--host", "claude")
        self.assertTrue(out["oncall"])
        self.assertIn("herdr " + ONCALL_UPDATE, self.step(out, "oncall-plugin")["action"])
        self.assertEqual(self.plugin_calls(), [ONCALL_INSTALL, ONCALL_UPDATE])
        self.assertEqual(self.herdr_state()["plugins"][0]["version"], "0.7.0")
        self.assertEqual(self.drover_env.read_text(), DROVER_ENV)
        self.assertEqual(self.load(self.state)["oncall"], {"installed_by_drover": True, "env": str(self.drover_env)})
        self.assertNotIn("oncall", self.load(self.config))  # 不替用户写选择
        self.drover_env.unlink()
        self.assertEqual(self.step(self.install_json("--check", rc=1), "oncall-env")["level"], "fail")  # 照旧查
        # 2) 安装记录里没有（比如别人自己装的插件、记录被删了）：herdr 里装着插件也算接上了
        self.state.unlink()
        out = self.install_json("--mode", "link", "--host", "claude")
        self.assertTrue(out["oncall"])
        self.assertEqual(self.drover_env.read_text(), DROVER_ENV)
        self.assertEqual(self.plugin_calls(), [ONCALL_INSTALL, ONCALL_UPDATE])  # 版本够了：不装、不更新
        self.assertTrue(self.install_json("--check")["ok"])

    def test_uninstall_removes_env_hook_and_only_drover_installed_plugin(self) -> None:
        """--uninstall：删 drover.env、撤主会话登记 hook；插件是 drover 装的才卸；插件的 .env 不删"""
        self.install_json("--host", "claude", *self.ws_args(), "--oncall")
        user_env = self.write_user_env()
        out = self.install_json("--uninstall")
        self.assertTrue(out["ok"])
        self.assertFalse(self.drover_env.exists())
        self.assertEqual((self.oncall_dir / ".env").read_bytes(), user_env)
        self.assertIn("plugin uninstall " + PLUGIN_ID, self.herdr_calls())
        self.assertEqual(self.herdr_state()["plugins"], [])
        self.assertFalse(self.settings.exists())  # install 新建的 settings.json：两个 hook 和权限都撤完，整个删掉
        self.assertTrue(any("飞书配对" in k for k in out["kept"]))

        # 用户自己装的插件：drover.env 删，插件不卸
        self.set_plugin(version="0.7.0", source="github")
        self.install_json("--host", "claude", *self.ws_args())
        self.assertFalse(self.load(self.state)["oncall"]["installed_by_drover"])
        before = len([c for c in self.herdr_calls() if c.startswith("plugin uninstall")])
        out = self.install_json("--uninstall")
        self.assertIn("不是 drover 装的", self.step(out, "oncall-plugin")["state"])
        self.assertFalse(self.step(out, "oncall-plugin")["changes"])
        self.assertEqual(len([c for c in self.herdr_calls() if c.startswith("plugin uninstall")]), before)
        self.assertEqual(len(self.herdr_state()["plugins"]), 1)
        self.assertFalse(self.drover_env.exists())

    def test_uninstall_one_host_keeps_oncall(self) -> None:
        """只撤一个宿主、另一个还装着：drover.env 和插件留着（宿主无关，和路由区块一样）"""
        self.install_json("--host", "both", *self.ws_args(), "--oncall")
        out = self.install_json("--uninstall", "--host", "codex")
        self.assertFalse([s for s in out["steps"] if s["key"].startswith("oncall")])
        self.assertTrue(self.drover_env.exists())
        self.assertEqual(self.load(self.state)["oncall"]["env"], str(self.drover_env))

    def test_dry_run_lists_commands_and_writes_nothing(self) -> None:
        """dry-run：计划里列出要执行的 herdr 命令；不装、不写，也不调会建目录的 config-dir"""
        before = self.snapshot()
        out = self.install_json("--host", "claude", *self.ws_args(), "--oncall", "--dry-run")
        self.assertIn("herdr " + ONCALL_INSTALL, self.step(out, "oncall-plugin")["action"])
        env_step = self.step(out, "oncall-env")
        self.assertEqual(env_step["path"], "<插件配置目录>/drover.env")
        self.assertIn(f"herdr plugin config-dir {PLUGIN_ID}", env_step["action"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse([c for c in self.herdr_calls() if c.startswith(("plugin install", "plugin config-dir"))])
        text = self.run_install("--host", "claude", *self.ws_args(), "--oncall", "--dry-run").stdout
        self.assertIn("herdr " + ONCALL_INSTALL, text)
        self.assertIn("herdr plugin install / update", text)
        self.assertEqual(self.snapshot(), before)

    def test_node_missing_or_old(self) -> None:
        """--check：Node 低于 22 只提示（收不到飞书回复）；没有 Node 算没装对。install 时没有 Node：插件这一项失败、
        说明原因，其余照装"""
        self.install_json("--host", "claude", *self.ws_args(), "--oncall")
        self.set_node("v20.11.1")
        check = self.install_json("--check")
        node = self.step(check, "oncall-node")
        self.assertEqual(node["level"], "warn")
        self.assertIn("收不到飞书里的回复", node["note"])
        self.set_node("v16.20.0")
        self.assertEqual(self.step(self.install_json("--check", rc=1), "oncall-node")["level"], "fail")
        env = self.env_without_node()
        check = self.install_json("--check", rc=1, env=env)
        verdicts = dict(zip([s["key"] for s in check["steps"]], check["verdicts"]))
        self.assertEqual(verdicts["oncall-node"], "fail")
        self.assertIn("没找到 Node", self.step(check, "oncall-node")["state"])
        self.assertIn("oncall-node", [s["key"] for s, v in zip(check["steps"], check["verdicts"]) if v == "fail"])

    def test_install_without_node_fails_only_that_item(self) -> None:
        env = self.env_without_node()
        out = self.install_json("--host", "claude", *self.ws_args(), "--oncall", rc=1, env=env)
        step = self.step(out, "oncall-plugin")
        self.assertIn("没法装", step["action"])
        self.assertIn("--no-oncall", step["note"])
        self.assertEqual(self.plugin_calls(), [])
        for key in ("pkg", "bin", "config", "claude-hook", "claude-session-hook", "routing"):
            self.assertTrue(self.step(out, key)["result"].startswith("完成"), key)
        self.assertTrue(self.agents.exists())

    def test_install_failure_does_not_block_others(self) -> None:
        """GitHub 访问不了（plugin install 失败）：这一项报失败、写明原因和 --no-oncall，其余照装，退出码 1"""
        self.set_herdr(plugin_install_fail=True)
        out = self.install_json("--host", "claude", *self.ws_args(), "--oncall", rc=1)
        step = self.step(out, "oncall-plugin")
        self.assertTrue(step["result"].startswith("失败"))
        self.assertIn("could not reach github.com", step["result"])
        self.assertIn("--no-oncall", step["result"])
        self.assertTrue(self.step(out, "oncall-env")["result"].startswith("失败"))
        self.assertTrue(self.step(out, "routing")["result"].startswith("完成"))
        self.assertTrue(self.step(out, "state")["result"].startswith("完成"))
        self.assertNotIn("oncall", self.load(self.state))  # 没装上：不记"drover 装的"
        self.set_herdr(plugin_install_fail=False)
        self.install_json("--host", "claude")  # 网络好了重跑：补上
        self.assertEqual(self.drover_env.read_text(), DROVER_ENV)

    def test_plugin_list_failure(self) -> None:
        """--oncall 时 herdr plugin list 报错（比如 herdr 没在运行）：插件这一项失败（说明原因），其余照装"""
        self.set_herdr(plugin_list_fail=True)
        out = self.install_json("--host", "claude", *self.ws_args(), "--oncall", rc=1)
        step = self.step(out, "oncall-plugin")
        self.assertIn("查不了", step["state"])
        self.assertIn("herdr 没在运行", step["state"])
        self.assertEqual(step["result"], "没法处理")
        self.assertTrue(self.step(out, "routing")["result"].startswith("完成"))

    def test_check_reports_env_and_plugin(self) -> None:
        self.install_json("--host", "claude", *self.ws_args(), "--oncall")
        self.drover_env.write_text("MAIN_AGENT_NAME=x\n")
        self.assertEqual(self.step(self.install_json("--check"), "oncall-env")["level"], "warn")
        self.drover_env.unlink()
        bad = self.install_json("--check", rc=1)
        self.assertEqual(self.step(bad, "oncall-env")["level"], "fail")
        self.set_herdr(plugins=[])
        bad = self.install_json("--check", rc=1)
        self.assertEqual(self.step(bad, "oncall-plugin")["level"], "fail")
        self.assertIn("没装", self.step(bad, "oncall-env")["state"])
        self.assertEqual(self.plugin_calls(), [ONCALL_INSTALL])  # --check 只查不装

    def test_herdr_installed_in_same_run(self) -> None:
        """herdr 是这次才装上的：装完再查插件、再装，drover.env 跟着写"""
        nobin = self.tmp / "nobin"
        nobin.mkdir()
        (nobin / "claude").write_text("#!/bin/sh\nexit 0\n")
        (nobin / "claude").chmod(0o755)
        os.symlink(self.fakebin / "node", nobin / "node")
        dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not os.path.exists(os.path.join(d, "herdr"))]
        script = self.tmp / "installer.sh"
        script.write_text(f"#!/bin/sh\nmkdir -p \"$HOME/.local/bin\"\nln -sf '{self.fakebin / 'herdr'}' \"$HOME/.local/bin/herdr\"\n")
        env = dict(self.env, PATH=os.pathsep.join([str(nobin), *dirs]), **{INSTALLER_ENV: f"sh '{script}'"})
        out = self.install_json("--install-deps", "--host", "claude", *self.ws_args(), "--oncall", env=env)
        self.assertEqual(self.step(out, "oncall-plugin")["state"], "herdr 还没装")
        self.assertEqual(self.plugin_calls(), [ONCALL_INSTALL])
        self.assertEqual(self.drover_env.read_text(), DROVER_ENV)


def disp_home(home: Path, p: Path) -> str:
    return "~" + str(p)[len(str(home)):]


# ---------------------------------------------------------------- 主会话登记 hook（~/.claude/settings.json 的 SessionStart）


class SessionHookTests(InstallTestBase):
    def ours(self) -> list:
        return [h for g in self.load(self.settings).get("hooks", {}).get("SessionStart", []) for h in g.get("hooks", [])
                if "main-register --hook" in h.get("command", "")]

    def test_install_update_check_uninstall(self) -> None:
        """装：SessionStart 末尾加一组（不设 matcher），herdr 的 SessionStart、用户的其他 hook 原样保留；重跑幂等；
        旧路径就地更新；--check 缺了算没装对；--uninstall 只撤自己那一项"""
        original = {"hooks": {"SessionStart": HERDR_SESSION_START, "PreToolUse": [USER_PRE_TOOL]}, "model": "opus"}
        self.write_json(self.settings, original)
        out = self.install_json("--host", "claude", *self.ws_args())
        step = self.step(out, "claude-session-hook")
        self.assertIn("SessionStart（不设 matcher）", step["action"])
        self.assertIn("timeout 15 秒", step["action"])
        settings = self.load(self.settings)
        self.assertEqual(settings["hooks"]["SessionStart"], HERDR_SESSION_START + [{"hooks": [self.session_entry()]}])
        self.assertEqual(settings["hooks"]["PreToolUse"][0], USER_PRE_TOOL)
        self.assertNotIn(str(PKG), self.session_command())  # 指向装好的包
        again = self.install_json("--host", "claude")
        self.assertFalse(self.step(again, "claude-session-hook")["changes"])
        self.assertEqual(len(self.ours()), 1)

        # 旧路径（比如以前装在别处）：就地更新那一项，不重复追加
        stale = "python3 -I /old/pkg/bin/drover main-register --hook || true"
        settings = self.load(self.settings)
        settings["hooks"]["SessionStart"][-1]["hooks"][0]["command"] = stale
        self.write_json(self.settings, settings)
        bad = self.install_json("--check", rc=1)
        self.assertEqual(self.step(bad, "claude-session-hook")["level"], "fail")
        upd = self.install_json("--host", "claude")
        self.assertIn("更新那一项", self.step(upd, "claude-session-hook")["action"])
        self.assertEqual(self.ours(), [self.session_entry()])
        self.assertTrue(self.install_json("--check")["ok"])

        # 被删了：--check 报没装对，重跑补上
        settings = self.load(self.settings)
        settings["hooks"]["SessionStart"] = HERDR_SESSION_START
        self.write_json(self.settings, settings)
        bad = self.install_json("--check", rc=1)
        failed = [s["key"] for s, v in zip(bad["steps"], bad["verdicts"]) if v == "fail"]
        self.assertEqual(failed, ["claude-session-hook"])
        self.install_json("--host", "claude")
        self.assertEqual(len(self.ours()), 1)

        self.install_json("--uninstall")
        after = self.load(self.settings)
        self.assertEqual((after["hooks"], after["model"]), (original["hooks"], original["model"]))  # 只撤自己的，别的原样

    def test_check_needs_installed_cli(self) -> None:
        """hook 在、但装好的包里没有 bin/drover（包被删了）：--check 算没装对"""
        self.install_json("--host", "claude", "--mode", "copy", *self.ws_args())
        (self.pkg_dir / "bin" / "drover").unlink()
        bad = self.install_json("--check", rc=1)
        self.assertIn("不存在", self.step(bad, "claude-session-hook")["state"])

    def test_hook_command_is_quiet_outside_main_session(self) -> None:
        """照 Claude Code 的做法用 shell 跑 settings 里的命令：不是主会话（不在 herdr 里、工作目录不是主工作台）时
        退出码 0、stdout 为空，一次 herdr 都不调"""
        self.install_json("--host", "claude", *self.ws_args())
        calls_before = len(self.herdr_calls())
        env = {k: v for k, v in self.env.items() if not k.startswith("HERDR_")}  # 测试进程可能跑在真的 herdr 里
        payload = json.dumps({"session_id": "s1", "cwd": str(self.tmp), "hook_event_name": "SessionStart",
                              "source": "startup"})
        for extra in ({}, {"HERDR_ENV": "1", "HERDR_PANE_ID": "w0:p0"}):
            p = subprocess.run(["sh", "-c", self.session_command()], input=payload, env=dict(env, **extra),
                               cwd=str(self.tmp), capture_output=True, text=True, timeout=60)
            self.assertEqual((p.returncode, p.stdout), (0, ""), p.stderr)
        self.assertEqual(len(self.herdr_calls()), calls_before)

    def test_codex_host_has_no_session_hook(self) -> None:
        """Codex 不装：codex 的 SessionStart 第一条消息时才跑、新 hook 要信任（会卡住所有 codex 会话的启动）"""
        out = self.install_json("--host", "codex", *self.ws_args())
        self.assertFalse([s for s in out["steps"] if s["key"] == "claude-session-hook"])
        self.assertFalse((self.codex / "hooks.json").exists())
        self.assertIn("第一条消息时跑 drover main-register", self.run_install("--help").stdout)


# ---------------------------------------------------------------- 主会话读新规则 hook（~/.claude/settings.json 的 UserPromptSubmit）

USER_PROMPT_SUBMIT = [{"hooks": [{"type": "command", "command": "~/.claude/hooks/prompt-log.sh"}]}]


class PromptHookTests(InstallTestBase):
    def ours(self) -> list:
        return [h for g in self.load(self.settings).get("hooks", {}).get("UserPromptSubmit", [])
                for h in g.get("hooks", []) if "rules --hook" in h.get("command", "")]

    def test_install_rerun_check_uninstall(self) -> None:
        """装：UserPromptSubmit 末尾加一组（不设 matcher、timeout 10 秒），用户自己的 UserPromptSubmit 和别的 hook 原样保留；
        重跑幂等（不重复追加）；旧路径就地更新；被删了 --check 算没装对、重跑补上；--uninstall 只撤自己那一项"""
        original = {"hooks": {"UserPromptSubmit": USER_PROMPT_SUBMIT, "SessionStart": HERDR_SESSION_START},
                    "model": "opus"}
        self.write_json(self.settings, original)
        out = self.install_json("--host", "claude", *self.ws_args())
        step = self.step(out, "claude-prompt-hook")
        self.assertIn("UserPromptSubmit（不设 matcher）", step["action"])
        self.assertIn("timeout 10 秒", step["action"])
        settings = self.load(self.settings)
        self.assertEqual(settings["hooks"]["UserPromptSubmit"], USER_PROMPT_SUBMIT + [{"hooks": [self.prompt_entry()]}])
        self.assertNotIn(str(PKG), self.prompt_command())  # 指向装好的包
        again = self.install_json("--host", "claude")
        self.assertFalse(self.step(again, "claude-prompt-hook")["changes"])
        self.assertEqual(self.ours(), [self.prompt_entry()])
        self.assertTrue(self.install_json("--check")["ok"])

        stale = "python3 -I /old/pkg/bin/drover rules --hook || true"
        settings["hooks"]["UserPromptSubmit"][-1]["hooks"][0]["command"] = stale
        self.write_json(self.settings, settings)
        self.assertEqual(self.step(self.install_json("--check", rc=1), "claude-prompt-hook")["level"], "fail")
        upd = self.install_json("--host", "claude")
        self.assertIn("更新那一项", self.step(upd, "claude-prompt-hook")["action"])
        self.assertEqual(self.ours(), [self.prompt_entry()])

        settings = self.load(self.settings)
        settings["hooks"]["UserPromptSubmit"] = USER_PROMPT_SUBMIT
        self.write_json(self.settings, settings)
        bad = self.install_json("--check", rc=1)
        self.assertEqual([s["key"] for s, v in zip(bad["steps"], bad["verdicts"]) if v == "fail"],
                         ["claude-prompt-hook"])
        self.install_json("--host", "claude")
        self.assertEqual(len(self.ours()), 1)

        out = self.install_json("--uninstall")
        self.assertTrue(self.step(out, "claude-prompt-hook")["changes"])
        after = self.load(self.settings)
        self.assertEqual((after["hooks"], after["model"]), (original["hooks"], original["model"]))

    def test_uninstall_drops_empty_event(self) -> None:
        """原来没有 UserPromptSubmit：撤完不留空的 UserPromptSubmit"""
        self.write_json(self.settings, {"hooks": {"SessionStart": HERDR_SESSION_START}})
        self.install_json("--host", "claude", *self.ws_args())
        self.assertEqual(len(self.ours()), 1)
        self.install_json("--uninstall")
        self.assertEqual(self.load(self.settings)["hooks"], {"SessionStart": HERDR_SESSION_START})

    def test_hook_command_is_quiet_outside_main_session(self) -> None:
        """照 Claude Code 的做法用 shell 跑 settings 里的命令：不是主会话（没有 main-session.json、session_id 对不上）、
        stdin 不是 JSON 时退出码 0、stdout 为空，一次 herdr 都不调"""
        self.install_json("--host", "claude", *self.ws_args())
        calls_before = len(self.herdr_calls())
        env = {k: v for k, v in self.env.items() if not k.startswith("HERDR_")}
        payload = json.dumps({"session_id": "s1", "cwd": str(self.ws), "hook_event_name": "UserPromptSubmit",
                              "prompt": "看板"})
        for text in (payload, "不是 JSON"):
            p = subprocess.run(["sh", "-c", self.prompt_command()], input=text, env=env, cwd=str(self.ws),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual((p.returncode, p.stdout), (0, ""), p.stderr)
        self.write_json(self.home / ".drover" / "main-session.json", {"session_id": "s-main"})
        p = subprocess.run(["sh", "-c", self.prompt_command()], input=payload, env=env, cwd=str(self.ws),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual((p.returncode, p.stdout), (0, ""), p.stderr)
        self.assertEqual(len(self.herdr_calls()), calls_before)

    def test_codex_host_has_no_prompt_hook(self) -> None:
        """Codex 不装：新 hook 要用户手动信任，信任之前所有 codex 会话（含编排者、worker）启动都卡住"""
        out = self.install_json("--host", "codex", *self.ws_args())
        self.assertFalse([s for s in out["steps"] if s["key"] == "claude-prompt-hook"])
        self.assertFalse((self.codex / "hooks.json").exists())


# ---------------------------------------------------------------- 终端里装完当场引导配对飞书（伪终端）


PAIR_URL = "https://example.invalid/pair?user_code=TEST-0001"
# 假的插件 status 脚本（contract 第 5 节的形状）：飞书配没配看假 herdr 状态文件里的 feishu_paired。
FAKE_STATUS = '''import json
state = json.load(open({state!r}))
print(json.dumps({{"ok": True, "version": "0.7.0", "enabled": True,
                  "channels": {{"selected": ["feishu"], "telegram": {{"paired": False}},
                               "feishu": {{"paired": bool(state.get("feishu_paired")), "replies_supported": True}}}}}}))
'''


class OncallPairingTests(InstallTestBase):
    """在交互终端里带 --oncall 装、插件装好且版本够、飞书没配对：装完问一句 [Y/n]，默认是 → 发起配对、打印链接（有 qrencode
    就画二维码）、等到配上、发 test。已配对一句带过；非交互不问；没选装的不问。用伪终端跑（stdin、stdout 都是终端）。"""

    def setUp(self) -> None:
        super().setUp()
        root = self.fakebin / "plugin-root"  # 假 herdr 装插件时的 plugin_root
        script = self.tmp / "fake_status.py"
        script.write_text(FAKE_STATUS.format(state=str(self.fakebin / "herdr-state.json")))
        self.write(root / "bin" / "run-node.sh", f"#!/bin/bash\nexec '{sys.executable}' '{script}'\n")
        self.write(root / "src" / "actions" / "status.mjs", "// fake\n")
        place_fake(self.fakebin / "qrencode", '#!/bin/sh\necho "[fake-qr] $3"\n')

    def pty_install(self, answers: list, wait: str = "20", oncall: tuple = ("--oncall",)) -> tuple:
        env = dict(self.env, DROVER_PAIR_WAIT=wait, DROVER_PAIR_POLL="0.05", DROVER_PAIR_LOG_WAIT="10")
        return self.pty_run([sys.executable, str(INSTALL), "--host", "claude", *self.ws_args(), *oncall], env, answers)

    def invokes(self) -> list:
        return [c.split()[3] for c in self.herdr_calls() if c.startswith("plugin action invoke")]

    def test_pairs_by_default_then_sends_test(self) -> None:
        self.set_herdr(pair_succeeds=True)
        rc, out, answered = self.pty_install([(b"[Y/n]", "")])
        self.assertEqual((rc, answered), (0, 1), out)
        self.assertIn("现在用飞书扫码配对吗？[Y/n]", out)
        self.assertIn(PAIR_URL, out)                   # 链接：没弹桌面二维码时用它
        self.assertIn("[fake-qr] " + PAIR_URL, out)    # 有 qrencode 就在终端里画
        self.assertIn("Ctrl-C 跳过", out)
        self.assertIn("飞书配对好了", out)
        self.assertIn("飞书里应该收到了", out)
        self.assertEqual(self.invokes(), ["pair-feishu", "test"])
        self.assertNotIn("扫码配对飞书（Mac", out)       # 当场问了，"下一步"里不再重复提示 pair
        self.assertNotIn("telegram", out.lower())

    def test_waits_for_plugin_action_to_finish(self) -> None:
        """真机：插件的配对动作要跑一阵（已配对的机器 20 多秒），herdr 先给 status: running、没有输出的日志条目。
        读链接那一步要等它跑完，不能读到空的就当"没给链接\""""
        self.set_herdr(pair_succeeds=True, action_running_polls=3)
        rc, out, answered = self.pty_install([(b"[Y/n]", "")])
        self.assertEqual((rc, answered), (0, 1), out)
        self.assertIn("发起配对，等插件给出配对链接", out)
        self.assertIn(PAIR_URL, out)
        self.assertNotIn("没读到配对链接", out)
        self.assertIn("飞书配对好了", out)
        self.assertEqual(self.invokes(), ["pair-feishu", "test"])

    def test_already_paired_only_says_so(self) -> None:
        self.set_herdr(feishu_paired=True)
        rc, out, _ = self.pty_install([])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("[Y/n]", out)
        self.assertIn("飞书已配对", out)
        self.assertEqual(self.invokes(), [])

    def test_decline(self) -> None:
        rc, out, answered = self.pty_install([(b"[Y/n]", "n")])
        self.assertEqual((rc, answered), (0, 1), out)
        self.assertIn("先不配", out)
        self.assertIn("drover oncall pair", out)
        self.assertEqual(self.invokes(), [])

    def test_timeout_then_hint(self) -> None:
        rc, out, _ = self.pty_install([(b"[Y/n]", "")], wait="0.3")
        self.assertEqual(rc, 0, out)
        self.assertIn("没等到配对", out)
        self.assertIn("drover oncall pair", out)
        self.assertEqual(self.invokes(), ["pair-feishu"])  # 没配上不发 test

    def test_ctrl_c_skips_waiting(self) -> None:
        rc, out, answered = self.pty_install([(b"[Y/n]", ""), (b"Ctrl-C", signal.SIGINT)], wait="60")
        self.assertEqual((rc, answered), (0, 2), out)
        self.assertIn("跳过了等待", out)
        self.assertIn("drover oncall status", out)
        self.assertEqual(self.invokes(), ["pair-feishu"])

    def test_non_interactive_does_not_ask(self) -> None:
        """agent 帮装（非交互）：不问、不发起配对，只在"下一步"里提示 drover oncall pair（配对由 agent-install.md 第 7 步做）"""
        out = self.install_json("--host", "claude", *self.ws_args(), "--oncall")
        self.assertIn("drover oncall pair", "\n".join(out["next_steps"]))
        self.assertEqual(self.invokes(), [])

    def test_not_chosen_not_asked(self) -> None:
        """终端里新装、没带 --oncall：不装插件、不问配对，"下一步"提示可以加 --oncall"""
        rc, out, _ = self.pty_install([], oncall=())
        self.assertEqual(rc, 0, out)
        self.assertNotIn("[Y/n]", out)
        self.assertIn("重跑 install 加 --oncall", out)
        self.assertEqual(self.plugin_calls(), [])
        self.assertEqual(self.invokes(), [])
        text = self.run_install("--host", "claude").stdout
        self.assertNotIn("[Y/n]", text)
        self.assertEqual(self.invokes(), [])

    def test_old_plugin_not_offered(self) -> None:
        """插件太旧（没有 status 脚本，看不出配没配）：不问，"下一步"里照常提示"""
        (self.fakebin / "plugin-root" / "src" / "actions" / "status.mjs").unlink()
        rc, out, _ = self.pty_install([])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("[Y/n]", out)
        self.assertIn("drover oncall pair", out)
        self.assertEqual(self.invokes(), [])


class RoutingOncallTests(InstallTestBase):
    def test_rendered_block_has_oncall_rules(self) -> None:
        """渲染进 AGENTS.md 的区块里有"飞书消息"一节；Telegram 只在这一节里留一句括号说明（主会话要认得 [Telegram] 前缀），
        别处只写飞书；两种宿主"会话刚开始"都有 main-register"""
        self.install_json("--host", "claude", *self.ws_args())
        text = self.agents.read_text()
        self.assert_block(text)
        block = text[text.index(BEGIN):text.index(END)]
        self.assertIn("## 飞书消息", block)
        self.assertNotIn("飞书 / Telegram", block)
        self.assertIn("`[飞书]`", block)
        feishu = block[block.index("## 飞书消息"):block.index("## 宿主差异")]
        self.assertIn("（插件也支持 Telegram，那边来的消息开头是 `[Telegram] `，同样处理）", feishu)
        self.assertEqual(block.lower().count("telegram"), 2)  # 只有那一句括号说明（Telegram、[Telegram] 各一次）
        self.assertEqual(len([line for line in block.splitlines() if "telegram" in line.lower()]), 1)
        claude_part = block[block.index("### Claude Code"):block.index("### Codex")]
        codex_part = block[block.index("### Codex"):]
        for part in (claude_part, codex_part):
            self.assertIn("~/.drover/bin/drover main-register", part)
        self.assertIn("AskUserQuestion", block)

    def test_rendered_block_has_relay_marker_rules(self) -> None:
        """三种转达（等你决定 / 完成 / 受阻）回复第一行以【转达】开头，写明哪些情况不用它；
        飞书一节不再说"每一轮都推"，改成只推飞书提问的回答、【转达】开头的转达、确认框"""
        self.install_json("--host", "claude", *self.ws_args())
        text = self.agents.read_text()
        block = text[text.index(BEGIN):text.index(END)]
        relay = block[block.index("## 追踪与转达"):block.index("## 派发约定")]
        lines = {name: next(line for line in relay.splitlines() if line.strip().startswith(f"- **{name}**"))
                 for name in ("等你决定", "完成", "受阻")}
        for name, line in lines.items():
            self.assertIn("`【转达】", line, name)
        self.assertIn("**`【转达】` 标记**", relay)
        dont = next(line for line in relay.splitlines() if "不要用这个标记" in line)
        for case in ("没有变化", "`rollup:进行中`", "追踪叫醒", "确认框", "普通问答"):
            self.assertIn(case, dont)
        self.assertIn("回答 `[飞书]` 消息不用加", relay)
        self.assertNotIn("Telegram", relay)
        self.assertIn("接收审批和下达命令", relay)
        feishu = block[block.index("## 飞书消息"):block.index("## 宿主差异")]
        self.assertIn("不是每轮都推", feishu)
        self.assertIn("只有飞书提问的那一轮的回答", feishu)
        self.assertIn("以 `【转达】` 开头的转达", feishu)
        self.assertIn("确认框会推到用户手机", feishu)
        self.assertNotIn("每一轮的回答都会推", block)

    def test_rendered_block_has_self_contained_question_rules(self) -> None:
        """等你决定转达时问题本身要带上判断要用的内容（选项框会盖住前文、飞书只推卡片）；
        清单类改用文字并结束这一轮；不靠 preview；Claude Code 写进 description，Codex 写进 title"""
        self.install_json("--host", "claude", *self.ws_args())
        text = self.agents.read_text()
        block = text[text.index(BEGIN):text.index(END)]
        relay = block[block.index("## 追踪与转达"):block.index("## 派发约定")]
        decide = relay[relay.index("- **等你决定**"):relay.index("- **完成**")]
        for phrase in ("选项框会盖住", "不推", "`preview`", "不用提问工具", "结束这一轮", "答法"):
            self.assertIn(phrase, decide)
        self.assertIn("不超过 4 个", decide)
        claude_part = block[block.index("### Claude Code"):block.index("### Codex")]
        codex_part = block[block.index("### Codex"):]
        claude_ask = next(line for line in claude_part.splitlines() if line.startswith("- 提问"))
        self.assertIn("AskUserQuestion", claude_ask)
        self.assertIn("`description`", claude_ask)
        self.assertIn("`preview`", claude_ask)
        codex_ask = next(line for line in codex_part.splitlines() if line.startswith("- **提问**"))
        self.assertIn("request_user_input_async", codex_ask)
        self.assertIn("`title`", codex_ask)
        self.assertIn("不结束这一轮", codex_ask)
        feishu = block[block.index("## 飞书消息"):block.index("## 宿主差异")]
        ask = next(line for line in feishu.splitlines() if "有明确选项的用提问工具" in line)
        self.assertIn("不显示 `preview`", ask)
        self.assertIn("之前的文字也不推", ask)

    def test_rendered_block_has_board_rules(self) -> None:
        """主会话用 watch --main 追踪（Claude Code 放后台 Bash，Monitor 是退路），rollup 用 drover rollup 读，
        转达末尾附 board --pending，会话开始开看板分屏，问进度 / 飞书"看板"回 drover board，Codex 每条消息跑 board --json；
        不再让主会话自己比 rollup_mtime"""
        self.install_json("--host", "claude", *self.ws_args())
        text = self.agents.read_text()
        block = text[text.index(BEGIN):text.index(END)]
        relay = block[block.index("## 追踪与转达"):block.index("## 派发约定")]
        for event in ("`rollup:<状态>`", "`blocked`", "`api-error`", "`gone`", "`stalled(Nm)`", "`herdr unreachable`"):
            self.assertIn(f"  - {event}", relay)
        idle = next(line for line in relay.splitlines() if "只有 `EVENT timeout`" in line)
        self.assertIn("不输出文字", idle)
        self.assertIn("~/.drover/bin/drover rollup --name <编排者名>", relay)
        self.assertIn("`--slug <slug>`", relay)  # 从看板出发、手里是 slug 的用 --slug
        self.assertNotIn("board --json` 的 `name` / `slug`", relay)  # EVENT 行带编排者名，直接 --name，不用再对照
        self.assertIn("~/.drover/bin/drover board --pending --except <slug>", relay)
        self.assertNotIn("rollup_mtime` 比", block)
        self.assertNotIn("按文件修改时间判断", block)
        feishu = block[block.index("## 飞书消息"):block.index("## 宿主差异")]
        self.assertIn("\"看板\"", feishu)
        claude_part = block[block.index("### Claude Code"):block.index("### Codex")]
        codex_part = block[block.index("### Codex"):]
        self.assertIn("~/.drover/bin/drover board --tab", claude_part)
        self.assertIn("~/.drover/bin/drover board --tab", codex_part)
        self.assertNotIn("board --pane", block)  # 看板挪进同级 tab，会话开始不再开下方分屏
        self.assertIn("`run_in_background` 跑 `~/.drover/bin/drover watch --main --until-event`", claude_part)
        self.assertIn("Monitor 跑 `~/.drover/bin/drover watch --main`", claude_part)
        self.assertIn("~/.drover/bin/drover board --json", codex_part)
        self.assertIn("watch --main --until-event --timeout 600", codex_part)

    def test_rendered_block_has_one_click_wrapup_rules(self) -> None:
        """一键收尾的口令（全部收尾 / 收尾 <slug>；看板发来的 [看板] 全部收尾：… / [看板] 收尾：…）从
        board --json 的 wrapup_ready 取名单，看板发来的以消息里的 slug 为准再核对，不在里面的说原因（wrapup_reason），
        逐个 drover prompt 带用户原话，全部完成后照"几个都收尾"合总表；[看板] 开头的消息等同用户本人；终端里问进度跑
        drover board（Markdown 表格），飞书"看板"跑 drover board --plain；不再提"要主会话处理"那组"""
        self.install_json("--host", "claude", *self.ws_args())
        text = self.agents.read_text()
        block = text[text.index(BEGIN):text.index(END)]
        relay = block[block.index("## 追踪与转达"):block.index("## 派发约定")]
        wrap = relay[relay.index("**一键收尾**"):]
        for phrase in ("全部收尾", "收尾 <slug>", "`[看板] 全部收尾：<slug1>、<slug2>`", "`[看板] 收尾：<slug>`",
                       "~/.drover/bin/drover board --json", "`wrapup_ready`", "以消息里写的 slug 为准", "核对",
                       "`wrapup_reason`", "drover prompt --name <编排者名>", "用户原话", "几个都收尾", "`【转达】`"):
            self.assertIn(phrase, wrap)
        feishu = block[block.index("## 飞书消息"):block.index("## 宿主差异")]
        self.assertIn("以 `[看板] ` 开头的消息是用户本人", feishu)
        self.assertIn("~/.drover/bin/drover board --plain", feishu)
        progress = next(line for line in relay.splitlines() if line.startswith("- 用户问进度时"))
        self.assertIn("`~/.drover/bin/drover board`", progress)
        self.assertNotIn("--plain", progress)
        self.assertNotIn('"要主会话处理"那组', block)
        claude_part = block[block.index("### Claude Code"):block.index("### Codex")]
        self.assertIn("\"主会话在处理：\"那一行", claude_part)

    def test_rendered_block_says_no_grep_on_watch(self) -> None:
        """Claude Code shell 里的 grep 是内置 ugrep，`--line-buffered -v` 扣住最新一行，
        编排者的 blocked 晚 10 分钟才到主会话；Claude Code 的追踪规则要写明盯 watch 不接 grep、要过滤只用 /usr/bin/grep"""
        self.install_json("--host", "claude", *self.ws_args())
        text = self.agents.read_text()
        block = text[text.index(BEGIN):text.index(END)]
        claude_part = block[block.index("### Claude Code"):block.index("### Codex")]
        rule = next(line for line in claude_part.splitlines() if "不要接 grep" in line)
        self.assertIn("内置的 ugrep", rule)
        self.assertIn("`/usr/bin/grep --line-buffered`", rule)

    def test_rendered_block_has_auto_update_rules(self) -> None:
        """全自动更新：watch --main 报 drover updated 时主会话跑 drover rules 读入新规则、不用 /clear；
        被挡住、连不上各给用户一行；Codex 看 board --json 的 drover 字段；"更新 drover"不再提醒 /clear"""
        self.install_json("--host", "claude", *self.ws_args())
        text = self.agents.read_text()
        block = text[text.index(BEGIN):text.index(END)]
        relay = block[block.index("## 追踪与转达"):block.index("## 派发约定")]
        self.assertIn("`drover updated(<a>..<b>)`", relay)
        self.assertIn("~/.drover/bin/drover rules", relay)
        self.assertIn("`drover update-blocked`", relay)
        self.assertIn("`drover update-unreachable(24h)`", relay)
        update_line = next(line for line in block.splitlines() if "用户说\"更新 drover\"" in line)
        self.assertIn("~/.drover/bin/drover rules", update_line)
        self.assertIn("\"auto_update\": false", update_line)
        self.assertNotIn("提醒用户 `/clear`", update_line)
        codex_part = block[block.index("### Codex"):]
        self.assertIn("`drover.rules_changed`", codex_part)
        self.assertIn("`drover.auto_update.announced`", codex_part)

    def test_rendered_block_has_session_restart_rules(self) -> None:
        """Claude Code /clear 之后旧的后台 watch 报 drover session-restarted 叫醒新对话，照"会话刚开始"接上、不等用户；
        UserPromptSubmit hook 送进来的规则改动以它为准；Codex /new 之后等用户第一条消息，看板补登记 drover 名字"""
        self.install_json("--host", "claude", *self.ws_args())
        text = self.agents.read_text()
        block = text[text.index(BEGIN):text.index(END)]
        relay = block[block.index("## 追踪与转达"):block.index("## 派发约定")]
        restarted = next(line for line in relay.splitlines() if "`drover session-restarted`" in line)
        self.assertIn("会话刚开始", restarted)
        self.assertIn("不等用户先发消息", restarted)
        claude_part = block[block.index("### Claude Code"):block.index("### Codex")]
        self.assertIn("`drover session-restarted` 叫醒你", claude_part)
        self.assertIn("UserPromptSubmit hook", claude_part)
        codex_part = block[block.index("### Codex"):]
        self.assertIn("`/new` 之后", codex_part)
        self.assertIn("看板会补登记", codex_part)


class RoutingHostKindTests(InstallTestBase):
    def test_rendered_block_follows_main_session_host(self) -> None:
        """编排者和 worker 默认跟主会话的宿主（主会话是 Codex 时派出的仍是 Claude Code）：第 4 步和"派发约定"都写明，
        不再有"编排者默认跑在 claude 里"；resume 时显式传原来的 --kind（不传会跟主会话走）"""
        self.install_json("--host", "claude", *self.ws_args())
        text = self.agents.read_text()
        block = text[text.index(BEGIN):text.index(END)]
        self.assertNotIn("编排者默认跑在 claude 里", block)
        self.assertNotIn("默认都用 claude", block)
        start = next(line for line in block.splitlines() if line.strip().startswith("4. 启动："))
        self.assertIn("[--kind claude|codex]", start)
        self.assertIn("编排者默认和主会话同一个宿主", start)
        conventions = block[block.index("## 派发约定"):block.index("## 飞书消息")]
        self.assertIn("编排者和 worker 默认跟主会话的宿主", conventions)
        resume = next(line for line in block.splitlines() if line.startswith("- **已有任务的后续**"))
        self.assertIn("显式传 `--kind", resume)


# ---- 验收工具（crapper、dryer）：假 python、假 uv，工具源是本地仓库，不联网

QUALITY_TOOL_NAMES = ("crapper", "dryer")
PYTHON_NAMES = ("python3.14", "python3.13", "python3.12", "python3.11", "python3")
# 假 python：版本是同目录 python-version.txt 写的；-m venv <目录> 建一个假 .venv（bin/python 是同目录 venv-python 的副本）。
FAKE_PYTHON = '''#!{python}
import shutil, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
args = sys.argv[1:]
if args[:1] == ["-c"]:
    print((HERE / "python-version.txt").read_text().strip())
elif args[:2] == ["-m", "venv"]:
    (Path(args[2]) / "bin").mkdir(parents=True)
    shutil.copy2(HERE / "venv-python", Path(args[2]) / "bin" / "python")
else:
    sys.exit(2)
'''
# 假 .venv 里的 python：pip install 在设了 FAKE_PIP_FAIL 时失败；-m <工具> --help 打印用法。调用记进工具目录的 venv-calls.log。
FAKE_VENV_PYTHON = '''#!{python}
import os, sys
from pathlib import Path
args = sys.argv[1:]
with (Path(__file__).parent.parent.parent / "venv-calls.log").open("a") as f:
    f.write(" ".join(args) + "\\n")
if args[:3] == ["-m", "pip", "install"]:
    if os.environ.get("FAKE_PIP_FAIL"):
        print("ERROR: No matching distribution found for setuptools>=68")
        sys.exit(1)
elif args[:1] == ["-m"] and args[2:] == ["--help"]:
    print("Usage: " + args[1])
else:
    sys.exit(2)
'''
# 假 uv：python find 找 $UV_PYTHON_INSTALL_DIR/cpython-fake/bin/python3.12（没有退出码 2）；python install 把 $FAKE_PY_SRC 里的
# 假 python 放进 --install-dir（版本 3.12）。调用记进 $FAKE_UV_LOG。
FAKE_UV = '''#!{python}
import os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_UV_LOG"], "a") as f:
    f.write(" ".join(args) + " | UV_PYTHON_INSTALL_DIR=" + os.environ.get("UV_PYTHON_INSTALL_DIR", "") + "\\n")
if args[:2] == ["python", "find"]:
    exe = Path(os.environ.get("UV_PYTHON_INSTALL_DIR", "/nonexistent")) / "cpython-fake" / "bin" / "python3.12"
    if not exe.exists():
        print("error: No interpreter found", file=sys.stderr)
        sys.exit(2)
    print(exe)
elif args[:2] == ["python", "install"]:
    d = Path(args[args.index("--install-dir") + 1]) / "cpython-fake" / "bin"
    d.mkdir(parents=True)
    src = Path(os.environ["FAKE_PY_SRC"])
    shutil.copy2(src / "python3.11", d / "python3.12")
    shutil.copy2(src / "venv-python", d / "venv-python")
    (d / "python-version.txt").write_text("3.12\\n")
else:
    sys.exit(2)
'''


class QualityToolsTests(InstallTestBase):
    """验收工具默认装进 ~/.drover/tools：装成功、装失败只记警告（install 退出码不变）、卸载删掉、--check 只提示、开关关掉不装；
    本机没有 Python 3.11+ 时装一份 drover 私有的 uv、再用它装 Python。"""

    def setUp(self) -> None:
        super().setUp()
        self.env.pop(QUALITY_SWITCH_ENV)
        self.tools = self.state_home / "tools"
        # PATH 最前面放假的 python3.14 … python3（盖住机器上真的），版本由 set_python 定。
        self.pybin = self.tmp / "pybin"
        self.pybin.mkdir()
        for name in PYTHON_NAMES:
            place_fake(self.pybin / name, FAKE_PYTHON.replace("{python}", sys.executable))
        place_fake(self.pybin / "venv-python", FAKE_VENV_PYTHON.replace("{python}", sys.executable))
        self.set_python("3.11")
        self.env["PATH"] = f"{self.pybin}{os.pathsep}{self.env['PATH']}"
        # 工具源：本地仓库，钉的提交是它们的 HEAD
        self.pins = {}
        git_env = {**self.env, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        for name in QUALITY_TOOL_NAMES:
            repo = self.tmp / "tool-src" / name
            self.write(repo / "pyproject.toml", f'[project]\nname = "{name}"\n')
            for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
                subprocess.run(["git", "-C", str(repo), *args], env=git_env, check=True, capture_output=True)
            sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], env=git_env, check=True,
                                 capture_output=True, text=True).stdout.strip()
            self.pins[name] = [str(repo), sha]
        self.env[QUALITY_SOURCE_ENV] = json.dumps(self.pins)

    def set_python(self, version: str) -> None:
        (self.pybin / "python-version.txt").write_text(version + "\n")

    def head(self, name: str) -> str:
        return subprocess.run(["git", "-C", str(self.tools / name), "rev-parse", "HEAD"], capture_output=True,
                              text=True).stdout.strip()

    def venv_calls(self, name: str) -> list:
        log = self.tools / name / "venv-calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def install(self, *args: str, rc: int = 0, env: dict = None) -> dict:
        return self.install_json("--host", "claude", *self.ws_args(), *args, rc=rc, env=env)

    def no_python_env(self) -> dict:
        """机器上没有 3.11+（假 python 都报 3.9）也没有 uv；假 uv 的源和日志。"""
        self.set_python("3.9")
        dirs = [d for d in self.env["PATH"].split(os.pathsep) if d and not os.path.exists(os.path.join(d, "uv"))]
        env = {**self.env, "PATH": os.pathsep.join(dirs), "FAKE_UV_LOG": str(self.tmp / "uv.log"),
               "FAKE_PY_SRC": str(self.pybin)}
        self.assertIsNone(shutil.which("uv", path=env["PATH"]))
        return env

    def test_install_check_rerun_uninstall(self) -> None:
        """装成功：clone、切到钉的提交、建 .venv、pip install -e、--help；重跑什么都不动；--check 是 ✓；卸载删掉 ~/.drover/tools"""
        out = self.install()
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.step(out, "tools-python")["state"], "Python 3.11")
        for name in QUALITY_TOOL_NAMES:
            self.assertTrue(self.step(out, f"tool-{name}")["result"].startswith("完成"), out)
            self.assertEqual(self.head(name), self.pins[name][1])
            self.assertTrue((self.tools / name / ".venv" / "bin" / "python").is_file())
            self.assertEqual(self.venv_calls(name)[:2], [f"-m pip install --quiet -e {self.tools / name}",
                                                         f"-m {name} --help"])
        again = self.install()
        for name in QUALITY_TOOL_NAMES:
            step = self.step(again, f"tool-{name}")
            self.assertEqual((step["changes"], step["state"]), (False, f"已装 {self.pins[name][1][:12]}"))
        self.assertEqual(self.step(again, "tools-python")["state"], "用不着（两个工具都装好了）")
        check = self.install_json("--check")
        verdicts = dict(zip([s["key"] for s in check["steps"]], check["verdicts"]))
        self.assertEqual([verdicts[f"tool-{n}"] for n in QUALITY_TOOL_NAMES], ["ok", "ok"])
        un = self.install_json("--uninstall")
        self.assertTrue(self.step(un, "quality-tools")["result"].startswith("完成"), un)
        self.assertFalse(self.tools.exists())

    def test_failure_only_warns(self) -> None:
        """pip install 失败：只记警告、退出码 0（汇总写"只记警告"），半截的 .venv 删掉，提示里有原因和怎么补；--check 只提示（!），
        修好后重跑就装上"""
        env = {**self.env, "FAKE_PIP_FAIL": "1"}
        p = self.run_install("--host", "claude", *self.ws_args(), env=env)
        self.assertIn(f"只记警告 {len(QUALITY_TOOL_NAMES)} 项", p.stdout)
        self.assertNotIn("有失败项", p.stdout)
        out = self.install(env=env)
        self.assertTrue(out["ok"], out)
        for name in QUALITY_TOOL_NAMES:
            step = self.step(out, f"tool-{name}")
            self.assertTrue(step["result"].startswith("警告"), step)
            self.assertIn("pip install -e 退出码 1", step["note"])
            self.assertIn("No matching distribution", step["note"])
            self.assertIn("重跑 install", step["note"])
            self.assertFalse((self.tools / name / ".venv").exists())
        self.assertTrue(any("验收工具 crapper：" in n for n in out["notes"]), out["notes"])
        check = self.install_json("--check")
        verdicts = dict(zip([s["key"] for s in check["steps"]], check["verdicts"]))
        self.assertEqual(verdicts["tool-crapper"], "warn")
        self.assertTrue(check["ok"])
        fixed = self.install()
        self.assertTrue(all(self.step(fixed, f"tool-{n}")["result"].startswith("完成") for n in QUALITY_TOOL_NAMES), fixed)

    def test_no_python311_installs_private_uv(self) -> None:
        """本机没有 3.11+、也没有 uv：用 uv 官方脚本（这里换成假的）装一份到 ~/.drover/tools/bin（UV_NO_MODIFY_PATH=1），
        再 uv python install 3.12 进 ~/.drover/tools/python（--no-bin），用它给两个工具建 .venv；~/.local/bin 不碰"""
        env = self.no_python_env()
        installer_log = self.tmp / "uv-installer.log"
        uv_src = self.tmp / "uv-src" / "uv"
        uv_src.parent.mkdir()
        uv_src.write_text(FAKE_UV.replace("{python}", sys.executable))
        uv_src.chmod(0o755)
        env[UV_INSTALLER_ENV] = (f'mkdir -p "$UV_INSTALL_DIR" && cp \'{uv_src}\' "$UV_INSTALL_DIR/uv" && '
                                 f'echo "$UV_INSTALL_DIR $UV_NO_MODIFY_PATH" >> \'{installer_log}\'')
        out = self.install(env=env)
        self.assertTrue(out["ok"], out)
        self.assertEqual(installer_log.read_text().split(), [str(self.tools / "bin"), "1"])
        self.assertIn(f"python install 3.12 --install-dir {self.tools / 'python'} --no-bin",
                      (self.tmp / "uv.log").read_text())
        self.assertTrue(self.step(out, "tools-python")["result"].startswith("完成：Python 3.12"), out)
        for name in QUALITY_TOOL_NAMES:
            self.assertTrue(self.step(out, f"tool-{name}")["result"].startswith("完成"), out)
        self.assertFalse((self.home / ".local" / "bin").exists())

    def test_no_python311_and_uv_fails(self) -> None:
        """没有 3.11+、装 uv 也失败：Python 那一项和两个工具都只记警告，退出码 0，不 clone、不建 .venv"""
        env = {**self.no_python_env(), UV_INSTALLER_ENV: "echo no network; exit 7"}
        out = self.install(env=env)
        self.assertTrue(out["ok"], out)
        py = self.step(out, "tools-python")
        self.assertTrue(py["result"].startswith("警告"), py)
        self.assertIn("退出码 7", py["note"])
        self.assertIn("python@3.12", py["note"])
        for name in QUALITY_TOOL_NAMES:
            self.assertIn("没有 Python 3.11+", self.step(out, f"tool-{name}")["note"])
        self.assertFalse(self.tools.joinpath("crapper").exists())

    def test_dry_run_and_switch_off(self) -> None:
        """--dry-run 列出要做的（clone、切提交、--help）但不建目录；config.json 写 quality_tools: false 就不装、--check 不提示"""
        out = self.install("--dry-run")
        step = self.step(out, "tool-crapper")
        self.assertTrue(step["changes"])
        self.assertIn(f"clone {self.pins['crapper'][0]}", step["action"])
        self.assertIn("--help", step["action"])
        self.assertFalse(self.tools.exists())
        self.write_json(self.config, {"workspace": str(self.ws), "repo_dirs": [str(self.ws)], "quality_tools": False})
        out = self.install_json("--host", "claude")
        self.assertTrue(out["ok"], out)
        for key in ("tools-python", "tool-crapper", "tool-dryer"):
            self.assertFalse(self.step(out, key)["changes"])
            self.assertIn("quality_tools", self.step(out, key)["state"])
        self.assertFalse(self.tools.exists())
        check = self.install_json("--check")
        verdicts = dict(zip([s["key"] for s in check["steps"]], check["verdicts"]))
        self.assertEqual(verdicts["tool-crapper"], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)

