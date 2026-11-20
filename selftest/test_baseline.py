"""2. 和基线比失败集合（accept --verify-baseline → run_verify(baseline=...)）。

解析单测直接喂文本；比对用假工具：基线提交里的 fake-<工具>.json 是基线上的失败，worktree 里改过的是这次的失败。
基线 worktree、缓存都建在临时状态目录的 baselines/ 下。
"""

from __future__ import annotations

import json
import shlex
import sys
import unittest
from collections import Counter

from selftest_support import DroverTestCase

A = {"file": "src/a.test.ts", "test": "math > adds", "kind": "assert"}
B = {"file": "src/b.test.ts", "test": "str > trims", "kind": "assert"}
C = {"file": "src/c.test.ts", "test": "date > parses", "kind": "assert"}
KEY_A, KEY_B, KEY_C = "src/a.test.ts > math > adds", "src/b.test.ts > str > trims", "src/c.test.ts > date > parses"
# 其他三种工具：基线失败 {x, y}，这次 {x, z} → 新增 z、修好 y
OTHER_TOOLS = {
    "pytest": ("pytest -q", ["tests/test_a.py::test_x", "tests/test_b.py::test_y"],
               ["tests/test_a.py::test_x", "tests/test_c.py::test_z"],
               "tests/test_c.py::test_z", "tests/test_b.py::test_y"),
    "tsc": ("tsc --noEmit -p .", [["src/a.ts", "TS2322"], ["src/b.ts", "TS2345"]],
            [["src/a.ts", "TS2322"], ["src/c.ts", "TS7006"]], "src/c.ts TS7006", "src/b.ts TS2345"),
    "eslint": ("eslint src", [["src/a.ts", "no-unused-vars"], ["src/b.ts", "no-undef"]],
               [["src/a.ts", "no-unused-vars"], ["src/c.ts", "@typescript-eslint/no-explicit-any"]],
               "src/c.ts @typescript-eslint/no-explicit-any", "src/b.ts no-undef"),
}


class ParseFailuresTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()
        self.root = str(self.work / "app")

    def test_vitest(self) -> None:
        """vitest：按 文件 + describe + 用例 记条目，../ 开头的路径换成相对 root 的"""
        dotdot = "../" * (self.root.count("/") + 2) + self.root.lstrip("/") + "/src/b.test.ts"
        text = (" RUN  v1.6.0 /x\n\n FAIL  src/a.test.ts > math > adds\nAssertionError: x\n"
                f" FAIL  {dotdot} > str > trims\nError: Test timed out in 5000ms.\n"
                " FAIL  src/c.test.ts [ src/c.test.ts ]\nError: Hook timed out in 10000ms.\n")
        tool, got = self.d.parse_failures(["bun", "run", "test"], text, self.root)
        self.assertEqual(tool, "vitest")
        self.assertEqual(got, Counter({KEY_A: 1, "src/b.test.ts > str > trims": 1,
                                       "src/c.test.ts [ src/c.test.ts ]": 1}))

    def test_vitest_project_labels(self) -> None:
        """vitest 4 配了 test.projects：FAIL 行文件前带 project 标签（不带颜色是 "|ts| "，带颜色是色块、去掉 ANSI 后剩 "ts "）。
        标签不进 key：单条失败、整个文件失败（[ 文件 ]）、超时，带不带标签得到同一组 key。样例取自 vitest 4.1.7 的真实输出"""
        unlabeled = (" RUN  v3.2.4 /x\n\n FAIL  src/b.test.ts [ src/b.test.ts ]\nError: Hook timed out in 300ms.\n"
                     " FAIL  src/c.test.tsx > dom > slow render\nError: Test timed out in 300ms.\n"
                     " FAIL  src/a.test.ts > math > adds\nAssertionError: expected 2 to be 3\n")
        plain = (" RUN  v4.1.7 /x\n\n FAIL  |ts| src/b.test.ts [ src/b.test.ts ]\nError: Hook timed out in 300ms.\n"
                 " FAIL  |tsx| src/c.test.tsx > dom > slow render\nError: Test timed out in 300ms.\n"
                 " FAIL  |ts| src/a.test.ts > math > adds\nAssertionError: expected 2 to be 3\n")
        color = ("\x1b[1m\x1b[30m\x1b[46m RUN \x1b[49m\x1b[39m\x1b[22m \x1b[36mv4.1.7 \x1b[39m\x1b[90m/x\x1b[39m\n\n"
                 "\x1b[41m\x1b[1m FAIL \x1b[22m\x1b[49m \x1b[30m\x1b[43m ts \x1b[49m\x1b[39m src/b.test.ts"
                 "\x1b[2m [ src/b.test.ts ]\x1b[22m\n\x1b[31m\x1b[1mError\x1b[22m: Hook timed out in 300ms.\n"
                 "\x1b[41m\x1b[1m FAIL \x1b[22m\x1b[49m \x1b[30m\x1b[45m tsx \x1b[49m\x1b[39m src/c.test.tsx"
                 "\x1b[2m > \x1b[22mdom\x1b[2m > \x1b[22mslow render\n\x1b[31m\x1b[1mError\x1b[22m: Test timed out in 300ms.\n"
                 "\x1b[41m\x1b[1m FAIL \x1b[22m\x1b[49m \x1b[30m\x1b[43m ts \x1b[49m\x1b[39m src/a.test.ts"
                 "\x1b[2m > \x1b[22mmath\x1b[2m > \x1b[22madds\n\x1b[31m\x1b[1mAssertionError\x1b[22m: expected 2 to be 3\x1b[39m\n")
        want = Counter({"src/b.test.ts [ src/b.test.ts ]": 1, "src/c.test.tsx > dom > slow render": 1, KEY_A: 1})
        for name, text in (("unlabeled", unlabeled), ("plain", plain), ("color", color)):
            with self.subTest(name):
                self.assertEqual(self.d.parse_failures(["bunx", "vitest", "run"], text, self.root), ("vitest", want))
                entries = self.d.vitest_failure_entries(self.d.ANSI_RE.sub("", text), self.root)
                self.assertEqual([(path, is_timeout) for _, path, is_timeout in entries],
                                 [("src/b.test.ts", True), ("src/c.test.tsx", True), ("src/a.test.ts", False)])
        # 用例名里又出现测试文件名：文件取标签后面的第一个
        tricky = " RUN  v4.1.7 /x\n\n FAIL  |ts| src/e.test.ts > loads x.spec.ts fixtures\nError: x\n"
        self.assertEqual(self.d.parse_failures(["vitest"], tricky, self.root),
                         ("vitest", Counter({"src/e.test.ts > loads x.spec.ts fixtures": 1})))

    def test_pytest(self) -> None:
        """pytest：FAILED / ERROR 行；命令名是 pytest，或输出里有 short test summary info 都认"""
        text = ("=== short test summary info ===\nFAILED tests/test_a.py::test_x - assert 1 == 2\n"
                "ERROR tests/test_b.py::test_y - fixture 'db' not found\n=== 1 failed, 1 error in 0.1s ===\n")
        self.assertEqual(self.d.parse_failures(["make", "test"], text, self.root),
                         ("pytest", Counter({"tests/test_a.py::test_x": 1, "tests/test_b.py::test_y": 1})))
        self.assertEqual(self.d.parse_failures(["uv", "run", "pytest"], "collected 0 items\n", self.root),
                         ("pytest", Counter()))

    def test_tsc(self) -> None:
        """tsc：按 文件 + 错误码 记条目（不带行号）；包脚本（bun run typecheck）靠输出格式认出"""
        text = ("src/a.ts(3,5): error TS2322: Type 'string' is not assignable to type 'number'.\n"
                "src/a.ts(9,1): error TS2322: Type 'string' is not assignable to type 'number'.\n"
                "src/b.ts(1,1): error TS7006: Parameter 'x' implicitly has an 'any' type.\n\nFound 3 errors.\n")
        self.assertEqual(self.d.parse_failures(["bun", "run", "typecheck"], text, self.root),
                         ("tsc", Counter({"src/a.ts TS2322": 2, "src/b.ts TS7006": 1})))

    def test_eslint(self) -> None:
        """eslint：包脚本（pnpm lint）、绝对路径换成相对 root 的、只算 error 不算 warning"""
        text = (f"> app@1.0.0 lint {self.root}\n> eslint src\n\n{self.root}/src/a.ts\n"
                "  3:7  error    'x' is assigned a value but never used  no-unused-vars\n"
                "  5:1  warning  Unexpected console statement            no-console\n\n"
                f"{self.root}/src/b.ts\n  1:1  error  Unexpected any. Specify a different type  "
                "@typescript-eslint/no-explicit-any\n\n✖ 3 problems (2 errors, 1 warning)\n")
        self.assertEqual(self.d.parse_failures(["pnpm", "lint"], text, self.root),
                         ("eslint", Counter({"src/a.ts no-unused-vars": 1,
                                             "src/b.ts @typescript-eslint/no-explicit-any": 1})))

    def test_unrecognized(self) -> None:
        """认不出的输出 → (None, None)"""
        self.assertEqual(self.d.parse_failures(["make", "check"], "Error: something broke\n", self.root),
                         (None, None))


