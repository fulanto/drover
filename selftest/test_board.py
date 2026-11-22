"""board：任务看板，加上它读的两份记录——主会话用 drover rollup 读时写的 main-seen.json（已读；
别的调用方读不写，RollupCommandTests）、
drover prompt 发给编排者时写的 answered.json（转过答复）。

写法：默认 Markdown（GFM 表格）、--plain（飞书，纯文字列表）、--live（框线表，按显示宽度对齐，窄屏退回列表）；--json 给 agent 读，
--line 给状态栏（不读屏幕），--pending 给【转达】当脚注，--pane / --tab 开看板。看板 tab 里能点 [收尾] / [全部收尾]：
把"收尾：<slug>"交给 herdr-oncall 的 send-main.mjs（假插件：run-node.sh 记下参数、打印 send-reply.json）。"收尾："一行决定
能不能一键收尾；旧 rollup 的"## 交付"一节（一行一样交付："- <可以看|处理中|等你|提醒>：<项目> — <状态>"）只进 --json 的
deliverables（BoardV2Tests）。

分组（BoardV5Tests、BoardScrollTests、BoardClickTests）：一张正常的表格，一行一个任务；按显示状态分组（等你 / 进行中 / 完成），
一组一张表，任务之间有横线，组之间空一行，几张表列宽一致；列是任务（中文标题）| 当前状态 | 要你做什么 | 状态持续 | 代号，
完成表多一列收尾（[收尾] / 收尾中 / 已收尾 / 先别：原因）。点某一行的 [收尾] 留在看板、那一格变"收尾中"；点 [全部收尾] 才切回
主会话；点别处什么都不做。终端窄时任务列固定、其余整列左右滚（SGR 66 / 67、← →、点 [<] [>]）。"状态持续"按时间线
（timeline.jsonl，见 test_timeline.py）算。要主会话处理的收成最底下一行"主会话在处理"。

时长（BoardSinceTests）："主会话在处理"的每个原因各带各的时长，不拿 rollup 的修改时间顶替：卡在确认框 / 接口错误 / 编排者已退出
按 agent-state.json（看板和 watch --main 每一轮记的状态和它从什么时候开始），这次才看到的写"刚发现"；未读、要换编排者按 rollup 的
修改时间；"停了 N 分钟没更新"后面不再接时长。假时钟：把 agent-state.json 的 since 往前拨。

旧 rollup（BoardTableTests 等）：不在第一个"："处猜拆，要你做什么取"答法："，没有就写一句用户要做的动作。

折行（BoardV6Tests）：看板 tab 里当前状态、要你做什么写长了在格子里折行（每行 28 / 22 格，最多 3 行，再多第 3 行末尾截成"..."），
英文单词、路径、sha 不从中间断；一个任务占几行时只在第一行写任务名、代号和 [收尾]。截断记号是 ASCII 的"..."，滚动按钮是 [<] [>]
（"…""◀""▶"宽度不定，画成两格时竖线错开、点不准）。--plain 的当前状态、要你做什么不截。表格（--live、Markdown）的格子里
rollup 原文带的宽度不定的字（… — “” · → ① 等）先换成 ASCII，没映射到的 NFKC / NFKD，再不行写 "?"。

拖选复制（BoardSelectTests）：看板 tab 里按住左键拖动选中、松开自动复制（看板自己画反色选区、发 OSC 52，herdr 写进本机剪贴板），
按下松开在同一格才算单击；折行守中文禁则（句末标点不在行首、开括号不在行尾）。

跨列的提醒行（BoardV8Tests、BoardNoteClickTests）：提醒跟着它的任务走，写在那个任务最后一行下面、后面几列合成的一格里。
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import io
import json
import os
import pty
import re
import select
import shlex
import signal
import struct
import subprocess
import sys
import termios
import time
import unicodedata
import unittest
from pathlib import Path
from typing import Optional

from selftest_screens import BASH_DIALOG, PARALLEL_SECOND, PARALLEL_SECOND_BLINK, WEBFETCH, bash_dialog
from selftest_support import DROVER, DroverTestCase, rollup_digest

NORMAL = """⏺ rollup 已更新。

✻ Cooked for 3m · done 1:05 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on · ← 1 agent"""
API_ERROR = """⏺ API Error: 503 No available accounts: no available accounts. This is a server-side issue.

✻ Brewed for 34m 26s · done 1:16 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on (shift+tab to cycle) · ← 1 agent"""
HOUR = 3600
MINUTE = 60
SLACK = 30  # 修改时间往前多拨 30 秒：跑到看板时不会跨过分钟 / 小时的边界
JSON_KEYS = {"slug", "name", "group", "status", "agent_status", "screen_status", "reason", "reasons", "summary",
             "age_minutes", "rollup_mtime", "unread", "answered_after_rollup", "wrapup", "wrapup_reason", "deliverables",
             "title", "ask", "display_state", "state_since", "state_seconds", "total_seconds", "wrapup_clicked", "note",
             "tab_id"}
HEAD_MD = r"^\*\*drover 看板\*\* · \d\d-\d\d \d\d:\d\d · "
HEAD_PLAIN = r"^drover 看板 · \d\d-\d\d \d\d:\d\d · "
# 第一行末尾 drover 自己的版本号（跑的是本 worktree：正好在 tag 上 v0.3.0、之后 N 个提交 v0.3.0+N、没有 tag 的 clone 是 7 位 sha；
# 这几条测版式，版本号怎么算见 test_auto_update.py）
VERSION = r"(?:v\d+\.\d+\.\d+(?:\+\d+)?|[0-9a-f]{7})"
# 假的 herdr-oncall bin/run-node.sh：不跑 node，记下工作目录和参数，打印 send-reply.json（send-main.mjs 的一行 JSON）。
FAKE_SEND_NODE = """#!/bin/bash
root="$(cd "$(dirname "$0")/.." && pwd -P)"
{ pwd -P; printf '%s\\n' "$@"; printf -- '--end--\\n'; } >> "$root/send-calls.txt"
cat "$root/send-reply.json"
"""


class BoardTestCase(DroverTestCase):
    def task(self, slug: str, name: str, status: str = None, *, parent: str = None, summary: str = None,
             sections: dict = None, age: float = 0, read: bool = False, answered_ago: float = None,
             deliver: list = None, wrapup: str = None, title: str = None, ask: str = None, note: str = None) -> None:
        """建一个任务：orch.json；status 不是 None 时写 rollup.md（开头按 dispatch.md 的顺序：状态、标题、摘要、要你做、收尾、
        提醒，再是旧的"## 交付"、各节），修改时间拨到 age 秒前；read 时主会话用 drover rollup 读一遍（已读，main_rollup）；answered_ago 不是 None
        时写 answered.json（那么多秒前转过答复，digest 是现在这份 rollup 的：转答复之后 rollup 没变）。"""
        d = self.state / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "orch.json").write_text(json.dumps({"slug": slug, "name": name, "kind": "claude", "parent": parent,
                                                 "started": "01-02 09:00"}, ensure_ascii=False))
        if status is not None:
            head = (("标题", title), ("摘要", summary), ("要你做", ask), ("收尾", wrapup), ("提醒", note))
            lines = [f"# {slug}", f"状态：{status}", *[f"{k}：{v}" for k, v in head if v], ""]
            if deliver:
                lines += ["## 交付", *deliver, ""]
            for title, body in (sections or {}).items():
                lines += [f"## {title}", *body, ""]
            (d / "rollup.md").write_text("\n".join(lines))
            if age:
                t = time.time() - age - SLACK
                os.utime(d / "rollup.md", (t, t))
            if read:
                self.main_rollup("--slug", slug)
        if answered_ago is not None:
            (d / "answered.json").write_text(json.dumps({"at": time.time() - answered_ago,
                                                         "rollup_digest": rollup_digest(d / "rollup.md")}))

    def started(self, slug: str) -> None:
        """这个任务是 orch-start 派发的（时间线里有派发那条）：装上已读记录之前的老任务没有时间线。"""
        with open(self.state / slug / "timeline.jsonl", "a") as fh:
            fh.write(json.dumps({"at": time.time() - 2 * HOUR, "logged": time.time(), "event": "start",
                                 "source": "orch-start"}) + "\n")

    def stopped(self, slug: str, name: str, shown: str, ago: float, seq: int = 0) -> None:
        """编排者 ago 秒前就停在 shown 上了（agent-state.json，watch --main / 看板记的；seq 要和假 herdr 的一致）。"""
        (self.state / slug / "agent-state.json").write_text(json.dumps(
            {"name": name, "shown": shown, "since": time.time() - ago - SLACK, "seq": seq}))

    def board(self, *args: str, rc: int = 0) -> str:
        return self.drover("board", *args, rc=rc).stdout

    def rollup_of(self, d, text: str) -> dict:
        path = self.tmp / "rollup.md"
        path.write_text(text)
        return d.read_rollup(path)

    def tasks_json(self) -> dict:
        return {t["slug"]: t for t in json.loads(self.board("--json"))["tasks"]}

    def design_tasks(self) -> None:
        """三个任务（DESIGN_WIDE / DESIGN_SCROLLED 画的就是它们）：orch-demo-export-job 等你决定 29 小时（rollup 没有
        标题、要你做，标题取 request.md，要你做取"答法："）、orch-demo-board 刚写的等你决定（标题、要你做都在 rollup 里）、
        orch-demo-perf 完成 2 小时 10 分（能收尾，带一句提醒）。都读过、编排者都停着，最底下没有"主会话在处理"。"""
        self.task("orch-demo-export-job", "orch-demo", "等你决定", summary="暂停，你要先弄清各功能点", age=29 * HOUR,
                  read=True, sections={"需要你决定": ["- 四个方案见转达", "- 答法：回 A/B/C/D（建议 A）。选了就另起实施任务"]})
        (self.state / "orch-demo-export-job" / "request.md").write_text("# 请求\n\n标题：订单导出改成异步处理\n用户原话：…\n")
        self.task("orch-demo-board", "orch-board", "等你决定", title="drover 任务看板", summary="设计稿出好了",
                  ask="看设计稿，回确认或要改的", read=True)
        self.task("orch-demo-perf", "orch-heavy", "完成", title="drover 检查提速", summary="heavy 按资源调度、自测 53 秒，已推",
                  ask="无", wrapup="可以", note="每台机器上每种命令第一次会单独跑一次，之后按实测排",
                  age=2 * HOUR + 10 * MINUTE, read=True)
        self.set_agents(**{"orch-demo": {"status": "done", "screen": NORMAL}, "orch-board": {"status": "done", "screen": NORMAL},
                           "orch-heavy": {"status": "idle", "screen": NORMAL}})

    def fake_plugin(self, script: bool = True, reply: dict = None) -> Path:
        """装一个假的 herdr-oncall：herdr plugin list 列出它；bin/run-node.sh 把工作目录和参数记进 send-calls.txt（每次一段，
        "--end--" 结尾），打印 send-reply.json。script 为 False 时没有 src/actions/send-main.mjs（旧版本）。"""
        root = self.tmp / "oncall"
        (root / "bin").mkdir(parents=True, exist_ok=True)
        (root / "bin" / "run-node.sh").write_text(FAKE_SEND_NODE)
        (root / "src" / "actions").mkdir(parents=True, exist_ok=True)
        if script:
            (root / "src" / "actions" / "send-main.mjs").write_text("// fake\n")
        (root / "send-reply.json").write_text(json.dumps(reply or {"ok": True, "result": "delivered", "reason": "ready",
                                                                   "notice": "已送进主会话"}, ensure_ascii=False))
        state = self.herdr_state()
        state["plugins"] = [{"plugin_id": "com.codreamer.herdr.oncall", "version": "0.7.3", "enabled": True,
                             "plugin_root": str(root), "source": {"kind": "github"}}]
        self.set_herdr_state(state)
        return root

    @staticmethod
    def send_calls(root: Path) -> list:
        path = root / "send-calls.txt"
        if not path.exists():
            return []
        return [chunk.strip("\n").split("\n") for chunk in path.read_text().split("--end--\n") if chunk.strip()]

    def set_panes(self, panes: dict, tabs: dict = None) -> None:
        """假 herdr 的 pane（按 pane 登记的 agent，见 selftest_fakes）；tabs 不是 None 时一起换掉 tab。"""
        state = self.herdr_state()
        state["panes"] = panes
        if tabs is not None:
            state["tabs"] = tabs
        self.set_herdr_state(state)

    def live_lines(self, width: int) -> list:
        """--live 跑一轮（终端 width 格宽，不在看板 tab 里：没开鼠标），画出来的一屏，一行一项。"""
        self.env["COLUMNS"] = str(width)
        try:
            out = self.board("--live", "--iterations", "1", "--interval", "0")
        finally:
            self.env.pop("COLUMNS")
        return out.replace("\033[H\033[2J", "").split("\n")

    def frame(self, width: int, offset: int = 0, mouse: bool = True) -> tuple:
        """进程里画一屏（board_frame，默认开着鼠标：看板 tab 里的样子）：(模块, 行, 点击区域, 实际偏移)。"""
        d = self.load_drover()
        now = time.time()
        view = d.board_view(d.board_rows(True, now), now)
        return (d, *d.board_frame(view, now, width, (), mouse, offset))


class BoardGroupTests(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task("t-unread", "orch-unread", "等你决定", age=2 * HOUR,
                  sections={"需要你决定": ["- 选 A 还是 B：A 快、B 稳。细节见下", "- 答法：回 A 或 B；都不要也行"]})
        self.task("t-user", "orch-user", "等你决定", summary="导出改走哪个队列：回 A/B/C/D（建议 A）",
                  sections={"需要你决定": ["- 别的行"]}, age=20 * HOUR, read=True)
        self.task("t-user-shop", "orch-user-shop", "等你决定", parent="t-user", age=HOUR)  # 子编排者不单列
        self.task("t-answered", "orch-ans", "受阻", sections={"结论": ["目标：修好权限", "卡在权限上"]}, age=HOUR,
                  read=True, answered_ago=5 * MINUTE + SLACK)
        self.task("t-prog", "orch-prog", "进行中", sections={"当前进度": ["- 读代码", "- 写自测"]}, age=5 * MINUTE)
        self.task("t-wrap", "orch-wrap", "完成", sections={"结论": ["目标：做看板", '- 方案 C 已推送；说"收尾"清理']},
                  age=17 * HOUR, read=True)
        self.task("t-gone", "orch-gone", "进行中", sections={"当前进度": ["- 读完代码"]}, age=3 * HOUR)
        self.task("t-replace", "orch-rep", "需要换编排者", sections={"当前进度": ["- 上下文快满了"]}, age=4 * HOUR, read=True)
        self.task("t-closed", "orch-closed", "完成（已收尾）", age=30 * HOUR)  # 退出了、完成了：不显示
        self.task("t-stall", "orch-stall", "进行中", sections={"当前进度": ["- 跑测试"]}, age=HOUR)
        self.stopped("t-stall", "orch-stall", "done", 20 * MINUTE)
        self.task("t-dialog", "orch-dialog", "进行中", sections={"当前进度": ["- 改 watch"]}, age=30 * MINUTE)
        self.set_agents(**{"orch-unread": {"status": "done", "screen": NORMAL},
                           "orch-user": {"status": "done", "screen": NORMAL},
                           "orch-user-shop": {"status": "done", "screen": NORMAL},
                           "orch-ans": {"status": "working", "screen": NORMAL},
                           "orch-prog": {"status": "working", "screen": NORMAL},
                           "orch-wrap": {"status": "idle", "screen": NORMAL},
                           "orch-rep": {"status": "done", "screen": NORMAL},
                           "orch-stall": {"status": "done", "screen": NORMAL},
                           "orch-dialog": {"status": "done", "screen": WEBFETCH}})

    def test_markdown_groups(self) -> None:
        """按显示状态分组，一组一张表、一行一个任务：等你决定（已读、未读都算）→ 等你；转过答复的受阻 → 进行中（"已转答复，
        等它接着做"，要你做"无"，状态持续从转答复算）；进行中、需要换编排者（"换编排者中："）、退出了的 → 进行中；完成且活着 → 完成
        （能收尾，标题后面写"可以收尾 1 个"）。没有标题的用代号；当前状态取"摘要："（截到 40 格以内的不截），没有就按状态取正文
        那一行的第一句；要你做什么取"答法："那一句，没有写"看转达，回你的决定"，别的写"无"。完成且已退出、子编排者不显示；组内按
        状态持续长的在前。原来"要主会话处理"的收成最底下一行，各原因带各自的时长（第一次看到的写"刚发现"）"""
        blocks = self.board().strip().split("\n\n")
        self.assertRegex(blocks[0], HEAD_MD + r"9 个任务 · drover " + VERSION + "$")
        head = "| 任务 | 当前状态 | 要你做什么 | 状态持续 | 代号 |\n|---|---|---|---|---|\n"
        self.assertEqual(blocks[1:], [
            "**等你（2）**",
            head + "| t-user | 导出改走哪个队列：回 A/B/C/D（建议 A） | 看转达，回你的决定 | 20 小时 | t-user |\n"
                   "| t-unread | 选 A 还是 B：A 快、B 稳 | 回 A 或 B | 2 小时 | t-unread |",
            "**进行中（6）**",
            head + "| t-replace | 换编排者中：上下文快满了 | 无 | 4 小时 | t-replace |\n"
                   "| t-gone | 读完代码 | 无 | 3 小时 | t-gone |\n| t-stall | 跑测试 | 无 | 1 小时 | t-stall |\n"
                   "| t-dialog | 改 watch | 无 | 30 分钟 | t-dialog |\n"
                   "| t-answered | 已转答复，等它接着做 | 无 | 5 分钟 | t-answered |\n| t-prog | 写自测 | 无 | 5 分钟 | t-prog |",
            "**完成（1）**　可以收尾 1 个：回\"全部收尾\"",
            "| 任务 | 当前状态 | 要你做什么 | 状态持续 | 代号 | 收尾 |\n|---|---|---|---|---|---|\n"
            "| t-wrap | 方案 C 已推送 | 无 | 17 小时 | t-wrap | 可以 |",
            "主会话在处理：t-replace 要换编排者（4 小时前写的）；t-gone 编排者已退出（刚发现，rollup：进行中）；"
            "t-unread 新的等你决定（未读，2 小时前写的）；t-stall 停了 20 分钟没更新；t-dialog 卡在确认框（刚发现）",
        ])

    def test_json_fields(self) -> None:
        """--json：ok、generated、counts（原来的四组和 total，主会话路由用）、tasks 的字段（含 title、ask、display_state、
        state_since、state_seconds、total_seconds、wrapup_clicked；deliverables 没有"## 交付"时是 []）、wrapup_ready；
        未读 / 转过答复 / 屏幕修正 / 退出的取值"""
        out = json.loads(self.board("--json"))
        self.assertEqual(set(out), {"ok", "generated", "counts", "tasks", "wrapup_ready", "drover"})  # drover 见 test_auto_update.py
        self.assertEqual(out["counts"], {"main": 5, "user": 1, "orch": 2, "wrapup": 1, "total": 9})
        self.assertRegex(out["generated"], r"^\d\d-\d\d \d\d:\d\d:\d\d$")
        tasks = {t["slug"]: t for t in out["tasks"]}
        self.assertTrue(all(set(t) == JSON_KEYS for t in out["tasks"]))
        self.assertEqual([t["group"] for t in out["tasks"]], ["main"] * 5 + ["user", "orch", "orch", "wrapup"])
        self.assertEqual((tasks["t-unread"]["unread"], tasks["t-unread"]["reason"], tasks["t-unread"]["age_minutes"]),
                         (True, "新的等你决定（未读，2 小时前写的）", 120))
        self.assertEqual((tasks["t-answered"]["answered_after_rollup"], tasks["t-answered"]["reason"],
                          tasks["t-answered"]["status"]), (True, "已转答复，等它接着做", "受阻"))
        self.assertEqual((tasks["t-dialog"]["agent_status"], tasks["t-dialog"]["screen_status"]), ("done", "blocked"))
        self.assertEqual((tasks["t-gone"]["agent_status"], tasks["t-gone"]["name"]), ("gone", "orch-gone"))
        self.assertEqual((tasks["t-user"]["unread"], tasks["t-user"]["reason"], tasks["t-user"]["screen_status"]),
                         (False, None, None))
        self.assertRegex(tasks["t-wrap"]["rollup_mtime"], r"^\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(out["wrapup_ready"], ["t-wrap"])
        self.assertEqual((tasks["t-wrap"]["wrapup"], tasks["t-wrap"]["wrapup_reason"], tasks["t-wrap"]["wrapup_clicked"]),
                         ("ready", None, False))
        self.assertEqual((tasks["t-prog"]["wrapup"], tasks["t-prog"]["wrapup_reason"]), ("hold", "还没完成"))
        self.assertEqual((tasks["t-user"]["deliverables"], tasks["t-prog"]["deliverables"]), ([], []))
        self.assertEqual({s: t["display_state"] for s, t in tasks.items()}, {
            "t-unread": "等你决定", "t-user": "等你决定", "t-answered": "已转答复", "t-prog": "进行中", "t-wrap": "完成",
            "t-gone": "进行中", "t-replace": "需要换编排者", "t-stall": "进行中", "t-dialog": "进行中"})
        self.assertEqual((tasks["t-user"]["title"], tasks["t-user"]["ask"], tasks["t-answered"]["ask"]),
                         ("t-user", "看转达，回你的决定", "无"))
        mtime = (self.state / "t-user" / "rollup.md").stat().st_mtime
        self.assertAlmostEqual(tasks["t-user"]["state_since"], mtime, places=2)
        self.assertEqual(tasks["t-user"]["state_seconds"] // 60, 20 * 60)
        answered = json.loads((self.state / "t-answered" / "answered.json").read_text())["at"]
        self.assertAlmostEqual(tasks["t-answered"]["state_since"], answered, places=2)
        self.assertIsInstance(tasks["t-wrap"]["total_seconds"], int)

    def test_line_no_screens(self) -> None:
        """--line：按新的组数任务（等你 / 进行中 / 完成），不读屏幕，herdr agent read 一次都没调、不写 agent-state.json、时间线
        （setUp 里 drover rollup 读过的那几个已经有 main-read 那一条，--line 不往里加）、observe.json"""
        timelines = {p: p.read_text() for p in self.state.glob("*/timeline.jsonl")}
        agent_states = {p: p.read_text() for p in self.state.glob("*/agent-state.json")}  # setUp 里 t-stall 预先写的那份
        self.assertEqual(self.board("--line").strip(), "drover 等你 2 · 进行中 6 · 完成 1")
        self.assertEqual([c for c in self.herdr_calls() if c[:2] == ["agent", "read"]], [])
        self.assertEqual({p: p.read_text() for p in self.state.glob("*/agent-state.json")}, agent_states)  # 没修正过的状态不记
        self.assertEqual({p: p.read_text() for p in self.state.glob("*/timeline.jsonl")}, timelines)
        self.assertFalse((self.state / "observe.json").exists())


class BoardSinceTests(BoardTestCase):
    """要主会话处理的每个原因各带各的时长（见模块说明）。"""

    def agent_state(self, slug: str) -> dict:
        return json.loads((self.state / slug / "agent-state.json").read_text())

    def rewind(self, slug: str, seconds: float) -> None:
        """假时钟：agent-state.json 的 since 往前拨 seconds 秒（再多拨 SLACK），相当于这个状态已经持续了这么久。"""
        state = self.agent_state(slug)
        state["since"] -= seconds + SLACK
        (self.state / slug / "agent-state.json").write_text(json.dumps(state))

    def row(self, slug: str) -> str:
        """这个任务在"主会话在处理"里的那一段，写成"· <slug> · <原因>"（--json 的 reason，和看板最底下那行同一份文字）。"""
        return f"· {slug} · {self.tasks_json()[slug]['reason']}"

    def main_rows(self) -> str:
        """"主会话在处理"的每个任务一行"· <slug> · <原因>"，末尾带换行。"""
        return "".join(f"· {s} · {t['reason']}\n" for s, t in self.tasks_json().items() if t["group"] == "main")

    def test_user_case_dialog_time_not_rollup_age(self) -> None:
        """rollup 是 15 分钟前写的，编排者 1 分钟前才卡在确认框 → "卡在确认框 1 分钟"，
        不出现 15 分钟；第一次看到时写"刚发现"，不编时长；状态一直没变 since 不动、时长跟着涨"""
        self.task("t-a", "orch-a", "进行中", sections={"当前进度": ["- 改看板"]}, age=15 * MINUTE)
        self.set_agents(**{"orch-a": {"status": "blocked", "seq": 7, "screen": BASH_DIALOG}})
        self.assertEqual(self.row("t-a"), "· t-a · 卡在确认框（刚发现）")
        first = self.agent_state("t-a")
        self.assertEqual(set(first), {"name", "shown", "since", "seq", "dialog_fp"})
        self.assertEqual((first["name"], first["shown"], first["seq"]), ("orch-a", "blocked", 7))
        self.rewind("t-a", MINUTE)
        self.assertEqual(self.main_rows(), "· t-a · 卡在确认框 1 分钟\n")
        bottom = next(x for x in self.board().splitlines() if x.startswith("主会话在处理："))
        self.assertEqual(bottom, "主会话在处理：t-a 卡在确认框 1 分钟")  # 看板上就是这一行，没有 15 分钟
        since = self.agent_state("t-a")["since"]
        self.rewind("t-a", 2 * HOUR)
        self.assertEqual(self.row("t-a"), "· t-a · 卡在确认框 2 小时")
        self.assertEqual(self.agent_state("t-a")["since"], since - 2 * HOUR - SLACK)  # 没变：只有拨的那一下

    def test_new_dialog_restarts(self) -> None:
        """批完一个弹框又弹一个（herdr 的序号变了、弹框也换了）→ 时长重新算；只是序号变了（同样的弹框又弹一次）、
        只是弹框换了（herdr 没看到中间那下 working，序号没变）也重新算；弹框上方那行 ⏺ 闪了、读屏失败（沿用记录里的
        弹框指纹）不算换"""
        self.task("t-a", "orch-a", "进行中", age=HOUR)
        self.set_agents(**{"orch-a": {"status": "blocked", "seq": 7, "screen": PARALLEL_SECOND}})
        self.board()
        self.rewind("t-a", 10 * MINUTE)
        self.set_agents(**{"orch-a": {"status": "blocked", "seq": 7, "screen": PARALLEL_SECOND_BLINK}})
        self.assertEqual(self.row("t-a"), "· t-a · 卡在确认框 10 分钟")
        self.set_agents(**{"orch-a": {"status": "blocked", "seq": 9, "screen": bash_dialog("b")}})
        self.assertEqual(self.row("t-a"), "· t-a · 卡在确认框（刚发现）")
        self.rewind("t-a", 2 * MINUTE)
        self.assertEqual(self.row("t-a"), "· t-a · 卡在确认框 2 分钟")
        self.set_agents(**{"orch-a": {"status": "blocked", "seq": 11, "screen": bash_dialog("b")}})
        self.assertEqual(self.row("t-a"), "· t-a · 卡在确认框（刚发现）")
        self.rewind("t-a", 2 * MINUTE)
        self.set_agents(**{"orch-a": {"status": "blocked", "seq": 11, "screen": BASH_DIALOG}})
        self.assertEqual(self.row("t-a"), "· t-a · 卡在确认框（刚发现）")
        self.assertEqual(self.agent_state("t-a")["seq"], 11)
        fp = self.agent_state("t-a")["dialog_fp"]
        self.rewind("t-a", 3 * MINUTE)
        self.set_agents(**{"orch-a": {"status": "blocked", "seq": 11, "screen": BASH_DIALOG, "read_fail": True}})
        self.assertEqual(self.row("t-a"), "· t-a · 卡在确认框 3 分钟")
        self.assertEqual(self.agent_state("t-a")["dialog_fp"], fp)

    def test_api_error_and_gone_since(self) -> None:
        """接口错误、编排者已退出同样按状态本身持续了多久（rollup 是几小时前写的也不管）；状态变了（恢复 working 再出错）
        重新算；退出了的记录：shown 是 gone、seq 是 null"""
        self.task("t-e", "orch-e", "进行中", age=3 * HOUR)
        self.task("t-g", "orch-g", "进行中", age=5 * HOUR)
        self.set_agents(**{"orch-e": {"status": "done", "seq": 3, "screen": API_ERROR}})
        text = self.main_rows()
        self.assertIn("· t-e · 接口错误（刚发现）\n", text)
        self.assertIn("· t-g · 编排者已退出（刚发现，rollup：进行中）\n", text)
        self.assertEqual(self.agent_state("t-g"), {"name": "orch-g", "shown": "gone",
                                                   "since": self.agent_state("t-g")["since"], "seq": None})
        self.rewind("t-e", 5 * MINUTE)
        self.rewind("t-g", 2 * HOUR)
        text = self.main_rows()
        self.assertIn("· t-e · 接口错误 5 分钟\n", text)
        self.assertIn("· t-g · 编排者已退出 2 小时（rollup：进行中）\n", text)
        self.set_agents(**{"orch-e": {"status": "working", "seq": 4, "screen": NORMAL}})
        self.board()
        self.assertEqual(self.agent_state("t-e")["shown"], "working")
        self.set_agents(**{"orch-e": {"status": "done", "seq": 5, "screen": API_ERROR}})
        self.assertEqual(self.row("t-e"), "· t-e · 接口错误（刚发现）")

    def test_unread_and_dialog_each_own_time(self) -> None:
        """未读又卡在确认框：两个原因各带各的时长（未读按 rollup 写的时间，确认框按它自己），用"、"连起来；
        "停了 N 分钟没更新"、要换编排者后面不再接 rollup 的时长"""
        self.task("t-a", "orch-a", "等你决定", sections={"需要你决定": ["- 选 A 还是 B"]}, age=15 * MINUTE)
        self.task("t-s", "orch-s", "进行中", sections={"当前进度": ["- 跑测试"]}, age=HOUR)
        self.stopped("t-s", "orch-s", "idle", 25 * MINUTE, seq=4)
        self.task("t-r", "orch-r", "需要换编排者", age=40 * MINUTE, read=True)
        self.set_agents(**{"orch-a": {"status": "done", "seq": 3, "screen": WEBFETCH},
                           "orch-s": {"status": "idle", "seq": 4, "screen": NORMAL},
                           "orch-r": {"status": "done", "seq": 5, "screen": NORMAL}})
        self.assertEqual(self.row("t-a"), "· t-a · 新的等你决定（未读，15 分钟前写的）、卡在确认框（刚发现）")
        self.rewind("t-a", MINUTE)
        text = self.main_rows()
        self.assertIn("· t-a · 新的等你决定（未读，15 分钟前写的）、卡在确认框 1 分钟\n", text)
        self.assertIn("· t-s · 停了 25 分钟没更新\n", text)
        self.assertIn("· t-r · 要换编排者（40 分钟前写的）\n", text)

    def test_json_reasons(self) -> None:
        """--json 的 reasons：每个原因的 kind、text、since（epoch）、since_source；第一次看到的状态 since 是 null、
        unknown；reason 是各个 text 用"、"连起来，age_minutes 照旧是 rollup 的；转过答复的（编排者在做）也有一项"""
        self.task("t-a", "orch-a", "等你决定", sections={"需要你决定": ["- 选 A 还是 B"]}, age=15 * MINUTE)
        self.task("t-s", "orch-s", "进行中", age=HOUR)
        self.stopped("t-s", "orch-s", "idle", 25 * MINUTE, seq=4)
        idle_since = round(self.agent_state("t-s")["since"], 3)  # 停下的起点取到毫秒，同时间线（stall_since）
        self.task("t-r", "orch-r", "需要换编排者", age=40 * MINUTE, read=True)
        self.task("t-g", "orch-g", "进行中", age=HOUR)
        self.task("t-q", "orch-q", "受阻", age=HOUR, read=True, answered_ago=5 * MINUTE)
        self.set_agents(**{"orch-a": {"status": "done", "seq": 3, "screen": WEBFETCH},
                           "orch-s": {"status": "idle", "seq": 4, "screen": NORMAL},
                           "orch-r": {"status": "done", "seq": 5, "screen": NORMAL},
                           "orch-q": {"status": "working", "seq": 6, "screen": NORMAL}})
        tasks = {t["slug"]: t for t in json.loads(self.board("--json"))["tasks"]}
        mtime = (self.state / "t-a" / "rollup.md").stat().st_mtime
        self.assertEqual(tasks["t-a"]["reasons"], [
            {"kind": "unread", "text": "新的等你决定（未读，15 分钟前写的）", "since": mtime, "since_source": "rollup"},
            {"kind": "blocked", "text": "卡在确认框（刚发现）", "since": None, "since_source": "unknown"}])
        self.assertEqual(tasks["t-a"]["reason"], "新的等你决定（未读，15 分钟前写的）、卡在确认框（刚发现）")
        self.assertEqual(tasks["t-a"]["age_minutes"], 15)
        self.rewind("t-a", MINUTE)
        self.rewind("t-g", 3 * MINUTE)
        tasks = {t["slug"]: t for t in json.loads(self.board("--json"))["tasks"]}
        self.assertEqual(tasks["t-a"]["reasons"][1], {"kind": "blocked", "text": "卡在确认框 1 分钟",
                                                      "since": self.agent_state("t-a")["since"],
                                                      "since_source": "agent-state"})
        self.assertEqual(tasks["t-g"]["reasons"], [{"kind": "gone", "text": "编排者已退出 3 分钟（rollup：进行中）",
                                                    "since": self.agent_state("t-g")["since"],
                                                    "since_source": "agent-state"}])
        self.assertEqual(tasks["t-s"]["reasons"], [{"kind": "stalled", "text": "停了 25 分钟没更新", "since": idle_since,
                                                    "since_source": "agent-state"}])
        self.assertEqual([(r["kind"], r["since_source"]) for r in tasks["t-r"]["reasons"]], [("replace", "rollup")])
        self.assertEqual((tasks["t-q"]["group"], tasks["t-q"]["reasons"][0]["kind"], tasks["t-q"]["reasons"][0]["text"],
                          tasks["t-q"]["reasons"][0]["since_source"]), ("orch", "answered", "已转答复，等它接着做", "answered"))
        self.assertEqual(tasks["t-q"]["reason"], "已转答复，等它接着做")


class BoardOutputTests(BoardTestCase):
    def test_unread_done_stays_until_read(self) -> None:
        """完成、主会话还没读：编排者已退出的（orch-start 派发、有时间线）照样留在看板，"主会话在处理"写未读、不写编排者已退出；
        编排者还开着的不能一键收尾：wrapup 是 hold、wrapup_reason "结论还没转达"、不在 wrapup_ready，收尾格"先别：结论还没转达"、
        没有"全部收尾"。主会话读过之后：退出的那个才算收尾完关掉（不显示），开着的能收尾。装上已读记录之前的老任务（已退出 +
        完成、没读过、没有时间线）算已读，不显示"""
        self.task("t-exit", "orch-exit", "完成", summary="推送了", wrapup="可以", age=HOUR)
        self.started("t-exit")
        self.task("t-live", "orch-live", "完成", summary="也推送了", wrapup="可以", age=HOUR)
        self.task("t-old", "orch-old", "完成", age=30 * HOUR)
        self.set_agents(**{"orch-live": {"status": "idle", "screen": NORMAL}})
        out = json.loads(self.board("--json"))
        tasks = {t["slug"]: t for t in out["tasks"]}
        self.assertEqual(sorted(tasks), ["t-exit", "t-live"])
        self.assertEqual((tasks["t-exit"]["group"], tasks["t-exit"]["reason"]), ("main", "新的完成（未读，1 小时前写的）"))
        self.assertEqual((tasks["t-live"]["wrapup"], tasks["t-live"]["wrapup_reason"]), ("hold", "结论还没转达"))
        self.assertEqual(out["wrapup_ready"], [])
        md = self.board()
        self.assertIn("| t-live | 也推送了 | 无 | 1 小时 | t-live | 先别：结论还没转达 |", md)
        self.assertNotIn("全部收尾", md)
        self.main_rollup("--slug", "t-exit")
        self.main_rollup("--slug", "t-live")
        out = json.loads(self.board("--json"))
        self.assertEqual(([t["slug"] for t in out["tasks"]], out["wrapup_ready"]), (["t-live"], ["t-live"]))

    def test_json_tab_id(self) -> None:
        """--json 每个任务带 tab_id：orch.json 里编排者所在的 tab（主会话收尾、换编排者时直接 herdr tab close 它）；旧的
        orch.json 没有这个键是 null"""
        self.task("t-a", "orch-a", "进行中")
        meta = self.state / "t-a" / "orch.json"
        meta.write_text(json.dumps({**json.loads(meta.read_text()), "tab_id": "w9:t3"}))
        self.task("t-b", "orch-b", "进行中")
        self.set_agents(**{"orch-a": {"status": "working", "screen": NORMAL}, "orch-b": {"status": "working", "screen": NORMAL}})
        tasks = self.tasks_json()
        self.assertEqual((tasks["t-a"]["tab_id"], tasks["t-b"]["tab_id"]), ("w9:t3", None))

    def test_answered_by_content(self) -> None:
        """"转过答复、rollup 还没更新"按内容比（answered.json 记的 digest），不按修改时间：编排者把 rollup 原样重写（修改时间
        在转答复之后）→ 还是已转答复；改了内容 → 不算（等你决定，已读）"""
        self.task("t-q", "orch-q", "等你决定", sections={"需要你决定": ["- 选 A 还是 B"]}, age=HOUR, read=True,
                  answered_ago=5 * MINUTE)
        path = self.state / "t-q" / "rollup.md"
        path.write_text(path.read_text())  # 原样重写：修改时间是现在
        self.set_agents(**{"orch-q": {"status": "done", "screen": NORMAL}})
        task = self.tasks_json()["t-q"]
        self.assertEqual((task["display_state"], task["answered_after_rollup"]), ("已转答复", True))
        path.write_text(path.read_text() + "- 又多了一个问题\n")
        self.main_rollup("--slug", "t-q")
        task = self.tasks_json()["t-q"]
        self.assertEqual((task["display_state"], task["answered_after_rollup"]), ("等你决定", False))

    def test_line_zero_and_empty(self) -> None:
        """--line 为 0 的项不写；一个任务都没有（只有退出了的完成任务）→ "drover 无任务"，看板写"没有任务\""""
        self.task("t-old", "orch-old", "完成", age=HOUR)
        self.set_agents()
        self.assertEqual(self.board("--line").strip(), "drover 无任务")
        self.assertRegex(self.board().strip(), HEAD_MD + r"没有任务 · drover " + VERSION + "$")
        self.assertRegex(self.board("--plain").strip(), HEAD_PLAIN + r"没有任务$")
        self.task("t-a", "orch-a", "等你决定", age=HOUR, read=True)
        self.task("t-b", "orch-b", "受阻", age=HOUR, read=True)
        self.set_agents(**{"orch-a": {"status": "done"}, "orch-b": {"status": "idle"}})
        self.assertEqual(self.board("--line").strip(), "drover 等你 2")

    def test_summary_rules_and_fallback(self) -> None:
        """摘要：先取"摘要："；没有就按状态取（等你决定取需要你决定第一行、进行中取当前进度最后一行、完成 / 受阻取结论里第一个
        不以"目标："开头的行，都去掉"- "）；都没有是空串。看板的"当前状态"用它（--plain 不截），没有摘要、没有 rollup 写一句占位；
        没有 rollup 的状态持续从派发算（刚派发的写"刚开始"）"""
        d = self.load_drover()
        cases = [
            ("等你决定", "摘要：要定 X", {"需要你决定": ["- 第一行"]}, "要定 X"),
            ("等你决定", None, {"当前进度": ["- 不取这里"], "需要你决定": ["", "- 选 A 还是 B", "- 第二个问题"]}, "选 A 还是 B"),
            ("进行中", None, {"当前进度": ["- 读代码", "- 写自测"], "结论": ["- 不取这里"]}, "写自测"),
            ("完成（已收尾）", None, {"结论": ["目标：做看板", "- 推送了 abc"]}, "推送了 abc"),
            ("受阻", None, {"结论": ["- 卡在权限上"]}, "卡在权限上"),
            ("等你决定", None, {"当前进度": ["- 只有进度"]}, ""),
        ]
        for status, field, sections, want in cases:
            with self.subTest(status=status, want=want):
                text = f"# x\n状态：{status}\n" + (field + "\n" if field else "") + "\n" + "".join(
                    f"## {t}\n" + "\n".join(body) + "\n\n" for t, body in sections.items())
                path = self.tmp / "rollup.md"
                path.write_text(text)
                self.assertEqual(d.rollup_summary(d.read_rollup(path)), want)
        long = "很长的摘要" * 16
        self.task("t-long", "orch-long", "进行中", summary=long, age=HOUR)
        self.task("t-none", "orch-none", "进行中", age=HOUR)
        self.task("t-norollup", "orch-norollup")
        self.set_agents(**{"orch-long": {"status": "working"}, "orch-none": {"status": "working"},
                           "orch-norollup": {"status": "working"}})
        lines = self.board("--plain").splitlines()
        self.assertIn(f"  {long}｜要你：无｜已持续 1 小时", lines)  # 160 格也整句写：飞书在手机上自己换行
        self.assertIn("  rollup 里没有摘要｜要你：无｜已持续 1 小时", lines)
        self.assertIn("  还没写 rollup｜要你：无｜刚开始", lines)

    def test_pending(self) -> None:
        """--pending：只列等你那组（未读的也算：它也在等用户），最多 3 个、等得久的在前，多了加"...还有 N 个"；--except 可以写
        多次；一个都没有时不输出、退出码 0。每个一行"· <标题>（<代号>）：<要你做什么>（已等 <状态持续>）"，标题就是代号时只写一次"""
        for i in range(1, 6):
            self.task(f"u{i}", f"orch-u{i}", "等你决定", sections={"需要你决定": [f"- 问题 {i}", "- 答法：回 A 或 B"]},
                      age=(6 - i) * HOUR, read=True)
        self.task("t-main", "orch-main", "等你决定", sections={"需要你决定": ["- 问题 M"]}, age=HOUR, title="主任务")  # 未读
        self.set_agents(**{f"orch-u{i}": {"status": "done"} for i in range(1, 6)}, **{"orch-main": {"status": "done"}})
        self.assertEqual(self.board("--pending").splitlines(), [
            "其他在等你的：", "· u1：回 A 或 B（已等 5 小时）", "· u2：回 A 或 B（已等 4 小时）",
            "· u3：回 A 或 B（已等 3 小时）", '...还有 3 个，回"看板"看全部'])
        self.assertEqual(self.board("--pending", "--except", "u1", "--except", "u3").splitlines(), [
            "其他在等你的：", "· u2：回 A 或 B（已等 4 小时）", "· u4：回 A 或 B（已等 2 小时）",
            "· u5：回 A 或 B（已等 1 小时）", '...还有 1 个，回"看板"看全部'])
        args = [x for i in range(1, 6) for x in ("--except", f"u{i}")]
        self.assertEqual(self.board("--pending", *args).splitlines(),
                         ["其他在等你的：", "· 主任务（t-main）：看转达，回你的决定（已等 1 小时）"])
        self.assertEqual(self.board("--pending", *args, "--except", "t-main"), "")
        self.assertIn("--except 只给 board --pending 用", self.drover("board", "--except", "u1", rc=2).stderr)

    def test_live_one_round(self) -> None:
        """--live 跑 1 轮：清屏后画看板（不在看板 tab 里：没有按钮）；一个任务也照样有任务列；herdr 连不上时显示一行提示
        （退出码 0，接着循环）；按显示宽度截断（中文占两列，截断的末尾是 ASCII 的"..."，算 3 格）"""
        self.task("t-a", "orch-a", "进行中", sections={"当前进度": ["- 写自测"]}, age=HOUR)
        self.set_agents(**{"orch-a": {"status": "working"}})
        self.env["COLUMNS"] = "80"
        out = self.board("--live", "--iterations", "1", "--interval", "0")
        self.assertTrue(out.startswith("\033[H\033[2J"), repr(out[:20]))
        self.assertRegex(out.splitlines()[0], r"· 1 个任务 · drover " + VERSION + "$")  # 一个任务也不再把任务名写进第一行
        self.assertIn("\n\n进行中（1）\n┌──────┬──────────┬────────────┬──────────┬──────┐\n"
                      "│ 任务 │ 当前状态 │ 要你做什么 │ 状态持续 │ 代号 │\n"
                      "├──────┼──────────┼────────────┼──────────┼──────┤\n"
                      "│ t-a  │ 写自测   │ 无         │ 1 小时   │ t-a  │\n"
                      "└──────┴──────────┴────────────┴──────────┴──────┘", out)
        self.assertNotIn("◀", out)
        (self.fakebin / "herdr-state.json").write_text("{坏的 JSON")
        out = self.board("--live", "--iterations", "1", "--interval", "0")
        self.assertIn("herdr 连不上", out)
        d = self.load_drover()
        self.assertEqual(d.fit_width("中文abc", 5), "中...")  # "..." 占 3 格，前面只剩 2 格
        self.assertEqual(d.fit_width("中文abc", 6), "中...")  # 放不下半个汉字时少一格
        self.assertEqual(d.fit_width("中文abcd", 7), "中文...")
        self.assertEqual(d.fit_width("中文ab", 6), "中文ab")


