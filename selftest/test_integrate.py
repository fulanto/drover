"""3. integrate：本地裸仓库当 origin，假 herdr 的 worktree create 真的建 git worktree。"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from selftest_repos import IntegrationCase


class IntegrateTests(IntegrationCase):
    def test_builds_from_latest_upstream_and_picks_items(self) -> None:
        """先 fetch、从最新上游建集成 worktree，按 --from 顺序 cherry-pick 两个条目；状态、依赖记录在状态目录"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        b = self.item("b", {"app/b.py": "B = 1\n"})
        latest = self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        res = self.integrate(a, b)
        self.assertTrue(res["ok"])
        self.assertEqual(res["base"], latest)
        self.assertEqual([p["subject"] for p in res["picked"]], ["item a", "item b"])
        self.assertEqual(res["skipped"], [])
        self.assertFalse(res["reused"])
        wt = Path(res["worktree_path"])
        self.assertEqual(wt.parent, self.home / ".herdr" / "worktrees" / self.REPO)
        self.assertEqual(self.head(wt, "HEAD~2"), latest)
        self.assertEqual(self.git(wt, "rev-parse", "--abbrev-ref", "HEAD"), "task/s1")
        self.assertEqual(Path(res["state_file"]), self.state_file())
        state = self.load_state()
        self.assertEqual(state["base"], latest)
        self.assertEqual([e["status"] for e in state["picked"]], ["picked", "picked"])
        self.assertTrue((self.state / self.SLUG / f"deps-integrate-{self.REPO}.json").is_file())
        creates = [c for c in self.herdr_calls() if c[:2] == ["worktree", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][creates[0].index("--base") + 1], "origin/main")

    def test_rerun_skips_everything(self) -> None:
        """重跑：复用集成 worktree（herdr worktree open），已应用的全部跳过，HEAD 不动"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        b = self.item("b", {"app/b.py": "B = 1\n"})
        # 上游先动一下：否则同一秒内 cherry-pick 到原父提交上会得到同一个 sha，git cherry 根本不列出它
        self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        first = self.integrate(a, b)
        second = self.integrate(a, b)
        self.assertTrue(second["reused"])
        self.assertEqual(second["picked"], [])
        self.assertEqual([s["reason"] for s in second["skipped"]], ["已在集成分支里"] * 2)
        self.assertEqual(second["head"], first["head"])
        self.assertEqual(second["base"], first["base"])
        self.assertEqual(len([c for c in self.herdr_calls() if c[:2] == ["worktree", "open"]]), 1)

    def test_conflict_stops_then_resolved_rerun_skips(self) -> None:
        """冲突停下（状态记 conflict）→ 没解完重跑被拦 → 解完 cherry-pick --continue 后重跑：跳过、状态改 resolved"""
        a = self.item("a", {"shared.txt": "from a\n"})
        c = self.item("c", {"shared.txt": "from c\n"})
        self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        res = self.integrate(a, c, rc=1)
        self.assertFalse(res["ok"])
        self.assertIn("冲突", res["error"])
        self.assertEqual(res["conflicts"], ["shared.txt"])
        self.assertEqual(len(res["picked_so_far"]), 1)
        self.assertEqual([e["status"] for e in self.load_state()["picked"]], ["picked", "conflict"])
        wt = Path(res["worktree_path"])
        blocked = self.integrate(a, c, rc=1)
        self.assertIn("CHERRY_PICK_HEAD", blocked["error"])
        (wt / "shared.txt").write_text("from a and c\n")
        self.git(wt, "add", "shared.txt")
        self.git(wt, "cherry-pick", "--continue")
        final = self.integrate(a, c)
        self.assertEqual(final["picked"], [])
        self.assertEqual(len(final["skipped"]), 2)
        self.assertEqual([e["status"] for e in self.load_state()["picked"]], ["picked", "resolved"])
        self.assertEqual((wt / "shared.txt").read_text(), "from a and c\n")

    def test_blocked_cherry_pick_marked_failed_then_retried(self) -> None:
        """条目提交被集成 worktree 里的未跟踪文件挡住（git 没开始就拒绝）→ ok: false、状态记 failed
        （不是 conflict）；挪走挡路文件后重跑：重新 cherry-pick，HEAD 前进、文件内容在"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        env_item = self.item("e", {".env.example": "KEY=from-item\n"})
        self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        first = self.integrate(a)
        wt = Path(first["worktree_path"])
        (wt / ".env.example").write_text("KEY=local\n")
        res = self.integrate(a, env_item, rc=1)
        self.assertIn("没能开始", res["error"])
        self.assertIn("重试这个提交", res["error"])
        self.assertNotIn("conflicts", res)
        self.assertIn("untracked", res["stderr"])
        self.assertEqual([e["status"] for e in self.load_state()["picked"]], ["picked", "failed"])
        self.assertEqual(self.head(wt), first["head"])
        (wt / ".env.example").unlink()
        final = self.integrate(a, env_item)
        self.assertEqual([p["subject"] for p in final["picked"]], ["item e"])
        self.assertEqual(self.head(wt, "HEAD~1"), first["head"])
        self.assertEqual((wt / ".env.example").read_text(), "KEY=from-item\n")
        self.assertEqual([e["status"] for e in self.load_state()["picked"]], ["picked", "picked"])
        self.assertEqual(self.git(wt, "status", "--porcelain"), "")

    def test_empty_pick_skipped_in_chinese_locale(self) -> None:
        """条目的改动已经被上游带进来了（上游那个提交还多改了别的文件，patch-id 不同，git cherry 认不出）→ cherry-pick 成了
        空提交：跳过、状态记 empty、不留进行中的 cherry-pick。用户的 locale 是中文（LC_ALL=zh_CN.UTF-8）时 git 的提示会被
        翻译，drover 跑这条 cherry-pick 时固定 C locale，照样按原话认出空提交。本机 git 没有中文翻译就跳过"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        latest = self.upstream_commit("upstream takes a", {"app/a.py": "A = 1\n", "docs/guide.md": "guide\n"})
        self.env.update(LANG="zh_CN.UTF-8", LC_ALL="zh_CN.UTF-8")
        probe = subprocess.run(["git", "-C", str(self.src), "status"], env=self.env, capture_output=True, text=True)
        if "位于分支" not in probe.stdout:
            self.skipTest(f"本机 git 在 zh_CN 下没有中文输出：{probe.stdout.strip()[:80]}")
        res = self.integrate(a)
        self.assertEqual(res["picked"], [])
        self.assertEqual([s["reason"] for s in res["skipped"]], ["改动已在集成分支里（空提交）"])
        self.assertEqual([e["status"] for e in self.load_state()["picked"]], ["empty"])
        wt = Path(res["worktree_path"])
        self.assertEqual(self.head(wt), latest)
        self.assertEqual(self.git(wt, "rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD", check=False), "")
        self.assertEqual(self.git(wt, "status", "--porcelain"), "")

    def test_fast_forwards_source_checkout_without_refetch(self) -> None:
        """新建集成 worktree 时主检出照样快进；主检出的上游就是目标分支时 wt 不再 fetch 一次"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        latest = self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        res = self.integrate(a)
        sync = res["source_sync"]
        self.assertEqual(sync["status"], "fast_forwarded")
        self.assertEqual(self.head(self.src), latest)
        self.assertEqual([(f["ref"], f.get("note")) for f in sync["fetch"]], [("origin/main", "调用方已 fetch，不重复")])
        self.assertEqual(res["warnings"], [])

    def test_source_on_branch_with_deleted_upstream_does_not_block(self) -> None:
        """主检出停在配了 origin 上游、但远端已删的旧分支上 → integrate 照常建（显式 --base origin/main），
        source_sync 记 upstream_gone，不报错"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        latest = self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        self.git(self.src, "checkout", "-q", "-b", "bugfix_old")
        self.git(self.src, "push", "-q", "-u", "origin", "bugfix_old")
        self.git(self.up, "push", "-q", "origin", "--delete", "bugfix_old")
        res = self.integrate(a)
        self.assertEqual(res["source_sync"]["status"], "upstream_gone")
        self.assertEqual(res["base"], latest)
        self.assertEqual([p["subject"] for p in res["picked"]], ["item a"])
        self.assertEqual(res["warnings"], [])

    def test_rejects_dirty_item_worktree_and_missing_branch(self) -> None:
        """条目分支的 worktree 有没提交的改动、或分支不存在 → 拒绝，不调 herdr"""
        d = self.item("d", {"app/d.py": "D = 1\n"})
        item_wt = self.tmp / "item-d"
        self.git(self.src, "worktree", "add", "-q", str(item_wt), d)
        (item_wt / "app" / "d.py").write_text("D = 2\n")
        res = self.integrate(d, rc=1)
        self.assertIn("没提交的改动", res["error"])
        missing = self.integrate("task/s1-nope", rc=1)
        self.assertIn("不存在", missing["error"])
        self.assertEqual(self.herdr_calls(), [])
        self.assertFalse(self.state_file().exists())

    def test_dry_run_plans_only(self) -> None:
        """--dry-run 只列步骤：不 fetch、不调 herdr、不写状态文件"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        before = self.git(self.src, "rev-parse", "origin/main")
        self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        res = self.integrate(a, extra=("--dry-run",))
        self.assertTrue(res["dry_run"])
        self.assertTrue(any("cherry-pick" in step for step in res["planned"]))
        self.assertEqual(self.git(self.src, "rev-parse", "origin/main"), before)
        self.assertEqual(self.herdr_calls(), [])
        self.assertFalse(self.state_file().exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
