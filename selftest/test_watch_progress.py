"""7. working-no-output / working-no-diff：阈值用小数分钟（0.02 分钟 = 1.2 秒）；--until-event 退出。

屏幕输出指纹有 7 项检查，watch 状态机有 3 个场景
（不 monkeypatch subprocess.run，用假 herdr + 命令行入口）。watch 在进程里跑、用假时钟（selftest_support 的 watch）：
阈值、轮询间隔、--timeout 都按假时间。正向用例（应该报）给足 30 秒上限、报了就退出；反向用例（不该报）跑 4 秒（阈值的 3 倍多、20 轮）到 timeout。
取指纹的 git 超时（WatchFingerprintTimeoutTests）：包住模块里起子进程的 capture_run，挑中的 git 调用直接抛 TimeoutExpired，不真等。
"""

from __future__ import annotations

import re
import subprocess
import unittest

from selftest_support import DroverTestCase

QUIET_MIN = 0.02
FAST = ["--interval", "0.2", "--output-interval", "0.2", "--diff-interval", "0.2", "--until-event"]
CODEX = "• Edited calc.py (+4 -1)\n\n• Working ({t} • esc to interrupt)\n\n» Ask Codex\n  GPT-6 · {pct}% context left"
CLAUDE = ("⏺ Bash(pytest)\n  ⎿  Running… (3m 2s)\n\n✶ Pondering… (3m 12s · ↓ 4.1k tokens · esc to interrupt)\n"
          "  ⎿  Tip: use /clear")
CODEX_WAIT = ("• Waiting for background terminal (9m 30s • esc to interrupt) · 1 background terminal running\n"
              "  └ drover watch")
CODEX_RUN = "• Running python3 -m unittest tests.test_calc -v\n\n• Working (1m 3s • esc to interrupt)"
QUIET = CODEX.format(t="3m 2s", pct=40)
DONE = "⏺ 报告已写到 ~/.drover/x/y.md。\n\n✻ Cooked for 3m · done 1:05 AM\n────────────────\n❯\n────────────────"


def assert_bucket(tc: unittest.TestCase, event: str, kind: str) -> None:
    """working-no-xxx(Nm)：N 是阈值的整数倍（机器慢时可能直接报到 2 倍）。"""
    m = re.fullmatch(rf"EVENT a {kind}\((\d+(?:\.\d+)?)m\)", event)
    tc.assertIsNotNone(m, event)
    ratio = float(m.group(1)) / QUIET_MIN
    tc.assertGreaterEqual(round(ratio), 1)
    tc.assertAlmostEqual(ratio, round(ratio), places=6)


def git_sub(cmd: list) -> str:
    """git 命令行的子命令：跳过 -C <目录>、-c <配置>。"""
    i = 1
    while i < len(cmd) and cmd[i] in ("-C", "-c"):
        i += 2
    return cmd[i] if i < len(cmd) else ""


class OutputFingerprintTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()

    def fp(self, screen: str) -> tuple:
        self.set_agents(x={"status": "working", "screen": screen})
        return self.d.output_fingerprint("x")

    def test_fingerprint_normalization(self) -> None:
        """计时 / 上下文百分比 / spinner 动词 / Tip 不改变指纹，新的工具调用改变指纹"""
        a, _ = self.fp(CODEX.format(t="11m 22s", pct=40))
        b, _ = self.fp(CODEX.format(t="16m 27s", pct=39))
        c, _ = self.fp(CODEX.replace("Edited calc.py", "Ran python3 -m unittest").format(t="16m 27s", pct=39))
        e, _ = self.fp(CLAUDE)
        f, _ = self.fp(CLAUDE.replace("Pondering", "Crunching").replace("use /clear", "press shift+tab"))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(e, f)

    def test_running_command_detection(self) -> None:
        """codex 只在思考不算在跑命令；claude Running…、codex 等后台终端、codex • Running 算在跑命令；读不到 → (None, False)"""
        self.assertFalse(self.fp(QUIET)[1])
        self.assertTrue(self.fp(CLAUDE)[1])
        self.assertTrue(self.fp(CODEX_WAIT)[1])
        self.assertTrue(self.fp(CODEX_RUN)[1])
        self.assertEqual(self.d.output_fingerprint("missing"), (None, False))


