"""试集成：请用户批推送之前，把各条目没提交的改动在最新上游上叠起来、跑整体检查（不提交到任何条目分支或集成分支、永不推）。
这里测：accept --integration 在试集成 worktree 上记下结论（树是工作区整体状态写成的树）；push 在集成 worktree 上没有覆盖
HEAD 的检查时认同任务、同仓库、同目标的试集成检查（check_from: trial），树对不上照旧拒绝；在试集成 worktree 上 push 被拒；
integrate 的 cherry-pick 冲突时按试集成里对得上的那一步记下的树还原、接着摘（冲突只解一次），对不上照旧停下。

试集成状态（<slug>/trial-<仓库>@<目标>.json）和步骤树在 hand_trial 里按 drover trial 的做法手工造：在最新上游上逐个
cherry-pick -n 条目提交，每一步记（父树, 树, 之前的树, 之后的树）。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Optional

from selftest_repos import IntegrationCase


class TrialCase(IntegrationCase):
    TRIAL_BRANCH = "task/s1--trial-main"

    def trial_file(self) -> Path:
        return self.state / self.SLUG / f"trial-{self.REPO}@main.json"

    def load_trial(self) -> dict:
        return json.loads(self.trial_file().read_text())

    def save_trial(self, state: dict) -> None:
        self.trial_file().parent.mkdir(parents=True, exist_ok=True)
        self.trial_file().write_text(json.dumps(state, ensure_ascii=False, indent=2))

    def tree(self, where: Path, rev: str) -> str:
        return self.git(where, "rev-parse", f"{rev}^{{tree}}")

    def hand_trial(self, *branches: str, resolve: Optional[dict] = None, extra: Optional[dict] = None) -> Path:
        """手工造一份叠完的试集成：从最新的 origin/main 建 worktree（分支 task/s1--trial-main，HEAD 不动），按顺序
        cherry-pick -n 各条目分支的提交（冲突用 resolve[分支] 的文件内容解），每一步 add -A、write-tree 记下；extra 是叠完
        之后的合并修正（写进工作区、不暂存）。写好试集成状态，返回 worktree。"""
        self.git(self.src, "fetch", "-q", "origin")
        wt = self.tmp / "trial-wt"
        self.git(self.src, "worktree", "add", "-q", "-b", self.TRIAL_BRANCH, str(wt), "origin/main")
        base, before, steps = self.head(wt), self.tree(wt, "HEAD"), []
        for b in branches:
            sha = self.git(self.src, "rev-parse", b)
            self.git(wt, "cherry-pick", "-n", sha, check=False)
            conflicts = self.git(wt, "diff", "--name-only", "--diff-filter=U").splitlines()
            if conflicts:
                self.write_files(wt, (resolve or {})[b])
            self.git(wt, "add", "-A")
            after = self.git(wt, "write-tree")
            steps.append({"branch": b, "sha": sha, "subject": self.git(self.src, "log", "-1", "--format=%s", sha),
                          "parent_tree": self.tree(self.src, f"{sha}^"), "tree": self.tree(self.src, sha),
                          "before_tree": before, "after_tree": after,
                          "status": "resolved" if conflicts else "applied", "conflicts": conflicts})
            before = after
        if extra:
            self.write_files(wt, extra)
        self.save_trial({"repo": str(self.src), "upstream": "origin/main", "branch": self.TRIAL_BRANCH,
                         "worktree_path": str(wt), "base": base, "steps": steps, "tree": before})
        return wt

    def trial_check(self, wt: Path, *verify: str, rc: int = 0) -> dict:
        """在试集成 worktree 上跑整体检查（accept --integration，存档 accept-trial-shop.json；默认假 pytest 打 10 passed）。"""
        args = ["accept", "--wt", str(wt), "--base", "HEAD", "--integration", "--brief", "--save", str(self.trial_archive())]
        for c in verify or ("pytest -q",):
            args += ["--verify", c]
        return self.drover_json(*args, rc=rc, env={"DROVER_QUALITY_TOOLS": "0"})

    def trial_archive(self) -> Path:
        return self.state / self.SLUG / f"accept-trial-{self.REPO}.json"


class TrialCheckTests(TrialCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = self.item("a", {"app/a.py": "A = 1\n"})
        self.b = self.item("b", {"app/b.py": "B = 1\n"})
        # 上游在条目起点之后又动了：试集成和正式集成都从最新上游起
        self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})

    def test_accept_records_check_on_trial_worktree(self) -> None:
        """accept --integration 跑在试集成 worktree 上：结论记进试集成状态；检查的树是工作区整体状态（叠上的条目 + 叠完之后
        没暂存的合并修正）写成的树，不是 HEAD 的树；有 verify、全过、叠完、没有没解决的冲突才算通过"""
        wt = self.hand_trial(self.a, self.b, extra={"app/fix.py": "FIX = 1\n"})
        ic = self.trial_check(wt)["integration_check"]
        self.assertTrue(ic["recorded"], ic)
        self.assertEqual(ic["state_file"], str(self.trial_file()))
        self.assertTrue(ic["passed"], ic)
        self.assertEqual(ic["saved_to"], str(self.trial_archive()))
        self.git(wt, "add", "-A")
        whole = self.git(wt, "write-tree")
        self.assertEqual(ic["tree"], whole)
        self.assertNotEqual(ic["tree"], self.tree(wt, "HEAD"))
        self.assertEqual(self.load_trial()["check"]["tree"], whole)
        self.assertEqual(self.head(wt), self.load_trial()["base"])  # 试集成的 HEAD 不动：什么都没提交

    def test_accept_on_trial_refused_until_stacked_and_resolved(self) -> None:
        """试集成还没叠完（状态里没有叠完的树）、或者还有没解决的冲突时，不检查、什么都不动、写明原因：accept 接着的
        git add -N . 会把冲突的 stage 抹掉，试集成就分不出哪些冲突还没解（拒绝之后冲突还在）"""
        c = self.item("c", {"app/a.py": "A = 'c'\n"})
        wt = self.hand_trial(self.a)
        state = self.load_trial()
        self.save_trial({**state, "tree": None})
        half = self.trial_check(wt, rc=1)
        self.assertIn("没叠完", half["error"])
        self.save_trial(state)
        self.git(wt, "cherry-pick", "-n", c, check=False)
        refused = self.trial_check(wt, rc=1)
        self.assertIn("没解决的冲突", refused["error"])
        self.assertEqual(refused["conflicts"], ["app/a.py"])
        self.assertEqual(self.git(wt, "diff", "--name-only", "--diff-filter=U"), "app/a.py")
        self.assertNotIn("check", self.load_trial())
        self.assertFalse(self.trial_archive().exists())

    def test_push_accepts_trial_check_when_tree_matches(self) -> None:
        """正式集成后的树等于试集成验过的树 → 集成 worktree 上不用再跑整体检查，push 认试集成的检查（check_from: trial），
        推送记录里带着它；integrate 的返回里写明和试集成的树对上了"""
        wt = self.hand_trial(self.a, self.b)
        self.trial_check(wt)
        res = self.integrate(self.a, self.b)
        iwt = Path(res["worktree_path"])
        self.assertTrue(res["trial"]["matches_head"], res["trial"])
        self.assertEqual(res["trial"]["state_file"], str(self.trial_file()))
        self.assertNotIn("check", self.load_state())
        pushed = self.push(iwt)
        self.assertTrue(pushed["pushed"] and pushed["verified"], pushed)
        self.assertEqual(self.remote_head(), self.head(iwt))
        self.assertEqual((pushed["check"]["check_from"], pushed["check"]["saved_to"]), ("trial", str(self.trial_archive())))
        self.assertEqual(pushed["check"]["tree"], self.tree(iwt, "HEAD"))
        self.assertEqual(self.records()[-1]["check"]["check_from"], "trial")

    def test_push_refuses_when_tree_differs_then_fix_commit_pushes(self) -> None:
        """试集成里叠完之后还做了合并修正（app/fix.py）→ 正式集成后的树和试集成的不一样：push 照旧拒绝，写明试集成的树和
        HEAD 差几个、哪些文件；按试集成的树还原、提交"集成修正"后树相等，push 认试集成的检查"""
        wt = self.hand_trial(self.a, self.b, extra={"app/fix.py": "FIX = 1\n"})
        checked = self.trial_check(wt)["integration_check"]["tree"]
        res = self.integrate(self.a, self.b)
        iwt = Path(res["worktree_path"])
        self.assertFalse(res["trial"]["matches_head"])
        self.assertEqual(res["trial"]["differs"], ["app/fix.py"])
        before = self.remote_head()
        refused = self.push(iwt, rc=1)
        self.assertIn("没做整体检查", refused["error"])
        self.assertIn("试集成", refused["error"])
        self.assertIn("差 1 个文件", refused["error"])
        self.assertIn("app/fix.py", refused["error"])
        self.assertEqual(self.remote_head(), before)
        self.git(iwt, "restore", f"--source={checked}", "--staged", "--worktree", ":/")
        self.commit(iwt, "集成修正")
        self.assertEqual(self.tree(iwt, "HEAD"), checked)
        pushed = self.push(iwt)
        self.assertTrue(pushed["pushed"] and pushed["verified"], pushed)
        self.assertEqual(pushed["check"]["check_from"], "trial")

    def test_failed_integration_check_on_same_tree_not_overridden_by_trial(self) -> None:
        """集成 worktree 上对同一棵树跑的整体检查没过 → 不拿更早通过的试集成检查盖掉它，照旧拒绝"""
        wt = self.hand_trial(self.a, self.b)
        self.trial_check(wt)
        iwt = Path(self.integrate(self.a, self.b)["worktree_path"])
        failed = self.check(iwt, "python3 -c 'raise SystemExit(1)'")
        self.assertTrue(failed["verify_failed"])
        refused = self.push(iwt, rc=1)
        self.assertIn("整体检查没过", refused["error"])
        self.assertEqual(self.records(), [])

    def test_trial_worktree_never_pushed(self) -> None:
        """试集成 worktree 永远不推：它有没提交的改动、也没有集成状态，push 拒绝，远端不动、不记推送"""
        wt = self.hand_trial(self.a, self.b)
        self.trial_check(wt)
        before = self.remote_head()
        refused = self.push(wt, "--slug", self.SLUG, rc=1)
        self.assertFalse(refused["ok"])
        self.assertEqual(self.remote_head(), before)
        self.assertEqual(self.records(), [])


class TrialReplayTests(TrialCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = self.item("a", {"shared.txt": "from a\n"})
        self.c = self.item("c", {"shared.txt": "from c\n"})
        self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})

    def test_integrate_replays_conflict_resolved_in_trial(self) -> None:
        """试集成里解过 a、c 在 shared.txt 上的冲突；正式 integrate 摘 c 时遇到同一个冲突（提交的父树、树和摘之前的树都和
        试集成那一步一样）→ 按那一步记下的树还原、--continue，不停下；返回 replayed，状态记 resolved 和来自哪份试集成；
        不建 rr-cache（不开 rerere，不影响用户自己在这个仓库的 merge）"""
        self.hand_trial(self.a, self.c, resolve={self.c: {"shared.txt": "from a and c\n"}})
        res = self.integrate(self.a, self.c)
        self.assertEqual([r["subject"] for r in res["replayed"]], ["item c"])
        self.assertEqual(res["replayed"][0]["conflicts"], ["shared.txt"])
        iwt = Path(res["worktree_path"])
        self.assertEqual((iwt / "shared.txt").read_text(), "from a and c\n")
        self.assertEqual(self.tree(iwt, "HEAD"), self.load_trial()["tree"])
        self.assertEqual(self.git(iwt, "status", "--porcelain"), "")
        picked = self.load_state()["picked"]
        self.assertEqual([e["status"] for e in picked], ["picked", "resolved"])
        self.assertEqual(picked[1]["replayed_from_trial"], str(self.trial_file()))
        self.assertTrue(res["trial"]["matches_head"])
        self.assertFalse((self.src / ".git" / "rr-cache").exists())
        again = self.integrate(self.a, self.c)  # 重跑：都已应用
        self.assertEqual((again["picked"], again.get("replayed")), ([], None))

    def test_integrate_stops_when_trial_step_does_not_match(self) -> None:
        """试集成那一步之前的树和正式集成摘之前的树不一样（比如条目顺序不同、中间多了别的改动）→ 不还原，照旧停在冲突上"""
        self.hand_trial(self.a, self.c, resolve={self.c: {"shared.txt": "from a and c\n"}})
        state = self.load_trial()
        state["steps"][1]["before_tree"] = state["steps"][0]["before_tree"]
        self.save_trial(state)
        res = self.integrate(self.a, self.c, rc=1)
        self.assertIn("冲突", res["error"])
        self.assertEqual(res["conflicts"], ["shared.txt"])
        self.assertEqual([e["status"] for e in self.load_state()["picked"]], ["picked", "conflict"])


class TrialCommandTests(TrialCase):
    """drover trial：条目 worktree 由 drover wt 建（起点记进 starts.jsonl），改动可以没提交。"""

    def setUp(self) -> None:
        super().setUp()
        (self.state / self.SLUG).mkdir(parents=True, exist_ok=True)

    def wt_item(self, name: str, files: dict, committed: Optional[dict] = None) -> tuple:
        """drover wt 建条目 worktree；committed 先提交成一个提交，files 写进工作区不提交（新文件不 add）。返回 (分支, worktree)。"""
        res = self.drover_json("wt", "--repo", str(self.src), "--branch", f"task/{self.SLUG}-{name}",
                               "--label", f"shop:{self.SLUG}-{name}", "--slug", self.SLUG)
        wt = Path(res["worktree_path"])
        if committed:
            self.commit(wt, f"item {name}", committed)
        self.write_files(wt, files)
        return res["branch"], wt

    def trial(self, *branches: str, rc: int = 0, extra: tuple = ()) -> dict:
        args = ["trial", "--repo", self.REPO, "--slug", self.SLUG, "--target", "main", *extra]
        for b in branches:
            args += ["--from", b]
        return self.drover_json(*args, rc=rc)

    def sync_src(self) -> None:
        """源仓库主检出先跟上远端：wt 新建 worktree 时会快进落后的主检出（wt 本来的行为，不是试集成干的），拍"之前"之前先做掉。"""
        self.git(self.src, "fetch", "-q", "origin")
        self.git(self.src, "merge", "-q", "--ff-only", "origin/main")

    def git_state(self) -> dict:
        """仓库里所有引用（分支、tag、远端跟踪）、所有 reflog（公共目录的和各 worktree 的 HEAD）、从引用能走到的提交、远端。"""
        common = self.src / ".git"
        return {"refs": self.git(self.src, "for-each-ref", "--format=%(refname) %(objectname)").splitlines(),
                "reflogs": {str(p.relative_to(common)): p.read_text() for p in sorted(common.rglob("*"))
                            if p.is_file() and "logs" in p.relative_to(common).parts},
                "reachable": sorted(self.git(self.src, "rev-list", "--all").splitlines()),
                "remote": self.git(self.tmp, "ls-remote", str(self.origin))}

    def item_view(self, wt: Path) -> tuple:
        """条目 worktree 的 index（逐条）、工作区改动、HEAD：试集成不能动它们。"""
        return self.git(wt, "ls-files", "--stage"), self.git(wt, "diff"), self.git(wt, "status", "--porcelain"), self.head(wt)

    def assert_only_trial_branch_added(self, before: dict, after: dict, base: str) -> None:
        """叠完前后：除了新建的试集成分支（指着上游的提交，上面没有提交）和它的 reflog，所有引用、tag、reflog、能走到的提交、
        远端都不变。"""
        trial_ref = f"refs/heads/{self.TRIAL_BRANCH}"
        self.assertEqual([r for r in after["refs"] if not r.startswith(trial_ref + " ")], before["refs"])
        self.assertIn(f"{trial_ref} {base}", after["refs"])
        new_logs = {k for k in after["reflogs"] if k not in before["reflogs"]}
        self.assertTrue(all(k.endswith(self.TRIAL_BRANCH) or k.endswith("/logs/HEAD") for k in new_logs), new_logs)
        self.assertEqual({k: after["reflogs"][k] for k in before["reflogs"]}, before["reflogs"])
        self.assertEqual(after["reachable"], before["reachable"])
        self.assertEqual(after["remote"], before["remote"])

    def test_stacks_items_in_order_without_committing_anything(self) -> None:
        """按 --from 的顺序叠：条目 a 起点之后的提交、a 没提交的改动（含没 add 的新文件）、条目 b 没提交的改动，各一步，都在
        最新上游上（HEAD 停在上游、不动）。前后所有分支、tag、reflog、能走到的提交、远端都不变（只多了指着上游的试集成分支）；
        条目 worktree 的 index、工作区、HEAD 都没动。重跑什么都不动（幂等）；在试集成 worktree 上 push 被拒"""
        a, wa = self.wt_item("a", {"app/a2.py": "A2 = 1\n", "README.md": "# shop by a\n"}, committed={"app/a.py": "A = 1\n"})
        b, wb = self.wt_item("b", {"app/b.py": "B = 1\n"})
        latest = self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        self.sync_src()
        before, views = self.git_state(), (self.item_view(wa), self.item_view(wb))
        res = self.trial(a, b)
        self.assertEqual([(s["branch"], s["subject"], s["status"]) for s in res["steps"]],
                         [(a, "item a", "applied"), (a, "没提交的改动", "applied"), (b, "没提交的改动", "applied")])
        wt = Path(res["worktree_path"])
        self.assertEqual((res["branch"], res["base"], self.head(wt)), (self.TRIAL_BRANCH, latest, latest))
        for rel, text in {"app/a.py": "A = 1\n", "app/a2.py": "A2 = 1\n", "README.md": "# shop by a\n",
                          "app/b.py": "B = 1\n", "docs/guide.md": "guide\n"}.items():
            self.assertEqual((wt / rel).read_text(), text)
        self.assertEqual(res["tree"], self.load_trial()["tree"])
        self.assertEqual(self.git(wt, "write-tree"), res["tree"])
        self.assertEqual(res["fixes"], [])
        after = self.git_state()
        self.assert_only_trial_branch_added(before, after, latest)
        self.assertEqual((self.item_view(wa), self.item_view(wb)), views)
        again = self.trial(a, b)
        self.assertEqual((again["applied"], again["tree"], again["fixes"]), ([], res["tree"], []))
        self.assertEqual(self.git_state(), after)
        refused = self.push(wt, "--slug", self.SLUG, rc=1)
        self.assertIn("未提交的改动", refused["error"])
        self.assertEqual(self.git_state(), after)

    def test_conflict_resolved_once_then_integrate_and_push_use_trial(self) -> None:
        """整条路：试集成叠到 c 冲突停下（写明怎么解；没解完重跑被拦）→ 解完 git add、重跑接着叠 d → 在试集成上整体检查通过；
        全程引用、reflog、远端不变。用户批了之后按条目提交、integrate：c 的同一个冲突按试集成解过的还原（replayed），树等于
        试集成验过的树 → push 认试集成的检查（check_from: trial），不用在集成 worktree 上再跑"""
        a, wa = self.wt_item("a", {"shared.txt": "from a\n"})
        c, wc = self.wt_item("c", {"shared.txt": "from c\n"})
        d, wd = self.wt_item("d", {"app/d.py": "D = 1\n"})
        latest = self.upstream_commit("upstream docs", {"docs/guide.md": "guide\n"})
        self.sync_src()
        before = self.git_state()
        stop = self.trial(a, c, d, rc=1)
        self.assertEqual(stop["conflicts"], ["shared.txt"])
        wt = Path(stop["worktree_path"])
        self.assertIn(f"git -C {wt} add", stop["error"])
        self.assertIn("再跑一次同样的 drover trial", stop["error"])
        self.assertEqual([s["status"] for s in self.load_trial()["steps"]], ["applied", "conflict"])
        created = self.git_state()
        self.assert_only_trial_branch_added(before, created, latest)
        blocked = self.trial(a, c, d, rc=1)
        self.assertIn("还有没解决的冲突", blocked["error"])
        (wt / "shared.txt").write_text("from a and c\n")
        self.git(wt, "add", "shared.txt")
        done = self.trial(a, c, d)
        self.assertEqual([s["subject"] for s in done["applied"]], ["没提交的改动"])
        self.assertEqual([(s["branch"], s["conflicts"]) for s in done["resolved"]], [(c, ["shared.txt"])])
        self.assertEqual(((wt / "shared.txt").read_text(), (wt / "app/d.py").read_text()), ("from a and c\n", "D = 1\n"))
        self.assertTrue(self.trial_check(wt)["integration_check"]["passed"])
        self.assertEqual(self.git_state(), created)
        # 用户批了推送：按条目提交（编排者 add 全部改动），正式集成
        for name, item_wt in (("a", wa), ("c", wc), ("d", wd)):
            self.commit(item_wt, f"item {name}")
        res = self.integrate(a, c, d)
        self.assertEqual([r["branch"] for r in res["replayed"]], [c])
        iwt = Path(res["worktree_path"])
        self.assertTrue(res["trial"]["matches_head"], res["trial"])
        self.assertIn("不用再跑整体检查", res["next"])
        pushed = self.push(iwt)
        self.assertTrue(pushed["pushed"] and pushed["verified"], pushed)
        self.assertEqual(pushed["check"]["check_from"], "trial")
        self.assertEqual(self.remote_head(), self.head(iwt))
        self.assertEqual(self.tree(iwt, "HEAD"), done["tree"])
        # 收尾时 cleanup 照常收试集成 worktree：没提交的内容都在推上去的分支里
        swept = self.drover_json("cleanup", "--slug", self.SLUG, "--wt", str(wt), "--dry-run")
        self.assertEqual([(w["branch"], w["result"]) for w in swept["worktrees"]], [(self.TRIAL_BRANCH, "would_remove")])

    def test_changed_item_restacked_and_fixes_set_aside(self) -> None:
        """叠完之后在试集成里做的合并修正，重跑时列在 fixes；条目 b 之后又改了 → 重跑只从 b 那一步起重叠（a 不动），重叠前把
        合并修正存成 patch（saved_fixes，能在试集成上重新打回去）"""
        a, _ = self.wt_item("a", {"app/a.py": "A = 1\n"})
        b, wb = self.wt_item("b", {"app/b.py": "B = 1\n"})
        first = self.trial(a, b)
        wt = Path(first["worktree_path"])
        (wt / "app/fix.py").write_text("FIX = 1\n")
        self.assertEqual(self.trial(a, b)["fixes"], ["app/fix.py"])
        (wb / "app/b.py").write_text("B = 2\n")
        res = self.trial(a, b)
        self.assertEqual([(s["branch"], s["status"]) for s in res["applied"]], [(b, "applied")])
        self.assertEqual((wt / "app/b.py").read_text(), "B = 2\n")
        self.assertFalse((wt / "app/fix.py").exists())
        patch = Path(res["saved_fixes"])
        self.assertIn("app/fix.py", patch.read_text())
        self.git(wt, "apply", "--3way", str(patch))
        self.assertEqual((wt / "app/fix.py").read_text(), "FIX = 1\n")

    def test_upstream_moved_keeps_base_until_restart(self) -> None:
        """上游在试集成之后又动了：重跑不换起点（报 upstream_moved，已解的冲突不白解）；--restart 才从最新上游重来"""
        a, _ = self.wt_item("a", {"app/a.py": "A = 1\n"})
        first = self.trial(a)
        newer = self.upstream_commit("upstream later", {"docs/later.md": "later\n"})
        kept = self.trial(a)
        self.assertEqual(kept["base"], first["base"])
        self.assertEqual(kept["upstream_moved"], {"from": first["base"], "to": newer})
        restarted = self.trial(a, extra=("--restart",))
        wt = Path(restarted["worktree_path"])
        self.assertEqual((restarted["base"], self.head(wt)), (newer, newer))
        self.assertNotIn("upstream_moved", restarted)
        self.assertEqual(((wt / "app/a.py").read_text(), (wt / "docs/later.md").read_text()), ("A = 1\n", "later\n"))

    def test_item_started_on_unpushed_local_commit_refused(self) -> None:
        """条目的起点带着目标分支没有的提交（主检出没推的本地提交）→ 和 integrate 一样停下、列出来交用户定，并写明试集成不带它们"""
        self.commit(self.src, "local only", {"app/local.py": "L = 1\n"})
        a, _ = self.wt_item("a", {"app/a.py": "A = 1\n"})
        res = self.trial(a, rc=1)
        self.assertIn("local only", res["error"])
        self.assertIn("试集成不带这些提交", res["error"])
        self.assertTrue(res["foreign"])

    def test_dry_run_plans_only(self) -> None:
        a, _ = self.wt_item("a", {"app/a.py": "A = 1\n"})
        res = self.trial(a, extra=("--dry-run",))
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["branch"], self.TRIAL_BRANCH)
        self.assertFalse(self.trial_file().exists())
        self.assertFalse([c for c in self.herdr_calls() if c[:2] == ["worktree", "create"] and self.TRIAL_BRANCH in c])


if __name__ == "__main__":
    unittest.main(verbosity=2)
