"""2. 和基线比失败集合（accept --verify-baseline → run_verify(baseline=...)）。

解析单测直接喂文本；比对用假工具：基线提交里的 fake-<工具>.json 是基线上的失败，worktree 里改过的是这次的失败。
BaselineEvidenceTests 让假工具原样回放真实输出样例（spec 的 output），测解析不出的失败、对不上数、提前停了、基线不可信。
vitest 的当前侧先单独重跑失败文件（test_vitest_recheck.py），再拿单独跑的结果和基线比：BaselineSubsetTests 里偶发的不算新增。
基线 worktree、缓存都建在临时状态目录的 baselines/ 下。
"""

from __future__ import annotations

import json
import shlex
import sys
import unittest
from collections import Counter

import selftest_fakes
import selftest_vitest_samples as samples
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
# 真实工具的输出样例，回放给假工具用。vitest 的在 selftest_vitest_samples.py（4.1.7 原样）；pytest 9.2.0 在本机实测、去掉 ANSI；
# eslint / tsc 按工具的格式写。
KEY_OLD = "src/old.test.js > old broken"
VITEST_OLD = samples.OLD
# 同一次运行又多了 2 个未处理错误：多一节 Unhandled Errors、汇总多一行 "Errors  2 errors"，没有 FAIL 行
VITEST_OLD_UNHANDLED = samples.OLD_UNHANDLED
TSC_OLD_LINE = "src/old.ts(1,14): error TS2322: Type 'string' is not assignable to type 'number'.\n"
TSC_GLOBAL = ("error TS2688: Cannot find type definition file for 'node'.\n  The file is in the program because:\n"
              "    Entry point of type library 'node' specified in compilerOptions\n")
ESLINT_OLD = "src/old.js\n  3:7  error  'x' is assigned a value but never used  no-unused-vars\n\n"
ESLINT_PARSE_ERROR = "src/new.js\n  1:18  error  Parsing error: Unexpected token ;\n\n"
PYTEST_OLD = ("=========================== short test summary info ============================\n"
              "FAILED tests/test_m.py::test_1 - assert 1 == 2\n")
