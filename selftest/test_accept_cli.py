"""accept 端到端（CLI 子进程）：--verify 的负载超时复核、--verify-baseline 和基线比、--no-graph 跳过建图
（--brief、accept-summary、review 读跳过的图谱）；不建图、锁和缓存都在临时状态目录。

建图绕开的办法：临时环境的 PATH 去掉了有 code-review-graph 的目录，accept 走"找不到 code-review-graph"的分支，
不会往临时仓库写 .code-review-graph，也不会花时间建图。--no-graph 的用例另外放一个只记日志的假 code-review-graph。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

from selftest_support import DroverTestCase

# 只记日志的假 code-review-graph：证明 --no-graph 时没被调用（不带 --no-graph 时同一个假命令会被调用）。
FAKE_GRAPH = r'''#!{python}
import json
import sys
from pathlib import Path

with (Path(__file__).resolve().parent / "graph-calls.log").open("a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1:2] == ["detect-changes"]:
    print("Analyzed 1 changed file\nOverall risk score: 0.10\nUntested: a")
'''


class AcceptCliTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        if shutil.which("code-review-graph", path=self.env["PATH"]):
            self.skipTest("临时 PATH 里还能找到 code-review-graph，跑 accept 会真的建图")
        self.wt = self.work / "app"
        self.base = self.init_repo(self.wt, {
            "src/a.test.ts": "test('a', () => {})\n",
            "fake-vitest.json": json.dumps({"failures": [{"file": "src/a.test.ts", "test": "a > b", "kind": "timeout"}]}),
            "fake-pytest.json": json.dumps({"failures": ["tests/test_a.py::test_x"]}),
        })
        self.write_files(self.wt, {"src/a.ts": "export const a = 1\n", "README.md": "# app\n"})

    def accept(self, *verify: str) -> dict:
        save = self.state / "s1" / "accept.json"
        res = self.drover_json("accept", "--wt", str(self.wt), "--base", self.base, "--save", str(save), *verify)
        self.assertEqual(Path(res["saved_to"]), save)
        self.assertTrue(save.is_file())
        return res

    def test_verify_flaky_timeout_without_graph(self) -> None:
        """--verify：只超时 → 复核通过、带 flaky_timeouts；不建图（graph 报找不到命令、没有 .code-review-graph）；调度状态在状态目录"""
        res = self.accept("--verify", "vitest run src/ --environment node")
        self.assertEqual(res["graph"], {"ok": False, "error": "找不到 code-review-graph 命令"})
        self.assertFalse((self.wt / ".code-review-graph").exists())
        self.assertEqual(sorted(res["intent_to_add"]), ["README.md", "src/a.ts"])
        v = res["verify"][0]
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["flaky_timeouts"], ["src/a.test.ts"])
        self.assertEqual(v["timeout_recheck"]["command"], "vitest run --environment node src/a.test.ts")
        self.assertFalse(res["verify_failed"])
        self.assertTrue((self.state / "locks" / "heavy" / "history.json").is_file())  # 进了 heavy 调度

    def test_verify_baseline_vs_plain_verify(self) -> None:
        """同一条失败的命令：--verify 判失败；--verify-baseline 和 BASE 比，失败都是基线上本来就有的 → 通过"""
        res = self.accept("--verify", "pytest -q", "--verify-baseline", "pytest -q", "--brief")
        plain, with_base = res["verify"]
        self.assertFalse(plain["ok"])
        self.assertNotIn("baseline", plain)
        self.assertTrue(with_base["ok"], with_base)
        self.assertIn("没有新增", with_base["note"])
        self.assertEqual(with_base["baseline"]["parser"], "pytest")
        self.assertEqual(with_base["baseline"]["base"], self.base[:12])
        self.assertTrue(res["verify_failed"])  # 第一条不带基线，仍算验收不通过
        self.assertEqual(len(list((self.state / "baselines").glob("*.json"))), 1)

    def install_fake_graph(self) -> Path:
        """PATH 最前面放一个只记日志的假 code-review-graph（不真的建图），返回它的调用日志路径。"""
        bindir = self.tmp / "graphbin"
        bindir.mkdir()
        script = bindir / "code-review-graph"
        script.write_text(FAKE_GRAPH.replace("{python}", sys.executable, 1))
        script.chmod(0o755)
        self.env["PATH"] = str(bindir) + os.pathsep + self.env["PATH"]
        return bindir / "graph-calls.log"

    def test_no_graph_skips_graph(self) -> None:
        """--no-graph：不调用 code-review-graph，graph 标 skipped（不是 error）；accept --brief、accept-summary 读这份存档不报错。
        对照：不带 --no-graph 时同一个假命令被调用（build + detect-changes）"""
        log = self.install_fake_graph()
        res = self.accept("--no-graph", "--verify", "tsc --noEmit", "--brief")
        self.assertFalse(log.exists(), "带 --no-graph 不该调用 code-review-graph")
        self.assertEqual(res["graph"]["ok"], False)
        self.assertEqual(res["graph"]["skipped"], True)
        self.assertNotIn("error", res["graph"])
        self.assertFalse(res["verify_failed"])
        self.assertEqual(res["head"], self.base)  # 补检查按"比这个提交多了什么"定范围时用
        saved = self.state / "s1" / "accept.json"
        self.assertEqual(json.loads(saved.read_text())["graph"]["skipped"], True)
        summary = self.drover_json("accept-summary", str(saved))
        self.assertEqual(summary["graph"], res["graph"])
        built = self.accept("--verify", "tsc --noEmit", "--brief")
        self.assertTrue(built["graph"]["ok"], built["graph"])
        self.assertEqual([json.loads(line)[0] for line in log.read_text().splitlines()], ["build", "detect-changes"])

    def test_review_reads_skipped_graph(self) -> None:
        """review 读到 --no-graph 的存档：照常生成 prompt，写明这次没建图，不当成"图谱不可用\""""
        self.accept("--no-graph", "--verify", "tsc --noEmit")
        res = self.drover_json("review", "--slug", "s1", "--name", "w1-rev", "--pane", "w1:p1", "--wt", str(self.wt),
                               "--base", self.base, "--dry-run")
        self.assertTrue(res["ok"], res)
        self.assertIn("没建图（--no-graph）", res["prompt"])
        self.assertNotIn("图谱不可用", res["prompt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
