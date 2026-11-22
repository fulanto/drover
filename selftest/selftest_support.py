"""selftest 的公共工具：每个用例一套临时环境，加载 / 执行本 worktree 的 bin/drover。

隔离（每个用例都一样）：
- 一个用例一个临时目录：HOME、DROVER_STATE_DIR（状态目录，里面有 config.json）、假可执行文件目录、git 仓库都在里面，
  用例结束整个删掉。config.json 的 workspace / repo_dirs 指向临时目录，仓库名只在临时目录里解析。
- PATH 最前面是假 herdr 和假测试工具（selftest_fakes），去掉了有 code-review-graph 的目录（accept 不建图）。
- git 用临时的全局配置（GIT_CONFIG_GLOBAL，user.name / email 在里面）、不读系统配置，远端是本地裸仓库，不联网。
- 在进程里加载 bin/drover：先把 os.environ 整个换成上面的环境再加载（模块级常量在加载时就算好了），
  用例结束还原 os.environ；加载后断言 STATE_HOME 就是临时状态目录，不对就直接失败。
- 要等的命令（watch 的"跑满 --timeout 看有没有多报"、轮询）在进程里跑 main()、换上假时钟（FakeClock），等待按假时间走：
  见 drover_in_process、watch、watch_then。
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Optional

import selftest_fakes

PKG = Path(__file__).resolve().parent.parent
DROVER = PKG / "bin" / "drover"
# 真实状态目录，只用来断言临时目录不在它下面。
REAL_STATE = (Path.home() / ".drover").resolve()
DROP_ENV_PREFIXES = ("GIT_", "HERDR_", "DROVER_", "CODEX_")
GIT_IDENTITY = {"GIT_AUTHOR_NAME": "drover selftest", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
                "GIT_COMMITTER_NAME": "drover selftest", "GIT_COMMITTER_EMAIL": "selftest@example.invalid"}
MAIN_PANE = "w1:p1"  # main_rollup 没有主会话记录时登记的 pane（drover rollup 按 HERDR_PANE_ID 和 main-session.json 认主会话）
_loaded = 0
_code: dict = {}  # bin/drover 编译好的代码，每个进程编译一次


def load_module(name: str):
    """把 bin/drover 当模块加载（它没有 .py 后缀）；不写 __pycache__。源码每个进程只编译一次，每次加载都是一份新模块
    （全局变量、模块级常量按加载时的环境重新算）。"""
    loader = importlib.machinery.SourceFileLoader(name, str(DROVER))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    if DROVER not in _code:
        _code[DROVER] = compile(DROVER.read_bytes(), str(DROVER), "exec", dont_inherit=True)
    exec(_code[DROVER], mod.__dict__)
    return mod


def events(stdout: str) -> list:
    """watch 的输出里的 EVENT 行，去掉末尾的 " @时间"。"""
    return [line.split(" @")[0] for line in stdout.splitlines() if line.startswith("EVENT ")]


def rollup_digest(path: Path) -> str:
    """drover 记的 rollup 内容哈希（read_rollup 的 digest：每行去掉首尾空白、删掉空行，再 sha1）。用例写 answered.json 时用：
    "转过答复之后 rollup 没变"按它比。"""
    lines = [line.strip() for line in Path(path).read_text().splitlines() if line.strip()]
    return hashlib.sha1("\n".join(lines).encode(errors="replace")).hexdigest()


class FakeClock:
    """假时钟，换掉在进程里加载的 drover 模块的 time（d.time = clock）：sleep 直接把表往后拨，轮询间隔、节流、阈值、--timeout
    都按假时间走，不用真等，窗口里跑几轮也是确定的；strftime / localtime 照常（只用来打印）。
    起点默认是现在（向上取整到秒：不比真实时间早，整秒起步的加减也是精确的）：drover 会把 time() 写进状态文件
    （main-watch.json 的 idle_since 等），也拿它和别处写的真实时间比（插件日志的 started_unix_ms、用例写的"一小时前"）。
    on_sleep：{第 n 次 sleep: 之后要做的事}（模拟两轮之间发生的变化，比如改假 herdr），或者每次 sleep 之后都调的函数（参数是第几次）。
    limit：真实时间的上限（秒），超了就报错（代替子进程的 timeout，免得写错的用例死循环）。"""

    def __init__(self, start: Optional[float] = None, on_sleep=None, limit: float = 120) -> None:
        self.start = self.now = float(math.ceil(time.time())) if start is None else start
        self.sleeps = 0
        self.on_sleep = {} if on_sleep is None else on_sleep
        self.deadline = time.time() + limit

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.sleeps += 1
        if time.time() > self.deadline:
            raise AssertionError(f"假时钟：真实时间超过上限（sleep 了 {self.sleeps} 次，假时间 {self.elapsed():g} 秒）")
        if callable(self.on_sleep):
            self.on_sleep(self.sleeps)
        elif self.sleeps in self.on_sleep:
            self.on_sleep[self.sleeps]()

    def elapsed(self) -> float:
        return self.now - self.start

    @staticmethod
    def strftime(fmt: str, *args) -> str:
        return time.strftime(fmt, *args)

    @staticmethod
    def localtime(*args):
        return time.localtime(*args)


def patch_clock(d) -> FakeClock:
    """把加载好的 drover 模块 d 里的 time 换成假时钟。"""
    clock = FakeClock()
    d.time = clock
    return clock


def run_cli(d, *argv: str) -> tuple:
    """在进程里执行加载好的 drover 模块 d 的 main()：参数解析、usage_problem、分发、退出码都和命令行同一条路。
    返回 (退出码, stdout, stderr)；argparse 报错（SystemExit）按它的退出码返回。"""
    out, err = io.StringIO(), io.StringIO()
    saved = sys.argv
    sys.argv = ["drover", *argv]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = d.main()
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.argv = saved
    return code, out.getvalue(), err.getvalue()


def run_watch(d, *argv: str) -> tuple:
    """在进程里跑加载好的模块 d 的 drover watch，返回 (退出码, EVENT 列表)。"""
    code, out, _ = run_cli(d, "watch", *argv)
    return code, events(out)


def wait_for(predicate, timeout: float = 30, step: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


class DroverTestCase(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="drover-selftest-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        if self.tmp == REAL_STATE or REAL_STATE in self.tmp.parents:
            self.fail(f"临时目录 {self.tmp} 落在真实状态目录 {REAL_STATE} 下")
        self.home = self.tmp / "home"
        self.state = self.tmp / "state"
        self.fakebin = self.tmp / "fakebin"
        self.work = self.tmp / "work"
        for d in (self.home, self.state, self.fakebin, self.work):
            d.mkdir(parents=True)
        (self.state / "config.json").write_text(
            json.dumps({"workspace": str(self.work), "repo_dirs": [str(self.work)]}))
        gitconfig = self.tmp / "gitconfig"
        gitconfig.write_text(selftest_fakes.GITCONFIG)
        selftest_fakes.install(self.fakebin, sys.executable)
        self.set_herdr_state({"agents": {}, "worktrees": [],
                              "worktree_root": str(self.home / ".herdr" / "worktrees")})
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(DROP_ENV_PREFIXES) and k not in ("LC_ALL", "LANGUAGE", "PYTHONPATH")}
        path = [str(self.fakebin)] + [d for d in os.environ.get("PATH", "").split(os.pathsep)
                                      if d and not (Path(d) / "code-review-graph").exists()]
        env.update(HOME=str(self.home), DROVER_STATE_DIR=str(self.state), PATH=os.pathsep.join(path),
                   GIT_CONFIG_GLOBAL=str(gitconfig), GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
                   GIT_EDITOR="true", LC_MESSAGES="C", PYTHONDONTWRITEBYTECODE="1", **GIT_IDENTITY)
        # 自动更新默认关：main-register、watch --main、board 都会触发 tick，自测不能真的在后台起 update。
        # 测 tick 的用例（test_auto_update.py）自己打开，并把后台命令换成假的。
        env["DROVER_AUTO_UPDATE"] = "0"
        # heavy 放行看的整机实测 CPU 换成这个文件里写的核数（默认 0，机器空着）：自测不能跟着本机真实负载变（别的 worker
        # 在跑全量时整机常年忙 6～9 核）。测"整机满了"的用例用 set_cpu_busy 改它。
        self.cpu_file = self.tmp / "cpu-busy"
        self.cpu_file.write_text("0")
        env["DROVER_HEAVY_CPU_FILE"] = str(self.cpu_file)
        self.env = env
        self._env_entered = False

    def set_cpu_busy(self, cores: Optional[float]) -> None:
        """假的整机实测 CPU：忙 cores 核；None 是读不到（删掉文件，heavy 退回按各条的实测 / 预留）。"""
        if cores is None:
            self.cpu_file.unlink(missing_ok=True)
        else:
            self.cpu_file.write_text(repr(float(cores)))

    # ---- 假 herdr / 假工具

    def set_herdr_state(self, state: dict) -> None:
        path = self.fakebin / "herdr-state.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        os.replace(tmp, path)

    def herdr_state(self) -> dict:
        return json.loads((self.fakebin / "herdr-state.json").read_text())

    def set_agents(self, **agents: dict) -> None:
        """设置假 herdr 的 agent：名字 = {status, cwd, seq, screen | screens, grow, agent, read_fail}。读屏计数从 0 开始。"""
        state = self.herdr_state()
        state["agents"] = {name: dict(info) for name, info in agents.items()}
        self.set_herdr_state(state)

    def herdr_calls(self) -> list:
        log = self.fakebin / "herdr-calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def tool_calls(self, tool: Optional[str] = None) -> list:
        log = self.fakebin / "tool-calls.log"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [c for c in calls if tool is None or c["tool"] == tool]

    def write_spec(self, root: Path, tool: str, spec: dict) -> None:
        (Path(root) / f"fake-{tool}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1))

    def assert_fake_herdr(self) -> None:
        """PATH 里的 herdr 是假的（真 herdr 连本机 server，会动到真实 pane）。"""
        self.assertEqual(shutil.which("herdr", path=self.env["PATH"]), str(self.fakebin / "herdr"))

    # ---- 执行 bin/drover

    def drover(self, *args: str, rc: Optional[int] = 0, timeout: float = 120,
               cwd: Optional[Path] = None, input: Optional[str] = None,
               env: Optional[dict] = None, script: Optional[Path] = None) -> subprocess.CompletedProcess:
        """子进程执行 bin/drover（临时环境）。rc=None 不检查退出码；input 是喂给 stdin 的文字（hook 模式用）；env 是只给这一条
        命令加的环境变量（盖在 self.env 上）；script 给了就执行它（临时 clone / 副本里的一份 bin/drover，版本按那里算）。"""
        p = subprocess.run([sys.executable, str(script or DROVER), *args], env={**self.env, **(env or {})},
                           cwd=str(cwd or self.tmp), capture_output=True, text=True, timeout=timeout, input=input)
        if rc is not None and p.returncode != rc:
            self.fail(f"drover {' '.join(args)} 退出码 {p.returncode}（期望 {rc}）\n"
                      f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}")
        return p

    def drover_json(self, *args: str, rc: Optional[int] = 0, **kw) -> dict:
        return json.loads(self.drover(*args, rc=rc, **kw).stdout)

    def main_rollup(self, *args: str, rc: Optional[int] = 0) -> subprocess.CompletedProcess:
        """主会话跑 drover rollup（args 是 --slug / --name …）：在 main-session.json 记的 pane 里跑（HERDR_PANE_ID）。还没有
        记录就先按 Codex 主会话的样子登记 MAIN_PANE（session_id、source 是 null：watch --main 不会当成重开、报 session-restarted）。"""
        path = self.state / "main-session.json"
        pane = (json.loads(path.read_text()) if path.exists() else {}).get("pane_id")
        if not pane:
            pane = MAIN_PANE
            path.write_text(json.dumps({"session_id": None, "pane_id": pane, "source": None, "at": time.time()}))
        return self.drover("rollup", *args, rc=rc, env={"HERDR_ENV": "1", "HERDR_PANE_ID": pane})

    def drover_in_process(self, *args: str, rc: Optional[int] = 0,
                          clock: Optional[FakeClock] = None, setup=None) -> subprocess.CompletedProcess:
        """和 drover() 一样执行一条命令，但在进程里跑 main()：每次新加载一份 bin/drover（和起新进程一样，没有上一次留下的
        模块状态；状态文件照常落盘），这条命令期间 os.environ 换成 self.env、工作目录是 self.tmp，模块里的 time 换成 clock
        （默认一个新的假时钟，起点是现在）。给要等的命令用：轮询、--timeout 都按假时间，不用真等。
        setup(模块)：加载之后、执行之前调（换掉模块里的函数，比如包住起子进程的 capture_run）。"""
        saved_env, saved_cwd = dict(os.environ), os.getcwd()
        os.environ.clear()
        os.environ.update(self.env)
        os.chdir(self.tmp)
        try:
            d = self._load()
            d.time = clock or FakeClock()
            if setup:
                setup(d)
            code, out, err = run_cli(d, *args)
        finally:
            os.chdir(saved_cwd)
            os.environ.clear()
            os.environ.update(saved_env)
        if rc is not None and code != rc:
            self.fail(f"drover {' '.join(args)}（进程里）退出码 {code}（期望 {rc}）\nstdout:\n{out}\nstderr:\n{err}")
        return subprocess.CompletedProcess(["drover", *args], code, out, err)

    def watch(self, *args: str, timeout: float = 90, on_sleep=None, setup=None) -> tuple:
        """执行 drover watch，返回 (退出码, EVENT 列表, 秒数)。在进程里跑（drover_in_process）、假时钟：--interval、--timeout、
        各种阈值都是假时间，"跑满 --timeout 看有没有多报"的窗口不用真等、跑几轮是确定的；返回的秒数也是假时间。
        on_sleep 见 FakeClock（两轮之间改假 herdr 等）；timeout 是真实时间的上限；setup 见 drover_in_process。"""
        clock = FakeClock(on_sleep=on_sleep, limit=timeout)
        p = self.drover_in_process("watch", *args, rc=None, clock=clock, setup=setup)
        return p.returncode, events(p.stdout), clock.elapsed()

    def start_watch(self, *args: str) -> subprocess.Popen:
        """后台执行 drover watch 子进程（stdout 可以逐行读；真实时间），用例结束还没退出就杀掉。给要真进程的用例用
        （两个 watch 抢同一把锁这类）。"""
        p = subprocess.Popen([sys.executable, str(DROVER), "watch", *args], env=self.env, cwd=str(self.tmp),
                             stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        return p

    def watch_then(self, args: list, first_lines: int, change, timeout: float = 90) -> tuple:
        """跑 watch：开头 first_lines 行 EVENT（第一轮输出的当前状态）之后调用 change() 改假 herdr / rollup，再等它退出。
        返回 (退出码, 开头的事件, 之后的事件)。同 watch()，在进程里跑、假时钟，change() 在第一轮之后（第 1 次 sleep）执行。"""
        code, evs, _ = self.watch(*args, timeout=timeout, on_sleep={1: change})
        self.assertGreaterEqual(len(evs), first_lines, evs)
        return code, evs[:first_lines], evs[first_lines:]

    def enter_env(self) -> None:
        """把 os.environ 换成临时环境（进程内调用 git / herdr / 假工具时生效），用例结束还原。"""
        if self._env_entered:
            return
        saved = dict(os.environ)

        def restore() -> None:
            os.environ.clear()
            os.environ.update(saved)

        os.environ.clear()
        os.environ.update(self.env)
        self.addCleanup(restore)
        self._env_entered = True

    def load_drover(self):
        """在临时环境里加载 bin/drover，返回模块；STATE_HOME 不是临时状态目录就直接失败。"""
        self.enter_env()
        return self._load()

    def _load(self):
        """按当前的 os.environ 加载一份新的 bin/drover；STATE_HOME 不是临时状态目录就直接失败。"""
        global _loaded
        _loaded += 1
        mod = load_module(f"drover_selftest_{_loaded}")
        expected = Path(os.environ.get("DROVER_STATE_DIR") or Path(os.environ["HOME"]) / ".drover")
        if mod.STATE_HOME != expected.expanduser() or REAL_STATE in (mod.STATE_HOME, *mod.STATE_HOME.parents):
            self.fail(f"加载后 STATE_HOME={mod.STATE_HOME}，不是临时状态目录 {expected}")
        return mod

    # ---- git

    def git(self, repo: Path, *args: str, check: bool = True) -> str:
        p = subprocess.run(["git", "-C", str(repo), *args], env=self.env, capture_output=True, text=True,
                           timeout=120)
        if check and p.returncode != 0:
            self.fail(f"git {' '.join(args)} 失败（{repo}）：{p.stderr}")
        return p.stdout.strip()

    @staticmethod
    def write_files(root: Path, files: dict) -> None:
        for rel, text in files.items():
            path = Path(root) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def commit(self, repo: Path, message: str, files: Optional[dict] = None) -> str:
        if files:
            self.write_files(repo, files)
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def init_repo(self, path: Path, files: dict) -> str:
        """建一个仓库（分支 main）并提交 files，返回提交的 sha。"""
        path.mkdir(parents=True, exist_ok=True)
        self.git(path, "init", "-q", "-b", "main")
        return self.commit(path, "init", files)
