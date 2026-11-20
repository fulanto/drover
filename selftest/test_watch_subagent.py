"""12. herdr 报 working（主 agent 在等后台子 agent）、屏幕底部却是子 agent 的确认框 → watch 报 blocked；子 agent 连着弹也要一个个报。

起因：编排者派的后台 Explore 子 agent 连着弹 Bash 确认（cd … && git …，强制审批）。第一个 herdr 报 blocked、watch 报了；
批完第二个紧接着弹出来，主 agent 停在 "✻ Waiting for 1 background agent to finish"，herdr 改报 working，watch 只在 done / idle 时读屏幕，
orch-status 也是 working，直到用户自己在 pane 里看到。之后用真实 agent 复现，屏幕见 selftest_screens.SUBAGENT_DIALOG。

规则：herdr 报 working 时也看屏幕（刚变成 working、上一轮判成 blocked、--once 时立刻读，否则每 WORKING_DIALOG_CHECK_SECONDS 秒读一次），
底部是确认框就报 blocked，走 blocked 的弹框指纹（弹框换了重报）；一直有弹框、herdr 的原始状态变了也重报一次；
卡在弹框上的时间不算进 working-no-output / working-no-diff；codex 不看。orch-status 的部分在 test_orch.py（OrchStatusTests）。

和时间有关的用例（节流、弹框出现后多久报、连着弹框、计时清零）用假时钟（selftest_support.FakeClock）：换掉 drover 模块里的 time，
sleep 直接拨表，默认参数（5 秒一轮、10 秒看一次屏幕）原样跑，不用真等。
"""

from __future__ import annotations

import unittest

from selftest_screens import (CODEX_QUOTE_WORKING, ORCH_QUOTE_WORKING, QUOTE_NAMED, SUBAGENT_DIALOG, SUBAGENT_DIALOG_2,
                              SUBAGENT_WAITING)
from selftest_support import DroverTestCase, FakeClock, patch_clock, run_watch

FAST = ["--interval", "0.2", "--stall-minutes", "0", "--quiet-minutes", "0"]
NO_PROGRESS = ["--stall-minutes", "0", "--quiet-minutes", "0"]
# 同一个后台 Explore 连着弹的两个确认框，都是抓的真实屏幕（herdr 两次都报 working）。
DIALOG_A = SUBAGENT_DIALOG
DIALOG_B = SUBAGENT_DIALOG_2


def patch_screens(d, clock: FakeClock, spans: list) -> list:
    """把 d.agent_screen 换成按假时间给屏幕：spans 是 [(从第几秒起, 屏幕)]，按开始时间排好。返回每次读屏的时间（秒）。"""
    reads: list = []

    def agent_screen(name: str, lines: int = 40) -> str:
        t = clock.elapsed()
        reads.append(t)
        return [screen for start, screen in spans if t >= start][-1]

    d.agent_screen = agent_screen
    return reads


class WorkingScreenTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()

    def test_real_subagent_dialog_blocked(self) -> None:
        """抓的真实屏幕（herdr 报 working）：screen_override 改报 blocked；识别规则本身没改，permission_dialog 原样认得"""
        self.assertTrue(self.d.permission_dialog(SUBAGENT_DIALOG))
        self.assertEqual(self.d.screen_override("working", SUBAGENT_DIALOG), "blocked")
        self.assertEqual(self.d.screen_override("working", SUBAGENT_DIALOG, "claude"), "blocked")
        self.assertEqual(self.d.screen_override("working", DIALOG_B, "claude"), "blocked")

    def test_quoted_dialog_and_waiting_line_not_blocked(self) -> None:
        """working 时正文引用别人的确认框（编排者 herdr agent read 出 worker 的屏幕，连弹框顶部横线都引用了）、
        只有 Waiting 行没有弹框，底部都是自己的输入框 → 不改报"""
        for name, screen in [("orch-quote", ORCH_QUOTE_WORKING), ("quote-named", QUOTE_NAMED),
                             ("waiting-only", SUBAGENT_WAITING)]:
            with self.subTest(name):
                self.assertEqual(self.d.screen_override("working", screen, "claude"), "")

    def test_codex_working_not_checked(self) -> None:
        """codex 的屏幕没有纯横线，引用的弹框文字会被 permission_dialog 认成弹框；codex 也没有后台子 agent 弹框 → working 时不改报。
        done / idle 的规则不分宿主（和原来一样）"""
        self.assertTrue(self.d.permission_dialog(CODEX_QUOTE_WORKING))
        self.assertEqual(self.d.screen_override("working", CODEX_QUOTE_WORKING, "codex"), "")
        self.assertEqual(self.d.screen_override("working", SUBAGENT_DIALOG, "codex"), "")
        self.assertEqual(self.d.screen_override("done", SUBAGENT_DIALOG, "codex"), "blocked")
        self.assertEqual(self.d.screen_override("blocked", SUBAGENT_DIALOG), "")