class WorktreeFingerprintTests(DroverTestCase):
    def test_changes_and_stability(self) -> None:
        """改已跟踪文件、加 / 改未跟踪文件、提交都会变；不动时稳定；不在 git 仓库里返回 None"""
        d = self.load_drover()
        repo = self.work / "r"
        self.init_repo(repo, {"a.txt": "1\n"})
        fps = [d.worktree_fingerprint(str(repo))]
        self.assertEqual(d.worktree_fingerprint(str(repo)), fps[0])
        (repo / "a.txt").write_text("2\n")
        fps.append(d.worktree_fingerprint(str(repo)))
        (repo / "u.txt").write_text("u\n")
        fps.append(d.worktree_fingerprint(str(repo)))
        (repo / "u.txt").write_text("uu\n")
        fps.append(d.worktree_fingerprint(str(repo)))
        self.commit(repo, "c")
        fps.append(d.worktree_fingerprint(str(repo)))
        self.assertEqual(len(set(fps)), len(fps), fps)
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertIsNone(d.worktree_fingerprint(str(plain)))


class WatchProgressCliTests(DroverTestCase):
    def test_no_output_reported_then_exit(self) -> None:
        """working、屏幕没变、也没在跑命令 → 报 working-no-output 后 --until-event 退出"""
        self.set_agents(a={"status": "working", "screen": QUIET, "cwd": str(self.tmp)})
        rc, evs, secs = self.watch("a", *FAST, "--quiet-minutes", str(QUIET_MIN), "--stall-minutes", "0",
                                   "--timeout", "30")
        self.assertEqual(rc, 0)
        self.assertEqual(len(evs), 2, evs)
        self.assertEqual(evs[0], "EVENT a working")
        assert_bucket(self, evs[1], "working-no-output")
        self.assertLess(secs, 30)

    def test_running_command_not_reported(self) -> None:
        """一直在跑命令（Running…）→ 不报，到期 timeout"""
        self.set_agents(a={"status": "working", "screen": CLAUDE})
        rc, evs, _ = self.watch("a", *FAST, "--quiet-minutes", str(QUIET_MIN), "--stall-minutes", "0",
                                "--timeout", "4")
        self.assertEqual((rc, evs), (0, ["EVENT a working", "EVENT timeout"]))

    def test_changing_output_not_reported(self) -> None:
        """输出一直在变 → 不报，到期 timeout"""
        self.set_agents(a={"status": "working", "screen": QUIET, "grow": True})
        rc, evs, _ = self.watch("a", *FAST, "--quiet-minutes", str(QUIET_MIN), "--stall-minutes", "0",
                                "--timeout", "4")
        self.assertEqual((rc, evs), (0, ["EVENT a working", "EVENT timeout"]))
        self.assertGreater(self.herdr_state()["agents"]["a"]["reads"], 3)  # 确实在反复读屏幕

    def test_no_diff_reported_then_exit(self) -> None:
        """working、worktree 一直没改动 → 报 working-no-diff 后 --until-event 退出"""
        repo = self.work / "r"
        self.init_repo(repo, {"a.txt": "1\n"})
        self.set_agents(a={"status": "working", "screen": QUIET, "cwd": str(repo)})
        rc, evs, secs = self.watch("a", *FAST, "--stall-minutes", str(QUIET_MIN), "--quiet-minutes", "0",
                                   "--timeout", "30")
        self.assertEqual(rc, 0)
        self.assertEqual(len(evs), 2, evs)
        self.assertEqual(evs[0], "EVENT a working")
        assert_bucket(self, evs[1], "working-no-diff")
        self.assertLess(secs, 30)

    def test_no_diff_not_reported_while_worktree_changes(self) -> None:
        """worktree 一直在改（每轮之间写一次，0.2 秒一轮）→ 不报，到期 timeout"""
        repo = self.work / "r"
        self.init_repo(repo, {"a.txt": "1\n"})
        self.set_agents(a={"status": "working", "screen": QUIET, "cwd": str(repo)})

        def churn(n: int) -> None:
            (repo / "a.txt").write_text("x" * n)

        rc, evs, _ = self.watch("a", *FAST, "--stall-minutes", str(QUIET_MIN), "--quiet-minutes", "0",
                                "--timeout", "4", on_sleep=churn)
        self.assertEqual((rc, evs), (0, ["EVENT a working", "EVENT timeout"]))

    def test_no_diff_skipped_outside_git(self) -> None:
        """cwd 不在 git 仓库里（编排者）→ 不查改动，不报"""
        plain = self.tmp / "plain"
        plain.mkdir()
        self.set_agents(a={"status": "working", "screen": QUIET, "cwd": str(plain)})
        rc, evs, _ = self.watch("a", *FAST, "--stall-minutes", str(QUIET_MIN), "--quiet-minutes", "0",
                                "--timeout", "4")
        self.assertEqual((rc, evs), (0, ["EVENT a working", "EVENT timeout"]))


