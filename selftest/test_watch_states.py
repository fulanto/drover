"""6. watch 认出后台命令（· N shell / · N monitor → waiting-background(N)）；8. api-error（API Error: 503、codex stream error）；
9. herdr 报 done 但屏幕底部是确认弹框（WebFetch 授权）→ blocked。

屏幕内容由假 herdr 的 agent read 返回。503 屏幕取自真实屏幕（网关域名换成了占位符）；
弹框屏幕取自临时 agent 实测（selftest_screens）。
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import unittest

from selftest_screens import (BASH_DIALOG, BASH_NAMED, QUOTE, QUOTE_IN_REPORT, QUOTE_NAMED, WEBFETCH, WEBFETCH_NAMED)
from selftest_support import DROVER, DroverTestCase, events

SHELL = """⏺ Bash(npm test) running in background
  ⎿  Running in the background (↓ to manage)

✻ Cooked for 1m 3s · done 10:01 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on (shift+tab to cycle) · 1 shell · ← 1 agent"""
TWO_SHELLS = SHELL.replace("· 1 shell", "· 2 shells")
BASHES = SHELL.replace("· 1 shell", "· 3 background bashes")
MONITOR = """⏺ All four are working. I'll handle the next monitor events as they arrive.

✻ Baked for 11m 22s · done 12:44 AM · 1 monitor still running
────────────────
❯
────────────────
  ➜ work ctx:20% Opus 5.5
  ⏵⏵ accept edits on · 1 monitor · ← 1 agent"""
NORMAL = """⏺ 报告已写到 ~/.drover/x/y.md。

✻ Cooked for 3m · done 1:05 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on · ← 1 agent"""
API_503 = """⏺ API Error: 503 No available accounts: no available accounts (request id: 2026110517). This is a server-side
  issue, usually temporary — try again in a moment. If it persists, check your inference gateway (gateway.example.invalid).

✻ Brewed for 34m 26s · done 1:16 AM
────────────────
❯
────────────────
  ➜ task-demo-api git:(task/demo-api) ✗ ctx:30% Opus 5.5
  ⏵⏵ accept edits on (shift+tab to cycle) · ← 1 agent"""
CODEX_STREAM = """■ stream disconnected before completion: error sending request
» Ask Codex to do anything
  GPT-6-Astra xhigh Fast · ~/work"""
CODEX_STATUS = """■ unexpected status 502 Bad Gateway: upstream connect error
» Ask Codex to do anything
  GPT-6-Astra xhigh Fast · ~/work"""
# 状态栏的 "· 1 shell" 在倒数第 8 行之外（之后又输出了很多行）：不算还在跑
SHELL_FAR_UP = "  ⏵⏵ accept edits on · 1 shell · ← 1 agent\n" + "\n".join(f"line {c}" for c in "abcdefghij")
# Claude Code 的接口错误在工具调用下面（⎿ 前缀），是最后一个输出块
API_UNDER_TOOL = """⏺ Bash(npm test)
  ⎿  API Error: Connection error.

✻ Cooked for 2m · done 1:16 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on · ← 1 agent"""
# 两次误报：正文里提到 "API Error"（worker 报告、编排者收尾总结），都不在行首
API_MENTION_REPORT = """⏺ 报告已写到 ~/.drover/demo/d1.md。
  - M1 API_ERROR_RE 不认 API Error
  - 已补用例

✻ Cooked for 3m · done 1:05 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on · ← 1 agent"""
API_MENTION_SUMMARY = """⏺ 收尾总结：done_state 认出 API Error 后报 api-error，watch 端到端验证过；三个 worker 都已验收。

✻ Cooked for 1m 10s · done 2:40 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on · ← 1 agent"""
# 更早的块是接口错误，被续上之后又有正常输出
API_EARLIER = API_503.split("\n✻")[0] + """

✻ Brewed for 34m 26s · done 1:16 AM

❯ 上一轮被接口错误打断了，从中断的地方接着做

⏺ 接着做完了，报告已写到 ~/.drover/x/y.md。