class WorkingDialogOnceTests(DroverTestCase):
    def reads(self) -> list:
        return [c[2] for c in self.herdr_calls() if c[:2] == ["agent", "read"]]

    def test_once_subagent_dialog_blocked(self) -> None:
        """watch --once：herdr 报 working + 真实的子 agent 弹框屏幕 → blocked"""
        self.assert_fake_herdr()
        self.set_agents(a={"status": "working", "agent": "claude", "screen": SUBAGENT_DIALOG})
        rc, evs, _ = self.watch("--once", "a")
        self.assertEqual((rc, evs), (0, ["EVENT a blocked"]))

    def test_once_quoted_dialog_stays_working(self) -> None:
        """watch --once：working、正文引用了确认框、底部是自己的输入框（带 / 不带会话名横线）→ working（确实读了屏幕）"""
        self.set_agents(orch={"status": "working", "screen": ORCH_QUOTE_WORKING},
                        quote={"status": "working", "screen": QUOTE_NAMED})
        rc, evs, _ = self.watch("--once", "orch", "quote")
        self.assertEqual((rc, evs), (0, ["EVENT orch working", "EVENT quote working"]))
        self.assertEqual(self.reads(), ["orch", "quote"])

    def test_once_waiting_line_only_stays_working(self) -> None:
        """watch --once：working、只有 "Waiting for 1 background agent to finish" 行、没有弹框 → working（确实读了屏幕）"""
        self.set_agents(a={"status": "working", "screen": SUBAGENT_WAITING})
        rc, evs, _ = self.watch("--once", "a")
        self.assertEqual((rc, evs), (0, ["EVENT a working"]))
        self.assertEqual(self.reads(), ["a"])

    def test_once_codex_working_not_read(self) -> None:
        """watch --once：codex agent working（屏幕上引用了弹框，读了会误认）→ 不读屏幕，报 working"""
        self.set_agents(cx={"status": "working", "agent": "codex", "screen": CODEX_QUOTE_WORKING})
        rc, evs, _ = self.watch("--once", "cx")
        self.assertEqual((rc, evs), (0, ["EVENT cx working"]))
        self.assertEqual(self.reads(), [])


class WorkingDialogCliTests(DroverTestCase):
    def test_until_event_dialog_appears(self) -> None:
        """--until-event、默认参数：working（只有 Waiting 行）→ 子 agent 弹框出现 → 第 10 秒那轮读屏报 blocked 并退出
        （屏幕按假 herdr 的读屏次数翻：第一次是 Waiting 行，第二次是弹框；5 秒那轮在节流间隔内不读）"""
        d = self.load_drover()
        clock = patch_clock(d)
        self.set_agents(a={"status": "working", "screens": [SUBAGENT_WAITING, DIALOG_A]})
        rc, evs = run_watch(d, "a", "--until-event", *NO_PROGRESS, "--timeout", "120")
        self.assertEqual((rc, evs), (0, ["EVENT a working", "EVENT a blocked"]))
        self.assertEqual(clock.elapsed(), 10)

    def test_dialog_gone_reports_working(self) -> None:
        """working + 子 agent 弹框 → blocked；批完弹框消失（只剩 Waiting 行）→ 下一轮就报回 working 并退出
        （判成 blocked 的每轮都读，不等节流间隔）"""
        self.set_agents(a={"status": "working", "screen": DIALOG_A})
        rc, first, rest = self.watch_then(["a", *FAST, "--until-event", "--timeout", "8"], 1,
                                          lambda: self.set_agents(a={"status": "working", "screen": SUBAGENT_WAITING}))
        self.assertEqual((rc, first, rest), (0, ["EVENT a blocked"], ["EVENT a working"]))


