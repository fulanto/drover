"""1. vitest 负载超时复核（run_verify / accept --verify）：失败全是超时才把失败文件单独重跑，过了（而且真的跑到了测试）就判通过；
汇总里还有未处理错误时不复核。

用假 vitest（selftest_fakes）：按当前目录的 fake-vitest.json 打印 vitest 格式的失败；参数里带测试文件时是复核，
只跑这些文件（超时单独跑时通过，rerun_fail 里的复核时还失败）。
"""

from __future__ import annotations

import os
import unittest

from selftest_support import DroverTestCase

TIMEOUT_A = {"file": "src/a.test.ts", "test": "math > adds", "kind": "timeout"}
HOOK_TIMEOUT_B = {"file": "src/b.test.ts", "kind": "file-timeout"}
ASSERT_C = {"file": "src/c.test.ts", "test": "math > subtracts", "kind": "assert"}
COMMAND = "vitest run src/ --environment node --maxWorkers 4"


class VitestTimeoutRecheckTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.wt = self.work / "app"
        self.init_repo(self.wt, {f"src/{n}.test.ts": "test('x', () => {})\n" for n in "abc"})
        self.d = self.load_drover()

    def verify(self, spec: dict, command: str = COMMAND) -> dict:
        self.write_spec(self.wt, "vitest", spec)
        return self.d.run_verify(str(self.wt), command, 60, lock=False)

    def test_only_timeouts_recheck_passes(self) -> None:
        """只有超时（用例级 + 文件级 Hook 超时）→ 单独重跑失败文件，通过就判通过并列出文件"""
        entry = self.verify({"failures": [TIMEOUT_A, HOOK_TIMEOUT_B]})
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["exit_code"], 1)  # 保留第一次的退出码
        self.assertEqual(entry["flaky_timeouts"], ["src/a.test.ts", "src/b.test.ts"])
        self.assertEqual(entry["timeout_recheck"]["exit_code"], 0)
        self.assertIn("超时", entry["note"])
        calls = self.tool_calls("vitest")
        self.assertEqual(len(calls), 2)
        # 复核：位置参数 src/ 去掉，选项和选项的值保留，失败文件追加在后面
        self.assertEqual(calls[1]["args"], ["run", "--environment", "node", "--maxWorkers", "4",
                                            "src/a.test.ts", "src/b.test.ts"])
        self.assertEqual(entry["timeout_recheck"]["command"],
                         "vitest run --environment node --maxWorkers 4 src/a.test.ts src/b.test.ts")

    def test_timeout_plus_real_failure_not_rechecked(self) -> None:
        """超时 + 真失败 → 不复核，直接判失败"""
        entry = self.verify({"failures": [TIMEOUT_A, ASSERT_C]})
        self.assertFalse(entry["ok"])
        self.assertNotIn("timeout_recheck", entry)
        self.assertNotIn("flaky_timeouts", entry)
        self.assertEqual(len(self.tool_calls("vitest")), 1)

    def test_recheck_still_failing(self) -> None:
        """只有超时、但单独重跑还失败 → 判失败，复核结果留在 timeout_recheck"""
        entry = self.verify({"failures": [TIMEOUT_A], "rerun_fail": ["src/a.test.ts"]})
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["timeout_recheck"]["exit_code"], 1)
        self.assertEqual(entry["timeout_recheck"]["files"], ["src/a.test.ts"])
        self.assertNotIn("flaky_timeouts", entry)

    def test_count_mismatch_not_rechecked(self) -> None:
        """"Tests N failed" 和解析到的用例级失败数对不上 → 不复核"""
        entry = self.verify({"failures": [TIMEOUT_A], "tests_failed": 3})
        self.assertFalse(entry["ok"])
        self.assertNotIn("timeout_recheck", entry)

    def test_unhandled_error_not_rechecked(self) -> None:
        """失败条目全是超时，但汇总里还有未处理错误（Unhandled Errors，没有 FAIL 行，别的文件抛的）→ 不复核、判失败：
        单独重跑超时的文件能过，也洗不掉同一次运行里的未处理错误"""
        entry = self.verify({"failures": [TIMEOUT_A], "unhandled": 1})
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["exit_code"], 1)
        self.assertNotIn("timeout_recheck", entry)
        self.assertNotIn("flaky_timeouts", entry)
        self.assertEqual(len(self.tool_calls("vitest")), 1)

    def test_recheck_without_tests_not_passed(self) -> None:
        """复核的重跑退出码 0、却一个测试都没跑（No test files found）→ 不算复核通过，判失败，timeout_recheck 写明原因"""
        entry = self.verify({"failures": [TIMEOUT_A], "rerun_no_tests": True})
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["timeout_recheck"]["exit_code"], 0)
        self.assertIn("没有测试真的跑", entry["timeout_recheck"]["note"])
        self.assertNotIn("flaky_timeouts", entry)

    def test_dotdot_paths_become_relative_to_worktree(self) -> None:
        """vitest 打印 ../../.. 开头的路径（root 经过符号链接）→ 复核和 flaky_timeouts 用相对 worktree 的路径"""
        real = os.path.realpath(self.wt)
        prefix = "../" * (len(real.split(os.sep)) + 2) + real.lstrip(os.sep) + "/"
        entry = self.verify({"failures": [TIMEOUT_A], "path_prefix": prefix})
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["flaky_timeouts"], ["src/a.test.ts"])
        self.assertEqual(self.tool_calls("vitest")[1]["args"][-1], "src/a.test.ts")

    def test_passing_run_not_rechecked(self) -> None:
        """全部通过 → 不复核，ok、计数行都在"""
        entry = self.verify({"failures": []})
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["exit_code"], 0)
        self.assertNotIn("timeout_recheck", entry)
        self.assertIn("Tests  10 passed (10)", entry["counts"])

    def test_project_labels_timeouts_recheck(self) -> None:
        """配了 test.projects 的 vitest 4（FAIL 行带 |ts| / |tsx| 标签，或带 ANSI 的色块）：只有超时（用例级、文件级）
        → 照样认出失败的文件、单独重跑，文件路径不带标签"""
        self.write_files(self.wt, {"src/d.test.tsx": "test('x', () => {})\n"})
        timeout_d = {"file": "src/d.test.tsx", "test": "dom > renders", "kind": "timeout"}
        files = ["src/a.test.ts", "src/b.test.ts", "src/d.test.tsx"]
        for labels in ("plain", "color"):
            with self.subTest(labels=labels):
                entry = self.verify({"failures": [TIMEOUT_A, HOOK_TIMEOUT_B, timeout_d], "project_labels": labels})
                self.assertTrue(entry["ok"], entry)
                self.assertEqual(entry["flaky_timeouts"], files)
                self.assertEqual(self.tool_calls("vitest")[-1]["args"],
                                 ["run", "--environment", "node", "--maxWorkers", "4", *files])


class VitestParseUnitTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.d = self.load_drover()

    def test_rerun_argv(self) -> None:
        """vitest_rerun_argv：直接调 vitest 的去掉位置参数、保留选项值；包脚本把文件追加在后面（npm 加 --）"""
        files = ["src/a.test.ts"]
        cases = [
            (["npx", "vitest", "run", "src/", "--environment", "node", "--maxWorkers=4", "--globals", "-t", "adds"],
             ["npx", "vitest", "run", "--environment", "node", "--maxWorkers=4", "--globals", "-t", "adds", *files]),
            (["./node_modules/.bin/vitest", "src/a"], ["./node_modules/.bin/vitest", "run", *files]),
            (["vitest", "--no-file-parallelism", "src", "--dir", "src"],
             ["vitest", "run", "--no-file-parallelism", "--dir", "src", *files]),
            (["bun", "run", "test"], ["bun", "run", "test", *files]),
            (["npm", "test"], ["npm", "test", "--", *files]),
        ]
        for argv, want in cases:
            with self.subTest(argv=argv):
                self.assertEqual(self.d.vitest_rerun_argv(argv, files), want)

    def test_timeout_failures_needs_vitest_banner(self) -> None:
        """没有 vitest 的 RUN 横幅、或没有 FAIL 条目 → 不当成 vitest 超时"""
        self.assertIsNone(self.d.vitest_timeout_failures(" FAIL  src/a.test.ts > x\nTest timed out in 5000ms"))
        self.assertIsNone(self.d.vitest_timeout_failures(" RUN  v1.6.0 /x\n\n Tests  1 failed"))

    def test_timeout_failures_real_project_output(self) -> None:
        """vitest 4.1.7 配了 test.projects 的真实输出（带颜色，标签是色块）：文件级 Hook 超时 + 用例级超时 → 两个文件"""
        text = ("\x1b[1m\x1b[30m\x1b[46m RUN \x1b[49m\x1b[39m\x1b[22m \x1b[36mv4.1.7 \x1b[39m\x1b[90m/x\x1b[39m\n\n"
                "\x1b[31m⎯⎯⎯⎯⎯⎯\x1b[39m\x1b[1m\x1b[41m Failed Suites 1 \x1b[49m\x1b[22m\x1b[31m⎯⎯⎯⎯⎯⎯⎯\x1b[39m\n\n"
                "\x1b[41m\x1b[1m FAIL \x1b[22m\x1b[49m \x1b[30m\x1b[43m ts \x1b[49m\x1b[39m src/b.test.ts"
                "\x1b[2m [ src/b.test.ts ]\x1b[22m\n\x1b[31m\x1b[1mError\x1b[22m: Hook timed out in 300ms.\n\n"
                "\x1b[31m⎯⎯⎯⎯⎯⎯⎯\x1b[39m\x1b[1m\x1b[41m Failed Tests 1 \x1b[49m\x1b[22m\x1b[31m⎯⎯⎯⎯⎯⎯⎯\x1b[39m\n\n"
                "\x1b[41m\x1b[1m FAIL \x1b[22m\x1b[49m \x1b[30m\x1b[45m tsx \x1b[49m\x1b[39m src/c.test.tsx"
                "\x1b[2m > \x1b[22mdom\x1b[2m > \x1b[22mslow render\n\x1b[31m\x1b[1mError\x1b[22m: Test timed out in 300ms.\n\n"
                "\x1b[2m Test Files \x1b[22m \x1b[1m\x1b[31m2 failed\x1b[39m\x1b[22m\x1b[90m (2)\x1b[39m\n"
                "\x1b[2m      Tests \x1b[22m \x1b[1m\x1b[31m1 failed\x1b[39m\x1b[22m\x1b[2m | \x1b[22m\x1b[33m1 skipped\x1b[39m"
                "\x1b[90m (2)\x1b[39m\n")
        self.assertEqual(self.d.vitest_timeout_failures(text), ["src/b.test.ts", "src/c.test.tsx"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