class BaselineCompareTests(DroverTestCase):
    """这组的测试文件不在 worktree 里（失败条目映射不到文件）→ 拆不出目标，基线照整条命令跑（以前的做法，也是退回的路径）。
    只重跑失败文件的子集见 BaselineSubsetTests。"""

    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()
        self.wt = self.work / "app"
        base_specs = {"vitest": {"failures": [A, B]}}
        base_specs.update({tool: {"failures": spec[1]} for tool, spec in OTHER_TOOLS.items()})
        files = {f"fake-{tool}.json": json.dumps(spec) for tool, spec in base_specs.items()}
        self.base = self.init_repo(self.wt, {**files, "README.md": "# app\n"})

    def verify(self, tool: str, spec: dict, command: str, baseline: bool = True) -> dict:
        self.write_spec(self.wt, tool, spec)
        return self.d.run_verify(str(self.wt), command, 60, lock=False, baseline=self.base if baseline else None)

    def test_no_new_failures_passes(self) -> None:
        """失败全是基线上本来就有的 → 通过，note 写明没有新增"""
        entry = self.verify("vitest", {"failures": [A, B]}, "vitest run")
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["exit_code"], 1)
        self.assertEqual(entry["baseline"]["new_failures"], [])
        self.assertEqual(entry["baseline"]["parser"], "vitest")
        self.assertFalse(entry["baseline"]["cached"])
        self.assertIn("没有新增", entry["note"])

    def test_second_run_uses_cache(self) -> None:
        """同一个（仓库, BASE, 命令）第二次走缓存，不再建基线 worktree；缓存和临时 worktree 都在状态目录 baselines/ 下"""
        self.verify("vitest", {"failures": [A]}, "vitest run")
        entry = self.verify("vitest", {"failures": [A]}, "vitest run")
        self.assertTrue(entry["ok"], entry)
        self.assertTrue(entry["baseline"]["cached"])
        base_runs = [c for c in self.tool_calls("vitest") if c["cwd"].startswith(str(self.state / "baselines"))]
        self.assertEqual(len(base_runs), 1)
        self.assertEqual(len(list((self.state / "baselines").glob("*.json"))), 1)
        self.assertEqual(list((self.state / "baselines").glob("wt-*")), [])  # 跑完就删
        self.assertEqual(len(self.git(self.wt, "worktree", "list").splitlines()), 1)

    def test_new_failure_fails_and_listed(self) -> None:
        """新增一条失败 → 判失败，new_failures 列出"""
        entry = self.verify("vitest", {"failures": [A, B, C]}, "vitest run")
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["baseline"]["new_failures"], [KEY_C])
        self.assertEqual(entry["baseline"]["fixed"], [])

    def test_fixed_one_and_new_one(self) -> None:
        """修好一条又新增一条 → 判失败，fixed 列出修好的"""
        entry = self.verify("vitest", {"failures": [A, C]}, "vitest run")
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["baseline"]["new_failures"], [KEY_C])
        self.assertEqual(entry["baseline"]["fixed"], [KEY_B])

    def test_without_baseline_no_compare(self) -> None:
        """不带 baseline 的 verify 不比基线"""
        entry = self.verify("vitest", {"failures": [A, B]}, "vitest run", baseline=False)
        self.assertFalse(entry["ok"])
        self.assertNotIn("baseline", entry)
        self.assertNotIn("compare_baseline", entry)
        self.assertFalse((self.state / "baselines").exists())

    def test_new_timeouts_rechecked(self) -> None:
        """新增的失败全是超时 → 单独重跑这些文件，通过就判通过（flaky_timeouts）"""
        entry = self.verify("vitest", {"failures": [A, B, dict(C, kind="timeout")]}, "vitest run")
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["flaky_timeouts"], ["src/c.test.ts"])
        self.assertEqual(entry["baseline"]["new_timeout_recheck"]["exit_code"], 0)
        self.assertEqual(self.tool_calls("vitest")[-1]["args"], ["run", "src/c.test.ts"])

    def test_other_parsers_fixed_and_new(self) -> None:
        """pytest / tsc / eslint：没有新增 → 通过；修好一条又新增一条 → 失败，new 和 fixed 都对"""
        for tool, (command, base, current, new, fixed) in OTHER_TOOLS.items():
            with self.subTest(tool=tool):
                same = self.verify(tool, {"failures": base, "warnings": [["src/c.ts", "no-console"]]}, command)
                self.assertTrue(same["ok"], same)
                self.assertEqual(same["baseline"]["parser"], tool)
                entry = self.verify(tool, {"failures": current}, command)
                self.assertFalse(entry["ok"])
                self.assertEqual(entry["baseline"]["new_failures"], [new])
                self.assertEqual(entry["baseline"]["fixed"], [fixed])
                self.assertTrue(entry["baseline"]["cached"])

    def test_unrecognized_output_fails(self) -> None:
        """认不出失败条目 → 不和基线比，按失败处理"""
        command = shlex.join([sys.executable, "-c", "print('boom'); raise SystemExit(3)"])
        entry = self.d.run_verify(str(self.wt), command, 60, lock=False, baseline=self.base)
        self.assertFalse(entry["ok"])
        self.assertIn("认不出", entry["baseline"]["error"])
        self.assertFalse((self.state / "baselines").exists())

    def test_nonzero_exit_without_entries_fails(self) -> None:
        """退出码非 0、但解析不到失败条目 → 按失败处理"""
        entry = self.verify("pytest", {"failures": [], "exit": 2}, "pytest -q")
        self.assertFalse(entry["ok"])
        self.assertIn("解析不到失败条目", entry["baseline"]["error"])


