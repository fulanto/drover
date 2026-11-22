"""board --live 自己换代码、board --tab 重启旧看板。

起因：drover 自动更新以后，看板 tab 里正在跑的 board --live 第一行显示新的版本号（每轮现读磁盘），
画的却还是启动时加载的旧代码的表；关掉 tab 重跑 board --tab 才是新的。现在：
- --live 启动时记下这份代码的版本，每轮和磁盘上的比，两边都取得到、又不一样就试跑新代码（board --help）、恢复终端、os.execv
  重新执行自己（进程号不变）；没换成在看板上留一行原因，接着用旧代码，下一轮再试。ReexecPtyTests 在伪终端里跑临时 clone /
  副本里的 bin/drover，提交 / 重装一份新代码（看板第一行改成"drover 新看板"），看同一个进程换过去；ReexecLogicTests 用假的
  版本来源。
- copy 模式的版本取副本标记里的 commit（install.py 复制时记的），不取 clone 的 HEAD：update 先快进 clone、再重装副本，中间
  换过去拿到的还是旧副本。
- 看板 tab 里的 --live 每轮写心跳 board-live.json；board --tab 复用 tab 时没有心跳、不是这个 pane 的、版本不对、心跳太旧（假时钟）
  就 herdr pane send-keys ctrl+c、pane run 重启（TabRestartTests）。
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from selftest_support import DROVER, FakeClock, run_cli
from test_board import LIVE_CMD, MOUSE_OFF, MOUSE_ON, PtyTestCase, TabTestCase

OLD_HEAD = 'lines = [f"drover 看板 · {board_head(view, now)}" + ('
NEW_HEAD = 'lines = [f"drover 新看板 · {board_head(view, now)}" + ('
FIRST_FRAME = "进行中（1）".encode()
NEW_BOARD = "drover 新看板".encode()
STUCK = "drover 看板没换成新代码"
CLEAR = b"\x1b[H\x1b[2J"


def new_code() -> str:
    """本 worktree 的 bin/drover，看板第一行改成"drover 新看板"：看得出画表的是哪份代码。"""
    text = DROVER.read_text()
    assert text.count(OLD_HEAD) == 1
    return text.replace(OLD_HEAD, NEW_HEAD)


def broken_code() -> str:
    """有语法错误的 bin/drover（加载就报 SyntaxError）。"""
    text = DROVER.read_text()
    marker = "\nfrom __future__ import annotations\n"
    assert text.count(marker) == 1
    return text.replace(marker, marker + "def broken(:\n")


def read_until(master: int, out: bytes, done, timeout: float = 30) -> bytes:
    """边读伪终端边等 done(累计输出) 成立，返回累计输出（含传进来的 out）；等不到也返回，由调用方断言。"""
    deadline = time.time() + timeout
    while not done(out) and time.time() < deadline:
        if select.select([master], [], [], 0.2)[0]:
            out += os.read(master, 65536)
    return out


def last_frame(out: bytes) -> list:
    """输出里最后一屏画完了的（后面又清过屏的）那一屏，一行一项（伪终端把 \\n 输出成 \\r\\n）。最新的一屏可能只读到一半，
    所以等的时候要等到想看的东西出现两次。"""
    return out.split(CLEAR)[-2].decode(errors="replace").replace("\r\n", "\n").split("\n")


class ReexecPtyTests(PtyTestCase):
    """伪终端里跑临时目录里的一份 bin/drover（看板 tab 里：开鼠标、写心跳），--interval 0.3 秒一轮；PKG_ROOT 是那个临时目录，
    drover_version 读它，不碰本 worktree 和真实安装。"""

    def make_clone(self) -> Path:
        """link 模式：临时 clone，bin/drover 是本 worktree 的，提交一次、打附注 tag v0.1.0（之后每提交一次版本号就是 v0.1.0+N）。
        返回 clone。"""
        clone = self.tmp / "clone"
        self.init_repo(clone, {"bin/drover": DROVER.read_text()})
        self.git(clone, "tag", "-a", "v0.1.0", "-m", "release v0.1.0")
        return clone

    def install_copy(self, clone: Path) -> Path:
        """copy 模式：同 install.py 的 copy_package——clone 复制到旁边的临时目录（不带 .git），写 .drover-copy（source、
        commit：clone 现在的 HEAD），再删掉旧副本、换上去。返回副本目录。"""
        pkg, tmp = self.tmp / "pkg", self.tmp / ".pkg.tmp"
        shutil.copytree(clone, tmp, ignore=shutil.ignore_patterns(".git"))
        marker = {"source": str(clone), "commit": self.git(clone, "rev-parse", "HEAD"), "copied_at": "2026-11-19T07:40:00"}
        (tmp / ".drover-copy").write_text(json.dumps(marker))
        if pkg.exists():
            shutil.rmtree(pkg)
        os.rename(tmp, pkg)
        return pkg

    def beat(self) -> dict:
        return json.loads((self.state / "board-live.json").read_text())

    def stop(self, p, master: int, slave: int, before: list) -> bytes:
        """Ctrl-C：退出码 0，最后关掉鼠标上报，终端属性恢复成启动前的。"""
        p.send_signal(signal.SIGINT)
        out = self.read_until_exit(p, master)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(out.endswith(MOUSE_OFF), out[-200:])
        self.assert_restored(slave, before)
        return out

    def test_new_commit_reexecs_same_process(self) -> None:
        """link 模式：版本没变时跑几轮都不重新执行（只开过一次鼠标、没关过），心跳是这个进程、这个 pane、启动时的版本，第一行版本号
        是 v0.1.0；clone 里提交一版新代码 → 同一个进程（pid 不变、没退出）换成新代码：先关鼠标上报、恢复终端，再重新开鼠标，之后画的
        是"drover 新看板"、第一行版本号是 v0.1.0+1，心跳的版本跟着变（存的照旧是完整 sha）；Ctrl-C 退出后终端属性和启动前一样
        （重新执行之前恢复过：新进程开鼠标前记下的是原来的属性）"""
        clone = self.make_clone()
        old = self.git(clone, "rev-parse", "HEAD")
        p, master, slave, before = self.start_pty("w1:p2", "--interval", "0.3", script=clone / "bin" / "drover")
        out = read_until(master, b"", lambda o: FIRST_FRAME in o)
        out = read_until(master, out, lambda o: o.count(FIRST_FRAME) >= 4)  # 又跑了三轮
        self.assertGreaterEqual(out.count(FIRST_FRAME), 4)
        self.assertRegex(last_frame(out)[0], r"^drover 看板 · .* · drover v0\.1\.0$")
        self.assertEqual((out.count(MOUSE_ON), out.count(MOUSE_OFF)), (1, 0))
        self.assertEqual((self.beat()["pid"], self.beat()["pane_id"], self.beat()["version"]), (p.pid, "w1:p2", old))
        new = self.commit(clone, "看板第一行改成新看板", {"bin/drover": new_code()})
        out = read_until(master, out, lambda o: o.count(NEW_BOARD) >= 2)
        self.assertIsNone(p.poll())
        first_on, off = out.index(MOUSE_ON), out.index(MOUSE_OFF)
        second_on = out.index(MOUSE_ON, off)
        self.assertLess(first_on, off)
        self.assertLess(second_on, out.index(NEW_BOARD))
        self.assertNotIn(NEW_BOARD, out[:second_on])
        self.assertRegex(last_frame(out)[0], r"^drover 新看板 · .* · drover v0\.1\.0\+1$")
        self.assertEqual((self.beat()["pid"], self.beat()["version"]), (p.pid, new))
        self.stop(p, master, slave, before)

    def test_broken_new_code_keeps_board_and_retries(self) -> None:
        """新提交的代码加载就报 SyntaxError → 不重新执行（没关鼠标、进程还是那个），看板接着用旧代码画（第一行还是"drover 看板"），
        多一行"drover 看板没换成新代码 <新>：新代码跑不起来（退出码 1：SyntaxError: …）；接着用 <旧>，下一轮再试"（两头是版本号：
        v0.1.0+1、v0.1.0）；再提交一版能跑的 → 下一轮就换过去（同一个进程），那一行没了"""
        clone = self.make_clone()
        old = self.git(clone, "rev-parse", "HEAD")
        p, master, slave, before = self.start_pty("w1:p2", "--interval", "0.3", script=clone / "bin" / "drover", cols=200)
        out = read_until(master, b"", lambda o: FIRST_FRAME in o)
        self.commit(clone, "坏代码", {"bin/drover": broken_code()})
        out = read_until(master, out, lambda o: o.count(STUCK.encode()) >= 2)  # 下一轮再试：又显示了一次
        frame = last_frame(out)
        self.assertTrue(frame[0].startswith("drover 看板 · "), frame[0])
        stuck = next(x for x in frame if x.startswith(STUCK))
        self.assertRegex(stuck, rf"^{STUCK} v0\.1\.0\+1：新代码跑不起来（退出码 1：SyntaxError: .+）；接着用 v0\.1\.0，下一轮再试$")
        self.assertIsNone(p.poll())
        self.assertNotIn(MOUSE_OFF, out)
        self.assertEqual(self.beat()["version"], old)
        self.commit(clone, "修好了", {"bin/drover": new_code()})
        out = read_until(master, out, lambda o: o.count(NEW_BOARD) >= 2)
        self.assertIsNone(p.poll())
        frame = last_frame(out)
        self.assertTrue(frame[0].startswith("drover 新看板 · "), frame)
        self.assertFalse(any(STUCK in x for x in frame), frame)
        self.stop(p, master, slave, before)

    def test_copy_mode_waits_for_new_copy(self) -> None:
        """copy 模式（副本里有 .drover-copy，版本取它记的 commit）：update 先快进了 clone、副本还没重装 → 不换（换了拿到的还是旧
        副本）；副本重装好（标记里是新 commit）→ 同一个进程换成副本里的新代码。副本没有 .git，第一行的版本号在标记的 source（clone）
        里算：v0.1.0+1"""
        clone = self.make_clone()
        pkg = self.install_copy(clone)
        old = self.git(clone, "rev-parse", "HEAD")
        p, master, slave, before = self.start_pty("w1:p2", "--interval", "0.3", script=pkg / "bin" / "drover")
        out = read_until(master, b"", lambda o: FIRST_FRAME in o)
        new = self.commit(clone, "看板第一行改成新看板", {"bin/drover": new_code()})
        seen = out.count(FIRST_FRAME)
        out = read_until(master, out, lambda o: o.count(FIRST_FRAME) >= seen + 3)  # clone 快进以后又跑了三轮
        self.assertEqual((out.count(MOUSE_OFF), self.beat()["version"]), (0, old))
        self.assertTrue(last_frame(out)[0].startswith("drover 看板 · "))
        self.install_copy(clone)
        out = read_until(master, out, lambda o: o.count(NEW_BOARD) >= 2)
        self.assertIsNone(p.poll())
        self.assertRegex(last_frame(out)[0], r"^drover 新看板 · .* · drover v0\.1\.0\+1$")
        self.assertEqual((self.beat()["pid"], self.beat()["version"]), (p.pid, new))
        self.stop(p, master, slave, before)


class ReexecLogicTests(TabTestCase):
    """进程里跑 --live（不在看板 tab 里、stdout 不是终端：不开鼠标、不写心跳），假的版本来源和假时钟。"""

    def live_rounds(self, versions: list) -> tuple:
        """drover_version 在第 k 轮返回 versions[k]（第 0 个也是启动时记下的），跑 len(versions) 轮；board_reexec 换成假的：
        记下调用、返回一句原因。返回 (board_reexec 被调的轮次, 每一屏的行)。"""
        d = self.load_drover()
        rounds = {"n": 0}
        d.drover_version = lambda: versions[rounds["n"]]
        d.time = FakeClock(on_sleep=lambda n: rounds.update(n=n))  # 一轮画完等 --interval 秒：sleep 一次进下一轮
        calls = []
        d.board_reexec = lambda saved: calls.append((rounds["n"], saved)) or "新代码跑不起来（假的）"
        with mock.patch.dict(os.environ, {"COLUMNS": "200"}):  # 不是终端时按 80 列截断，原因那一行会被截掉
            code, out, err = run_cli(d, "board", "--live", "--iterations", str(len(versions)), "--interval", "15")
        self.assertEqual(code, 0, err)
        frames = [f.split("\n") for f in out.split("\x1b[H\x1b[2J")[1:]]
        self.assertEqual(len(frames), len(versions))
        return [n for n, _ in calls], frames

    def test_reexec_only_when_both_known_and_different(self) -> None:
        """版本一直没变、启动时取不到（之后取到了也不算）、后来取不到 → 都不换；变了 → 每一轮都去换（没换成下一轮再试），看板上
        留一行原因；又变回启动时的版本 → 不再换，那一行没了"""
        a, b = "a" * 40, "b" * 40
        for versions in ([a, a, a], [None, b, b], [a, None, None]):
            with self.subTest(versions=[v and v[0] for v in versions]):
                calls, frames = self.live_rounds(versions)
                self.assertEqual(calls, [])
                self.assertFalse(any(STUCK in x for f in frames for x in f))
        calls, frames = self.live_rounds([a, b, b, a])
        self.assertEqual(calls, [1, 2])
        want = f"{STUCK} bbbbbbb：新代码跑不起来（假的）；接着用 aaaaaaa，下一轮再试"
        self.assertEqual([want in f for f in frames], [False, True, True, False])
        self.assertEqual(frames[1][1], want)  # 紧接在第一行下面

    def test_exec_failure_keeps_old_code(self) -> None:
        """真的 board_reexec：新代码试跑过了（本 worktree 的 bin/drover board --help），os.execv 报错 → 返回原因；开着鼠标时先
        关鼠标恢复终端、再重新打开。exec 的命令：同一个解释器、原来的参数。试跑没过（脚本不在了）→ 不 exec、终端不动"""
        d = self.load_drover()
        events = []
        d.mouse_off = lambda saved: events.append(("off", saved))
        d.mouse_on = lambda: events.append("on")
        argv = [str(DROVER), "board", "--live"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(os, "execv", side_effect=OSError(8, "Exec format error")) as execv:
            self.assertEqual(d.board_reexec(None), "os.execv 失败（[Errno 8] Exec format error）")
            self.assertEqual(events, [])
            self.assertEqual(d.board_reexec(["saved"]), "os.execv 失败（[Errno 8] Exec format error）")
        self.assertEqual(events, [("off", ["saved"]), "on"])
        self.assertEqual(execv.call_args_list, [mock.call(sys.executable, [sys.executable, *argv])] * 2)
        with mock.patch.object(sys, "argv", [str(self.tmp / "missing"), "board", "--live"]), \
                mock.patch.object(os, "execv") as execv:
            self.assertRegex(d.board_reexec(["saved"]), r"^新代码跑不起来（退出码 2：.*No such file or directory）$")
        execv.assert_not_called()
        self.assertEqual(len(events), 2)  # 试跑没过：终端没动


T0 = 1_800_000_000.0  # 心跳写的时间（假时钟的起点按它拨）
NEW, OLD = "b" * 40, "a" * 40
RESTART_CALLS = [["pane", "send-keys", "w1:p2", "ctrl+c"], ["pane", "run", "w1:p2", LIVE_CMD]]


class TabRestartTests(TabTestCase):
    """board --tab 复用看板 tab（w1:t2 / pane w1:p2，见 setup_tab）时按 --live 的心跳认旧看板。进程里跑，drover_version 换成假的
    （装着的是 NEW），时间是假时钟。"""

    def tab(self, *args: str, now: float, version=NEW, rc: int = 0, root: Path = None) -> dict:
        """root 给了就把这份 drover 当成装在 root 这个仓库里（版本号在那里算）。"""
        d = self.load_drover()
        d.drover_version = lambda: version
        if root:
            d.PKG_ROOT = root
        d.time = FakeClock(start=now)
        code, out, err = run_cli(d, "board", "--tab", *args)
        self.assertEqual(code, rc, out + err)
        return json.loads(out)

    def beat(self, **changes) -> None:
        """写心跳：pid 4242、pane w1:p2、版本 NEW、T0 写的，changes 改其中几项。"""
        beat = {"pid": 4242, "pane_id": "w1:p2", "version": NEW, "at": T0, **changes}
        (self.state / "board-live.json").write_text(json.dumps(beat))

    def restart_calls(self) -> list:
        return [c for c in self.herdr_calls() if c[:2] in (["pane", "send-keys"], ["pane", "run"])]

    def test_fresh_heartbeat_not_restarted(self) -> None:
        """心跳是这个 pane 的、版本和装着的一样、刚好 120 秒前写的 → 复用、不重启（restarted: false，没有 send-keys / pane run）；
        装着的版本取不到、心跳里没写版本 → 比不了，也不重启"""
        self.setup_tab()
        self.beat()
        for now, version, changes in ((T0 + 120, NEW, {}), (T0 + 5, None, {}), (T0 + 5, NEW, {"version": None})):
            with self.subTest(age=now - T0, version=version, changes=changes):
                self.beat(**changes)
                out = self.tab(now=now, version=version)
                self.assertEqual((out["reused"], out["restarted"], "restart_reason" in out), (True, False, False))
        self.assertEqual(self.restart_calls(), [])
        self.assertEqual(self.herdr_state().get("pane_runs"), None)

    def test_old_board_restarted(self) -> None:
        """没有心跳（e8fc2c4 及更早的 --live 不写）、心跳是别的 pane 的、版本和装着的不一样、121 秒没更新 → 先 herdr pane send-keys
        <pane> ctrl+c 再 pane run 看板命令，返回 reused、restarted: true 和原因"""
        self.setup_tab()
        cases = ((None, T0 + 5, "没有心跳：里面是不会自己换代码的旧版看板，或者看板没在跑"),
                 ({"pane_id": "w1:p9"}, T0 + 5, "心跳是 pane w1:p9 的，不是看板 tab 的 pane w1:p2"),
                 ({"version": OLD}, T0 + 5, "看板跑的是 aaaaaaa，装着的是 bbbbbbb"),
                 ({}, T0 + 121, "心跳 2 分钟没更新（超过 120 秒）：看板停了或卡住了"))
        for changes, now, reason in cases:
            with self.subTest(reason=reason):
                (self.state / "board-live.json").unlink(missing_ok=True)
                if changes is not None:
                    self.beat(**changes)
                before = len(self.restart_calls())
                out = self.tab(now=now)
                self.assertEqual((out["reused"], out["tab_id"], out["pane_id"], out["restarted"], out["restart_reason"]),
                                 (True, "w1:t2", "w1:p2", True, reason))
                self.assertEqual(self.restart_calls()[before:], RESTART_CALLS)
        self.assertEqual(self.calls("tab", "create"), [])
        self.assertEqual(self.herdr_state()["pane_keys"], [["w1:p2", "ctrl+c"]] * 4)

    def test_version_labels_in_reason(self) -> None:
        """版本不一样的原因写版本号（在这份 drover 所在的仓库里算；上面不在仓库里的假 sha 退回 7 位 sha）：v0.1.0 和 v0.1.0+1；
        两边版本号一样、提交却不同（同一个 tag 之后提交数相同：amend、换了分支）时各自带上 7 位 sha，看得出是两个版本"""
        self.setup_tab()
        repo = self.tmp / "repo"
        c1 = self.init_repo(repo, {"a.txt": "1\n"})
        self.git(repo, "tag", "-a", "v0.1.0", "-m", "release v0.1.0")
        c2 = self.commit(repo, "main 上的提交", {"a.txt": "2\n"})
        self.git(repo, "switch", "-q", "-c", "other", c1)
        o2 = self.commit(repo, "别的分支上的提交", {"a.txt": "other\n"})
        for running, reason in ((c1, "看板跑的是 v0.1.0，装着的是 v0.1.0+1"),
                                (o2, f"看板跑的是 v0.1.0+1（{o2[:7]}），装着的是 v0.1.0+1（{c2[:7]}）")):
            with self.subTest(reason=reason):
                self.beat(version=running)
                out = self.tab(now=T0 + 5, version=c2, root=repo)
                self.assertEqual((out["restarted"], out["restart_reason"]), (True, reason))

    def test_dry_run_and_failures(self) -> None:
        """--dry-run 只列两条命令（不执行）；pane run 失败 → 报错（ctrl+c 已经按了：让手动在看板 tab 里跑看板命令），退出码 1"""
        self.setup_tab()
        out = self.tab("--dry-run", now=T0)
        self.assertEqual((out["dry_run"], out["reused"], out["restarted"], out["planned"]),
                         (True, True, True, ["herdr pane send-keys w1:p2 ctrl+c", f"herdr pane run w1:p2 '{LIVE_CMD}'"]))
        self.assertEqual(self.restart_calls(), [])
        state = self.herdr_state()
        state["pane_run_fail"] = True
        self.set_herdr_state(state)
        out = self.tab(now=T0, rc=1)
        self.assertIn(f"手动在看板 tab 里执行 {LIVE_CMD}", out["error"])
        self.assertEqual([c[:2] for c in self.restart_calls()], [["pane", "send-keys"], ["pane", "run"]])

    def test_live_writes_heartbeat_in_board_tab(self) -> None:
        """--live 跑在看板 tab 里每轮写心跳：pid、pane_id、version（这份代码的版本，本 worktree 的 HEAD）、at；不在看板 tab 里
        （--pane 的分屏、不在 herdr 里）不写；写过心跳的看板再跑 board --tab 不重启"""
        self.setup_tab()
        self.live("w1:p101")
        self.live()
        self.assertFalse((self.state / "board-live.json").exists())
        started = time.time()
        self.live("w1:p2", iterations=2)
        beat = json.loads((self.state / "board-live.json").read_text())
        self.assertEqual(set(beat), {"pid", "pane_id", "version", "at"})
        self.assertEqual((beat["pane_id"], beat["version"]), ("w1:p2", self.git(DROVER.parent.parent, "rev-parse", "HEAD")))
        self.assertIsInstance(beat["pid"], int)
        self.assertLessEqual(started, beat["at"])
        out = self.drover_json("board", "--tab")
        self.assertEqual((out["reused"], out["restarted"]), (True, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
