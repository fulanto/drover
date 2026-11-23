#!/usr/bin/env python3
"""要真实 herdr server 的实测用的隔离环境：在 /tmp 下的临时根目录里起 headless 的 herdr server，和用户的 herdr 不共用东西。

为什么不用 herdr 的具名会话（herdr --session <名>）：具名会话只隔离 socket 和会话数据，插件注册表、插件的 config / state、
config.toml 都是共用的，而每个 server 启动都会跑所有 enabled 插件的 startup（herdr v0.9.5 的 src/session.rs:163-173、
src/plugin_paths.rs:44-54、src/server/headless/bootstrap.rs:93）。实测踩过：测试 server 一启动就跑了用户全局插件的 startup，
把用户的飞书收信进程接到了测试会话上。所以这里换的是 herdr 取数据目录的两个根 XDG_CONFIG_HOME、XDG_STATE_HOME
（herdr 的 src/config/io.rs:30-42；它自己的 scripts/smoke_live_handoff_sessions.sh:38-44 也是这样隔离的），另外补上
这两个根盖不住的两样：worktree 目录（默认按 HOME 算）和提示音。

模块用法（worker 现写的端到端脚本）：

    sys.path.insert(0, "<drover 的包或 worktree>/selftest")
    from live_herdr import LiveHerdr

    with LiveHerdr() as h:              # 建临时根目录、做动手前的自检、记下用户真实环境的指纹
        h.start()                       # headless 的默认 server；h.start("two") 是同一根目录下的具名会话
        h.herdr("workspace", "create", "--cwd", str(h.root), "--no-focus")   # 对它发 herdr 命令
        h.run([sys.executable, "bin/drover", "watch", "--once", "x"])        # 带隔离环境跑任意命令
        h.restart()                     # 停了再起
    # 退出 with（中途出错也一样）：停掉这个环境里的 server、核对用户真实环境没变、删临时根目录；自检不过抛 LiveHerdrError

命令行用法：python3 selftest/live_herdr.py run -- <命令…>
起默认 server、带隔离环境跑这条命令、收尾。退出码是命令的；自检不过是 3。只用标准库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Mapping, Optional

# 根目录建在 /tmp 下，不用 $TMPDIR：unix socket 的路径上限是 104 字节（macOS sys/un.h 的 sun_path），macOS 的 $TMPDIR
# 本身就有几十个字符，再接上 config/herdr/sessions/<名>/herdr-client.sock 就超了。
ROOT_BASE = "/tmp"
ROOT_PREFIX = "drover-live-"
SUN_PATH_MAX = 104
LOCKED_ENV = ("XDG_CONFIG_HOME", "XDG_STATE_HOME")
SESSION_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
SELF_CHECK_EXIT = 3


class LiveHerdrError(RuntimeError):
    """自检没过，或者隔离的 server 起不来。"""


def isolated_env(base: Mapping[str, str], root: Path) -> dict:
    """隔离环境：继承的 HERDR_* 全去掉（socket、会话、pane / tab / workspace 的 ID、HERDR_ENV，还有会让 server 去读用户
    config.toml 的 HERDR_CONFIG_PATH），herdr 的两个数据根和 drover 的状态目录指进临时根目录，关掉提示音（隔离 server 里的
    agent 变状态时不在用户机器上响）。"""
    env = {key: value for key, value in base.items() if not key.startswith("HERDR_")}
    env.update(XDG_CONFIG_HOME=str(root / "config"), XDG_STATE_HOME=str(root / "state"),
               DROVER_STATE_DIR=str(root / "drover"), HERDR_DISABLE_SOUND="1")
    return env


def user_herdr_dir(env: Mapping[str, str]) -> Path:
    """用户真实的 herdr 配置目录：按继承环境算，和 herdr 自己的算法一样（XDG_CONFIG_HOME，没有就是 HOME/.config）。"""
    return Path(env.get("XDG_CONFIG_HOME") or Path(env["HOME"]) / ".config") / "herdr"


def under(path: str, root: Path) -> bool:
    resolved = Path(os.path.realpath(path))
    return resolved == root or root in resolved.parents


def digest(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def listener_pid(sock: Path) -> Optional[str]:
    """拿着这个 unix socket 的 herdr 进程的 pid（lsof）；没有进程拿着、或者本机没有 lsof，返回 None。"""
    try:
        out = subprocess.run(["lsof", "-nP", "-U", "-a", "-c", "herdr", "-Fpn"], capture_output=True, text=True,
                             timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    pid = None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line == f"n{sock}":
            return pid
    return None


def user_fingerprint(herdr_dir: Path) -> dict:
    """用户真实 herdr 的指纹，进入时和收尾时各取一次，要一样：默认 server 还是同一个进程（监听进程的 pid、socket 文件），
    插件注册表和 config.toml 没被改，没多出具名会话。具体某个插件的状态（比如 oncall 的收信进程）不归这里管。"""
    sock = herdr_dir / "herdr.sock"
    try:
        inode = sock.stat().st_ino
    except OSError:
        inode = None
    try:
        sessions = sorted(p.name for p in (herdr_dir / "sessions").iterdir())
    except OSError:
        sessions = []
    return {"默认 server 的进程": listener_pid(sock), "默认 socket 文件": inode,
            "plugins.json": digest(herdr_dir / "plugins.json"), "config.toml": digest(herdr_dir / "config.toml"),
            "具名会话": sessions}


def connectable(sock: Path) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(1)
            conn.connect(str(sock))
        return True
    except OSError:
        return False


def wait_ready(proc: subprocess.Popen, sock: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while proc.poll() is None and time.monotonic() < deadline:
        if connectable(sock):
            return True
        time.sleep(0.05)
    return False


def kill(proc: subprocess.Popen) -> None:
    """唯一的兜底：herdr 的 stop 没把它停掉时，结束自己 Popen 出来的这个子进程（不然根目录删不干净）。不碰别的进程。"""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def label(session: Optional[str]) -> str:
    return f"会话 {session} 的 server" if session else "默认会话的 server"


def stop_args(session: Optional[str]) -> list:
    return ["session", "stop", session] if session else ["server", "stop"]


class LiveHerdr:
    """一套隔离的 herdr 环境。root 是临时根目录，env 是隔离环境（可以读，脚本自己起进程时用），config_dir / state_dir 是
    这个环境里 herdr 的配置目录和状态目录；起 server 之前想改 config.toml、放假插件，往这些目录里写。"""

    def __init__(self) -> None:
        self.root: Optional[Path] = None  # 进入 with 时才建；之前没有任何可删的东西
        self.env: dict = {}
        self._servers: dict = {}  # 会话名（默认会话是 None）→ start() 起的 server 进程
        self._user_dir = user_herdr_dir(os.environ)
        self._user_before: dict = {}

    @property
    def config_dir(self) -> Path:
        return self.root / "config" / "herdr"

    @property
    def state_dir(self) -> Path:
        return self.root / "state" / "herdr"

    def socket(self, session: Optional[str] = None) -> Path:
        return (self.config_dir / "sessions" / session if session else self.config_dir) / "herdr.sock"

    def __enter__(self) -> "LiveHerdr":
        self._user_before = user_fingerprint(self._user_dir)
        self.root = Path(os.path.realpath(tempfile.mkdtemp(prefix=ROOT_PREFIX, dir=ROOT_BASE)))
        try:
            for folder in (self.config_dir, self.root / "state", self.root / "drover"):
                folder.mkdir(parents=True)
            # herdr 的 worktree 目录默认是 ~/.herdr/worktrees（按 HOME 算，XDG 的两个根盖不住）：收进根目录。
            (self.config_dir / "config.toml").write_text(
                f"[worktrees]\ndirectory = {json.dumps(str(self.root / 'worktrees'))}\n")
            self.env = isolated_env(os.environ, self.root)
            self._check(None, first=True)
        except BaseException:
            shutil.rmtree(self.root, ignore_errors=True)
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        problems = self.close()
        if problems and exc_type is None:
            raise LiveHerdrError("；".join(problems))
        for problem in problems:  # with 里已经出错了：不盖掉原来的异常
            print(f"live_herdr 收尾：{problem}", file=sys.stderr)
        return False

    def close(self) -> list:
        """停掉这个环境里的 server、做收尾自检、删根目录；返回发现的问题（空的就是都好）。"""
        if self.root is None:
            return []
        problems = []
        steps = [lambda s=s: self.stop(s) for s in list(self._servers)]
        for step in (*steps, self._stop_strays, lambda: self._check_plugins(first=False)):
            try:
                step()
            except (LiveHerdrError, subprocess.TimeoutExpired) as err:
                problems.append(str(err))
        after = user_fingerprint(self._user_dir)
        problems += [f"用户的 herdr（{self._user_dir}）变了：{key} {self._user_before[key]} → {after[key]}"
                     for key in after if after[key] != self._user_before[key]]
        shutil.rmtree(self.root, ignore_errors=True)
        if self.root.exists():
            problems.append(f"临时根目录没删掉：{self.root}")
        return problems

    # ---- 对隔离环境发命令

    def _isolated(self) -> dict:
        """隔离环境。还没进入 with 就拒绝：这时没有隔离环境，命令会带着继承的环境打到用户的 herdr。"""
        if not self.env:
            raise LiveHerdrError("还没进入隔离环境：先 `with LiveHerdr() as h:`")
        return self.env

    def herdr(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
        """对隔离环境发一条 herdr 命令。这个环境里解析不到用户的 socket，所以 stop 这类命令也只停得到这里的 server。"""
        return subprocess.run(["herdr", *args], env=self._isolated(), capture_output=True, text=True, timeout=timeout)

    def run(self, argv: list, env: Optional[Mapping[str, str]] = None, **kwargs) -> subprocess.CompletedProcess:
        """带隔离环境跑任意命令（参数同 subprocess.run）。env 是额外加的变量；隔离环境定的那几个不能覆盖。"""
        extra = dict(env or {})
        locked = sorted(key for key in extra if key in LOCKED_ENV or key.startswith("HERDR_"))
        if locked:
            raise LiveHerdrError(f"这些变量由隔离环境定，不能覆盖：{'、'.join(locked)}")
        return subprocess.run(argv, env={**self._isolated(), **extra}, **kwargs)

    # ---- 起、停、重启

    def start(self, session: Optional[str] = None, timeout: float = 20) -> int:
        """起 headless 的 herdr server：默认会话，或者同一根目录下名叫 session 的具名会话。等到 socket 可连，返回它的 pid。"""
        env = self._isolated()
        if session is not None and (session == "default" or not SESSION_NAME_RE.fullmatch(session)):
            raise LiveHerdrError(f"会话名不合规：{session!r}（默认会话不传名字；名字只用小写字母、数字、- 和 _）")
        if session in self._servers:
            raise LiveHerdrError(f"{label(session)} 已经起着")
        self._check(session)
        log_path = self.root / f"server-{session or 'default'}.log"
        argv = ["herdr", *(["--session", session] if session else []), "server"]
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, cwd=str(self.root))
        if not wait_ready(proc, self.socket(session), timeout):
            kill(proc)
            raise LiveHerdrError(f"{label(session)} 没起来：{log_path.read_text(errors='replace').strip()[-500:]}")
        self._servers[session] = proc
        return proc.pid

    def stop(self, session: Optional[str] = None, timeout: float = 20) -> None:
        """停 start() 起的 server。停的命令只经隔离环境发；没停掉就结束那个子进程。"""
        proc = self._servers.get(session)
        if proc is None:
            raise LiveHerdrError(f"{label(session)} 不是这个隔离环境起的，不停")
        try:
            self.herdr(*stop_args(session), timeout=timeout)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill(proc)
        del self._servers[session]

    def restart(self, session: Optional[str] = None) -> int:
        self.stop(session)
        return self.start(session)

    # ---- 自检

    def _listing(self, args: tuple, *keys: str) -> list:
        """隔离环境下跑一条 herdr 的 --json 列表命令，取出 keys 指的那个列表。"""
        done = self.herdr(*args)
        try:
            value = json.loads(done.stdout) if done.returncode == 0 else None
            for key in keys:
                value = value[key]
        except (ValueError, TypeError, KeyError):
            value = None
        if not isinstance(value, list):
            raise LiveHerdrError(f"自检跑不了：herdr {' '.join(args)} 退出码 {done.returncode}："
                                 f"{(done.stdout + done.stderr).strip()[-300:]}")
        return value

    def _sessions(self) -> list:
        return self._listing(("session", "list", "--json"), "sessions")

    def _check(self, session: Optional[str], first: bool = False) -> None:
        """动手前的自检（进入时、每次起 server 之前），过不了就不起：socket 路径放得下；herdr 解析出来的 socket 都在临时
        根目录下；这个环境里没有用户的插件。"""
        longest = self.socket(session).with_name("herdr-client.sock")
        if len(os.fsencode(longest)) >= SUN_PATH_MAX:
            raise LiveHerdrError(f"socket 路径太长（unix socket 的上限是 {SUN_PATH_MAX} 字节）：{longest}")
        outside = [s["socket_path"] for s in self._sessions() if not under(s["socket_path"], self.root)]
        if outside:
            raise LiveHerdrError(f"隔离没生效：herdr 解析出的 socket 不在临时根目录 {self.root} 下：{'、'.join(outside)}")
        self._check_plugins(first)

    def _check_plugins(self, first: bool) -> None:
        """进入时 herdr plugin list 必须是空的；之后只允许插件目录在临时根目录下的插件（实测自己 link 进来的）。"""
        plugins = self._listing(("plugin", "list", "--json"), "result", "plugins")
        bad = [str(p.get("plugin_id")) for p in plugins if first or not under(p.get("plugin_root") or "/", self.root)]
        if bad and first:
            raise LiveHerdrError(f"这个环境里看得到插件（进入时应该是空的）：{'、'.join(bad)}")
        if bad:
            raise LiveHerdrError(f"有插件的目录不在临时根目录下（要测真实插件，先把它复制进 {self.root} 再 link）："
                                 f"{'、'.join(bad)}")

    def _stop_strays(self) -> None:
        """不是 start() 起的、但还在这个根目录下跑着的 server（比如实测里的客户端自己拉起来的）：同样只经隔离环境停。"""
        for name in self._running():
            self.herdr(*stop_args(name))
        left = self._running()
        if left:
            raise LiveHerdrError(f"隔离根目录下还有 server 在跑、停不掉：{'、'.join(name or 'default' for name in left)}")

    def _running(self) -> list:
        return [None if s["default"] else s["name"] for s in self._sessions() if s["running"]]


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 selftest/live_herdr.py",
                                     description="在和用户的 herdr 隔离的环境里起真实的 herdr server（见文件开头的说明）。")
    actions = parser.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run", help="起隔离的默认 server、带隔离环境跑一条命令、收尾；退出码是命令的，自检不过是 3")
    run.add_argument("command", nargs=argparse.REMAINDER, metavar="-- 命令", help="要跑的命令和它的参数，写在 -- 后面")
    a = parser.parse_args(argv)
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not command:
        parser.error("run 后面要给命令：run -- <命令…>")
    for signum in (signal.SIGTERM, signal.SIGHUP):  # 被结束时也走 with 的收尾，不留下 server
        signal.signal(signum, lambda received, frame: sys.exit(128 + received))
    try:
        with LiveHerdr() as h:
            h.start()
            return h.run(command).returncode
    except LiveHerdrError as err:
        print(f"live_herdr：{err}", file=sys.stderr)
        return SELF_CHECK_EXIT


if __name__ == "__main__":
    sys.exit(main())
