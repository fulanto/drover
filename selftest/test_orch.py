"""编排者（orchestrator）相关：orch-start 的开场提示和入口检查、slug 保留名、orch-status、worker 会话名、旧名字残留；
宿主（--kind）不给时跟主会话走：orch-start 跟调用方、记 default_kind，agent-start 先用 default_kind 再用调用方。

drover 全面改过一次名：入口都叫 drover（/drover 命令、$drover 技能、~/.drover/bin/drover），
中间层叫编排者（orch），派发这个动作的名字（flow/dispatch.md、<slug>/dispatch.md、=== DISPATCH RESULT ===）不变；不留旧名字的兼容。
旧名字的正则用 [x] 字符类写：本文件自己也不出现旧名字的字面串，`git grep` 查残留时不会被它干扰。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import unittest
from typing import Optional

from selftest_screens import CODEX_QUOTE_WORKING, IDLE_AFTER_START, QUOTE_NAMED, SUBAGENT_DIALOG
from selftest_support import DROVER, DroverTestCase, rollup_digest

# 写完 rollup 还挂着 Monitor 的编排者（herdr 报 done，屏幕修正成 waiting-background(1)）
MONITOR = """⏺ 两个 worker 都在干活，等 monitor 的事件。

✻ Baked for 11m 22s · done 12:44 AM · 1 monitor still running
────────────────
❯
────────────────
  ⏵⏵ accept edits on · 1 monitor · ← 1 agent"""

# 开场提示和 CLI 里都不能再出现的旧名字（旧 CLI、旧状态目录、旧命令 / 技能入口、旧角色名）。
OLD_NAME_RE = re.compile(r"c[o]ord|协[调]者|Orchestr[a]tor|\$d[i]spatch|d[i]spatch 技能|Skill 工具调用 d[i]spatch"
                         r"|\.herdr/d[i]spatch|bin/d[i]spatch|skills/d[i]spatch|commands/d[i]spatch|Skill\(d[i]spatch\)"
                         r"|D[i]spatchError|D[I]SPATCH_HOME|D[I]SPATCH_BIN")
# 改名时特意不改的：派发这个动作的名字（flow/dispatch.md、<slug>/dispatch.md、=== DISPATCH RESULT ===）。bin/drover 里的 dispatch 只能是这些。
KEEP_DISPATCH_RE = re.compile(r"dispatch\.md|DISPATCH_DOC|=== DISPATCH RESULT ===|# dispatch: ")
RESERVED = ("pkg", "src", "bin", "backups", "baselines", "locks", "oncall-images")


def set_caller(case: DroverTestCase, agent: Optional[str], pane: str = "w0:p1") -> None:
    """调用方（跑 drover 的那个 pane）：HERDR_PANE_ID 设成 pane，假 herdr 在这个 pane 里放一个类型为 agent 的会话；
    agent 为 None 时 pane 里没有 agent（herdr agent get 报 agent_not_found）。"""
    case.env["HERDR_PANE_ID"] = pane
    state = case.herdr_state()
    state["panes"] = {pane: {"agent": agent} if agent else {"present": False}}
    case.set_herdr_state(state)


def kinds(out: dict) -> tuple:
    return out["kind"], out["kind_source"], out["default_kind"]


class OrchStartTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.request = self.tmp / "request.md"
        self.request.write_text("# 请求\n\n用户原话：测试\n")
        self.claude_command = self.home / ".claude" / "commands" / "drover.md"
        self.codex_skill = self.home / ".codex" / "skills" / "drover" / "SKILL.md"

    def install_entries(self, claude: bool = True, codex: bool = True) -> None:
        """在临时 HOME 里放 install.py 装的两个入口（内容随便，orch-start 只看文件在不在）。"""
        for path, wanted in ((self.claude_command, claude), (self.codex_skill, codex)):
            if wanted:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("---\nname: drover\n---\n")

    def orch_start(self, slug: str, *extra: str, rc: int = 0) -> dict:
        return self.drover_json("orch-start", "--slug", slug, "--name", f"orch-{slug}"[:32],
                                "--request-file", str(self.request), "--dry-run", *extra, rc=rc)

    def assert_no_old_names(self, out: dict) -> None:
        text = out["kickoff"] + "\n" + "\n".join(out["planned"])
        self.assertIsNone(OLD_NAME_RE.search(text), OLD_NAME_RE.findall(text))

    def test_claude_kickoff_new_names(self) -> None:
        """claude 编排者：编排者称呼、Skill 工具调用 drover、--orch <slug>、编排者模式；会话名 orch-<slug>；workspace label orch"""
        self.install_entries(codex=False)
        out = self.orch_start("t1", "--kind", "claude", "--args", "@shop --review")
        kickoff = out["kickoff"]
        self.assertTrue(kickoff.startswith("你是任务 t1 的「编排者」（orchestrator）"), kickoff)
        self.assertIn("然后用 Skill 工具调用 drover，参数是：--orch t1 @shop --review", kickoff)
        self.assertIn("接手之前的编排者：先读 ~/.drover/t1/dispatch.md 和 rollup.md", kickoff)
        self.assertIn("按 dispatch.md 的「编排者模式」一节运行", kickoff)
        self.assertIn("不要用向用户提问的工具（AskUserQuestion）", kickoff)
        self.assertIn(str(self.state / "t1" / "rollup.md"), kickoff)
        self.assertEqual(out["planned"][0], f"herdr workspace create --cwd {self.work} --label orch --no-focus")
        self.assertIn(f"--name orch-t1 --add-dir {self.state}", out["planned"][2])
        self.assert_no_old_names(out)
        self.assertFalse((self.state / "t1").exists())  # dry-run 不建任务目录

    def test_codex_kickoff_new_names(self) -> None:
        """codex 编排者：$drover 技能和它的 SKILL.md 路径、流程文档路径、追踪用 ~/.drover/bin/drover watch"""
        self.install_entries(claude=False)
        out = self.orch_start("t2", "--kind", "codex")
        kickoff = out["kickoff"]
        self.assertIn(f"然后使用 drover 技能（$drover，文件 {self.codex_skill}），它会让你完整读 "
                      f"{DROVER.parent.parent / 'flow' / 'dispatch.md'} 并照做；流程里的 $ARGUMENTS 就是：--orch t2", kickoff)
        self.assertIn("「编排者」", kickoff)
        self.assertIn("「编排者模式」", kickoff)
        self.assertIn("`~/.drover/bin/drover watch --until-event --timeout 1500 <agent名>...`", kickoff)
        self.assertIn("request_user_input / request_user_input_async", kickoff)
        self.assertIn("-s danger-full-access", out["planned"][2])
        self.assert_no_old_names(out)

    def test_sub_orch_kickoff_new_names(self) -> None:
        """子编排者（claude、codex 都试）：子编排者 / 上层编排者、--orch <slug> --parent <上层>、拆出去的 flow/sub-orch.md"""
        self.install_entries()
        (self.state / "t3").mkdir()
        (self.state / "t3" / "dispatch.md").write_text("# dispatch: t3\n")
        for kind in ("claude", "codex"):
            with self.subTest(kind):
                out = self.orch_start("t3-shop", "--kind", kind, "--parent", "t3")
                kickoff = out["kickoff"]
                self.assertTrue(kickoff.startswith("你是任务 t3-shop 的「子编排者」，由上层编排者（任务 t3，"
                                                   "状态文件 ~/.drover/t3/dispatch.md）派来"), kickoff)
                self.assertIn("--orch t3-shop --parent t3", kickoff)
                self.assertIn("接手之前的子编排者", kickoff)
                self.assertIn("先读 ~/.drover/pkg/flow/sub-orch.md", kickoff)
                self.assertIn("等上层编排者转达答复", kickoff)
                self.assertIn("Skill 工具调用 drover" if kind == "claude" else "$drover", kickoff)
                self.assert_no_old_names(out)

    def test_claude_requires_drover_command(self) -> None:
        """claude 编排者要 ~/.claude/commands/drover.md：没有（或是断掉的软链）就报错让先跑 install.py --host claude，不调 herdr"""
        self.install_entries(claude=False)
        self.claude_command.parent.mkdir(parents=True)
        for state in ("missing", "dangling"):
            with self.subTest(state):
                if state == "dangling":
                    self.claude_command.symlink_to(self.tmp / "gone" / "drover.md")
                out = self.orch_start("t4", "--kind", "claude", rc=1)
                self.assertIn(str(self.claude_command), out["error"])
                self.assertIn("/drover 命令", out["error"])
                self.assertIn("install.py --host claude", out["error"])
        self.assertEqual(self.herdr_calls(), [])
        self.assertFalse((self.state / "t4").exists())

    def test_codex_requires_drover_skill(self) -> None:
        """codex 编排者要 ~/.codex/skills/drover/SKILL.md：没有就报错让先跑 install.py --host codex"""
        self.install_entries(codex=False)
        out = self.orch_start("t5", "--kind", "codex", rc=1)
        self.assertIn(str(self.codex_skill), out["error"])
        self.assertIn("drover 技能", out["error"])
        self.assertIn("install.py --host codex", out["error"])
        self.assertEqual(self.herdr_calls(), [])

    def test_reuses_orch_workspace(self) -> None:
        """已有 label 为 orch 的 workspace 就在里面新开 tab；别的 label（比如 drover）不算"""
        d = self.load_drover()
        for workspaces, want in (([{"label": "drover", "workspace_id": "w1"}, {"label": "orch", "workspace_id": "w9"}],
                                  ("w9", None, [])),
                                 ([{"label": "drover", "workspace_id": "w1"}],
                                  (None, None, [f"herdr workspace create --cwd {self.work} --label orch --no-focus"]))):
            d.herdr = lambda args, timeout=None, ws=workspaces: (True, {"workspaces": ws})
            self.assertEqual(d.orch_workspace(True), want)

    def test_writes_orch_json(self) -> None:
        """真起编排者（herdr、启动、发 prompt 都换成假的）：任务目录里写 orch.json；子编排者再往上层状态文件追加一行"""
        self.install_entries()
        d = self.load_drover()
        d.herdr = lambda args, timeout=None: (
            (True, {"workspaces": [{"label": "orch", "workspace_id": "w9"}]}) if args[:2] == ["workspace", "list"] else
            (True, {"root_pane": {"pane_id": "w9:p2"}, "tab": {"tab_id": "w9:t2"}}) if args[:2] == ["tab", "create"] else
            (False, {"code": "unexpected", "message": " ".join(args)}))
        started = []
        d.start_agent = lambda name, kind, pane, agent_args, dry_run: started.append((name, pane, agent_args)) or {"ok": True}
        d.send_prompt = lambda name, text, timeout_ms: {"ok": True, "outcome": "settled"}
        (self.state / "t6").mkdir()
        (self.state / "t6" / "dispatch.md").write_text("# dispatch: t6\n")
        for slug, parent in (("t6", None), ("t6-shop", "t6")):
            res = d.cmd_orch_start(argparse.Namespace(slug=slug, name=f"orch-{slug}", kind="claude", request_file=str(self.request),
                                                      args=None, effort=None, model=None, parent=parent, dry_run=False))
            self.assertTrue(res["ok"], res)
            meta = json.loads((self.state / slug / "orch.json").read_text())
            self.assertEqual((meta["slug"], meta["name"], meta["kind"], meta["default_kind"], meta["parent"],
                              meta["tab_id"], meta["pane_id"]),
                             (slug, f"orch-{slug}", "claude", "claude", parent, "w9:t2", "w9:p2"))
            self.assertEqual(sorted(p.name for p in (self.state / slug).glob("*.json")),
                             ["orch.json", "parent.json"] if parent else ["orch.json"])
        self.assertIn("--name", started[0][2])
        self.assertEqual(started[0][2][started[0][2].index("--name") + 1], "orch-t6")
        self.assertIn("子编排者 orch-t6-shop（任务 t6-shop，tab w9:t2，pane w9:p2）",
                      (self.state / "t6" / "dispatch.md").read_text())

    # ---- 宿主：不给 --kind 时跟主会话走（主会话是 Codex 时，派出的编排者和 worker 仍是 Claude Code）

    def test_orch_start_reports_resume_command(self) -> None:
        """orch-start 起好就把续命令报给 herdr（编排者的启动参数 + --resume <herdr 报的会话 ID>，herdr 重启后按它拉回），
        记进 orch.json 的 resume_reported_session；codex 编排者第一轮之前 herdr 没有会话 ID：不报、返回里写原因（后台维护补报），
        orch.json 里是 null。子编排者走同一条路"""
        self.install_entries()
        state = self.herdr_state()
        state["workspaces"] = [{"label": "orch", "workspace_id": "w9"}]
        state["starting"] = {"orch-t1": {"status": "idle", "agent": "claude", "screen": IDLE_AFTER_START},
                             "orch-t2": {"status": "idle", "agent": "codex", "screen": "› Ask Codex to do anything"}}
        self.set_herdr_state(state)

        def orch_start(slug: str, kind: str) -> dict:
            p = self.drover_in_process("orch-start", "--slug", slug, "--name", f"orch-{slug}", "--kind", kind,
                                       "--request-file", str(self.request))
            return json.loads(p.stdout)

        out = orch_start("t1", "claude")
        agents = self.herdr_state()["agents"]
        want = ["claude", *agents["orch-t1"]["args"], "--resume", "fake-session-orch-t1"]
        self.assertIn(f"--add-dir {self.state}", " ".join(want))
        self.assertEqual(out["herdr_resume"], {"reported": True, "session_id": "fake-session-orch-t1", "argv": want})
        self.assertEqual(agents["orch-t1"]["resume_argv"], want)
        meta = json.loads((self.state / "t1" / "orch.json").read_text())
        self.assertEqual((meta["name"], meta["resume_reported_session"]), ("orch-t1", "fake-session-orch-t1"))
        out = orch_start("t2", "codex")
        self.assertEqual((out["ok"], out["herdr_resume"]["reported"]), (True, False))
        self.assertIn("会话 ID", out["herdr_resume"]["reason"])
        self.assertIsNone(json.loads((self.state / "t2" / "orch.json").read_text())["resume_reported_session"])
        self.assertEqual([r["pane"] for r in self.herdr_state()["resume_reports"]], [agents["orch-t1"]["pane_id"]])

    def test_kind_follows_caller(self) -> None:
        """不给 --kind：编排者跟调用方（主会话，HERDR_PANE_ID 那个 pane）里的 agent 走，claude、codex 各一次；
        default_kind（任务里 worker 的默认宿主）也是它，kind_source 是 caller"""
        self.install_entries()
        for kind in ("claude", "codex"):
            with self.subTest(kind):
                set_caller(self, kind)
                out = self.orch_start("t1")
                self.assertEqual(kinds(out), (kind, "caller", kind))
                self.assertIn(f"herdr agent start orch-t1 --kind {kind} --pane", out["planned"][2])
                self.assertIn("Skill 工具调用 drover" if kind == "claude" else "$drover", out["kickoff"])
        self.assertIn(["agent", "get", "w0:p1"], self.herdr_calls())

    def test_explicit_kind_keeps_caller_default(self) -> None:
        """显式 --kind 只管编排者：主会话是 claude、--kind codex → 编排者 codex（explicit），worker 默认仍是主会话的 claude"""
        self.install_entries()
        set_caller(self, "claude")
        out = self.orch_start("t2", "--kind", "codex")
        self.assertEqual(kinds(out), ("codex", "explicit", "claude"))
        self.assertIn("-s danger-full-access", out["planned"][2])

    def test_sub_orch_inherits_default_kind(self) -> None:
        """子编排者：default_kind 取上层 orch.json 的（主会话的宿主），不取调用方（上层编排者）；上层 orch.json 没有这个字段
        （旧版起的）时用调用方；编排者本身不给 --kind 时跟调用方，给了照用"""
        self.install_entries()
        (self.state / "t3").mkdir()
        (self.state / "t3" / "dispatch.md").write_text("# dispatch: t3\n")
        parent_meta = self.state / "t3" / "orch.json"
        set_caller(self, "claude")  # 主会话是 codex，用户写了 --orch claude：上层编排者跑在 claude 里
        for meta, want in (({"slug": "t3", "kind": "claude", "default_kind": "codex"}, ("claude", "caller", "codex")),
                           ({"slug": "t3", "kind": "claude"}, ("claude", "caller", "claude"))):
            with self.subTest(meta):
                parent_meta.write_text(json.dumps(meta))
                self.assertEqual(kinds(self.orch_start("t3-shop", "--parent", "t3")), want)
        parent_meta.write_text(json.dumps({"slug": "t3", "kind": "claude", "default_kind": "codex"}))
        out = self.orch_start("t3-shop", "--parent", "t3", "--kind", "codex")
        self.assertEqual(kinds(out), ("codex", "explicit", "codex"))

    def test_unknown_caller_needs_kind(self) -> None:
        """认不出调用方（不在 herdr pane 里、pane 里没有 agent、agent 不是 claude / codex）又没给 --kind → 报错让加 --kind，
        不悄悄回落成 claude；给了 --kind 照常，default_kind 取 --kind"""
        self.install_entries()
        for case in ("no_pane", "no_agent", "other_agent"):
            with self.subTest(case):
                if case != "no_pane":
                    set_caller(self, None if case == "no_agent" else "opencode")
                out = self.orch_start("t4", rc=1)
                self.assertIn("认不出调用方的宿主", out["error"])
                self.assertIn("加 --kind claude|codex", out["error"])
                self.assertEqual(kinds(self.orch_start("t4", "--kind", "codex")), ("codex", "explicit", "codex"))
        self.assertFalse((self.state / "t4").exists())

    def test_orch_json_records_default_kind(self) -> None:
        """真起（herdr、启动、发 prompt 换成假的）：主会话 codex 不给 --kind 起顶层编排者 → orch.json 的 kind、default_kind
        都是 codex；上层编排者（claude）不给 --kind 起子编排者 → kind claude，default_kind 仍是 codex（取上层 orch.json）"""
        self.install_entries()
        d = self.load_drover()
        hosts = {"w0:p1": "codex", "w9:p2": "claude"}  # 主会话、上层编排者所在的 pane
        d.herdr = lambda args, timeout=None: (
            (True, {"workspaces": [{"label": "orch", "workspace_id": "w9"}]}) if args[:2] == ["workspace", "list"] else
            (True, {"root_pane": {"pane_id": "w9:p2"}, "tab": {"tab_id": "w9:t2"}}) if args[:2] == ["tab", "create"] else
            (True, {"agent": {"agent": hosts[args[2]]}}) if args[:2] == ["agent", "get"] else
            (False, {"code": "unexpected", "message": " ".join(args)}))
        started = []
        d.start_agent = lambda name, kind, pane, agent_args, dry_run: started.append(kind) or {"ok": True}
        d.send_prompt = lambda name, text, timeout_ms: {"ok": True, "outcome": "settled"}
        (self.state / "t7").mkdir()
        (self.state / "t7" / "dispatch.md").write_text("# dispatch: t7\n")
        for slug, parent, caller, want in (("t7", None, "w0:p1", ("codex", "codex")),
                                           ("t7-shop", "t7", "w9:p2", ("claude", "codex"))):
            os.environ["HERDR_PANE_ID"] = caller
            res = d.cmd_orch_start(argparse.Namespace(slug=slug, name=f"orch-{slug}", kind=None, request_file=str(self.request),
                                                      args=None, effort=None, model=None, parent=parent, dry_run=False))
            self.assertTrue(res["ok"], res)
            self.assertEqual(kinds(res), (want[0], "caller", want[1]))
            meta = json.loads((self.state / slug / "orch.json").read_text())
            self.assertEqual((meta["kind"], meta["default_kind"]), want)
        self.assertEqual(started, ["codex", "claude"])


class ReservedSlugTests(DroverTestCase):
    def test_reserved_slugs_rejected(self) -> None:
        """slug 不能是 ~/.drover 下的固定目录名（pkg、src、bin、backups、baselines、locks、oncall-images）：orch-start、agent-start、brief、
        integrate、review、collect 都拒绝，不建目录、不调 herdr；只是包含这些词的 slug（pkg-x）照常"""
        command = self.home / ".claude" / "commands" / "drover.md"
        command.parent.mkdir(parents=True)
        command.write_text("# drover\n")
        request = self.tmp / "request.md"
        request.write_text("# 请求\n")
        for slug in RESERVED:
            with self.subTest(slug):
                out = self.drover_json("orch-start", "--slug", slug, "--name", "orch-x", "--request-file", str(request),
                                       "--dry-run", rc=1)
                self.assertIn(f"slug {slug!r} 是保留名", out["error"])
        others = [
            ["agent-start", "--name", "w1", "--slug", "bin", "--pane", "w1:p1", "--dry-run"],
            ["brief", "--slug", "locks", "--name", "w1", "--repo", "x", "--wt", "x", "--deps-json", "x",
             "--task-file", "x", "--goal", "g", "--evidence", "e", "--dry-run"],
            ["integrate", "--repo", "x", "--slug", "baselines", "--target", "main", "--from", "b", "--dry-run"],
            ["review", "--slug", "pkg", "--name", "w1-rev", "--pane", "w1:p1", "--wt", "x", "--base", "HEAD", "--dry-run"],
            ["collect", "--slug", "src", "--name", "w1-rev", "--pane", "w1:p2"],
        ]
        for args in others:
            with self.subTest(args[0]):
                out = self.drover_json(*args, rc=1)
                self.assertIn("是保留名", out["error"])
        self.assertEqual(self.herdr_calls(), [])
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["config.json"])
        ok = self.drover_json("orch-start", "--slug", "pkg-x", "--name", "orch-x", "--request-file", str(request),
                              "--kind", "claude", "--dry-run")
        self.assertTrue(ok["ok"])


class OrchStatusTests(DroverTestCase):
    def orch(self, slug: str, name: str, status: str, parent: str = None, kind: str = "claude") -> None:
        d = self.state / slug
        d.mkdir(parents=True)
        (d / "orch.json").write_text(json.dumps({"slug": slug, "name": name, "kind": kind, "parent": parent,
                                                 "started": "11-06 09:00"}))
        (d / "rollup.md").write_text(f"# {slug}\n状态：{status}\n更新：11-06 10:00\n")

    def test_reads_orch_json(self) -> None:
        """orch-status 按 orch.json 列 orchestrators；需要换编排者 / 完成这些状态（未读）、agent 停下了 → needs_attention；
        orch- 开头却没有 orch.json 的活 agent 进 without_orch_json；子编排者要 --with-sub，退出的要 --all；
        退出了、rollup 又是完成的（收尾完关掉了）不进 needs_attention"""
        self.orch("t1", "orch-t1", "需要换编排者")
        self.orch("t2", "orch-t2", "进行中")
        self.orch("t2-shop", "orch-t2-shop", "等你决定", parent="t2")
        self.orch("t3", "orch-t3", "完成")  # agent 已退出
        self.set_agents(**{"orch-t1": {"status": "done"}, "orch-t2": {"status": "working"},
                           "orch-t2-shop": {"status": "idle"}, "orch-manual": {"status": "idle"},
                           "w1": {"status": "working"}})
        out = self.drover_json("orch-status")
        self.assertEqual(set(out), {"ok", "orchestrators", "without_orch_json", "needs_attention"})
        self.assertEqual([(r["slug"], r["name"], r["agent_status"], r["rollup_status"]) for r in out["orchestrators"]],
                         [("t1", "orch-t1", "done", "需要换编排者"), ("t2", "orch-t2", "working", "进行中")])
        self.assertEqual(out["needs_attention"], ["t1"])
        self.assertEqual([x["name"] for x in out["without_orch_json"]], ["orch-manual"])
        # 带"更新："行的旧 rollup 照样读（只是展示）；rollup_mtime 是文件修改时间
        self.assertEqual(out["orchestrators"][0]["rollup_updated"], "11-06 10:00")
        self.assertRegex(out["orchestrators"][0]["rollup_mtime"], r"^\d\d-\d\d \d\d:\d\d:\d\d$")
        full = self.drover_json("orch-status", "--all", "--with-sub")
        self.assertEqual([(r["slug"], r["agent_status"]) for r in full["orchestrators"]],
                         [("t1", "done"), ("t2", "working"), ("t2-shop", "idle"), ("t3", "gone")])
        self.assertEqual(full["needs_attention"], ["t1", "t2-shop"])

    def test_unread_summary_answered(self) -> None:
        """每行的 rollup_summary（"摘要："，没有就按状态从正文取）、unread（状态要处理、内容和
        drover rollup 读过的不同）、answered_after_rollup（drover prompt 转过答复、晚于 rollup）。needs_attention 只算未读的：
        读过就不算；按前缀匹配（"完成（已收尾）"也算，以前全等匹配漏了）；退出了的受阻算，退出了的完成不算；转过答复的不算"""
        rollups = {
            "t1": ("orch-t1", "等你决定", "摘要：要定 X\n\n## 需要你决定\n- 选 A 还是 B\n"),
            "t2": ("orch-t2", "完成（已收尾）", "\n## 结论\n目标：做看板\n- 推送了\n"),
            "t3": ("orch-t3", "完成", "\n## 结论\n- 旧任务\n"),
            "t4": ("orch-t4", "受阻", "\n## 结论\n- 卡在权限上\n"),
            "t5": ("orch-t5", "进行中", "\n## 当前进度\n- 读代码\n- 写自测\n"),
            "t6": ("orch-t6", "等你决定", "\n## 需要你决定\n- 已经转过答复\n"),
        }
        for slug, (name, status, body) in rollups.items():
            d = self.state / slug
            d.mkdir(parents=True)
            (d / "orch.json").write_text(json.dumps({"slug": slug, "name": name, "kind": "claude", "parent": None}))
            (d / "rollup.md").write_text(f"# {slug}\n状态：{status}\n{body}")
        self.main_rollup("--slug", "t6")
        (self.state / "t6" / "answered.json").write_text(json.dumps({"at": time.time() - 5,
                                                                     "rollup_digest": rollup_digest(self.state / "t6" / "rollup.md")}))
        self.set_agents(**{"orch-t1": {"status": "done"}, "orch-t2": {"status": "idle"}, "orch-t5": {"status": "working"},
                           "orch-t6": {"status": "done"}})
        out = self.drover_json("orch-status", "--all")
        rows = {r["slug"]: r for r in out["orchestrators"]}
        self.assertEqual({s: (r["rollup_summary"], r["unread"], r["answered_after_rollup"]) for s, r in rows.items()}, {
            "t1": ("要定 X", True, False), "t2": ("推送了", True, False), "t3": ("旧任务", True, False),
            "t4": ("卡在权限上", True, False), "t5": ("写自测", False, False), "t6": ("已经转过答复", False, True)})
        self.assertEqual(out["needs_attention"], ["t1", "t2", "t4"])
        self.drover("rollup", "--slug", "t1", env={"HERDR_ENV": "1", "HERDR_PANE_ID": "w2:p3"})  # 编排者读：不算主会话读过
        self.assertEqual(self.drover_json("orch-status", "--all")["needs_attention"], ["t1", "t2", "t4"])
        self.main_rollup("--slug", "t1")
        out = self.drover_json("orch-status", "--all")
        self.assertEqual(out["needs_attention"], ["t2", "t4"])
        self.assertFalse({r["slug"]: r for r in out["orchestrators"]}["t1"]["unread"])

    def test_unread_done_after_exit_needs_attention(self) -> None:
        """编排者写完"完成"就退出了、主会话还没读（orch-start 派发的，有时间线）→ 进 needs_attention；主会话读过这一版才算收尾完
        关掉的、不算；装上已读记录之前的老任务（没有时间线）算已读、不算"""
        self.orch("t1", "orch-t1", "完成")
        (self.state / "t1" / "timeline.jsonl").write_text(json.dumps({"at": time.time(), "event": "start"}) + "\n")
        self.orch("t2", "orch-t2", "完成")
        self.set_agents()
        self.assertEqual(self.drover_json("orch-status", "--all")["needs_attention"], ["t1"])
        self.main_rollup("--slug", "t1")
        self.assertEqual(self.drover_json("orch-status", "--all")["needs_attention"], [])

    def test_no_orch_json_no_rows(self) -> None:
        """一个 orch.json 都没有：orchestrators 为空，没有 without_orch_json 键（没有 orch- 开头的活 agent）"""
        self.set_agents(w1={"status": "working"})
        out = self.drover_json("orch-status")
        self.assertEqual(out, {"ok": True, "orchestrators": [], "needs_attention": []})

    def test_screen_status_and_blocked(self) -> None:
        """herdr 报 working、屏幕底部是后台子 agent 的确认框（真实屏幕）→ screen_status: blocked、进顶层 blocked，
        agent_status 仍是 herdr 的原值 working；herdr 自己报 blocked 的也进 blocked（不读屏幕）；done + 挂着 Monitor →
        screen_status: waiting-background(1)，needs_attention 仍按原值 done 算它；屏幕和原值一致的没有 screen_status；
        codex 编排者 working 时不读屏幕（引用的弹框文字会被误认）"""
        self.orch("t1", "orch-t1", "进行中")
        self.orch("t2", "orch-t2", "完成")
        self.orch("t3", "orch-t3", "完成")
        self.orch("t4", "orch-t4", "进行中")
        self.orch("t5", "orch-t5", "进行中", kind="codex")
        self.set_agents(**{"orch-t1": {"status": "working", "agent": "claude", "screen": SUBAGENT_DIALOG},
                           "orch-t2": {"status": "done", "screen": QUOTE_NAMED},
                           "orch-t3": {"status": "done", "screen": MONITOR},
                           "orch-t4": {"status": "blocked", "screen": SUBAGENT_DIALOG},
                           "orch-t5": {"status": "working", "agent": "codex", "screen": CODEX_QUOTE_WORKING}})
        out = self.drover_json("orch-status")
        self.assertEqual([(r["slug"], r["agent_status"], r.get("screen_status")) for r in out["orchestrators"]],
                         [("t1", "working", "blocked"), ("t2", "done", None), ("t3", "done", "waiting-background(1)"),
                          ("t4", "blocked", None), ("t5", "working", None)])
        self.assertEqual(out["blocked"], [{"slug": "t1", "name": "orch-t1"}, {"slug": "t4", "name": "orch-t4"}])
        self.assertEqual(out["needs_attention"], ["t2", "t3"])
        read = [c[2] for c in self.herdr_calls() if c[:2] == ["agent", "read"]]
        self.assertEqual(read, ["orch-t1", "orch-t2", "orch-t3"])

    def test_screen_read_failure_keeps_status(self) -> None:
        """读屏失败（herdr agent read 报错）不报错，按 herdr 的原值：没有 screen_status，也不进 blocked（没有这个键）"""
        self.orch("t1", "orch-t1", "进行中")
        self.orch("t2", "orch-t2", "完成")
        self.set_agents(**{"orch-t1": {"status": "working", "screen": SUBAGENT_DIALOG, "read_fail": True},
                           "orch-t2": {"status": "done", "screen": MONITOR, "read_fail": True}})
        out = self.drover_json("orch-status")
        self.assertEqual(set(out), {"ok", "orchestrators", "needs_attention"})
        self.assertEqual([(r["slug"], r["agent_status"], r.get("screen_status")) for r in out["orchestrators"]],
                         [("t1", "working", None), ("t2", "done", None)])
        self.assertEqual(out["needs_attention"], ["t2"])
        self.assertEqual(len([c for c in self.herdr_calls() if c[:2] == ["agent", "read"]]), 2)  # 确实去读了


class WorkerAgentStartTests(DroverTestCase):
    def test_worker_session_name(self) -> None:
        """worker 的 claude 会话名是 worker-<slug>（--pane 和 --split-from 两种）；codex worker 不带 --name"""
        out = self.drover_json("agent-start", "--name", "w1", "--kind", "claude", "--slug", "s1", "--pane", "w1:p1",
                               "--dry-run")
        self.assertIn(f"--name worker-s1 --add-dir {self.state / 's1'}", out["planned"][0])
        split = self.drover_json("agent-start", "--name", "w2", "--kind", "claude", "--slug", "s1", "--split-from", "w1:p1",
                                 "--wt", str(self.work), "--dry-run")
        self.assertIn("--name worker-s1", split["planned"][1])
        self.assertEqual((split["kind"], split["kind_source"]), ("claude", "explicit"))
        codex = self.drover_json("agent-start", "--name", "w3", "--kind", "codex", "--slug", "s1", "--pane", "w1:p1",
                                 "--dry-run")
        self.assertNotIn("--name", codex["planned"][0].split(" -- ", 1)[1])
        self.assertIsNone(OLD_NAME_RE.search(json.dumps([out, split, codex], ensure_ascii=False)))
        self.assertFalse((self.state / "s1").exists())  # dry-run 不建任务目录

    def test_kind_defaults_to_main_session(self) -> None:
        """不给 --kind 时 worker 用任务 orch.json 的 default_kind（主会话的宿主），不看调用方（编排者）；没有 orch.json
        （主会话直接编排）时跟调用方 pane；都认不出就报错让加 --kind；给了 --kind 照用"""
        dry = ("--pane", "w1:p1", "--dry-run")
        set_caller(self, "claude")  # 主会话是 codex、用户写了 --orch claude：编排者跑在 claude 里
        (self.state / "s2").mkdir()
        (self.state / "s2" / "orch.json").write_text(json.dumps({"slug": "s2", "kind": "claude", "default_kind": "codex"}))
        out = self.drover_json("agent-start", "--name", "w1", "--slug", "s2", *dry)
        self.assertEqual((out["kind"], out["kind_source"]), ("codex", "orch"))
        self.assertIn("herdr agent start w1 --kind codex --pane w1:p1", out["planned"][0])
        explicit = self.drover_json("agent-start", "--name", "w1", "--slug", "s2", "--kind", "claude", *dry)
        self.assertEqual((explicit["kind"], explicit["kind_source"]), ("claude", "explicit"))
        set_caller(self, "codex")  # 主会话直接编排（任务没有 orch.json），主会话是 codex
        direct = self.drover_json("agent-start", "--name", "w2", "--slug", "s3", *dry)
        self.assertEqual((direct["kind"], direct["kind_source"]), ("codex", "caller"))
        self.assertIn("herdr agent start w2 --kind codex --pane w1:p1", direct["planned"][0])
        del self.env["HERDR_PANE_ID"]
        out = self.drover_json("agent-start", "--name", "w2", "--slug", "s3", *dry, rc=1)
        self.assertIn("没有 default_kind", out["error"])
        self.assertIn("加 --kind claude|codex", out["error"])
        self.assertFalse((self.state / "s3").exists())


class ModelEffortConfigTests(DroverTestCase):
    """编排者、worker、复审的模型和 effort 可以在 config.json 配（claude_model / claude_effort / codex_effort）；
    没配时和以前完全一样（claude：opus[1m] + xhigh；codex：xhigh、不传模型），命令行的 --effort 优先于 config。"""

    def setUp(self) -> None:
        super().setUp()
        self.request = self.tmp / "request.md"
        self.request.write_text("# 请求\n")
        for path in (self.home / ".claude" / "commands" / "drover.md",
                     self.home / ".codex" / "skills" / "drover" / "SKILL.md"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\nname: drover\n---\n")

    def configure(self, **keys: str) -> None:
        config = self.state / "config.json"
        config.write_text(json.dumps({**json.loads(config.read_text()), **keys}))

    def agent_args(self, kind: str, *extra: str) -> str:
        """agent-start --dry-run 计划里 herdr agent start 的 -- 后面那段（交给 claude / codex 的参数）"""
        out = self.drover_json("agent-start", "--name", "w1", "--kind", kind, "--slug", "s1", "--pane", "w1:p1",
                               "--dry-run", *extra)
        return out["planned"][0].split(" -- ", 1)[1]

    def orch_args(self, kind: str, *extra: str) -> str:
        out = self.drover_json("orch-start", "--slug", "t1", "--name", "orch-t1", "--request-file", str(self.request),
                               "--kind", kind, "--dry-run", *extra)
        return next(p for p in out["planned"] if p.startswith("herdr agent start")).split(" -- ", 1)[1]

    def review_args(self) -> list:
        """review 起只读 codex 的参数（split_pane、start_agent 换成假的，只看传了什么）"""
        d = self.load_drover()
        d.split_pane = lambda pane, wt: "w1:p9"
        d.start_agent = lambda name, kind, pane, args, dry_run: {"ok": True, "args": args}
        return d.spawn_reviewer("w1:p1", str(self.work), "rev1")["args"]

    def test_defaults_unchanged(self) -> None:
        """config 没配：claude 是 --model 'opus[1m]' --effort xhigh，codex 是 -c model_reasoning_effort=xhigh、不带 --model"""
        self.assertTrue(self.agent_args("claude").startswith(
            "--model 'opus[1m]' --effort xhigh --permission-mode acceptEdits --name worker-s1 "))
        self.assertTrue(self.orch_args("claude").startswith(
            "--model 'opus[1m]' --effort xhigh --permission-mode acceptEdits --name orch-t1 "))
        for args in (self.agent_args("codex"), self.orch_args("codex")):
            self.assertTrue(args.endswith(" -c model_reasoning_effort=xhigh"), args)
            self.assertNotIn("--model", args)
        self.assertEqual(self.review_args()[-2:], ["-c", "model_reasoning_effort=xhigh"])

    def test_config_used(self) -> None:
        """config 配了就用配的：worker、编排者、复审都认"""
        self.configure(claude_model="sonnet", claude_effort="high", codex_effort="medium")
        self.assertTrue(self.agent_args("claude").startswith("--model sonnet --effort high --permission-mode acceptEdits "))
        self.assertTrue(self.orch_args("claude").startswith("--model sonnet --effort high --permission-mode acceptEdits "))
        for args in (self.agent_args("codex"), self.orch_args("codex")):
            self.assertTrue(args.endswith(" -c model_reasoning_effort=medium"), args)
            self.assertNotIn("sonnet", args)
        self.assertEqual(self.review_args()[-2:], ["-c", "model_reasoning_effort=medium"])
        # 只配模型：effort 照旧是默认值
        self.configure(claude_effort="", codex_effort="")
        self.assertTrue(self.agent_args("claude").startswith("--model sonnet --effort xhigh "))
        self.assertTrue(self.agent_args("codex").endswith(" -c model_reasoning_effort=xhigh"))

    def test_effort_flag_wins(self) -> None:
        """命令行的 --effort 优先于 config（模型照用 config 的）"""
        self.configure(claude_model="sonnet", claude_effort="high", codex_effort="medium")
        self.assertTrue(self.agent_args("claude", "--effort", "max").startswith("--model sonnet --effort max "))
        self.assertTrue(self.orch_args("claude", "--effort", "max").startswith("--model sonnet --effort max "))
        self.assertTrue(self.agent_args("codex", "--effort", "low").endswith(" -c model_reasoning_effort=low"))
        self.assertTrue(self.orch_args("codex", "--effort", "low").endswith(" -c model_reasoning_effort=low"))


class PromptTests(DroverTestCase):
    """发 prompt 带 --until working（working / idle / done / blocked 四种都写明），看到开工就返回，不再固定等满 30 秒；
    herdr 的错误码（timeout / agent_prompt_stalled / agent_blocked）照旧按 PROMPT_HINTS 给 hint。真实 herdr 实测的返回时机：
    空闲 agent 0.4–0.6 秒返回 working，卡在确认框上的立刻报 agent_blocked。"""

    UNTIL = ["--wait", "--until", "working", "--until", "idle", "--until", "done", "--until", "blocked"]

    def fake_herdr(self, d, reply) -> list:
        calls: list = []

        def herdr(args, timeout=None):
            calls.append(args)
            if args[:2] == ["agent", "prompt"]:
                return reply
            if args[:2] == ["agent", "get"]:
                return True, {"agent": {"name": args[2], "agent_status": "working"}}
            return False, {"code": "unexpected", "message": " ".join(args)}

        d.herdr = herdr
        return calls

    def test_returns_when_working(self) -> None:
        """herdr 返回 agent_prompted + working → outcome started、ok；命令带四个 --until 和默认 30000 的上限"""
        d = self.load_drover()
        calls = self.fake_herdr(d, (True, {"agent": {"name": "w1", "agent_status": "working"}, "type": "agent_prompted"}))
        self.assertEqual(d.send_prompt("w1", "hello", d.PROMPT_TIMEOUT_MS),
                         {"ok": True, "outcome": "started", "status": "working"})
        self.assertEqual(calls, [["agent", "prompt", "w1", "hello", *self.UNTIL, "--timeout", "30000"]])

    def test_other_outcomes_keep_hints(self) -> None:
        """很快停下 → settled（带 status）；timeout 仍算 ok（交给 watch）；stalled / blocked 不 ok，hint 取 PROMPT_HINTS"""
        d = self.load_drover()
        cases = [
            ((True, {"agent": {"agent_status": "done"}}), True, "settled"),
            ((False, {"code": "timeout", "message": "timed out waiting for agent status"}), True, "timeout"),
            ((False, {"code": "agent_prompt_stalled", "message": "prompt did not start"}), False, "agent_prompt_stalled"),
            ((False, {"code": "agent_blocked", "message": "agent w1 is blocked and requires interactive input"}),
             False, "agent_blocked"),
        ]
        for reply, ok, outcome in cases:
            with self.subTest(outcome):
                self.fake_herdr(d, reply)
                sent = d.send_prompt("w1", "hello", 5000)
                self.assertEqual((sent["ok"], sent["outcome"]), (ok, outcome))
                if outcome == "settled":
                    self.assertEqual(sent["status"], "done")
                else:
                    self.assertEqual(sent["hint"], d.PROMPT_HINTS[outcome])
                    self.assertEqual(sent["message"], reply[1]["message"])

    def test_dry_run_plans_match(self) -> None:
        """prompt、orch-start 的 --dry-run 写的是同一组等待参数；prompt 的默认上限就是 PROMPT_TIMEOUT_MS"""
        d = self.load_drover()
        out = self.drover_json("prompt", "--name", "w1", "--text", "开始", "--dry-run")
        self.assertEqual(out["planned"], [f"herdr agent prompt w1 <--text 的内容，2 字符> {' '.join(self.UNTIL)} "
                                          f"--timeout {d.PROMPT_TIMEOUT_MS}"])
        command = self.home / ".claude" / "commands" / "drover.md"
        command.parent.mkdir(parents=True)
        command.write_text("# drover\n")
        request = self.tmp / "request.md"
        request.write_text("# 请求\n")
        orch = self.drover_json("orch-start", "--slug", "t9", "--name", "orch-t9", "--request-file", str(request),
                                "--kind", "claude", "--dry-run")
        self.assertEqual(orch["planned"][-1], f"herdr agent prompt orch-t9 <开场 prompt> {' '.join(self.UNTIL)} "
                                              f"--timeout {d.PROMPT_TIMEOUT_MS}")


class ResidueTests(unittest.TestCase):
    def test_no_old_names_in_cli(self) -> None:
        """bin/drover 里没有旧名字；dispatch 只剩改名时特意不改的那几个（flow/dispatch.md、<slug>/dispatch.md、
        DISPATCH_DOC、=== DISPATCH RESULT ===、# dispatch: <slug>）；argparse 的 prog 是 drover，子命令是 orch-start / orch-status"""
        text = DROVER.read_text()
        self.assertEqual(OLD_NAME_RE.findall(text), [])
        leftover = [line for line in KEEP_DISPATCH_RE.sub("", text).splitlines() if "dispatch" in line.lower()]
        self.assertEqual(leftover, [])
        self.assertIn('prog="drover"', text)
        self.assertIn('"orch-start": cmd_orch_start', text)
        self.assertIn('"orch-status": cmd_orch_status', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
