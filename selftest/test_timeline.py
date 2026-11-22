"""时间线：统计数据一直记（以后 drover stats 用），看板上不显示总耗时。
每个任务一个 ~/.drover/<slug>/timeline.jsonl，一行一个 JSON（at 是事情发生的时间、logged 是写下的时间、event、source），只追加。

- drover 自己发起的事在那条命令里当场记（CommandTimelineTests）：orch-start（start 派发；--args 第一个词是 resume 时是 replace，
  老任务补一条估计的 start）、drover prompt 发给编排者（answered，带开头 20 个字；以"收尾"开头的再记 wrapup-sent）、
  看板点收尾（wrapup-clicked）、wrapup --draft（wrapup-summary）、drover rollup 读到新内容（main-read）。
- 只能看到的由观察者补（ObserverTimelineTests）：watch --main 每一轮、看板每次读屏。显示状态变了（state，时间用 rollup 的
  修改时间，已转答复用 answered.json 的时间）、编排者每换一段状态（agent：确认框、接口错误、退出，也有 working / done 之间的
  来回）、停了没更新（stalled）；两边同时跑只记一次（时间线锁）；没有时间线的老任务第一次看到时补 start 和当前状态（估的）；
  观察中断记在全局的 observe-gaps.jsonl。状态持续、total_seconds 按时间线算。
时间都靠改文件修改时间、往时间线里写 at 来拨，watch --main 在进程里跑假时钟，不真等。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import unittest

from selftest_screens import BASH_DIALOG
from selftest_support import DROVER, rollup_digest
from test_board import API_ERROR, HOUR, MINUTE, NORMAL, SLACK, BoardTestCase


class TimelineCase(BoardTestCase):
    def events(self, slug: str, kind: str = None) -> list:
        """这个任务的时间线（kind 给了只留这一种）。"""
        path = self.state / slug / "timeline.jsonl"
        out = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [e for e in out if kind is None or e["event"] == kind]

    def write_events(self, slug: str, *events: dict) -> None:
        """往时间线里先放几条（模拟之前记下的）。"""
        with open(self.state / slug / "timeline.jsonl", "a") as fh:
            for e in events:
                fh.write(json.dumps({"logged": e["at"], "source": "test", **e}, ensure_ascii=False) + "\n")

    def age(self, path, seconds: float) -> float:
        """把文件的修改时间拨到 seconds 秒前，返回那个时间。"""
        t = time.time() - seconds
        os.utime(path, (t, t))
        return t

    def rewrite(self, slug: str, status: str, ago: float) -> float:
        """整份重写 rollup（只有状态和摘要），修改时间拨到 ago 秒前，返回它。"""
        path = self.state / slug / "rollup.md"
        path.write_text(f"# {slug}\n状态：{status}\n摘要：{status}的摘要\n")
        return self.age(path, ago)


class CommandTimelineTests(TimelineCase):
    """drover 自己发起的事：在那条命令里当场记，不靠 watch / 看板在跑。"""

    def install_entries(self) -> None:
        """orch-start 要的 /drover 命令（内容随便，只看文件在不在）。"""
        path = self.home / ".claude" / "commands" / "drover.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nname: drover\n---\n")

    def orch_start(self, d, slug: str, name: str, args: str = None, parent: str = None) -> dict:
        request = self.tmp / "request.md"
        request.write_text("# 请求\n\n标题：测试任务\n")
        return d.cmd_orch_start(argparse.Namespace(slug=slug, name=name, kind="claude", request_file=str(request), args=args,
                                                   effort=None, model=None, parent=parent, dry_run=False))

    def test_orch_start_and_resume(self) -> None:
        """orch-start 派发 → start（name、kind、parent，at 是派发的时间）；--args "resume <slug>" → replace（换上的编排者），
        不再记 start；没有时间线的老任务 resume → 先按 request.md / rollup.md 修改时间里早的那个补一条估计的 start；
        子编排者也记（parent 是上层）；--dry-run 什么都不写"""
        self.install_entries()
        d = self.load_drover()
        d.herdr = lambda args, timeout=None: (
            (True, {"workspaces": [{"label": "orch", "workspace_id": "w9"}]}) if args[:2] == ["workspace", "list"] else
            (True, {"root_pane": {"pane_id": "w9:p2"}, "tab": {"tab_id": "w9:t2"}}) if args[:2] == ["tab", "create"] else
            (False, {"code": "unexpected", "message": " ".join(args)}))
        d.start_agent = lambda name, kind, pane, agent_args, dry_run: {"ok": True}
        d.send_prompt = lambda name, text, timeout_ms: {"ok": True, "outcome": "settled"}
        before = time.time()
        self.assertTrue(self.orch_start(d, "t1", "orch-t1")["ok"])
        self.assertTrue(self.orch_start(d, "t1", "orch-t1b", args="resume t1")["ok"])
        got = [(e["event"], e["source"], e.get("name"), e.get("kind"), e.get("parent"), e.get("estimated"))
               for e in self.events("t1")]
        self.assertEqual(got, [("start", "orch-start", "orch-t1", "claude", None, None),
                               ("replace", "orch-start", "orch-t1b", "claude", None, None)])
        self.assertGreaterEqual(self.events("t1")[0]["at"], round(before, 3))
        old = self.state / "t-old"  # 装上这套记录之前派发的：有 request.md、rollup.md，没有时间线
        old.mkdir()
        (old / "request.md").write_text("# 请求\n")
        request_at = self.age(old / "request.md", 5 * HOUR)
        (old / "rollup.md").write_text("# t-old\n状态：需要换编排者\n")
        self.age(old / "rollup.md", HOUR)
        self.assertTrue(self.orch_start(d, "t-old", "orch-old2", args="resume t-old")["ok"])
        got = [(e["event"], e.get("estimated"), e["at"]) for e in self.events("t-old")]
        self.assertEqual(got[0], ("start", True, round(request_at, 3)))
        self.assertEqual(got[1][:2], ("replace", None))
        (self.state / "t1" / "dispatch.md").write_text("# dispatch: t1\n")
        self.assertTrue(self.orch_start(d, "t1-shop", "orch-t1-shop", parent="t1")["ok"])
        self.assertEqual([(e["event"], e["parent"]) for e in self.events("t1-shop")], [("start", "t1")])
        self.install_entries()
        dry = self.drover_json("orch-start", "--slug", "t9", "--name", "orch-t9", "--request-file", str(self.tmp / "request.md"),
                               "--kind", "claude", "--dry-run")
        self.assertTrue(dry["dry_run"])
        self.assertFalse((self.state / "t9").exists())

    def prompt(self, d, name: str, text: str, ok: bool = True) -> dict:
        d.herdr = lambda args, timeout=None: (
            ((True, {"agent": {"agent_status": "working"}}) if ok else (False, {"code": "agent_blocked", "message": "x"}))
            if args[:2] == ["agent", "prompt"] else (True, {"agent": {"agent_status": "working"}}))
        return d.cmd_prompt(argparse.Namespace(name=name, text=text, file=None, timeout=1000, dry_run=False))

    def test_prompt_answered_and_wrapup_sent(self) -> None:
        """drover prompt 发给编排者成功 → answered（和写 answered.json 同一处、同一个时间：时间线的 at 就是 answered.json 的 at
        取 3 位小数，严格相等；带编排者名、开头 20 个字，统计时分得出是转答复还是"接着做"）；内容以"收尾"开头的再记一条
        wrapup-sent（时间也是同一个）；发送失败、发给 worker（对不上 orch.json）都不记"""
        self.task("t-a", "orch-a", "等你决定", sections={"需要你决定": ["- 选 A 还是 B"]})
        d = self.load_drover()
        self.assertFalse(self.prompt(d, "orch-a", "答复：选 A", ok=False)["ok"])
        self.assertEqual(self.events("t-a"), [])
        self.prompt(d, "orch-a", "答复：选 A")
        self.prompt(d, "orch-a", "收尾。用户原话：全部收尾，顺便把分支删了，再跑一遍检查")
        self.prompt(d, "w1", "开始")
        got = [(e["event"], e["source"], e["name"], e.get("head")) for e in self.events("t-a")]
        self.assertEqual(got, [("answered", "prompt", "orch-a", "答复：选 A"),
                               ("answered", "prompt", "orch-a", "收尾。用户原话：全部收尾，顺便把分支删了"),
                               ("wrapup-sent", "prompt", "orch-a", None)])
        answered = json.loads((self.state / "t-a" / "answered.json").read_text())["at"]
        self.assertEqual([e["at"] for e in self.events("t-a")[1:]], [round(answered, 3)] * 2)
        self.assertEqual([p.parent.name for p in self.state.glob("*/timeline.jsonl")], ["t-a"])

    def test_wrapup_summary_only_with_draft(self) -> None:
        """drover wrapup 只看事实时不记；--draft 写成了骨架 → wrapup-summary（带骨架路径）；--draft 被拒（已有文件、没加 --force）
        不记"""
        d = self.state / "t-w"
        d.mkdir()
        (d / "dispatch.md").write_text("# dispatch: t-w\n\n目标：做个功能\n")
        self.drover_json("wrapup", "--slug", "t-w")
        self.assertEqual(self.events("t-w"), [])
        draft = d / "wrapup.md"
        self.drover_json("wrapup", "--slug", "t-w", "--draft", str(draft))
        self.assertEqual([(e["event"], e["source"], e["draft"]) for e in self.events("t-w")],
                         [("wrapup-summary", "wrapup", str(draft))])
        self.drover_json("wrapup", "--slug", "t-w", "--draft", str(draft), rc=1)
        self.assertEqual(len(self.events("t-w")), 1)

    def test_rollup_read(self) -> None:
        """主会话用 drover rollup 读到新内容 → main-read（rollup 的状态和修改时间：统计主会话读晚了多久）；同一版再读不记，
        改了再读又记；别的 pane（编排者）读不记（RollupCommandTests 测输出和 main-seen.json）"""
        self.task("t-a", "orch-a", "等你决定", sections={"需要你决定": ["- 选 A 还是 B"]}, age=HOUR)
        mtime = (self.state / "t-a" / "rollup.md").stat().st_mtime
        self.main_rollup("--slug", "t-a")
        self.main_rollup("--slug", "t-a")
        self.assertEqual([(e["event"], e["source"], e["status"], e["rollup_mtime"]) for e in self.events("t-a")],
                         [("main-read", "rollup", "等你决定", round(mtime, 3))])
        self.rewrite("t-a", "完成", 0)
        self.drover("rollup", "--name", "orch-a", env={"HERDR_ENV": "1", "HERDR_PANE_ID": "w2:p3"})
        self.assertEqual([e["status"] for e in self.events("t-a", "main-read")], ["等你决定"])
        self.main_rollup("--name", "orch-a")
        self.assertEqual([e["status"] for e in self.events("t-a", "main-read")], ["等你决定", "完成"])

    def test_board_click_marks(self) -> None:
        """看板点收尾送成了（board_mark_wrapup）：每个任务写 wrapup-clicked.json（at、送的那句话），时间线记 wrapup-clicked
        （source board，all 是不是点的全部收尾）。看板 tab 里真点的过程见 test_board.py 的 BoardClickTests"""
        for slug in ("t-a", "t-b"):
            self.task(slug, f"orch-{slug}", "完成", age=HOUR, read=True)
        d = self.load_drover()
        d.board_mark_wrapup(["t-a"], False, "收尾：t-a")
        d.board_mark_wrapup(["t-a", "t-b"], True, "全部收尾：t-a、t-b")
        self.assertEqual([(e["source"], e["all"]) for e in self.events("t-a", "wrapup-clicked")],
                         [("board", False), ("board", True)])
        self.assertEqual([(e["source"], e["all"]) for e in self.events("t-b", "wrapup-clicked")], [("board", True)])
        self.assertEqual(json.loads((self.state / "t-b" / "wrapup-clicked.json").read_text())["text"], "全部收尾：t-a、t-b")


class ObserverTimelineTests(TimelineCase):
    """只能看到的事由观察者补：watch --main 每一轮、看板每次读屏（文本、--plain、--json、--live）。"""

    def dispatched(self, slug: str, ago: float) -> float:
        """这个任务是装上之后 orch-start 派发的：时间线里先有一条 start（ago 秒前），返回它的时间。"""
        at = time.time() - ago
        self.write_events(slug, {"at": at, "event": "start", "name": f"orch-{slug}"})
        return at

    def watch_main(self) -> None:
        """watch --main 跑几轮（进程里、假时钟）。"""
        self.watch("--main", "--interval", "0.2", "--timeout", "1")

    def test_state_changes_at_change_time(self) -> None:
        """显示状态变了才记一条 state，时间是这次变化的时间、不是看到的时间：没有 rollup 的算进行中、从派发算；rollup 状态变了按
        它的修改时间（编排者直接写文件，晚看到也准）；转过答复（等你决定 → 已转答复）按 answered.json 的时间；同一个显示状态
        rollup 重写了不记、再看一遍也不记。--json 的 state_since / state_seconds 从最后一条 state 算，total_seconds 从派发算，
        完成的算到这一段完成开始"""
        self.task("t-a", "orch-a")  # 刚派发，还没写 rollup
        self.set_agents(**{"orch-a": {"status": "working", "screen": NORMAL}})
        dispatch = self.dispatched("t-a", 20 * MINUTE)
        self.board("--json")
        self.assertEqual([(e["state"], e["at"], e["status"], e.get("estimated")) for e in self.events("t-a", "state")],
                         [("进行中", round(dispatch, 3), None, None)])
        self.rewrite("t-a", "进行中", 15 * MINUTE)
        self.board()
        self.assertEqual(len(self.events("t-a", "state")), 1)
        waiting = self.rewrite("t-a", "等你决定", 10 * MINUTE)
        self.board("--plain")
        answered = time.time() - 5 * MINUTE
        (self.state / "t-a" / "answered.json").write_text(json.dumps(
            {"at": answered, "rollup_digest": rollup_digest(self.state / "t-a" / "rollup.md")}))
        self.board("--json")
        done = self.rewrite("t-a", "完成（已收尾）", 2 * MINUTE)
        task = self.tasks_json()["t-a"]
        self.board()
        self.assertEqual([(e["state"], e["at"], e["source"], e["status"]) for e in self.events("t-a", "state")], [
            ("进行中", round(dispatch, 3), "board", None), ("等你决定", round(waiting, 3), "board", "等你决定"),
            ("已转答复", round(answered, 3), "board", "等你决定"), ("完成", round(done, 3), "board", "完成（已收尾）")])
        self.assertEqual(task["display_state"], "完成")
        self.assertAlmostEqual(task["state_since"], done, places=2)
        self.assertEqual(task["state_seconds"] // 60, 2)
        self.assertEqual(task["total_seconds"] // 60, 18)  # 派发 20 分钟前，2 分钟前进入完成

    def test_watch_and_board_record_once(self) -> None:
        """watch --main 和看板都在看：同一件事（显示状态变了、编排者换了一段）只记一次——先看板后 watch、先 watch 后看板（记的
        source 是先看到的那个）、4 个看板和 1 个 watch --main 子进程同时跑都一样（读-比-追加在时间线锁里，状态在锁里重新读）"""
        self.task("t-a", "orch-a", "进行中", summary="在做", age=HOUR)
        self.dispatched("t-a", 2 * HOUR)
        self.set_agents(**{"orch-a": {"status": "working", "seq": 1, "screen": NORMAL}})
        self.board("--json")
        self.watch_main()
        self.assertEqual([(e["event"], e["source"]) for e in self.events("t-a")],
                         [("start", "test"), ("agent", "board"), ("state", "board")])
        self.rewrite("t-a", "等你决定", MINUTE)
        self.set_agents(**{"orch-a": {"status": "done", "seq": 2, "screen": NORMAL}})
        self.watch_main()
        self.board("--json")
        self.assertEqual([(e["event"], e["source"]) for e in self.events("t-a")[3:]],
                         [("agent", "watch-main"), ("state", "watch-main")])
        self.rewrite("t-a", "完成", 30)
        self.set_agents(**{"orch-a": {"status": "idle", "seq": 3, "screen": NORMAL}})
        commands = [["board", "--json"]] * 4 + [["watch", "--main", "--interval", "0.2", "--timeout", "1"]]
        procs = [subprocess.Popen([sys.executable, str(DROVER), *args], env=self.env, cwd=str(self.tmp),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for args in commands]
        self.assertEqual([p.wait(timeout=60) for p in procs], [0] * 5)
        self.assertEqual(sorted((e["event"], e.get("shown"), e.get("state")) for e in self.events("t-a")[5:]),
                         [("agent", "idle", None), ("state", None, "完成")])

    def test_agent_segments(self) -> None:
        """编排者每换一段状态记一条 agent（name、shown、prev、seq；确认框带弹框指纹）：working → 卡在确认框 → working → done →
        接口错误 → 退出（gone，seq 是 null）；没变不记。确认框、接口错误、意外退出的次数和时长，编排者自己干活的时长都靠它算"""
        self.task("t-a", "orch-a", "进行中", age=HOUR)
        for agent in ({"status": "working", "seq": 1, "screen": NORMAL}, {"status": "blocked", "seq": 2, "screen": BASH_DIALOG},
                      {"status": "blocked", "seq": 2, "screen": BASH_DIALOG}, {"status": "working", "seq": 3, "screen": NORMAL},
                      {"status": "done", "seq": 4, "screen": NORMAL}, {"status": "done", "seq": 5, "screen": API_ERROR}, None):
            self.set_agents(**({"orch-a": agent} if agent else {}))
            self.board("--json")
        got = [(e["name"], e["shown"], e["prev"], e["seq"], "dialog_fp" in e) for e in self.events("t-a", "agent")]
        self.assertEqual(got, [("orch-a", "working", None, 1, False), ("orch-a", "blocked", "working", 2, True),
                               ("orch-a", "working", "blocked", 3, False), ("orch-a", "done", "working", 4, False),
                               ("orch-a", "api-error", "done", 5, False), ("orch-a", "gone", "api-error", None, False)])

    def test_stalled_once(self) -> None:
        """停了没更新：编排者 done / idle、显示状态是进行中，从两段里开始得晚的那个算起满 10 分钟记一条 stalled（at 是看到的时间，
        since 是起点），之后看板、watch --main 再看都不重记；刚停下的不记；等你决定的不算（那是在等用户）"""
        self.task("t-s", "orch-s", "进行中", summary="跑测试", age=HOUR)
        self.task("t-u", "orch-u", "等你决定", summary="选 A", age=HOUR, read=True)
        self.set_agents(**{"orch-s": {"status": "done", "seq": 1, "screen": NORMAL},
                           "orch-u": {"status": "done", "seq": 1, "screen": NORMAL}})
        self.board("--json")
        self.assertEqual(self.events("t-s", "stalled"), [])  # 编排者刚停
        for slug in ("t-s", "t-u"):
            path = self.state / slug / "agent-state.json"
            record = json.loads(path.read_text())
            record["since"] -= 15 * MINUTE + SLACK
            path.write_text(json.dumps(record))
        since = json.loads((self.state / "t-s" / "agent-state.json").read_text())["since"]
        self.board("--json")
        self.watch_main()
        self.board()
        stalled = self.events("t-s", "stalled")
        self.assertEqual([(e["since"], e["source"]) for e in stalled], [(round(since, 3), "board")])
        self.assertGreater(stalled[0]["at"], since + 15 * MINUTE)
        self.assertEqual(self.events("t-u", "stalled"), [])

    def test_stalled_more_ways(self) -> None:
        """停了没更新（时间线、看板"主会话在处理"、watch --main 同一个判定）：没写 rollup 就停了、herdr 报 unknown 也算停着，
        满 10 分钟记 stalled、看板写"停了 N 分钟没更新"；状态栏挂着后台命令（waiting-background）的，过了 Monitor 的 30 分钟
        上限才开始算停（起点是挂上之后 30 分钟），挂了 35 分钟不算、45 分钟算（停了 15 分钟）"""
        self.task("t-n", "orch-n")  # 两小时前派发，没写 rollup
        self.started("t-n")
        self.task("t-u", "orch-u", "进行中", age=HOUR)
        self.task("t-g", "orch-g", "进行中", age=2 * HOUR)
        self.set_agents(**{"orch-n": {"status": "done", "screen": NORMAL}, "orch-u": {"status": "unknown", "screen": NORMAL},
                           "orch-g": {"status": "done", "screen": NORMAL.replace("· ← 1 agent", "· 1 shell · ← 1 agent")}})
        self.stopped("t-n", "orch-n", "done", 12 * MINUTE)
        self.stopped("t-u", "orch-u", "unknown", 12 * MINUTE)
        self.stopped("t-g", "orch-g", "waiting-background(1)", 35 * MINUTE)
        tasks = self.tasks_json()
        self.assertEqual({s: (t["reason"], len(self.events(s, "stalled"))) for s, t in tasks.items()},
                         {"t-n": ("停了 12 分钟没更新", 1), "t-u": ("停了 12 分钟没更新", 1), "t-g": (None, 0)})
        self.stopped("t-g", "orch-g", "waiting-background(1)", 45 * MINUTE)
        since = json.loads((self.state / "t-g" / "agent-state.json").read_text())["since"]
        self.assertEqual(self.tasks_json()["t-g"]["reason"], "停了 15 分钟没更新")
        self.assertEqual([e["since"] for e in self.events("t-g", "stalled")], [round(since + 30 * MINUTE, 3)])

    def test_observe_gap(self) -> None:
        """观察中断：观察者每一轮记最后一次看的时间（observe.json），离上次超过 60 秒才在 observe-gaps.jsonl 记一条
        {from, to}；第一次（没有记录）不记；时间不往回走。看板读屏、watch --main 是观察者，--line / --pending 不是"""
        d = self.load_drover()
        t = 1_800_000_000.0
        for dt in (0, 30, 100, 90, 150):
            d.note_observed(t + dt)
        gaps = self.state / "observe-gaps.jsonl"
        self.assertEqual([json.loads(x) for x in gaps.read_text().splitlines()], [{"from": t + 30, "to": t + 100}])
        self.assertEqual(json.loads((self.state / "observe.json").read_text()), {"last": t + 150})
        self.task("t-a", "orch-a", "进行中", age=HOUR)
        self.set_agents(**{"orch-a": {"status": "working", "screen": NORMAL}})
        last = time.time() - 2 * HOUR
        (self.state / "observe.json").write_text(json.dumps({"last": last}))
        self.board("--line")
        self.board("--pending")
        self.assertEqual(len(gaps.read_text().splitlines()), 1)
        before = time.time()
        self.board("--plain")
        self.watch_main()
        lines = [json.loads(x) for x in gaps.read_text().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1]["from"], round(last, 3))
        self.assertGreaterEqual(lines[1]["to"], round(before, 3))

    def test_old_task_backfill(self) -> None:
        """装上之前就在跑的任务（没有时间线，或者只有 drover rollup 记的 main-read）：第一次看到时补一条 start（request.md 和
        rollup.md 修改时间里早的那个）和当前显示状态（rollup 的修改时间），都标 estimated；--json 的 total_seconds 从补的派发算，
        state_seconds 从补的状态算；request.md 后来被改过、比 rollup 还新时，派发估成 rollup 的时间（派发一定在编排者写 rollup 之前）"""
        self.task("t-old", "orch-old", "等你决定", summary="选 A", age=3 * HOUR, read=True)
        (self.state / "t-old" / "request.md").write_text("# 请求\n")
        request_at = self.age(self.state / "t-old" / "request.md", 5 * HOUR)
        self.task("t-edit", "orch-edit", "进行中", summary="在做", age=3 * HOUR)
        (self.state / "t-edit" / "request.md").write_text("# 请求\n")
        self.age(self.state / "t-edit" / "request.md", HOUR)
        self.set_agents(**{"orch-old": {"status": "done", "screen": NORMAL}, "orch-edit": {"status": "working", "screen": NORMAL}})
        tasks = self.tasks_json()
        events = [(e["event"], e.get("state"), e["at"], e.get("estimated")) for e in self.events("t-old")
                  if e["event"] in ("start", "state")]
        rollup_at = (self.state / "t-old" / "rollup.md").stat().st_mtime
        self.assertEqual(events, [("start", None, round(request_at, 3), True), ("state", "等你决定", round(rollup_at, 3), True)])
        self.assertEqual((tasks["t-old"]["total_seconds"] // 60, tasks["t-old"]["state_seconds"] // 60), (5 * 60, 3 * 60))
        edit = self.events("t-edit", "start")[0]
        self.assertEqual(edit["at"], round((self.state / "t-edit" / "rollup.md").stat().st_mtime, 3))
        self.board("--json")
        self.assertEqual(len(self.events("t-old", "start")) + len(self.events("t-old", "state")), 2)  # 只补一次


if __name__ == "__main__":
    unittest.main(verbosity=2)