def cols(d, line: str, start: int, end: int) -> str:
    """line 里占显示列 [start, end) 的那几个字（中文两格）。"""
    out, col = "", 0
    for ch in line:
        if start <= col and col + d.char_width(ch) <= end:
            out += ch
        col += d.char_width(ch)
    return out


def box_tables(lines: list) -> list:
    """画出来的一屏里的框线表：每张表是从"┌"到"└"的那几行。"""
    tables, current = [], None
    for line in lines:
        if line.startswith("┌"):
            current = []
        if current is not None:
            current.append(line)
        if line.startswith("└"):
            tables.append(current)
            current = None
    return tables


def box_cells(line: str) -> list:
    """框线表的一行（"│ a │ b │"）拆成去掉两边空格的格子。"""
    return [cell.strip() for cell in line.split("│")[1:-1]]


def box_rows(table: list) -> list:
    """一张框线表（box_tables 的一项）里的各个任务（表头不算）：任务之间是横线，一个任务的几行"│ … │"按列合起来，每格是它的
    几行字（去掉末尾补齐用的空行）：[[["任务名"], ["当前状态第 1 行", "第 2 行"], …], …]。"""
    rows, current = [], []
    for line in table[3:]:  # ┌、表头、├ 之后
        if line.startswith("│"):
            current.append(box_cells(line))
            continue
        cells = [list(col) for col in zip(*current)]
        for col in cells:
            while len(col) > 1 and not col[-1]:
                col.pop()
        rows.append(cells)
        current = []
    return rows


BOX_CHARS = "│┌┬┐├┼┤└┴┘"


def bar_cols(line: str) -> list:
    """框线表一行里竖线、交叉（BOX_CHARS）所在的显示列（测试自己算宽度）。"""
    return [text_cols(line[:i]) for i, ch in enumerate(line) if ch in BOX_CHARS]


def bar_errors(table: list) -> list:
    """一张框线表（box_tables 的一项）里竖线、交叉画得不对的行，全对是 []。提醒行和它上下那两条线以外，每行的竖线都在表头
    那几列；提醒上面那条线是"│ ├ ┴…┴ ┤"，位置就是上面那几列竖线的位置；提醒那几行只有两头和任务列右边那根（合并处没有竖线）；
    下面那条线后面还有任务是"├ ┼ ┬…┬ ┤"，位置就是下面那几列竖线的位置，是底线是"└ ┴ ┘"（合并处一条直线）。只画了任务列和一列
    时这两条线上没有 ┴ / ┬。"""
    head = bar_cols(table[1])
    merged, inner = [head[0], head[1], head[-1]], len(head) - 3  # 任务列右边那根和最右边那根之间的竖线
    errors, noted = [], False
    for line in table:
        got = (bar_cols(line), "".join(ch for ch in line if ch in BOX_CHARS))
        if line.startswith("│") and "├" in line:
            want, noted = (head, "│├" + "┴" * inner + "┤"), True
        elif noted and line.startswith("│"):
            want = (merged, "│││")
        elif noted:
            want = (merged, "└┴┘") if line.startswith("└") else (head, "├┼" + "┬" * inner + "┤")
            noted = False
        else:
            want = (head, got[1])  # 别的行只看位置
        if got != want:
            errors.append(line)
    return errors


