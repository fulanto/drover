"""1. vitest 偶发失败的复核（run_verify / accept --verify）：整套跑失败后，把失败所在的文件在同一个 worktree 上单独重跑一次，
以单独跑的结果为准。单独跑不复现的不算失败、列进 flaky_files；仍然失败的照常判失败。看的是能不能复现，不看报错文字
（断言、超时、整个文件失败、describe 级套件失败都一样）。整套的输出比不了、失败对不上文件、命令行关了隔离时不重跑；
重跑的结果不能用（没跑到测试、点名的文件没跑全）时按整套那次的算。

用假 vitest（selftest_fakes）：按当前目录的 fake-vitest.json 打印 vitest 格式的失败；参数里带测试文件时只跑这些文件，
flaky 的失败这时不出现。
"""

from __future__ import annotations

import os
import shlex
import sys
import unittest

import selftest_vitest_samples as samples
from selftest_support import DroverTestCase

FLAKY_ASSERT_A = {"file": "src/a.test.ts", "test": "math > adds", "kind": "assert", "flaky": True}
FLAKY_FILE_B = {"file": "src/b.test.ts", "kind": "file", "flaky": True}
REAL_C = {"file": "src/c.test.ts", "test": "math > subtracts", "kind": "assert"}
KEY_A, KEY_B, KEY_C = ("src/a.test.ts > math > adds", "src/b.test.ts [ src/b.test.ts ]",
                       "src/c.test.ts > math > subtracts")
COMMAND = "vitest run src/ --environment node --maxWorkers 4"
RERUN_OPTIONS = ["run", "--environment", "node", "--maxWorkers", "4"]


class VitestIsolatedRerunTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.wt = self.work / "app"
        self.init_repo(self.wt, {f"src/{n}.test.ts": "test('x', () => {})\n" for n in "abc"})
        self.d = self.load_drover()

    def verify(self, spec: dict, command: str = COMMAND) -> dict:
        self.write_spec(self.wt, "vitest", spec)
        return self.d.run_verify(str(self.wt), command, 60, lock=False)

    def test_flaky_assert_and_file_level_pass(self) -> None:
        """整套里一条断言失败、一个文件整个失败，单独重跑都不复现 → 判通过，列出这两个文件和重跑的命令、退出码、计数"""
        entry = self.verify({"failures": [FLAKY_ASSERT_A, FLAKY_FILE_B]})
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["exit_code"], 1)  # 保留第一次的退出码
        self.assertEqual(entry["flaky_files"], ["src/a.test.ts", "src/b.test.ts"])
        rerun = entry["isolated_rerun"]
        self.assertEqual(rerun["files"], ["src/a.test.ts", "src/b.test.ts"])
        self.assertEqual(rerun["exit_code"], 0)
        self.assertEqual(rerun["tests"], {"passed": 10, "failed": 0, "skipped": 0})
        self.assertIn("Test Files  2 passed (2)", rerun["counts"])
        self.assertEqual(rerun["not_reproduced"], [KEY_B, KEY_A])  # 输出里的顺序：套件一节在前
        self.assertIn("没复现", entry["note"])
        calls = self.tool_calls("vitest")
        self.assertEqual(len(calls), 2)
        # 重跑：位置参数 src/ 去掉，选项和选项的值保留，失败文件追加在后面
        self.assertEqual(calls[1]["args"], [*RERUN_OPTIONS, "src/a.test.ts", "src/b.test.ts"])
        self.assertEqual(rerun["command"], "vitest run --environment node --maxWorkers 4 src/a.test.ts src/b.test.ts")

    def test_every_failure_shape_is_rerun(self) -> None:
        """报错是什么形态都重跑：断言、超时、describe 级套件失败、整个文件失败（import 抛错 / 钩子超时）、一条用例两个 error"""
        shapes = [{"test": "math > adds", "kind": "assert"}, {"test": "math > adds", "kind": "timeout"},
                  {"test": "needs setup", "kind": "suite"}, {"kind": "file"}, {"kind": "file-timeout"},
                  {"test": "soft > two", "kind": "assert", "errors": 2}]
        for shape in shapes:
            with self.subTest(shape=shape):
                entry = self.verify({"failures": [dict(shape, file="src/a.test.ts", flaky=True)]})
                self.assertTrue(entry["ok"], entry)
                self.assertEqual(entry["flaky_files"], ["src/a.test.ts"])
                self.assertEqual(entry["isolated_rerun"]["exit_code"], 0)

    def test_flaky_and_real_failure_kept_apart(self) -> None:
        """偶发的和单独跑也失败的同时出现 → 判失败；偶发的两个文件列进 flaky_files，仍失败的那条不在没复现的条目里"""
        entry = self.verify({"failures": [FLAKY_ASSERT_A, FLAKY_FILE_B, REAL_C]})
        self.assertFalse(entry["ok"], entry)
        self.assertEqual(entry["flaky_files"], ["src/a.test.ts", "src/b.test.ts"])
        self.assertEqual(entry["isolated_rerun"]["exit_code"], 1)
        self.assertEqual(entry["isolated_rerun"]["tests"]["failed"], 1)
        self.assertEqual(entry["isolated_rerun"]["not_reproduced"], [KEY_B, KEY_A])
        self.assertIn("没复现", entry["note"])
        self.assertEqual(self.tool_calls("vitest")[1]["args"],
                         [*RERUN_OPTIONS, "src/a.test.ts", "src/b.test.ts", "src/c.test.ts"])

    def test_rerun_still_failing(self) -> None:
        """单独重跑还失败（哪怕报的是超时）→ 判失败，重跑的结果留在 isolated_rerun，没有 flaky_files"""
        entry = self.verify({"failures": [dict(REAL_C, kind="timeout")]})
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["isolated_rerun"]["exit_code"], 1)
        self.assertEqual(entry["isolated_rerun"]["files"], ["src/c.test.ts"])
        self.assertEqual(entry["isolated_rerun"]["not_reproduced"], [])
        self.assertNotIn("flaky_files", entry)
        self.assertNotIn("note", entry)

    def test_unparsed_failure_not_rerun(self) -> None:
        """整套的输出里有解析不出的失败（文件名不带 .test.：vitest 打了 2 条失败、解析出 1 条）→ 不重跑、判失败：
        只重跑认得出的那个文件，会把认不出的那条当成没发生"""
        smoke = {"file": "checks/smoke.check.ts", "test": "smoke", "kind": "assert"}
        entry = self.verify({"failures": [FLAKY_ASSERT_A, smoke]})
        self.assertFalse(entry["ok"])
        self.assertNotIn("isolated_rerun", entry)
        self.assertNotIn("flaky_files", entry)
        self.assertEqual(len(self.tool_calls("vitest")), 1)

    def test_unhandled_error_not_rerun(self) -> None:
        """整套的汇总里还有未处理错误（Unhandled Errors，没有 FAIL 行，对不上文件）→ 不重跑、判失败、note 写原因：
        单独重跑失败的文件能过，也洗不掉同一次运行里的未处理错误"""
        entry = self.verify({"failures": [FLAKY_ASSERT_A], "unhandled": 1})
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["exit_code"], 1)
        self.assertNotIn("isolated_rerun", entry)
        self.assertNotIn("flaky_files", entry)
        self.assertIn("没做单独重跑", entry["note"])
        self.assertEqual(len(self.tool_calls("vitest")), 1)

    def test_stopped_early_not_rerun(self) -> None:
        """命令带 --bail 1、失败到了 1 条（后面的文件没跑）→ 不重跑、判失败：失败的那个文件单独跑能过，也说明不了没跑到的"""
        entry = self.verify({"failures": [dict(FLAKY_ASSERT_A, kind="timeout")]}, "vitest run --bail 1")
        self.assertFalse(entry["ok"], entry)
        self.assertNotIn("isolated_rerun", entry)
        self.assertEqual(len(self.tool_calls("vitest")), 1)

    def test_isolation_off_not_rerun(self) -> None:
        """命令行关了文件隔离（文件之间共享模块状态，单独跑能过说明不了什么）→ 不重跑、判失败、note 写原因"""
        for flag in ("--no-isolate", "--isolate=false", "--poolOptions.threads.isolate=false"):
            with self.subTest(flag=flag):
                calls = len(self.tool_calls("vitest"))
                entry = self.verify({"failures": [dict(FLAKY_ASSERT_A, kind="timeout")]}, f"vitest run {flag}")
                self.assertFalse(entry["ok"], entry)
                self.assertNotIn("isolated_rerun", entry)
                self.assertIn("隔离", entry["note"])
                self.assertEqual(len(self.tool_calls("vitest")), calls + 1)

    def test_wrapped_command_not_rerun(self) -> None:
        """不是直接调 vitest、也不是包脚本的命令（这里是一条只打印 vitest 真实输出的 python）→ 拆不出只跑失败文件的命令，
        不重跑、判失败、note 写原因"""
        self.write_files(self.wt, {"src/suite-hook.test.ts": "// x\n"})
        command = shlex.join([sys.executable, "-c", f"import sys; sys.stdout.write({samples.SUITE_HOOK!r}); sys.exit(1)"])
        entry = self.d.run_verify(str(self.wt), command, 60, lock=False)
        self.assertFalse(entry["ok"])
        self.assertNotIn("isolated_rerun", entry)
        self.assertIn("没做单独重跑", entry["note"])

    def test_rerun_without_tests_not_passed(self) -> None:
        """重跑退出码 0、却一个测试都没跑（No test files found）→ 重跑的结果不能用，判失败，isolated_rerun 写明原因"""
        entry = self.verify({"failures": [FLAKY_ASSERT_A], "rerun_no_tests": True})
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["isolated_rerun"]["exit_code"], 0)
        self.assertIn("没有测试真的跑", entry["isolated_rerun"]["note"])
        self.assertNotIn("not_reproduced", entry["isolated_rerun"])
        self.assertNotIn("flaky_files", entry)
        self.assertIn("没法用", entry["note"])

    def test_rerun_missing_named_files_not_trusted(self) -> None:
        """点名了 2 个文件、重跑只跑了 1 个（--shard 之类把点名的文件筛掉了）→ 没跑到的不能当成没复现：重跑的结果不能用，
        判失败，没有 flaky_files"""
        entry = self.verify({"failures": [FLAKY_ASSERT_A, FLAKY_FILE_B], "rerun_skip": ["src/b.test.ts"]})
        self.assertFalse(entry["ok"], entry)
        self.assertEqual(entry["isolated_rerun"]["exit_code"], 0)
        self.assertIn("点名了 2 个文件", entry["isolated_rerun"]["note"])
        self.assertNotIn("flaky_files", entry)
        self.assertIn("没法用", entry["note"])

    def test_dotdot_paths_become_relative_to_worktree(self) -> None:
        """vitest 打印 ../../.. 开头的路径（root 经过符号链接）→ 重跑和 flaky_files 用相对 worktree 的路径"""
        real = os.path.realpath(self.wt)
        prefix = "../" * (len(real.split(os.sep)) + 2) + real.lstrip(os.sep) + "/"
        entry = self.verify({"failures": [FLAKY_ASSERT_A], "path_prefix": prefix})
        self.assertTrue(entry["ok"], entry)
        self.assertEqual(entry["flaky_files"], ["src/a.test.ts"])
        self.assertEqual(self.tool_calls("vitest")[1]["args"][-1], "src/a.test.ts")

    def test_passing_run_not_rerun(self) -> None:
        """全部通过 → 不重跑，ok、计数行都在"""
        entry = self.verify({"failures": []})
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["exit_code"], 0)
        self.assertNotIn("isolated_rerun", entry)
        self.assertIn("Tests  10 passed (10)", entry["counts"])
        self.assertEqual(len(self.tool_calls("vitest")), 1)

    def test_project_labels(self) -> None:
        """配了 test.projects 的 vitest 4（FAIL 行带 |ts| / |tsx| 标签，或带 ANSI 的色块；横幅、分隔线也带颜色）
        → 照样认出失败的文件、单独重跑，文件路径不带标签"""
        self.write_files(self.wt, {"src/d.test.tsx": "test('x', () => {})\n"})
        flaky_d = {"file": "src/d.test.tsx", "test": "dom > renders", "kind": "timeout", "flaky": True}
        files = ["src/a.test.ts", "src/b.test.ts", "src/d.test.tsx"]
        for labels in ("plain", "color"):
            with self.subTest(labels=labels):
                entry = self.verify({"failures": [FLAKY_ASSERT_A, FLAKY_FILE_B, flaky_d], "project_labels": labels})
                self.assertTrue(entry["ok"], entry)
                self.assertEqual(entry["flaky_files"], files)
                self.assertEqual(self.tool_calls("vitest")[-1]["args"], [*RERUN_OPTIONS, *files])


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

    def test_isolation_off(self) -> None:
        """vitest_isolation_off：只认命令行上看得出的写法；--no-file-parallelism、--isolate 不算关"""
        off = (["vitest", "run", "--no-isolate"], ["bunx", "vitest", "run", "--isolate=false", "src"],
               ["vitest", "--poolOptions.forks.isolate=false"], ["vitest", "run", "--browser.isolate=false"],
               ["bun", "run", "test", "--no-isolate"])
        on = (["vitest", "run"], ["vitest", "run", "--isolate"], ["vitest", "run", "--no-file-parallelism"],
              ["vitest", "run", "--isolate=true"], ["vitest", "run", "-t", "no-isolate"])
        for argv in off:
            self.assertTrue(self.d.vitest_isolation_off(argv), argv)
        for argv in on:
            self.assertFalse(self.d.vitest_isolation_off(argv), argv)

    def test_failed_files_need_vitest_banner(self) -> None:
        """没有 vitest 的 RUN 横幅 → 不当成 vitest 的输出"""
        self.assertEqual(self.d.parse_failures(["make", "check"], " FAIL  src/a.test.ts > x\nTest timed out in 5000ms", "."),
                         (None, None))

    def test_real_project_output(self) -> None:
        """vitest 4.1.7 配了 test.projects 的真实输出（带颜色，标签是色块；节选，没有分隔线）：一个文件整个失败 + 一条用例失败
        → 两个条目、两个文件，路径不带标签"""
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
        entries = self.d.vitest_failure_entries(self.d.ANSI_RE.sub("", text), ".")
        self.assertEqual(entries, [("src/b.test.ts [ src/b.test.ts ]", "src/b.test.ts"),
                                   ("src/c.test.tsx > dom > slow render", "src/c.test.tsx")])
        self.assertIsNone(self.d.reported_mismatch("vitest", text, self.d.Counter()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