class ConsecutiveDialogTests(DroverTestCase):
    """子 agent 连着弹框（授权一次后，后面的弹框也要照样报）：弹框 A、B 是同一个后台 Explore 连着弹的两个真实确认框。
    默认参数、假时钟：5 秒一轮，没判成 blocked 时 10 秒读一次屏，判成 blocked 的每轮读。"""

    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()

    def run_sequence(self, before: dict, after: dict = None, timeout: str = "30") -> list:
        """假 herdr 先按 before，after 不为空时第一轮之后换成 after；跑到 --timeout（假时间秒数），返回 EVENT 列表。"""
        clock = patch_clock(self.d)
        self.set_agents(a=before)
        if after is not None:
            clock.on_sleep[1] = lambda: self.set_agents(a=after)
        rc, evs = run_watch(self.d, "a", *NO_PROGRESS, "--timeout", timeout)
        self.assertEqual(rc, 0)
        return evs

    def test_dialog_gone_then_next_dialog(self) -> None:
        """① herdr 一直 working：弹框 A → 批完、弹框消失（只剩 Waiting 行）→ 弹框 B：blocked、working、blocked（B 一直在，不再重报）"""
        evs = self.run_sequence({"status": "working", "screens": [DIALOG_A, SUBAGENT_WAITING, DIALOG_B]}, timeout="40")
        self.assertEqual(evs, ["EVENT a blocked", "EVENT a working", "EVENT a blocked", "EVENT timeout"])

    def test_dialog_replaced_directly(self) -> None:
        """② herdr 一直 working、中间那下没有弹框的屏幕没轮询到：弹框 A 直接换成 B → 靠弹框指纹再报一次 blocked"""
        evs = self.run_sequence({"status": "working", "screens": [DIALOG_A, DIALOG_B]})
        self.assertEqual(evs, ["EVENT a blocked", "EVENT a blocked", "EVENT timeout"])

    def test_herdr_blocked_then_working_next_dialog(self) -> None:
        """③ 当初漏报的顺序：弹框 A 时 herdr 报 blocked，批完 B 紧接着弹出来，herdr 因为 Waiting 行改报 working → 再报一次 blocked"""
        evs = self.run_sequence({"status": "blocked", "screen": DIALOG_A}, {"status": "working", "screen": DIALOG_B})
        self.assertEqual(evs, ["EVENT a blocked", "EVENT a blocked", "EVENT timeout"])

    def test_herdr_status_change_same_dialog_reports_again(self) -> None:
        """③' 屏幕上一直是同一个弹框、herdr 的原始状态变了（blocked → working，或 working → blocked）→ 当作可能换了弹框，
        重报一次 blocked（只重报这一次）"""
        for before, after in (("blocked", "working"), ("working", "blocked")):
            with self.subTest(before=before, after=after):
                evs = self.run_sequence({"status": before, "screen": DIALOG_A}, {"status": after, "screen": DIALOG_A})
                self.assertEqual(evs, ["EVENT a blocked", "EVENT a blocked", "EVENT timeout"])

    def test_same_dialog_not_reported_again(self) -> None:
        """④ herdr working、同一个弹框一直在：每轮都读屏（看它消失没有、换没换），但不重复报"""
        evs = self.run_sequence({"status": "working", "screen": DIALOG_A}, timeout="60")
        self.assertEqual(evs, ["EVENT a blocked", "EVENT timeout"])
        calls = self.herdr_calls()
        self.assertEqual(len([c for c in calls if c[:2] == ["agent", "list"]]), 12)
        self.assertEqual(len([c for c in calls if c[:2] == ["agent", "read"]]), 12)


class WorkingDialogTimingTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()

    def test_throttle_default_interval(self) -> None:
        """节流：默认 5 秒一轮，working、没有弹框时 10 秒才读一次屏幕（刚变成 working 那轮立刻读）：
        60 秒 12 轮只读 6 次（按假 herdr 的调用记录数）"""
        patch_clock(self.d)
        self.set_agents(a={"status": "working", "screen": SUBAGENT_WAITING})
        rc, evs = run_watch(self.d, "a", *NO_PROGRESS, "--timeout", "60")
        self.assertEqual((rc, evs), (0, ["EVENT a working", "EVENT timeout"]))
        calls = self.herdr_calls()
        self.assertEqual(len([c for c in calls if c[:2] == ["agent", "list"]]), 12)
        self.assertEqual(len([c for c in calls if c[:2] == ["agent", "read"]]), 6)

    def test_dialog_reported_within_15s(self) -> None:
        """默认参数下弹框不管在哪一刻出现，15 秒内（实际不超过 10 秒）报出 blocked，--until-event 随即退出"""
        for appear in (0.5, 4.9, 5.1, 10.1, 13, 19.9):
            with self.subTest(appear=appear):
                clock = patch_clock(self.d)
                patch_screens(self.d, clock, [(0, SUBAGENT_WAITING), (appear, DIALOG_A)])
                self.set_agents(a={"status": "working"})
                rc, evs = run_watch(self.d, "a", "--until-event", *NO_PROGRESS, "--timeout", "120")
                self.assertEqual((rc, evs), (0, ["EVENT a working", "EVENT a blocked"]))
                self.assertLessEqual(clock.elapsed() - appear, 15)

    def test_blocked_time_not_counted_in_progress(self) -> None:
        """卡在弹框上的时间不算进 working-no-output / working-no-diff（阈值 30 秒）：working 20 秒 → 弹框 60 秒（期间不报没动静）
        → 批完回到 working 时从头计时，报 working 而不是 working-no-output / working-no-diff"""
        repo = self.work / "r"
        self.init_repo(repo, {"a.txt": "1\n"})
        spans = [(0, SUBAGENT_WAITING), (20, DIALOG_A), (80, SUBAGENT_WAITING)]
        for kind, opts in (("no-output", ["--quiet-minutes", "0.5", "--output-interval", "5", "--stall-minutes", "0"]),
                           ("no-diff", ["--stall-minutes", "0.5", "--diff-interval", "5", "--quiet-minutes", "0"])):
            with self.subTest(kind):
                clock = patch_clock(self.d)
                patch_screens(self.d, clock, spans)
                self.set_agents(a={"status": "working", "cwd": str(repo)})
                rc, evs = run_watch(self.d, "a", *opts, "--timeout", "100")
                self.assertEqual((rc, evs), (0, ["EVENT a working", "EVENT a blocked", "EVENT a working",
                                                 "EVENT timeout"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