class BoardV2Tests(BoardTestCase):
    """旧格式留下来的：旧 rollup 的"## 交付"只进 --json 的 deliverables、"收尾："的解析、送进主会话。"""

    def test_parse_deliverables_and_wrapup(self) -> None:
        """"## 交付"（已经从 rollup 格式里删了，旧 rollup 照样解析给 --json）：开头只认 可以看 / 处理中 / 等你 / 提醒（其余的行不认），
        " — "（或"——"）前面是项目、后面是状态，提醒整句放状态；没有这一节、一行都认不出、没有 rollup 都是 []（不再退回按状态
        造一项）；"收尾："可以 / 先别，<原因>，没写时完成算可以、别的状态"还没完成\""""
        d = self.load_drover()
        roll = self.rollup_of(d, "# x\n状态：进行中\n收尾：先别，有没推的改动\n摘要：在做\n\n## 交付\n"
                                 "- 可以看：导出按钮恢复原样 — 两端已合入，要跑最新主分支才看得到\n"
                                 "- 处理中：CSV 乱码、首次慢 — 正在取原文件和耗时日志\n"
                                 "- 等你：测试环境部署 — 两个新接口已推 release/1.2，部署好回我一句\n"
                                 "- 提醒：测试环境现在不是最新 main — 新按钮要部署新版才看得到\n"
                                 "- 随手写的一行\n* 可以看：只有项目\n- 可以看：订单列表页 —— 已部署到测试环境\n\n## 当前进度\n"
                                 "- 可以看：不在交付里\n")
        self.assertEqual(roll["fields"]["收尾"], "先别，有没推的改动")
        self.assertEqual(d.rollup_deliverables(roll), [
            {"kind": "look", "item": "导出按钮恢复原样", "status": "两端已合入，要跑最新主分支才看得到"},
            {"kind": "doing", "item": "CSV 乱码、首次慢", "status": "正在取原文件和耗时日志"},
            {"kind": "user", "item": "测试环境部署", "status": "两个新接口已推 release/1.2，部署好回我一句"},
            {"kind": "note", "item": "", "status": "测试环境现在不是最新 main — 新按钮要部署新版才看得到"},
            {"kind": "look", "item": "只有项目", "status": ""},
            {"kind": "look", "item": "订单列表页", "status": "已部署到测试环境"}])
        self.assertEqual(d.rollup_wrapup(roll), (False, "有没推的改动", True))
        for text in ("# x\n状态：等你决定\n摘要：导出改走哪个队列\n", "# x\n状态：进行中\n\n## 交付\n- 随手写的一行\n"):
            with self.subTest(text=text):
                self.assertEqual(d.rollup_deliverables(self.rollup_of(d, text)), [])
        self.assertEqual(d.rollup_deliverables(None), [])
        for head, want in (("状态：完成\n收尾：可以", (True, None, True)), ("状态：完成\n收尾：先别", (False, "先别", True)),
                           ("状态：完成", (True, None, False)), ("状态：进行中", (False, "还没完成", False)),
                           ("状态：进行中\n收尾：可以，做完再收", (True, None, True))):
            with self.subTest(head=head):
                self.assertEqual(d.rollup_wrapup(self.rollup_of(d, f"# x\n{head}\n")), want)
        self.assertEqual(d.rollup_wrapup(None), (False, "还没完成", False))

    def test_send_main_and_downgrade(self) -> None:
        """送进主会话：调插件的 bin/run-node.sh src/actions/send-main.mjs --source board --text "<话>"（工作目录是插件目录，
        话里不带 [看板] 前缀，插件自己加），返回 (True, 插件给的 notice)；插件报错写原因和口令；没装插件、旧版本没有
        send-main.mjs → "点不了……在主对话里说'全部收尾'"。没送成的都是 False（看板不记成点过）"""
        d = self.load_drover()
        self.assertEqual(d.BOARD_NO_SEND, "点不了：飞书插件没装或版本太旧，在主对话里说'全部收尾'")
        self.assertEqual(d.board_send_main("收尾：doctor"), (False, d.BOARD_NO_SEND))
        root = self.fake_plugin(script=False)
        self.assertEqual(d.board_send_main("收尾：doctor"), (False, d.BOARD_NO_SEND))
        self.assertEqual(self.send_calls(root), [])
        self.fake_plugin(reply={"ok": True, "result": "queued", "reason": "draft",
                                "notice": "主会话输入框里有没发的内容，已排队，发完自动送"})
        self.assertEqual(d.board_send_main("全部收尾：doctor、board"), (True, "主会话输入框里有没发的内容，已排队，发完自动送"))
        self.assertEqual(self.send_calls(root), [[str(root), "src/actions/send-main.mjs", "--source", "board", "--text",
                                                  "全部收尾：doctor、board"]])
        self.fake_plugin(reply={"ok": False, "error": "主会话没登记"})
        self.assertEqual(d.board_send_main("收尾：doctor"),
                         (False, "没送进主会话（主会话没登记），在主对话里说'收尾：doctor'"))


LONG_SLUG = "drover-very-long-task-slug-for-limits"  # 37 格
LONG_TITLE = "一二三四五六七八九十" * 3  # 60 格
LONG_STATE = "甲乙丙丁戊己庚辛壬癸" * 4  # 80 格
LONG_ASK = "abcdefghijklmnopqrstuvwxyz0123456789"  # 36 格


class BoardTableTests(BoardTestCase):
    """每格上限（任务 24、当前状态 40、要你做什么 30、代号 24，中文两格，超出截断加"..."；状态持续、收尾不截；--live 里当前状态、
    要你做什么折行，见 BoardV6Tests）和旧 rollup 的退回（当前状态取正文那一行的第一句、要你做什么取答法或通用动作）。"""

    def long_tasks(self) -> None:
        """一个各格都超长的完成任务（先别收尾，原因也超长）和一个短的进行中任务。"""
        self.task(LONG_SLUG, "orch-long", "完成", title=LONG_TITLE, summary=LONG_STATE, ask=LONG_ASK,
                  wrapup="先别，等你把测试环境部署完、验证好再收", age=HOUR, read=True)
        self.task("short", "orch-short", "进行中", summary="在做", age=5 * MINUTE)
        self.set_agents(**{"orch-long": {"status": "idle", "screen": NORMAL},
                           "orch-short": {"status": "working", "screen": NORMAL}})

    def test_cell_limits(self) -> None:
        """Markdown：任务 24 格、当前状态 40 格、要你做什么 30 格、代号 24 格，按显示宽度算（中文两格，放不下半个汉字时少一格），
        超出的截断、末尾加"..."（算 3 格）；"先别"的原因 20 格（10 个字），哪种写法都截；--plain 只截任务名，当前状态、要你做什么
        整句写；--live 任务、代号照旧截，当前状态（80 格）折成 3 行、每行 28 格，要你做什么（36 个字母的一个词）硬断成 22 格加 14 格；
        状态持续不截；--json 的 title、ask 不截"""
        self.long_tasks()
        d = self.load_drover()
        title, state, ask, slug = (d.fit_width(LONG_TITLE, 24), d.fit_width(LONG_STATE, 40), d.fit_width(LONG_ASK, 30),
                                   d.fit_width(LONG_SLUG, 24))
        hold = "等你把测试环境部..."
        self.assertEqual((title, state, ask, slug), ("一二三四五六七八九十...", LONG_STATE[:18] + "...",
                                                     LONG_ASK[:27] + "...", LONG_SLUG[:21] + "..."))
        self.assertEqual([d.text_width(x) for x in (title, state, ask, slug, hold)], [23, 39, 30, 24, 19])
        md = self.board().splitlines()
        self.assertIn(f"| {title} | {state} | {ask} | 1 小时 | {slug} | 先别：{hold} |", md)
        self.assertIn("| short | 在做 | 无 | 5 分钟 | short |", md)
        lines = self.board("--plain").splitlines()
        self.assertEqual(lines[lines.index("完成（1）") + 1:lines.index("完成（1）") + 3],
                         [f"· {title}（{LONG_SLUG}）", f"  {LONG_STATE}｜要你：{LONG_ASK}｜已持续 1 小时｜先别收尾：{hold}"])
        table = box_tables(self.live_lines(200))[-1]
        self.assertEqual(box_rows(table), [[[title], [LONG_STATE[:14], LONG_STATE[14:28], LONG_STATE[28:]],
                                            [LONG_ASK[:22], LONG_ASK[22:]], ["1 小时"], [slug], [f"先别：{hold}"]]])
        task = self.tasks_json()[LONG_SLUG]
        self.assertEqual((task["title"], task["ask"]), (LONG_TITLE, LONG_ASK))

    def test_old_rollup_sample(self) -> None:
        """旧格式的 rollup（等你决定，没有摘要、标题、要你做，"需要你决定"第一行没有"："，最后有"- 答法："）：任务列是代号，
        当前状态是第一行的第一句（Markdown 截到 40 格，--live 折行：~/ 开头的路径整个挪到第二行、比一行长才硬断），要你做什么是
        答法那一句（Markdown、--pending 截到 30 格，--plain 都不截）；进行中的旧 rollup 摘要整句当当前状态，要你做什么"无"。
        哪种写法都不出现"等了\""""
        self.task("export", "orch-export", "等你决定", age=27 * HOUR, read=True, sections={"需要你决定": [
            "调研完了，全文见 ~/.drover/orch-demo-export-job/survey.md。推荐不变，仍是 A；规模比之前估计的小，A 跟着简化。",
            "- 规模：同步导出的入口 10 个，实际要改的 9 个",
            "- A【建议，简化版】web-app 里加一张\"导出任务 → 结果文件\"表",
            "- 答法：回 A / B / C / D，加上\"默认做法同意\"或要改的条目。选了就另起实施任务（A 含 1 条迁移，先在测试环境执行）。"]})
        summary = "你定了容量自动校准，已加给 dr-sched；两个 worker 在实施和验证"
        self.task("heavy", "orch-heavy", "进行中", summary=summary, age=46 * MINUTE,
                  sections={"当前进度": ["- dr-sched 在做实机验证"]})
        self.set_agents(**{"orch-export": {"status": "done", "screen": NORMAL},
                           "orch-heavy": {"status": "working", "screen": NORMAL}})
        d = self.load_drover()
        tasks = self.tasks_json()
        answer = "回 A / B / C / D，加上\"默认做法同意\"或要改的条目"
        self.assertEqual((tasks["export"]["title"], tasks["export"]["ask"], tasks["export"]["deliverables"]),
                         ("export", answer, []))
        self.assertEqual((tasks["heavy"]["ask"], tasks["heavy"]["summary"]), ("无", summary))
        full = "调研完了，全文见 ~/.drover/orch-demo-export-job/survey.md"
        state = d.fit_width(full, 40)
        progress = d.fit_width(summary, 40)
        md = self.board()
        self.assertIn(f"| export | {state} | {d.fit_width(answer, 30)} | 27 小时 | export |", md)
        self.assertIn(f"| heavy | {progress} | 无 | 46 分钟 | heavy |", md)
        plain = self.board("--plain")
        self.assertIn(f"\n等你（1）\n· export\n  {full}｜要你：{answer}｜已持续 27 小时\n进行中（1）\n"
                      f"· heavy\n  {summary}｜要你：无｜已持续 46 分钟", plain)
        pending = self.board("--pending")
        self.assertEqual(pending.splitlines(), ["其他在等你的：", f"· export：{d.fit_width(answer, 30)}（已等 27 小时）"])
        live = "\n".join(self.live_lines(140))
        self.assertEqual([box_rows(t) for t in box_tables(live.split("\n"))], [
            [[["export"], ["调研完了，全文见", "~/.drover/orch-demo-export-j", "ob/survey.md"],
              ["回 A / B / C / D，加上", "\"默认做法同意\"或要改的", "条目"], ["27 小时"], ["export"]]],
            [[["heavy"], ["你定了容量自动校准，已加给", "dr-sched；两个 worker 在实施", "和验证"], ["无"], ["46 分钟"],
              ["heavy"]]]])
        for text in (md, plain, pending, live):
            self.assertNotIn("等了", text)

    def test_old_rollup_generic_ask(self) -> None:
        """旧 rollup 没有"要你做："：等你决定取"答法："那一句（带不带"- "、半角冒号都认），没有写"看转达，回你的决定"；受阻同样先取
        答法，没有写"看转达，定下一步"，当前状态前面加"受阻："（取"## 结论"第一个不以"目标："开头的行的第一句）；完成、进行中、需要换
        编排者写"无"（需要换编排者前面加"换编排者中："）；已转答复不管 rollup 写了什么都是"无"、当前状态"已转答复，等它接着做"；
        rollup 写了"要你做："就用它"""
        d = self.load_drover()
        cases = [
            ("等你决定", "", {"需要你决定": ["- 要不要把 X 删掉；删了省事", "- A 删", "- B 留"]},
             ("要不要把 X 删掉", "看转达，回你的决定")),
            ("受阻", "", {"结论": ["目标：修好权限", "- 卡在权限上。要管理员开 admin 权限"]},
             ("受阻：卡在权限上", "看转达，定下一步")),
            ("受阻", "", {"需要你决定": ["- 答法：回\"重试\"或\"放弃\""], "结论": ["- 卡在权限上"]},
             ("受阻：卡在权限上", "回\"重试\"或\"放弃\"")),
            ("等你决定", "", {"需要你决定": ["推不推 drover main（建议 A）：", "- A 推", "答法：回\"推\"或\"先不推\"。"]},
             ("推不推 drover main（建议 A）：", "回\"推\"或\"先不推\"")),
            ("等你决定", "", {"需要你决定": ["- 选哪个", "* 答法: 回 1 或 2；都行"]}, ("选哪个", "回 1 或 2")),
            ("等你决定", "要你做：回 A 或 B\n", {"需要你决定": ["- 选哪个", "- 答法：不取这里"]}, ("选哪个", "回 A 或 B")),
            ("完成", "", {"结论": ["目标：做看板", "- 做完了：已推 main"]}, ("做完了：已推 main", "无")),
            ("需要换编排者", "摘要：上下文快满了\n", {}, ("换编排者中：上下文快满了", "无")),
            ("进行中", "要你做：部署好测试环境回我一句\n", {}, ("rollup 里没有摘要", "部署好测试环境回我一句")),
        ]
        for status, head, sections, want in cases:
            with self.subTest(status=status, sections=sections):
                roll = self.rollup_of(d, f"# x\n状态：{status}\n{head}\n" + "".join(
                    f"## {t}\n" + "\n".join(b) + "\n\n" for t, b in sections.items()))
                state = d.display_state(roll, None)
                self.assertEqual((d.board_state_text(state, roll), d.board_ask(state, roll)), want)
        roll = self.rollup_of(d, "# x\n状态：等你决定\n要你做：回 A 或 B\n")
        self.assertEqual(d.display_state(roll, roll["mtime"] + 1), "已转答复")
        self.assertEqual((d.board_state_text("已转答复", roll), d.board_ask("已转答复", roll)), ("已转答复，等它接着做", "无"))
        self.assertEqual((d.display_state(None, None), d.board_state_text("进行中", None), d.board_ask("进行中", None)),
                         ("进行中", "还没写 rollup", "无"))


# design_tasks 那三个任务在终端够宽时（DESIGN_WIDE）、100 格往右滚了一列时（DESIGN_SCROLLED）该画成的样子（当前状态 28 格、
# 要你做什么 22 格一行，写长了折行），逐行比对（标题行的时间、版本除外）。守中文禁则：heavy 的当前状态不守禁则会折成"…自测 53 秒" /
# "，已推"（第二行以逗号开头），守了以后"秒"跟着逗号下来。往右滚了以后当前状态那一列藏起来了，heavy 那个任务照样占两行（行数按
# 所有列算，滚动时表不会上下跳）。heavy 的提醒不单独列在最底下，写在 heavy 最后一行下面、后面几列合成的一格里（宽屏这一格 100 格、
# 往右滚了以后 69 格，都放得下一行）；这一格和 heavy 的任务行之间一条线，合并的几列在线上收成 ┴，heavy 是表里最后一个任务，底线在
# 合并处一条直线。
NOTE_HEAVY = "每台机器上每种命令第一次会单独跑一次，之后按实测排"
DESIGN_WIDE = f"""\
等你（2）
┌──────────────────────┬──────────────────────────────┬────────────────────────┬──────────────┬──────────────────────┐
│ 任务                 │ 当前状态                     │ 要你做什么             │ 状态持续     │ 代号                 │
├──────────────────────┼──────────────────────────────┼────────────────────────┼──────────────┼──────────────────────┤
│ 订单导出改成异步处理 │ 暂停，你要先弄清各功能点     │ 回 A/B/C/D（建议 A）   │ 29 小时      │ orch-demo-export-job │
├──────────────────────┼──────────────────────────────┼────────────────────────┼──────────────┼──────────────────────┤
│ drover 任务看板      │ 设计稿出好了                 │ 看设计稿，回确认或要改 │ 刚刚         │ orch-demo-board      │
│                      │                              │ 的                     │              │                      │
└──────────────────────┴──────────────────────────────┴────────────────────────┴──────────────┴──────────────────────┘

完成（1）  [全部收尾（1 个）]
┌──────────────────────┬──────────────────────────────┬────────────────────────┬──────────────┬──────────────────────┬────────┐
│ 任务                 │ 当前状态                     │ 要你做什么             │ 状态持续     │ 代号                 │ 收尾   │
├──────────────────────┼──────────────────────────────┼────────────────────────┼──────────────┼──────────────────────┼────────┤
│ drover 检查提速      │ heavy 按资源调度、自测 53    │ 无                     │ 2 小时 10 分 │ orch-demo-perf       │ [收尾] │
│                      │ 秒，已推                     │                        │              │                      │        │
│                      ├──────────────────────────────┴────────────────────────┴──────────────┴──────────────────────┴────────┤
│                      │ 提醒：{NOTE_HEAVY}{' ' * 44} │
└──────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────────┘"""
DESIGN_SCROLLED = f"""\
等你（2）
┌──────────────────────┬────────────────────────┬──────────────┬──────────────────────┐
│ 任务                 │ 要你做什么             │ 状态持续     │ 代号                 │
├──────────────────────┼────────────────────────┼──────────────┼──────────────────────┤
│ 订单导出改成异步处理 │ 回 A/B/C/D（建议 A）   │ 29 小时      │ orch-demo-export-job │
├──────────────────────┼────────────────────────┼──────────────┼──────────────────────┤
│ drover 任务看板      │ 看设计稿，回确认或要改 │ 刚刚         │ orch-demo-board      │
│                      │ 的                     │              │                      │
└──────────────────────┴────────────────────────┴──────────────┴──────────────────────┘

完成（1）  [全部收尾（1 个）]
┌──────────────────────┬────────────────────────┬──────────────┬──────────────────────┬────────┐
│ 任务                 │ 要你做什么             │ 状态持续     │ 代号                 │ 收尾   │
├──────────────────────┼────────────────────────┼──────────────┼──────────────────────┼────────┤
│ drover 检查提速      │ 无                     │ 2 小时 10 分 │ orch-demo-perf       │ [收尾] │
│                      │                        │              │                      │        │
│                      ├────────────────────────┴──────────────┴──────────────────────┴────────┤
│                      │ 提醒：{NOTE_HEAVY}{' ' * 13} │
└──────────────────────┴───────────────────────────────────────────────────────────────────────┘

[<]  左边还有 1 列（当前状态）  [>]     触控板左右划、左右方向键，或点 [<] [>]"""
SCROLL_HINT_LEFT = "[<]  右边还有 2 列（代号、收尾）  [>]     触控板左右划、左右方向键，或点 [<] [>]"


