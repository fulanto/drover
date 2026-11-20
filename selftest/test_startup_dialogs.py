"""启动弹窗：agent-start / orch-start 起的 agent 停在已知弹窗上时自动处理，认不出的不按任何键。

Claude Code 对 --add-dir 加进来、这个会话没用过的目录弹 "Adding a new directory:" 信任框（真实样本 TRUST_ADD_DIR），
光标默认在 "No, exit"：要先按 down 移到 "Yes, add it" 再回车，直接回车 agent 就退出了。
在进程里跑 agent-start / orch-start（build_parser 解析参数、HANDLERS 里的处理函数，和命令行同一条路），herdr 是假的
（selftest_fakes）：state["starting"] 是 agent start 之后的样子，on_keys 按收到的按键整串换屏幕 / 状态。
模块里的 time 换成假时钟（selftest_support.FakeClock）：起步等的 4 秒、按键之间的 0.5 / 1 秒都按假时间，不用真等。
"""

from __future__ import annotations

import unittest

from selftest_screens import (BASH_DIALOG, IDLE_AFTER_START, TRUST_ADD_DIR, TRUST_ADD_DIR_YES, TRUST_FOLDER_LEGACY,
                              TRUST_MOVE_DIR)
from selftest_support import DroverTestCase, patch_clock

# 光标在 "No, exit"：down 移到 "Yes, add it"，接着 enter 进入空闲输入框；直接 enter（选了 No, exit）agent 退出。
ADD_DIR_AGENT = {"status": "blocked", "agent": "claude", "screen": TRUST_ADD_DIR,
                 "on_keys": {"down": {"screen": TRUST_ADD_DIR_YES},
                             "down enter": {"status": "idle", "screen": IDLE_AFTER_START},
                             "enter": {"gone": True}}}


class StartupDialogTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.assert_fake_herdr()
        self.d = self.load_drover()
        patch_clock(self.d)

    def run_command(self, *argv: str) -> dict:
        args = self.d.build_parser().parse_args(argv)
        return self.d.HANDLERS[args.command](args)

    def starting(self, **agents: dict) -> None:
        state = self.herdr_state()
        state["starting"] = agents
        self.set_herdr_state(state)

    def agent_start(self, name: str) -> dict:
        return self.run_command("agent-start", "--name", name, "--kind", "claude", "--slug", "s1", "--pane", "w1:p1")

    def keys_sent(self, name: str) -> list:
        return [c[3:] for c in self.herdr_calls() if c[:3] == ["agent", "send-keys", name]]

    @staticmethod
    def outcome(out: dict) -> tuple:
        return out["ok"], out["status"], out["startup_dialogs_handled"]

    def test_dialog_table(self) -> None:
        """弹窗表：新信任框认成 trust_add_dir（问句在窄 pane 里折行也认），旧信任框 trust_folder；Bash 确认框、
        同一组件的 "Moving to a new directory:"（问句一样、标题不同）不认"""
        def label(screen: str):
            return next((name for pattern, _, name in self.d.STARTUP_DIALOGS if pattern.search(screen)), None)

        wrapped = TRUST_ADD_DIR.replace("hasn't worked here before", "hasn't worked\n here before")
        self.assertNotEqual(wrapped, TRUST_ADD_DIR)
        self.assertEqual([label(s) for s in (TRUST_ADD_DIR, TRUST_ADD_DIR_YES, wrapped, TRUST_FOLDER_LEGACY,
                                             BASH_DIALOG, TRUST_MOVE_DIR)],
                         ["trust_add_dir", "trust_add_dir", "trust_add_dir", "trust_folder", None, None])

    def test_add_dir_trust_agent_start(self) -> None:
        """agent-start：claude 带 --add-dir 起来停在 "Adding a new directory" 信任框（真实样本）→ 先 down 把光标从
        "No, exit" 移到 "Yes, add it" 再 enter，等到 idle：返回 ok、startup_dialogs_handled 记 trust_add_dir"""
        self.starting(w1=ADD_DIR_AGENT)
        out = self.agent_start("w1")
        self.assertEqual(self.outcome(out), (True, "idle", ["trust_add_dir"]))
        self.assertEqual(self.keys_sent("w1"), [["down"], ["enter"]])
        args = self.herdr_state()["agents"]["w1"]["args"]
        self.assertEqual(args[args.index("--add-dir") + 1], str(self.state / "s1"))

    def test_add_dir_trust_orch_start(self) -> None:
        """orch-start：编排者停在同一个信任框 → 同样 down 再 enter、等到 idle，之后照常发开场 prompt（在按键之后）"""
        command = self.home / ".claude" / "commands" / "drover.md"
        command.parent.mkdir(parents=True)
        command.write_text("# drover\n")
        request = self.tmp / "request.md"
        request.write_text("# 请求\n\n用户原话：测试\n")
        state = self.herdr_state()
        state["workspaces"] = [{"label": "orch", "workspace_id": "w9"}]
        self.set_herdr_state(state)
        self.starting(**{"orch-t1": ADD_DIR_AGENT})
        out = self.run_command("orch-start", "--slug", "t1", "--name", "orch-t1", "--request-file", str(request),
                               "--kind", "claude")
        self.assertEqual(self.outcome(out), (True, "idle", ["trust_add_dir"]))
        self.assertEqual(out["kickoff_sent"]["outcome"], "started")
        self.assertEqual(self.keys_sent("orch-t1"), [["down"], ["enter"]])
        prompts = self.herdr_state()["prompts"]
        self.assertEqual([name for name, _ in prompts], ["orch-t1"])
        self.assertTrue(prompts[0][1].startswith("你是任务 t1 的「编排者」"), prompts[0][1])
        calls = [c[:2] for c in self.herdr_calls()]
        last_key = max(i for i, c in enumerate(calls) if c == ["agent", "send-keys"])
        self.assertGreater(calls.index(["agent", "prompt"]), last_key)

    def test_folder_trust_still_handled(self) -> None:
        """原来的 "Do you trust the files in this folder?"（光标已在 "Yes, proceed"）照样认成 trust_folder：不按 down，直接 enter"""
        self.starting(w1={"status": "blocked", "agent": "claude", "screen": TRUST_FOLDER_LEGACY,
                          "on_keys": {"enter": {"status": "idle", "screen": IDLE_AFTER_START}}})
        out = self.agent_start("w1")
        self.assertEqual(self.outcome(out), (True, "idle", ["trust_folder"]))
        self.assertEqual(self.keys_sent("w1"), [["enter"]])

    def test_unknown_screen_blocked_no_keys(self) -> None:
        """认不出的界面照样返回 blocked、不按任何键：Bash 确认框（真实样本，光标在 "1. Yes"）、
        "Moving to a new directory:"（问句和 --add-dir 的信任框一样，标题不同）"""
        for name, screen in (("u1", BASH_DIALOG), ("u2", TRUST_MOVE_DIR)):
            with self.subTest(name):
                self.starting(**{name: {"status": "blocked", "agent": "claude", "screen": screen}})
                out = self.agent_start(name)
                self.assertEqual(self.outcome(out), (False, "blocked", []))
                self.assertIn("不认识的界面", out["error"])
                self.assertEqual(self.keys_sent(name), [])
                self.assertIn(name, self.herdr_state()["agents"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
