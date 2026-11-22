"""deps：worktree 里已有依赖时，依赖清单（lockfile、各 package.json、安装配置、安装命令）自上次 drover 安装以来没变才跳过，
变了（或者不是 drover 装的）照常严格安装；软链不经它装；--dry-run 只报会不会装。

PATH 最前面是假 pnpm / npm / uv（selftest_fakes.FAKE_PKG）：调用记进 pkg-calls.log，"包管理器没被调用"以它为证；
装出来的 node_modules / .venv 里有 fake-installed（第几次装的）。源仓库是 tmp/work/app，worktree 用 git worktree add 建在
tmp/wt（不经 herdr）。integrate 端到端的用例用 selftest_repos 的仓库布局。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import selftest_fakes
from selftest_repos import IntegrationCase
from selftest_support import DROVER, DroverTestCase

GITIGNORE = "node_modules/\n.venv/\n"
PNPM_REPO = {
    ".gitignore": GITIGNORE,
    "package.json": '{"name": "root", "private": true}\n',
    "pnpm-workspace.yaml": "packages:\n  - packages/*\n",
    "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
    "packages/a/package.json": '{"name": "a", "version": "1.0.0"}\n',
}
# drover 记在 node_modules / .venv 里的安装记录（只有模拟旧版 drover 记的命令时直接改它）
STAMP = ".drover-deps.json"


class DepsCase(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        selftest_fakes.install_pkg_tools(self.fakebin, sys.executable)
        self.src = self.work / "app"
        self.wt = self.tmp / "wt"

    def make_repo(self, files: dict) -> None:
        """源仓库提交 files，从它建 worktree（分支 task/x）。"""
        self.init_repo(self.src, files)
        self.git(self.src, "worktree", "add", "-q", "-b", "task/x", str(self.wt))

    def deps(self, *extra: str) -> dict:
        return self.drover_json("deps", "--repo", str(self.src), "--wt", str(self.wt), *extra)

    def deps_item(self, kind: str, *extra: str) -> dict:
        items = [i for i in self.deps(*extra)["items"] if i["kind"] == kind]
        self.assertEqual(len(items), 1, items)
        return items[0]

    def calls(self, tool: str) -> list:
        return [r["args"] for r in self.call_rows() if r["tool"] == tool]

    def call_rows(self) -> list:
        """假包管理器的每次调用：{tool, args, stdin}（stdin 的取值见 selftest_fakes.FAKE_PKG）。"""
        log = self.fakebin / "pkg-calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def deps_on_tty(self) -> dict:
        """stdin 是伪终端时跑 deps（编排者在 Codex 的 exec_command 里跑 integrate 就是这样：stdin 是 PTY）。"""
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        p = subprocess.run([sys.executable, str(DROVER), "deps", "--repo", str(self.src), "--wt", str(self.wt)],
                           env=self.env, stdin=slave, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def fail_tools(self, *tools: str, args: tuple = ()) -> None:
        """之后这些包管理器的每次调用、以及带着 args 里某个参数的调用都失败（见 selftest_fakes.FAKE_PKG）。"""
        (self.fakebin / "fake-pkg.json").write_text(json.dumps({"fail": list(tools), "fail_args": list(args)}))

    def pnpm_install(self) -> list:
        return ["install", "--dir", str(self.wt), "--frozen-lockfile", "--config.confirm-modules-purge=false"]


class UnattendedTests(DepsCase):
    def test_package_managers_never_wait_for_input(self) -> None:
        """deps 是无人值守的一步：调用方的 stdin 是终端时，包管理器收到的 stdin 也是马上 EOF 的（pnpm / uv 的每一条，含
        复刻源 venv 时的 uv pip freeze），不会停在交互确认上；pnpm 带 confirm-modules-purge=false（要清空 node_modules 时直接清空重装）"""
        self.make_repo({**PNPM_REPO, "requirements.txt": "requests\n"})
        self.write_files(self.src, {".venv/bin/python": "#!/bin/sh\n", ".venv/pyvenv.cfg": "version = 3.12.1\n"})
        res = self.deps_on_tty()
        self.assertEqual([(i["kind"], i["action"]) for i in res["items"]], [("js", "installed"), ("python", "installed")])
        rows = self.call_rows()
        self.assertEqual([r["tool"] for r in rows], ["pnpm"] + ["uv"] * 5)
        self.assertEqual([r["stdin"] for r in rows], ["eof"] * 6, rows)
        self.assertEqual(self.calls("pnpm"), [self.pnpm_install()])


class JsDepsTests(DepsCase):
    def test_unchanged_skips_without_calling_package_manager(self) -> None:
        """第一次装；什么都没变再跑：跳过，摘要写"没变"，pnpm 没再被调用"""
        self.make_repo(PNPM_REPO)
        first = self.deps_item("js")
        self.assertEqual(first["action"], "installed")
        self.assertEqual(self.calls("pnpm"), [self.pnpm_install()])
        again = self.deps_item("js")
        self.assertEqual(again["action"], "skipped")
        self.assertIn("没变", again["summary"])
        self.assertEqual(len(self.calls("pnpm")), 1)

    def test_lockfile_change_reinstalls(self) -> None:
        """lockfile 变了：在已有 node_modules 上照原来的严格命令重装，摘要写出是 lockfile 变了；装完再跑跳过"""
        self.make_repo(PNPM_REPO)
        self.deps()
        (self.wt / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\nimporters: {}\n")
        res = self.deps_item("js")
        self.assertEqual(res["action"], "installed")
        self.assertIn("pnpm-lock.yaml", res["summary"])
        self.assertEqual(self.calls("pnpm"), [self.pnpm_install()] * 2)
        self.assertEqual((self.wt / "node_modules" / "fake-installed").read_text(), "2")
        self.assertEqual(self.deps_item("js")["action"], "skipped")

    def test_new_workspace_package_reinstalls(self) -> None:
        """新加一个工作区包（多出一个 package.json，lockfile 没动）：重装，摘要点名新包的 package.json"""
        self.make_repo(PNPM_REPO)
        self.deps()
        self.write_files(self.wt, {"packages/b/package.json": '{"name": "b", "dependencies": {"a": "workspace:*"}}\n'})
        res = self.deps_item("js")
        self.assertEqual(res["action"], "installed")
        self.assertIn("packages/b/package.json", res["summary"])
        self.assertEqual(len(self.calls("pnpm")), 2)

    def test_package_json_under_node_modules_does_not_count(self) -> None:
        """node_modules 里的 package.json（装出来的包）不算依赖清单：装完它们多了，再跑照样跳过"""
        self.make_repo(PNPM_REPO)
        self.deps()
        self.write_files(self.wt, {"node_modules/left-pad/package.json": "{}\n",
                                   "packages/a/node_modules/x/package.json": "{}\n"})
        self.assertEqual(self.deps_item("js")["action"], "skipped")

    def test_existing_node_modules_without_record_installs(self) -> None:
        """已有 node_modules、但不是 drover 装的（没有安装记录）：照常严格安装，摘要写原因"""
        self.make_repo(PNPM_REPO)
        self.write_files(self.wt, {"node_modules/left-pad/index.js": "module.exports = 1\n"})
        res = self.deps_item("js")
        self.assertEqual(res["action"], "installed")
        self.assertIn("没有 drover 的安装记录", res["summary"])
        self.assertEqual(self.calls("pnpm"), [self.pnpm_install()])
        self.assertEqual(self.deps_item("js")["action"], "skipped")

    def test_install_command_change_reinstalls(self) -> None:
        """drover 改了安装参数（上次记的命令和这次的不一样）：重装，摘要写安装命令变了"""
        self.make_repo(PNPM_REPO)
        self.deps()
        stamp = self.wt / "node_modules" / STAMP
        data = json.loads(stamp.read_text())
        data["command"] = ["pnpm", "install", "--dir", str(self.wt)]  # 模拟旧版 drover 不带 --frozen-lockfile
        stamp.write_text(json.dumps(data))
        res = self.deps_item("js")
        self.assertEqual(res["action"], "installed")
        self.assertIn("安装命令", res["summary"])
        self.assertEqual(len(self.calls("pnpm")), 2)

    def test_failed_install_is_retried_next_time(self) -> None:
        """安装失败不记安装记录：第一次失败 → 下次重试；装好后 lockfile 变了又失败 → 下次还重试，不会当成没变跳过；
        失败之后 lockfile 改回装好时的样子也重装（失败的那次可能已经动过 node_modules，不能再按旧记录跳过）"""
        self.make_repo(PNPM_REPO)
        self.fail_tools("pnpm")
        self.assertEqual(self.deps_item("js")["action"], "failed")
        self.fail_tools()
        self.assertEqual(self.deps_item("js")["action"], "installed")
        (self.wt / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\nimporters: {}\n")
        self.fail_tools("pnpm")
        failed = self.deps_item("js")
        self.assertEqual(failed["action"], "failed")
        self.assertIn("pnpm-lock.yaml", failed["reason"])
        self.fail_tools()
        self.assertEqual(self.deps_item("js")["action"], "installed")
        self.assertEqual(len(self.calls("pnpm")), 4)
        (self.wt / "pnpm-lock.yaml").write_text(PNPM_REPO["pnpm-lock.yaml"])
        self.fail_tools("pnpm")
        self.assertEqual(self.deps_item("js")["action"], "failed")
        (self.wt / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\nimporters: {}\n")
        self.fail_tools()
        self.assertEqual(self.deps_item("js")["action"], "installed")
        self.assertEqual(len(self.calls("pnpm")), 6)

    def test_symlinked_node_modules_is_not_installed_through(self) -> None:
        """node_modules 是软链（指向源仓库的）：不调用包管理器、不往链接目标里写东西，摘要写清软链指向哪、怎么独立安装；
        链接目标不在了（断链）也一样"""
        self.make_repo(PNPM_REPO)
        target = self.src / "node_modules"
        self.write_files(target, {"left-pad/index.js": "module.exports = 1\n"})
        os.symlink(target, self.wt / "node_modules")
        res = self.deps_item("js")
        self.assertEqual(res["action"], "skipped")
        self.assertIn("软链", res["summary"])
        self.assertIn(str(target), res["summary"])
        self.assertIn("删掉", res["summary"])
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["left-pad"])
        (self.wt / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\nimporters: {}\n")
        self.assertEqual(self.deps_item("js")["action"], "skipped")
        (target / "left-pad" / "index.js").unlink()
        (target / "left-pad").rmdir()
        target.rmdir()
        self.assertEqual(self.deps_item("js")["action"], "skipped")
        self.assertFalse(target.exists())
        self.assertEqual(self.calls("pnpm"), [])

    def test_npm_uses_ci_and_skips_when_unchanged(self) -> None:
        """package-lock.json 的仓库走 npm ci；没变再跑跳过（npm ci 会删掉重装，所以也要靠安装记录）；lockfile 变了再 npm ci"""
        self.make_repo({".gitignore": GITIGNORE, "package.json": '{"name": "app"}\n',
                        "package-lock.json": '{"lockfileVersion": 3}\n'})
        self.assertEqual(self.deps_item("js")["action"], "installed")
        self.assertEqual(self.deps_item("js")["action"], "skipped")
        (self.wt / "package-lock.json").write_text('{"lockfileVersion": 3, "packages": {}}\n')
        self.assertEqual(self.deps_item("js")["action"], "installed")
        self.assertEqual(self.calls("npm"), [["ci", "--prefix", str(self.wt)]] * 2)
        self.assertEqual(self.deps_item("js")["action"], "skipped")

    def test_dry_run_reports_without_recording(self) -> None:
        """--dry-run 不装、不记：没装过 → 报会装；装过没变 → 报跳过；lockfile 变了 → 报会装（写原因），连跑两次都一样，
        之后真跑才装"""
        self.make_repo(PNPM_REPO)
        fresh = self.deps_item("js", "--dry-run")
        self.assertEqual(fresh["action"], "installed")
        self.assertFalse((self.wt / "node_modules").exists())
        self.assertEqual(self.calls("pnpm"), [])
        self.deps()
        self.assertEqual(self.deps_item("js", "--dry-run")["action"], "skipped")
        (self.wt / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\nimporters: {}\n")
        for _ in range(2):
            res = self.deps_item("js", "--dry-run")
            self.assertEqual(res["action"], "installed")
            self.assertIn("pnpm-lock.yaml", res["reason"])
        self.assertEqual(len(self.calls("pnpm")), 1)
        self.assertEqual(self.deps_item("js")["action"], "installed")
        self.assertEqual(len(self.calls("pnpm")), 2)


class PythonDepsTests(DepsCase):
    UV_REPO = {".gitignore": GITIGNORE, "pyproject.toml": "[project]\nname = 'app'\n", "uv.lock": "version = 1\n"}

    def test_uv_lock_change_resyncs_existing_venv(self) -> None:
        """有 uv.lock：第一次 uv sync --frozen；没变再跑跳过、uv 没被调用；uv.lock 变了在已有 .venv 上重跑 uv sync --frozen"""
        self.make_repo(self.UV_REPO)
        sync = ["sync", "--frozen", "--project", str(self.wt)]
        self.assertEqual(self.deps_item("python")["action"], "installed")
        self.assertEqual(self.calls("uv"), [sync])
        again = self.deps_item("python")
        self.assertEqual(again["action"], "skipped")
        self.assertIn("没变", again["summary"])
        self.assertEqual(len(self.calls("uv")), 1)
        (self.wt / "uv.lock").write_text("version = 1\n[[package]]\nname = 'requests'\n")
        res = self.deps_item("python")
        self.assertEqual(res["action"], "installed")
        self.assertIn("uv.lock", res["summary"])
        self.assertEqual(self.calls("uv"), [sync] * 2)
        self.assertEqual((self.wt / ".venv" / "fake-installed").read_text(), "2")

    def test_existing_venv_without_record_syncs(self) -> None:
        """有 uv.lock、已有 .venv 但不是 drover 建的：照常 uv sync --frozen（在已有 .venv 上）"""
        self.make_repo(self.UV_REPO)
        self.write_files(self.wt, {".venv/pyvenv.cfg": "version = 3.12.1\n"})
        self.assertEqual(self.deps_item("python")["action"], "installed")
        self.assertEqual(self.calls("uv"), [["sync", "--frozen", "--project", str(self.wt)]])
        self.assertTrue((self.wt / ".venv" / "pyvenv.cfg").is_file())

    def test_requirements_change_tops_up_without_rebuilding(self) -> None:
        """复刻源仓库 venv 那条路：第一次全建（源 venv 里可编辑安装的本仓库改指向 worktree）；没变再跑跳过；
        requirements.txt 变了只在已有 .venv 上 uv pip install -r 补装，不重建（没有 uv venv、.venv 里原有的文件还在）"""
        self.make_repo({".gitignore": GITIGNORE, "requirements.txt": "requests\n"})
        self.write_files(self.src, {".venv/bin/python": "#!/bin/sh\n", ".venv/pyvenv.cfg": "version = 3.12.1\n"})
        self.assertEqual(self.deps_item("python")["action"], "installed")
        wt_py = str(self.wt / ".venv" / "bin" / "python")
        calls = self.calls("uv")
        self.assertEqual([c[:2] for c in calls],
                         [["venv", "--seed"], ["pip", "freeze"], ["pip", "install"], ["pip", "install"], ["pip", "install"]])
        self.assertEqual(calls[0], ["venv", "--seed", "--python", "3.12.1", str(self.wt / ".venv")])
        self.assertEqual(calls[2], ["pip", "install", "--python", wt_py, "-r", str(self.wt / ".venv" / "base-freeze.txt")])
        self.assertEqual(calls[4], ["pip", "install", "--python", wt_py, "-e", str(self.wt)])
        self.assertEqual(self.deps_item("python")["action"], "skipped")
        self.assertEqual(len(self.calls("uv")), 5)
        (self.wt / ".venv" / "keep.txt").write_text("worker 装过的东西\n")
        (self.wt / "requirements.txt").write_text("requests\nrich\n")
        res = self.deps_item("python")
        self.assertEqual(res["action"], "installed")
        self.assertIn("requirements.txt", res["summary"])
        self.assertEqual(self.calls("uv")[5:], [["pip", "install", "--python", wt_py, "-r",
                                                 str(self.wt / "requirements.txt")]])
        self.assertTrue((self.wt / ".venv" / "keep.txt").is_file())
        self.assertEqual(self.deps_item("python")["action"], "skipped")

    def test_editable_repoint_failure_reports_failed(self) -> None:
        """复刻源 venv 时，把源 venv 里可编辑安装的本仓库改指向 worktree 那一步失败：这一项报失败（has_failure 为真），
        摘要写明哪一个没装上和错误尾部，不说"已建好"——复刻时 -e 行是去掉的，改指失败 worktree 的环境里就没有本项目这个包，
        worker 跑测试才撞上 ImportError；没做完不记安装记录（审核 LC-15）"""
        self.make_repo({".gitignore": GITIGNORE, "requirements.txt": "requests\n"})
        self.write_files(self.src, {".venv/bin/python": "#!/bin/sh\n", ".venv/pyvenv.cfg": "version = 3.12.1\n"})
        self.fail_tools(args=("-e",))
        res = self.deps()
        self.assertTrue(res["has_failure"])
        item = [i for i in res["items"] if i["kind"] == "python"][0]
        self.assertEqual(item["action"], "failed")
        self.assertIn(f"可编辑安装没能改指向 worktree：{self.wt}", item["summary"])
        self.assertIn("fake uv: install failed", item["summary"])
        self.assertNotIn("已建好", item["summary"])
        self.assertIn(item["summary"], res["summary_for_prompt"])
        self.assertEqual([(e["installed_as"], e["ok"]) for e in item["editable"]], [(str(self.wt), False)])
        self.assertFalse((self.wt / ".venv" / STAMP).exists())

    def test_editable_failure_not_hidden_on_rerun(self) -> None:
        """可编辑安装改指失败之后再跑 deps：这个 .venv 是 drover 复刻到一半的，不能当成别人建的 .venv 去按 requirements.txt
        补装（补装那条路不管可编辑安装，会报 installed，把改指失败盖掉），要删掉重新复刻。改指还失败就还报失败；改指成功了
        才报 installed、记安装记录，之后没变就跳过"""
        self.make_repo({".gitignore": GITIGNORE, "requirements.txt": "requests\n"})
        self.write_files(self.src, {".venv/bin/python": "#!/bin/sh\n", ".venv/pyvenv.cfg": "version = 3.12.1\n"})
        self.fail_tools(args=("-e",))
        self.assertTrue(self.deps()["has_failure"])
        (self.wt / ".venv" / "half.txt").write_text("复刻到一半的 .venv 里的东西\n")
        again = self.deps()
        self.assertTrue(again["has_failure"])
        self.assertEqual([i["action"] for i in again["items"] if i["kind"] == "python"], ["failed"])
        self.fail_tools()
        fixed = self.deps_item("python")
        self.assertEqual(fixed["action"], "installed")
        self.assertIn("没做完", fixed["reason"])
        self.assertEqual([(e["installed_as"], e["ok"]) for e in fixed["editable"]], [(str(self.wt), True)])
        self.assertFalse((self.wt / ".venv" / "half.txt").exists())  # 重新复刻的，不是在半成品上补装
        self.assertEqual([c[:2] for c in self.calls("uv")].count(["venv", "--seed"]), 3)
        self.assertEqual(self.deps_item("python")["action"], "skipped")

    def test_clone_failed_midway_rebuilt_on_rerun(self) -> None:
        """复刻到一半就失败了（源 venv 的包没装上）：再跑也要重新完整复刻，不在半成品上只按 requirements.txt 补装、报
        installed。只补装的话，requirements 里没写的包（pytest 这类）会一直缺着"""
        self.make_repo({".gitignore": GITIGNORE, "requirements.txt": "requests\n"})
        self.write_files(self.src, {".venv/bin/python": "#!/bin/sh\n", ".venv/pyvenv.cfg": "version = 3.12.1\n"})
        self.fail_tools(args=("-r",))
        self.assertEqual(self.deps_item("python")["action"], "failed")
        self.fail_tools()
        self.assertEqual(self.deps_item("python")["action"], "installed")
        self.assertEqual([c[:2] for c in self.calls("uv")][3:],
                         [["venv", "--seed"], ["pip", "freeze"], ["pip", "install"], ["pip", "install"], ["pip", "install"]])

    def test_existing_venv_without_record_tops_up(self) -> None:
        """复刻那条路、已有 .venv 但不是 drover 建的：按 requirements.txt 补装，不重建"""
        self.make_repo({".gitignore": GITIGNORE, "requirements.txt": "requests\n"})
        self.write_files(self.src, {".venv/bin/python": "#!/bin/sh\n", ".venv/pyvenv.cfg": "version = 3.12.1\n"})
        self.write_files(self.wt, {".venv/bin/python": "#!/bin/sh\n", ".venv/pyvenv.cfg": "version = 3.12.1\n"})
        self.assertEqual(self.deps_item("python")["action"], "installed")
        self.assertEqual([c[:2] for c in self.calls("uv")], [["pip", "install"]])
        self.assertEqual(self.deps_item("python")["action"], "skipped")

    def test_symlinked_venv_is_not_installed_through(self) -> None:
        """.venv 是软链：不调用 uv、不往链接目标里写，摘要写清软链指向哪"""
        self.make_repo(self.UV_REPO)
        target = self.src / ".venv"
        self.write_files(target, {"pyvenv.cfg": "version = 3.12.1\n"})
        os.symlink(target, self.wt / ".venv")
        res = self.deps_item("python")
        self.assertEqual(res["action"], "skipped")
        self.assertIn("软链", res["summary"])
        self.assertIn(str(target), res["summary"])
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["pyvenv.cfg"])
        self.assertEqual(self.calls("uv"), [])


class IntegrateDepsTests(IntegrationCase):
    def setUp(self) -> None:
        super().setUp()
        selftest_fakes.install_pkg_tools(self.fakebin, sys.executable)
        self.commit(self.src, "js workspace", PNPM_REPO)
        self.git(self.src, "push", "-q", "origin", "main")
        self.git(self.up, "pull", "-q", "--ff-only", "origin", "main")

    def pnpm_calls(self) -> int:
        log = self.fakebin / "pkg-calls.log"
        return sum(1 for line in log.read_text().splitlines() if json.loads(line)["tool"] == "pnpm") if log.exists() else 0

    def test_second_integrate_with_lockfile_change_reinstalls(self) -> None:
        """第一次 integrate 装依赖；第二次摘进一个改了 lockfile、加了工作区包的条目：集成 worktree 复用、已有 node_modules，
        照样重装（deps 存档和 integrate 返回里的 action 为证）；再跑一次什么都没摘进来：跳过"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        first = self.integrate(a)
        self.assertEqual(first["deps"]["items"], [{"kind": "js", "action": "installed"}])
        self.assertEqual(self.pnpm_calls(), 1)

        b = self.item("b", {"pnpm-lock.yaml": "lockfileVersion: '9.0'\nimporters: {}\n",
                            "packages/b/package.json": '{"name": "b", "dependencies": {"a": "workspace:*"}}\n'})
        second = self.integrate(a, b)
        self.assertTrue(second["reused"])
        self.assertEqual([p["subject"] for p in second["picked"]], ["item b"])
        self.assertEqual(second["deps"]["items"], [{"kind": "js", "action": "installed"}])
        self.assertFalse(second["deps"]["has_failure"])
        saved = json.loads(Path(second["deps"]["saved_to"]).read_text())
        self.assertEqual(Path(second["deps"]["saved_to"]), self.state / self.SLUG / f"deps-integrate-{self.REPO}.json")
        js = [i for i in saved["items"] if i["kind"] == "js"][0]
        self.assertEqual(js["action"], "installed")
        self.assertIn("pnpm-lock.yaml", js["summary"])
        self.assertEqual(self.pnpm_calls(), 2)

        third = self.integrate(a, b)
        self.assertEqual(third["deps"]["items"], [{"kind": "js", "action": "skipped"}])
        self.assertEqual(self.pnpm_calls(), 2)


if __name__ == "__main__":
    unittest.main()