class BoardV5Tests(BoardTestCase):
    """分组的表格：分组、一行一个任务、横线和空行、几张表列宽一致、组内排序、收尾列、各种写法。"""

    def clicked(self, slug: str, ago: float = 0) -> None:
        """看板上点过这个任务的收尾（ago 秒前）。"""
        (self.state / slug / "wrapup-clicked.json").write_text(json.dumps({"at": time.time() - ago, "text": f"收尾：{slug}"}))

    def test_design_wide_frame(self) -> None:
        """终端够宽（看板 tab 里，开着鼠标）：和 DESIGN_WIDE 逐行一样——标题行、各组之间空一行，一组一张表，任务之间有
        横线（├─┼─┤），写长了的格子折行、同一个任务的几行之间不画线，两张表共用列宽、竖线上下对齐，完成表多一列收尾，组标题后面是
        [全部收尾（1 个）]，组内状态持续长的在前；heavy 的提醒写在它下面合成的一格里，和它的任务行之间一条线、合并的
        几列在线上收成 ┴，底线合并处一条直线，这几行以外竖线都对齐；表下面不再单独列提醒；能点的区域
        正好落在 [全部收尾（1 个）] 和 [收尾]（占两行的任务的第一行）上；不再写"点看板任意位置回到主对话\""""
        self.design_tasks()
        d, lines, regions, offset = self.frame(140)
        self.assertRegex(lines[0], r"^drover 看板 · \d\d-\d\d \d\d:\d\d · 3 个任务 · drover " + VERSION + "$")
        self.assertEqual(lines[1:], ["", *DESIGN_WIDE.splitlines()])
        self.assertEqual(offset, 0)
        tables = box_tables(lines)
        self.assertEqual([bar_errors(t) for t in tables], [[], []])  # 提醒行和它上下那两条线以外，竖线都在表头那几列
        # 等你那张的竖线和完成表的前 6 根在同一列
        self.assertEqual({tuple(bar_cols(t[1])[:6]) for t in tables}, {tuple(bar_cols(tables[0][1]))})
        note = next(i for i, x in enumerate(lines) if "提醒：" in x)
        self.assertEqual((lines[note - 1][0], lines[note + 1][0]), ("│", "└"))  # 上面一条线、下面是底线
        self.assertEqual(d.text_width(lines[note]), d.text_width(lines[note + 1]))  # 和完成表一样宽
        self.assertEqual({r["action"]["send"]: cols(d, lines[r["line"]], r["start"], r["end"]) for r in regions},
                         {"全部收尾：orch-demo-perf": "[全部收尾（1 个）]", "收尾：orch-demo-perf": "[收尾]"})
        self.assertEqual([(r["action"]["slugs"], r["action"]["all"]) for r in regions],
                         [(["orch-demo-perf"], True), (["orch-demo-perf"], False)])
        _, lines, regions, _ = self.frame(140, mouse=False)  # 不在看板 tab 里：没有按钮，收尾格写"可以"
        self.assertEqual(regions, [])
        self.assertIn("完成（1）", lines)
        self.assertTrue(lines[lines.index("完成（1）") + 4].endswith("│ 可以 │"))

    def test_wrapup_cell_states(self) -> None:
        """收尾列四种写法：能收尾 [收尾]；点过、rollup 还没在点了之后重写、不到 30 分钟 → 收尾中（不能点，[全部收尾] 的个数
        不算它，K 为 0 不显示；--json 的 wrapup_ready 照样有它——主会话拿它核对看板送来的口令，wrapup_clicked 是 true）；
        点了 31 分钟还没动 → 变回 [收尾]；rollup 在点了之后重写成"收尾：先别，已经收尾过了" → 已收尾；收尾做完但留了东西
        "收尾：先别，已经收尾过了，留 <留了什么>" → 已收尾，留 <留了什么>（截到 10 个字；收尾做完但留了东西时不写"可以"——
        写"可以"看板就显示能点的 [收尾]）；别的先别 → "先别：<原因>"（原因截到 10 个字）。这几种都不能点、不在 wrapup_ready、[全部收尾]
        不算它们。Markdown 写 可以 / 收尾中 / 已收尾 / 已收尾，留 … / 先别：原因，--plain 行末写 可以收尾 / 收尾中 / 已收尾 /
        已收尾，留 … / 先别收尾：原因"""
        self.design_tasks()
        heavy = self.state / "orch-demo-perf"
        self.clicked("orch-demo-perf")
        _, lines, regions, _ = self.frame(140)
        row = lambda lines: next(x for x in lines if "orch-demo-perf" in x)  # noqa: E731  heavy 那个任务的第一行
        self.assertEqual(lines[DESIGN_WIDE.splitlines().index("完成（1）  [全部收尾（1 个）]") + 2], "完成（1）")
        self.assertTrue(row(lines).endswith("│ orch-demo-perf       │ 收尾中 │"), lines)
        self.assertEqual(regions, [])
        out = json.loads(self.board("--json"))
        self.assertEqual((out["wrapup_ready"], {t["slug"]: t["wrapup_clicked"] for t in out["tasks"]}["orch-demo-perf"]),
                         (["orch-demo-perf"], True))
        md = self.board()
        self.assertIn("\n\n**完成（1）**\n\n", md)
        self.assertIn("| orch-demo-perf | 收尾中 |", md)
        plain = self.board("--plain").splitlines()
        self.assertTrue(plain[-2].endswith("｜收尾中"), plain)
        self.assertFalse(any("全部收尾" in x for x in plain), plain)
        self.clicked("orch-demo-perf", ago=31 * MINUTE)
        self.assertTrue(row(self.frame(140)[1]).endswith("│ [收尾] │"))
        self.clicked("orch-demo-perf")
        for head, live, mdcell, plaincell in (
                ("先别，已经收尾过了", "已收尾", "已收尾", "已收尾"),
                ("先别，已经收尾过了，留 1 个 worktree", "已收尾，留 1 个 worktree", "已收尾，留 1 个 worktree",
                 "已收尾，留 1 个 worktree"),
                ("先别，已经收尾过了，留 1 个 worktree、两条分支", "已收尾，留 1 个 worktree、两...",
                 "已收尾，留 1 个 worktree、两...", "已收尾，留 1 个 worktree、两..."),
                ("先别，有没推的改动和别的原因", "先别：有没推的改动和别...", "先别：有没推的改动和别...",
                 "先别收尾：有没推的改动和别...")):
            with self.subTest(head=head):
                text = (heavy / "rollup.md").read_text()
                (heavy / "rollup.md").write_text(re.sub(r"(?m)^收尾：.*$", f"收尾：{head}", text))
                t = time.time() + 2  # 点了之后重写的
                os.utime(heavy / "rollup.md", (t, t))
                self.main_rollup("--slug", "orch-demo-perf")  # 读过：最底下不多一行"主会话在处理"
                _, lines, regions, _ = self.frame(200)  # 原因长了表也宽了：放得下，不滚
                self.assertEqual(box_cells(row(lines))[-1], live)
                self.assertEqual((regions, lines[lines.index(row(lines)) - 4]), ([], "完成（1）"))  # 没有 [收尾] / [全部收尾]
                self.assertEqual(json.loads(self.board("--json"))["wrapup_ready"], [])
                md = self.board()
                self.assertIn(f"| orch-demo-perf | {mdcell} |", md)
                self.assertNotIn("全部收尾", md)
                plain = self.board("--plain").splitlines()
                self.assertTrue(plain[-2].endswith(f"｜{plaincell}"), plain)
                self.assertFalse(any("全部收尾" in x for x in plain), plain)

    def test_markdown_plain_pending_line(self) -> None:
        """design_tasks 那三个任务的 Markdown（每组一张 GFM 表，完成组标题后面"可以收尾 1 个：回"全部收尾""，heavy 的提醒在完成表
        下面写成"提醒（drover 检查提速）：…"）、--plain（每组一个标题，每个任务两行，heavy 写了提醒加第三行"  提醒：…"，最后
        一行"回"全部收尾"…"）、--pending（"· <标题>（<代号>）：<要你做>（已等 <状态持续>）"）、--line、--json 的标题和要你做"""
        self.design_tasks()
        blocks = self.board().strip().split("\n\n")
        self.assertRegex(blocks[0], HEAD_MD + r"3 个任务 · drover " + VERSION + "$")
        self.assertEqual(blocks[1:], [
            "**等你（2）**",
            "| 任务 | 当前状态 | 要你做什么 | 状态持续 | 代号 |\n|---|---|---|---|---|\n"
            "| 订单导出改成异步处理 | 暂停，你要先弄清各功能点 | 回 A/B/C/D（建议 A） | 29 小时 | orch-demo-export-job |\n"
            "| drover 任务看板 | 设计稿出好了 | 看设计稿，回确认或要改的 | 刚刚 | orch-demo-board |",
            "**完成（1）**　可以收尾 1 个：回\"全部收尾\"",
            "| 任务 | 当前状态 | 要你做什么 | 状态持续 | 代号 | 收尾 |\n|---|---|---|---|---|---|\n"
            "| drover 检查提速 | heavy 按资源调度、自测 53 秒，已推 | 无 | 2 小时 10 分 | orch-demo-perf | 可以 |",
            f"提醒（drover 检查提速）：{NOTE_HEAVY}"])
        lines = self.board("--plain").splitlines()
        self.assertRegex(lines[0], HEAD_PLAIN + "3 个任务$")
        self.assertEqual(lines[1:], [
            "等你（2）",
            "· 订单导出改成异步处理（orch-demo-export-job）",
            "  暂停，你要先弄清各功能点｜要你：回 A/B/C/D（建议 A）｜已持续 29 小时",
            "· drover 任务看板（orch-demo-board）",
            "  设计稿出好了｜要你：看设计稿，回确认或要改的｜刚开始",
            "完成（1）",
            "· drover 检查提速（orch-demo-perf）",
            "  heavy 按资源调度、自测 53 秒，已推｜要你：无｜已持续 2 小时 10 分｜可以收尾",
            f"  提醒：{NOTE_HEAVY}",
            "回\"全部收尾\"把能收尾的 1 个都收尾"])
        self.assertEqual(self.board("--pending", "--except", "orch-demo-board").splitlines(), [
            "其他在等你的：", "· 订单导出改成异步处理（orch-demo-export-job）：回 A/B/C/D（建议 A）（已等 29 小时）"])
        self.assertEqual(self.board("--pending", "--except", "orch-demo-export-job").splitlines(), [
            "其他在等你的：", "· drover 任务看板（orch-demo-board）：看设计稿，回确认或要改的（刚开始等）"])
        self.assertEqual(self.board("--line").strip(), "drover 等你 2 · 完成 1")
        tasks = self.tasks_json()
        self.assertEqual({s: (t["title"], t["ask"]) for s, t in tasks.items()}, {
            "orch-demo-export-job": ("订单导出改成异步处理", "回 A/B/C/D（建议 A）"),
            "orch-demo-board": ("drover 任务看板", "看设计稿，回确认或要改的"), "orch-demo-perf": ("drover 检查提速", "无")})

    def test_groups_order_and_notes(self) -> None:
        """显示状态分三组、空组不画；组内按"状态持续"长的在前，不是按 rollup 的修改时间：转过答复的（rollup 3 小时前写的，
        10 分钟前转的答复）排在 1 小时前开始进行的后面；当前状态的前缀（受阻：、换编排者中：）；几个任务写了同一句提醒，各自都写
        （提醒跟着任务走，只列一次就分不清是谁的），不再读旧的"## 交付"里的"- 提醒：\""""
        self.task("t-ans", "orch-ans", "等你决定", summary="选 A 还是 B", age=3 * HOUR, read=True,
                  answered_ago=10 * MINUTE, note="同一句提醒")
        self.task("t-prog", "orch-prog", "进行中", summary="在改", age=HOUR, note="同一句提醒")
        self.task("t-block", "orch-block", "受阻", summary="卡在权限上", age=2 * HOUR, read=True)
        self.task("t-rep", "orch-rep", "需要换编排者", summary="上下文满了", age=20 * MINUTE, read=True,
                  deliver=["- 提醒：交付里的提醒不读了"])
        self.set_agents(**{f"orch-{x}": {"status": "working", "screen": NORMAL} for x in ("ans", "prog", "block", "rep")})
        blocks = self.board().strip().split("\n\n")
        self.assertEqual([b for b in blocks[1:] if b.startswith("**")], ["**等你（1）**", "**进行中（3）**"])
        rows = [line.split(" | ")[0][2:] for line in blocks[4].splitlines()[2:]]
        self.assertEqual(rows, ["t-prog", "t-rep", "t-ans"])
        self.assertIn("| t-block | 受阻：卡在权限上 | 看转达，定下一步 | 2 小时 | t-block |", blocks[2])
        self.assertIn("| t-rep | 换编排者中：上下文满了 | 无 | 20 分钟 | t-rep |", blocks[4])
        self.assertIn("| t-ans | 已转答复，等它接着做 | 无 | 10 分钟 | t-ans |", blocks[4])
        self.assertEqual([b for b in blocks if b.startswith("提醒")], ["提醒（t-prog）：同一句提醒", "提醒（t-ans）：同一句提醒"])

    def test_duration_format(self) -> None:
        """状态持续：不到 1 分钟"刚刚"，不到 1 小时"N 分钟"，不到 10 小时"1 小时 50 分"（整点不写
        "0 分"），不到 48 小时"29 小时"，再长"2 天 3 小时"（整天不写"0 小时"）；None 是空串"""
        d = self.load_drover()
        for seconds, want in ((0, "刚刚"), (59, "刚刚"), (60, "1 分钟"), (59 * MINUTE + 59, "59 分钟"), (HOUR, "1 小时"),
                              (HOUR + 50 * MINUTE, "1 小时 50 分"), (9 * HOUR + 59 * MINUTE, "9 小时 59 分"),
                              (10 * HOUR + 30 * MINUTE, "10 小时"), (47 * HOUR, "47 小时"), (48 * HOUR, "2 天"),
                              (51 * HOUR + 10 * MINUTE, "2 天 3 小时"), (-5, "刚刚"), (None, "")):
            with self.subTest(seconds=seconds):
                self.assertEqual(d.board_duration(seconds), want)


# v6_tasks 那三个任务在终端够宽时该画成的样子（当前状态 28 格、要你做什么 22 格一行）：完成表那一行的当前状态、要你做什么
# 写长了，各折成两行，不截断。逐行比对（标题行的时间、版本除外）。
WRAPPED_WIDE = """\
等你（1）
┌──────────────────────┬──────────────────────────────┬────────────────────────┬──────────┬──────────────────────┐
│ 任务                 │ 当前状态                     │ 要你做什么             │ 状态持续 │ 代号                 │
├──────────────────────┼──────────────────────────────┼────────────────────────┼──────────┼──────────────────────┤
│ 订单导出改成异步处理 │ 暂停中，待选 MQ 队列方案     │ 回 A/B/C/D（建议 A）   │ 44 分钟  │ orch-demo-export-job │
└──────────────────────┴──────────────────────────────┴────────────────────────┴──────────┴──────────────────────┘

进行中（1）
┌──────────────────────┬──────────────────────────────┬────────────────────────┬──────────┬──────────────────────┐
│ 任务                 │ 当前状态                     │ 要你做什么             │ 状态持续 │ 代号                 │
├──────────────────────┼──────────────────────────────┼────────────────────────┼──────────┼──────────────────────┤
│ drover 检查提速      │ 5 个后续修复在查根因（摸底） │ 无                     │ 18 分钟  │ orch-demo-perf       │
└──────────────────────┴──────────────────────────────┴────────────────────────┴──────────┴──────────────────────┘

完成（1）  [全部收尾（1 个）]
┌──────────────────────┬──────────────────────────────┬────────────────────────┬──────────┬──────────────────────┬────────┐
│ 任务                 │ 当前状态                     │ 要你做什么             │ 状态持续 │ 代号                 │ 收尾   │
├──────────────────────┼──────────────────────────────┼────────────────────────┼──────────┼──────────────────────┼────────┤
│ drover 任务看板      │ 看板换新代码的修复已推       │ 看一眼看板 tab 是新版  │ 6 分钟   │ orch-demo-board      │ [收尾] │
│                      │ drover main d84a80c          │ 表格（可选）           │          │                      │        │
└──────────────────────┴──────────────────────────────┴────────────────────────┴──────────┴──────────────────────┴────────┘"""
V6_STATE = "看板换新代码的修复已推 drover main d84a80c"
WRAP_SEMICOLON = "drover 三件修好、集成检查过；等 web-app 那件"  # 不守禁则时按 28 格折，第二行会以"；"开头
V6_ASK = "看一眼看板 tab 是新版表格（可选）"
V6_AGENTS = {"orch-demo": {"status": "done", "screen": NORMAL}, "orch-heavy": {"status": "working", "screen": NORMAL},
             "orch-board": {"status": "idle", "screen": NORMAL}}
AMBIGUOUS = ("…", "◀", "▶")  # 宽度不定、看板里不该出现的字


class BoardV6Tests(BoardTestCase):
    """折行：--live 里当前状态、要你做什么在格子里折行（wrap_width），一个任务占几行时只在第一行写
    任务名、代号和 [收尾]；截断记号"..."、滚动按钮 [<] [>]；--plain 不截当前状态、要你做什么。"""

    def v6_tasks(self, **agents: dict) -> None:
        """三个任务（WRAPPED_WIDE 画的就是它们）：orch-demo-export-job 等你决定 44 分钟、orch-demo-perf 进行中
        18 分钟、orch-demo-board 完成 6 分钟（能收尾；当前状态、要你做什么写长了，要折行的那一行），都读过；agents 是
        另加的 agent。"""
        self.task("orch-demo-export-job", "orch-demo", "等你决定", title="订单导出改成异步处理",
                  summary="暂停中，待选 MQ 队列方案", ask="回 A/B/C/D（建议 A）", age=44 * MINUTE, read=True)
        self.task("orch-demo-perf", "orch-heavy", "进行中", title="drover 检查提速", summary="5 个后续修复在查根因（摸底）",
                  ask="无", age=18 * MINUTE, read=True)
        self.task("orch-demo-board", "orch-board", "完成", title="drover 任务看板", summary=V6_STATE, ask=V6_ASK,
                  wrapup="可以", age=6 * MINUTE, read=True)
        self.set_agents(**V6_AGENTS, **agents)

    def test_wrap_rules(self) -> None:
        """wrap_width：按显示宽度折（中文两格），短的照旧一行；英文单词、数字、路径、sha（字母数字和 ._/:-，~/ 开头的路径带上 ~）
        不从中间断，放不下整个挪到下一行；一个词比一行还长才硬断，剩下的接着排；中文哪里都能断；行首不留空格（放不下的空格就是
        折行处）、行尾的去掉；超过 3 行的，从第 3 行开头起的原文截成一行、末尾"..."（算 3 格）；空串是一行空的"""
        d = self.load_drover()
        han = "一二三四五六七八九十"
        for text, width, want in (
                (V6_STATE, 28, ["看板换新代码的修复已推", "drover main d84a80c"]),
                (V6_ASK, 22, ["看一眼看板 tab 是新版", "表格（可选）"]),
                ("暂停中，待选 MQ 队列方案", 28, ["暂停中，待选 MQ 队列方案"]),
                ("无", 22, ["无"]),
                ("已推 main d84a80c0fbf2 到远端", 16, ["已推 main", "d84a80c0fbf2 到", "远端"]),
                ("全文见 ~/.drover/a/b.md", 16, ["全文见", "~/.drover/a/b.md"]),
                ("看 v1.2.3-rc:x/y_z", 16, ["看", "v1.2.3-rc:x/y_z"]),
                ("看 " + "a" * 30, 22, ["看", "a" * 22, "a" * 8]),
                ("a" * 30 + " 完成", 22, ["a" * 22, "a" * 8 + " 完成"]),
                (han * 2, 22, [han + "一", han[1:]]),
                ("看板 drover", 6, ["看板", "drover"]),
                ("  前面有空格 drover  ", 22, ["前面有空格 drover"]),
                (han * 5, 22, [han + "一", han[1:] + "一二", "三四五六七八九十一..."]),
                ("alpha beta gamma delta epsilon zeta eta theta", 12, ["alpha beta", "gamma delta", "epsilon z..."]),
                ("", 22, [""])):
            with self.subTest(text=text, width=width):
                lines = d.wrap_width(text, width, 3)
                self.assertEqual(lines, want)
                self.assertTrue(all(d.text_width(x) <= width for x in lines), lines)

    def test_wrap_no_punct_at_line_start(self) -> None:
        """中文禁则（WRAP_SEMICOLON 不守禁则时按 28 格会折成"drover 三件修好、集成检查过" / "；等 web-app 那件"）：折出来的
        一行不以句末标点开头（全角，。；：！？、）」』】》〉，英文 , . ; : ! ? ) ] }，board_ascii 把"…"换成的"..."也算），遇到就把
        上一行最后一个字 / 词一起带下来，连着两个标点的再带一个；一行不以开括号、开引号结尾（（「『【《〈 ( [ {），带到下一行；
        带下来上一行就空了的照原来断；".venv"这样以点开头的路径不算标点。--live 的框线表里那一句的第二行不以"；"开头"""
        d = self.load_drover()
        han = "一二三四五六七八九十"
        for text, width, want in (
                (WRAP_SEMICOLON, 28, ["drover 三件修好、集成检查", "过；等 web-app 那件"]),
                (han + "。", 20, [han[:-1], "十。"]),
                (han + "，好", 20, [han[:-1], "十，好"]),
                (han + "）", 20, [han[:-1], "十）"]),
                (han[:-1] + "。」", 20, [han[:-2], "九。」"]),
                (d.board_ascii(han + "…"), 20, [han[:-1], "十..."]),
                ("abc defg, hi", 8, ["abc", "defg, hi"]),
                ("看 done. 好", 8, ["看 done.", "好"]),
                ("abcdefghij)", 10, ["abcdefghij", ")"]),
                ("一，", 2, ["一", "，"]),
                (han[:-2] + "（九十）", 18, [han[:-2], "（九十）"]),
                ("abcd (efgh)", 6, ["abcd", "(efgh)"]),
                ("一二三「四五」", 8, ["一二三", "「四五」"]),
                (han + " .venv/bin", 20, [han, ".venv/bin"])):
            with self.subTest(text=text, width=width):
                lines = d.wrap_width(text, width, 3)
                self.assertEqual(lines, want)
                self.assertTrue(all(d.text_width(x) <= width for x in lines), lines)
        self.v6_tasks()
        self.task("orch-demo-perf", "orch-heavy", "进行中", title="drover 检查提速", summary=WRAP_SEMICOLON, ask="无",
                  age=18 * MINUTE, read=True)
        _, lines, _, _ = self.frame(140)
        rows = [row for table in box_tables(lines) for row in box_rows(table) if row[0] == ["drover 检查提速"]]
        self.assertEqual([row[1] for row in rows], [["drover 三件修好、集成检查", "过；等 web-app 那件"]])

    def test_design_v6_frame(self) -> None:
        """看板 tab 里（开着鼠标）终端够宽：和 WRAPPED_WIDE 逐行一样——完成表那一行的当前状态、要你做什么各折成两行（列宽
        28 / 22：折了行的格子按满一行算宽），任务名、状态持续、代号、[收尾] 只在第一行，同一个任务的两行之间不画线；短的照旧一行；
        几张表竖线上下对齐；[收尾] 点第一行点得中、第二行点不中。不在看板 tab 里（--live 子进程，没开鼠标）一样折行，收尾格写"可以\""""
        self.v6_tasks()
        d, lines, regions, offset = self.frame(140)
        self.assertRegex(lines[0], r"^drover 看板 · \d\d-\d\d \d\d:\d\d · 3 个任务 · drover " + VERSION + "$")
        self.assertEqual((offset, lines[1:]), (0, ["", *WRAPPED_WIDE.splitlines()]))
        bars = [[d.text_width(x[:i]) for i, ch in enumerate(x) if ch in "│┌┬┐├┼┤└┴┘"] for x in lines if x[:1] in tuple("│┌├└")]
        self.assertEqual({tuple(b[:6]) for b in bars}, {tuple(bars[0])})
        wrapup = next(r for r in regions if not r["action"]["all"])
        self.assertEqual((cols(d, lines[wrapup["line"]], wrapup["start"], wrapup["end"]), wrapup["action"]["send"]),
                         ("[收尾]", "收尾：orch-demo-board"))
        self.assertIn("drover 任务看板", lines[wrapup["line"]])
        self.assertIn("drover main d84a80c", lines[wrapup["line"] + 1])
        x = wrapup["start"] + 1
        self.assertEqual(d.board_hit(regions, 0, (x, wrapup["line"] + 1)), wrapup["action"])
        self.assertIsNone(d.board_hit(regions, 0, (x, wrapup["line"] + 2)))  # 同一个任务的第二行
        tables = [box_rows(t) for t in box_tables(self.live_lines(140))]
        self.assertEqual(tables, [
            [[["订单导出改成异步处理"], ["暂停中，待选 MQ 队列方案"], ["回 A/B/C/D（建议 A）"], ["44 分钟"], ["orch-demo-export-job"]]],
            [[["drover 检查提速"], ["5 个后续修复在查根因（摸底）"], ["无"], ["18 分钟"], ["orch-demo-perf"]]],
            [[["drover 任务看板"], ["看板换新代码的修复已推", "drover main d84a80c"], ["看一眼看板 tab 是新版", "表格（可选）"],
              ["6 分钟"], ["orch-demo-board"], ["可以"]]]])

    def test_wrapped_rows_and_clicks(self) -> None:
        """一张表里几个任务各占几行：t-long 当前状态超过 3 行（第 3 行末尾截成"..."）、要你做什么两行，orch-demo-board 两行；
        任务之间有横线，同一个任务的几行之间没有；任务名、代号、状态持续、[收尾] 只在每个任务的第一行；两个 [收尾] 的点击区域
        各在自己任务的第一行（行号按画出来的算，前一个任务占 3 行），下面那几行点不中。往右滚一列（100 格）以后照样：[收尾] 还在
        各自的第一行、[<] [>] 在提示行上"""
        self.v6_tasks(**{"orch-long": {"status": "idle", "screen": NORMAL}})
        state, ask = "甲乙丙丁戊己庚辛壬癸" * 5, "部署测试环境后在页面上点一遍导出"
        self.task("t-long", "orch-long", "完成", title="第二个长任务", summary=state, ask=ask, wrapup="可以", age=2 * HOUR,
                  read=True)
        for width, offset in ((140, 0), (100, 1)):
            with self.subTest(width=width):
                d, lines, regions, got = self.frame(width, offset=offset)
                self.assertEqual(got, offset)
                table = box_tables(lines)[-1]
                rows = box_rows(table)
                want_long = [["第二个长任务"], [state[:14], state[14:28], "壬癸甲乙丙丁戊己庚辛壬癸..."],
                             ["部署测试环境后在页面上", "点一遍导出"], ["2 小时"], ["t-long"], ["[收尾]"]]
                want_board = [["drover 任务看板"], ["看板换新代码的修复已推", "drover main d84a80c"],
                              ["看一眼看板 tab 是新版", "表格（可选）"], ["6 分钟"], ["orch-demo-board"], ["[收尾]"]]
                if offset:  # 当前状态那一列滚出去了
                    want_long, want_board = [x[:1] + x[2:] for x in (want_long, want_board)]
                self.assertEqual(rows, [want_long, want_board])
                top = lines.index(table[0])
                self.assertEqual([x[0] for x in table[3:]], ["│", "│", "│", "├", "│", "│", "└"])
                wrapups = [r for r in regions if r["action"].get("all") is False]
                self.assertEqual([(r["line"] - top, r["action"]["send"]) for r in wrapups],
                                 [(3, "收尾：t-long"), (7, "收尾：orch-demo-board")])
                for r in wrapups:
                    self.assertEqual(cols(d, lines[r["line"]], r["start"], r["end"]), "[收尾]")
                    for below in (1, 2):
                        self.assertIsNone(d.board_hit(regions, 0, (r["start"] + 1, r["line"] + 1 + below)))
                scrolls = {cols(d, lines[r["line"]], r["start"], r["end"]): r["action"] for r in regions
                           if "scroll" in r["action"]}
                self.assertEqual(scrolls, {"[<]": {"scroll": -1}, "[>]": {"scroll": 1}} if offset else {})

    def test_plain_markdown_pending_and_no_ambiguous_marks(self) -> None:
        """--plain 的当前状态、要你做什么整句写（任务名照旧截）；Markdown 照旧按 40 / 30 格截、--pending 按 30 格截，记号都是
        "..."，--pending 多出来的写"...还有 N 个"。哪种写法、哪种宽度都不出现宽度不定的"…""◀""▶"：Markdown、--plain、--pending、
        --json、--live 子进程 200 / 80 / 40 格（没开鼠标；40 格退回列表，长的行由 board_draw 按终端宽度截）、看板 tab 里 140 / 100 / 50 格
        （开着鼠标；100 格有 [<] [>]，50 格退回列表）"""
        self.v6_tasks(**{"orch-long": {"status": "idle", "screen": NORMAL},
                         **{f"orch-u{i}": {"status": "done", "screen": NORMAL} for i in range(1, 4)}})
        self.task(LONG_SLUG, "orch-long", "完成", title=LONG_TITLE, summary=LONG_STATE * 2, ask=LONG_ASK,
                  wrapup="先别，等你把测试环境部署完、验证好再收", age=HOUR, read=True)
        long_ask = "回 A 或 B，" + "再写长一点" * 6
        for i in range(1, 4):
            self.task(f"u{i}", f"orch-u{i}", "等你决定", title=f"任务 {i}", summary="等你", ask=long_ask, age=i * HOUR,
                      read=True)
        d = self.load_drover()
        plain = self.board("--plain")
        self.assertIn(f"· drover 任务看板（orch-demo-board）\n  {V6_STATE}｜要你：{V6_ASK}｜已持续 6 分钟｜可以收尾", plain)
        self.assertIn(f"· {d.fit_width(LONG_TITLE, 24)}（{LONG_SLUG}）\n  {LONG_STATE * 2}｜要你：{LONG_ASK}｜", plain)
        md = self.board()
        md_state, md_ask = "看板换新代码的修复已推 drover main d8...", "看一眼看板 tab 是新版表格（..."
        self.assertIn(f"| drover 任务看板 | {md_state} | {md_ask} | 6 分钟 | orch-demo-board | 可以 |", md)
        self.assertEqual((d.text_width(md_state), d.text_width(md_ask)), (40, 30))
        pending = self.board("--pending")
        self.assertEqual(pending.splitlines(), [
            "其他在等你的：", f"· 任务 3（u3）：{d.fit_width(long_ask, 30)}（已等 3 小时）",
            f"· 任务 2（u2）：{d.fit_width(long_ask, 30)}（已等 2 小时）", f"· 任务 1（u1）：{d.fit_width(long_ask, 30)}（已等 1 小时）",
            '...还有 1 个，回"看板"看全部'])
        self.assertTrue(d.fit_width(long_ask, 30).endswith("..."))
        outputs = {"markdown": md, "plain": plain, "pending": pending, "json": self.board("--json")}
        for width in (200, 80, 40):
            outputs[f"live {width}"] = "\n".join(self.live_lines(width))
        for width in (140, 100, 50):
            outputs[f"tab {width}"] = "\n".join(self.frame(width)[1])
        self.assertIn("[<]  右边还有", outputs["tab 100"])
        self.assertIn("右边还有", outputs["live 80"])
        cut = [x for x in outputs["live 40"].split("\n") if x.endswith("...")]  # 40 格退回列表，长的行按终端宽度截
        self.assertTrue(cut and all(d.text_width(x) <= 40 for x in cut), outputs["live 40"])
        for name, text in outputs.items():
            with self.subTest(output=name):
                self.assertEqual([ch for ch in AMBIGUOUS if ch in text], [])

    def test_ascii_rules(self) -> None:
        """board_ascii：BOARD_ASCII 的键都是宽度不定（East Asian Width 为 A）的字、换成的都没有宽度不定的字；… → ...，— – → -
        （"——" → "--"），“ ” → "，‘ ’ → '，· → -，→ ← ↑ ↓ → -> <- ^ v，× → x，① 到 ⑳ → (1) 到 (20)；表里没有的取 NFKC（Ⅱ → II、
        № → No、½ → 1⁄2），再取 NFKD 去掉附加符号（é → e，变体选择符 U+FE0F、单独的组合符号去掉）；都不行写 "?"（α、°）；宽度确定
        的字（中文、全角标点、ASCII）原样"""
        d = self.load_drover()
        self.assertEqual([k for k in d.BOARD_ASCII if unicodedata.east_asian_width(k) != "A"], [])
        self.assertEqual([v for v in d.BOARD_ASCII.values() if any(unicodedata.east_asian_width(c) == "A" for c in v)], [])
        for text, want in (("等…——“好”·→①⑳×", '等...--"好"-->(1)(20)x'), ("‘a’–b←↑↓", "'a'-b<-^v"),
                           ("Ⅱ№½", "IINo1⁄2"), ("café", "cafe"), ("⚠️注意", "⚠注意"), ("é", "e"), ("α 30°", "? 30?"),
                           ("中文，（）abc｜【】", "中文，（）abc｜【】"), ("", "")):
            with self.subTest(text=text):
                self.assertEqual(d.board_ascii(text), want)

    def test_ambiguous_cells(self) -> None:
        """rollup 的标题、摘要、要你做、"收尾：先别"的原因里带 … — “” · → ① α °：--live（子进程和看板 tab 里）、Markdown 的表格里
        这些字都换掉了（没映射到的 α ° 写 "?"），表格的每一行除了框线没有宽度不定的字，竖线位置每一行一致，换长了的格子照样按
        换完的宽度截（收尾原因）；[收尾] 的点击区域照样落在 [收尾] 上；--plain、--pending、--json 是原文"""
        title, state, ask, hold = "看板…改版", "等…“确认”—下一步→①", "回 A·B 或 α（30°）", "等…验完 α"
        self.task("t-amb", "orch-amb", "完成", title=title, summary=state, ask=ask, wrapup="可以", age=2 * HOUR, read=True)
        self.task("t-hold", "orch-hold", "完成", summary="做完了", wrapup=f"先别，{hold}", age=HOUR, read=True)
        self.task("t-wait", "orch-wait", "等你决定", summary="选 A→B", ask="回 ①…或 ②", age=HOUR, read=True)
        self.set_agents(**{f"orch-{x}": {"status": "idle", "screen": NORMAL} for x in ("amb", "hold", "wait")})
        d = self.load_drover()
        title2, state2, ask2 = "看板...改版", '等..."确认"-下一步->(1)', "回 A-B 或 ?（30?）"
        want_rows = [[[title2], [state2], [ask2], ["2 小时"], ["t-amb"]], [["t-hold"], ["做完了"], ["无"], ["1 小时"], ["t-hold"]]]

        def table_lines(lines: list) -> list:
            return [x for x in lines if x[:1] in tuple("┌│├└")]

        def ambiguous(lines: list) -> list:  # 框线（U+2500 到 U+257F）以外的宽度不定的字
            return [ch for x in lines for ch in x if unicodedata.east_asian_width(ch) == "A" and not "─" <= ch <= "╿"]

        def bars(lines: list) -> set:
            return {tuple(d.text_width(x[:i]) for i, ch in enumerate(x) if ch in "│┌┬┐├┼┤└┴┘")[:6] for x in lines}

        for name, lines, wrapup in (("live", self.live_lines(200), "可以"), ("tab", self.frame(140)[1], "[收尾]")):
            with self.subTest(output=name):
                self.assertEqual(ambiguous(table_lines(lines)), [])
                self.assertEqual(len(bars(table_lines(lines))), 1, table_lines(lines))
                done = box_rows(box_tables(lines)[-1])
                self.assertEqual([row[:5] for row in done], want_rows)
                self.assertEqual([row[5] for row in done], [[wrapup], ["先别：等...验完 ?"]])
                self.assertEqual(box_rows(box_tables(lines)[0])[0][1:3], [["选 A->B"], ["回 (1)...或 (2)"]])
        _, lines, regions, _ = self.frame(140)
        wrapup = next(r for r in regions if r["action"].get("all") is False)
        self.assertEqual((cols(d, lines[wrapup["line"]], wrapup["start"], wrapup["end"]), wrapup["action"]["send"]),
                         ("[收尾]", "收尾：t-amb"))
        self.assertIn(title2, lines[wrapup["line"]])
        md = self.board().splitlines()
        self.assertEqual(ambiguous([x for x in md if x.startswith("|")]), [])
        self.assertIn(f"| {title2} | {state2} | {ask2} | 2 小时 | t-amb | 可以 |", md)
        self.assertIn("| t-hold | 做完了 | 无 | 1 小时 | t-hold | 先别：等...验完 ? |", md)
        plain = self.board("--plain")
        self.assertIn(f"· {title}（t-amb）\n  {state}｜要你：{ask}｜已持续 2 小时｜可以收尾", plain)
        self.assertIn(f"｜先别收尾：{hold}", plain)
        self.assertEqual(self.board("--pending").splitlines(), ["其他在等你的：", "· t-wait：回 ①…或 ②（已等 1 小时）"])
        self.assertEqual({k: (v["title"], v["ask"]) for k, v in self.tasks_json().items() if k == "t-amb"},
                         {"t-amb": (title, ask)})


