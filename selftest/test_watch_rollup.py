"""11. rollup 事件：watch 对有 orch.json 的 agent（编排者、子编排者）同时盯它的 rollup.md，输出 EVENT <名> rollup:<状态>。

起因：编排者写了"等你决定""完成"，却还挂着 Monitor（watch 报 waiting-background(N)），主会话只在 done / idle 时读 rollup，
就漏了。规则：状态变了就报；状态没变、内容变了，只在 等你决定 / 完成 / 受阻 / 需要换编排者 时报；
启动时和 --once 输出当前值，--until-event 不因启动时的输出退出。orch.json 每轮重新解析，同名多条取修改时间最新的。
不看模型手写的"更新："行（状态没变、又写了同一个推算时间时，新的"等你决定"会漏报）；没有"更新："行的 rollup 照常。
改没改按规范化内容（每行去掉首尾空白、删掉空行后的 digest）判断，不按文件修改时间：
原样重写、只改空白空行不再重报，正文变了（"更新："行没变也算）照样报。
"""

from __future__ import annotations

import json
import os
import time
import unittest

from selftest_support import DroverTestCase

FAST = ["--until-event", "--interval", "0.2", "--stall-minutes", "0", "--quiet-minutes", "0"]
BODY = "- 一个 worker 在干活"
NORMAL = """⏺ rollup 已更新。

✻ Cooked for 3m · done 1:05 AM
────────────────
❯
────────────────
  ⏵⏵ accept edits on · ← 1 agent"""
# 写完 rollup 还挂着 Monitor 的编排者：herdr 报 done，watch 报 waiting-background(1)
MONITOR = """⏺ rollup 写好了，状态：完成。

✻ Baked for 11m 22s · done 7:13 PM · 2 monitors still running
────────────────
❯
────────────────
  ⏵⏵ accept edits on · 2 monitors · ← 1 agent"""