PYTEST_STOPPED = PYTEST_OLD + ("!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                               "============================== 1 failed in 0.02s ===============================\n")
PYTEST_COLLECT_STOPPED = ("=========================== short test summary info ============================\n"
                          "ERROR tests/test_a.py\n!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection "
                          "!!!!!!!!!!!!!!!!!!!!\n1 error in 0.05s\n")
# vitest --bail 1：串行时没开始的文件不算进任何一项（各项之和小于总数）；并行时已开始的被取消、算成 skipped
VITEST_BAIL_SERIAL = samples.BAIL_SERIAL
VITEST_BAIL_PARALLEL = samples.BAIL_PARALLEL


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
                self.assertEqual([path for _, path in entries], ["src/b.test.ts", "src/c.test.tsx", "src/a.test.ts"])
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

    def test_unparsed_error_lines_become_entries(self) -> None:
        """认不出条目格式的错误行也记成条目：vitest 汇总里的未处理错误（"Errors  N errors"，没有 FAIL 行）记 N 条、
        tsc 不带文件的全局错误记 "(全局) 错误码"、eslint 没有规则名的（Parsing error）记 "文件 (无规则)\""""
        self.assertEqual(self.d.parse_failures(["vitest"], VITEST_OLD_UNHANDLED, self.root),
                         ("vitest", Counter({KEY_OLD: 1, "(未处理错误)": 2})))
        self.assertEqual(self.d.parse_failures(["npx", "tsc"], TSC_GLOBAL + TSC_OLD_LINE, self.root),
                         ("tsc", Counter({"(全局) TS2688": 1, "src/old.ts TS2322": 1})))
        self.assertEqual(self.d.parse_failures(["pnpm", "lint"], ESLINT_PARSE_ERROR + ESLINT_OLD
                                               + "✖ 2 problems (2 errors, 0 warnings)\n", self.root),
                         ("eslint", Counter({"src/new.js (无规则)": 1, "src/old.js no-unused-vars": 1})))

    def test_vitest_real_failure_shapes(self) -> None:
        """vitest 4.1.7 的真实输出：describe 级套件失败（FAIL 行不带方括号）、整个文件失败、一条用例两个 error（同名两行）、
        两条同名用例抛同样的 error（并成一组）→ 一行 FAIL 一条；解析到的行数等于分隔线上的 error 总数，自检不报对不上"""
        suite, multi = "src/suite-hook.test.ts > needs setup", "src/multi-error.test.ts > soft > two soft failures"
        cases = [("describe 级", samples.SUITE_HOOK, Counter({suite: 1})),
                 ("两个 error", samples.MULTI_ERROR, Counter({multi: 2})),
                 ("混在一起", samples.MIXED, Counter({"src/file-import.test.ts [ src/file-import.test.ts ]": 1, suite: 1,
                                                  multi: 2, "src/same-name.test.ts > dup": 2}))]
        for name, text, want in cases:
            with self.subTest(name):
                self.assertEqual(self.d.parse_failures(["vitest", "run"], text, self.root), ("vitest", want))
                self.assertIsNone(self.d.reported_mismatch("vitest", text, want))
        # 两段 vitest 输出（一条命令里连着跑两次）：各段的总数相加
        self.assertIsNone(self.d.reported_mismatch("vitest", samples.MIXED + samples.MULTI_ERROR, Counter()))

    def test_vitest_unparsed_fail_lines_reported(self) -> None:
        """文件名认不出的 FAIL 行（不带 .test. / .spec.）：解析到的行数比分隔线上的总数少 → 自检报对不上。
        用例级的、整个文件失败的（"Tests N failed" 里没有它）都兜得住"""
        self.assertEqual(self.d.reported_mismatch("vitest", samples.UNPARSED_NAME, Counter()),
                         "vitest 自己报了 1 条失败，解析出 0 条")
        self.assertEqual(self.d.reported_mismatch("vitest", samples.UNPARSED_FILE_LEVEL, Counter()),
                         "vitest 自己报了 3 条失败，解析出 2 条")

    def test_vitest_bare_counter_line_is_not_a_divider(self) -> None:
        """测试自己往 stdout 打的 "[1/3]"（不带 ⎯）不是分隔线，不算进 vitest 报的总数：自检不报对不上。真的分隔线照常认：
        这份输出后面再接一份有解析不出的失败的输出，报的是两条真分隔线的总数之和（2），不是 2 + 3"""
        text = samples.CONSOLE_COUNTER
        self.assertIn("\n[1/3]\n", text)
        self.assertIn("⎯[1/1]⎯\n", text)
        self.assertIsNone(self.d.reported_mismatch("vitest", text, Counter()))
        self.assertEqual(self.d.reported_mismatch("vitest", text + samples.UNPARSED_NAME, Counter()),
                         "vitest 自己报了 2 条失败，解析出 1 条")
        # 终端窄到左边一个 ⎯ 都放不下时，右边那个还在（vitest 的 divider(text, undefined, 1)：left 最小 0，right 固定 1）。
        # 这一行是照源码写的，不是抓的样本。
        narrow = " RUN  v4.1.7 /x\n\n FAIL  src/a.test.ts > x\nError: boom\n\n[1/2]⎯\n"
        self.assertEqual(self.d.reported_mismatch("vitest", narrow, Counter()), "vitest 自己报了 2 条失败，解析出 1 条")


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

    def test_unmapped_failures_not_rerun(self) -> None:
        """失败对不上 worktree 里的文件（这组的测试文件都不在 worktree 里）→ 没有能点名重跑的文件：不做单独重跑、note 写原因，
        当前侧按整套那次的算，整套里才出现的那条照样算新增"""
        entry = self.verify("vitest", {"failures": [A, B, dict(C, flaky=True)]}, "vitest run")
        self.assertFalse(entry["ok"], entry)
        self.assertNotIn("isolated_rerun", entry)
        self.assertNotIn("flaky_files", entry)
        self.assertEqual(entry["baseline"]["new_failures"], [KEY_C])
        self.assertIn("没做单独重跑", entry["note"])
        self.assertEqual([c["args"] for c in self.tool_calls("vitest")], [["run"], ["run"]])  # 这边整套 + 基线上整条

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


class BaselineEvidenceTests(DroverTestCase):
    """解析不出来的失败不能被"都是基线本来就有的"盖掉、基线不可信时不比也不缓存。假工具回放真实输出样例：基线提交里的
    fake-<工具>.json 是基线上的输出，worktree 里改过的是这次的。样例里的文件不在 worktree 里，基线照整条命令跑。"""

    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()
        self.wt = self.work / "app"

    def make(self, tool: str, base_output: str, base_exit: int, files: tuple = (), **extra) -> None:
        """基线提交：tool 在基线上打印 base_output、以 base_exit 退出（extra 里的 sleep 等原样写进 spec）；files 是另外提交的
        空测试文件（样例里的失败对得上 worktree 里的文件，vitest 才会单独重跑）。"""
        self.base = self.init_repo(self.wt, {f"fake-{tool}.json": json.dumps(
            {"output": base_output, "exit": base_exit, **extra}), **{path: "// x\n" for path in files}})

    def verify(self, tool: str, output: str, command: str, exit_code: int = 1, timeout: float = 60) -> dict:
        self.write_spec(self.wt, tool, {"output": output, "exit": exit_code})
        return self.d.run_verify(str(self.wt), command, timeout, lock=False, baseline=self.base)

    def caches(self) -> list:
        return sorted((self.state / "baselines").glob("*.json"))

    def test_unparsed_new_failures_not_masked(self) -> None:
        """基线上本来有一条旧失败，这次又多了解析不成普通条目的失败：vitest 的未处理错误（没有 FAIL 行）、tsc 不带文件的
        全局错误、eslint 的语法错误（没有规则名）→ 判失败，new_failures 列出它们，不是"全部是基线上本来就有的\""""
        cases = [("vitest", "vitest run", VITEST_OLD, VITEST_OLD_UNHANDLED, ["(未处理错误)"]),
                 ("tsc", "tsc --noEmit", TSC_OLD_LINE + "\nFound 1 error.\n",
                  TSC_GLOBAL + TSC_OLD_LINE + "\nFound 2 errors in 1 file.\n", ["(全局) TS2688"]),
                 ("eslint", "eslint src", ESLINT_OLD + "✖ 1 problem (1 error, 0 warnings)\n",
                  ESLINT_PARSE_ERROR + ESLINT_OLD + "✖ 2 problems (2 errors, 0 warnings)\n", ["src/new.js (无规则)"])]
        for tool, command, base_output, output, new in cases:
            with self.subTest(tool):
                self.make(tool, base_output, 1)
                entry = self.verify(tool, output, command)
                self.assertFalse(entry["ok"], entry)
                self.assertEqual(entry["baseline"]["new_failures"], new)
                self.assertNotIn("没有新增", entry.get("note", ""))
                if tool != "vitest":  # vitest 的 note 写的是为什么没做单独重跑
                    self.assertNotIn("note", entry)

    def test_vitest_suite_failures_and_multi_error_compared(self) -> None:
        """vitest 4.1.7 的真实输出回放：describe 级套件失败、一条用例两个 error、几种形态混在一起、测试自己往 stdout 打了
        不带 ⎯ 的 "[1/3]"，基线上也是同样的失败 → 不报"解析对不上"，照常和基线比、没有新增、判通过。样例里的文件在
        worktree 里，当前侧先单独重跑、基线只跑这些文件"""
        cases = [("describe 级", samples.SUITE_HOOK, ["src/suite-hook.test.ts"], 1),
                 ("两个 error", samples.MULTI_ERROR, ["src/multi-error.test.ts"], 2),
                 ("混在一起", samples.MIXED, ["src/file-import.test.ts", "src/multi-error.test.ts",
                                          "src/same-name.test.ts", "src/suite-hook.test.ts"], 6),
                 ("测试打的 [1/3]", samples.CONSOLE_COUNTER, ["src/console-divider.test.ts"], 1)]
        for name, sample, files, count in cases:
            with self.subTest(name):
                self.make("vitest", sample, 1, files=tuple(files))
                entry = self.verify("vitest", sample, "vitest run")
                self.assertTrue(entry["ok"], entry)
                self.assertNotIn("error", entry["baseline"])
                self.assertEqual(entry["baseline"]["new_failures"], [])
                self.assertEqual(entry["baseline"]["current_failures"], count)
                self.assertEqual(entry["baseline"]["baseline_failures"], count)
                self.assertEqual(entry["baseline"]["targets"], files)
                self.assertEqual(entry["isolated_rerun"]["files"], files)
                self.assertEqual(entry["isolated_rerun"]["not_reproduced"], [])
                self.assertNotIn("flaky_files", entry)
                self.assertIn("没有新增", entry["note"])

    def test_vitest_unparsed_failure_still_blocked(self) -> None:
        """真有解析不出的失败照样兜住：文件名认不出的用例失败、认不出的文件整个失败（基线上也是同样的输出，认得出的两条
        都是本来就有的）→ 不和基线比、按失败处理，baseline.error 写明对不上，不建基线 worktree、不单独重跑"""
        for name, sample, want in (("用例级", samples.UNPARSED_NAME, "解析出 0 条"),
                                   ("整个文件", samples.UNPARSED_FILE_LEVEL, "报了 3 条失败，解析出 2 条")):
            with self.subTest(name):
                self.make("vitest", sample, 1, files=("src/assert-and-timeout.test.ts",))
                entry = self.verify("vitest", sample, "vitest run")
                self.assertFalse(entry["ok"], entry)
                self.assertIn(want, entry["baseline"]["error"])
                self.assertNotIn("isolated_rerun", entry)
                self.assertFalse((self.state / "baselines").exists())

    def test_reported_total_mismatch_fails(self) -> None:
        """工具自己报的失败数（pytest 汇总写 2 failed）和解析出的条数（1 条 FAILED 行，基线上本来就有）对不上 → 不和基线比，
        按失败处理，baseline.error 写明，不建基线 worktree"""
        self.make("pytest", PYTEST_OLD + "1 failed, 4 passed in 0.1s\n", 1)
        entry = self.verify("pytest", PYTEST_OLD + "2 failed, 3 passed in 0.1s\n", "pytest -q")
        self.assertFalse(entry["ok"], entry)
        self.assertIn("解析出 1 条", entry["baseline"]["error"])
        self.assertFalse((self.state / "baselines").exists())

    def test_stopped_early_not_passed(self) -> None:
        """输出里看得出提前停了（pytest -x / --maxfail 的 "stopping after"、收集出错的 "Interrupted"、vitest --bail 串行时
        没开始的文件、命令带 --bail 1 且失败到了 1 条）→ 停在旧失败上也不判通过，error 写明提前停了。
        对照：同样的并行输出、命令没带 --bail → 照常比（没有新增，通过）"""
        cases = [("pytest", "pytest -x tests", PYTEST_STOPPED), ("pytest", "pytest -q tests", PYTEST_COLLECT_STOPPED),
                 ("vitest", "vitest run --bail 1 --no-file-parallelism", VITEST_BAIL_SERIAL)]
        for tool, command, output in cases:
            with self.subTest(command):
                self.make(tool, output, 1)
                entry = self.verify(tool, output, command)
                self.assertFalse(entry["ok"], entry)
                self.assertIn("提前停了", entry["baseline"]["error"])
        self.make("vitest", VITEST_BAIL_PARALLEL, 1)
        bailed = self.verify("vitest", VITEST_BAIL_PARALLEL, "vitest run --bail=1")
        self.assertFalse(bailed["ok"], bailed)
        self.assertIn("提前停了", bailed["baseline"]["error"])
        control = self.verify("vitest", VITEST_BAIL_PARALLEL, "vitest run")
        self.assertTrue(control["ok"], control)
        self.assertIn("没有新增", control["note"])

    def test_base_deps_failure_untrusted_not_cached(self) -> None:
        """基线 worktree 装依赖失败 → 基线不可信：不写缓存、判失败，baseline.error 写明；依赖能装了再验，重新跑基线、通过、
        这才进缓存。旧版本写进缓存的不可信结果（deps_has_failure 是 true）不再拿来用"""
        selftest_fakes.install_pkg_tools(self.fakebin, sys.executable)
        (self.fakebin / "fake-pkg.json").write_text(json.dumps({"fail": ["pnpm"]}))
        self.base = self.init_repo(self.wt, {
            "package.json": '{"name": "app", "private": true}\n', "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
            ".gitignore": "node_modules/\n", "fake-tsc.json": json.dumps({"output": TSC_OLD_LINE, "exit": 2})})
        entry = self.verify("tsc", TSC_OLD_LINE, "tsc --noEmit")
        self.assertFalse(entry["ok"], entry)
        self.assertIn("基线不可信", entry["baseline"]["error"])
        self.assertIn("装依赖失败", entry["baseline"]["error"])
        self.assertEqual(self.caches(), [])
        (self.fakebin / "fake-pkg.json").write_text(json.dumps({"fail": []}))
        fixed = self.verify("tsc", TSC_OLD_LINE, "tsc --noEmit")
        self.assertTrue(fixed["ok"], fixed)
        self.assertFalse(fixed["baseline"]["cached"])
        [cache] = self.caches()
        old = json.loads(cache.read_text())
        cache.write_text(json.dumps(dict(old, deps_has_failure=True)))  # 旧版本照样会缓存的不可信结果
        again = self.verify("tsc", TSC_OLD_LINE, "tsc --noEmit")
        self.assertTrue(again["ok"], again)
        self.assertFalse(again["baseline"]["cached"])
        self.assertFalse(json.loads(cache.read_text())["deps_has_failure"])

    def test_base_timeout_untrusted_not_cached(self) -> None:
        """基线上命令没跑成（超时）→ 基线不可信：不写缓存、判失败并写明，不是把这次的旧失败全报成新增"""
        self.make("tsc", TSC_OLD_LINE, 2, sleep=5)
        entry = self.verify("tsc", TSC_OLD_LINE, "tsc --noEmit", timeout=1.5)
        self.assertFalse(entry["ok"], entry)
        self.assertIn("基线不可信", entry["baseline"]["error"])
        self.assertIn("没跑成", entry["baseline"]["error"])
        self.assertNotIn("new_failures", entry["baseline"])
        self.assertEqual(self.caches(), [])


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

    def wt_calls(self, tool: str) -> list:
        """不在基线 worktree 里的调用：这边整套那次，vitest 还有单独重跑那次。"""
        return [c["args"] for c in self.tool_calls(tool) if not c["cwd"].startswith(str(self.state / "baselines"))]

    def test_vitest_only_failed_files(self) -> None:
        """vitest：这边先单独重跑失败的文件，基线只跑仍然失败的文件（两边同一条命令：位置参数 src/ 去掉、选项保留）；
        B 没重跑，fixed 不统计它；两边都是单独跑的结果，note 不再提示"单独跑能过的误报\""""
        entry = self.verify("vitest", {"failures": [A, C]}, "vitest run src/ --environment node")
        subset = ["run", "--environment", "node", "src/a.test.ts", "src/c.test.ts"]
        self.assertEqual(self.wt_calls("vitest"), [["run", "src/", "--environment", "node"], subset])
        self.assertEqual(self.base_calls("vitest"), [subset])
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["baseline"]["targets"], ["src/a.test.ts", "src/c.test.ts"])
        self.assertEqual(entry["baseline"]["new_failures"], [KEY_C])
        self.assertEqual(entry["baseline"]["fixed"], [])
        self.assertIn("只重跑了当前失败所在的 2 个文件", entry["note"])
        self.assertNotIn("单独跑", entry["note"])
        self.assertNotIn("flaky_files", entry)

    def test_vitest_flaky_assert_not_new(self) -> None:
        """整套里多出一条断言形态的失败，单独重跑不复现 → 不算新增、判通过，flaky_files 列出这个文件；
        基线只跑仍然失败的两个文件"""
        entry = self.verify("vitest", {"failures": [A, B, dict(C, flaky=True)]}, "vitest run")
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["flaky_files"], ["src/c.test.ts"])
        self.assertEqual(entry["isolated_rerun"]["not_reproduced"], [KEY_C])
        self.assertEqual(entry["isolated_rerun"]["exit_code"], 1)  # A、B 单独跑仍然失败
        self.assertEqual(entry["baseline"]["new_failures"], [])
        self.assertEqual(entry["baseline"]["targets"], ["src/a.test.ts", "src/b.test.ts"])
        self.assertEqual(entry["baseline"]["current_failures"], 2)
        self.assertEqual(self.wt_calls("vitest"), [["run"], ["run", "src/a.test.ts", "src/b.test.ts", "src/c.test.ts"]])
        self.assertEqual(self.base_calls("vitest"), [["run", "src/a.test.ts", "src/b.test.ts"]])
        self.assertIn("没有新增", entry["note"])
        self.assertIn("没复现", entry["note"])

    def test_vitest_flaky_file_level_not_new(self) -> None:
        """整套里一个文件整个失败（import 时抛错），单独重跑不复现 → 不算新增、判通过，flaky_files 列出这个文件"""
        entry = self.verify("vitest", {"failures": [A, {"file": "src/c.test.ts", "kind": "file", "flaky": True}]},
                            "vitest run")
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["flaky_files"], ["src/c.test.ts"])
        self.assertEqual(entry["isolated_rerun"]["not_reproduced"], ["src/c.test.ts [ src/c.test.ts ]"])
        self.assertEqual(entry["baseline"]["new_failures"], [])
        self.assertEqual(entry["baseline"]["targets"], ["src/a.test.ts"])

    def test_vitest_flaky_and_real_new_kept_apart(self) -> None:
        """偶发的（一条断言、一个文件整个失败）和真新增的同时出现 → 判失败；new_failures 只有单独跑也失败、基线上没有的那条，
        偶发的两个文件在 flaky_files 里"""
        flaky_assert = {"file": "src/b.test.ts", "test": "str > pads", "kind": "assert", "flaky": True}
        flaky_file = {"file": "src/ok.test.ts", "kind": "file", "flaky": True}
        entry = self.verify("vitest", {"failures": [A, flaky_assert, flaky_file, C]}, "vitest run")
        self.assertFalse(entry["ok"], entry)
        self.assertEqual(entry["flaky_files"], ["src/b.test.ts", "src/ok.test.ts"])
        self.assertEqual(entry["baseline"]["new_failures"], [KEY_C])
        self.assertEqual(entry["baseline"]["targets"], ["src/a.test.ts", "src/c.test.ts"])
        self.assertEqual(self.base_calls("vitest"), [["run", "src/a.test.ts", "src/c.test.ts"]])
        self.assertIn("没复现", entry["note"])

    def test_vitest_all_flaky_skips_baseline(self) -> None:
        """整套里的失败单独重跑全都不复现 → 直接判通过，不和基线比、不建基线 worktree"""
        entry = self.verify("vitest", {"failures": [dict(C, flaky=True)]}, "vitest run")
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["flaky_files"], ["src/c.test.ts"])
        self.assertNotIn("baseline", entry)
        self.assertNotIn("compare_baseline", entry)
        self.assertEqual(self.base_calls("vitest"), [])
        self.assertFalse((self.state / "baselines").exists())

    def test_vitest_flaky_does_not_change_baseline_targets(self) -> None:
        """每次偶发的文件不一样，基线的目标文件还是稳定失败的那两个 → 第二次走缓存，基线只跑一次"""
        first = self.verify("vitest", {"failures": [A, B, dict(C, flaky=True)]}, "vitest run")
        second = self.verify("vitest", {"failures": [A, B, {"file": "src/ok.test.ts", "kind": "file", "flaky": True}]},
                             "vitest run")
        self.assertFalse(first["baseline"]["cached"])
        self.assertTrue(second["ok"], second)
        self.assertTrue(second["baseline"]["cached"])
        self.assertEqual(second["flaky_files"], ["src/ok.test.ts"])
        self.assertEqual(self.base_calls("vitest"), [["run", "src/a.test.ts", "src/b.test.ts"]])

    def test_vitest_unusable_rerun_compares_full_run(self) -> None:
        """重跑时点名的文件没跑全（没跑到的不能当成没复现）→ 重跑的结果不用，当前侧按整套那次的和基线比：
        整套里才出现的那条照样算新增，note 提示误报怎么判断"""
        entry = self.verify("vitest", {"failures": [A, dict(C, flaky=True)], "rerun_skip": ["src/c.test.ts"]},
                            "vitest run")
        self.assertFalse(entry["ok"], entry)
        self.assertNotIn("flaky_files", entry)
        self.assertIn("点名了 2 个文件", entry["isolated_rerun"]["note"])
        self.assertEqual(entry["baseline"]["new_failures"], [KEY_C])
        self.assertEqual(entry["baseline"]["targets"], ["src/a.test.ts", "src/c.test.ts"])
        self.assertIn("没法用", entry["note"])
        self.assertIn("单独跑", entry["note"])

    def test_vitest_isolation_off_compares_full_run(self) -> None:
        """命令行关了文件隔离 → 不做单独重跑，当前侧按整套那次的和基线比（以前的做法），note 写原因并提示误报怎么判断"""
        entry = self.verify("vitest", {"failures": [A, dict(C, flaky=True)]}, "vitest run --no-isolate")
        self.assertFalse(entry["ok"], entry)
        self.assertNotIn("isolated_rerun", entry)
        self.assertEqual(self.wt_calls("vitest"), [["run", "--no-isolate"]])
        self.assertEqual(entry["baseline"]["new_failures"], [KEY_C])
        self.assertIn("隔离", entry["note"])
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
        只有真新增的算新增；基线照样只点名失败的文件；整套里才出现的失败照样单独重跑、不算新增"""
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
                flaky = self.verify("vitest", {"failures": [A, B, dict(C, kind="timeout", flaky=True)],
                                               "project_labels": labels}, "vitest run")
                self.assertTrue(flaky["ok"], flaky)
                self.assertEqual(flaky["flaky_files"], ["src/c.test.ts"])
                self.assertEqual(self.wt_calls("vitest")[-1], ["run", "src/a.test.ts", "src/b.test.ts", "src/c.test.ts"])

    def test_pytest_only_failed_files(self) -> None:
        """pytest：基线只跑失败 node id 所在的文件（-k 和它的值保留，目录 tests 去掉）"""
        _, _, current, new, _ = OTHER_TOOLS["pytest"]
        entry = self.verify("pytest", {"failures": current}, 'pytest -q -k "not slow" tests')
        self.assertEqual(self.base_calls("pytest"), [["-q", "-k", "not slow", "tests/test_a.py", "tests/test_c.py"]])
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["baseline"]["targets"], ["tests/test_a.py", "tests/test_c.py"])
        self.assertEqual(entry["baseline"]["new_failures"], [new])

    def test_pytest_eslint_not_rerun_and_keep_hint(self) -> None:
        """pytest、eslint 不做单独重跑（pytest 同进程跑，单独跑一个文件和整套跑语义不同；eslint 是确定性的）：这边只跑整条那一次，
        当前侧就是它的失败；有新增落在基线单独跑过的文件里时，note 照旧提示误报怎么判断"""
        for tool, command, calls in (("pytest", "pytest -q tests", [["-q", "tests"]]), ("eslint", "eslint src", [["src"]])):
            with self.subTest(tool):
                extra = "tests/test_a.py::test_new" if tool == "pytest" else ["src/a.ts", "no-undef"]
                entry = self.verify(tool, {"failures": [*OTHER_TOOLS[tool][1], extra]}, command)
                self.assertFalse(entry["ok"], entry)
                self.assertEqual(self.wt_calls(tool), calls)
                self.assertNotIn("isolated_rerun", entry)
                self.assertEqual(len(entry["baseline"]["new_failures"]), 1)
                self.assertIn("单独跑", entry["note"])

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
