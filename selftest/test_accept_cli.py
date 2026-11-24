"""accept 端到端（CLI 子进程）：--verify 的偶发失败复核（单独重跑）、--verify-baseline 和基线比、--no-graph 跳过建图
（--brief、accept-summary、review 读跳过的图谱）；不建图、锁和缓存都在临时状态目录。
验收看证据：认得出测试框架（vitest / pytest / selftest/run.py / mvn surefire）、退出码 0 却一个测试都没跑的判不通过，
摘要固定给 通过 / 失败 / 跳过 三个数；两个 accept 同时比同一个基线不互删。

建图绕开的办法：临时环境的 PATH 去掉了有 code-review-graph 的目录，accept 走"找不到 code-review-graph"的分支，
不会往临时仓库写 .code-review-graph，也不会花时间建图。--no-graph 的用例另外放一个只记日志的假 code-review-graph。
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from selftest_support import DROVER, DroverTestCase

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

# 真实工具的输出样例（vitest 4.1.11、pytest 9.2.0、maven-surefire 3.5.4 在本机实测，去掉 ANSI；路径换短）。
VITEST_NO_FILES = (" RUN  v4.1.11 /x/app\n\nNo test files found, exiting with code 0\n\n"
                   "include: **/*.{test,spec}.?(c|m)[jt]s?(x)\nexclude:  **/node_modules/**, **/.git/**\n")
VITEST_ALL_SKIPPED = (" RUN  v4.1.11 /x/app\n\n\n Test Files  1 skipped (1)\n      Tests  2 skipped (2)\n"
                      "   Start at  15:26:04\n   Duration  78ms (transform 7ms, setup 0ms, import 12ms, tests 0ms)\n")
VITEST_MIXED = (" RUN  v4.1.11 /x/app\n\n Test Files  1 failed | 3 passed (4)\n"
                "      Tests  1 failed | 5 passed | 1 expected fail | 2 skipped | 1 todo (10)\n   Start at  15:26:04\n")
PYTEST_Q_SKIPPED = "s                                                                        [100%]\n1 skipped in 0.02s\n"
PYTEST_SKIPPED = ("============================= test session starts ==============================\n"
                  "collected 1 item\n\ntests/test_calc.py s                                                     [100%]\n\n"
                  "============================== 1 skipped in 0.12s ==============================\n")
PYTEST_Q_PASSED = "..                                                                       [100%]\n2 passed in 0.00s\n"
PYTEST_Q_MIXED = ("FFs.\n=========================== short test summary info ============================\n"
                  "FAILED tests/test_m.py::test_1 - assert 1 == 2\nFAILED tests/test_m.py::test_2 - assert 2 == 3\n"
                  "2 failed, 1 passed, 1 skipped in 0.01s\n")
MVN_PASSED = ("[INFO] --- surefire:3.5.4:test (default-test) @ p ---\n[INFO] \n"
              "[INFO] -------------------------------------------------------\n[INFO]  T E S T S\n"
              "[INFO] -------------------------------------------------------\n[INFO] Running p.AppTest\n"
              "[WARNING] Tests run: 3, Failures: 0, Errors: 0, Skipped: 1, Time elapsed: 0.021 s -- in p.AppTest\n"
              "[INFO] \n[INFO] Results:\n[INFO] \n[WARNING] Tests run: 3, Failures: 0, Errors: 0, Skipped: 1\n"
              "[INFO] \n[INFO] BUILD SUCCESS\n")
MVN_ALL_SKIPPED = ("[INFO] Results:\n[INFO] \n[WARNING] Tests run: 2, Failures: 0, Errors: 0, Skipped: 2\n"
                   "[INFO] \n[INFO] BUILD SUCCESS\n")
SELFTEST_SUMMARY = ("最慢的 10 个用例（秒）：\n     1.2  test_x.A.test_y\n"
                    "========== 1 failed, 40 passed, 2 skipped in 12.3s (-j 8) ==========\n")
# 基线上本来就有的一条 tsc 错误（集成检查同时比基线的用例用）
TSC_OLD = "src/old.ts(1,14): error TS2322: Type 'string' is not assignable to type 'number'.\n\nFound 1 error.\n"
# 假 vitest 的一条偶发失败：断言形态，只在整套跑时出现，点名 src/a.test.ts 单独跑时通过
FLAKY_A = {"file": "src/a.test.ts", "test": "a > b", "kind": "assert", "flaky": True}


def printer(output: str, code: int = 0) -> str:
    """一条只打印 output、以 code 退出的命令（不经 shell）：计数只看输出，和命令叫什么无关。"""
    return shlex.join([sys.executable, "-c", f"import sys; sys.stdout.write({output!r}); sys.exit({code})"])


class AcceptCliTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        if shutil.which("code-review-graph", path=self.env["PATH"]):
            self.skipTest("临时 PATH 里还能找到 code-review-graph，跑 accept 会真的建图")
        self.wt = self.work / "app"
        # 基线上 vitest 整套跑时有一条断言失败，单独跑 src/a.test.ts 不复现（偶发）
        self.base = self.init_repo(self.wt, {
            "src/a.test.ts": "test('a', () => {})\n",
            "fake-vitest.json": json.dumps({"failures": [FLAKY_A]}),
            "fake-pytest.json": json.dumps({"failures": ["tests/test_a.py::test_x"]}),
        })
        self.write_files(self.wt, {"src/a.ts": "export const a = 1\n", "README.md": "# app\n"})

    def accept(self, *verify: str) -> dict:
        save = self.state / "s1" / "accept.json"
        res = self.drover_json("accept", "--wt", str(self.wt), "--base", self.base, "--save", str(save), *verify)
        self.assertEqual(Path(res["saved_to"]), save)
        self.assertTrue(save.is_file())
        return res

    def test_verify_flaky_failure_without_graph(self) -> None:
        """--verify：整套里的断言失败单独重跑不复现 → 判通过、带 flaky_files 和 isolated_rerun；不建图（graph 报找不到命令、
        没有 .code-review-graph）；调度状态在状态目录"""
        res = self.accept("--verify", "vitest run src/ --environment node")
        self.assertEqual(res["graph"], {"ok": False, "error": "找不到 code-review-graph 命令"})
        self.assertFalse((self.wt / ".code-review-graph").exists())
        self.assertEqual(sorted(res["intent_to_add"]), ["README.md", "src/a.ts"])
        v = res["verify"][0]
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["flaky_files"], ["src/a.test.ts"])
        self.assertEqual(v["isolated_rerun"]["command"], "vitest run --environment node src/a.test.ts")
        self.assertIn("output_tail", v["isolated_rerun"])
        self.assertFalse(res["verify_failed"])
        self.assertTrue((self.state / "locks" / "heavy" / "history.json").is_file())  # 进了 heavy 调度

    def test_baseline_flaky_and_real_new_in_brief(self) -> None:
        """--verify-baseline --brief：整套里一条偶发的断言失败（单独重跑不复现）加一条真新增（新加的测试文件，单独跑也失败）
        → verify_failed，new_failures 只有真新增那条，flaky_files 列出偶发的文件；摘要带单独重跑的命令、退出码、计数，
        不带输出尾部。去掉真新增再验 → 通过、照样列出偶发的文件"""
        self.write_files(self.wt, {"src/n.test.ts": "test('n', () => {})\n"})
        real_new = {"file": "src/n.test.ts", "test": "n > breaks", "kind": "assert"}
        self.write_spec(self.wt, "vitest", {"failures": [FLAKY_A, real_new]})
        res = self.accept("--no-graph", "--brief", "--verify-baseline", "vitest run")
        v = res["verify"][0]
        self.assertTrue(res["verify_failed"])
        self.assertFalse(v["ok"], v)
        self.assertEqual(v["baseline"]["new_failures"], ["src/n.test.ts > n > breaks"])
        self.assertEqual(v["flaky_files"], ["src/a.test.ts"])
        rerun = v["isolated_rerun"]
        self.assertEqual(rerun["command"], "vitest run src/a.test.ts src/n.test.ts")
        self.assertEqual(rerun["exit_code"], 1)
        self.assertEqual(rerun["tests"], {"passed": 10, "failed": 1, "skipped": 0})
        self.assertEqual(rerun["not_reproduced"], ["src/a.test.ts > a > b"])
        self.assertNotIn("output_tail", rerun)
        self.write_spec(self.wt, "vitest", {"failures": [FLAKY_A]})
        res = self.accept("--no-graph", "--brief", "--verify-baseline", "vitest run")
        v = res["verify"][0]
        self.assertFalse(res["verify_failed"])
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["flaky_files"], ["src/a.test.ts"])
        self.assertEqual(v["isolated_rerun"]["exit_code"], 0)
        self.assertNotIn("baseline", v)

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

    def test_brief_gives_three_numbers_and_flags_zero_tests(self) -> None:
        """--brief 和 accept-summary：认得出测试框架的每条 verify 固定带 tests（通过 / 失败 / 跳过）；vitest run --changed
        一个测试文件都没找到（退出码 0）→ 判不通过、note 写明没有测试真的跑，整份验收 verify_failed"""
        self.write_spec(self.wt, "vitest", {"output": VITEST_NO_FILES, "exit": 0})
        res = self.accept("--no-graph", "--brief", "--verify", printer(PYTEST_Q_PASSED),
                          "--verify", f"vitest run --changed {self.base}")
        passed, no_tests = res["verify"]
        self.assertTrue(passed["ok"], passed)
        self.assertEqual(passed["tests"], {"passed": 2, "failed": 0, "skipped": 0})
        self.assertEqual(passed["counts"], ["2 passed in 0.00s"])
        self.assertFalse(no_tests["ok"], no_tests)
        self.assertEqual(no_tests["exit_code"], 0)
        self.assertEqual(no_tests["tests"], {"passed": 0, "failed": 0, "skipped": 0})
        self.assertIn("没有测试真的跑", no_tests["note"])
        self.assertTrue(res["verify_failed"])
        summary = self.drover_json("accept-summary", str(self.state / "s1" / "accept.json"))
        self.assertEqual([v.get("tests") for v in summary["verify"]], [passed["tests"], no_tests["tests"]])

    def test_concurrent_accepts_share_one_baseline(self) -> None:
        """两个 accept 同时和同一个基线比（同一仓库的两个 worktree、同一 BASE、同一条命令；基线上那次 tsc 要跑 2 秒、
        跑的过程中还要读文件）：后到的等先到的跑完、直接用它的缓存，不删对方的基线 worktree。两边都判对（失败都是基线上
        本来就有的），基线只跑了一次、缓存一份"""
        repo, other = self.work / "race", self.work / "race-b"
        base = self.init_repo(repo, {"fake-tsc.json": json.dumps({"output": TSC_OLD, "exit": 2, "sleep": 2})})
        self.git(repo, "worktree", "add", "-q", "-b", "task/b", str(other))
        for wt in (repo, other):
            self.write_spec(wt, "tsc", {"output": TSC_OLD, "exit": 2})
        procs = [subprocess.Popen([sys.executable, str(DROVER), "accept", "--wt", str(wt), "--base", base, "--no-graph",
                                   "--brief", "--save", str(self.state / "race" / f"{n}.json"),
                                   "--verify-baseline", "tsc --noEmit"],
                                  env=self.env, cwd=str(self.tmp), stdout=subprocess.PIPE, text=True)
                 for n, wt in enumerate((repo, other))]
        results = [json.loads(p.communicate(timeout=120)[0]) for p in procs]
        for res in results:
            v = res["verify"][0]
            self.assertTrue(v["ok"], v)
            self.assertIn("没有新增", v["note"])
        base_runs = [c for c in self.tool_calls("tsc") if c["cwd"].startswith(str(self.state / "baselines"))]
        self.assertEqual(len(base_runs), 1, base_runs)
        self.assertEqual(sorted(v["verify"][0]["baseline"]["cached"] for v in results), [False, True])
        self.assertEqual(len(list((self.state / "baselines").glob("*.json"))), 1)
        self.assertEqual(list((self.state / "baselines").glob("wt-*")), [])


class VerifyEvidenceTests(DroverTestCase):
    """run_verify 看证据：认得出测试框架的输出（vitest / pytest / selftest/run.py / mvn surefire）时算真的跑了几个，
    退出码 0 却一个都没跑的判不通过；tests 固定给 通过 / 失败 / 跳过；认不出的命令（tsc、build、脚本）判定不变。
    命令是只打印真实输出样例的 python（printer），或回放样例的假工具（fake-<工具>.json 的 output）。"""

    def setUp(self) -> None:
        super().setUp()
        self.wt = self.work / "app"
        self.base = self.init_repo(self.wt, {"README.md": "# app\n"})
        self.d = self.load_drover()

    def verify(self, command: str) -> dict:
        return self.d.run_verify(str(self.wt), command, 60, lock=False)

    def test_zero_tests_fail_with_note(self) -> None:
        """退出码 0 但没有测试真的跑 → 不通过，note 写明：vitest 没找到测试文件（--changed 没碰到测试）、全部跳过；
        pytest -q / 默认输出全部跳过；mvn 全部跳过"""
        self.write_spec(self.wt, "vitest", {"output": VITEST_NO_FILES, "exit": 0})
        cases = {"vitest --changed 没找到测试文件": f"vitest run --changed {self.base}",
                 "vitest 全部跳过": printer(VITEST_ALL_SKIPPED), "pytest -q 全部跳过": printer(PYTEST_Q_SKIPPED),
                 "pytest 全部跳过": printer(PYTEST_SKIPPED), "mvn 全部跳过": printer(MVN_ALL_SKIPPED)}
        for name, command in cases.items():
            with self.subTest(name):
                entry = self.verify(command)
                self.assertFalse(entry["ok"], entry)
                self.assertEqual(entry["exit_code"], 0)
                self.assertEqual(entry["tests"]["passed"] + entry["tests"]["failed"], 0)
                self.assertIn("没有测试真的跑", entry["note"])

    def test_counts_three_numbers(self) -> None:
        """tests 固定三个数，counts 是计数行：pytest -q 的汇总（含 skipped）、mvn 的 Tests run（Results 汇总那行，不重复算
        每个类那行；Tests run 含跳过的）、selftest/run.py 的汇总行、vitest 的 expected fail / todo"""
        cases = [(printer(PYTEST_Q_PASSED), True, {"passed": 2, "failed": 0, "skipped": 0}, ["2 passed in 0.00s"]),
                 (printer(PYTEST_Q_MIXED, 1), False, {"passed": 1, "failed": 2, "skipped": 1},
                  ["2 failed, 1 passed, 1 skipped in 0.01s"]),
                 (printer(MVN_PASSED), True, {"passed": 2, "failed": 0, "skipped": 1},
                  ["[WARNING] Tests run: 3, Failures: 0, Errors: 0, Skipped: 1"]),
                 (printer(SELFTEST_SUMMARY, 1), False, {"passed": 40, "failed": 1, "skipped": 2},
                  ["========== 1 failed, 40 passed, 2 skipped in 12.3s (-j 8) =========="]),
                 (printer(VITEST_MIXED, 1), False, {"passed": 6, "failed": 1, "skipped": 3},
                  ["Test Files  1 failed | 3 passed (4)",
                   "Tests  1 failed | 5 passed | 1 expected fail | 2 skipped | 1 todo (10)"])]
        for command, ok, tests, counts in cases:
            with self.subTest(counts[-1]):
                entry = self.verify(command)
                self.assertEqual(entry["ok"], ok, entry)
                self.assertEqual(entry["tests"], tests)
                self.assertEqual(entry["counts"], counts)
                self.assertNotIn("note", entry)

    def test_unrecognized_commands_unchanged(self) -> None:
        """认不出测试框架的命令（tsc、build、脚本）只看退出码，没有 tests 字段"""
        for command in ("tsc --noEmit", printer("vite v5 building for production...\n✓ 34 modules transformed.\n"),
                        printer("Ran 0 checks\n")):
            with self.subTest(command):
                entry = self.verify(command)
                self.assertTrue(entry["ok"], entry)
                self.assertNotIn("tests", entry)
                self.assertEqual(entry["counts"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
