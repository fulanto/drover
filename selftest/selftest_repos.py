"""integrate / push 用的仓库布局：本地裸仓库当 origin，不联网。

  tmp/origin.git    裸仓库（远端）
  tmp/work/shop     源仓库（主检出），条目分支 task/s1-<名字> 都从它的 main 拉
  tmp/upstream      origin 的另一个克隆：模拟别人往上游推提交
  tmp/moved.git     move_origin 之后 origin 改指的裸仓库：全新历史（换了托管地址、新地址上的历史是重建的）
集成 worktree 由假 herdr 的 worktree create 建在临时 HOME 的 .herdr/worktrees/shop/ 下。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import selftest_fakes
from selftest_support import DroverTestCase


class IntegrationCase(DroverTestCase):
    REPO = "shop"
    SLUG = "s1"

    def setUp(self) -> None:
        super().setUp()
        self.origin = self.tmp / "origin.git"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.src = self.work / self.REPO
        self.init_repo(self.src, {"README.md": "# shop\n", "app/core.py": "VALUE = 1\n", "shared.txt": "base\n"})
        self.git(self.src, "remote", "add", "origin", str(self.origin))
        self.git(self.src, "push", "-q", "origin", "main")
        self.up = self.tmp / "upstream"
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.up))
        # 远端是本地目录（不联网），herdr 是假的
        self.assertEqual(self.git(self.src, "remote", "get-url", "origin"), str(self.origin))
        self.assertTrue(self.origin.is_dir())
        self.assert_fake_herdr()

    def upstream_commit(self, message: str, files: dict) -> str:
        """别人往上游推一个提交，返回 sha。"""
        sha = self.commit(self.up, message, files)
        self.git(self.up, "push", "-q", "origin", "main")
        return sha

    def item(self, name: str, files: dict) -> str:
        """在源仓库从 main 拉条目分支 task/s1-<名字>、提交（说明 "item <名字>"），切回 main，返回分支名。"""
        branch = f"task/{self.SLUG}-{name}"
        self.git(self.src, "checkout", "-q", "-b", branch, "main")
        self.commit(self.src, f"item {name}", files)
        self.git(self.src, "checkout", "-q", "main")
        return branch

    def integrate(self, *branches: str, rc: int = 0, extra: tuple = ()) -> dict:
        args = ["integrate", "--repo", self.REPO, "--slug", self.SLUG, "--target", "main", *extra]
        for b in branches:
            args += ["--from", b]
        return self.drover_json(*args, rc=rc)

    def push(self, wt: Path, *extra: str, rc: int = 0) -> dict:
        return self.drover_json("push", "--wt", str(wt), "--target", "main", *extra, rc=rc)

    def remote_head(self) -> str:
        """独立核对：ls-remote 读远端 main。"""
        return self.git(self.tmp, "ls-remote", str(self.origin), "refs/heads/main").split()[0]

    def move_origin(self) -> Path:
        """源仓库的 origin 改指另一个裸仓库，它的 main 和原来的没有共同祖先（仓库换了托管地址、新地址上的历史是重建的），
        返回新仓库的路径。tmp/upstream 还指着原来的 origin。"""
        moved = self.tmp / "moved.git"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(moved))
        seed = self.tmp / "moved-seed"
        self.init_repo(seed, {"README.md": "# shop（重建的历史）\n", "app/core.py": "VALUE = 1\n"})
        self.git(seed, "push", "-q", str(moved), "main")
        self.git(self.src, "remote", "set-url", "origin", str(moved))
        return moved

    def ssh_origin(self, fail: int, message: str) -> str:
        """装假 ssh（前 fail 次打 message、退出 255，之后在本机执行远端命令），返回经它访问 origin 的地址。"""
        selftest_fakes.install_ssh(self.fakebin, sys.executable, fail, message)
        return f"ssh://fakehost{self.origin}"

    def ssh_calls(self) -> list:
        """假 ssh 每次被调用时的参数（最后一个是远端命令，如 git-upload-pack '<路径>'）。"""
        log = self.fakebin / "fake-ssh-calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def state_file(self) -> Path:
        return self.state / self.SLUG / f"integrate-{self.REPO}.json"

    def load_state(self) -> dict:
        return json.loads(self.state_file().read_text())

    def records_file(self) -> Path:
        """任务的推送记录（push 每推一次追加一行）。"""
        return self.state / self.SLUG / "pushes.jsonl"

    def records(self) -> list:
        path = self.records_file()
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def head(self, wt: Path, rev: Optional[str] = None) -> str:
        return self.git(wt, "rev-parse", rev or "HEAD")
