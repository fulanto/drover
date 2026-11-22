"""watch --main：主会话专用的追踪，只报主会话要处理的事，报过的记在磁盘上。

背景：主会话两天里挂了 42 次 Monitor、收到 146 条通知，35 条是 30 分钟到期、34 条是重挂时 watch 的启动快照
（去重状态只在进程内存里，第一轮必定把当前值全打一遍）；working / waiting-background 对主会话也没有对应的动作。
--main：不写名字时每轮自动找顶层编排者，只报 未读的 rollup:<要处理的状态>、blocked（同一个弹框只报一次）、api-error、
意外 gone、stalled(Nm)；报过的记在 <slug>/main-watch.json，rollup 已读看 main-seen.json（drover rollup 写），
所以重挂之后只报没报过的，没有新东西时启动不输出任何一行。--until-event 时启动那一轮的输出也算事件。
用例里的 watch --main 在进程里跑、用假时钟（selftest_support 的 watch）：--timeout、--idle-minutes 按假时间，不用真等；
启动不输出、参数用法两条用例照旧起子进程。
"""

from __future__ import annotations

import json
import os
import time
import unittest

from selftest_screens import BASH_DIALOG, SUBAGENT_DIALOG, bash_dialog
from selftest_support import DroverTestCase, events

FAST = ["--interval", "0.2"]
NORMAL = """⏺ rollup 已更新，等主会话转达。

✻ Cooked for 3m · done 1:05 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on · ← 1 agent"""
# 写完 rollup 还挂着 Monitor 等 worker 的编排者：herdr 报 done，屏幕修正成 waiting-background(1)
MONITOR = """⏺ 两个 worker 都在干活，等 monitor 的事件。

✻ Baked for 11m 22s · done 12:44 AM · 1 monitor still running
────────────────
❯
────────────────
  ⏵⏵ accept edits on · 1 monitor · ← 1 agent"""
API_ERROR = """⏺ API Error: 503 No available accounts: no available accounts. This is a server-side issue.

✻ Brewed for 34m 26s · done 1:16 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on (shift+tab to cycle) · ← 1 agent"""


