"""4. push：上游没动 / 只改文档 / 改代码 / add/add 冲突 / 手动 rebase；ls-remote 按完整引用名核对；base 回写进 integrate 状态文件；
只推整体检查验过的内容（没检查 / 没过 / 检查之后又改了都不推，--unchecked 带理由才推）；远端还没有的目标分支；
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
        self.check(self.wt)

    def test_no_upstream_change_pushes_and_verifies(self) -> None:
        """上游没有新提交 → 推送，ls-remote 核对远端就是 HEAD，推送记进任务的 pushes.jsonl（不再写进集成状态），记录里带着
        覆盖它的整体检查（检查的提交、树）；再跑一次没有要推的"""
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
        self.assertEqual((records[0]["check"]["head"], records[0]["check"]["tree"]),
                         (head, self.git(self.wt, "rev-parse", "HEAD^{tree}")))
        self.assertNotIn("unchecked", records[0])
        self.assertNotIn("pushes", self.load_state())
        again = self.push(self.wt)
        self.assertFalse(again["pushed"])
        self.assertIn("没有要推的提交", again["note"])
        self.assertEqual(len(self.records()), 1)

    def test_patch_mode_push_needs_unchecked(self) -> None:
        """没有集成状态文件（集成 worktree 是 wt 建的，patch 模式）：整体检查结论没有地方记，不带 --unchecked 不推；
        带了理由才推，推送照样落盘：任务按分支 task/s1-… 归属，记分支、目标、远端和它当时的推送地址、远端提交、范围、
        提交列表、时间、是否核对过，以及 --unchecked 的理由和差在哪；返回里也有推送地址"""
        wt = Path(self.drover_json("wt", "--repo", str(self.src), "--branch", "task/s1-r2", "--label", "shop:s1-r2",
                                   "--base", "origin/main")["worktree_path"])
        sha = self.commit(wt, "patch round", {"app/p.py": "P = 1\n"})
        refused = self.push(wt, rc=1)
        self.assertIn("没有集成状态", refused["error"])
        self.assertIn("--unchecked", refused["error"])
        self.assertEqual(self.remote_head(), self.base0)
        res = self.push(wt, "--unchecked", "patch 模式：整体检查在 accept-integrate-shop.json")
        self.assertTrue(res["verified"])
        self.assertNotIn("state_file", res)
        self.assertEqual((res["task"], res["task_from"]), (self.SLUG, "worktree 分支 task/s1-r2"))
        self.assertEqual(res["remote_url"], str(self.origin))
        rec = self.records()[-1]
        self.assertEqual({k: rec[k] for k in ("repo", "remote", "remote_url", "target", "upstream", "branch",
                                              "worktree_path", "from", "remote_sha", "range", "commits", "verified",
                                              "unchecked")},
                         {"repo": str(self.src), "remote": "origin", "remote_url": str(self.origin), "target": "main",
                          "upstream": "origin/main", "branch": "task/s1-r2", "worktree_path": str(wt), "from": self.base0,
                          "remote_sha": sha, "range": f"{self.base0[:9]}..{sha[:9]}", "commits": [f"{sha[:7]} patch round"],
                          "verified": True, "unchecked": "patch 模式：整体检查在 accept-integrate-shop.json"})
        self.assertIn("没有集成状态", rec["check_gap"])
        self.assertRegex(rec["at"], r"^\d\d-\d\d \d\d:\d\d:\d\d$")

    def test_upstream_docs_only(self) -> None:
        """上游只改文档 → rebase、needs_reverify false、base 回写，这次不推；再跑一次推出并核对：检查时的树和现在只差文档，
        通过标记带到新提交上（check.docs_only_after 列出差的文件）"""
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
        self.assertEqual(res2["check"]["docs_only_after"], ["docs/notes.md"])
        self.assertEqual(self.records()[-1]["check"]["docs_only_after"], ["docs/notes.md"])

    def test_upstream_code_change_writes_back_base(self) -> None:
        """上游改代码 → rebase、needs_reverify true、base 回写；integrate 再加条目时读到回写后的 base；不补检查直接 push
        被拒（检查之后又有改动），补检查后才推"""
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
        refused = self.push(self.wt, rc=1)
        self.assertIn("检查之后又有改动", refused["error"])
        self.assertIn("app/core.py", refused["error"])
        self.assertEqual(self.remote_head(), up)
        self.check(self.wt)
        res3 = self.push(self.wt)
        self.assertTrue(res3["pushed"] and res3["verified"])
        self.assertEqual(self.remote_head(), self.head(self.wt))

    def test_add_add_conflict_then_manual_resolve(self) -> None:
        """上游制造 add/add 冲突 → push 报冲突 → 人工解完 → push 发现树和检查时不同、拒绝（base 更新、标 manual）→
        补检查后再 push 推出。不再留"先停一次"的标记"""
        up = self.upstream_commit("upstream adds a.py", {"app/a.py": "A = 'upstream'\n"})
        res = self.push(self.wt, rc=1)
        self.assertFalse(res["ok"])
        self.assertIn("冲突", res["error"])
        self.assertEqual(res["conflicts"], ["app/a.py"])
        self.assertNotIn("pending_reverify", self.load_state())
        (self.wt / "app" / "a.py").write_text("A = 1  # merged with upstream\n")
        self.git(self.wt, "add", "app/a.py")
        self.git(self.wt, "-c", "core.editor=true", "rebase", "--continue")
        stop = self.push(self.wt, rc=1)
        self.assertIn("检查之后又有改动", stop["error"])
        self.assertEqual(self.remote_head(), up)
        state = self.load_state()
        self.assertEqual(state["base"], up)
        self.assertEqual({k: state["rebases"][-1].get(k) for k in ("from", "to", "manual")},
                         {"from": self.base0, "to": up, "manual": True})
        self.check(self.wt)
        final = self.push(self.wt)
        self.assertTrue(final["pushed"] and final["verified"])
        self.assertEqual(self.remote_head(), self.head(self.wt))

    def test_manual_rebase_marked_manual(self) -> None:
        """手动 rebase 过（上游已在 HEAD 里但不是记录的 base）→ base 更新、rebases 记 manual；上游只改了文档，照常推送"""
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

    # ---- 只推整体检查验过的内容

    def drop_check(self) -> None:
        """模拟旧版 drover 写的集成状态：没有整体检查结论。"""
        state = self.load_state()
        state.pop("check", None)
        self.state_file().write_text(json.dumps(state))

    def test_refuses_without_check(self) -> None:
        """集成状态里没有整体检查结论（accept --integration 没在这个 worktree 上跑过），任务目录里只有一份写着没通过、
        提交也对不上的存档 → 不推，说明差在哪、怎么补"""
        self.drop_check()
        (self.state / self.SLUG / "accept-integrate-shop.json").write_text(json.dumps({
            "ok": True, "worktree_path": str(self.wt), "base": self.base0, "head": "0" * 40, "status_porcelain": [],
            "verify": [{"command": "pytest", "ok": False, "exit_code": 1}], "verify_failed": True}))
        res = self.push(self.wt, rc=1)
        self.assertIn("没做整体检查", res["error"])
        self.assertIn("accept", res["error"])
        self.assertIn("--integration", res["error"])
        self.assertEqual(self.remote_head(), self.base0)
        self.assertEqual(self.records(), [])

    def test_refuses_failed_check_unless_unchecked(self) -> None:
        """整体检查没过（假 pytest 报 1 条失败）→ 不推；--unchecked 带理由才推，理由和差在哪记进 pushes.jsonl"""
        self.commit(self.wt, "pytest fixture", {"fake-pytest.json": json.dumps({"failures": ["tests/test_a.py::test_x"]})})
        res = self.check(self.wt)
        self.assertTrue(res["verify_failed"])
        refused = self.push(self.wt, rc=1)
        self.assertIn("整体检查没过", refused["error"])
        self.assertIn("--unchecked", refused["error"])
        self.assertEqual(self.remote_head(), self.base0)
        pushed = self.push(self.wt, "--unchecked", "用户确认这条失败是已知的旧问题")
        self.assertTrue(pushed["pushed"] and pushed["verified"])
        self.assertEqual(pushed["unchecked"], "用户确认这条失败是已知的旧问题")
        rec = self.records()[-1]
        self.assertEqual(rec["unchecked"], "用户确认这条失败是已知的旧问题")
        self.assertIn("整体检查没过", rec["check_gap"])
        self.assertNotIn("check", rec)

    def test_refuses_commit_after_check(self) -> None:
        """整体检查通过在 H1，之后又提交了一个没检查过的代码改动 H2 → 不推，列出多出来的提交和改了的文件；补检查后推出"""
        checked = self.head(self.wt)
        self.commit(self.wt, "late change", {"app/late.py": "L = 1\n"})
        refused = self.push(self.wt, rc=1)
        self.assertIn("检查之后又有改动", refused["error"])
        self.assertIn(checked[:9], refused["error"])
        self.assertIn("late change", refused["error"])
        self.assertIn("app/late.py", refused["error"])
        self.assertEqual(self.remote_head(), self.base0)
        self.check(self.wt)
        self.assertTrue(self.push(self.wt)["verified"])

    def test_docs_commit_after_check_pushes(self) -> None:
        """检查之后只加了文档（dispatch.md 9.1：只改了文档不用重跑）→ 照推，记录里列出检查之后差的文档"""
        self.commit(self.wt, "notes", {"docs/notes.md": "notes\n"})
        res = self.push(self.wt)
        self.assertTrue(res["pushed"] and res["verified"])
        self.assertEqual(res["check"]["docs_only_after"], ["docs/notes.md"])

    def test_check_from_legacy_archive(self) -> None:
        """过渡：旧版 drover 的 accept --integration 没往集成状态里记结论 → 按内容找任务目录里的存档（worktree 相同、head 的树
        等于 HEAD 的树、有 verify、全部通过、检查时干净）算检查过，返回标 check_from: archive 并给 warning；
        存档里检查时有没提交的改动的不算"""
        self.drop_check()
        res = self.push(self.wt, "--dry-run")
        self.assertIsNone(res["check_gap"])
        archive = self.state / self.SLUG / "accept-integrate-shop.json"
        raw = json.loads(archive.read_text())
        archive.write_text(json.dumps({**raw, "status_porcelain": ["?? stray.txt"]}))
        dirty = self.push(self.wt, rc=1)
        self.assertIn("没做整体检查", dirty["error"])
        archive.write_text(json.dumps(raw))
        pushed = self.push(self.wt)
        self.assertTrue(pushed["pushed"] and pushed["verified"])
        self.assertEqual((pushed["check"]["check_from"], pushed["check"]["saved_to"]), ("archive", str(archive)))
        self.assertTrue(any("旧版" in w for w in pushed["warnings"]), pushed["warnings"])

    def test_ls_remote_reads_exact_branch(self) -> None:
        """远端另有一条 hotfix/main：推完的 ls-remote 只认 refs/heads/main，verified、记录里的 remote_sha 都是 HEAD"""
        self.git(self.up, "checkout", "-q", "-b", "hotfix/main")
        self.commit(self.up, "hotfix work", {"app/h.py": "H = 1\n"})
        self.git(self.up, "push", "-q", "origin", "hotfix/main")
        self.git(self.up, "checkout", "-q", "main")
        head = self.head(self.wt)
        res = self.push(self.wt)
        self.assertTrue(res["ok"] and res["verified"], res)
        self.assertEqual((res["remote_sha"], self.records()[-1]["remote_sha"]), (head, head))

    def test_new_remote_branch_needs_create_from(self) -> None:
        """目标分支远端还没有：integrate 报清楚是新建远程分支、要用户定从哪条建（--create-from）；带了从源分支建集成
        worktree；目标其实已有却带 --create-from 拒绝；push 照常核对整体检查，推上去建出分支，记录 from 是源分支的提交"""
        target = "release/2026-11"
        args = ["integrate", "--repo", self.REPO, "--slug", self.SLUG, "--target", target, "--from", self.a]
        refused = self.drover_json(*args, rc=1)
        self.assertIn("新建远程分支", refused["error"])
        self.assertIn(f"--target {target} --create-from origin/<源分支>", refused["error"])
        made = self.drover_json(*args, "--create-from", "origin/main")
        self.assertEqual((made["upstream"], made["base"], made["reused"]), (f"origin/{target}", self.base0, False))
        self.assertEqual([p["subject"] for p in made["picked"]], ["item a"])
        self.assertEqual(self.load_state(target)["create_from"], "origin/main")
        wrong = self.drover_json("integrate", "--repo", self.REPO, "--slug", self.SLUG, "--target", "main",
                                 "--from", self.a, "--create-from", "origin/main", rc=1)
        self.assertIn("远端已有分支 main", wrong["error"])
        wt = Path(made["worktree_path"])
        unchecked = self.drover_json("push", "--wt", str(wt), "--target", target, rc=1)
        self.assertIn("没做整体检查", unchecked["error"])
        self.check(wt)
        pushed = self.drover_json("push", "--wt", str(wt), "--target", target)
        self.assertTrue(pushed["pushed"] and pushed["verified"])
        rec = self.records()[-1]
        self.assertEqual((rec["from"], rec["created_from"], rec["remote_sha"]), (self.base0, "origin/main", self.head(wt)))
        self.assertEqual(self.git(self.tmp, "ls-remote", str(self.origin), f"refs/heads/{target}").split()[0],
                         self.head(wt))

    def test_upstream_deps_and_migration_files_classified(self) -> None:
        """上游改的依赖 / 工具链文件归 deps：deps 指纹认的（pnpm-workspace.yaml、.npmrc、.python-version）和 deps 不装的
        （.nvmrc、requirements/ 目录、Pipfile、Cargo.lock、gradle.properties）；alembic/versions 和迁移目录外的 .sql 归迁移
        （push 和 wrapup 用同一个正则）"""
        deps = {".nvmrc": "20\n", "pnpm-workspace.yaml": "packages: []\n", ".python-version": "3.12\n", ".npmrc": "x=1\n",
                "requirements/base.txt": "x\n", "Pipfile": "x\n", "Cargo.lock": "x\n", "gradle.properties": "x=1\n"}
        mig = {"alembic/versions/0001_x.py": "x = 1\n", "sql/patch_001.sql": "SELECT 1;\n"}
        self.upstream_commit("upstream toolchain", {**deps, **mig})
        res = self.push(self.wt)
        self.assertTrue(res["needs_reverify"])
        self.assertEqual(sorted(res["upstream_files"]["deps"]["files"]), sorted(deps))
        self.assertEqual(sorted(res["upstream_files"]["migrations"]["files"]), sorted(mig))
        self.assertEqual(res["upstream_files"]["code"]["count"], 0)
        self.assertIn("migrations_note", res)

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