# v8_tasks 那三个任务在终端够宽时该画成的样子：提醒写在那个任务最后一行下面，任务列空着、后面画出来的几列合成一格（宽屏这一格
# 89 格）。提醒和上面的任务行之间一条线（从任务列右边那条竖线起，看得出是合并的单元格），合并的几列在线上收成 ┴；提醒下面那条线
# 合并处没有交叉——后面还有任务时下一个任务的列从这里分开（┬），底线一条直线。逐行比对（标题行的时间、版本除外）。NOTED_100 是
# 100 格时的那两张：偏移 0（右边还藏着代号一列），合成的那一格 66 格，orch-demo-export-job 的提醒折成两行；最后的提示行只比
# 开头（"触控板…"那半句不在这里）。
NOTE_EXPORT = "已定导出改走统一 MQ 队列，旧的同步导出入口会从 10 个减到 9 个"  # 前面加上"提醒："67 格
NOTE_PERF = "这轮的改动都还没提交，做完一起问你推不推"  # 46 格
NOTED_WIDE = """\
等你（1）
┌──────────────────────┬──────────────────────────────┬──────────────────────┬──────────────┬──────────────────────┐
│ 任务                 │ 当前状态                     │ 要你做什么           │ 状态持续     │ 代号                 │
├──────────────────────┼──────────────────────────────┼──────────────────────┼──────────────┼──────────────────────┤
│ 订单导出改成异步处理 │ 暂停中，待选 MQ 队列方案     │ 回 A/B/C/D（建议 A） │ 4 小时 14 分 │ orch-demo-export-job │
│                      ├──────────────────────────────┴──────────────────────┴──────────────┴──────────────────────┤
│                      │ 提醒：已定导出改走统一 MQ 队列，旧的同步导出入口会从 10 个减到 9 个                       │
└──────────────────────┴───────────────────────────────────────────────────────────────────────────────────────────┘

进行中（2）
┌──────────────────────┬──────────────────────────────┬──────────────────────┬──────────────┬──────────────────────┐
│ 任务                 │ 当前状态                     │ 要你做什么           │ 状态持续     │ 代号                 │
├──────────────────────┼──────────────────────────────┼──────────────────────┼──────────────┼──────────────────────┤
│ drover 检查提速      │ 新编排者接手，等放行规则改完 │ 无                   │ 34 分钟      │ orch-demo-perf       │
│                      │ 再集成                       │                      │              │                      │
│                      ├──────────────────────────────┴──────────────────────┴──────────────┴──────────────────────┤
│                      │ 提醒：这轮的改动都还没提交，做完一起问你推不推                                            │
├──────────────────────┼──────────────────────────────┬──────────────────────┬──────────────┬──────────────────────┤
│ worktree 及时清理    │ 摸底完成，worker 在改 drover │ 无                   │ 20 分钟      │ orch-demo-cleanup    │
└──────────────────────┴──────────────────────────────┴──────────────────────┴──────────────┴──────────────────────┘"""
NOTED_100 = """\
等你（1）
┌──────────────────────┬──────────────────────────────┬──────────────────────┬──────────────┐
│ 任务                 │ 当前状态                     │ 要你做什么           │ 状态持续     │
├──────────────────────┼──────────────────────────────┼──────────────────────┼──────────────┤
│ 订单导出改成异步处理 │ 暂停中，待选 MQ 队列方案     │ 回 A/B/C/D（建议 A） │ 4 小时 14 分 │
│                      ├──────────────────────────────┴──────────────────────┴──────────────┤
│                      │ 提醒：已定导出改走统一 MQ 队列，旧的同步导出入口会从 10 个减到 9   │
│                      │ 个                                                                 │
└──────────────────────┴────────────────────────────────────────────────────────────────────┘

进行中（2）
┌──────────────────────┬──────────────────────────────┬──────────────────────┬──────────────┐
│ 任务                 │ 当前状态                     │ 要你做什么           │ 状态持续     │
├──────────────────────┼──────────────────────────────┼──────────────────────┼──────────────┤
│ drover 检查提速      │ 新编排者接手，等放行规则改完 │ 无                   │ 34 分钟      │
│                      │ 再集成                       │                      │              │
│                      ├──────────────────────────────┴──────────────────────┴──────────────┤
│                      │ 提醒：这轮的改动都还没提交，做完一起问你推不推                     │
├──────────────────────┼──────────────────────────────┬──────────────────────┬──────────────┤
│ worktree 及时清理    │ 摸底完成，worker 在改 drover │ 无                   │ 20 分钟      │
└──────────────────────┴──────────────────────────────┴──────────────────────┴──────────────┘

[<]  右边还有 1 列（代号）  [>]"""
V8_AGENTS = {"orch-demo": {"status": "done", "screen": NORMAL}, "orch-heavy": {"status": "working", "screen": NORMAL},
             "orch-cleanup": {"status": "working", "screen": NORMAL}}


def span_line(text: str, task: int, span: int) -> str:
    """跨列的一行（测试自己拼，不用 drover 的 box_row）：任务列 task 格空着，后面合成的一格 span 格宽。"""
    return f"│ {' ' * task} │ {text}{' ' * (span - text_cols(text))} │"


def note_rule(task: int, widths: list) -> str:
    """提醒上面那条线（测试自己拼，不用 drover 的 box_rule）：任务列 task 格空着照旧往下接，后面画出来的几列（widths）各一段
    横线、在列之间收成 ┴。"""
    return f"│ {' ' * task} ├" + "┴".join("─" * (w + 2) for w in widths) + "┤"


def task_blocks(table: list) -> list:
    """一张框线表（box_tables 的一项）里每个任务的几行（表头不算；横线之间的那几行"│ … │"，提醒上面那条线以"│"开头，也算在
    这个任务里）。"""
    blocks, current = [], []
    for line in table[3:]:
        if line.startswith("│"):
            current.append(line)
        else:
            blocks.append(current)
            current = []
    return blocks


class BoardV8Tests(BoardTestCase):
    """跨列的提醒行：提醒跟着它的任务走——--live 写在那个任务最后一行下面、后面几列合成的一格里（按这一格
    的宽度折行，滚动以后跟着画出来的列重新折；和任务行之间一条线，合并的几列在线上收成 ┴，下面那条线合并处没有交叉），列表写法
    （--plain、窄屏的 --live）是这个任务的第三行，Markdown 每张表下面"提醒（<标题>）：…"；表下面不再单独列提醒，几个任务写了
    同一句各自都写。"""

    def v8_tasks(self) -> None:
        """三个任务（NOTED_WIDE 画的就是它们）：orch-demo-export-job 等你决定 4 小时 14 分（带提醒）、orch-demo-perf
        进行中 34 分钟（带提醒，当前状态折两行）、orch-demo-cleanup 进行中 20 分钟（不带），都读过。"""
        self.task("orch-demo-export-job", "orch-demo", "等你决定", title="订单导出改成异步处理",
                  summary="暂停中，待选 MQ 队列方案", ask="回 A/B/C/D（建议 A）", note=NOTE_EXPORT, age=4 * HOUR + 14 * MINUTE,
                  read=True)
        self.task("orch-demo-perf", "orch-heavy", "进行中", title="drover 检查提速", summary="新编排者接手，等放行规则改完再集成",
                  ask="无", note=NOTE_PERF, age=34 * MINUTE, read=True)
        self.task("orch-demo-cleanup", "orch-cleanup", "进行中", title="worktree 及时清理",
                  summary="摸底完成，worker 在改 drover", ask="无", age=20 * MINUTE, read=True)
        self.set_agents(**V8_AGENTS)

    def test_design_v8_frame(self) -> None:
        """看板 tab 里（开着鼠标）终端够宽：和 NOTED_WIDE 逐行一样——写了提醒的任务最后一行下面先一条线
        （任务列照旧往下接，合并的几列在线上收成 ┴），再一行"提醒：…"，任务列空着、后面几列合成一格（中间不画竖线）；提醒下面
        是底线（等你那张）时合并处一条直线，后面还有任务（进行中那张）时下一个任务的列从这里分开（┬）；同一个任务的几行之间不画
        横线；这几行以外竖线都在表头那几列，两条线上的 ┴ / ┬ 正好在上面 / 下面那几列竖线的位置；表宽、列宽和不写提醒时一样
        （116 格，列宽只按表头和五格算）；表下面没有单独的"提醒："段。不在看板 tab 里（--live 子进程，没开鼠标）画法一样"""
        self.v8_tasks()
        d, lines, regions, offset = self.frame(160)
        self.assertRegex(lines[0], r"^drover 看板 · \d\d-\d\d \d\d:\d\d · 3 个任务 · drover " + VERSION + "$")
        self.assertEqual((offset, regions, lines[1:]), (0, [], ["", *NOTED_WIDE.splitlines()]))
        tables = box_tables(lines)
        self.assertEqual([{d.text_width(x) for x in t} for t in tables], [{116}, {116}])
        self.assertEqual([bar_errors(t) for t in tables], [[], []])
        self.assertEqual([[x[0] for x in t if "─" in x] for t in tables],
                         [["┌", "├", "│", "└"], ["┌", "├", "│", "├", "└"]])  # 横线：提醒上面那条以"│"起头
        self.assertFalse([x for x in lines if x.startswith("提醒")], lines)
        self.assertEqual(self.live_lines(160)[1:], ["", *NOTED_WIDE.splitlines()])

    def test_design_v8_scrolled(self) -> None:
        """100 格（要左右滚）：合成的那一格跟着画出来的列变宽变窄、重新折行，不会被滚出去，提醒上面那条线、下面那条线也跟着
        画出来的列画。偏移 0（右边藏着代号）就是 NOTED_100：这一格 66 格，orch-demo-export-job 的提醒折成两行
        （"…减到 9" / "个"）、heavy 的一行；往右滚过一列（当前状态藏到左边）这一格 58 格，orch-demo-export-job 的提醒照样两行、断在
        别处（滚动前后行数可以不同），线上的 ┴ 只有三列之间的两个；每行都不比表宽，竖线照样对齐，表下面没有单独的提醒"""
        self.v8_tasks()
        d, lines, regions, offset = self.frame(100, offset=0)
        want = NOTED_100.splitlines()
        self.assertEqual((offset, lines[1:-1]), (0, ["", *want[:-1]]))
        self.assertTrue(lines[-1].startswith(want[-1] + "     触控板左右划"), lines[-1])
        _, lines, _, offset = self.frame(100, offset=1)
        tables = box_tables(lines)
        self.assertEqual(offset, 1)
        self.assertEqual(task_blocks(tables[0])[0][1:], [
            note_rule(20, [20, 12, 20]),
            span_line("提醒：已定导出改走统一 MQ 队列，旧的同步导出入口会从 10 个", 20, 58), span_line("减到 9 个", 20, 58)])
        self.assertEqual(task_blocks(tables[1])[0][2:], [note_rule(20, [20, 12, 20]), span_line(f"提醒：{NOTE_PERF}", 20, 58)])
        self.assertTrue(lines[-1].startswith("[<]  左边还有 1 列（当前状态）  [>]"), lines[-1])
        self.assertEqual([{d.text_width(x) for x in t} for t in tables], [{85}, {85}])
        self.assertEqual([bar_errors(t) for t in tables], [[], []])
        self.assertFalse([x for x in lines if x.startswith("提醒")], lines)

    def test_same_note_each_and_long_note(self) -> None:
        """两个任务写了同一句提醒：各自那一行下面都写（以前只列一次，分不清是谁的），各有一条线和任务行隔开；任务甲的提醒下面
        还有任务乙，那条线是"├ ┼ ┬…┤"，任务乙的提醒下面是底线"└ ┴ ──┘"；提醒很长：在合成的那一格（41 格）里折 3 行，第 3 行
        末尾截成"..."，表宽不因它变（两张表一样宽，都是只按表头和五格算的 54 格）；竖线照样对齐"""
        long_note = "甲乙丙丁戊己庚辛壬癸" * 12
        self.task("t-a", "orch-a", "进行中", title="任务甲", summary="在改", ask="无", age=HOUR, read=True, note="同一句提醒")
        self.task("t-b", "orch-b", "进行中", title="任务乙", summary="在测", ask="无", age=30 * MINUTE, read=True,
                  note="同一句提醒")
        self.task("t-long", "orch-long", "等你决定", title="长提醒", summary="等你定", ask="回 A 或 B", age=2 * HOUR, read=True,
                  note=long_note)
        self.set_agents(**{"orch-a": {"status": "working", "screen": NORMAL}, "orch-b": {"status": "working", "screen": NORMAL},
                           "orch-long": {"status": "done", "screen": NORMAL}})
        d, lines, _, _ = self.frame(160)
        waiting, doing = box_tables(lines)
        rule = note_rule(6, [8, 10, 8, 6])
        self.assertEqual(task_blocks(doing), [
            ["│ 任务甲 │ 在改     │ 无         │ 1 小时   │ t-a    │", rule, span_line("提醒：同一句提醒", 6, 41)],
            ["│ 任务乙 │ 在测     │ 无         │ 30 分钟  │ t-b    │", rule, span_line("提醒：同一句提醒", 6, 41)]])
        self.assertEqual([x for x in doing[3:] if "─" in x], [
            rule,
            "├────────┼──────────┬────────────┬──────────┬────────┤",  # 任务甲的提醒下面还有任务乙：列从这里分开
            rule,
            "└────────┴───────────────────────────────────────────┘"])  # 任务乙的提醒下面是底线：合并处一条直线
        self.assertEqual(task_blocks(waiting)[0][1:], [rule, *[
            span_line(x, 6, 41) for x in ("提醒：" + long_note[:17], long_note[17:37], long_note[37:56] + "...")]])
        self.assertEqual([{d.text_width(x) for x in t} for t in (waiting, doing)], [{54}, {54}])
        self.assertEqual([bar_errors(t) for t in (waiting, doing)], [[], []])
        self.assertFalse([x for x in lines if x.startswith("提醒")], lines)

    def test_wrapup_regions_with_note(self) -> None:
        """完成表里带提醒的任务（t-note）和它下面那个任务（t-next）：两个 [收尾] 的点击区域各在自己任务的第一行（行号算上了
        t-note 的提醒和它上面那条线），点得中；点 t-note 的提醒上面那条线、提醒那几行、下面那条线（[收尾] 那一列、提醒的字上、
        任务列）都不送；竖线照样对齐。往右滚一列（60 格，合成的那一格变窄，提醒折成两行）、滚到只画了任务列和收尾列（30 格，
        偏移 4，提醒折 3 行）以后一样"""
        self.note_tasks()
        for width, offset, spans in ((160, 0, 1), (60, 1, 2), (30, 4, 3)):
            with self.subTest(width=width):
                d, lines, regions, got = self.frame(width, offset=offset)
                self.assertEqual(got, offset)
                wrapups = {r["action"]["send"]: r for r in regions if r["action"].get("all") is False}
                self.assertEqual(set(wrapups), {"收尾：t-note", "收尾：t-next"})
                for slug, title in (("t-note", "带提醒的任务"), ("t-next", "下面那个任务")):
                    r = wrapups[f"收尾：{slug}"]
                    self.assertEqual(cols(d, lines[r["line"]], r["start"], r["end"]), "[收尾]")
                    self.assertIn(title, lines[r["line"]])
                    self.assertEqual(d.board_hit(regions, 0, (r["start"] + 1, r["line"] + 1)), r["action"])
                note = next(i for i, x in enumerate(lines) if "提醒：" in x)
                # t-note 的任务行、提醒上面那条线、提醒那几行、下面那条线，接着是 t-next
                self.assertEqual((note, wrapups["收尾：t-next"]["line"]), (wrapups["收尾：t-note"]["line"] + 2, note + spans + 1))
                self.assertEqual((lines[note - 1][0], lines[note + spans][0]), ("│", "├"))
                self.assertEqual(bar_errors(box_tables(lines)[-1]), [])
                x = wrapups["收尾：t-note"]["start"] + 1
                for y in range(note - 1, note + spans + 1):
                    for col in (x, 30, 2):
                        self.assertIsNone(d.board_hit(regions, 0, (col, y + 1)))

    def test_note_two_columns(self) -> None:
        """滚到只画了任务列和一列（30 格：偏移 0 剩当前状态 8 格，偏移 4 剩收尾 6 格）：合成的那一格就是这一列，提醒上面那条线
        "│ … ├──┤"、下面那条线（下面还有 t-next）"├──┼──┤"，都没有 ┴ / ┬；t-next 下面照旧是底线"└──┴──┘\""""
        self.note_tasks()
        for offset, rules in ((0, ["│              ├──────────┤", "├──────────────┼──────────┤", "└──────────────┴──────────┘"]),
                              (4, ["│              ├────────┤", "├──────────────┼────────┤", "└──────────────┴────────┘"])):
            with self.subTest(offset=offset):
                _, lines, _, got = self.frame(30, offset=offset)
                table = box_tables(lines)[-1]
                self.assertEqual(got, offset)
                self.assertEqual([x for x in table[3:] if "─" in x], rules)
                self.assertEqual(bar_errors(table), [])
                self.assertEqual([len(b) for b in task_blocks(table)], [5, 1])  # 任务行、那条线、提醒 3 行；t-next 一行

    def note_tasks(self) -> None:
        """完成表里两个能收尾的任务：t-note（2 小时，带提醒 NOTE_PERF）在上，t-next（1 小时，不带）在下。"""
        self.task("t-note", "orch-note", "完成", title="带提醒的任务", summary="做完了", ask="无", wrapup="可以", note=NOTE_PERF,
                  age=2 * HOUR, read=True)
        self.task("t-next", "orch-next", "完成", title="下面那个任务", summary="也做完了", ask="无", wrapup="可以", age=HOUR,
                  read=True)
        self.set_agents(**{"orch-note": {"status": "idle", "screen": NORMAL}, "orch-next": {"status": "idle", "screen": NORMAL}})

    def test_list_plain_markdown_json(self) -> None:
        """列表写法：窄到退回列表（看板 tab 54 格、--live 子进程 40 格）时，写了提醒的任务在原来两行后面加第三行"  提醒：<原文>"
        （子进程按终端宽度截）；--plain 同样第三行、原文不截；Markdown 每张表下面一条一段"提醒（<标题>）：<原文>"，只列这张表里
        的任务，标题按 24 格截、原文里的"|"转义；--json 的 tasks 每项有 note（原文，没有是 null）。哪种写法底下都没有单独的提醒"""
        self.v8_tasks()
        d = self.load_drover()
        export = ["· 订单导出改成异步处理（orch-demo-export-job）",
                  "  暂停中，待选 MQ 队列方案｜要你：回 A/B/C/D（建议 A）｜已持续 4 小时 14 分", f"  提醒：{NOTE_EXPORT}"]
        heavy = ["· drover 检查提速（orch-demo-perf）", "  新编排者接手，等放行规则改完再集成｜要你：无｜已持续 34 分钟",
                 f"  提醒：{NOTE_PERF}"]
        cleanup = ["· worktree 及时清理（orch-demo-cleanup）", "  摸底完成，worker 在改 drover｜要你：无｜已持续 20 分钟"]
        _, lines, _, _ = self.frame(54)
        self.assertEqual(lines[1:], ["", "等你（1）", *export, "", "进行中（2）", *heavy, *cleanup])
        self.assertEqual(self.board("--plain").splitlines()[1:], ["等你（1）", *export, "进行中（2）", *heavy, *cleanup])
        live = self.live_lines(40)
        self.assertEqual(live[live.index("· drover 检查提速（orch-demo-perf）") + 2], d.fit_width(heavy[2], 40))
        self.assertTrue(d.fit_width(export[2], 40).endswith("..."))
        self.assertIn(d.fit_width(export[2], 40), live)
        self.task("t-pipe", "orch-pipe", "进行中", title=LONG_TITLE, summary="在改", ask="无", note="看 a|b 两处",
                  age=10 * MINUTE, read=True)
        self.set_agents(**V8_AGENTS, **{"orch-pipe": {"status": "working", "screen": NORMAL}})
        blocks = self.board().strip().split("\n\n")
        self.assertEqual([b for b in blocks if not b.startswith("|")][1:], [
            "**等你（1）**", f"提醒（订单导出改成异步处理）：{NOTE_EXPORT}",
            "**进行中（3）**", f"提醒（drover 检查提速）：{NOTE_PERF}", f"提醒（{d.fit_width(LONG_TITLE, 24)}）：看 a\\|b 两处"])
        self.assertEqual([i for i, b in enumerate(blocks) if b.startswith("|")], [2, 5])  # 提醒紧跟在它那张表下面
        self.assertEqual({k: v["note"] for k, v in self.tasks_json().items()}, {
            "orch-demo-export-job": NOTE_EXPORT, "orch-demo-perf": NOTE_PERF, "orch-demo-cleanup": None,
            "t-pipe": "看 a|b 两处"})


