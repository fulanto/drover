"""5. heavy：经调度执行（状态在 DROVER_STATE_DIR/locks/heavy/ 下）。整机实测满了时第二条排队、打印在跑的那条；
accept --verify 进同一套调度。只执行构建 / 测试工具（跳过开头的 env 和 VAR=值 之后看程序名），其他命令不执行、
退出码 2。按资源调度的其他行为（按实测放行、轻命令同时跑、小命令、防饿死、回收、--status）在 test_heavy_sched.py。
整机实测是假采样文件（selftest_support 的 set_cpu_busy），不跟着本机负载变。

持有者是 hold.py：开始时写 <名字>.start，wait 模式下等测试放行（<名字>.release）才结束，结束写 <名字>.end。
排队与否看事件顺序（第二条打印"排队中"时第一条还没放行），不靠 sleep 估时间。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from selftest_support import DROVER, DroverTestCase, wait_for

HOLD = """import sys, time
from pathlib import Path
name, root, mode = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
(root / (name + ".start")).write_text(repr(time.time()))
deadline = time.time() + 120
while mode == "wait" and not (root / (name + ".release")).exists() and time.time() < deadline:
    time.sleep(0.05)
(root / (name + ".end")).write_text(repr(time.time()))
"""


class HeavyTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.hold = self.tmp / "hold.py"
        self.hold.write_text(HOLD)
        self.marks = self.tmp / "marks"
        self.marks.mkdir()
        self.heavy = self.state / "locks" / "heavy"

    def start(self, name: str, mode: str) -> subprocess.Popen:
        err = (self.tmp / f"{name}.stderr").open("w")
        p = subprocess.Popen([sys.executable, str(DROVER), "heavy", "--cwd", str(self.tmp), "--",
                              sys.executable, str(self.hold), name, str(self.marks), mode],
                             env=self.env, cwd=str(self.tmp), stdout=subprocess.DEVNULL, stderr=err)
        err.close()
        self.addCleanup(lambda: p.poll() is None and p.kill())
        return p

    def stderr(self, name: str) -> str:
        return (self.tmp / f"{name}.stderr").read_text()

    def mark(self, name: str, kind: str) -> float:
        return float((self.marks / f"{name}.{kind}").read_text())

    def test_second_waits_and_prints_holder(self) -> None:
        """整机实测满了（假采样忙到逻辑核数）：第二条排队、打印在跑的那条（pid 和命令），第一条结束后才开始（没有 heavy 在跑时
        总放一条）；跑完票都退掉"""
        self.set_cpu_busy(os.cpu_count() or 1)
        first = self.start("first", "wait")
        self.assertTrue(wait_for(lambda: (self.marks / "first.start").exists()), "第一条没有放行开始执行")
        (ticket,) = [json.loads(p.read_text()) for p in self.heavy.glob("run-*.json")]
        self.assertEqual((ticket["pid"], ticket["state"]), (first.pid, "running"))
        self.assertIn("hold.py first", ticket["command"])
        second = self.start("second", "go")
        self.assertTrue(wait_for(lambda: "排队中" in self.stderr("second")), self.stderr("second"))
        self.assertFalse((self.marks / "second.start").exists(), "第二条没排队就开始了")
        (self.marks / "first.release").write_text("")
        self.assertEqual(first.wait(60), 0)
        self.assertEqual(second.wait(60), 0)
        err = self.stderr("second")
        self.assertIn(f"在跑：pid {first.pid} ", err)
        self.assertIn("hold.py first", err)
        self.assertIn("开始执行", err)
        self.assertGreaterEqual(self.mark("second", "start"), self.mark("first", "end"))
        self.assertNotIn("排队中", self.stderr("first"))
        self.assertEqual(list(self.heavy.glob("run-*.json")), [])

    def test_exit_code_and_output_passthrough(self) -> None:
        """输出原样透传，退出码同被执行的命令；找不到命令 127；没给命令 2"""
        p = self.drover("heavy", "--", sys.executable, "-c", "print('hi'); raise SystemExit(3)", rc=3)
        self.assertEqual(p.stdout, "hi\n")
        missing = self.drover("heavy", "--", str(self.tmp / "no-such-dir" / "mvn"), rc=127)
        self.assertIn("找不到命令", missing.stderr)
        self.drover("heavy", rc=2)
        self.drover("heavy", "--", "env", "JAVA_HOME=/x", rc=2)  # 只有赋值、没有命令

    def fake_tool(self, path: Path, label: str) -> Path:
        """写一个假的构建 / 测试工具：打印自己的名字、cwd、JAVA_HOME 和参数。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'#!/bin/sh\necho "{label} cwd=$(pwd -P) JAVA_HOME=$JAVA_HOME args=$*"\n')
        path.chmod(0o755)
        return path

    def test_build_tools_run(self) -> None:
        """名单：env JAVA_HOME=x mvn …、开头直接是 JAVA_HOME=x（shell 原样传进来，heavy 自己放进环境）、
        ./.venv/bin/python -m pytest …（相对 --cwd 的路径，程序名取 basename）、node <脚本>（文档的 CDP 检查）都照常排队执行。
        node 用假脚本，不依赖本机装了 node"""
        mvn = self.fake_tool(self.tmp / "tools" / "mvn", "mvn")
        node = self.fake_tool(self.tmp / "tools" / "node", "node")
        proj = self.tmp / "proj"
        self.fake_tool(proj / ".venv" / "bin" / "python", "python")
        self.env.pop("JAVA_HOME", None)  # 本机环境里可能设了
        p = self.drover("heavy", "--", "env", "JAVA_HOME=/opt/jdk17", str(mvn), "-o", "-q", "test")
        self.assertEqual(p.stdout, f"mvn cwd={self.tmp} JAVA_HOME=/opt/jdk17 args=-o -q test\n")
        p = self.drover("heavy", "--", "JAVA_HOME=/opt/jdk21", str(mvn), "-o", "test")
        self.assertEqual(p.stdout, f"mvn cwd={self.tmp} JAVA_HOME=/opt/jdk21 args=-o test\n")
        p = self.drover("heavy", "--cwd", str(proj), "--", "./.venv/bin/python", "-m", "pytest", "tests/test_x.py", "-q")
        self.assertEqual(p.stdout, f"python cwd={proj} JAVA_HOME= args=-m pytest tests/test_x.py -q\n")
        p = self.drover("heavy", "--", str(node), "verify-slides-cdp.mjs", "all")
        self.assertEqual(p.stdout, f"node cwd={self.tmp} JAVA_HOME= args=verify-slides-cdp.mjs all\n")
        self.assertTrue((self.heavy / "history.json").is_file())  # 进了调度，记下了实测

    def test_other_commands_rejected(self) -> None:
        """不是构建 / 测试工具的不执行：退出码 2，stderr 说明 heavy 只排队跑构建 / 测试、其他命令直接执行；
        git push、rm、bash -c、env bash -c、VAR=值 rm 都一样，命令没有跑（文件还在、标记没写），也没进调度"""
        victim = self.tmp / "keep.txt"
        victim.write_text("x")
        marker = self.tmp / "marker"
        for argv in (["git", "push", "origin", "main"], ["rm", str(victim)], ["/bin/rm", "-f", str(victim)],
                     ["bash", "-c", f"touch {marker}"], ["env", "bash", "-c", f"touch {marker}"],
                     ["env", "-i", "mvn", "-v"], ["FOO=1", "rm", str(victim)]):
            with self.subTest(argv[0]):
                p = self.drover("heavy", "--", *argv, rc=2)
                self.assertIn("heavy 只用来排队跑构建 / 测试", p.stderr)
                self.assertIn("其他命令直接执行", p.stderr)
                self.assertEqual(p.stdout, "")
        self.assertTrue(victim.exists())
        self.assertFalse(marker.exists())
        self.assertFalse(self.heavy.exists())

    def test_allowed_names(self) -> None:
        """程序名判断：basename 在名单里、python3.N 也算；其他（含 python3x 这类）不算"""
        d = self.load_drover()
        for argv, want in ((["./mvnw", "-o"], ("mvnw", ["./mvnw", "-o"], {})),
                           (["A=1", "B=2", "npx", "vitest"], ("npx", ["npx", "vitest"], {"A": "1", "B": "2"})),
                           (["/usr/bin/env", "A=1", "go", "test"], ("go", ["/usr/bin/env", "A=1", "go", "test"], {})),
                           ([], (None, [], {}))):
            self.assertEqual(d.heavy_split(argv), want)
        for prog, ok in (("python3.12", True), ("python3", True), ("pytest", True), ("gradlew", True), ("node", True),
                         ("python3x", False), ("nodejs", False), ("sh", False), ("curl", False)):
            self.assertEqual(d.heavy_allowed(prog), ok, prog)

    def test_unwritable_lock_runs_without_queue(self) -> None:
        """调度目录建不了（codex 沙箱不让写）→ 不排队直接执行，并提示"""
        if os.geteuid() == 0:
            self.skipTest("root 无视目录权限，模拟不了状态目录不可写")
        locks = self.heavy.parent
        locks.mkdir(parents=True)
        locks.chmod(0o500)
        self.addCleanup(locks.chmod, 0o700)
        p = self.drover("heavy", "--", sys.executable, "-c", "pass")
        self.assertIn("不可写", p.stderr)
        self.assertIn("不排队直接执行", p.stderr)
        self.assertFalse(self.heavy.exists())

    def test_accept_verify_queues_on_same_lock(self) -> None:
        """accept --verify 的 run_verify 默认进同一套调度：整机实测满了、有别的在跑时等着，放行后才跑，记下排队秒数"""
        wt = self.work / "app"
        self.init_repo(wt, {"README.md": "x\n"})
        self.write_spec(wt, "vitest", {"failures": []})
        self.set_cpu_busy(os.cpu_count() or 1)
        d = self.load_drover()
        first = self.start("first", "wait")
        self.assertTrue(wait_for(lambda: (self.marks / "first.start").exists()))
        result: dict = {}
        worker = threading.Thread(target=lambda: result.update(d.run_verify(str(wt), "vitest run", 60)))
        worker.start()
        time.sleep(1.0)
        self.assertTrue(worker.is_alive())
        self.assertEqual(self.tool_calls("vitest"), [], "持有者还没放行，verify 不该开始")
        (self.marks / "first.release").write_text("")
        worker.join(60)
        self.assertEqual(first.wait(60), 0)
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(result["lock_waited_seconds"], 0.5)
        self.assertEqual(len(self.tool_calls("vitest")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