class RollupCliTests(DroverTestCase):
    def orch(self, slug: str, name: str, parent: str = None) -> None:
        """写 <状态目录>/<slug>/orch.json（orch-start 写的那份的字段）。"""
        d = self.state / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "orch.json").write_text(json.dumps(
            {"slug": slug, "name": name, "kind": "claude", "parent": parent, "workspace_id": "w1", "tab_id": "w1:t1",
             "pane_id": "w1:p1", "started": "11-06 09:00"}, ensure_ascii=False))

    def rollup(self, slug: str, status: str, updated: str = None, parent: str = None, body: str = BODY) -> None:
        """整份覆盖 rollup.md（先写临时文件再改名，watch 读不到半截）；updated 为 None 时不写"更新："行。"""
        lines = [f"# {slug}", f"状态：{status}"] + ([f"更新：{updated}"] if updated else []) \
            + ([f"上层：{parent}"] if parent else [])
        lines += ["", "## 当前进度", body, ""]
        path = self.state / slug / "rollup.md"
        tmp = path.with_name("rollup.md.tmp")
        tmp.write_text("\n".join(lines))
        os.replace(tmp, path)

    def rewrite_rollup(self, slug: str, status: str, updated: str = None, body: str = BODY) -> None:
        """重写 rollup.md，并把修改时间往后拨 2 秒（文件系统的时间精度再粗也能看出改过）。"""
        self.rollup(slug, status, updated, body=body)
        later = time.time() + 2
        os.utime(self.state / slug / "rollup.md", (later, later))

    def change_rollup(self, status: str, updated: str, new_status: str, new_updated: str, timeout: str = "30",
                      new_body: str = BODY) -> tuple:
        """编排者 orch-x（done + 挂着 Monitor）和 worker w（working，没有 orch.json）一起盯；
        读完开头 3 行后重写 rollup，等 watch 退出。"""
        self.orch("task-x", "orch-x")
        self.rollup("task-x", status, updated)
        self.set_agents(**{"orch-x": {"status": "done", "screen": MONITOR}, "w": {"status": "working"}})
        return self.watch_then(["orch-x", "w", *FAST, "--timeout", timeout], 3,
                               lambda: self.rewrite_rollup("task-x", new_status, new_updated, new_body))

    def test_status_change_reported_then_exit(self) -> None:
        """进行中 → 完成：编排者还挂着 Monitor（waiting-background），照样单独报 rollup:完成，--until-event 因它退出；
        启动时输出当前 rollup 状态但不退出；没有 orch.json 的 worker 不出 rollup 行"""
        rc, first, rest = self.change_rollup("进行中", "11-06 10:00", "完成", "11-06 10:05")
        self.assertEqual(first, ["EVENT orch-x waiting-background(2)", "EVENT orch-x rollup:进行中", "EVENT w working"])
        self.assertEqual((rc, rest), (0, ["EVENT orch-x rollup:完成"]))

    def test_decision_update_reported(self) -> None:
        """等你决定下重写了文件（又加了一个要决定的问题，"更新："也改了）→ 再报 rollup:等你决定"""
        rc, first, rest = self.change_rollup("等你决定", "11-06 10:00", "等你决定", "11-06 10:07")
        self.assertEqual(first[1], "EVENT orch-x rollup:等你决定")
        self.assertEqual((rc, rest), (0, ["EVENT orch-x rollup:等你决定"]))

    def test_decision_body_change_same_header_reported(self) -> None:
        """状态和"更新："行都没变、正文改了（又加了一个要决定的问题）→ 照样再报 rollup:等你决定
        （以前按"更新："判断，模型写了同一个推算时间就漏报）"""
        rc, first, rest = self.change_rollup("等你决定", "11-06 10:00", "等你决定", "11-06 10:00",
                                             new_body="- 一个 worker 在干活\n- 新问题：要不要顺手改 B")
        self.assertEqual(first[1], "EVENT orch-x rollup:等你决定")
        self.assertEqual((rc, rest), (0, ["EVENT orch-x rollup:等你决定"]))

    def test_decision_rewrite_without_updated_line(self) -> None:
        """rollup 不写"更新："行（可省）：正文变了照样再报"""
        rc, first, rest = self.change_rollup("等你决定", None, "等你决定", None, new_body="- 换了一个问题")
        self.assertEqual(first[1], "EVENT orch-x rollup:等你决定")
        self.assertEqual((rc, rest), (0, ["EVENT orch-x rollup:等你决定"]))

    def test_identical_rewrite_not_reported(self) -> None:
        """等你决定 / 完成下原样重写（修改时间变了、内容一字不差）、只改了空白和空行 → 都不报，到期 timeout
        （以前按修改时间判断，每重写一次就报一次）"""
        for status, body in (("等你决定", BODY), ("完成", "  - 一个 worker 在干活   \n\n")):
            with self.subTest(status):
                rc, first, rest = self.change_rollup(status, None, status, None, timeout="3", new_body=body)
                self.assertEqual(first[1], f"EVENT orch-x rollup:{status}")
                self.assertEqual((rc, rest), (0, ["EVENT timeout"]))

    def test_decision_untouched_not_repeated(self) -> None:
        """等你决定、文件没动（修改时间不变）→ 只有启动时那一行，不重复报，到期 timeout"""
        self.orch("task-x", "orch-x")
        self.rollup("task-x", "等你决定", "11-06 10:00")
        self.set_agents(**{"orch-x": {"status": "done", "screen": MONITOR}, "w": {"status": "working"}})
        rc, first, rest = self.watch_then(["orch-x", "w", *FAST, "--timeout", "3"], 3, lambda: None)
        self.assertEqual(first[1], "EVENT orch-x rollup:等你决定")
        self.assertEqual((rc, rest), (0, ["EVENT timeout"]))

    def test_replace_orch_update_reported(self) -> None:
        """需要换编排者下重写了文件 → 也再报 rollup:需要换编排者（它和等你决定一样要主会话处理）"""
        rc, first, rest = self.change_rollup("需要换编排者", "11-06 10:00", "需要换编排者", "11-06 10:07")
        self.assertEqual(first[1], "EVENT orch-x rollup:需要换编排者")
        self.assertEqual((rc, rest), (0, ["EVENT orch-x rollup:需要换编排者"]))

    def test_in_progress_update_not_reported(self) -> None:
        """进行中下重写了文件（里程碑）→ 不报，到期 timeout"""
        rc, first, rest = self.change_rollup("进行中", "11-06 10:00", "进行中", "11-06 10:07", timeout="3")
        self.assertEqual(first[1], "EVENT orch-x rollup:进行中")
        self.assertEqual((rc, rest), (0, ["EVENT timeout"]))

    def test_orch_json_written_later(self) -> None:
        """watch 起来时还没有 orch.json，之后才写（orch.json 每轮重新解析）→ 出现时报 rollup:<状态>，--until-event 退出"""
        self.set_agents(**{"orch-late": {"status": "done", "screen": NORMAL}})

        def start_orch() -> None:
            self.orch("task-late", "orch-late")
            self.rollup("task-late", "进行中", "11-06 10:00")

        rc, first, rest = self.watch_then(["orch-late", *FAST, "--timeout", "30"], 1, start_orch)
        self.assertEqual((rc, first, rest), (0, ["EVENT orch-late done"], ["EVENT orch-late rollup:进行中"]))

    def test_once_reports_current_rollup(self) -> None:
        """--once：编排者、子编排者输出当前 rollup 状态（原值，如"完成（已收尾）"）；没有 rollup.md、rollup 里没有状态行的
        不输出也不报错；worker（没有 orch.json）不出 rollup 行；别的编排者的 orch.json 不串"""
        self.orch("task-x", "orch-x")
        self.rollup("task-x", "完成（已收尾）", "11-06 10:00")
        self.orch("task-x-repo", "orch-x-repo", parent="task-x")
        self.rollup("task-x-repo", "受阻", "11-06 10:01", parent="task-x")
        self.orch("task-empty", "orch-empty")  # 只有 orch.json
        self.orch("task-nostatus", "orch-nostatus")
        (self.state / "task-nostatus" / "rollup.md").write_text("# task-nostatus\n\n## 当前进度\n")
        self.orch("task-other", "orch-other")
        self.rollup("task-other", "等你决定", "11-06 10:02")
        self.set_agents(**{"orch-x": {"status": "done", "screen": MONITOR},
                           "orch-x-repo": {"status": "done", "screen": NORMAL},
                           "orch-empty": {"status": "done", "screen": NORMAL},
                           "orch-nostatus": {"status": "working"}, "w": {"status": "working"}})
        rc, evs, _ = self.watch("--once", "orch-x", "orch-x-repo", "orch-empty", "orch-nostatus", "w")
        self.assertEqual(rc, 0)
        self.assertEqual(evs, ["EVENT orch-x waiting-background(2)", "EVENT orch-x rollup:完成（已收尾）",
                               "EVENT orch-x-repo done", "EVENT orch-x-repo rollup:受阻", "EVENT orch-empty done",
                               "EVENT orch-nostatus working", "EVENT w working"])

    def test_latest_orch_json_wins(self) -> None:
        """同一个编排者名有两份 orch.json（名字被后来的任务复用）→ 取修改时间最新的那份任务的 rollup"""
        self.orch("task-old", "orch-x")
        self.rollup("task-old", "完成", "11-05 18:00")
        hour_ago = time.time() - 3600
        os.utime(self.state / "task-old" / "orch.json", (hour_ago, hour_ago))
        self.orch("task-new", "orch-x")
        self.rollup("task-new", "进行中", "11-06 10:00")
        self.set_agents(**{"orch-x": {"status": "working"}})
        rc, evs, _ = self.watch("--once", "orch-x")
        self.assertEqual((rc, evs), (0, ["EVENT orch-x working", "EVENT orch-x rollup:进行中"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