class BoardScrollTests(BoardTestCase):
    """终端窄时横向滚动：任务列固定，其余整列左右滚，几张表一起滚，表下面一行写左右还剩哪几列；任务列加最宽的
    一列都放不下时退回列表。伪终端里的滚动（66 / 67、← →、点 [<] [>]）在 BoardClickTests 里。"""

    def test_columns_layout(self) -> None:
        """board_columns：放得下全画、偏移归零；放不下时从 1 + 偏移列起往右取放得下的整列，偏移夹在 0 到"往右都放得下"之间；
        任务列加最宽的一列都放不下返回 None"""
        d = self.load_drover()
        widths = [20, 34, 24, 12, 20, 6]  # 任务、当前状态、要你做什么、状态持续、代号、收尾的列宽；整张完成表 135 格
        self.assertEqual(d.board_columns(widths, 135, 3), (0, [1, 2, 3, 4, 5], [], []))
        self.assertEqual(d.board_columns(widths, 100, 0), (0, [1, 2], [], [3, 4, 5]))
        self.assertEqual(d.board_columns(widths, 100, 1), (1, [2, 3, 4, 5], [1], []))
        self.assertEqual(d.board_columns(widths, 100, 9), (1, [2, 3, 4, 5], [1], []))
        self.assertEqual(d.board_columns(widths, 100, -2), (0, [1, 2], [], [3, 4, 5]))
        self.assertEqual(d.board_columns(widths, 61, 0), (0, [1], [], [2, 3, 4, 5]))  # 任务 20 + 当前状态 34 + 框 7
        self.assertEqual(d.board_columns(widths, 61, 2), (2, [3], [1, 2], [4, 5]))
        self.assertEqual(d.board_columns(widths, 61, 9), (3, [4, 5], [1, 2, 3], []))
        self.assertIsNone(d.board_columns(widths, 60, 0))

    def test_design_scrolled_frame(self) -> None:
        """100 格：往右滚了一列（偏移 1）就是 DESIGN_SCROLLED——几张表一起滚、任务列固定、竖线上下对齐，表下面一行"[<]  左边还有
        1 列（当前状态）  [>]     触控板左右划、左右方向键，或点 [<] [>]"；偏移再大也夹在 1；偏移 0 时右边藏 2 列、[收尾] 不在屏幕
        上就不能点；[<] [>] 能点（各三格）、[收尾] 在占两行的任务的第一行；每行都不超过 100 格，画出来的格子都是完整的（不切半个字、
        不加"..."）；放宽到 140 格偏移归零、没有提示行；没开鼠标时提示行只写还剩哪几列；不到 55 格（任务列 20 加最宽的当前状态列
        28 加框线 7）退回列表，[收尾] 接在任务那一行后面"""
        self.design_tasks()
        d, lines, regions, offset = self.frame(100, offset=1)
        self.assertEqual((offset, lines[1:]), (1, ["", *DESIGN_SCROLLED.splitlines()]))
        self.assertTrue(all(d.text_width(x) <= 100 for x in lines))
        hint = len(lines) - 1  # 提示行就是最后一行（提醒跟着任务写在表里，不单独列在它下面）
        self.assertEqual({str(r["action"]): cols(d, lines[r["line"]], r["start"], r["end"]) for r in regions}, {
            str(d.board_send_action(["orch-demo-perf"], True)): "[全部收尾（1 个）]",
            str(d.board_send_action(["orch-demo-perf"], False)): "[收尾]",
            "{'scroll': -1}": "[<]", "{'scroll': 1}": "[>]"})
        self.assertEqual({r["line"] for r in regions if "scroll" in r["action"]}, {hint})
        wrapup = next(r for r in regions if r["action"].get("slugs") == ["orch-demo-perf"] and not r["action"]["all"])
        self.assertIn("drover 检查提速", lines[wrapup["line"]])
        self.assertEqual(self.frame(100, offset=9)[3], 1)
        _, lines, regions, offset = self.frame(100, offset=0)
        self.assertEqual(lines[-1], SCROLL_HINT_LEFT)
        self.assertEqual([box_rows(t) for t in box_tables(lines)][0], [
            [["订单导出改成异步处理"], ["暂停，你要先弄清各功能点"], ["回 A/B/C/D（建议 A）"], ["29 小时"]],
            [["drover 任务看板"], ["设计稿出好了"], ["看设计稿，回确认或要改", "的"], ["刚刚"]]])
        self.assertNotIn(str(d.board_send_action(["orch-demo-perf"], False)), [str(r["action"]) for r in regions])
        self.assertFalse(any("…" in x or "..." in x for x in lines), lines)
        _, lines, _, offset = self.frame(140, offset=3)
        self.assertEqual((offset, lines[1:]), (0, ["", *DESIGN_WIDE.splitlines()]))
        _, lines, regions, _ = self.frame(100, offset=0, mouse=False)
        self.assertEqual((lines[-1], regions), ("右边还有 2 列（代号、收尾）", []))
        self.assertNotEqual(box_tables(self.frame(55)[1]), [])  # 55 格刚好放得下任务列加当前状态列
        _, lines, regions, _ = self.frame(54)
        self.assertEqual(box_tables(lines), [])
        self.assertIn("· drover 检查提速（orch-demo-perf）  [收尾]", lines)
        self.assertIn("  设计稿出好了｜要你：看设计稿，回确认或要改的｜刚开始", lines)
        self.assertEqual([cols(d, lines[r["line"]], r["start"], r["end"]) for r in regions],
                         ["[全部收尾（1 个）]", "[收尾]"])

    def test_draw_clamps_and_offsets(self) -> None:
        """board_draw：终端宽度取 COLUMNS，返回夹过的偏移和画出来的行（就是写出去的那一屏，拖选照它算）；插件回的提示空一行接在
        最后；一屏比终端高时开头滚出去，点击的行号加上滚掉的行数（board_hit），横向不用再换算"""
        self.design_tasks()
        d = self.load_drover()
        now = time.time()
        view = d.board_view(d.board_rows(True, now), now)
        os.environ.update(COLUMNS="100", LINES="100")
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            drawn, regions, offset, hoff = d.board_draw(view, now, (), True, "已送进主会话", 7)
        shown = buf.getvalue().replace("\033[H\033[2J", "").split("\n")
        self.assertEqual((offset, hoff, shown[-2:], drawn), (0, 1, ["", "已送进主会话"], shown))
        os.environ["LINES"] = str(len(shown) - 5)
        with contextlib.redirect_stdout(io.StringIO()):
            _, regions, offset, hoff = d.board_draw(view, now, (), True, "", hoff)
        self.assertEqual((offset, hoff), (len(shown) - 2 - (len(shown) - 5), 1))  # 没有提示那两行，多出来的滚出去了
        r = next(r for r in regions if r["action"].get("scroll") == 1)
        y = r["line"] - offset + 1
        self.assertEqual(d.board_hit(regions, offset, (r["start"] + 1, y)), {"scroll": 1})
        self.assertEqual(d.board_hit(regions, offset, (r["end"], y)), {"scroll": 1})  # 最后一格
        self.assertIsNone(d.board_hit(regions, offset, (r["end"] + 1, y)))
        self.assertIsNone(d.board_hit(regions, offset, (r["start"] + 1, y + 1)))


class BoardPaneTests(BoardTestCase):
    SPLIT = ["pane", "split", "--pane", "w1:p1", "--direction", "down", "--ratio", "0.78", "--no-focus"]

    def splits(self) -> list:
        return [c for c in self.herdr_calls() if c[:2] == ["pane", "split"]]

    def test_not_registered(self) -> None:
        """herdr 里没有叫 drover 的主会话 → 报错让先 main-register，不切 pane"""
        out = self.drover_json("board", "--pane", rc=1)
        self.assertIn("main-register", out["error"])
        self.assertEqual(self.splits(), [])

    def test_create_reuse_recreate(self) -> None:
        """--dry-run 只列命令；新建：在主会话 pane 下面 split（按 50 行算 --ratio 0.78，看板约 10 行）再 pane run 看板，写记录；
        再跑一次复用（reused: true，不再 split）；看板 pane 不在主会话的 tab 里了、被关了 → 重新开"""
        self.set_panes({"w1:p1": {"name": "drover", "tab_id": "w1:t1", "workspace_id": "w1", "rows": 50}})
        record = self.state / "board-pane.json"
        dry = self.drover_json("board", "--pane", "--dry-run")
        self.assertEqual((dry["dry_run"], dry["reused"], dry["ratio"]), (True, False, 0.78))
        self.assertEqual(dry["planned"][:2], ["herdr " + " ".join(self.SPLIT),
                                              "herdr pane run <新 pane> '~/.drover/bin/drover board --live'"])
        self.assertEqual((self.splits(), record.exists()), ([], False))
        out = self.drover_json("board", "--pane")
        self.assertEqual((out["reused"], out["pane_id"], out["main_pane_id"]), (False, "w1:p101", "w1:p1"))
        self.assertEqual(self.splits(), [self.SPLIT])
        self.assertEqual(self.herdr_state()["pane_runs"], [["w1:p101", "~/.drover/bin/drover board --live"]])
        self.assertEqual(json.loads(record.read_text()), {"pane_id": "w1:p101", "main_pane_id": "w1:p1"})
        again = self.drover_json("board", "--pane")
        self.assertEqual((again["reused"], again["pane_id"]), (True, "w1:p101"))
        self.assertEqual(len(self.splits()), 1)
        state = self.herdr_state()
        state["panes"]["w1:p1"]["tab_id"] = "w1:t2"  # 主会话挪到了别的 tab
        self.set_herdr_state(state)
        moved = self.drover_json("board", "--pane")
        self.assertEqual((moved["reused"], moved["pane_id"]), (False, "w1:p102"))
        state = self.herdr_state()
        del state["panes"]["w1:p102"]  # 看板 pane 被关了
        self.set_herdr_state(state)
        self.assertFalse(self.drover_json("board", "--pane")["reused"])
        self.assertEqual(len(self.splits()), 3)
        self.assertIn("--dry-run 只给 board --pane / --tab 用", self.drover("board", "--dry-run", rc=2).stderr)

    def live(self) -> str:
        return self.board("--live", "--iterations", "1", "--interval", "0")

    def renames(self) -> list:
        return [c for c in self.herdr_calls() if c[:2] == ["agent", "rename"]]

    def test_live_reregisters_unnamed_main_pane(self) -> None:
        """--live 每一轮：board-pane.json 记的主会话 pane 里的 agent 还在、没有名字（Codex /new 之后 herdr 清掉了）、工作目录是
        主工作台，herdr 里也没有别的 drover → 补登记成 drover，看板上留一行"已补登记主会话"；下一轮已经有名字了，不再改"""
        (self.state / "board-pane.json").write_text(json.dumps({"pane_id": "w1:p101", "main_pane_id": "w1:p1"}))
        self.set_panes({"w1:p1": {"agent": "codex", "tab_id": "w1:t1", "cwd": str(self.work)}})
        out = self.live()
        self.assertEqual(self.renames(), [["agent", "rename", "w1:p1", "drover"]])
        self.assertEqual(self.herdr_state()["panes"]["w1:p1"]["name"], "drover")
        self.assertRegex(out, r"已补登记主会话（\d\d:\d\d，pane w1:p1）")
        self.live()
        self.assertEqual(len(self.renames()), 1)

    def test_live_not_reregister_other_dir(self) -> None:
        """主会话的 pane 里换成了一个在别的目录（某个仓库）开的、没名字的会话：不是主会话，不补登记（飞书的"提交推送"会送进它）；
        herdr 没给工作目录的也不补（核实不了）；前台进程的工作目录（foreground_cwd）优先：pane 的 shell 在主工作台、会话在仓库里
        也不补"""
        (self.state / "board-pane.json").write_text(json.dumps({"pane_id": "w1:p101", "main_pane_id": "w1:p1"}))
        repo = self.work / "order-service"
        repo.mkdir()
        for pane in ({"agent": "claude", "cwd": str(repo)}, {"agent": "claude"},
                     {"agent": "claude", "cwd": str(self.work), "foreground_cwd": str(repo)}):
            with self.subTest(pane=pane):
                self.set_panes({"w1:p1": {**pane, "tab_id": "w1:t1"}})
                self.assertNotIn("已补登记", self.live())
        self.assertEqual(self.renames(), [])

    def test_live_leaves_other_names_alone(self) -> None:
        """别的 agent 已经叫 drover（另开了一个主会话）、主会话 pane 里的 agent 名字是别的、pane 里没有 agent、没有
        board-pane.json：都不改名"""
        (self.state / "board-pane.json").write_text(json.dumps({"pane_id": "w1:p101", "main_pane_id": "w1:p1"}))
        self.set_panes({"w1:p1": {}, "w2:p1": {"name": "drover"}})
        self.assertNotIn("已补登记", self.live())
        self.set_panes({"w1:p1": {"name": "orch-x"}})
        self.live()
        self.set_panes({"w1:p1": {"present": False}})
        self.live()
        (self.state / "board-pane.json").unlink()
        self.set_panes({"w1:p1": {}})
        self.live()
        self.assertEqual(self.renames(), [])


class BoardNudgeTests(BoardTestCase):
    """主会话的追踪断了（watch --main 每轮写的心跳 main-watch-live.json 停了），由常驻的看板发现，经飞书插件给主会话送一句
    "接上"（插件自己在开头加"[看板] "）。"""

    def live(self) -> str:
        return self.board("--live", "--iterations", "1", "--interval", "0")

    def beat(self, ago: float) -> None:
        (self.state / "main-watch-live.json").write_text(json.dumps({"pid": 1, "at": time.time() - ago}))

    def test_nudge_once_per_gap(self) -> None:
        """"主会话在处理"那一行不空、心跳停了 5 分钟以上 → 送一句"接上"，看板上留一行；同一段断档（心跳没变）只送一次；心跳接上
        又停了（换了一段）再送；没有心跳文件（没跑过 watch --main，比如 Codex 主会话）、心跳还新、"主会话在处理"是空的都不送；
        插件没装时看板上写没送成和原因"""
        self.task("t-a", "orch-a", "等你决定", sections={"需要你决定": ["- 选 A 还是 B"]}, age=HOUR)
        self.set_agents(**{"orch-a": {"status": "done", "screen": NORMAL}})
        self.beat(6 * MINUTE)
        self.assertRegex(self.live(), r"主会话的追踪停了 6 分钟，\"接上\"没送成（\d\d:\d\d）：飞书插件没装或版本太旧")
        root = self.fake_plugin()
        (self.state / "main-watch-live.json").unlink()
        self.assertNotIn("主会话的追踪", self.live())
        self.beat(4 * MINUTE)
        self.live()
        self.assertEqual(self.send_calls(root), [])
        self.beat(6 * MINUTE)
        out = self.live()
        want = [str(root), "src/actions/send-main.mjs", "--source", "board", "--text", "接上"]
        self.assertEqual(self.send_calls(root), [want])
        self.assertRegex(out, r"主会话的追踪停了 6 分钟，已送\"接上\"（\d\d:\d\d）")
        self.live()
        self.assertEqual(len(self.send_calls(root)), 1)
        self.beat(7 * MINUTE)
        self.live()
        self.assertEqual(self.send_calls(root), [want, want])
        self.main_rollup("--slug", "t-a")
        self.beat(8 * MINUTE)
        self.assertNotIn("主会话的追踪", self.live())
        self.assertEqual(len(self.send_calls(root)), 2)


MAIN_PANE = {"name": "drover", "tab_id": "w1:t1", "workspace_id": "w1"}
LIVE_CMD = "~/.drover/bin/drover board --live"
CREATE = ["tab", "create", "--workspace", "w1", "--label", "看板", "--no-focus"]
MOUSE_ON = b"\x1b[?1000h\x1b[?1002h\x1b[?1006h"
MOUSE_OFF = b"\x1b[?1006l\x1b[?1002l\x1b[?1000l"
BOARD_CLICK_HINT = "点看板任意位置回到主对话"


class TabTestCase(BoardTestCase):
    """--tab、看板 tab 里的 --live 共用的工具（没有用例）。"""

    def calls(self, *prefix: str) -> list:
        return [c for c in self.herdr_calls() if c[:len(prefix)] == list(prefix)]

    def record(self) -> dict:
        return json.loads((self.state / "board-tab.json").read_text())

    def live(self, pane: str = None, iterations: int = 1) -> str:
        """跑 --live；pane 给了就当它在这个 herdr pane 里跑（HERDR_PANE_ID）。"""
        if pane:
            self.env["HERDR_PANE_ID"] = pane
        try:
            return self.board("--live", "--iterations", str(iterations), "--interval", "0")
        finally:
            self.env.pop("HERDR_PANE_ID", None)

    def setup_tab(self) -> None:
        """看板 tab 已经开好（w1:t2，pane w1:p2），主会话在 w1:t1，旁边还有一个 --pane 开的分屏 w1:p101。"""
        self.set_panes({"w1:p1": MAIN_PANE, "w1:p2": {"tab_id": "w1:t2", "workspace_id": "w1", "present": False},
                        "w1:p101": {"tab_id": "w1:t1", "workspace_id": "w1", "present": False}},
                       tabs={"w1:t2": {"workspace_id": "w1", "label": "看板"}})
        (self.state / "board-tab.json").write_text(json.dumps(
            {"tab_id": "w1:t2", "pane_id": "w1:p2", "workspace_id": "w1", "main_pane_id": "w1:p1"}))


class BoardTabTests(TabTestCase):
    """--tab：看板挪进主会话所在 workspace 里一个和主对话同级的"看板"tab，不占主对话的屏幕；
    --live 在这个 tab 里时 tab 名跟着汇总改。点看板回主对话见 BoardClickTests。"""

    def test_not_registered(self) -> None:
        """herdr 里没有叫 drover 的主会话 → 报错让先 main-register，不开 tab、不关旧分屏"""
        (self.state / "board-pane.json").write_text(json.dumps({"pane_id": "w1:p101", "main_pane_id": "w1:p1"}))
        self.set_panes({"w1:p101": {"tab_id": "w1:t1", "present": False}})
        out = self.drover_json("board", "--tab", rc=1)
        self.assertIn("main-register", out["error"])
        self.assertEqual((self.calls("tab"), self.calls("pane", "close")), ([], []))
        self.assertTrue((self.state / "board-pane.json").exists())

    def test_create_reuse_recreate(self) -> None:
        """--dry-run 只列命令；新建：tab create（主会话的 workspace、标签"看板"、--no-focus，不抢焦点）再在它的 pane 里
        pane run 看板，记 board-tab.json（tab_id、pane_id、workspace_id、main_pane_id）；再跑一次复用（reused: true）；
        看板 tab 被关了、主会话挪到别的 workspace → 重新开；主会话换了 pane → 复用、记录里的 main_pane_id 跟着改"""
        self.set_panes({"w1:p1": MAIN_PANE})
        record = self.state / "board-tab.json"
        dry = self.drover_json("board", "--tab", "--dry-run")
        self.assertEqual((dry["dry_run"], dry["reused"], dry["workspace_id"], dry["main_tab_id"]), (True, False, "w1", "w1:t1"))
        self.assertEqual(dry["planned"], ["herdr " + shlex.join(CREATE), f"写 {record}", f"herdr pane run <新 tab 的 pane> '{LIVE_CMD}'"])
        self.assertEqual((self.calls("tab"), record.exists()), ([], False))
        out = self.drover_json("board", "--tab")
        self.assertEqual((out["reused"], out["tab_id"], out["pane_id"], out["workspace_id"], out["main_pane_id"]),
                         (False, "w1:t2", "w1:p2", "w1", "w1:p1"))
        self.assertEqual(self.calls("tab"), [CREATE])  # 只有 create：没有 tab focus
        self.assertFalse(self.herdr_state()["tabs"]["w1:t2"]["focus"])
        self.assertEqual(self.herdr_state()["pane_runs"], [["w1:p2", LIVE_CMD]])
        self.assertEqual(self.record(), {"tab_id": "w1:t2", "pane_id": "w1:p2", "workspace_id": "w1", "main_pane_id": "w1:p1"})
        again = self.drover_json("board", "--tab")
        self.assertEqual((again["reused"], again["tab_id"], again["pane_id"]), (True, "w1:t2", "w1:p2"))
        self.assertEqual(len(self.calls("tab", "create")), 1)
        state = self.herdr_state()
        del state["tabs"]["w1:t2"], state["panes"]["w1:p2"]  # 用户把看板 tab 关了
        self.set_herdr_state(state)
        self.assertEqual((self.drover_json("board", "--tab")["tab_id"], self.record()["tab_id"]), ("w1:t3", "w1:t3"))
        state = self.herdr_state()
        state["panes"]["w1:p7"] = state["panes"].pop("w1:p1")  # 主会话换了 pane（同一个 workspace）
        self.set_herdr_state(state)
        self.assertTrue(self.drover_json("board", "--tab")["reused"])
        self.assertEqual(self.record()["main_pane_id"], "w1:p7")
        state = self.herdr_state()
        state["panes"]["w2:p1"] = dict(state["panes"].pop("w1:p7"), tab_id="w2:t1", workspace_id="w2")
        self.set_herdr_state(state)
        moved = self.drover_json("board", "--tab")
        self.assertEqual((moved["reused"], moved["tab_id"], moved["workspace_id"]), (False, "w2:t2", "w2"))
        self.assertEqual(self.calls("tab", "focus"), [])

    def test_closes_old_split(self) -> None:
        """顺带收掉 --pane 开的下方分屏：board-pane.json 记的 pane 还在 → --dry-run 只列出来；真跑时看板 tab 开好之后
        pane close 它、删记录（只关它，主会话的 pane 不动）；记的 pane 已经不在了 → 只删记录；复用看板 tab 时也收"""
        self.set_panes({"w1:p1": MAIN_PANE, "w1:p101": {"tab_id": "w1:t1", "present": False}})
        old = self.state / "board-pane.json"
        old.write_text(json.dumps({"pane_id": "w1:p101", "main_pane_id": "w1:p1"}))
        dry = self.drover_json("board", "--tab", "--dry-run")
        self.assertEqual(dry["old_split"], {"pane_id": "w1:p101", "action": "planned",
                                            "planned": ["herdr pane close w1:p101", f"删 {old}"]})
        self.assertEqual((self.calls("pane", "close"), old.exists()), ([], True))
        out = self.drover_json("board", "--tab")
        self.assertEqual(out["old_split"], {"pane_id": "w1:p101", "action": "closed"})
        self.assertEqual(self.calls("pane", "close"), [["pane", "close", "w1:p101"]])
        names = [c[:2] for c in self.herdr_calls()]
        self.assertLess(names.index(["pane", "run"]), names.index(["pane", "close"]))  # 新看板起来了才关旧的
        self.assertFalse(old.exists())
        self.assertIn("w1:p1", self.herdr_state()["panes"])
        old.write_text(json.dumps({"pane_id": "w1:p102", "main_pane_id": "w1:p1"}))  # 记的 pane 已经被关了
        again = self.drover_json("board", "--tab")
        self.assertEqual((again["reused"], again["old_split"]), (True, {"pane_id": "w1:p102", "action": "already_gone"}))
        self.assertEqual((len(self.calls("pane", "close")), old.exists()), (1, False))
        self.assertNotIn("old_split", self.drover_json("board", "--tab"))

    def test_pane_run_failure(self) -> None:
        """tab 开好了、pane run 失败 → 报错（提示关掉那个 tab 再重跑），不留记录（下次重新开，不会复用一个没跑看板的 tab），
        旧分屏不关"""
        self.set_panes({"w1:p1": MAIN_PANE, "w1:p101": {"tab_id": "w1:t1", "present": False}})
        (self.state / "board-pane.json").write_text(json.dumps({"pane_id": "w1:p101", "main_pane_id": "w1:p1"}))
        state = self.herdr_state()
        state["pane_run_fail"] = True
        self.set_herdr_state(state)
        out = self.drover_json("board", "--tab", rc=1)
        self.assertIn("herdr tab close w1:t2", out["error"])
        self.assertFalse((self.state / "board-tab.json").exists())
        self.assertEqual(self.calls("pane", "close"), [])

    def test_live_tab_label(self) -> None:
        """--live 在看板 tab 里：tab 名就是汇总（等你 / 进行中 / 完成各几个任务，按这个顺序，为 0 的不写，都为 0 叫"看板"），
        按计数变，没变不重复 rename；不在 herdr 里（没有 HERDR_PANE_ID）、在别的 tab（--pane 的分屏）里不改名，照常画看板；
        stdout 不是终端时不开鼠标"""
        self.setup_tab()
        self.task("t-a", "orch-a", "进行中", age=HOUR)
        self.set_agents(**{"orch-a": {"status": "done", "screen": WEBFETCH}})
        out = self.live("w1:p2", iterations=2)
        self.assertEqual(self.calls("tab", "rename"), [["tab", "rename", "w1:t2", "看板·进行中1"]])  # 第二轮没变不改
        self.assertNotIn("\033[?1000h", out)
        self.assertNotIn("◀", out)
        self.task("t-u", "orch-u", "等你决定", age=HOUR, read=True)
        self.task("t-w", "orch-w", "完成", summary="做完了", age=HOUR, read=True)
        self.set_agents(**{"orch-u": {"status": "done", "screen": NORMAL}, "orch-a": {"status": "done", "screen": WEBFETCH},
                           "orch-w": {"status": "idle", "screen": NORMAL}})
        self.live("w1:p2")
        self.assertEqual(self.calls("tab", "rename")[-1], ["tab", "rename", "w1:t2", "看板·等你1·进行中1·完成1"])
        self.assertEqual(self.herdr_state()["tabs"]["w1:t2"]["label"], "看板·等你1·进行中1·完成1")
        before = len(self.herdr_calls())
        self.assertIn("t-u", self.live())
        self.assertEqual([c for c in self.herdr_calls()[before:] if c[0] == "tab" or c[:2] == ["pane", "get"]], [])
        self.live("w1:p101")  # --pane 的分屏：在主会话的 tab 里，不是看板 tab
        self.assertEqual(len(self.calls("tab", "rename")), 2)
        d = self.load_drover()
        view = {"groups": [{"key": "wait", "title": "等你", "rows": []}, {"key": "doing", "title": "进行中", "rows": []},
                           {"key": "done", "title": "完成", "rows": []}]}
        self.assertEqual((d.board_tab_label(view), d.board_line(view)), ("看板", "drover 无任务"))
        view["groups"][1]["rows"] = [{}, {}, {}]
        self.assertEqual(d.board_tab_label(view), "看板·进行中3")
        view["groups"][0]["rows"], view["groups"][2]["rows"] = [{}, {}], [{}]
        self.assertEqual(d.board_tab_label(view), "看板·等你2·进行中3·完成1")
        self.assertEqual(d.board_line(view), "drover 等你 2 · 进行中 3 · 完成 1")

    def test_live_reregisters_from_tab_record(self) -> None:
        """--tab 开的看板（board-pane.json 已经收掉了）照样补登记：主会话的 pane 记在 board-tab.json 的 main_pane_id"""
        self.setup_tab()
        state = self.herdr_state()
        state["panes"]["w1:p1"] = {"agent": "codex", "tab_id": "w1:t1", "workspace_id": "w1", "cwd": str(self.work)}
        self.set_herdr_state(state)
        self.assertRegex(self.live(), r"已补登记主会话（\d\d:\d\d，pane w1:p1）")
        self.assertEqual(self.calls("agent", "rename"), [["agent", "rename", "w1:p1", "drover"]])