✻ Cooked for 2m · done 1:30 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on · ← 1 agent"""
# codex：更早有 stream error，之后又有正常的 • 输出
CODEX_EARLIER = """■ stream disconnected before completion: error sending request
› 接着做
• 报告已写到 ~/.drover/x/y.md
» Ask Codex to do anything
  GPT-6-Astra xhigh Fast · ~/work"""


class DoneStateTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()

    def done_state(self, screen: str) -> str:
        self.set_agents(x={"status": "done", "screen": screen})
        return self.d.done_state("x")

    def test_background_commands(self) -> None:
        """状态栏 · N shell / shells / background bashes / monitor → waiting-background(N)"""
        for screen, want in [(SHELL, "waiting-background(1)"), (TWO_SHELLS, "waiting-background(2)"),
                             (BASHES, "waiting-background(3)"), (MONITOR, "waiting-background(1)")]:
            with self.subTest(want=want, screen=screen.splitlines()[-1]):
                self.assertEqual(self.done_state(screen), want)
        self.set_agents(x={"status": "done", "screen": TWO_SHELLS})
        self.assertEqual(self.d.background_shells("x"), 2)

    def test_marker_outside_last_8_lines_ignored(self) -> None:
        """后台命令标记不在屏幕最后 8 行里 → 不算"""
        self.assertEqual(self.done_state(SHELL_FAR_UP), "")

    def test_api_errors(self) -> None:
        """Claude Code 的 API Error: 503（行首 ⏺）、工具调用下的 ⎿ API Error，codex 的 stream disconnected / unexpected status → api-error"""
        for name, screen in [("claude-503", API_503), ("claude-under-tool", API_UNDER_TOOL),
                             ("codex-stream", CODEX_STREAM), ("codex-status", CODEX_STATUS)]:
            with self.subTest(name):
                self.assertEqual(self.done_state(screen), "api-error")

    def test_api_error_mentions_not_flagged(self) -> None:
        """正文中间提到 API Error（两次误报的原句）、更早的块是接口错误但后面又有正常输出 → 不报 api-error"""
        for name, screen in [("report-mention", API_MENTION_REPORT), ("summary-mention", API_MENTION_SUMMARY),
                             ("claude-earlier-block", API_EARLIER), ("codex-earlier-block", CODEX_EARLIER)]:
            with self.subTest(name):
                self.assertEqual(self.done_state(screen), "")

    def test_permission_dialog_blocked(self) -> None:
        """herdr 报 done、屏幕底部是确认弹框 → blocked：WebFetch 授权（实测 herdr 报 done 的那屏）、Bash 确认框，
        带 / 不带会话名横线（弹框底部那条 "──── worker-<slug> ─"），弹框顶部横线超出读到的范围"""
        cut_top = "\n".join(BASH_NAMED.splitlines()[-12:])  # 弹框太高：顶部纯横线不在读到的行里
        for name, screen in [("webfetch", WEBFETCH), ("webfetch-named", WEBFETCH_NAMED), ("bash", BASH_DIALOG),
                             ("bash-named", BASH_NAMED), ("bash-top-cut", cut_top)]:
            with self.subTest(name):
                self.assertEqual(self.done_state(screen), "blocked")

    def test_quoted_dialog_not_blocked(self) -> None:
        """正文里引用了弹框文字、底部是正常输入框（输入框上边框带 / 不带会话名，引用在 ⏺ 行或续行里）→ 不报 blocked"""
        for name, screen in [("quote", QUOTE), ("quote-named", QUOTE_NAMED), ("quote-in-report", QUOTE_IN_REPORT)]:
            with self.subTest(name):
                self.assertEqual(self.done_state(screen), "")

    def test_dialog_wins_over_background(self) -> None:
        """弹框 + 状态栏还有后台命令 → blocked（有弹框就必须有人处理）"""
        self.assertEqual(self.done_state(BASH_NAMED + "\n  ⏵⏵ accept edits on · 1 shell · ← 1 agent"), "blocked")

    def test_normal_done_not_flagged(self) -> None:
        """正常 done → 空串（watch 照报 done）"""
        self.assertEqual(self.done_state(NORMAL), "")

    def test_api_error_wins_over_background(self) -> None:
        """既有接口错误又有后台命令 → 报 api-error：这一轮被打断了、编排者自己不会再动；后台命令（Monitor 到期前最多 30 分钟，
        留着的 dev server 一直不退）不会替它续上，以前报 waiting-background 就一直没人管"""
        self.assertEqual(self.done_state(API_503.replace("· ← 1 agent", "· 1 shell · ← 1 agent")), "api-error")


class WatchStateCliTests(DroverTestCase):
    def test_until_event_first_round_needs_handling(self) -> None:
        """--until-event 第一轮（重挂时）就有 blocked / api-error / gone → 输出完当前状态就退出（这三种处理过就不会还在，Codex
        编排者处理上一个事件期间别的 worker 卡住的不白等 25 分钟）；只有 done / idle / working / waiting-background 的不退出，
        到期 timeout（done / idle 分不出处理过没有）"""
        for name, agents, want in (
                ("blocked", {"a": {"status": "blocked", "screen": BASH_DIALOG}}, "EVENT a blocked"),
                ("api-error", {"a": {"status": "done", "screen": API_503}}, "EVENT a api-error"),
                ("gone", {}, "EVENT a gone")):
            with self.subTest(name):
                self.set_agents(w={"status": "done", "screen": NORMAL}, **agents)
                rc, evs, secs = self.watch("w", "a", "--until-event", "--interval", "1", "--stall-minutes", "0",
                                           "--quiet-minutes", "0", "--timeout", "30")
                self.assertEqual((rc, evs, secs), (0, ["EVENT w done", want], 0))
        self.set_agents(w={"status": "done", "screen": NORMAL}, x={"status": "idle", "screen": NORMAL},
                        b={"status": "done", "screen": SHELL}, y={"status": "working", "screen": NORMAL})
        rc, evs, secs = self.watch("w", "x", "b", "y", "--until-event", "--interval", "1", "--stall-minutes", "0",
                                   "--quiet-minutes", "0", "--timeout", "5")
        self.assertEqual((rc, evs[-1], secs >= 5), (0, "EVENT timeout", True))
    def test_once_reports_each_state(self) -> None:
        """watch --once：done / idle 看屏幕改报 blocked（确认弹框）/ waiting-background / api-error，working 也读屏幕
        （只认确认框，停在 API Error 的屏幕照样报 working），不在列表里的报 gone"""
        self.assert_fake_herdr()
        self.set_agents(shell={"status": "done", "screen": SHELL}, two={"status": "idle", "screen": TWO_SHELLS},
                        monitor={"status": "done", "screen": MONITOR}, api={"status": "done", "screen": API_503},
                        codex={"status": "idle", "screen": CODEX_STREAM}, fine={"status": "done", "screen": NORMAL},
                        fetch={"status": "done", "screen": WEBFETCH_NAMED}, quote={"status": "done", "screen": QUOTE},
                        busy={"status": "working", "screen": API_503})
        names = ["shell", "two", "monitor", "api", "codex", "fine", "fetch", "quote", "busy", "ghost"]
        rc, evs, _ = self.watch("--once", *names)
        self.assertEqual(rc, 0)
        self.assertEqual(evs, ["EVENT shell waiting-background(1)", "EVENT two waiting-background(2)",
                               "EVENT monitor waiting-background(1)", "EVENT api api-error", "EVENT codex api-error",
                               "EVENT fine done", "EVENT fetch blocked", "EVENT quote done", "EVENT busy working",
                               "EVENT ghost gone"])
        read = [c[2] for c in self.herdr_calls() if c[:2] == ["agent", "read"]]
        self.assertEqual(read, names[:9])

    def watch_until_change(self, before: dict, after: dict) -> tuple:
        """watch --until-event：先按 before 输出第一行，改成 after 后等它退出，返回 (退出码, 第一行事件, 之后的事件)。"""
        self.set_agents(a=before)
        p = subprocess.Popen([sys.executable, str(DROVER), "watch", "a", "--until-event", "--interval", "0.2",
                              "--stall-minutes", "0", "--quiet-minutes", "0", "--timeout", "60"],
                             env=self.env, cwd=str(self.tmp), stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        first = events(p.stdout.readline())
        self.set_agents(a=after)
        rest, _ = p.communicate(timeout=90)
        return p.returncode, first, events(rest)

    def test_until_event_background_agent_resumes(self) -> None:
        """done + 后台命令 → waiting-background(1)；后台命令把 agent 叫醒回到 working → 输出后退出"""
        rc, first, rest = self.watch_until_change({"status": "done", "screen": SHELL},
                                                  {"status": "working", "screen": SHELL})
        self.assertEqual((rc, first, rest), (0, ["EVENT a waiting-background(1)"], ["EVENT a working"]))

    def test_until_event_api_error(self) -> None:
        """working → 停在 API Error 的 done → 报 api-error 后退出"""
        rc, first, rest = self.watch_until_change({"status": "working", "screen": NORMAL},
                                                  {"status": "done", "screen": API_503})
        self.assertEqual((rc, first, rest), (0, ["EVENT a working"], ["EVENT a api-error"]))

    def test_background_recheck_reports_done(self) -> None:
        """一直 done：后台命令结束（屏幕上没了 · 1 shell）、agent 没接着干 → 复查时改报 done（复查间隔调成 0.5 秒）"""
        d = self.load_drover()
        d.BACKGROUND_RECHECK_SECONDS = 0.5
        self.set_agents(a={"status": "done", "screens": [SHELL, NORMAL]})
        args = d.build_parser().parse_args(["watch", "a", "--until-event", "--interval", "0.1", "--stall-minutes", "0",
                                            "--quiet-minutes", "0", "--timeout", "60"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = d.cmd_watch(args)
        self.assertEqual(rc, 0)
        self.assertEqual(events(buf.getvalue()), ["EVENT a waiting-background(1)", "EVENT a done"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