class WatchFingerprintTimeoutTests(DroverTestCase):
    """取 worktree 指纹的 git 超时（满载时）：这一轮没取到，不算有变化也不算没变化；watch 不退出，之后的轮次照常取、照常报
    working-no-diff。第几轮按假时钟 sleep 的次数算（第一轮是 0，一轮 0.2 秒）。"""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.work / "r"
        self.init_repo(self.repo, {"a.txt": "1\n"})
        self.set_agents(a={"status": "working", "screen": QUIET, "cwd": str(self.repo)})
        self.round = 0
        self.timed_out: list = []  # 每次超时的 (第几轮, git 子命令)

    def watch_with_timeouts(self, times_out, *args: str, on_round=None) -> tuple:
        """watch 期间 times_out(第几轮, 命令) 为真的 git 调用直接抛 TimeoutExpired（包住 drover 起子进程的 capture_run，
        run 把它转成 DroverError，和真超时同一条路）；on_round(第几轮) 在每轮开始前调。返回同 watch()。"""
        def setup(d) -> None:
            real = d.capture_run

            def fake(cmd: list, **kw):
                if cmd[:1] == ["git"] and times_out(self.round, cmd):
                    self.timed_out.append((self.round, git_sub(cmd)))
                    raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
                return real(cmd, **kw)

            d.capture_run = fake

        def tick(n: int) -> None:
            self.round = n
            if on_round:
                on_round(n)

        return self.watch("a", *args, on_sleep=tick, setup=setup)

    def test_first_fingerprint_times_out(self) -> None:
        """刚进 working 第一次取（rev-parse --show-toplevel 起）就超时、前 3 轮都超时 → 不退出；第 3 轮取到，从这时起算没改动，
        满阈值报 working-no-diff（不早于 0.6 + 1.2 秒）"""
        rc, evs, secs = self.watch_with_timeouts(lambda r, cmd: r <= 2, *FAST, "--stall-minutes", str(QUIET_MIN),
                                                 "--quiet-minutes", "0", "--timeout", "30")
        self.assertEqual(self.timed_out, [(0, "rev-parse"), (1, "rev-parse"), (2, "rev-parse")])
        self.assertEqual(rc, 0)
        self.assertEqual(len(evs), 2, evs)
        self.assertEqual(evs[0], "EVENT a working")
        assert_bucket(self, evs[1], "working-no-diff")
        self.assertGreaterEqual(secs, 1.8 - 1e-6)

    def test_later_fingerprint_times_out(self) -> None:
        """第一次取到，之后第 1～4 轮取到一半（git diff HEAD）超时 → 不退出、超时的轮次不算有变化：照样从第 0 轮起算，
        满阈值报 working-no-diff（算成有变化的话要到 1.0 + 1.2 秒以后）"""
        rc, evs, secs = self.watch_with_timeouts(lambda r, cmd: 1 <= r <= 4 and git_sub(cmd) == "diff",
                                                 *FAST, "--stall-minutes", str(QUIET_MIN), "--quiet-minutes", "0",
                                                 "--timeout", "30")
        self.assertEqual(self.timed_out, [(r, "diff") for r in range(1, 5)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(evs), 2, evs)
        self.assertEqual(evs[0], "EVENT a working")
        assert_bucket(self, evs[1], "working-no-diff")
        self.assertLess(secs, 1.9)

    def test_always_timing_out_keeps_watching(self) -> None:
        """取指纹的 git 一直超时 → 不退出、不报 working-no-diff（一次都没取到），状态变化照常报，到期 timeout"""
        def finish(n: int) -> None:
            if n == 10:
                self.set_agents(a={"status": "done", "screen": DONE, "cwd": str(self.repo)})

        rc, evs, _ = self.watch_with_timeouts(lambda r, cmd: True, "--interval", "0.2", "--diff-interval", "0.2",
                                              "--stall-minutes", str(QUIET_MIN), "--quiet-minutes", "0",
                                              "--timeout", "4", on_round=finish)
        self.assertEqual((rc, evs), (0, ["EVENT a working", "EVENT a done", "EVENT timeout"]))
        self.assertEqual(sorted({r for r, _ in self.timed_out}), list(range(10)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
