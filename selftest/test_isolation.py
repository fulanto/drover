"""隔离：DROVER_STATE_DIR 把状态目录整个挪走；herdr 解析到假的；CLI 的状态文件也落在临时目录。"""

from __future__ import annotations

import sys
import unittest

from selftest_support import DROVER, REAL_STATE, DroverTestCase


class StateDirTests(DroverTestCase):
    def test_state_dir_env_moves_all_state(self) -> None:
        """设了 DROVER_STATE_DIR：config.json、任务目录、heavy 调度状态、基线缓存都在它下面，config 读的是临时的"""
        d = self.load_drover()
        self.assertEqual(d.STATE_HOME, self.state)
        self.assertEqual(d.CONFIG_FILE, self.state / "config.json")
        self.assertEqual(d.HEAVY_DIR, self.state / "locks" / "heavy")
        self.assertEqual(d.BASELINE_DIR, self.state / "baselines")
        self.assertEqual(d.TRUSTED_ROOT, self.work)
        self.assertEqual(d.REPO_DIRS, [self.work])
        for path in (d.STATE_HOME, d.HEAVY_DIR, d.BASELINE_DIR):
            self.assertNotIn(REAL_STATE, (path, *path.parents))

    def assert_default_state(self, d) -> None:
        default = self.home / ".drover"
        self.assertEqual(d.STATE_HOME, default)
        self.assertEqual(d.CONFIG_FILE, default / "config.json")
        self.assertEqual(d.HEAVY_DIR, default / "locks" / "heavy")
        self.assertEqual(d.BASELINE_DIR, default / "baselines")

    def test_default_is_home_drover(self) -> None:
        """没设 DROVER_STATE_DIR 时状态目录是 HOME/.drover：config.json、heavy 调度状态、基线缓存都在它下面"""
        self.env.pop("DROVER_STATE_DIR")
        self.assert_default_state(self.load_drover())

    def test_empty_state_dir_is_home_drover(self) -> None:
        """DROVER_STATE_DIR 设成空串，和没设一样是 HOME/.drover"""
        self.env["DROVER_STATE_DIR"] = ""
        self.assert_default_state(self.load_drover())

    def test_drover_cmd_is_literal_tilde(self) -> None:
        """写进任务书的 CLI 入口 DROVER_CMD 是字面的 ~/.drover/bin/drover：不展开成 HOME 的绝对路径（权限规则按字面前缀
        匹配，展开了就对不上 install.py 加的 Bash(~/.drover/bin/drover:*)，worker 每跑一次 heavy 都弹框），也不跟 DROVER_STATE_DIR 走"""
        d = self.load_drover()
        self.assertEqual(d.DROVER_CMD, "~/.drover/bin/drover")
        self.assertIn(f'"Bash({d.DROVER_CMD}:*)"', (DROVER.parent.parent / "install.py").read_text())

    def test_tilde_is_expanded(self) -> None:
        """DROVER_STATE_DIR 里的 ~ 按 HOME 展开"""
        self.env["DROVER_STATE_DIR"] = "~/alt-state"
        d = self.load_drover()
        self.assertEqual(d.STATE_HOME, self.home / "alt-state")


class FakeHerdrTests(DroverTestCase):
    def test_herdr_resolves_to_fake(self) -> None:
        """CLI 和进程内看到的 herdr 都是假 herdr，调用记进日志；不认识的子命令报错退出"""
        self.assert_fake_herdr()
        rc, evs, _ = self.watch("--once", "nobody")
        self.assertEqual(rc, 0)
        self.assertEqual(evs, ["EVENT nobody gone"])
        self.assertEqual(self.herdr_calls(), [["agent", "list"]])
        d = self.load_drover()
        ok, err = d.herdr(["tab", "list"])
        self.assertFalse(ok)
        self.assertEqual(err["code"], "unsupported")
        self.assertEqual(self.herdr_calls()[-1], ["tab", "list"])

    def test_cli_state_lands_in_state_dir(self) -> None:
        """CLI 子进程：heavy 调度状态建在 DROVER_STATE_DIR 下，临时 HOME 的默认位置没有东西"""
        p = self.drover("heavy", "--", sys.executable, "-c", "pass")
        self.assertEqual(p.returncode, 0)
        self.assertTrue((self.state / "locks" / "heavy" / "history.json").is_file())
        self.assertFalse((self.home / ".drover").exists())

    def test_cli_default_state_is_home_drover(self) -> None:
        """CLI 子进程、没设 DROVER_STATE_DIR：heavy 调度状态建在（临时）HOME/.drover/locks/heavy/ 下"""
        self.env.pop("DROVER_STATE_DIR")
        p = self.drover("heavy", "--", sys.executable, "-c", "pass")
        self.assertEqual(p.returncode, 0)
        self.assertTrue((self.home / ".drover" / "locks" / "heavy" / "history.json").is_file())
        self.assertFalse((self.state / "locks").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
