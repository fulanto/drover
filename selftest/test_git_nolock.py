"""drover 起的只读 git 不改写 index、不拿 index.lock（出过两次残留 0 字节 index.lock：拿着锁的 git 被 SIGKILL）。

- 真 git 的行为（ReadOnlyGitIndexTests）：已跟踪文件"内容没变、只改了修改时间"（index 里记的 stat 对不上）时，默认的
  git status、比对工作区的 git diff 会算一遍内容、刷新并写回 index，写的时候拿 index.lock。drover 的只读路径（watch 取进度、
  accept 的 status 和 diff --stat、集成时列冲突文件）经过探针（ReadOnlyGitProbe 包住模块里起子进程的 capture_run）：每条 git status /
  git diff 跑之前把这个文件的修改时间往前拨一次，跑前跑后比 index 的内容和修改时间、git 目录的修改时间（先拨回 2001 年，
  目录里建过 index.lock 就会变成现在）；再在同样的状态下跑一遍改之前的写法（不带 GIT_OPTIONAL_LOCKS=0、不带
  -c diff.autoRefreshIndex=false）：它改写了 index（对照，证明测得出来），输出和 drover 的一样。
  accept 在 add -N 之后显式刷新 index：detect-changes（假的，照真的跑默认写法的 git diff --name-status <base> --）不再改写
  index（改之前这次刷新是开头那条拿锁的 status 顺手做的）。
- 子进程环境（GitEnvTests）：git()、git_bytes()、cleanup 的 git worktree remove 起的 git 都带 GIT_OPTIONAL_LOCKS=0，
  外面的环境是 1、调用方另给 env 时也一样（PATH 前面放一个记环境的假 git）。
- 超时怎么停（GitTimeoutStopTests、StubbornChildTests）：git commit 卡在一直睡的 pre-commit hook 上（持着 index.lock），
  git() / git_bytes() 短超时 → 报命令超时、不留锁、hook 一起停；对照：原来的 subprocess.run(timeout)（SIGKILL）留锁。
  不理 SIGTERM 的假 git：宽限期（改小到 0.5 秒）后被 SIGKILL，总耗时有上界。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Optional

from selftest_support import DroverTestCase, wait_for

NO_REFRESH = ["-c", "diff.autoRefreshIndex=false"]
PAST_NS = 10 ** 18  # 2001-09-09：量之前先把 git 目录的修改时间拨到这里
COMMIT_TIMEOUT = 2  # 秒：git commit 卡在 hook 上，等这么久算超时
# 带 -a：git commit 跑 pre-commit hook 时一直拿着 index.lock（不带 -a、不给路径时 git 先写好 index、放了锁才跑 hook，git 2.51 实测）
COMMIT = ("commit", "-q", "-a", "-m", "x")
STUBBORN_TIMEOUT = 2

# pre-commit hook：记下自己的 pid 后一直睡（git commit 跑 hook 时拿着 index.lock）。
HOOK = """#!/bin/sh
echo $$ > '{pidfile}'
exec sleep 60
"""

# 收到 SIGTERM 不退的假 git：SIGTERM 只记一笔，记下 pid 后一直睡（只有 SIGKILL 停得下）。
STUBBORN_GIT = r'''#!{python}
import os
import signal
import time
from pathlib import Path

here = Path(__file__).resolve().parent
signal.signal(signal.SIGTERM, lambda *_: (here / "signals.log").open("a").write("TERM\n"))
(here / "git.pid").write_text(str(os.getpid()))
time.sleep(60)
'''

# 只记日志的假 git：参数和几个环境变量（看 drover 给 git 子进程的环境）。
FAKE_GIT = r'''#!{python}
import json
import os
import sys
from pathlib import Path

keys = ("GIT_OPTIONAL_LOCKS", "GIT_INDEX_FILE", "LC_ALL")
with (Path(__file__).resolve().parent / "git-env.log").open("a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], **{k: os.environ.get(k) for k in keys}}) + "\n")
'''

# 假 code-review-graph：每次调用记一行（子命令、环境里的 GIT_OPTIONAL_LOCKS）。detect-changes 照真的跑一条默认写法的
# git diff --name-status <base> --（真的那条见 code-review-graph incremental.py 的 get_changed_files），记下它改没改写 index、
# 列了哪些文件，再照真的格式打一段摘要。
FAKE_GRAPH = r'''#!{python}
import json
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
entry = {"cmd": args[0], "optional_locks": os.environ.get("GIT_OPTIONAL_LOCKS")}
if args[0] == "detect-changes":
    git = ["git", "-C", args[args.index("--repo") + 1]]
    gitdir = subprocess.run([*git, "rev-parse", "--absolute-git-dir"], capture_output=True, text=True).stdout.strip()
    index = Path(gitdir, "index")
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    names = subprocess.run([*git, "diff", "--name-status", args[args.index("--base") + 1], "--"],
                           capture_output=True, text=True).stdout.splitlines()
    entry.update(rewrote=(index.read_bytes(), index.stat().st_mtime_ns) != before, names=names)
    print("Analyzed 1 changed file\nOverall risk score: 0.10\nUntested: a")
with (Path(__file__).resolve().parent / "graph-calls.log").open("a") as f:
    f.write(json.dumps(entry) + "\n")
'''


def git_sub(cmd: list) -> str:
    """git 命令行的子命令：跳过 -C <目录>、-c <配置>。"""
    i = 1
    while i < len(cmd) and cmd[i] in ("-C", "-c"):
        i += 2
    return cmd[i] if i < len(cmd) else ""


def touch_back(path: Path) -> None:
    """内容不动，修改时间往前拨 1 秒：index 里记的 stat 对不上了。"""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - 10 ** 9))


def git_dir_state(gitdir: Path, mark: bool = False) -> tuple:
    """(index 的内容, index 的修改时间, git 目录的修改时间)。mark：先把 git 目录的修改时间拨回 2001 年，之后目录里建 / 删 /
    改名任何文件（index.lock 出现过、改名成 index）都会把它变成现在。"""
    if mark:
        os.utime(gitdir, ns=(PAST_NS, PAST_NS))
    index = gitdir / "index"
    return index.read_bytes(), index.stat().st_mtime_ns, gitdir.stat().st_mtime_ns


def old_style(cmd: list) -> list:
    """改之前的写法：去掉 -c diff.autoRefreshIndex=false。"""
    for i in range(len(cmd) - 1):
        if cmd[i:i + 2] == NO_REFRESH:
            return cmd[:i] + cmd[i + 2:]
    return list(cmd)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ReadOnlyGitProbe:
    """包住加载好的 drover 模块里起子进程的 capture_run（install），只管 git status / git diff，别的照原样执行。
    每条记一行 calls：参数（git -C <目录> 之后的）、环境里的 GIT_OPTIONAL_LOCKS、index 和 git 目录前后有没有变、
    跑完有没有留下 index.lock、改之前的写法改没改写 index、两种写法的输出（退出码、stdout）是不是一样。"""

    def __init__(self, gitdir: Path, stale: Path) -> None:
        self.gitdir, self.stale = gitdir, stale
        self.real_run = None
        self.calls: list = []

    def install(self, d) -> None:
        self.real_run, d.capture_run = d.capture_run, self

    def __call__(self, cmd: list, **kw):
        if not (cmd[:1] == ["git"] and git_sub(cmd) in ("status", "diff")):
            return self.real_run(cmd, **kw)
        env = kw.get("env") or dict(os.environ)
        touch_back(self.stale)
        before = git_dir_state(self.gitdir, mark=True)
        p = self.real_run(cmd, **kw)
        after = git_dir_state(self.gitdir)
        lock_left = (self.gitdir / "index.lock").exists()
        touch_back(self.stale)
        old_before = git_dir_state(self.gitdir, mark=True)
        old = self.real_run(old_style(cmd), **{**kw, "env": {k: v for k, v in env.items() if k != "GIT_OPTIONAL_LOCKS"}})
        old_after = git_dir_state(self.gitdir)
        self.calls.append({"args": cmd[3:], "optional_locks": env.get("GIT_OPTIONAL_LOCKS"),
                           "index_kept": before[:2] == after[:2], "dir_kept": before[2] == after[2],
                           "lock_left": lock_left, "old_rewrote": old_before[:2] != old_after[:2],
                           "same_output": (p.returncode, p.stdout) == (old.returncode, old.stdout)})
        return p


class ReadOnlyGitIndexTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.work / "app"
        self.base = self.init_repo(self.repo, {"stale.txt": "same\n", "edit.txt": "1\n", "src/m.py": "x = 1\n"})
        self.write_files(self.repo, {"edit.txt": "2\n", "new.txt": "new\n"})

    def gitdir(self, repo: Path) -> Path:
        return Path(self.git(repo, "rev-parse", "--absolute-git-dir"))

    def assert_read_only(self, probe: ReadOnlyGitProbe, expected: list) -> None:
        """expected：应该经过探针的 git（git -C <目录> 之后的参数），按顺序。每条都要：环境里有 GIT_OPTIONAL_LOCKS=0、
        index 没被改写、git 目录里没建过文件（index.lock 没出现过）、没留下锁、输出和改之前的写法一样；改之前的写法在同样的
        状态下改写了 index（对照）。"""
        for c in probe.calls:
            self.assertEqual(c, {**c, "optional_locks": "0", "index_kept": True, "dir_kept": True, "lock_left": False,
                                 "old_rewrote": True, "same_output": True})
        self.assertEqual([c["args"] for c in probe.calls], expected)

    def test_control_plain_status_rewrites_index(self) -> None:
        """对照：不带变量的原始 git status，stat 对不上时改写 index、git 目录里建过文件（index.lock）"""
        gitdir = self.gitdir(self.repo)
        touch_back(self.repo / "stale.txt")
        before = git_dir_state(gitdir, mark=True)
        self.git(self.repo, "status", "--porcelain")
        after = git_dir_state(gitdir)
        self.assertNotEqual(before[:2], after[:2])
        self.assertNotEqual(before[2], after[2])

    def test_watch_fingerprint(self) -> None:
        """watch 取进度（worktree_fingerprint）的 git diff HEAD、git status --porcelain --untracked-files=all"""
        d = self.load_drover()
        probe = ReadOnlyGitProbe(self.gitdir(self.repo), self.repo / "stale.txt")
        probe.install(d)
        fp = d.worktree_fingerprint(str(self.repo))
        self.assert_read_only(probe, [[*NO_REFRESH, "diff", "HEAD"], ["status", "--porcelain", "--untracked-files=all"]])
        self.assertTrue(fp)
        self.assertEqual(d.worktree_fingerprint(str(self.repo)), fp)

    def accept(self, setup=None) -> dict:
        """在进程里跑 accept（PATH 前面放假 code-review-graph），返回结果；self.graph_log 是假 code-review-graph 的调用记录。
        setup 见 drover_in_process。"""
        if shutil.which("code-review-graph", path=self.env["PATH"]):
            self.skipTest("临时 PATH 里还能找到 code-review-graph，跑 accept 会真的建图")
        bindir = self.tmp / "graphbin"
        bindir.mkdir()
        (bindir / "code-review-graph").write_text(FAKE_GRAPH.replace("{python}", sys.executable, 1))
        (bindir / "code-review-graph").chmod(0o755)
        self.graph_log = bindir / "graph-calls.log"
        self.env["PATH"] = str(bindir) + os.pathsep + self.env["PATH"]
        saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in saved.items()])  # accept 在进程里装了信号处理
        p = self.drover_in_process("accept", "--wt", str(self.repo), "--base", self.base, "--no-lock", setup=setup)
        return json.loads(p.stdout)

    def detect_changes_calls(self) -> list:
        calls = [json.loads(line) for line in self.graph_log.read_text().splitlines()]
        return [c for c in calls if c["cmd"] == "detect-changes"]

    def test_accept(self) -> None:
        """accept 开头的 git status --porcelain、git diff <base> --stat、算改动行的 git diff -U0 <base>（复杂度检查用；
        中间的 git add -N .、update-index --refresh 本来就要写 index，不在此列）；detect-changes 也带 GIT_OPTIONAL_LOCKS=0
        （它改动为空时退回 git status）。accept 开头、结尾各取一次改动指纹（worktree_fingerprint 的 git diff HEAD、
        git status --porcelain --untracked-files=all），都是只读"""
        probe = ReadOnlyGitProbe(self.gitdir(self.repo), self.repo / "stale.txt")
        res = self.accept(setup=probe.install)
        fingerprint = [[*NO_REFRESH, "diff", "HEAD"], ["status", "--porcelain", "--untracked-files=all"]]
        self.assert_read_only(probe, [*fingerprint, ["status", "--porcelain"], [*NO_REFRESH, "diff", self.base, "--stat"],
                                      [*NO_REFRESH, "-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff",
                                       "-U0", self.base], *fingerprint])
        self.assertEqual(res["status_porcelain"], [" M edit.txt", "?? new.txt"])
        self.assertEqual(res["intent_to_add"], ["new.txt"])
        self.assertIn("edit.txt", res["diff_stat"])
        self.assertNotIn("stale.txt", res["diff_stat"])
        self.assertTrue(res["graph"]["ok"], res["graph"])
        self.assertEqual([c["optional_locks"] for c in self.detect_changes_calls()], ["0"])

    def test_accept_refreshes_index_before_detect_changes(self) -> None:
        """accept 在 add -N 之后显式刷新 index（update-index -q --refresh）：只改了时间的文件到 detect-changes（假的，照真的跑
        默认写法的 git diff --name-status <base> --）时已经刷新过，它不改写 index、输出里没有这个文件。
        对照：accept 之后再把这个文件改一次时间，同一条 diff 就改写 index 了"""
        touch_back(self.repo / "stale.txt")
        res = self.accept()
        self.assertNotIn("index_refresh_error", res)
        self.assertEqual(self.detect_changes_calls(),
                         [{"cmd": "detect-changes", "optional_locks": "0", "rewrote": False,
                           "names": ["M\tedit.txt", "A\tnew.txt"]}])
        gitdir = self.gitdir(self.repo)
        touch_back(self.repo / "stale.txt")
        before = git_dir_state(gitdir, mark=True)
        names = self.git(self.repo, "diff", "--name-status", self.base, "--").splitlines()
        after = git_dir_state(gitdir)
        self.assertNotEqual(before[:2], after[:2])
        self.assertEqual(names, ["M\tedit.txt", "A\tnew.txt"])

    def test_conflicted_files(self) -> None:
        """集成时列冲突文件（git diff --name-only --diff-filter=U）：cherry-pick 停在冲突上、另一个文件 stat 对不上 →
        只列冲突的文件，和改之前一样（--name-only 不受 diff.autoRefreshIndex 影响的前提是 --diff-filter=U）"""
        repo = self.work / "c"
        self.init_repo(repo, {"stale.txt": "same\n", "b.txt": "base\n"})
        self.git(repo, "checkout", "-q", "-b", "side")
        sha = self.commit(repo, "side", {"b.txt": "side\n"})
        self.git(repo, "checkout", "-q", "main")
        self.commit(repo, "main", {"b.txt": "main\n"})
        self.git(repo, "cherry-pick", sha, check=False)
        d = self.load_drover()
        probe = ReadOnlyGitProbe(self.gitdir(repo), repo / "stale.txt")
        probe.install(d)
        files = d.conflicted_files(str(repo))
        self.assertEqual(files, ["b.txt"])
        self.assert_read_only(probe, [[*NO_REFRESH, "diff", "--name-only", "--diff-filter=U"]])


class GitEnvTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        bindir = self.tmp / "gitbin"
        bindir.mkdir()
        (bindir / "git").write_text(FAKE_GIT.replace("{python}", sys.executable, 1))
        (bindir / "git").chmod(0o755)
        self.log = bindir / "git-env.log"
        self.env["PATH"] = str(bindir) + os.pathsep + self.env["PATH"]
        self.env["GIT_OPTIONAL_LOCKS"] = "1"  # 外面的环境要拿可选锁：drover 起的 git 照样是 0
        self.d = self.load_drover()
        self.repo = str(self.work)

    def logged(self) -> list:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def entry(self, *argv: str, index: Optional[str] = None, lc_all: Optional[str] = None) -> dict:
        return {"argv": ["-C", self.repo, *argv], "GIT_OPTIONAL_LOCKS": "0", "GIT_INDEX_FILE": index, "LC_ALL": lc_all}

    def test_git_and_git_bytes(self) -> None:
        """git()、git_bytes()：都带 GIT_OPTIONAL_LOCKS=0；调用方另给的 env（临时 index、C locale）照样生效"""
        d, idx = self.d, str(self.tmp / "x.index")
        d.git(self.repo, "status", "--porcelain")
        d.git(self.repo, "add", "-A", env={"GIT_INDEX_FILE": idx})
        d.git(self.repo, "fetch", "origin", env=d.c_locale_env())
        d.git_bytes(self.repo, "cat-file", "blob", "HEAD:a")
        d.git_bytes(self.repo, "diff", "--cached", env={"GIT_INDEX_FILE": idx})
        self.assertEqual(self.logged(), [
            self.entry("status", "--porcelain"),
            self.entry("add", "-A", index=idx),
            self.entry("fetch", "origin", lc_all="C"),
            self.entry("cat-file", "blob", "HEAD:a"),
            self.entry("diff", "--cached", index=idx),
        ])

    def test_cleanup_worktree_remove(self) -> None:
        """cleanup 删 worktree（workspace 没开，直接 git worktree remove）也走 git()：带 GIT_OPTIONAL_LOCKS=0，命令和以前一样"""
        wt = str(self.tmp / "wt")
        self.assertIsNone(self.d.cleanup_remove(self.repo, wt, None, True))
        self.assertEqual(self.d.cleanup_remove_cmd(self.repo, wt, None, True),
                         ("git", ["-C", self.repo, "worktree", "remove", "--force", wt]))
        self.assertEqual(self.logged(), [self.entry("worktree", "remove", "--force", wt)])


class GitTimeoutStopTests(DroverTestCase):
    """drover 起的 git 超时：先给进程树发 SIGTERM（git 在信号处理里删掉自己拿着的 index.lock），等 HEAVY_KILL_GRACE 秒还在
    才 SIGKILL。git commit 跑 pre-commit hook 时拿着 index.lock：hook 记下自己的 pid 后一直睡，commit 就卡在持锁的状态。"""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.work / "r"
        self.init_repo(self.repo, {"a.txt": "1\n"})
        self.pidfile = self.tmp / "hook.pid"
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_text(HOOK.replace("{pidfile}", str(self.pidfile)))
        hook.chmod(0o755)
        self.write_files(self.repo, {"a.txt": "2\n"})
        self.lock = self.repo / ".git" / "index.lock"
        self.addCleanup(self.kill_hook)

    def kill_hook(self) -> None:
        """收掉 hook 起的 sleep（对照组里 git 被 SIGKILL，它成了孤儿接着睡）。"""
        with contextlib.suppress(OSError, ValueError):
            os.kill(int(self.pidfile.read_text()), signal.SIGKILL)

    def hook_pid(self) -> int:
        self.assertTrue(self.pidfile.is_file(), "超时之前 pre-commit hook 没跑起来（git commit 还没拿锁）")
        return int(self.pidfile.read_text())

    def test_control_sigkill_leaves_lock(self) -> None:
        """对照：原来的写法 subprocess.run(timeout)，超时直接 SIGKILL git → 跑 hook 时拿着的 index.lock 留下了"""
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run(["git", "-C", str(self.repo), *COMMIT], capture_output=True, timeout=COMMIT_TIMEOUT,
                           env=self.env)
        self.hook_pid()
        self.assertTrue(self.lock.exists())

    def assert_stopped_cleanly(self, call) -> None:
        """call(模块) 跑 git commit 超时：报命令超时、没留 index.lock、hook 起的 sleep 也停了，之后照常能提交。"""
        d = self.load_drover()
        with self.assertRaises(d.DroverError) as ctx:
            call(d)
        self.assertIn(f"命令超时（{COMMIT_TIMEOUT} 秒）", ctx.exception.message)
        pid = self.hook_pid()
        self.assertFalse(self.lock.exists())
        self.assertTrue(wait_for(lambda: not alive(pid), timeout=10), "hook 起的 sleep 还在")
        self.git(self.repo, "commit", "-q", "-a", "--no-verify", "-m", "x")

    def test_git_timeout_leaves_no_lock(self) -> None:
        """git()（经 run）超时：SIGTERM 进程树 → git 删锁退出、hook 一起停"""
        self.assert_stopped_cleanly(lambda d: d.git(str(self.repo), *COMMIT, timeout=COMMIT_TIMEOUT))

    def test_git_bytes_timeout_leaves_no_lock(self) -> None:
        """git_bytes() 超时：同上"""
        self.assert_stopped_cleanly(lambda d: d.git_bytes(str(self.repo), *COMMIT, timeout=COMMIT_TIMEOUT))


class StubbornChildTests(DroverTestCase):
    def test_ignoring_sigterm_killed_after_grace(self) -> None:
        """收到 SIGTERM 不退的子进程（假 git，SIGTERM 只记一笔）：超时后先收到 SIGTERM，宽限期（这里改成 0.5 秒）过了
        被 SIGKILL；报命令超时，总耗时在 超时 + 宽限期 到再加 3 秒之间，不会挂住，进程没了"""
        bindir = self.tmp / "gitbin"
        bindir.mkdir()
        (bindir / "git").write_text(STUBBORN_GIT.replace("{python}", sys.executable, 1))
        (bindir / "git").chmod(0o755)
        self.env["PATH"] = str(bindir) + os.pathsep + self.env["PATH"]
        d = self.load_drover()
        d.HEAVY_KILL_GRACE = 0.5
        started = time.monotonic()
        with self.assertRaises(d.DroverError) as ctx:
            d.git(str(self.work), "status", timeout=STUBBORN_TIMEOUT)
        secs = time.monotonic() - started
        self.assertIn(f"命令超时（{STUBBORN_TIMEOUT} 秒）", ctx.exception.message)
        self.assertEqual((bindir / "signals.log").read_text(), "TERM\n")
        self.assertGreaterEqual(secs, STUBBORN_TIMEOUT + 0.5)
        self.assertLess(secs, STUBBORN_TIMEOUT + 0.5 + 3)
        self.assertFalse(alive(int((bindir / "git.pid").read_text())))


if __name__ == "__main__":
    unittest.main(verbosity=2)
