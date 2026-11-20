"""主工作台没有默认值：没有 config.json（没跑过 install.py）时，bin/drover 不再假定任何目录。

- 不自动信任任何目录（TRUSTED_ROOT 为 None）：wt 遇到 herdr 要求信任仓库时一律报错、让先问用户；
- 按名字找仓库只看当前目录（REPO_DIRS 为空）；
- orch-start（编排者的工作目录就是主工作台）报错，error 里写"先跑 install.py"；
- preflight 报出 config 的情况，但不因此失败。
对照组（有 config.json）确认这几处照旧：信任主工作台下的仓库、按 workspace 起编排者、preflight 不带提示。
"""

from __future__ import annotations

import argparse
import json
import unittest

from selftest_support import DroverTestCase

TRUST_ERROR = {"code": "repository_not_trusted", "message": "fake herdr: repository is not trusted"}


def fake_herdr_requiring_trust(calls: list):
    """进程内替换 drover 模块的 herdr：worktree create 不带 --trust-repository 时报"要信任仓库"，带了也失败（不往下建）。"""
    def herdr(args: list, *, timeout=None) -> tuple:
        calls.append(list(args))
        if args[:2] == ["worktree", "list"]:
            return True, {"worktrees": []}
        if args[:2] == ["workspace", "list"]:
            return True, {"workspaces": []}
        if args[:2] == ["worktree", "create"] and "--trust-repository" not in args:
            return False, dict(TRUST_ERROR)
        return False, {"code": "fake_stop", "message": "fake herdr: stop here"}
    return herdr


def wt_args(repo) -> argparse.Namespace:
    return argparse.Namespace(repo=str(repo), branch="task/cfg", label="beta:cfg", path=None, base="HEAD",
                              no_sync=False, dry_run=False)


class ConfigMissingTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.state / "config.json").unlink()

    def write_request(self) -> str:
        request = self.tmp / "request.md"
        request.write_text("# 请求\n\n用户原话：测试\n")
        return str(request)

    def test_no_trusted_root_or_repo_dirs(self) -> None:
        """没有 config.json：TRUSTED_ROOT、编排者工作目录都是 None，REPO_DIRS 为空，不再落到 ~/Git-Repo"""
        d = self.load_drover()
        self.assertFalse(d.CONFIG_FILE.exists())
        self.assertIsNone(d.TRUSTED_ROOT)
        self.assertEqual(d.REPO_DIRS, [])
        self.assertIsNone(d.ORCH_CWD)
        self.assertIsNone(d.ROUTING_FILE)
        self.assertIsNone(d.ORCH_MEMORY_INDEX)
        self.assertNotIn("Git-Repo", json.dumps(d.CONFIG))
        info = d.config_info()
        self.assertEqual((info["exists"], info["workspace"], info["repo_dirs"]), (False, None, []))
        self.assertIn("先跑 install.py", info["warning"])

    def test_config_without_workspace_key(self) -> None:
        """config.json 在、但没有 workspace（比如只写了 profile）：同样不信任、不按名字找"""
        (self.state / "config.json").write_text(json.dumps({"profile": None, "playbook_dirs": []}))
        d = self.load_drover()
        self.assertIsNone(d.TRUSTED_ROOT)
        self.assertEqual(d.REPO_DIRS, [])
        info = d.config_info()
        self.assertTrue(info["exists"])
        self.assertIn("没有配置主工作台", info["warning"])

    def test_repo_by_name_only_in_cwd(self) -> None:
        """按名字找仓库只看当前目录：在别处报找不到（提示没配置 repo_dirs），在仓库的上一级能找到"""
        repo = self.work / "alpha"
        self.init_repo(repo, {"a.txt": "a\n"})
        out = self.drover_json("playbook", "--repo", "alpha", rc=1, cwd=self.tmp)
        self.assertFalse(out["ok"])
        self.assertIn("找不到仓库 alpha", out["error"])
        self.assertIn("按名字只在当前目录下找", out["error"])
        out = self.drover_json("playbook", "--repo", "alpha", cwd=self.work)
        self.assertEqual(out["repos"][0]["path"], str(repo))

    def test_orch_start_requires_workspace(self) -> None:
        """orch-start（含 --dry-run）报错：没有配置主工作台，先跑 install.py；不调 herdr、不建任务目录"""
        request = self.write_request()
        for extra in (["--dry-run"], []):
            out = self.drover_json("orch-start", "--slug", "cfg-t", "--name", "orch-cfg", "--request-file", request,
                                   *extra, rc=1)
            self.assertFalse(out["ok"])
            self.assertIn("没有配置主工作台", out["error"])
            self.assertIn("先跑 install.py", out["error"])
            self.assertIn(str(self.state / "config.json"), out["error"])
            self.assertEqual(out["config_file"], str(self.state / "config.json"))
        self.assertEqual(self.herdr_calls(), [])
        self.assertFalse((self.state / "cfg-t").exists())

    def test_preflight_reports_missing_config(self) -> None:
        """preflight 照常 ok，config 里报出没有 config.json、没有主工作台，并提示先跑 install.py"""
        self.env["HERDR_ENV"] = "1"
        out = self.drover_json("preflight")
        self.assertTrue(out["ok"])
        cfg = out["config"]
        self.assertEqual(cfg["file"], str(self.state / "config.json"))
        self.assertFalse(cfg["exists"])
        self.assertIsNone(cfg["workspace"])
        self.assertEqual(cfg["repo_dirs"], [])
        self.assertIn("没有配置主工作台", cfg["warning"])
        self.assertIn("先跑 install.py", cfg["warning"])

    def test_wt_does_not_auto_trust(self) -> None:
        """herdr 要求信任仓库时不自动加 --trust-repository（哪个目录都不算可信），报错让先问用户"""
        repo = self.work / "beta"
        self.init_repo(repo, {"b.txt": "b\n"})
        d = self.load_drover()
        calls = []
        d.herdr = fake_herdr_requiring_trust(calls)
        with self.assertRaises(d.DroverError) as cm:
            d.cmd_wt(wt_args(repo))
        self.assertIn("先问用户确认可信", cm.exception.message)
        self.assertIn("没有配置主工作台", cm.exception.message)
        self.assertEqual(cm.exception.extra["error"], TRUST_ERROR)
        self.assertTrue([c for c in calls if c[:2] == ["worktree", "create"]])
        self.assertFalse([c for c in calls if "--trust-repository" in c])


