"""4. push：上游没动 / 只改文档 / 改代码 / add/add 冲突 / 手动 rebase；ls-remote 核对；base 回写进 integrate 状态文件；
push 遇到永久错误不重试、偶发错误重试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from selftest_repos import IntegrationCase
from selftest_support import FakeClock


class PushTests(IntegrationCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = self.item("a", {"app/a.py": "A = 1\n"})
        self.base0 = self.upstream_commit("upstream readme", {"README.md": "# shop v2\n"})
        res = self.integrate(self.a)
        self.wt = Path(res["worktree_path"])
        self.assertEqual(res["base"], self.base0)

    def test_no_upstream_change_pushes_and_verifies(self) -> None:
        """上游没有新提交 → 推送，ls-remote 核对远端就是 HEAD，推送记进任务的 pushes.jsonl（不再写进集成状态）；
        再跑一次没有要推的"""
        head = self.head(self.wt)
        res = self.push(self.wt)
        self.assertTrue(res["pushed"])
        self.assertTrue(res["verified"])
        self.assertEqual(res["remote_sha"], head)
        self.assertEqual(self.remote_head(), head)
        self.assertEqual(res["tries"], {"fetch": 1, "push": 1, "ls_remote": 1})
        self.assertEqual(Path(res["state_file"]), self.state_file())
        self.assertEqual((res["task"], res["push_record"]), (self.SLUG, str(self.records_file())))
        records = self.records()
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["verified"])
        self.assertEqual((records[0]["remote_sha"], records[0]["state_file"]), (head, str(self.state_file())))
        self.assertNotIn("pushes", self.load_state())
        again = self.push(self.wt)
        self.assertFalse(again["pushed"])
        self.assertIn("没有要推的提交", again["note"])
        self.assertEqual(len(self.records()), 1)

    def test_patch_mode_push_recorded_without_state(self) -> None:
        """没有集成状态文件（集成 worktree 是 wt 建的，patch 模式）的推送也落盘：任务按分支 task/s1-… 归属，记分支、目标、
        远端和它当时的推送地址、远端提交、范围、提交列表、时间、是否核对过；返回里也有推送地址"""
        wt = Path(self.drover_json("wt", "--repo", str(self.src), "--branch", "task/s1-r2", "--label", "shop:s1-r2",
                                   "--base", "origin/main")["worktree_path"])
        sha = self.commit(wt, "patch round", {"app/p.py": "P = 1\n"})
        res = self.push(wt)
        self.assertTrue(res["verified"])
        self.assertNotIn("state_file", res)
        self.assertEqual((res["task"], res["task_from"]), (self.SLUG, "worktree 分支 task/s1-r2"))
        self.assertEqual(res["remote_url"], str(self.origin))
        rec = self.records()[-1]
        self.assertEqual({k: rec[k] for k in ("repo", "remote", "remote_url", "target", "upstream", "branch",
                                              "worktree_path", "from", "remote_sha", "range", "commits", "verified")},
                         {"repo": str(self.src), "remote": "origin", "remote_url": str(self.origin), "target": "main",
                          "upstream": "origin/main", "branch": "task/s1-r2", "worktree_path": str(wt), "from": self.base0,
                          "remote_sha": sha, "range": f"{self.base0[:9]}..{sha[:9]}", "commits": [f"{sha[:7]} patch round"],
                          "verified": True})
        self.assertRegex(rec["at"], r"^\d\d-\d\d \d\d:\d\d:\d\d$")

    def test_upstream_docs_only(self) -> None:
        """上游只改文档 → rebase、needs_reverify false、base 回写，这次不推；再跑一次推出并核对"""
        up = self.upstream_commit("upstream docs", {"docs/notes.md": "notes\n"})
        res = self.push(self.wt)
        self.assertTrue(res["rebased"])
        self.assertFalse(res["pushed"])
        self.assertFalse(res["needs_reverify"])
        self.assertEqual(res["upstream_files"]["docs"]["count"], 1)
        self.assertEqual(res["upstream_files"]["code"]["count"], 0)
        self.assertEqual(res["base"], up)
        self.assertEqual(self.remote_head(), up)  # 这次没推
        state = self.load_state()
        self.assertEqual(state["base"], up)
        self.assertEqual({k: state["rebases"][-1][k] for k in ("from", "to", "behind", "needs_reverify")},
                         {"from": self.base0, "to": up, "behind": 1, "needs_reverify": False})
        res2 = self.push(self.wt)
        self.assertTrue(res2["pushed"] and res2["verified"])
        self.assertEqual(self.remote_head(), self.head(self.wt))
        self.assertEqual(self.head(self.wt, "HEAD~1"), up)

    def test_upstream_code_change_writes_back_base(self) -> None:
        """上游改代码 → rebase、needs_reverify true、base 回写；integrate 再加条目时读到回写后的 base"""
        up = self.upstream_commit("upstream code", {"app/core.py": "VALUE = 2\n"})
        res = self.push(self.wt)
        self.assertTrue(res["rebased"])
        self.assertTrue(res["needs_reverify"])
        self.assertEqual(res["upstream_files"]["code"]["count"], 1)
        self.assertIn(f"accept --base {up[:12]}", res["next"])
        state = self.load_state()
        self.assertEqual(state["base"], up)
        self.assertTrue(state["rebases"][-1]["needs_reverify"])
        b = self.item("b", {"app/b.py": "B = 1\n"})
        res2 = self.integrate(self.a, b)
        self.assertTrue(res2["reused"])
        self.assertEqual(res2["base"], up)
        self.assertEqual([p["subject"] for p in res2["picked"]], ["item b"])
        res3 = self.push(self.wt)
        self.assertTrue(res3["pushed"] and res3["verified"])
        self.assertEqual(self.remote_head(), self.head(self.wt))

    def test_add_add_conflict_then_manual_resolve(self) -> None:
        """上游制造 add/add 冲突 → push 报冲突、留标记 → 人工解完 → push 先停一次（base 更新、标 manual）→ 再 push 推出"""
        up = self.upstream_commit("upstream adds a.py", {"app/a.py": "A = 'upstream'\n"})
        res = self.push(self.wt, rc=1)
        self.assertFalse(res["ok"])
        self.assertIn("冲突", res["error"])
        self.assertEqual(res["conflicts"], ["app/a.py"])
        self.assertEqual(self.load_state()["pending_reverify"]["to"], up)
        (self.wt / "app" / "a.py").write_text("A = 1  # merged with upstream\n")
        self.git(self.wt, "add", "app/a.py")
        self.git(self.wt, "-c", "core.editor=true", "rebase", "--continue")
        stop = self.push(self.wt)
        self.assertFalse(stop["pushed"])
        self.assertTrue(stop["needs_reverify"])
        self.assertIn("冲突", stop["reason"])
        self.assertEqual(stop["base"], up)
        self.assertEqual(self.remote_head(), up)
        state = self.load_state()
        self.assertNotIn("pending_reverify", state)
        self.assertEqual(state["base"], up)
        self.assertEqual({k: state["rebases"][-1].get(k) for k in ("from", "to", "manual")},
                         {"from": self.base0, "to": up, "manual": True})
        final = self.push(self.wt)
        self.assertTrue(final["pushed"] and final["verified"])
        self.assertEqual(self.remote_head(), self.head(self.wt))

    def test_manual_rebase_marked_manual(self) -> None:
        """手动 rebase 过（上游已在 HEAD 里但不是记录的 base）→ base 更新、rebases 记 manual，照常推送"""
        up = self.upstream_commit("upstream docs", {"docs/notes.md": "notes\n"})
        self.git(self.wt, "fetch", "-q", "origin", "main")
        self.git(self.wt, "rebase", "-q", "origin/main")
        res = self.push(self.wt)
        self.assertTrue(res["pushed"] and res["verified"])
        state = self.load_state()
        self.assertEqual(state["base"], up)
        self.assertEqual({k: state["rebases"][-1].get(k) for k in ("from", "to", "manual")},
                         {"from": self.base0, "to": up, "manual": True})
        self.assertEqual(len(self.records()), 1)

    def test_state_lookup_and_dirty_worktree(self) -> None:
        """--slug 只在该任务里找状态文件；定不了任务（分支不是 task/<已有任务>…、没有集成状态）→ 不推、提示加 --slug；
        加了 --slug、不是 integrate 建的 worktree 给 state_note；有未提交改动拒绝推送"""
        found = self.push(self.wt, "--slug", self.SLUG, "--dry-run")
        self.assertEqual(Path(found["state_file"]), self.state_file())
        other = self.tmp / "other-wt"
        self.git(self.src, "worktree", "add", "-q", "-b", "task/other", str(other), "origin/main")
        refused = self.push(other, "--dry-run", rc=1)
        self.assertIn("加 --slug", refused["error"])
        self.assertIn("每次推送都要记下", refused["error"])
        note = self.push(other, "--dry-run", "--slug", self.SLUG)
        self.assertNotIn("state_file", note)
        self.assertIn("state_note", note)
        self.assertEqual(note["task"], self.SLUG)
        self.assertIn("没有任务目录", self.push(other, "--dry-run", "--slug", "nope", rc=1)["error"])
        (self.wt / "stray.txt").write_text("x\n")
        dirty = self.push(self.wt, rc=1)
        self.assertIn("未提交的改动", dirty["error"])
        self.assertEqual(self.remote_head(), self.base0)

    def push_with_clock(self, rc: int = 0) -> tuple:
        """在进程里跑 push（假时钟：重试前的 sleep 不真等），返回 (结果, 时钟)。"""
        clock = FakeClock()
        p = self.drover_in_process("push", "--wt", str(self.wt), "--target", "main", rc=rc, clock=clock)
        return json.loads(p.stdout), clock

    def test_permanent_push_error_not_retried(self) -> None:
        """push 的地址指向不存在的仓库（"does not appear to be a git repository"，不会自己好）→ 只试 1 次、不等，
        报错带 git 的原因行；远端没动。fetch 走的 url 照常"""
        self.git(self.wt, "config", "remote.origin.pushurl", str(self.tmp / "gone.git"))
        res, clock = self.push_with_clock(rc=1)
        self.assertEqual(res["error"], "git push 失败（试了 1 次）")
        self.assertIn("does not appear to be a git repository", res["stderr"])
        self.assertEqual(clock.sleeps, 0)
        self.assertEqual(self.remote_head(), self.base0)

    def test_transient_push_error_retried(self) -> None:
        """push 遇到偶发的网络错误（ssh 会话被对端关掉）→ 隔 5 秒重试，第 2 次推出去，ls-remote 核对通过。
        push 的地址经假 ssh 访问（第一次打 ssh 的报错原文，之后在本机执行 git-receive-pack）"""
        url = self.ssh_origin(1, "Connection to fakehost closed by remote host.")
        self.git(self.wt, "config", "remote.origin.pushurl", url)
        head = self.head(self.wt)
        res, clock = self.push_with_clock()
        self.assertTrue(res["pushed"] and res["verified"])
        self.assertEqual(res["tries"], {"fetch": 1, "push": 2, "ls_remote": 1})
        self.assertEqual((clock.sleeps, clock.elapsed()), (1, 5))
        self.assertEqual([c[-1].split()[0] for c in self.ssh_calls()], ["git-receive-pack"] * 2)
        self.assertEqual(self.remote_head(), head)


if __name__ == "__main__":
    unittest.main(verbosity=2)