class WatchMainTests(DroverTestCase):
    def orch(self, slug: str, name: str, parent: str = None) -> None:
        d = self.state / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "orch.json").write_text(json.dumps(
            {"slug": slug, "name": name, "kind": "claude", "parent": parent, "workspace_id": "w1", "tab_id": "w1:t1",
             "pane_id": "w1:p1", "started": "11-18 09:00"}, ensure_ascii=False))

    def rollup(self, slug: str, status: str, body: str = "- 一个 worker 在干活", later: float = 0) -> None:
        """整份覆盖 rollup.md（临时文件再改名）；later 秒把修改时间往后拨（文件系统时间精度再粗也能看出改过）。"""
        path = self.state / slug / "rollup.md"
        tmp = path.with_name("rollup.md.tmp")
        tmp.write_text(f"# {slug}\n状态：{status}\n\n## 当前进度\n{body}\n")
        os.replace(tmp, path)
        if later:
            t = time.time() + later
            os.utime(path, (t, t))

    def main_watch(self, *extra: str, timeout: str = "2") -> list:
        """watch --main --until-event，返回 EVENT 列表（去掉时间）；退出码必须是 0。self.watch 在进程里跑、假时钟：
        --timeout、--idle-minutes 是假时间，"跑满 timeout 没有多报"不用真等（每次调用和起一个新进程一样，状态文件照常落盘）。"""
        rc, evs, _ = self.watch("--main", "--until-event", *FAST, "--timeout", timeout, *extra)
        self.assertEqual(rc, 0, evs)
        return evs

    def main_state(self, slug: str) -> dict:
        path = self.state / slug / "main-watch.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def started(self, slug: str) -> None:
        """这个任务是 orch-start 派发的（时间线里有派发那条）：装上已读记录之前的老任务没有时间线。"""
        with open(self.state / slug / "timeline.jsonl", "a") as fh:
            fh.write(json.dumps({"at": time.time() - 3600, "logged": time.time(), "event": "start", "source": "orch-start"})
                     + "\n")

    def test_unread_done_after_exit_reported(self) -> None:
        """编排者写完"完成"就退出、主会话还没读（orch-start 派发的、有时间线）→ 报一次 rollup:完成（不报 gone），重挂不再报；
        主会话读过这一版才算收尾完关掉的历史任务，什么都不报；读过之后完成又改了 → 照报。装上已读记录之前的老任务（已退出 + 完成、
        没读过、没有时间线）算已读：不报、不写 main-watch.json"""
        self.orch("task-d", "orch-d")
        self.started("task-d")
        self.rollup("task-d", "完成", "- 推送了")
        self.orch("task-old", "orch-old")
        self.rollup("task-old", "完成", "- 很久以前做完的")
        self.set_agents()
        self.assertEqual(self.main_watch(), ["EVENT orch-d rollup:完成"])
        self.assertEqual(self.main_watch(), ["EVENT timeout"])
        self.main_rollup("--slug", "task-d")
        self.assertEqual(self.main_watch(), ["EVENT timeout"])
        self.rollup("task-d", "完成", "- 推送了\n- 补了一句上线步骤", later=2)
        self.assertEqual(self.main_watch(), ["EVENT orch-d rollup:完成"])
        self.assertFalse((self.state / "task-old" / "main-watch.json").exists())

    def test_unread_and_dialog_repeat_after_10_minutes(self) -> None:
        """报过却没处理：rollup 还是未读、同一个弹框还在，满 10 分钟再报一次（30 秒一轮跑 700 秒假时间：0 秒、600 秒各报一次）；
        旧版本写的 main-watch.json（只有 digest / 指纹、没有报出的时间）从现在起算、不立刻重报，记下的时间满 10 分钟了才报；
        处理了（读了、弹框没了）就不再报"""
        self.orch("task-a", "orch-a")
        self.rollup("task-a", "等你决定", "- 选 A 还是 B")
        self.orch("task-b", "orch-b")
        self.rollup("task-b", "进行中")
        self.set_agents(**{"orch-a": {"status": "done", "screen": NORMAL},
                           "orch-b": {"status": "blocked", "screen": BASH_DIALOG}})
        rc, evs, _ = self.watch("--main", "--interval", "30", "--timeout", "700")
        self.assertEqual((rc, evs), (0, ["EVENT orch-a rollup:等你决定", "EVENT orch-b blocked",
                                         "EVENT orch-a rollup:等你决定", "EVENT orch-b blocked", "EVENT timeout"]))
        keys = {"task-a": "rollup_emitted", "task-b": "blocked_fp"}
        for slug, key in keys.items():
            (self.state / slug / "main-watch.json").write_text(json.dumps({key: self.main_state(slug)[key]}))
        self.assertEqual(self.main_watch(), ["EVENT timeout"])
        for slug in keys:
            state = self.main_state(slug)
            for k in [k for k in state if k.endswith("_at")]:
                state[k] -= 601
            (self.state / slug / "main-watch.json").write_text(json.dumps(state))
        self.assertEqual(self.main_watch(), ["EVENT orch-a rollup:等你决定", "EVENT orch-b blocked"])
        self.main_rollup("--slug", "task-a")
        self.set_agents(**{"orch-a": {"status": "done", "screen": NORMAL}, "orch-b": {"status": "working", "screen": NORMAL}})
        rc, evs, _ = self.watch("--main", "--interval", "30", "--timeout", "700")
        self.assertEqual((rc, evs), (0, ["EVENT timeout"]))

    def test_stalled_same_rule_as_board(self) -> None:
        """停了没更新和看板、时间线同一套判定：显示状态是进行中（没写 rollup、状态写成别的词的也算）或已转答复，编排者 done / idle /
        unknown 满 --idle-minutes（默认 10）报 stalled；状态栏挂着后台命令的，过了 Monitor 的 30 分钟上限还挂着，再停满
        --idle-minutes 也报（第 40 分钟）；接口错误被后台命令盖住时照样报 api-error（接口错误排在后台命令前面）"""
        self.orch("task-n", "orch-n")  # 没写 rollup 就停了
        self.orch("task-o", "orch-o")
        self.rollup("task-o", "已完成")  # 状态写成别的词
        self.orch("task-u", "orch-u")
        self.rollup("task-u", "进行中")
        self.orch("task-g", "orch-g")
        self.rollup("task-g", "进行中")
        self.orch("task-e", "orch-e")
        self.rollup("task-e", "进行中")
        self.set_agents(**{"orch-n": {"status": "done", "screen": NORMAL}, "orch-o": {"status": "idle", "screen": NORMAL},
                           "orch-u": {"status": "unknown", "screen": NORMAL}, "orch-g": {"status": "done", "screen": MONITOR},
                           "orch-e": {"status": "done", "screen": API_ERROR.replace("· ← 1 agent", "· 1 monitor · ← 1 agent")}})
        rc, evs, _ = self.watch("--main", "--interval", "60", "--timeout", str(45 * 60))
        self.assertEqual((rc, evs), (0, ["EVENT orch-e api-error", "EVENT orch-n stalled(10m)", "EVENT orch-o stalled(10m)",
                                         "EVENT orch-u stalled(10m)", "EVENT orch-g stalled(10m)", "EVENT timeout"]))

    def test_gone_again_after_replacement(self) -> None:
        """编排者意外退出报 gone；同名的编排者换上来（活着）之后又退出 → 再报一次 gone"""
        self.orch("task-g", "orch-g")
        self.rollup("task-g", "进行中")
        self.set_agents()
        self.assertEqual(self.main_watch(), ["EVENT orch-g gone"])
        self.set_agents(**{"orch-g": {"status": "working", "screen": NORMAL}})
        self.assertEqual(self.main_watch(timeout="1"), ["EVENT timeout"])
        self.set_agents()
        self.assertEqual(self.main_watch(), ["EVENT orch-g gone"])

    def test_answered_by_content(self) -> None:
        """转过答复之后 rollup 没变，按内容判断（answered.json 里的 digest），不按修改时间：编排者把 rollup 一字不改重写一遍再停下
        → 还是已转答复，停满报 stalled；内容改了 → 不算转过答复（等你决定已读，不报）"""
        self.orch("task-q", "orch-q")
        self.rollup("task-q", "等你决定", "- 选 A 还是 B")
        self.main_rollup("--slug", "task-q")
        digest = json.loads((self.state / "task-q" / "main-seen.json").read_text())["digest"]
        (self.state / "task-q" / "answered.json").write_text(json.dumps({"at": time.time() - 60, "rollup_digest": digest}))
        self.rollup("task-q", "等你决定", "- 选 A 还是 B", later=2)  # 原样重写，修改时间在转答复之后
        self.set_agents(**{"orch-q": {"status": "done", "screen": NORMAL}})
        rc, evs, _ = self.watch("--main", "--until-event", *FAST, "--timeout", "20", "--idle-minutes", "0.02")
        self.assertEqual((rc, evs), (0, ["EVENT orch-q stalled(0m)"]))
        self.rollup("task-q", "等你决定", "- 选 A 还是 B\n- 又多了一个问题", later=4)
        self.main_rollup("--slug", "task-q")
        self.assertEqual(self.main_watch("--idle-minutes", "0.02", timeout="5"), ["EVENT timeout"])

    def test_heartbeat_each_round(self) -> None:
        """watch --main 每一轮写心跳 ~/.drover/main-watch-live.json（pid、at）：看板靠它发现主会话的追踪断了"""
        rc, evs, secs = self.watch("--main", *FAST, "--timeout", "1")
        beat = json.loads((self.state / "main-watch-live.json").read_text())
        self.assertEqual(set(beat), {"pid", "at"})
        self.assertEqual(beat["pid"], os.getpid())  # 进程里跑
        self.assertLessEqual(time.time() - 5, beat["at"])
        self.assertGreater(secs, 0)

    def test_quiet_start_prints_nothing(self) -> None:
        """没有新东西：working + 进行中、挂着 Monitor 的 done + 进行中、已读的等你决定 → 不带 --until-event 跑到 timeout，
        整个输出只有 EVENT timeout 一行（启动不再输出当前状态）"""
        self.orch("task-a", "orch-a")
        self.rollup("task-a", "进行中")
        self.orch("task-b", "orch-b")
        self.rollup("task-b", "进行中")
        self.orch("task-c", "orch-c")
        self.rollup("task-c", "等你决定", "- 选 A 还是 B")
        self.main_rollup("--slug", "task-c")
        self.set_agents(**{"orch-a": {"status": "working"}, "orch-b": {"status": "done", "screen": MONITOR},
                           "orch-c": {"status": "done", "screen": NORMAL}})
        p = self.drover("watch", "--main", *FAST, "--timeout", "1.5")
        self.assertEqual(events(p.stdout), ["EVENT timeout"])
        self.assertEqual(len(p.stdout.splitlines()), 1, p.stdout)

    def test_unread_decision_once_across_rehang(self) -> None:
        """未读的等你决定、未读的完成各报一次，--until-event 第一轮有输出就退出；重挂不再报；内容变了再报；
        drover rollup 读过之后（没报过的新内容）也不报"""
        self.orch("task-a", "orch-a")
        self.rollup("task-a", "等你决定", "- 选 A 还是 B")
        self.orch("task-d", "orch-d")
        self.rollup("task-d", "完成（已收尾）", "- 推送了")
        self.set_agents(**{"orch-a": {"status": "done", "screen": NORMAL}, "orch-d": {"status": "idle", "screen": NORMAL}})
        rc, evs, secs = self.watch("--main", "--until-event", *FAST, "--timeout", "20")
        self.assertEqual((rc, evs), (0, ["EVENT orch-a rollup:等你决定", "EVENT orch-d rollup:完成（已收尾）"]))
        self.assertEqual(secs, 0)  # 第一轮就退出，一次都没睡（不等 timeout）
        self.assertEqual(self.main_watch(), ["EVENT timeout"])
        self.rollup("task-a", "等你决定", "- 选 A 还是 B\n- 又多了一个问题：C 要不要", later=2)
        self.assertEqual(self.main_watch(), ["EVENT orch-a rollup:等你决定"])
        self.rollup("task-a", "等你决定", "- 第三版问题", later=4)
        self.main_rollup("--slug", "task-a")
        self.main_rollup("--slug", "task-d")
        self.assertEqual(self.main_watch(), ["EVENT timeout"])

    def test_rewrite_and_whitespace_not_reported(self) -> None:
        """报过（或读过）之后：原样重写（修改时间变了）、只改空白和空行 → 不报；内容变了 → 报"""
        self.orch("task-a", "orch-a")
        self.rollup("task-a", "等你决定", "- 选 A 还是 B")
        self.set_agents(**{"orch-a": {"status": "done", "screen": NORMAL}})
        self.assertEqual(self.main_watch(), ["EVENT orch-a rollup:等你决定"])
        self.rollup("task-a", "等你决定", "- 选 A 还是 B", later=2)
        self.assertEqual(self.main_watch(), ["EVENT timeout"])
        path = self.state / "task-a" / "rollup.md"
        path.write_text("\n\n" + path.read_text().replace("- 选 A 还是 B", "   - 选 A 还是 B   \n\n") + "\n\n")
        self.assertEqual(self.main_watch(), ["EVENT timeout"])
        self.rollup("task-a", "等你决定", "- 选 A 还是 B 还是 C", later=4)
        self.assertEqual(self.main_watch(), ["EVENT orch-a rollup:等你决定"])

    def test_read_by_others_still_reported(self) -> None:
        """只有主会话读才算已读（出过编排者用 drover rollup 看别的任务，刚写的"完成"被记成已读；读在 watch --main 这一轮
        之前的话就不报了）：编排者的 pane、普通终端先读了 → 照报；主会话读过改过的那版 → 不报；再改、编排者读 → 照报"""
        self.orch("task-a", "orch-a")
        self.rollup("task-a", "完成", "- 推送了")
        self.set_agents(**{"orch-a": {"status": "done", "screen": NORMAL}})
        (self.state / "main-session.json").write_text(json.dumps({"session_id": "s1", "pane_id": "w1:p1", "source": "startup"}))
        orch_pane = {"HERDR_ENV": "1", "HERDR_PANE_ID": "w2:p3"}
        self.drover("rollup", "--slug", "task-a", env=orch_pane)
        self.drover("rollup", "--slug", "task-a")  # 不在 herdr 里的普通终端
        self.assertEqual(self.main_watch(), ["EVENT orch-a rollup:完成"])
        self.rollup("task-a", "完成", "- 推送了\n- 补了文档", later=2)
        self.main_rollup("--slug", "task-a")
        self.assertEqual(self.main_watch(), ["EVENT timeout"])
        self.rollup("task-a", "等你决定", "- 要不要发版", later=4)
        self.drover("rollup", "--slug", "task-a", env=orch_pane)
        self.assertEqual(self.main_watch(), ["EVENT orch-a rollup:等你决定"])

    def test_working_background_in_progress_not_reported(self) -> None:
        """working、waiting-background（done + 挂着 Monitor，还没过 Monitor 的 30 分钟上限）、rollup:进行中 都不报"""
        self.orch("task-w", "orch-w")
        self.rollup("task-w", "进行中")
        self.orch("task-m", "orch-m")
        self.rollup("task-m", "进行中")
        self.set_agents(**{"orch-w": {"status": "working", "screen": NORMAL},
                           "orch-m": {"status": "done", "screen": MONITOR}})
        self.assertEqual(self.main_watch("--idle-minutes", "0"), ["EVENT timeout"])
        self.assertNotIn("stalled_since", self.main_state("task-m"))

    def test_same_dialog_once_new_dialog_again(self) -> None:
        """herdr 报 blocked：同一个弹框跨重挂只报一次；换了弹框再报；弹框没了清掉记录，再弹（同一个）又报。
        herdr 报 working、屏幕底部是后台子 agent 的确认框 → 同样报 blocked"""
        self.orch("task-b", "orch-b")
        self.rollup("task-b", "进行中")
        self.set_agents(**{"orch-b": {"status": "blocked", "screen": BASH_DIALOG}})
        self.assertEqual(self.main_watch(), ["EVENT orch-b blocked"])
        self.assertEqual(self.main_watch(), ["EVENT timeout"])
        self.set_agents(**{"orch-b": {"status": "blocked", "screen": bash_dialog("b")}})
        self.assertEqual(self.main_watch(), ["EVENT orch-b blocked"])
        self.set_agents(**{"orch-b": {"status": "done", "screen": NORMAL}})
        self.assertEqual(self.main_watch(timeout="1"), ["EVENT timeout"])
        self.assertNotIn("blocked_fp", self.main_state("task-b"))
        self.set_agents(**{"orch-b": {"status": "blocked", "screen": bash_dialog("b")}})
        self.assertEqual(self.main_watch(), ["EVENT orch-b blocked"])
        self.set_agents(**{"orch-b": {"status": "working", "agent": "claude", "screen": SUBAGENT_DIALOG}})
        self.assertEqual(self.main_watch(), ["EVENT orch-b blocked"])
        self.assertEqual(self.main_watch(), ["EVENT timeout"])

    def test_api_error_once(self) -> None:
        """停在接口错误上：报一次 api-error（不算停下没更新），重挂不再报；恢复后清掉记录，再出错再报"""
        self.orch("task-e", "orch-e")
        self.rollup("task-e", "进行中")
        self.set_agents(**{"orch-e": {"status": "done", "screen": API_ERROR}})
        self.assertEqual(self.main_watch("--idle-minutes", "0"), ["EVENT orch-e api-error"])
        self.assertEqual(self.main_watch("--idle-minutes", "0"), ["EVENT timeout"])
        self.set_agents(**{"orch-e": {"status": "working", "screen": NORMAL}})
        self.assertEqual(self.main_watch(timeout="1"), ["EVENT timeout"])
        self.assertNotIn("api_error", self.main_state("task-e"))
        self.set_agents(**{"orch-e": {"status": "done", "screen": API_ERROR}})
        self.assertEqual(self.main_watch(), ["EVENT orch-e api-error"])

    def test_unexpected_gone_once(self) -> None:
        """编排者不在 herdr 里：rollup 进行中、没有 rollup → 报 gone 一次（重挂不再报）；装上已读记录之前的老任务（rollup 完成、
        没有时间线）什么都不报；需要换编排者的不报 gone、按未读的 rollup 报"""
        self.orch("task-f", "orch-f")
        self.rollup("task-f", "完成")
        self.orch("task-g", "orch-g")
        self.rollup("task-g", "进行中")
        self.orch("task-n", "orch-n")  # 只有 orch.json，编排者没写 rollup 就退出了
        self.orch("task-r", "orch-r")
        self.rollup("task-r", "需要换编排者", "- 上下文快满了")
        self.set_agents()
        self.assertEqual(self.main_watch(),
                         ["EVENT orch-g gone", "EVENT orch-n gone", "EVENT orch-r rollup:需要换编排者"])
        self.assertEqual(self.main_watch(), ["EVENT timeout"])
        self.assertEqual(self.main_state("task-g")["gone_reported"], "orch-g")
        self.assertFalse((self.state / "task-f" / "main-watch.json").exists())

    def test_stalled_in_progress_once(self) -> None:
        """rollup 进行中、编排者 done（屏幕正常）：从第一次看到开始计时，满 --idle-minutes 报一次 stalled(Nm)，重挂不再报；
        变回 working 清掉计时，再停下重新计时、再报"""
        self.orch("task-s", "orch-s")
        self.rollup("task-s", "进行中")
        self.set_agents(**{"orch-s": {"status": "done", "screen": NORMAL}})
        rc, evs, secs = self.watch("--main", "--until-event", *FAST, "--timeout", "20", "--idle-minutes", "0.02")
        self.assertEqual((rc, evs), (0, ["EVENT orch-s stalled(0m)"]))
        self.assertGreaterEqual(secs, 1.2)  # 第一轮只开始计时，满 0.02 分钟（假时间）才报
        self.assertEqual(self.main_watch("--idle-minutes", "0.02"), ["EVENT timeout"])
        self.set_agents(**{"orch-s": {"status": "working", "screen": NORMAL}})
        self.assertEqual(self.main_watch(timeout="1"), ["EVENT timeout"])
        self.assertEqual(self.main_state("task-s"), {})
        self.set_agents(**{"orch-s": {"status": "idle", "screen": NORMAL}})
        self.assertEqual(self.main_watch("--idle-minutes", "0.02", timeout="20"), ["EVENT orch-s stalled(0m)"])

    def test_stalled_after_answer(self) -> None:
        """等你决定已读、转过答复（answered.json 的 digest 就是现在的内容）却没更新、编排者 done → 停满报 stalled；
        假时钟：编排者停下（agent-state.json）和转答复都是一小时前 → 第一轮就报 stalled(60m)。没转过答复的已读等你决定不报"""
        self.orch("task-q", "orch-q")
        self.rollup("task-q", "等你决定", "- 选 A 还是 B")
        self.main_rollup("--slug", "task-q")
        self.set_agents(**{"orch-q": {"status": "done", "screen": NORMAL}})
        self.assertEqual(self.main_watch("--idle-minutes", "0"), ["EVENT timeout"])
        digest = json.loads((self.state / "task-q" / "main-seen.json").read_text())["digest"]
        hour_ago = time.time() - 3600
        (self.state / "task-q" / "answered.json").write_text(json.dumps({"at": hour_ago, "rollup_digest": digest}))
        path = self.state / "task-q" / "agent-state.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "since": hour_ago}))
        self.assertEqual(self.main_watch(), ["EVENT orch-q stalled(60m)"])
        self.assertEqual(self.main_watch(), ["EVENT timeout"])

    def test_new_orch_followed_sub_orch_ignored(self) -> None:
        """watch 起来之后才写的 orch.json（每轮重新找）→ 跟上并报；子编排者（orch.json 有 parent）的未读等你决定不报；
        写了名字时只盯这些"""
        self.orch("task-p", "orch-p")
        self.rollup("task-p", "进行中")
        self.orch("task-p-shop", "orch-p-shop", parent="task-p")
        self.rollup("task-p-shop", "等你决定", "- 子编排者的问题")
        self.set_agents(**{"orch-p": {"status": "working"}, "orch-p-shop": {"status": "done", "screen": NORMAL},
                           "orch-new": {"status": "done", "screen": NORMAL}})

        def new_orch() -> None:
            self.orch("task-new", "orch-new")
            self.rollup("task-new", "等你决定", "- 新任务的问题")

        rc, evs, _ = self.watch("--main", "--until-event", *FAST, "--timeout", "20", on_sleep={5: new_orch})  # 跑了 5 轮才写
        self.assertEqual((rc, evs), (0, ["EVENT orch-new rollup:等你决定"]))
        self.assertFalse((self.state / "task-p-shop" / "main-watch.json").exists())
        self.orch("task-z", "orch-z")
        self.rollup("task-z", "受阻", "- 卡住了")
        self.set_agents(**{"orch-z": {"status": "done", "screen": NORMAL}, "orch-p": {"status": "working"},
                           "orch-new": {"status": "done", "screen": NORMAL}})
        self.assertEqual(self.main_watch("orch-p"), ["EVENT timeout"])
        self.assertEqual(self.main_watch(), ["EVENT orch-z rollup:受阻"])

    def test_usage(self) -> None:
        """不带 --main 必须写名字；--main 不支持 --once（argparse 报错，退出码 2）"""
        self.assertIn("只有 --main 时可以不写", self.drover("watch", rc=2).stderr)
        self.assertIn("不支持 --once", self.drover("watch", "--main", "--once", rc=2).stderr)

    def agent_states(self, slugs) -> dict:
        return {s: json.loads((self.state / s / "agent-state.json").read_text()) for s in slugs}

    def test_agent_state_same_as_board(self) -> None:
        """watch --main 每一轮把编排者现在的状态记进 agent-state.json（name、shown、since、seq，
        blocked 加 dialog_fp），和看板记的一致。各种状态（herdr 报 blocked、working 但屏幕是子 agent 的确认框、接口错误、
        挂着 Monitor、working、退出了）：watch 先记、看板再看 → 文件一字不变，看板的时长从 watch 记的时间算（不写"刚发现"）；
        删掉记录换看板先记、watch 再看 → 也一字不变，除了 since 两边记的一样"""
        cases = {"task-b": ("orch-b", {"status": "blocked", "seq": 3, "screen": BASH_DIALOG}),
                 "task-s": ("orch-s", {"status": "working", "agent": "claude", "seq": 4, "screen": SUBAGENT_DIALOG}),
                 "task-e": ("orch-e", {"status": "done", "seq": 5, "screen": API_ERROR}),
                 "task-m": ("orch-m", {"status": "done", "seq": 6, "screen": MONITOR}),
                 "task-w": ("orch-w", {"status": "working", "seq": 7, "screen": NORMAL}),
                 "task-g": ("orch-g", None)}
        for slug, (name, _) in cases.items():
            self.orch(slug, name)
            self.rollup(slug, "进行中")
        self.set_agents(**{name: info for name, info in cases.values() if info})
        self.watch("--main", *FAST, "--timeout", "1")
        by_watch = self.agent_states(cases)
        self.assertEqual({s: (x["name"], x["shown"], x["seq"], "dialog_fp" in x) for s, x in by_watch.items()}, {
            "task-b": ("orch-b", "blocked", 3, True), "task-s": ("orch-s", "blocked", 4, True),
            "task-e": ("orch-e", "api-error", 5, False), "task-m": ("orch-m", "waiting-background(1)", 6, False),
            "task-w": ("orch-w", "working", 7, False), "task-g": ("orch-g", "gone", None, False)})
        text = self.drover("board", "--plain").stdout
        self.assertEqual(self.agent_states(cases), by_watch)
        bottom = next(x for x in text.splitlines() if x.startswith("主会话在处理："))
        for part in ("task-b 卡在确认框 不到 1 分钟", "task-s 卡在确认框 不到 1 分钟", "task-e 接口错误 不到 1 分钟",
                     "task-g 编排者已退出 不到 1 分钟（rollup：进行中）"):
            self.assertIn(part, bottom.split("：", 1)[1].split("；"))
        for slug in cases:
            (self.state / slug / "agent-state.json").unlink()
        self.drover("board")
        by_board = self.agent_states(cases)
        self.assertEqual({s: {k: v for k, v in x.items() if k != "since"} for s, x in by_board.items()},
                         {s: {k: v for k, v in x.items() if k != "since"} for s, x in by_watch.items()})
        self.watch("--main", *FAST, "--timeout", "1")
        self.assertEqual(self.agent_states(cases), by_board)


if __name__ == "__main__":
    unittest.main(verbosity=2)
