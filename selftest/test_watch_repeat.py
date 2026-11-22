"""10. 连续 blocked / done 重报：同一状态 herdr 的 state_change_seq 变了（中间经历过别的状态没轮询到）、
或者 blocked 时弹框换了（herdr 自己也没看到中间那下 working），都要再报一次；两样都没变不重报。

起因是三次空等 10～24 分钟：worker 连着弹两个确认，编排者批完第一个，watch 看到的是 blocked → blocked 就不报。
实测（selftest_screens 的屏幕）：一条回复里并行两个工具调用时，批完第一个、第二个立刻弹出来，herdr 一直是 blocked、序号不变。
"""

from __future__ import annotations

import unittest

from selftest_screens import (BASH_NAMED, NAMED_RULE, PARALLEL_FIRST, PARALLEL_SECOND, PARALLEL_SECOND_BLINK,
                              WEBFETCH_NAMED, webfetch_dialog)
from selftest_support import DroverTestCase

FAST = ["--until-event", "--interval", "0.2", "--stall-minutes", "0", "--quiet-minutes", "0"]
NORMAL = """⏺ 报告已写到 ~/.drover/x/y.md。

✻ Cooked for 3m · done 1:05 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on · ← 1 agent"""
MONITOR = """⏺ All four are working. I'll handle the next monitor events as they arrive.

✻ Baked for 11m 22s · done 12:44 AM · 1 monitor still running
────────────────
❯
────────────────
  ⏵⏵ accept edits on · 1 monitor · ← 1 agent"""


class DialogFingerprintTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()

    def test_fingerprint(self) -> None:
        """弹框换了（c → d）指纹变；弹框上方那行 ⏺ 闪烁不变；普通数字保留（sips -Z 512 / 1024 不同）；时长和时钟抹掉"""
        fp = self.d.dialog_fingerprint
        self.assertNotEqual(fp(PARALLEL_FIRST), fp(PARALLEL_SECOND))
        self.assertEqual(fp(PARALLEL_SECOND), fp(PARALLEL_SECOND_BLINK))
        self.assertNotEqual(fp(BASH_NAMED.replace("ln -s /dev/null a", "sips -Z 512 shot.png")),
                            fp(BASH_NAMED.replace("ln -s /dev/null a", "sips -Z 1024 shot.png")))
        waiting = " This command requires approval (waiting {}, since {})"
        self.assertEqual(fp(BASH_NAMED.replace(" This command requires approval", waiting.format("3s", "9:07"))),
                         fp(BASH_NAMED.replace(" This command requires approval", waiting.format("1m 12s", "9:08"))))


class RepeatCliTests(DroverTestCase):
    def change(self, before: dict, after: dict, timeout: str = "30") -> tuple:
        """watch --until-event：先按 before 输出当前状态，改成 after 后等它退出。返回 (退出码, 第一行事件, 之后的事件)。"""
        self.set_agents(a=before)
        return self.watch_then(["a", *FAST, "--timeout", timeout], 1, lambda: self.set_agents(a=after))

    def change_blocked(self, before: dict, after: dict) -> tuple:
        """同 change，但开头就是 blocked：--until-event 第一轮有 blocked 就退出（test_watch_states），所以不带它、跑满 3 秒
        假时间。返回 (退出码, 第一行事件, 之后的事件，末尾是 EVENT timeout)。"""
        self.set_agents(a=before)
        return self.watch_then(["a", *FAST[1:], "--timeout", "3"], 1, lambda: self.set_agents(a=after))

    def test_blocked_seq_changed_reports_again(self) -> None:
        """blocked → (序号变了，中间那下 working 没轮询到) → blocked、弹框一样 → 再报一次 blocked"""
        rc, first, rest = self.change_blocked({"status": "blocked", "seq": 10, "screen": BASH_NAMED},
                                              {"status": "blocked", "seq": 12, "screen": BASH_NAMED})
        self.assertEqual((rc, first, rest), (0, ["EVENT a blocked"], ["EVENT a blocked", "EVENT timeout"]))

    def test_blocked_dialog_changed_reports_again(self) -> None:
        """blocked、序号没变，弹框换了（并行两个工具调用，批完第一个，第二个立刻弹出来）→ 再报一次 blocked"""
        rc, first, rest = self.change_blocked({"status": "blocked", "seq": 10, "screen": PARALLEL_FIRST},
                                              {"status": "blocked", "seq": 10, "screen": PARALLEL_SECOND})
        self.assertEqual((rc, first, rest), (0, ["EVENT a blocked"], ["EVENT a blocked", "EVENT timeout"]))

    def test_blocked_unchanged_not_reported(self) -> None:
        """blocked、序号和弹框都不变（弹框上方那行的 ⏺ 一直在闪）→ 不重报，到期 timeout"""
        self.set_agents(a={"status": "blocked", "seq": 10, "screens": [PARALLEL_SECOND, PARALLEL_SECOND_BLINK] * 30})
        rc, evs, _ = self.watch("a", *FAST[1:], "--timeout", "3")
        self.assertEqual((rc, evs), (0, ["EVENT a blocked", "EVENT timeout"]))
        self.assertGreater(self.herdr_state()["agents"]["a"]["reads"], 3)  # 确实每轮都在读屏幕取指纹

    def test_done_seq_changed_reports_again(self) -> None:
        """done → (序号变了：又干了一轮，没轮询到 working) → done → 再报一次 done"""
        rc, first, rest = self.change({"status": "done", "seq": 10, "screen": NORMAL},
                                      {"status": "done", "seq": 13, "screen": NORMAL})
        self.assertEqual((rc, first, rest), (0, ["EVENT a done"], ["EVENT a done"]))

    def test_done_dialog_changed_reports_again(self) -> None:
        """herdr 报 done 的 WebFetch 弹框 → 序号没变、换成另一个域名的弹框 → 再报一次 blocked"""
        other = webfetch_dialog("docs.example.invalid") + "\n" + NAMED_RULE
        rc, first, rest = self.change_blocked({"status": "done", "seq": 10, "screen": WEBFETCH_NAMED},
                                              {"status": "done", "seq": 10, "screen": other})
        self.assertEqual((rc, first, rest), (0, ["EVENT a blocked"], ["EVENT a blocked", "EVENT timeout"]))

    def test_waiting_background_seq_changed_not_reported(self) -> None:
        """挂着 Monitor 的编排者处理完一个 worker 事件（序号变了），还是同一个 waiting-background(1) → 不重报"""
        rc, first, rest = self.change({"status": "done", "seq": 10, "screen": MONITOR},
                                      {"status": "done", "seq": 13, "screen": MONITOR}, timeout="3")
        self.assertEqual((rc, first, rest), (0, ["EVENT a waiting-background(1)"], ["EVENT timeout"]))

    def test_working_seq_changed_not_reported(self) -> None:
        """working 不因序号变化重报"""
        rc, first, rest = self.change({"status": "working", "seq": 10, "screen": NORMAL},
                                      {"status": "working", "seq": 11, "screen": NORMAL}, timeout="3")
        self.assertEqual((rc, first, rest), (0, ["EVENT a working"], ["EVENT timeout"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