class PtyTestCase(TabTestCase):
    """在伪终端里跑看板 tab 的 --live 共用的工具（没有用例）：BoardClickTests、test_board_reexec.py 用。"""

    def start_pty(self, pane: str, *args: str, setup: bool = True, cols: int = 120, script: Path = DROVER) -> tuple:
        """在伪终端（40 行 × cols 列）里跑 script（默认本 worktree 的 bin/drover）board --live（HERDR_PANE_ID=pane）：返回
        (进程, master, slave, 启动前的终端属性)。setup 时先开好看板 tab、建一个进行中的任务 t-a。"""
        if setup:
            self.setup_tab()
            self.task("t-a", "orch-a", "进行中", age=HOUR)
            self.set_agents(**{"orch-a": {"status": "working", "screen": NORMAL}})
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, cols, 0, 0))
        before = termios.tcgetattr(slave)
        p = subprocess.Popen([sys.executable, str(script), "board", "--live", *args], env=dict(self.env, HERDR_PANE_ID=pane),
                             cwd=str(self.tmp), stdin=slave, stdout=slave, stderr=slave)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        return p, master, slave, before

    @staticmethod
    def read_until(master: int, marker: bytes, timeout: float = 30) -> bytes:
        out, deadline = b"", time.time() + timeout
        while marker not in out and time.time() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                out += os.read(master, 65536)
        return out

    @staticmethod
    def read_until_exit(p: subprocess.Popen, master: int, timeout: float = 60) -> bytes:
        """边读伪终端边等进程退出（伪终端的缓冲很小，不读的话它写不出去、退不了），返回读到的全部输出。"""
        out, deadline = b"", time.time() + timeout
        while time.time() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                out += os.read(master, 65536)
            elif p.poll() is not None:
                break
        return out

    def focused(self) -> list:
        return self.herdr_state().get("tab_focus", [])

    def assert_restored(self, slave: int, before: list) -> None:
        """终端属性恢复成启动前的。lflag 的 PENDIN 不比：从 cbreak 切回 canonical 时 macOS（BSD）的 tty 驱动自己置上它
        （"retype pending input"，让还没处理的输入按行重新处理一遍），不是 drover 设的（实测只差这一位）。"""
        mask = ~getattr(termios, "PENDIN", 0)
        after = termios.tcgetattr(slave)
        self.assertEqual([after[3] & mask] + after[:3] + after[4:], [before[3] & mask] + before[:3] + before[4:])

    @staticmethod
    def frame(out: bytes) -> list:
        """输出里最后画的那一屏，一行一项（伪终端把 \\n 输出成 \\r\\n）。"""
        return out.rsplit(b"\x1b[H\x1b[2J", 1)[-1].decode(errors="replace").replace("\r\n", "\n").split("\n")

    def until_frame(self, master: int, want, timeout: float = 20) -> list:
        """边读伪终端边等最后画的那一屏满足 want（一个函数，参数是那一屏的行）；满足之后再读到 0.3 秒没有新输出（一屏可能分几次
        读到），返回那一屏。等不到就失败，带上最后一屏。"""
        out, deadline = b"", time.time() + timeout
        while time.time() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                out += os.read(master, 65536)
            elif out and want(self.frame(out)):
                return self.frame(out)
        self.fail("\n".join(self.frame(out)))

    @staticmethod
    def wait_reading(master: int, predicate, timeout: float = 20) -> bytes:
        """边读伪终端边等 predicate 成立（看板点完要重画，伪终端的缓冲很小，不读它就卡在写上）；返回读到的输出。"""
        out, deadline = b"", time.time() + timeout
        while not predicate() and time.time() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                out += os.read(master, 65536)
        return out

    def click(self, master: int, lines: list, text: str, nth: int = 0) -> None:
        """按画出来的那一屏点 text（第 nth 个出现的那一行）的第二格（中文两格；SGR 的列、行从 1 数）：左键按下、松开在同一格。"""
        d = self.load_drover()
        y = [i for i, line in enumerate(lines) if text in line][nth]
        x = d.text_width(lines[y][:lines[y].index(text)]) + 2
        os.write(master, f"\x1b[<0;{x};{y + 1}M\x1b[<0;{x};{y + 1}m".encode())


class BoardClickTests(PtyTestCase):
    """看板 tab 里的 --live：stdin / stdout 是终端时，stdin 改 cbreak、打开 SGR 鼠标上报，读点击和左右滚。点击规则：
    某一行的 [收尾] 送进主会话、留在看板、那一格变"收尾中"；[全部收尾] 送进主会话再切到主会话的 tab；[<] [>] 左右滚；别的地方
    什么都不做。用伪终端跑真的 bin/drover（不替换 tty 操作），看终端属性和输出的转义序列。"""

    def test_click_elsewhere_does_nothing_and_ctrl_c_restores(self) -> None:
        """看板 tab 里：输出开鼠标上报的序列（1000、1002 拖动、1006 SGR），终端改成 cbreak（不回显、不等回车，Ctrl-C 照样是信号），
        不再写"点看板任意位置回到主对话"；普通按键、没按下的左键松开、右键、竖向滚轮、没按下的拖动、左键点在标题和表格里（不在
        按钮上，含 Shift+左键）→ 什么都不做（不切 tab、不送东西）；Ctrl-C → 退出码 0，关掉鼠标上报，终端属性恢复成启动前的"""
        root = self.fake_plugin()
        p, master, slave, before = self.start_pty("w1:p2", "--interval", "60")
        out = self.read_until(master, "进行中（1）".encode())
        self.assertIn(MOUSE_ON, out)
        self.assertNotIn(BOARD_CLICK_HINT.encode(), out)
        lflag = termios.tcgetattr(slave)[3]
        self.assertEqual((lflag & termios.ICANON, lflag & termios.ECHO, bool(lflag & termios.ISIG)), (0, 0, True))
        lines = self.frame(out)
        os.write(master, b"abc q\x1b[<0;10;5m\x1b[<2;10;5M\x1b[<64;10;5M\x1b[<65;10;5M\x1b[<32;10;5M")
        self.click(master, lines, "drover 看板")
        self.click(master, lines, "t-a")
        os.write(master, b"\x1b[<4;3;2M\x1b[<4;3;2m")  # Shift+左键
        self.wait_reading(master, lambda: False, timeout=1.5)
        self.assertEqual((self.focused(), self.send_calls(root)), ([], []))
        p.send_signal(signal.SIGINT)
        out += self.read_until_exit(p, master)  # 边读边等：伪终端的缓冲很小，不读的话它写不出去
        self.assertEqual(p.returncode, 0)
        self.assertIn(MOUSE_OFF, out)
        self.assertGreater(out.rindex(MOUSE_OFF), out.rindex(MOUSE_ON))
        self.assert_restored(slave, before)

    def test_iterations_exit_restores_and_other_tab_no_mouse(self) -> None:
        """--iterations 画够了正常退出：同样关掉鼠标上报、恢复终端；不在看板 tab 里（--pane 的分屏）即使是终端也不开鼠标、
        不动终端属性"""
        p, master, slave, before = self.start_pty("w1:p2", "--iterations", "1", "--interval", "0")
        out = self.read_until_exit(p, master)
        self.assertEqual(p.returncode, 0)
        self.assertIn(MOUSE_ON, out)
        self.assertIn(MOUSE_OFF, out)
        self.assert_restored(slave, before)
        p, master, slave, before = self.start_pty("w1:p101", "--iterations", "1", "--interval", "0")
        out = self.read_until_exit(p, master)
        self.assertEqual(p.returncode, 0)
        self.assertIn("进行中（1）".encode(), out)
        self.assertNotIn(MOUSE_ON, out)
        self.assertEqual(termios.tcgetattr(slave), before)  # 一直没碰终端属性：连 PENDIN 都一样
        self.assertEqual(self.calls("tab", "rename"), [["tab", "rename", "w1:t2", "看板·进行中1"]])  # 只有看板 tab 那次改名

    def test_click_wrapup_buttons(self) -> None:
        """两个能收尾的完成任务（终端够宽，收尾列在屏幕上）：点 heavy 那一行的 [收尾] → 把"收尾：orch-demo-perf"交给
        herdr-oncall 的 send-main.mjs（--source board，不带 [看板] 前缀），插件回的 notice 显示在看板上，不切 tab；那一格变
        "收尾中"、[全部收尾] 的个数从 2 变 1，记 wrapup-clicked.json 和时间线；再点那一格（已经不是按钮）不送。点
        [全部收尾（1 个）] → 只送还没点过的那个（"全部收尾：t-done2"），两行都是"收尾中"，切到主会话的 tab。
        点的位置按画出来的那一屏找（行号、显示列，中文两格）"""
        self.setup_tab()
        self.design_tasks()
        self.task("t-done2", "orch-done2", "完成", title="第二个任务", summary="做完了", age=HOUR, read=True)
        self.set_agents(**{"orch-demo": {"status": "done", "screen": NORMAL}, "orch-board": {"status": "done", "screen": NORMAL},
                           "orch-heavy": {"status": "idle", "screen": NORMAL}, "orch-done2": {"status": "idle", "screen": NORMAL}})
        root = self.fake_plugin(reply={"ok": True, "result": "delivered", "reason": "ready", "notice": "已送进主会话，在收尾 1 个"})
        p, master, slave, before = self.start_pty("w1:p2", "--interval", "60", setup=False, cols=160)
        lines = self.until_frame(master, lambda f: any("[全部收尾（2 个）]" in x for x in f))
        row = next(i for i, x in enumerate(lines) if "orch-demo-perf" in x)
        self.assertTrue(lines[row].endswith("│ [收尾] │"), lines[row])
        self.click(master, lines, "[收尾]", nth=[i for i, x in enumerate(lines) if "[收尾]" in x].index(row))
        lines = self.until_frame(master, lambda f: any("已送进主会话，在收尾 1 个" in x for x in f)
                                 and any("[全部收尾（1 个）]" in x for x in f))
        self.assertEqual(self.send_calls(root), [[str(root), "src/actions/send-main.mjs", "--source", "board",
                                                  "--text", "收尾：orch-demo-perf"]])
        self.assertEqual(lines[-2:], ["", "已送进主会话，在收尾 1 个"])
        row = next(i for i, x in enumerate(lines) if "orch-demo-perf" in x)
        self.assertTrue(lines[row].endswith("│ 收尾中 │"), lines[row])
        self.assertEqual(self.focused(), [])
        clicked = json.loads((self.state / "orch-demo-perf" / "wrapup-clicked.json").read_text())
        self.assertEqual(clicked["text"], "收尾：orch-demo-perf")
        events = [json.loads(x) for x in (self.state / "orch-demo-perf" / "timeline.jsonl").read_text().splitlines()]
        self.assertEqual([(e["source"], e["all"]) for e in events if e["event"] == "wrapup-clicked"], [("board", False)])
        self.click(master, lines, "收尾中")  # 已经点过：不是按钮了
        self.wait_reading(master, lambda: False, timeout=1.5)
        self.assertEqual(len(self.send_calls(root)), 1)
        self.click(master, lines, "[全部收尾（1 个）]")
        self.wait_reading(master, lambda: self.focused() == ["w1:t1"])
        self.assertEqual(self.send_calls(root)[1][-1], "全部收尾：t-done2")
        self.assertEqual(self.focused(), ["w1:t1"])
        lines = self.until_frame(master, lambda f: sum("收尾中" in x for x in f) == 2)
        self.assertFalse(any("[全部收尾" in x for x in lines), lines)
        events = [json.loads(x) for x in (self.state / "t-done2" / "timeline.jsonl").read_text().splitlines()]
        self.assertEqual([(e["source"], e["all"]) for e in events if e["event"] == "wrapup-clicked"], [("board", True)])
        p.send_signal(signal.SIGINT)
        self.read_until_exit(p, master)
        self.assertEqual(p.returncode, 0)
        self.assert_restored(slave, before)

    def test_horizontal_scroll(self) -> None:
        """100 列的伪终端（design_tasks 那三个任务，整张完成表 127 格）：一开始偏移 0，提示行"右边还有 2 列"；SGR 67（触控板往右划）
        → 偏移 1，就是 DESIGN_SCROLLED（任务列固定、几张表一起滚、左边还有 1 列）；Shift 加 66（70）→ 回到 0；→ 键按两下 → 1（滚到
        头不再动）；← 键 → 0；点 [>] → 1；点 [<] → 0。收到的原始鼠标序列记进 board-mouse.log（ESC 写成"ESC"），方向键不记。
        滚过以后点击照样按画出来的位置算：再滚到 1，heavy 那个任务占两行，点它第二行 [收尾] 那一列不送，点第一行的 [收尾] 送
        "收尾：orch-demo-perf\""""
        self.setup_tab()
        self.design_tasks()
        root = self.fake_plugin()
        p, master, slave, before = self.start_pty("w1:p2", "--interval", "60", setup=False, cols=100)
        at_left = lambda f: SCROLL_HINT_LEFT in f  # noqa: E731
        at_right = lambda f: any(x.startswith("[<]  左边还有 1 列（当前状态）  [>]") for x in f)  # noqa: E731
        self.until_frame(master, at_left)
        os.write(master, b"\x1b[<67;50;10M")
        lines = self.until_frame(master, at_right)
        self.assertEqual(lines[1:], ["", *DESIGN_SCROLLED.splitlines()])
        os.write(master, b"\x1b[<70;50;10M")
        self.until_frame(master, at_left)
        os.write(master, b"\x1b[C\x1b[C")
        self.until_frame(master, at_right)
        os.write(master, b"\x1b[D")
        lines = self.until_frame(master, at_left)
        self.click(master, lines, "[>]")
        lines = self.until_frame(master, at_right)
        self.click(master, lines, "[<]")
        self.until_frame(master, at_left)
        log = (self.state / "board-mouse.log").read_text().splitlines()
        self.assertEqual([x.split(" ", 2)[2] for x in log][:2], ["ESC[<67;50;10M", "ESC[<70;50;10M"])
        self.assertEqual(len(log), 6)  # 两次滚轮、两次点击（按下、松开各一条）；方向键不是鼠标序列
        self.assertRegex(log[0], r"^\d\d-\d\d \d\d:\d\d:\d\d ESC\[<67;50;10M$")
        os.write(master, b"\x1b[C")
        lines = self.until_frame(master, at_right)
        row = next(i for i, x in enumerate(lines) if "orch-demo-perf" in x)
        self.assertEqual(box_cells(lines[row + 1]), ["", "", "", "", ""])  # 占两行：第二行都空着
        x = self.load_drover().text_width(lines[row][:lines[row].index("[收尾]")]) + 2
        os.write(master, f"\x1b[<0;{x};{row + 2}M\x1b[<0;{x};{row + 2}m".encode())  # 第二行、[收尾] 那一列
        self.wait_reading(master, lambda: False, timeout=1.5)
        self.assertEqual(self.send_calls(root), [])
        self.click(master, lines, "[收尾]")
        self.wait_reading(master, lambda: self.send_calls(root) != [])
        self.assertEqual([c[-1] for c in self.send_calls(root)], ["收尾：orch-demo-perf"])
        p.send_signal(signal.SIGINT)
        self.read_until_exit(p, master)
        self.assertEqual(p.returncode, 0)
        self.assert_restored(slave, before)

    def test_mouse_log_keeps_last(self) -> None:
        """board-mouse.log 只留最近 50 条：一次读到 60 条鼠标序列，留后 50 条；没有鼠标序列（方向键、普通按键）不写文件；
        拖动（32，带修饰键 36）不记，同一次读到的按下、松开照记"""
        d = self.load_drover()
        d.board_mouse_log(b"\x1b[C abc")
        self.assertFalse((self.state / "board-mouse.log").exists())
        d.board_mouse_log(b"".join(f"\x1b[<66;{i};1M".encode() for i in range(60)))
        log = (self.state / "board-mouse.log").read_text().splitlines()
        self.assertEqual((len(log), log[0].split(" ", 2)[2], log[-1].split(" ", 2)[2]), (50, "ESC[<66;10;1M", "ESC[<66;59;1M"))
        d.board_mouse_log(b"\x1b[<0;1;1M\x1b[<32;2;1M\x1b[<36;3;1M\x1b[<0;3;1m")
        log = (self.state / "board-mouse.log").read_text().splitlines()
        self.assertEqual([x.split(" ", 2)[2] for x in log[-3:]], ["ESC[<66;59;1M", "ESC[<0;1;1M", "ESC[<0;3;1m"])

    def test_input_parser(self) -> None:
        """SGR 1006：M 是按下、m 是松开；按键码去掉 Shift / Meta / Ctrl（4 / 8 / 16）之后 0 是左键（按下 press、松开 release），
        32 是按住左键移动（1002 的拖动 drag），66 / 67 是横向滚轮往左 / 往右（xterm：按键 6、7 加 64）；← →（ESC [ D / ESC [ C，
        应用光标模式 ESC O D / C）往左 / 往右滚；一次读到好几个按出现的顺序都给；中键、右键（按下、拖动 33 / 34、松开）、竖向滚轮
        64 / 65、滚轮的"松开"、普通按键、旧的 X10 编码都不算"""
        d = self.load_drover()
        for data, want in ((b"\x1b[<0;1;1M", [("press", 1, 1)]), (b"\x1b[<16;80;24M", [("press", 80, 24)]),
                           (b"x\x1b[<0;1;1m\x1b[<0;2;3M\x1b[<0;9;9M", [("release", 1, 1), ("press", 2, 3), ("press", 9, 9)]),
                           (b"\x1b[<0;5;5M\x1b[<32;6;5M\x1b[<36;7;6M\x1b[<0;7;6m",
                            [("press", 5, 5), ("drag", 6, 5), ("drag", 7, 6), ("release", 7, 6)]),
                           (b"\x1b[<66;5;5M\x1b[<67;5;5M\x1b[<70;5;5M\x1b[<75;5;5M\x1b[<83;5;5M",
                            [("scroll", -1), ("scroll", 1), ("scroll", -1), ("scroll", 1), ("scroll", 1)]),
                           (b"\x1b[D\x1b[C\x1bOD\x1bOC", [("scroll", -1), ("scroll", 1), ("scroll", -1), ("scroll", 1)]),
                           (b"\x1b[<66;5;5m", []), (b"\x1b[<1;1;1M", []), (b"\x1b[<2;1;1M", []), (b"\x1b[<2;1;1m", []),
                           (b"\x1b[<33;1;1M", []), (b"\x1b[<34;1;1M", []), (b"\x1b[<32;1;1m", []), (b"\x1b[<64;1;1M", []),
                           (b"\x1b[<65;1;1M", []), (b"\x1b[M !!", []), (b"hello\r\x1b[A", [])):
            with self.subTest(data=data):
                self.assertEqual(d.board_input(data), want)


OSC52_RE = re.compile(rb"\x1b\]52;c;([A-Za-z0-9+/=]*)\x07")
SELECT_ON, SELECT_OFF = "\x1b[7m", "\x1b[27m"