class ConfigPresentTests(DroverTestCase):
    """对照组：有 config.json（workspace = 临时的 work 目录）时照旧。"""

    def test_preflight_reports_config(self) -> None:
        self.env["HERDR_ENV"] = "1"
        cfg = self.drover_json("preflight")["config"]
        self.assertEqual((cfg["exists"], cfg["workspace"], cfg["repo_dirs"]), (True, str(self.work), [str(self.work)]))
        self.assertNotIn("warning", cfg)

    def test_repo_dirs_default_to_workspace(self) -> None:
        """config 只写了 workspace：按名字找仓库的目录就是它（和 install.py 的 --repo-dir 默认一致）"""
        (self.state / "config.json").write_text(json.dumps({"workspace": str(self.work)}))
        d = self.load_drover()
        self.assertEqual(d.TRUSTED_ROOT, self.work)
        self.assertEqual(d.REPO_DIRS, [self.work])

    def test_orch_start_dry_run_uses_workspace(self) -> None:
        """orch-start --dry-run：编排者的 workspace 在主工作台下、label 是 orch，开场 prompt 提到主工作台的 AGENTS.md"""
        command = self.home / ".claude" / "commands" / "drover.md"
        command.parent.mkdir(parents=True)
        command.write_text("# drover\n")
        request = self.tmp / "request.md"
        request.write_text("# 请求\n")
        out = self.drover_json("orch-start", "--slug", "cfg-t", "--name", "orch-cfg", "--request-file",
                               str(request), "--kind", "claude", "--dry-run")
        self.assertTrue(out["ok"])
        self.assertEqual(out["planned"][0], f"herdr workspace create --cwd {self.work} --label orch --no-focus")
        self.assertIn(str(self.work / "AGENTS.md"), out["kickoff"])

    def test_wt_auto_trusts_under_workspace(self) -> None:
        repo = self.work / "beta"
        self.init_repo(repo, {"b.txt": "b\n"})
        d = self.load_drover()
        calls = []
        d.herdr = fake_herdr_requiring_trust(calls)
        with self.assertRaises(d.DroverError) as cm:
            d.cmd_wt(wt_args(repo))
        self.assertEqual(cm.exception.message, "herdr worktree 命令失败")  # 带上 --trust-repository 重试过
        self.assertTrue([c for c in calls if c[:2] == ["worktree", "create"] and "--trust-repository" in c])


if __name__ == "__main__":
    unittest.main(verbosity=2)
