"""3. integrate：本地裸仓库当 origin，假 herdr 的 worktree create 真的建 git worktree。"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from selftest_repos import IntegrationCase


class IntegrateTests(IntegrationCase):
    def setUp(self) -> None:
        super().setUp()
        (self.state / self.SLUG).mkdir(parents=True, exist_ok=True)

    def wt_item(self, name: str, files: dict) -> str:
        """照 dispatch.md 3.1：drover wt --slug 建条目 worktree（起点记进 starts.jsonl）、提交，返回分支名。"""
        res = self.drover_json("wt", "--repo", str(self.src), "--branch", f"task/{self.SLUG}-{name}",
                               "--label", f"shop:{self.SLUG}-{name}", "--slug", self.SLUG)
        self.commit(Path(res["worktree_path"]), f"item {name}", files)
        return res["branch"]

    def integrate_to(self, target: str, *branches: str, rc: int = 0, extra: tuple = ()) -> dict:
        args = ["integrate", "--repo", self.REPO, "--slug", self.SLUG, "--target", target, *extra]
        for b in branches:
            args += ["--from", b]
        return self.drover_json(*args, rc=rc)

    def push_to(self, wt, target: str, rc: int = 0) -> dict:
        return self.drover_json("push", "--wt", str(wt), "--target", target, "--slug", self.SLUG, rc=rc)

    def others_commit_on(self, branch: str, message: str, files: dict) -> str:
        """别人往远端的 branch 推一个提交，返回 sha。"""
        self.git(self.up, "fetch", "-q", "origin")
        self.git(self.up, "checkout", "-q", "-B", branch, f"origin/{branch}")
        sha = self.commit(self.up, message, files)
        self.git(self.up, "push", "-q", "origin", branch)
        self.git(self.up, "checkout", "-q", "main")
        return sha

    def remote_log(self, branch: str) -> list:
        return self.git(self.tmp, "--git-dir", str(self.origin), "log", "--format=%s", branch).splitlines()

    def test_builds_from_latest_upstream_and_picks_items(self) -> None:
        """先 fetch、从最新上游建集成 worktree，按 --from 顺序 cherry-pick 两个条目；状态、依赖记录在状态目录；
        next 提示的整体检查带 --integration（push 按它记下的结论核对）"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        b = self.item("b", {"app/b.py": "B = 1\n"})
        latest = self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        res = self.integrate(a, b)
        self.assertTrue(res["ok"])
        self.assertEqual(res["base"], latest)
        self.assertEqual([p["subject"] for p in res["picked"]], ["item a", "item b"])
        self.assertEqual(res["skipped"], [])
        self.assertFalse(res["reused"])
        self.assertIn("--integration", res["next"])
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

    # ---- 只摘条目自己的提交（起点记录）

    def test_item_started_on_other_branch_stops_and_lists_foreign(self) -> None:
        """主检出在 dev（比 main 多两个别人的提交，已推到 origin/dev），wt 建的条目从 dev 起、记下起点；integrate --target main
        → 不摘、ok: false，列出起点带着的两个提交（短 sha、标题）、起点在 origin/dev 上，做法是 --target dev 或 --carry-base；
        带 --carry-base 才一起摘，理由和带上的提交记进 carry.jsonl（kind: base）"""
        self.git(self.src, "checkout", "-q", "-b", "dev")
        d1 = self.commit(self.src, "D1 别人在 dev 上的提交", {"app/d1.py": "D1 = 1\n"})
        d2 = self.commit(self.src, "D2 别人在 dev 上的提交", {"app/d2.py": "D2 = 1\n"})
        self.git(self.src, "push", "-q", "-u", "origin", "dev")
        a = self.wt_item("a", {"app/a.py": "A = 1\n"})
        starts = [json.loads(x) for x in (self.state / self.SLUG / "starts.jsonl").read_text().splitlines()]
        self.assertEqual([(s["branch"], s["start"]) for s in starts], [(a, d2)])
        res = self.integrate(a, rc=1)
        self.assertEqual([c["subject"] for c in res["foreign"][0]["commits"]],
                         ["D1 别人在 dev 上的提交", "D2 别人在 dev 上的提交"])
        self.assertEqual(res["foreign"][0]["start_on"], ["origin/dev"])
        self.assertIn(f"{d1[:9]} D1 别人在 dev 上的提交", res["error"])
        self.assertIn("--target dev", res["error"])
        self.assertIn("--carry-base", res["error"])
        self.assertEqual(self.head(Path(res["worktree_path"])), self.git(self.src, "rev-parse", "origin/main"))
        carried = self.integrate(a, extra=("--carry-base", "用户定了带上 dev 的两个提交"))
        self.assertEqual([p["subject"] for p in carried["picked"]],
                         ["D1 别人在 dev 上的提交", "D2 别人在 dev 上的提交", "item a"])
        record = [json.loads(x) for x in (self.state / self.SLUG / "carry.jsonl").read_text().splitlines()][-1]
        self.assertEqual((record["kind"], record["reason"], [c["sha"] for c in record["commits"]]),
                         ("base", "用户定了带上 dev 的两个提交", [d1, d2]))

    def test_item_from_main_pushed_to_release_stops(self) -> None:
        """release 比 main 少一个别人的提交；条目从 main 起，集成到 release → 停下列出那个提交，不摘、不推"""
        self.git(self.src, "push", "-q", "origin", "main:release")
        self.upstream_commit("M1 别人只在 main 上的提交", {"app/m1.py": "M1 = 1\n"})
        a = self.wt_item("a", {"app/a.py": "A = 1\n"})
        res = self.integrate_to("release", a, rc=1)
        self.assertEqual([c["subject"] for c in res["foreign"][0]["commits"]], ["M1 别人只在 main 上的提交"])
        self.assertIn("origin/main", res["foreign"][0]["start_on"])
        self.assertEqual(self.remote_log("release"), ["init"])

    def test_source_local_commit_not_carried_into_push(self) -> None:
        """主检出上有用户没推的本地提交，wt 从 HEAD 起（本地领先）→ integrate 停下列出它（起点不在任何远端分支上）"""
        self.commit(self.src, "用户在主检出上没推的本地提交", {"local_wip.txt": "wip\n"})
        w = self.wt_item("w", {"app/w.py": "W = 1\n"})
        res = self.integrate(w, rc=1)
        self.assertEqual([c["subject"] for c in res["foreign"][0]["commits"]], ["用户在主检出上没推的本地提交"])
        self.assertEqual(res["foreign"][0]["start_on"], [])
        self.assertIn("不在任何远端分支上", res["error"])

    def test_legacy_state_file_and_item_without_start(self) -> None:
        """向后兼容：没有起点记录的条目（旧版 drover 建的）照旧按目标分支没有的提交摘、给 warning；旧名字的集成状态
        integrate-shop.json（不带目标分支）按里面记的 upstream 认，接着用它：复用、摘新条目、整体检查记进它、push 照常"""
        a = self.item("a", {"app/a.py": "A = 1\n"}, record=False)
        self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        first = self.integrate(a)
        self.assertEqual([p["subject"] for p in first["picked"]], ["item a"])
        self.assertTrue(any("没有起点记录" in w and a in w for w in first["warnings"]), first["warnings"])
        legacy = self.state / self.SLUG / f"integrate-{self.REPO}.json"
        self.state_file().rename(legacy)
        b = self.item("b", {"app/b.py": "B = 1\n"}, record=False)
        again = self.integrate(a, b)
        self.assertTrue(again["reused"])
        self.assertEqual(Path(again["state_file"]), legacy)
        self.assertEqual([p["subject"] for p in again["picked"]], ["item b"])
        self.assertFalse(self.state_file().exists())
        wt = Path(again["worktree_path"])
        self.check(wt)
        self.assertTrue(json.loads(legacy.read_text())["check"]["passed"])
        pushed = self.push(wt)
        self.assertTrue(pushed["pushed"] and pushed["verified"])

    # ---- "已应用"要核实

    def test_abort_after_conflict_is_repicked(self) -> None:
        """冲突后编排者 cherry-pick --abort（HEAD 没前进）→ 重跑不算已应用：重摘、又冲突就照样停下，返回单列 abandoned；
        解完 --continue 后重跑才算 resolved"""
        a = self.item("a", {"shared.txt": "from a\n"})
        self.upstream_commit("upstream shared", {"shared.txt": "from upstream\n"})
        res = self.integrate(a, rc=1)
        wt = Path(res["worktree_path"])
        self.git(wt, "cherry-pick", "--abort")
        again = self.integrate(a, rc=1)
        self.assertIn("冲突", again["error"])
        self.assertIn("上次冲突后被放弃", again["error"])
        self.assertEqual([x["subject"] for x in again["abandoned"]], ["item a"])
        (wt / "shared.txt").write_text("from upstream and a\n")
        self.git(wt, "add", "shared.txt")
        self.git(wt, "cherry-pick", "--continue")
        final = self.integrate(a)
        self.assertEqual(final["picked"], [])
        self.assertEqual([s["reason"] for s in final["skipped"]], ["已在集成分支里"])
        self.assertEqual([e["status"] for e in self.load_state()["picked"]], ["resolved"])
        self.assertEqual((wt / "shared.txt").read_text(), "from upstream and a\n")

    def test_picking_leftover_is_repicked(self) -> None:
        """状态里留着 picking（drover 在记下 picking 之后、git 动手之前被杀）→ 重跑照样摘，文件在集成 worktree 里"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        b = self.item("b", {"app/b.py": "B = 1\n"})
        self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        first = self.integrate(a)
        wt = Path(first["worktree_path"])
        state = self.load_state()
        state["picked"].append({"branch": b, "sha": self.git(self.src, "rev-parse", b), "subject": "item b",
                                "status": "picking"})
        self.state_file().write_text(json.dumps(state))
        again = self.integrate(a, b)
        self.assertEqual([p["subject"] for p in again["picked"]], ["item b"])
        self.assertTrue((wt / "app" / "b.py").exists())

    def test_blocked_pick_with_empty_in_path_is_failed(self) -> None:
        """cherry-pick 被未跟踪文件挡住、路径里带 empty（git 的输出里有这个词）→ 照样是 failed（git 没开始），不当空提交跳过"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        e = self.item("e", {"app/empty_list.py": "def handle(xs):\n    return xs or []\n"})
        self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        first = self.integrate(a)
        wt = Path(first["worktree_path"])
        (wt / "app" / "empty_list.py").write_text("UNTRACKED\n")
        res = self.integrate(a, e, rc=1)
        self.assertIn("没能开始", res["error"])
        self.assertEqual([x["status"] for x in self.load_state()["picked"]], ["picked", "failed"])
        self.assertEqual((wt / "app" / "empty_list.py").read_text(), "UNTRACKED\n")

    def test_empty_pick_skipped(self) -> None:
        """条目的改动已经被上游带进来了（git cherry 认不出）→ cherry-pick 停在空提交上：有进行中的 cherry-pick、没有冲突、
        暂存区和 HEAD 一样 → 跳过、记 empty、不留进行中的 cherry-pick"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        latest = self.upstream_commit("upstream takes a", {"app/a.py": "A = 1\n", "docs/guide.md": "guide\n"})
        res = self.integrate(a)
        self.assertEqual([s["reason"] for s in res["skipped"]], ["改动已在集成分支里（空提交）"])
        self.assertEqual([e["status"] for e in self.load_state()["picked"]], ["empty"])
        wt = Path(res["worktree_path"])
        self.assertEqual(self.head(wt), latest)
        self.assertEqual(self.git(wt, "rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD", check=False), "")

    def test_whitespace_only_difference_not_treated_as_applied(self) -> None:
        """上游的提交和条目的提交只差缩进（Python 里语义不同）：git cherry 判补丁相同，integrate 不能当成已在集成分支里跳过，
        照样去摘（这里两边改的是同一处，停在冲突上交编排者）"""
        self.upstream_commit("upstream adds fmt", {"app/fmt.py": "def f():\n        return 1\n"})
        a = self.item("a", {"app/fmt.py": "def f():\n    return 1\n"})
        res = self.integrate(a, rc=1)
        self.assertIn("冲突", res["error"])
        self.assertEqual(res["conflicts"], ["app/fmt.py"])

    def test_patch_judgement_keeps_whitespace_at_all_three_call_sites(self) -> None:
        """补丁相同的公共判断（cherry_marks）：只差空白的不算相同、一模一样的算；integrate、新一轮起点核实（cherry_plus）、
        cleanup 核对已提交的（cleanup_commits）三处都按它"""
        up = self.commit_on("ws-up", {"app/fmt.py": "def f():\n        return 1\n"})
        ours = self.commit_on("ws-ours", {"app/fmt.py": "def f():\n    return 1\n"})
        same = self.commit_on("ws-same", {"app/fmt.py": "def f():\n        return 1\n"})
        d = self.load_drover()
        src = str(self.src)
        self.assertEqual(d.cherry_marks(src, "ws-up", "ws-ours"), [("+", ours)])
        self.assertEqual(d.cherry_marks(src, "ws-up", "ws-same"), [("-", same)])
        self.assertEqual(d.cherry_plus(src, "ws-up", "ws-ours", "main"), [ours])
        self.assertEqual(d.cherry_plus(src, "ws-up", "ws-same", "main"), [])
        ref = [{"ref": "origin/ws-up", "sha": up}]
        self.assertEqual(len(d.cleanup_commits(src, "refs/heads/ws-ours", ref, [], {})["reasons"]), 1)
        self.assertEqual(d.cleanup_commits(src, "refs/heads/ws-same", ref, [], {})["reasons"], [])

    def commit_on(self, branch: str, files: dict) -> str:
        """源仓库从 main 拉 branch、提交一次（说明带分支名，提交号各不相同），切回 main，返回 sha。"""
        self.git(self.src, "checkout", "-q", "-b", branch, "main")
        sha = self.commit(self.src, f"fmt on {branch}", files)
        self.git(self.src, "checkout", "-q", "main")
        return sha

    # ---- 集成 worktree / 状态按（仓库，目标分支）分

    def ship_main_then_release(self, release_has_own: bool) -> tuple:
        """release 从最初的 main 拉；别人往 main 推了 M1（release 没有），release_has_own 时又往 release 推了 R1。
        条目 a 集成、检查、推到 main。返回 (第一条的集成结果, R1 或 None)。"""
        self.git(self.src, "push", "-q", "origin", "main:release")
        self.upstream_commit("M1 别人只在 main 上的提交", {"app/m1.py": "M1 = 1\n"})
        r1 = self.others_commit_on("release", "R1 别人只在 release 上的提交", {"docs/r1.md": "r1\n"}) \
            if release_has_own else None
        a = self.item("a", {"app/a.py": "A = 1\n"})
        first = self.integrate_to("main", a)
        self.check(Path(first["worktree_path"]))
        self.assertTrue(self.push_to(first["worktree_path"], "main")["verified"])
        return first, r1

    def test_second_target_gets_own_worktree(self) -> None:
        """同一轮推第二条分支只换 --target：不复用第一条的集成 worktree，自动另建 task/s1-release、状态另起一份
        integrate-shop@release.json（base 是 origin/release）；拿第一条的 worktree 推 release 被拒；release 上推上去的只有
        条目的提交，没有只在 main 上的 M1"""
        first, r1 = self.ship_main_then_release(release_has_own=True)
        refused = self.push_to(first["worktree_path"], "release", rc=1)
        self.assertIn("origin/main", refused["error"])
        self.assertIn("--target release", refused["error"])
        second = self.integrate_to("release", "task/s1-a")
        self.assertFalse(second["reused"])
        self.assertEqual((second["branch"], second["upstream"], second["base"]), ("task/s1-release", "origin/release", r1))
        self.assertNotEqual(second["worktree_path"], first["worktree_path"])
        self.assertEqual([p["subject"] for p in second["picked"]], ["item a"])
        self.assertEqual(Path(second["state_file"]), self.state_file("release"))
        self.assertEqual(self.load_state("main")["worktree_path"], first["worktree_path"])
        self.check(Path(second["worktree_path"]))
        self.assertTrue(self.push_to(second["worktree_path"], "release")["verified"])
        self.assertEqual(self.remote_log("release"), ["item a", "R1 别人只在 release 上的提交", "init"])

    def test_second_target_that_is_ancestor_gets_no_foreign_commits(self) -> None:
        """同上，release 上没有自己的新提交（是 main 的祖先）：以前一次 push 就把 M1 推进了 release，现在只推条目"""
        self.ship_main_then_release(release_has_own=False)
        second = self.integrate_to("release", "task/s1-a")
        self.check(Path(second["worktree_path"]))
        self.assertTrue(self.push_to(second["worktree_path"], "release")["verified"])
        self.assertEqual(self.remote_log("release"), ["item a", "init"])

    def test_explicit_branch_of_other_target_refused(self) -> None:
        """显式 --branch 指到别的目标的集成 worktree、或 wt 手建的 worktree（没有集成状态）→ 拒绝复用"""
        self.git(self.src, "push", "-q", "origin", "main:release")
        a = self.item("a", {"app/a.py": "A = 1\n"})
        self.integrate_to("main", a)
        res = self.integrate_to("release", a, rc=1, extra=("--branch", "task/s1"))
        self.assertIn("origin/main", res["error"])
        self.drover_json("wt", "--repo", str(self.src), "--branch", "task/s1-hand", "--label", "shop:hand",
                         "--base", "origin/main")
        hand = self.integrate_to("main", a, rc=1, extra=("--branch", "task/s1-hand"))
        self.assertIn("没有集成状态", hand["error"])

    def test_two_integration_worktrees_keep_own_state(self) -> None:
        """一个仓库两个集成 worktree（release 用 --branch 另起）各有一份状态，互不冲掉；回到 main 的那个再 integrate：
        状态还是它的，base 仍是 HEAD 的祖先（集成分支有没推的提交，不快进），diff 里没有上游的改动"""
        self.git(self.src, "push", "-q", "origin", "main:release")
        a = self.item("a", {"shared.txt": "from a\n"})
        self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        first = self.integrate_to("main", a)
        wt_main = Path(first["worktree_path"])
        rel = self.integrate_to("release", a, extra=("--branch", "task/s1-rel"))
        self.assertEqual(self.load_state("release")["worktree_path"], rel["worktree_path"])
        self.assertEqual(self.load_state("main")["worktree_path"], str(wt_main))
        self.upstream_commit("upstream code", {"app/other.py": "O = 1\n"})
        b = self.item("b", {"app/b.py": "B = 1\n"})
        again = self.integrate_to("main", a, b)
        self.assertTrue(again["reused"])
        self.assertEqual(again["worktree_path"], str(wt_main))
        self.assertEqual(again["base"], first["base"])
        self.assertEqual([p["subject"] for p in again["picked"]], ["item b"])
        self.assertNotIn("app/other.py", self.git(wt_main, "diff", "--stat", again["base"]))

    def round_one(self) -> Path:
        a = self.wt_item("a", {"app/a.py": "A = 1\n"})
        res = self.integrate(a)
        wt = Path(res["worktree_path"])
        self.check(wt)
        self.assertTrue(self.push(wt, "--slug", self.SLUG)["verified"])
        self.git(self.up, "pull", "-q", "--ff-only", "origin", "main")
        return wt

    def test_round_two_fast_forwards_reused_worktree(self) -> None:
        """第一轮推完、别人往 main 推了两个代码提交；第二轮的条目从最新 origin/main 起。integrate 复用第一轮的集成 worktree：
        先快进到最新上游、base 跟着更新，只摘条目 b；之后 push 不用 rebase、直接推"""
        wt = self.round_one()
        self.upstream_commit("U1 别人的代码提交", {"app/u1.py": "U1 = 1\n"})
        latest = self.upstream_commit("U2 别人的代码提交", {"app/u2.py": "U2 = 1\n"})
        b = self.wt_item("b", {"app/b.py": "B = 1\n"})
        res = self.integrate(b)
        self.assertTrue(res["reused"])
        self.assertEqual([p["subject"] for p in res["picked"]], ["item b"])
        self.assertEqual(res["base"], latest)
        self.assertEqual(self.head(wt, "HEAD~1"), latest)
        self.check(wt)
        pushed = self.push(wt, "--slug", self.SLUG)
        self.assertTrue(pushed["pushed"] and pushed["verified"])
        self.assertNotIn("rebased", pushed)

    def test_round_two_upstream_merge_not_replayed(self) -> None:
        """同上，上游这段时间有一个解过冲突的合并提交：第二轮不去重放别人的提交，不会停在别人的冲突上"""
        self.round_one()
        self.git(self.up, "checkout", "-q", "-b", "feature-x")
        self.commit(self.up, "X 别人在 feature-x 上改 shared", {"shared.txt": "x\n"})
        self.git(self.up, "checkout", "-q", "main")
        self.commit(self.up, "M 别人在 main 上改 shared", {"shared.txt": "m\n"})
        self.git(self.up, "merge", "-q", "--no-ff", "feature-x", check=False)
        (self.up / "shared.txt").write_text("m + x\n")
        self.git(self.up, "add", "shared.txt")
        self.git(self.up, "commit", "-q", "-m", "Merge feature-x（解过冲突）")
        self.git(self.up, "push", "-q", "origin", "main")
        b = self.wt_item("b", {"app/b.py": "B = 1\n"})
        res = self.integrate(b)
        self.assertEqual([p["subject"] for p in res["picked"]], ["item b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
