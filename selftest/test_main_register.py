"""drover main-register：把主会话在 herdr 里登记成 drover（herdr-oncall 插件按这个名字认主会话）。

- 无名 → 改名为 drover；已经叫 drover → 什么都不做；别的 pane 已经叫 drover → 不动并报出；自己叫别的名字 → 不改；
- 在编排者的 orch workspace 里两种模式都拒绝（编排者的工作目录也是主工作台）；
- --hook（Claude Code 的 SessionStart hook）：工作目录不是主工作台就直接结束（不调 herdr）；先等 herdr 里的
  agent_session 对上 stdin 的 session_id 再判断（/clear 后 herdr 过一会儿才换 session、清名字，探针实测）；
  任何情况退出码都是 0、stdout 为空。
- 认出主会话后记两样：main-rules.json（载入时的 drover 区块，drover rules 拿它比）、
  登记上了再写 main-session.json（session_id、pane_id、source，rules --hook、watch --main、drover rollup 用）；--hook 和
  Codex 记，Claude Code 第一条消息时的兜底登记只在记录不是这个 pane 时补写（session_id 写 null）；编排者、没登记上的不记。
假 herdr 的 state["panes"] 模拟按 pane 登记的 agent，pending 模拟 herdr 换 session 的延迟（按 agent get 的次数，不按时间）。
等 session 的上限（DROVER_MAIN_REGISTER_WAIT）给得宽：要轮询几次才对上的用例，机器再忙也不会先撞上限；
本来就要等到上限的两个用例（herdr 拿不到 session、一直没有 agent）单独设成 0.3 秒。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import unittest

from selftest_support import DroverTestCase

PANE = "w1:p1"
SHORT_WAIT = "0.3"  # 等到上限的用例：至少再查一次（轮询间隔 0.5 秒）就到上限


class MainRegisterTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.env.update(HERDR_ENV="1", HERDR_PANE_ID=PANE, HERDR_WORKSPACE_ID="w1", DROVER_MAIN_REGISTER_WAIT="30")
        self.set_panes({PANE: {"session": "s1"}})

    def set_panes(self, panes: dict) -> None:
        state = self.herdr_state()
        state["panes"] = panes
        state["workspaces"] = [{"workspace_id": "w1", "label": "work"}, {"workspace_id": "w9", "label": "orch"}]
        self.set_herdr_state(state)

    def pane(self, pane_id: str = PANE) -> dict:
        return self.herdr_state()["panes"][pane_id]

    def renames(self) -> list:
        return [c for c in self.herdr_calls() if c[:2] == ["agent", "rename"]]

    def hook(self, session: str = "s1", cwd=None, source: str = "startup", input_text=None):
        """跑 --hook 模式，断言退出码 0、stdout 为空，返回进程结果。"""
        payload = input_text if input_text is not None else json.dumps(
            {"session_id": session, "cwd": str(cwd or self.work), "hook_event_name": "SessionStart", "source": source})
        p = self.drover("main-register", "--hook", rc=None, cwd=cwd or self.work, input=payload, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout, "")
        return p

    # ---- 不带 --hook

    def test_unnamed_is_renamed(self) -> None:
        """当前 agent 没有名字：改名为 drover"""
        out = self.drover_json("main-register", cwd=self.work)
        self.assertEqual((out["ok"], out["action"], out["pane_id"], out["name"]), (True, "renamed", PANE, "drover"))
        self.assertEqual(self.renames(), [["agent", "rename", PANE, "drover"]])
        self.assertEqual(self.pane()["name"], "drover")

    def test_already_drover_is_noop(self) -> None:
        """已经叫 drover：什么都不做，不调 rename"""
        self.set_panes({PANE: {"session": "s1", "name": "drover"}})
        out = self.drover_json("main-register")
        self.assertEqual((out["ok"], out["action"]), (True, "already"))
        self.assertEqual(self.renames(), [])

    def test_name_taken_by_other_pane(self) -> None:
        """别的 pane 已经叫 drover（开了第二个主会话）：不动，报出那个 pane"""
        self.set_panes({PANE: {"session": "s1"}, "w2:p1": {"session": "s2", "name": "drover"}})
        out = self.drover_json("main-register", rc=1)
        self.assertFalse(out["ok"])
        self.assertEqual((out["reason"], out["conflict_pane"]), ("conflict", "w2:p1"))
        self.assertIn("w2:p1", out["error"])
        self.assertEqual(self.renames(), [])
        self.assertNotIn("name", self.pane())

    def test_named_agent_not_renamed(self) -> None:
        """自己已经有别的名字（比如 worker）：不改名"""
        self.set_panes({PANE: {"session": "s1", "name": "hca-e2e"}})
        out = self.drover_json("main-register", rc=1)
        self.assertEqual((out["reason"], out["current_name"]), ("named", "hca-e2e"))
        self.assertEqual(self.renames(), [])

    def test_orch_workspace_refused(self) -> None:
        """在编排者的 orch workspace 里：不带 --hook 也拒绝（编排者被 /clear 清掉名字后也像没名字的主会话）"""
        self.env["HERDR_WORKSPACE_ID"] = "w9"
        self.set_panes({PANE: {"session": "s1", "workspace_id": "w9"}})
        out = self.drover_json("main-register", rc=1, cwd=self.work)
        self.assertEqual(out["reason"], "orch_workspace")
        self.hook(cwd=self.work)
        self.assertEqual(self.renames(), [])
        self.assertNotIn("name", self.pane())

    def test_not_in_herdr(self) -> None:
        self.env.pop("HERDR_ENV")
        out = self.drover_json("main-register", rc=1)
        self.assertEqual(out["reason"], "not_in_herdr")
        self.assertEqual(self.herdr_calls(), [])

    # ---- --hook

    def test_hook_registers_main_session(self) -> None:
        """SessionStart（startup）：工作目录是主工作台、session 对上、没名字 → 改名，stdout 为空"""
        self.hook()
        self.assertEqual(self.pane()["name"], "drover")
        self.assertEqual(self.renames(), [["agent", "rename", PANE, "drover"]])

    def test_hook_waits_for_new_session_after_clear(self) -> None:
        """/clear：hook 刚跑时 herdr 里还是旧 session、旧名字 drover，下一次查才换成新 session 并清掉名字。
        要等 session 对上再判断：否则会把旧名字当成已登记，随后名字被清掉，主会话就丢了名字"""
        self.set_panes({PANE: {"session": "old", "name": "drover", "pending": [{}, {"session": "new", "name": None}]}})
        self.hook(session="new", source="clear")
        self.assertEqual(self.pane()["name"], "drover")
        self.assertEqual(self.pane()["session"], "new")
        self.assertEqual(self.renames(), [["agent", "rename", PANE, "drover"]])
        gets = [c for c in self.herdr_calls() if c[:2] == ["agent", "get"]]
        self.assertGreaterEqual(len(gets), 2)

    def test_hook_waits_until_agent_appears(self) -> None:
        """手动起的会话：hook 刚跑时 herdr 里还没有这个 agent（agent_not_found），等它出现再登记"""
        self.set_panes({PANE: {"session": "s1", "present": False, "pending": [{}, {}, {"present": True}]}})
        self.hook()
        self.assertEqual(self.pane()["name"], "drover")

    def test_hook_without_herdr_session_still_registers(self) -> None:
        """没装 herdr 的状态 hook（herdr 拿不到 agent_session）：等到上限后按现状判断，照样登记"""
        self.env["DROVER_MAIN_REGISTER_WAIT"] = SHORT_WAIT
        self.set_panes({PANE: {}})
        self.hook(session="s9")
        self.assertEqual(self.pane()["name"], "drover")

    def test_hook_skips_outside_workspace(self) -> None:
        """工作目录不是主工作台（worker 的 worktree、别处开的会话）：直接结束，一次 herdr 都不调"""
        other = self.tmp / "elsewhere"
        other.mkdir()
        self.hook(cwd=other)
        self.assertEqual(self.herdr_calls(), [])
        self.assertNotIn("name", self.pane())

    def test_hook_accepts_symlinked_workspace(self) -> None:
        """工作目录按真实路径比：经软链进的主工作台也算"""
        link = self.tmp / "ws-link"
        os.symlink(self.work, link)
        self.hook(cwd=link)
        self.assertEqual(self.pane()["name"], "drover")

    def test_hook_exit_zero_in_every_failure(self) -> None:
        """--hook 任何情况退出码都是 0、stdout 为空：不在 herdr、冲突、herdr 里一直没有这个 agent、herdr 命令找不到、
        stdin 不是 JSON、没有 config.json"""
        self.env["DROVER_MAIN_REGISTER_WAIT"] = SHORT_WAIT  # 一直没有 agent、herdr 命令找不到这两条要等到上限
        self.set_panes({PANE: {"session": "s1"}, "w2:p1": {"name": "drover"}})
        self.hook()  # 冲突
        self.assertEqual(self.renames(), [])

        self.set_panes({PANE: {"session": "s1", "present": False}})
        self.hook()  # 一直没有 agent：等到上限

        self.set_panes({PANE: {"session": "s1"}})
        self.hook(input_text="不是 JSON")  # 没有 session_id、cwd 用进程的工作目录（主工作台）：照样登记
        self.assertEqual(self.pane()["name"], "drover")

        saved_path = self.env["PATH"]
        # 去掉所有带 herdr 的目录（不只是假 herdr 的）：绝不能让真 herdr 连上本机 server、改到真实 pane
        self.env["PATH"] = os.pathsep.join(d for d in saved_path.split(os.pathsep)
                                           if d and not os.path.exists(os.path.join(d, "herdr")))
        self.assertIsNone(shutil.which("herdr", path=self.env["PATH"]))
        self.set_panes({PANE: {"session": "s1"}})
        self.hook()  # 找不到 herdr 命令
        self.env["PATH"] = saved_path

        env_backup = dict(self.env)
        self.env.pop("HERDR_ENV")
        self.hook()  # 不在 herdr
        self.env.clear()
        self.env.update(env_backup)

        (self.state / "config.json").unlink()
        self.hook()  # 没有主工作台
        self.assertNotIn("name", self.pane())

    # ---- 主会话的记录：main-rules.json（载入时的规则）、main-session.json

    BLOCK = "<!-- drover:routing:begin -->\n# drover 路由规则\n- 第一条\n<!-- drover:routing:end -->"

    def write_block(self, block: str = BLOCK) -> None:
        (self.work / "AGENTS.md").write_text("# 个人规则\n\n" + block + "\n")

    def record(self, name: str) -> dict:
        path = self.state / name
        return json.loads(path.read_text()) if path.exists() else {}

    def test_hook_records_rules_and_session(self) -> None:
        """--hook 登记上了：main-rules.json 记下载入时的区块（digest、text），main-session.json 记 session_id、pane_id、
        source、at；/clear 后换成新的；已经叫 drover 也写；stdout 照旧为空（hook() 断言）"""
        self.write_block()
        self.hook(session="s1", source="startup")
        rules = self.record("main-rules.json")
        self.assertEqual((rules["text"], rules["digest"]), (self.BLOCK, hashlib.sha1(self.BLOCK.encode()).hexdigest()))
        session = self.record("main-session.json")
        self.assertEqual({k: session[k] for k in ("session_id", "pane_id", "source")},
                         {"session_id": "s1", "pane_id": PANE, "source": "startup"})
        self.assertIsInstance(session["at"], float)

        self.set_panes({PANE: {"session": "s2"}})
        self.hook(session="s2", source="clear")
        self.assertEqual((self.record("main-session.json")["session_id"], self.record("main-session.json")["source"]),
                         ("s2", "clear"))
        self.set_panes({PANE: {"session": "s3", "name": "drover"}})
        self.hook(session="s3", source="compact")
        self.assertEqual(self.record("main-session.json")["session_id"], "s3")

    def test_refused_session_not_recorded(self) -> None:
        """没登记上（别的 pane 已经是 drover）：不写 main-session.json；在 orch workspace 里（编排者）：两样都不记"""
        self.write_block()
        self.set_panes({PANE: {"session": "s1"}, "w2:p1": {"session": "s2", "name": "drover"}})
        self.hook(session="s1")
        self.assertEqual(self.record("main-session.json"), {})
        (self.state / "main-rules.json").unlink()
        self.env["HERDR_WORKSPACE_ID"] = "w9"
        self.set_panes({PANE: {"session": "s1", "workspace_id": "w9"}})
        self.hook(session="s1")
        self.drover_json("main-register", rc=1, cwd=self.work)
        self.assertEqual((self.record("main-rules.json"), self.record("main-session.json")), ({}, {}))

    def test_codex_register_records_null_session(self) -> None:
        """Codex 主会话（不带 --hook，pane 里是 codex）：记下载入时的规则，main-session.json 的 session_id、source 是 null"""
        self.write_block()
        self.set_panes({PANE: {"session": "c1", "agent": "codex"}})
        out = self.drover_json("main-register", cwd=self.work)
        self.assertEqual(out["action"], "renamed")
        self.assertEqual(self.record("main-rules.json")["text"], self.BLOCK)
        session = self.record("main-session.json")
        self.assertEqual((session["session_id"], session["source"], session["pane_id"]), (None, None, PANE))

    def test_claude_register_keeps_hook_records(self) -> None:
        """Claude Code 主会话第一条消息时的兜底 main-register（不带 --hook）：不动 hook 记的 main-rules.json、
        main-session.json（再记会把载入时盖成之后的版本、session_id 盖成 null）"""
        self.write_block()
        self.hook(session="s1")
        rules, session = self.record("main-rules.json"), self.record("main-session.json")
        self.write_block(self.BLOCK.replace("第一条", "第一条（改了）"))
        self.assertEqual(self.drover_json("main-register", cwd=self.work)["action"], "already")
        self.assertEqual((self.record("main-rules.json"), self.record("main-session.json")), (rules, session))

    def test_claude_register_records_other_pane(self) -> None:
        """Claude Code 的兜底 main-register：main-session.json 记的不是这个 pane（hook 没登记上，记录停在别的 pane 上一个会话），
        或者根本没有记录 → 补写这个 pane，session_id、source 写 null（drover rollup 按 pane_id 认主会话）；main-rules.json 照旧不记"""
        self.write_block()
        (self.state / "main-session.json").write_text(
            json.dumps({"session_id": "old", "pane_id": "w1:p9", "source": "startup", "at": 1.0}))
        self.assertEqual(self.drover_json("main-register", cwd=self.work)["action"], "renamed")
        session = self.record("main-session.json")
        self.assertEqual((session["session_id"], session["source"], session["pane_id"]), (None, None, PANE))
        self.assertEqual(self.record("main-rules.json"), {})
        (self.state / "main-session.json").unlink()
        self.assertEqual(self.drover_json("main-register", cwd=self.work)["action"], "already")
        self.assertEqual(self.record("main-session.json")["pane_id"], PANE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