class BoardSelectTests(PtyTestCase):
    """拖选复制：看板开了鼠标上报，herdr 不替它选中，看板自己做拖选复制——按住左键拖动
    反色画选区（按阅读顺序：首行从按下那一列到行尾、中间整行、末行从行首到现在这一列，反着拖一样，中文字盖住一半算整个），
    松开时把画出来的那几行字（行尾空格去掉）用 OSC 52 写进剪贴板，底下显示"已复制 N 行"、高亮消掉；按下松开在同一格才算单击，
    拖过的不算点按钮；拖动中不刷新、不重读。用伪终端跑真的 bin/drover，看它输出的转义序列。"""

    @staticmethod
    def spot(lines: list, row: str, text: str, cell: int) -> tuple:
        """SGR 的位置（列、行，从 1 数）：lines 里第一个含 row 的那一行，text 开头那个字的第 cell 格（中文 1 是左半、2 是右半）。"""
        y = next(i for i, line in enumerate(lines) if row in line)
        return text_cols(lines[y][:lines[y].index(text)]) + cell, y + 1

    @staticmethod
    def until_quiet(master: int, marker: bytes, timeout: float = 20) -> bytes:
        """边读伪终端边等 marker 出现，出现以后再读到 0.3 秒没有新输出；返回这期间读到的全部输出。"""
        out, deadline = b"", time.time() + timeout
        while time.time() < deadline:
            if select.select([master], [], [], 0.3)[0]:
                out += os.read(master, 65536)
            elif marker in out:
                return out
        raise AssertionError(f"等不到 {marker!r}：{out[-500:]!r}")

    @staticmethod
    def mouse(*events: tuple) -> bytes:
        """("press" / "drag" / "release", (列, 行)) 写成 SGR 序列：按下 0…M、拖动 32…M、松开 0…m。"""
        codes = {"press": ("0", "M"), "drag": ("32", "M"), "release": ("0", "m")}
        return b"".join(f"\x1b[<{codes[k][0]};{x};{y}{codes[k][1]}".encode() for k, (x, y) in events)

    def test_selection_rules(self) -> None:
        """board_selection：两个位置按阅读顺序排（反着一样），换成 shown 的行号（加上滚出屏幕的行数）和显示列，两头那一格都算；
        同一格是 {}；拖到最后一行下面算到最后一行的行尾。board_cut：中文字盖住一半（左半、右半）都算整个字。board_copy：选中的字
        每行去掉行尾空格、换行连起来，写成 OSC 52（BEL 结尾），返回行数；一个字都没选中不写、返回 0"""
        d = self.load_drover()
        shown = ["ab中文cd  ", "", "第三行", "x"]
        self.assertEqual(d.board_selection(shown, 0, (4, 1), (2, 3)), {0: (3, 10), 1: (0, 0), 2: (0, 1)})
        self.assertEqual(d.board_selection(shown, 0, (2, 3), (4, 1)), d.board_selection(shown, 0, (4, 1), (2, 3)))
        self.assertEqual(d.board_selection(shown, 2, (1, 1), (3, 1)), {2: (0, 2)})
        self.assertEqual(d.board_selection(shown, 0, (5, 1), (5, 1)), {})
        self.assertEqual(d.board_selection(shown, 0, (3, 3), (9, 30)), {2: (2, 6), 3: (0, 1)})
        for lo, hi, want in ((3, 3, "中"), (2, 2, "中"), (3, 4, "中文"), (4, 5, "文"), (0, 100, "ab中文cd  "), (8, 9, "  ")):
            with self.subTest(lo=lo, hi=hi):
                before, picked, after = d.board_cut(shown[0], lo, hi)
                self.assertEqual((picked, before + picked + after), (want, shown[0]))
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(d.board_copy(shown, d.board_selection(shown, 0, (4, 1), (2, 3))), 3)
        self.assertEqual(OSC52_RE.fullmatch(buf.getvalue().encode()).group(1),
                         base64.b64encode("中文cd\n\n第".encode()))
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(d.board_copy(shown, d.board_selection(shown, 0, (1, 2), (9, 2))), 0)
        self.assertEqual(buf.getvalue(), "")

    def test_drag_copies_and_click_still_works(self) -> None:
        """design_tasks 那三个任务（160 列，不用滚）：从"drover 任务看板"的"任"右半格按下，拖过中间几行到 heavy 那一行"检"的左半格
        → 这一屏照原样重画，首行从"任"起到行尾、中间几行整行、末行从行首到"检"反色（两个半格都算整个字），不送东西；松开 → OSC 52
        的 base64 解出来正好是这几行（首行从"任"起、末行到"检"止，每行去掉行尾空格），底下"已复制 N 行"，高亮没了。反着拖（从
        "检"拖回"任"）复制的一样。从 [收尾] 按下、拖到 [全部收尾（1 个）] 上松开 → 复制，不送收尾、不切 tab。没拖动的单击 [收尾]
        照旧送"收尾：orch-demo-perf\""""
        self.setup_tab()
        self.design_tasks()
        root = self.fake_plugin()
        p, master, slave, before = self.start_pty("w1:p2", "--interval", "60", setup=False, cols=160)
        lines = self.until_frame(master, lambda f: any("[全部收尾（1 个）]" in x for x in f))
        start = self.spot(lines, "drover 任务看板", "任务看板", 2)
        end = self.spot(lines, "orch-demo-perf", "检查提速", 1)
        top, bottom = start[1] - 1, end[1] - 1
        first, last = lines[top], lines[bottom]
        want = "\n".join([first[first.index("任务看板"):].rstrip(), *[x.rstrip() for x in lines[top + 1:bottom]],
                          last[:last.index("检查提速") + 1]])
        middle = (start[0] + 3, top + 3)
        os.write(master, self.mouse(("press", start), ("drag", middle), ("drag", end)))
        painted = self.frame(self.until_quiet(master, SELECT_ON.encode()))
        i, j = first.index("任务看板"), last.index("检查提速") + 1
        self.assertEqual(painted[top], first[:i] + SELECT_ON + first[i:] + SELECT_OFF)
        self.assertEqual(painted[top + 1:bottom], [SELECT_ON + x + SELECT_OFF if x else x for x in lines[top + 1:bottom]])
        self.assertEqual(painted[bottom], SELECT_ON + last[:j] + SELECT_OFF + last[j:])
        self.assertEqual(painted[:top] + painted[bottom + 1:], lines[:top] + lines[bottom + 1:])
        copied = f"已复制 {bottom - top + 1} 行"
        os.write(master, self.mouse(("release", end)))
        out = self.until_quiet(master, copied.encode())
        self.assertEqual([base64.b64decode(x).decode() for x in OSC52_RE.findall(out)], [want])
        after = self.frame(out)
        self.assertEqual((after[:-2], after[-2:]), (lines, ["", copied]))
        self.assertFalse(any(SELECT_ON in x for x in after))
        os.write(master, self.mouse(("press", end), ("drag", middle), ("drag", start), ("release", start)))  # 反着拖
        out = self.until_quiet(master, b"\x1b]52;")
        self.assertEqual([base64.b64decode(x).decode() for x in OSC52_RE.findall(out)], [want])
        wrapup, all_ = self.spot(lines, "orch-demo-perf", "[收尾]", 2), self.spot(lines, "[全部收尾", "[全部收尾", 2)
        title = lines[all_[1] - 1]
        want = "\n".join([title[title.index("[全部收尾") + 1:].rstrip(), *[x.rstrip() for x in lines[all_[1]:bottom]],
                          last[:last.index("[收尾]") + 2]])
        os.write(master, self.mouse(("press", wrapup), ("drag", middle), ("drag", all_), ("release", all_)))
        out = self.until_quiet(master, b"\x1b]52;")
        self.assertEqual([base64.b64decode(x).decode() for x in OSC52_RE.findall(out)], [want])
        self.wait_reading(master, lambda: False, timeout=1)
        self.assertEqual((self.send_calls(root), self.focused()), ([], []))
        self.click(master, lines, "[收尾]")
        self.wait_reading(master, lambda: self.send_calls(root) != [])
        self.assertEqual([c[-1] for c in self.send_calls(root)], ["收尾：orch-demo-perf"])
        p.send_signal(signal.SIGINT)
        self.read_until_exit(p, master)
        self.assertEqual(p.returncode, 0)
        self.assert_restored(slave, before)

    def test_no_refresh_while_dragging(self) -> None:
        """--interval 0.3（平时一秒刷新好几轮）：按下拖开以后不再刷新、不重读（1.5 秒里没有新输出、没有新的 herdr 调用、心跳不动），
        松开以后复制、接着刷新；在空白处拖（一个字都没盖到）不复制、不显示"已复制"。鼠标上报带 1002（拖动），退出时一起关"""
        p, master, slave, before = self.start_pty("w1:p2", "--interval", "0.3")
        lines = self.until_frame(master, lambda f: any("进行中（1）" in x for x in f))
        self.assertEqual(lines[1], "")
        os.write(master, self.mouse(("press", (3, 2)), ("drag", (30, 2)), ("release", (30, 2))))  # 第二行是空行
        out = self.wait_reading(master, lambda: False, timeout=1.5)
        self.assertNotIn(b"\x1b]52;", out)
        self.assertFalse(any("已复制" in x for x in self.frame(out)), self.frame(out))
        os.write(master, self.mouse(("press", (2, 1)), ("drag", (10, 3))))
        self.until_quiet(master, SELECT_ON.encode())
        calls, beat = len(self.herdr_calls()), (self.state / "board-live.json").read_text()
        self.assertEqual(self.wait_reading(master, lambda: False, timeout=1.5), b"")
        self.assertEqual((len(self.herdr_calls()), (self.state / "board-live.json").read_text()), (calls, beat))
        os.write(master, self.mouse(("release", (10, 3))))
        self.assertIn(b"\x1b]52;c;", self.read_until(master, "已复制 3 行".encode()))
        self.wait_reading(master, lambda: len(self.herdr_calls()) > calls)
        self.assertGreater(len(self.herdr_calls()), calls)
        p.send_signal(signal.SIGINT)
        out = self.read_until_exit(p, master)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(out.endswith(MOUSE_OFF), out[-100:])
        self.assert_restored(slave, before)


class BoardNoteClickTests(PtyTestCase):
    """跨列的提醒行在看板 tab 里：多出来的跨列行不影响点 [收尾] 和拖选。用伪终端跑真的 bin/drover。"""

    def test_clicks_and_drag_across_note(self) -> None:
        """完成表里 t-note 带提醒、t-next 在它下面（160 列，不用滚）：点提醒上面那条线、提醒那一行（[收尾] 那一列、提醒的字上）
        不送；从 t-note 的任务名拖到 t-next 的任务名（跨过那条线和提醒那一行）松开 → 复制的正好是画出来的这几行（那条线、提醒
        那一行整行，行尾空格去掉），不送；点 t-next 的 [收尾] 送"收尾：t-next"，那一格变"收尾中"；再点 t-note 的 [收尾] 送
        "收尾：t-note\""""
        self.setup_tab()
        self.task("t-note", "orch-note", "完成", title="带提醒的任务", summary="做完了", ask="无", wrapup="可以", note=NOTE_PERF,
                  age=2 * HOUR, read=True)
        self.task("t-next", "orch-next", "完成", title="下面那个任务", summary="也做完了", ask="无", wrapup="可以", age=HOUR,
                  read=True)
        self.set_agents(**{"orch-note": {"status": "idle", "screen": NORMAL}, "orch-next": {"status": "idle", "screen": NORMAL}})
        root = self.fake_plugin()
        p, master, slave, before = self.start_pty("w1:p2", "--interval", "60", setup=False, cols=160)
        lines = self.until_frame(master, lambda f: any("[全部收尾（2 个）]" in x for x in f))
        note = next(i for i, x in enumerate(lines) if f"提醒：{NOTE_PERF}" in x)
        self.assertIn("带提醒的任务", lines[note - 2])
        self.assertTrue(lines[note - 1].startswith("│") and "┴" in lines[note - 1], lines[note - 1])
        self.assertIn("下面那个任务", lines[note + 2])
        x = text_cols(lines[note - 2][:lines[note - 2].index("[收尾]")]) + 2
        for y in (note - 1, note):  # 那条线、提醒那一行，[收尾] 那一列
            os.write(master, f"\x1b[<0;{x};{y + 1}M\x1b[<0;{x};{y + 1}m".encode())
        self.click(master, lines, f"提醒：{NOTE_PERF}")
        self.wait_reading(master, lambda: False, timeout=1.5)
        self.assertEqual(self.send_calls(root), [])
        start = BoardSelectTests.spot(lines, "带提醒的任务", "带提醒的任务", 1)
        end = BoardSelectTests.spot(lines, "下面那个任务", "下面那个任务", 2)
        first, last = lines[note - 2], lines[note + 2]
        want = "\n".join([first[first.index("带提醒的任务"):].rstrip(), lines[note - 1], lines[note].rstrip(), lines[note + 1],
                          last[:last.index("下面那个任务") + 1]])
        os.write(master, BoardSelectTests.mouse(("press", start), ("drag", end), ("release", end)))
        out = BoardSelectTests.until_quiet(master, "已复制 5 行".encode())
        self.assertEqual([base64.b64decode(x).decode() for x in OSC52_RE.findall(out)], [want])
        self.assertEqual(self.send_calls(root), [])
        self.click(master, lines, "[收尾]", nth=1)
        lines = self.until_frame(master, lambda f: any("下面那个任务" in x and x.endswith("│ 收尾中 │") for x in f))
        self.assertEqual([c[-1] for c in self.send_calls(root)], ["收尾：t-next"])
        self.click(master, lines, "[收尾]")
        self.wait_reading(master, lambda: len(self.send_calls(root)) > 1)
        self.assertEqual([c[-1] for c in self.send_calls(root)], ["收尾：t-next", "收尾：t-note"])
        p.send_signal(signal.SIGINT)
        self.read_until_exit(p, master)
        self.assertEqual(p.returncode, 0)
        self.assert_restored(slave, before)


def text_cols(text: str) -> int:
    """显示宽度（中文两格），测试自己算，不用 drover 的。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


class RollupCommandTests(BoardTestCase):
    HEAD_RE = r"^# 和上次读（\d\d-\d\d \d\d:\d\d）相比："

    def write(self, slug: str, body: str) -> None:
        path = self.state / slug / "rollup.md"
        path.write_text(body)
        t = time.time() + 2
        os.utime(path, (t, t))

    def test_first_same_changed(self) -> None:
        """主会话第一次读 → "# 第一次读" + 空行 + 原文，写 main-seen.json（digest、status、规范化后的 lines、read_at、rollup_mtime）；
        再读、只改空白空行 → 内容没变；改一行加一行 → 改了 2 行并列出 - / +；改动超过 15 行只列 15 行"""
        self.task("t-a", "orch-a", "等你决定", sections={"需要你决定": ["- 选 A 还是 B"]})
        text = (self.state / "t-a" / "rollup.md").read_text()
        out = self.main_rollup("--slug", "t-a").stdout
        self.assertEqual(out, "# 第一次读\n\n" + text + ("" if text.endswith("\n") else "\n"))
        seen = json.loads((self.state / "t-a" / "main-seen.json").read_text())
        self.assertEqual(set(seen), {"digest", "status", "lines", "read_at", "rollup_mtime"})
        self.assertEqual((seen["status"], seen["lines"]), ("等你决定", ["# t-a", "状态：等你决定", "## 需要你决定", "- 选 A 还是 B"]))
        self.assertRegex(self.main_rollup("--slug", "t-a").stdout.splitlines()[0], self.HEAD_RE + "内容没变$")
        self.write("t-a", "\n# t-a\n  状态：等你决定  \n\n\n## 需要你决定\n- 选 A 还是 B\n\n")
        self.assertRegex(self.main_rollup("--slug", "t-a").stdout.splitlines()[0], self.HEAD_RE + "内容没变$")
        self.write("t-a", "# t-a\n状态：等你决定\n## 需要你决定\n- 选 A 还是 C\n- 新问题\n")
        lines = self.main_rollup("--slug", "t-a").stdout.splitlines()
        self.assertRegex(lines[0], self.HEAD_RE + "改了 2 行$")
        self.assertEqual(lines[1:5], ["- - 选 A 还是 B", "+ - 选 A 还是 C", "+ - 新问题", ""])
        self.write("t-a", "# t-a\n状态：进行中\n" + "\n".join(f"- 第 {i} 步" for i in range(20)) + "\n")
        lines = self.main_rollup("--slug", "t-a").stdout.splitlines()
        self.assertRegex(lines[0], self.HEAD_RE + r"改了 \d+ 行$")
        self.assertTrue(all(x[:2] in ("- ", "+ ") for x in lines[1:16]))
        self.assertRegex(lines[16], r"^…还有 \d+ 行没列$")
        self.assertEqual(json.loads((self.state / "t-a" / "main-seen.json").read_text())["status"], "进行中")

    def test_missing_rollup(self) -> None:
        """没有 rollup.md → 报错（JSON），退出码 1，主会话读也不写 main-seen.json；--slug / --name 必须二选一"""
        self.task("t-a", "orch-a")
        out = json.loads(self.main_rollup("--slug", "t-a", rc=1).stdout)
        self.assertIn("没有 rollup", out["error"])
        self.assertFalse((self.state / "t-a" / "main-seen.json").exists())
        self.drover("rollup", rc=2)
        self.drover("rollup", "--slug", "t-a", "--name", "orch-a", rc=2)

    def test_by_name(self) -> None:
        """--name：按 orch.json 的 name 找任务（watch --main 的 EVENT 行带的是编排者名）；找不到报错、退出码 1；
        同名多份 orch.json 取修改时间最新的那份"""
        self.task("t-old", "orch-x", "完成", sections={"结论": ["- 旧任务"]})
        hour_ago = time.time() - HOUR
        os.utime(self.state / "t-old" / "orch.json", (hour_ago, hour_ago))
        self.task("t-new", "orch-x", "等你决定", sections={"需要你决定": ["- 新任务的问题"]})
        out = self.main_rollup("--name", "orch-x").stdout
        self.assertTrue(out.startswith("# 第一次读\n\n# t-new\n"), out)
        self.assertTrue((self.state / "t-new" / "main-seen.json").exists())
        self.assertFalse((self.state / "t-old" / "main-seen.json").exists())
        missing = self.drover_json("rollup", "--name", "orch-nobody", rc=1)
        self.assertIn("找不到编排者 orch-nobody", missing["error"])

    # ---- 只有主会话读才记已读（编排者用 drover rollup 看别的任务，不能记成主会话已读）

    NOTE = "# 不是主会话（{}），没记已读；下面是和主会话上次读的比"

    def other_rollup(self, *args: str, pane: Optional[str] = "w2:p3") -> list:
        """别的调用方跑 drover rollup，返回输出的行：pane 是它所在的 herdr pane（默认一个编排者的），None 是不在 herdr 里的普通终端"""
        return self.drover("rollup", *args, env={"HERDR_ENV": "1", "HERDR_PANE_ID": pane} if pane else {}).stdout.splitlines()

    def main_reads(self, slug: str) -> list:
        path = self.state / slug / "timeline.jsonl"
        events = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
        return [e for e in events if e["event"] == "main-read"]

    def register_main(self, record: dict) -> None:
        (self.state / "main-session.json").write_text(json.dumps({"at": time.time(), **record}))

    def test_other_pane_not_recorded(self) -> None:
        """别的 pane（编排者）读：开头多一行"不是主会话（这个 pane 是…，登记的主会话在…），没记已读"，其余和主会话这时读到的
        一字不差；main-seen.json、时间线的 main-read 都不写，看板照样算未读。主会话（Claude Code 的 hook 写的记录）再读 → 记已读；
        改了之后编排者再读，main-seen.json 一个字节都不变、看板又算未读，主会话读了才不算。认主会话不调 herdr（herdr-calls.log
        不多一条），herdr 连不上也一样"""
        self.task("t-a", "orch-a", "等你决定", sections={"需要你决定": ["- 选 A 还是 B"]})
        self.set_agents(**{"orch-a": {"status": "done", "screen": NORMAL}})
        self.register_main({"session_id": "s1", "pane_id": "w1:p1", "source": "startup"})
        seen = self.state / "t-a" / "main-seen.json"
        note = self.NOTE.format("这个 pane 是 w2:p3，登记的主会话在 w1:p1")
        calls = len(self.herdr_calls())
        other = self.other_rollup("--slug", "t-a")
        self.assertEqual((other[0], seen.exists(), self.main_reads("t-a")), (note, False, []))
        main = self.main_rollup("--slug", "t-a").stdout.splitlines()
        self.assertEqual(len(self.herdr_calls()), calls)
        self.assertEqual((main[0], other[1:]), ("# 第一次读", main))
        self.assertEqual(len(self.main_reads("t-a")), 1)
        self.assertFalse(self.tasks_json()["t-a"]["unread"])

        self.write("t-a", "# t-a\n状态：等你决定\n## 需要你决定\n- 选 A 还是 C\n")
        before = seen.read_bytes()
        other = self.other_rollup("--slug", "t-a")
        self.assertEqual((other[0], seen.read_bytes(), len(self.main_reads("t-a"))), (note, before, 1))
        self.assertTrue(self.tasks_json()["t-a"]["unread"])
        main = self.main_rollup("--slug", "t-a").stdout.splitlines()
        self.assertRegex(main[0], self.HEAD_RE + "改了 1 行$")
        self.assertEqual(other[1:], main)
        self.assertNotEqual(seen.read_bytes(), before)
        self.assertFalse(self.tasks_json()["t-a"]["unread"])

    def test_unrecognized_main_not_recorded(self) -> None:
        """认不出主会话一律不记已读，开头写原因：不在 herdr 的 pane 里（普通终端）、没有 main-session.json、记录里没有 pane_id；
        Codex 主会话的记录（session_id、source 是 null）照样认"""
        self.task("t-a", "orch-a", "等你决定", sections={"需要你决定": ["- 选 A 还是 B"]})
        seen = self.state / "t-a" / "main-seen.json"
        cases = ((None, None, "不在 herdr 的 pane 里"),
                 ("w1:p1", None, f"还没有登记过主会话（{self.state / 'main-session.json'} 里没有 pane_id）"),
                 ("w1:p1", {"session_id": None}, f"还没有登记过主会话（{self.state / 'main-session.json'} 里没有 pane_id）"))
        for pane, record, why in cases:
            with self.subTest(pane=pane, record=record):
                if record is not None:
                    self.register_main(record)
                lines = self.other_rollup("--slug", "t-a", pane=pane)
                self.assertEqual((lines[0], lines[1]), (self.NOTE.format(why), "# 第一次读"))
                self.assertEqual((seen.exists(), self.main_reads("t-a")), (False, []))
        self.register_main({"session_id": None, "pane_id": "w1:p1", "source": None})
        self.assertEqual(self.other_rollup("--slug", "t-a", pane="w1:p1")[0], "# 第一次读")
        self.assertTrue(seen.exists())


class PromptAnsweredTests(BoardTestCase):
    def fake_prompt(self, d, reply) -> None:
        d.herdr = lambda args, timeout=None: (reply if args[:2] == ["agent", "prompt"] else
                                              (True, {"agent": {"agent_status": "working"}}))

    def prompt(self, d, name: str) -> dict:
        return d.cmd_prompt(argparse.Namespace(name=name, text="答复：选 A", file=None, timeout=1000, dry_run=False))

    def test_prompt_writes_answered(self) -> None:
        """发给编排者成功 → 它（同名取最新那份 orch.json）的任务目录写 answered.json：at、发送时 rollup 的 digest；
        发给 worker（没有 orch.json）不写；发送失败（卡在确认框上）不写"""
        self.task("t-old", "orch-a", "完成")
        hour_ago = time.time() - HOUR
        os.utime(self.state / "t-old" / "orch.json", (hour_ago, hour_ago))
        self.task("t-a", "orch-a", "等你决定", sections={"需要你决定": ["- 选 A 还是 B"]})
        d = self.load_drover()
        self.fake_prompt(d, (False, {"code": "agent_blocked", "message": "blocked"}))
        self.assertFalse(self.prompt(d, "orch-a")["ok"])
        self.assertEqual(list(self.state.glob("*/answered.json")), [])
        self.fake_prompt(d, (True, {"agent": {"agent_status": "working"}}))
        before = time.time()
        self.assertTrue(self.prompt(d, "orch-a")["ok"])
        answered = json.loads((self.state / "t-a" / "answered.json").read_text())
        self.assertEqual(set(answered), {"at", "rollup_digest"})
        self.assertGreaterEqual(answered["at"], before)
        self.assertEqual(answered["rollup_digest"], d.read_rollup(self.state / "t-a" / "rollup.md")["digest"])
        self.assertTrue(self.prompt(d, "w1")["ok"])
        self.assertEqual([p.parent.name for p in self.state.glob("*/answered.json")], ["t-a"])

    def test_prompt_warns_unread_rollup(self) -> None:
        """发给顶层编排者之前算一次未读：rollup 在主会话上次读之后又改过（要处理的状态）→ 照发，返回里多一句 warning（先读新版本、
        确认答复对的是哪一版）；读过的这一版不提醒；子编排者（主会话不读它的 rollup，上层编排者给它发）、worker 不提醒"""
        self.task("t-a", "orch-a", "等你决定", sections={"需要你决定": ["- 选 A 还是 B"]}, read=True)
        self.task("t-a-shop", "orch-a-shop", "等你决定", parent="t-a")
        d = self.load_drover()
        self.fake_prompt(d, (True, {"agent": {"agent_status": "working"}}))
        self.assertNotIn("warning", self.prompt(d, "orch-a"))
        path = self.state / "t-a" / "rollup.md"
        path.write_text(path.read_text() + "- 又多了一个问题：C 要不要\n")
        out = self.prompt(d, "orch-a")
        self.assertTrue(out["ok"])
        self.assertIn("rollup 在你上次读之后改过", out["warning"])
        self.assertIn("drover rollup --slug t-a", out["warning"])
        self.assertNotIn("warning", self.prompt(d, "orch-a-shop"))
        self.assertNotIn("warning", self.prompt(d, "w1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