D = {"file": "src/d.test.ts", "test": "new > feature", "kind": "assert"}
KEY_D = "src/d.test.ts > new > feature"


class BaselineSubsetTests(DroverTestCase):
    """失败所在的文件在 worktree 和 BASE 上都有 → 基线只重跑这些文件。假工具把收到的参数记进 tool-calls.log，
    在基线 worktree（状态目录 baselines/ 下）里的那几次就是基线命令。"""

    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()
        self.wt = self.work / "app"
        sources = ["src/a.test.ts", "src/b.test.ts", "src/c.test.ts", "src/ok.test.ts", "src/a.ts", "src/b.ts",
                   "src/c.ts", "tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
        base_specs = {"vitest": {"failures": [A, B]}}
        base_specs.update({tool: {"failures": spec[1]} for tool, spec in OTHER_TOOLS.items()})
        files = {f"fake-{tool}.json": json.dumps(spec) for tool, spec in base_specs.items()}
        self.base = self.init_repo(self.wt, {**files, **{p: "// x\n" for p in sources}})

    def verify(self, tool: str, spec: dict, command: str) -> dict:
        self.write_spec(self.wt, tool, spec)
        return self.d.run_verify(str(self.wt), command, 60, lock=False, baseline=self.base)

    def base_calls(self, tool: str) -> list:
        return [c["args"] for c in self.tool_calls(tool) if c["cwd"].startswith(str(self.state / "baselines"))]

    def test_vitest_only_failed_files(self) -> None:
        """vitest：基线只跑当前失败的文件（位置参数 src/ 去掉、选项保留）；B 没重跑，fixed 不统计它；有新增时 note 提示误报怎么判断"""
        entry = self.verify("vitest", {"failures": [A, C]}, "vitest run src/ --environment node")
        self.assertEqual(self.base_calls("vitest"), [["run", "--environment", "node", "src/a.test.ts", "src/c.test.ts"]])
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["baseline"]["targets"], ["src/a.test.ts", "src/c.test.ts"])
        self.assertEqual(entry["baseline"]["new_failures"], [KEY_C])
        self.assertEqual(entry["baseline"]["fixed"], [])
        self.assertIn("只重跑了当前失败所在的 2 个文件", entry["note"])
        self.assertIn("单独跑", entry["note"])

    def test_vitest_no_new_note_says_subset(self) -> None:
        """没有新增 → 通过，note 写明基线只重跑了失败的文件"""
        entry = self.verify("vitest", {"failures": [A]}, "vitest run")
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(self.base_calls("vitest"), [["run", "src/a.test.ts"]])
        self.assertIn("没有新增", entry["note"])
        self.assertIn("只重跑了当前失败所在的 1 个文件", entry["note"])

    def test_vitest_changed_dropped_on_base(self) -> None:
        """集成后的补检查 vitest run --changed <提交>：基线命令去掉 --changed 和提交，只点名失败的文件"""
        entry = self.verify("vitest", {"failures": [A]}, f"vitest run --changed {self.base}")
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(self.base_calls("vitest"), [["run", "src/a.test.ts"]])

    def test_vitest_project_labels_match_unlabeled_base(self) -> None:
        """这边配了 test.projects（FAIL 行带 |ts| 标签 / 带颜色的色块）、BASE 上是旧配置（不带标签）：同一条用例对上同一个 key，
        只有真新增的算新增；基线照样只点名失败的文件；新增的全是超时照样单独重跑"""
        for labels in ("plain", "color"):
            with self.subTest(labels=labels):
                same = self.verify("vitest", {"failures": [A, B], "project_labels": labels}, "vitest run")
                self.assertTrue(same["ok"], same)
                self.assertEqual(same["baseline"]["new_failures"], [])
                self.assertEqual(same["baseline"]["targets"], ["src/a.test.ts", "src/b.test.ts"])
                entry = self.verify("vitest", {"failures": [A, C], "project_labels": labels}, "vitest run")
                self.assertFalse(entry["ok"])
                self.assertEqual(entry["baseline"]["new_failures"], [KEY_C])
                self.assertEqual(entry["baseline"]["targets"], ["src/a.test.ts", "src/c.test.ts"])
                flaky = self.verify("vitest", {"failures": [A, B, dict(C, kind="timeout")], "project_labels": labels},
                                    "vitest run")
                self.assertTrue(flaky["ok"], flaky)
                self.assertEqual(flaky["flaky_timeouts"], ["src/c.test.ts"])
                self.assertEqual(self.tool_calls("vitest")[-1]["args"], ["run", "src/c.test.ts"])

    def test_pytest_only_failed_files(self) -> None:
        """pytest：基线只跑失败 node id 所在的文件（-k 和它的值保留，目录 tests 去掉）"""
        _, _, current, new, _ = OTHER_TOOLS["pytest"]
        entry = self.verify("pytest", {"failures": current}, 'pytest -q -k "not slow" tests')
        self.assertEqual(self.base_calls("pytest"), [["-q", "-k", "not slow", "tests/test_a.py", "tests/test_c.py"]])
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["baseline"]["targets"], ["tests/test_a.py", "tests/test_c.py"])
        self.assertEqual(entry["baseline"]["new_failures"], [new])

    def test_eslint_only_error_files(self) -> None:
        """eslint：基线只查报错的文件（只有 warning 的文件不算；--ext 的值保留，目录 src 去掉）"""
        _, _, current, new, _ = OTHER_TOOLS["eslint"]
        entry = self.verify("eslint", {"failures": current, "warnings": [["src/b.ts", "no-console"]]},
                            "eslint --ext .ts src")
        self.assertEqual(self.base_calls("eslint"), [["--ext", ".ts", "src/a.ts", "src/c.ts"]])
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["baseline"]["new_failures"], [new])

    def test_tsc_runs_whole_command(self) -> None:
        """tsc：拆不了文件，基线照整条命令跑，没有 targets"""
        command, _, current, new, _ = OTHER_TOOLS["tsc"]
        entry = self.verify("tsc", {"failures": current}, command)
        self.assertEqual(self.base_calls("tsc"), [["--noEmit", "-p", "."]])
        self.assertNotIn("targets", entry["baseline"])
        self.assertEqual(entry["baseline"]["new_failures"], [new])

    def test_file_not_on_base_counts_as_new(self) -> None:
        """失败的文件 BASE 上没有（新加的测试）→ 不放进基线命令，它的失败算新增"""
        self.write_files(self.wt, {"src/d.test.ts": "// new\n"})
        entry = self.verify("vitest", {"failures": [A, D]}, "vitest run")
        self.assertEqual(self.base_calls("vitest"), [["run", "src/a.test.ts"]])
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["baseline"]["targets"], ["src/a.test.ts"])
        self.assertEqual(entry["baseline"]["not_on_base"], ["src/d.test.ts"])
        self.assertEqual(entry["baseline"]["new_failures"], [KEY_D])
        self.assertIn("BASE 上没有", entry["note"])
        self.assertNotIn("单独跑", entry["note"])  # 新增只在新文件里，不是"单独跑能过"的误报，不提示

    def test_all_files_new_no_baseline_worktree(self) -> None:
        """失败的文件 BASE 上全都没有 → 不建基线 worktree，失败全算新增"""
        self.write_files(self.wt, {"src/d.test.ts": "// new\n"})
        entry = self.verify("vitest", {"failures": [D]}, "vitest run")
        self.assertEqual(self.base_calls("vitest"), [])
        self.assertFalse((self.state / "baselines").exists())
        self.assertEqual(len(self.git(self.wt, "worktree", "list").splitlines()), 1)
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["baseline"]["targets"], [])
        self.assertEqual(entry["baseline"]["not_on_base"], ["src/d.test.ts"])
        self.assertEqual(entry["baseline"]["new_failures"], [KEY_D])
        self.assertIn("没建基线 worktree", entry["note"])

    def test_cache_key_includes_targets(self) -> None:
        """同一批文件第二次走缓存；失败的文件变了（目标集合不同）不命中旧缓存，重新跑基线"""
        self.verify("vitest", {"failures": [A]}, "vitest run")
        again = self.verify("vitest", {"failures": [A]}, "vitest run")
        self.assertTrue(again["baseline"]["cached"])
        more = self.verify("vitest", {"failures": [A, B]}, "vitest run")
        self.assertFalse(more["baseline"]["cached"])
        self.assertTrue(more["ok"], more)
        self.assertEqual(self.base_calls("vitest"), [["run", "src/a.test.ts"], ["run", "src/a.test.ts", "src/b.test.ts"]])
        self.assertEqual(len(list((self.state / "baselines").glob("*.json"))), 2)

    def test_unmapped_file_falls_back_to_whole_command(self) -> None:
        """有一条失败映射不到 worktree 里的文件（路径相对别的 root）→ 基线照整条命令跑，没有 targets"""
        ghost = {"file": "pkg/x.test.ts", "test": "x > y", "kind": "assert"}
        entry = self.verify("vitest", {"failures": [A, ghost]}, "vitest run src/")
        self.assertEqual(self.base_calls("vitest"), [["run", "src/"]])
        self.assertNotIn("targets", entry["baseline"])
        self.assertEqual(entry["baseline"]["new_failures"], ["pkg/x.test.ts > x > y"])


class BaselineSubsetArgvTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()

    def test_subset_argv(self) -> None:
        """baseline_subset_argv：去掉位置参数、保留选项和值；包脚本 vitest 追加文件；tsc、make、eslint 的包脚本拆不出 → None"""
        py, js = ["tests/test_a.py"], ["src/a.ts"]
        cases = [
            ("pytest", ["./.venv/bin/python", "-m", "pytest", "-q", "tests", "--maxfail", "1", "-x", "--tb=short"],
             ["./.venv/bin/python", "-m", "pytest", "-q", "--maxfail", "1", "-x", "--tb=short", *py]),
            ("pytest", ["uv", "run", "pytest", "-ra", "-n", "4", "tests/unit", "--lf"],
             ["uv", "run", "pytest", "-ra", "-n", "4", "--lf", *py]),
            ("pytest", ["pytest", "--unknown-opt", "tests"], ["pytest", "--unknown-opt", "tests", *py]),  # 认不出的长选项：留下目录，等于整条跑
            ("pytest", ["make", "test"], None),
            ("eslint", ["npx", "eslint", "--max-warnings", "0", "--cache", "src", "--ext", ".ts,.tsx"],
             ["npx", "eslint", "--max-warnings", "0", "--cache", "--ext", ".ts,.tsx", *js]),
            ("eslint", ["pnpm", "lint"], None),
            ("tsc", ["tsc", "--noEmit"], None),
            ("vitest", ["bun", "run", "test"], ["bun", "run", "test", *js]),
            ("vitest", ["make", "test"], None),
            # --changed [提交] 去掉：基线 worktree 里"相对那个提交改了什么"不一样，留着会把点名的文件筛掉
            ("vitest", ["npx", "vitest", "run", "--changed", "abc123", "--environment", "node", "src"],
             ["npx", "vitest", "run", "--environment", "node", *js]),
            ("vitest", ["bun", "run", "test", "--changed=abc123"], ["bun", "run", "test", *js]),
            ("vitest", ["npm", "test", "--", "--changed", "abc123"], ["npm", "test", "--", *js]),
        ]
        for parser, argv, want in cases:
            with self.subTest(argv=argv):
                files = py if parser == "pytest" else js
                self.assertEqual(self.d.baseline_subset_argv(parser, argv, files), want)


if __name__ == "__main__":
    unittest.main(verbosity=2)
