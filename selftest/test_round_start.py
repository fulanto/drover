"""新一轮的起点：任务在这个仓库有推送记录时，wt 不传 --base 从上一轮推的远端分支起；
建之前核实起点含上一轮推的提交（同一条分支看祖先，换了分支按补丁内容比），不含就 ok:false、不建，列出缺的提交和两种做法；
--carry 带上缺的提交照常建、理由记进 carry.jsonl；推过两条分支不传 --base 要求显式指定；integrate 建集成 worktree 同样核实；
没有推送记录（第一轮）起点照旧。origin 换了地址（RemoteMovedTests）：推到旧地址的推送不拿来定起点、不核实、不进 --carry；
没记地址的旧记录照旧核实，拒绝时说明怎么补。

仓库布局同 integrate（selftest_repos）：本地裸仓库当 origin，tmp/upstream 模拟别人往上游推。推送记录由真的 wt + push 写
（patch 模式：集成 worktree 是 wt 建的、没有集成状态文件）。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from selftest_repos import IntegrationCase
from selftest_support import DroverTestCase

SLUG = "r1"


class RoundStartCase(IntegrationCase):
    SLUG = SLUG

    def setUp(self) -> None:
        super().setUp()
        (self.state / SLUG).mkdir(parents=True, exist_ok=True)
        (self.state / SLUG / "dispatch.md").write_text(f"# dispatch: {SLUG}\n\n涉及仓库：shop\n目标：测新一轮的起点\n")
        self.git(self.src, "push", "-q", "origin", "main:release")

    def wt(self, branch: str, *extra: str, rc: int = 0) -> dict:
        return self.drover_json("wt", "--repo", str(self.src), "--branch", branch, "--label", f"shop:{branch}",
                                *extra, rc=rc)

    def creates(self) -> list:
        return [c for c in self.herdr_calls() if c[:2] == ["worktree", "create"]]

    def ship(self, target: str, name: str, files: dict, *extra: str) -> tuple[Path, str]:
        """一轮 patch 模式：wt --base origin/<target> 建集成 worktree、提交、push（不传 --slug，按分支 task/r1-… 归属；
        没有集成状态，带 --unchecked）。返回 (集成 worktree, 推上去的提交)。"""
        res = self.wt(f"task/{SLUG}-{name}", "--base", f"origin/{target}", *extra)
        wt = Path(res["worktree_path"])
        sha = self.commit(wt, f"{name} work", files)
        pushed = self.drover_json("push", "--wt", str(wt), "--target", target, "--unchecked", "自测：patch 模式")
        self.assertTrue(pushed["pushed"] and pushed["verified"], pushed)
        self.assertEqual((pushed["task"], pushed["task_from"]), (SLUG, f"worktree 分支 task/{SLUG}-{name}"))
        return wt, sha

    def new_branch(self, name: str, start: str = "main", pick: str = "") -> str:
        """别人在上游从 start 拉一条新分支 name（pick 给了就先把这个提交 cherry-pick 过去），返回分支头。"""
        self.git(self.up, "fetch", "-q", "origin")
        self.git(self.up, "checkout", "-q", "-b", name, f"origin/{start}")
        if pick:
            self.git(self.up, "cherry-pick", pick)
        sha = self.commit(self.up, f"{name} base", {f"docs/{name}.md": f"{name}\n"})
        self.git(self.up, "push", "-q", "origin", name)
        self.git(self.up, "checkout", "-q", "main")
        return sha

    def records(self, name: str = "pushes.jsonl") -> list:
        path = self.state / SLUG / name
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class RoundStartTests(RoundStartCase):
    def test_default_base_is_last_pushed_branch(self) -> None:
        """有推送记录、不传 --base → 起点是上一轮推的 origin/release（不跟主检出的 main），核实按祖先通过"""
        _, sha = self.ship("release", "int", {"app/r.py": "R = 1\n"})
        rec = self.records()
        self.assertEqual(len(rec), 1)
        self.assertEqual((rec[0]["target"], rec[0]["remote_sha"], rec[0]["branch"]), ("release", sha, f"task/{SLUG}-int"))
        self.upstream_commit("main moves on", {"app/m.py": "M = 1\n"})
        res = self.wt(f"task/{SLUG}-b")
        self.assertEqual(self.creates()[-1][self.creates()[-1].index("--base") + 1], "origin/release")
        self.assertEqual((res["base_ref"], res["base_from"], res["task"]), ("origin/release", "这个任务上一轮推的分支", SLUG))
        self.assertEqual((res["round_check"]["status"], res["round_check"]["how"]), ("ok", "ancestor"))
        self.assertEqual(res["head_sha"], sha)

    def test_no_records_start_unchanged(self) -> None:
        """没有推送记录（第一轮）：带不带 --slug，起点都和以前一样（主检出跟上游，这里是 HEAD），round_check 是 no_records"""
        plain = self.wt("task/other-a")
        with_slug = self.wt(f"task/{SLUG}-a")
        self.assertEqual([c[c.index("--base") + 1] for c in self.creates()], ["HEAD", "HEAD"])
        self.assertIsNone(plain["task"])
        self.assertEqual(plain["round_check"]["status"], "no_records")
        self.assertEqual((with_slug["task"], with_slug["round_check"]["status"], with_slug["base_from"]),
                         (SLUG, "no_records", None))

    def test_missing_commits_refused_with_choices(self) -> None:
        """换到不含上一轮提交的新分支 → ok:false、不建 worktree，列出缺的提交（短 sha、标题、推到哪、什么时候）和两种做法；
        --dry-run 只预测（would_refuse），不报错"""
        _, sha = self.ship("release", "int", {"app/r.py": "R = 1\n"})
        self.new_branch("release2")
        before = len(self.creates())
        res = self.wt(f"task/{SLUG}-b", "--base", "origin/release2", rc=1)
        self.assertFalse(res["ok"])
        self.assertEqual(len(self.creates()), before)
        self.assertEqual([m["sha"] for m in res["missing"]], [sha])
        m = res["missing"][0]
        self.assertEqual((m["short"], m["subject"], m["pushed_to"]), (sha[:9], "int work", "origin/release"))
        self.assertIn(f"{sha[:9]} int work", res["error"])
        self.assertIn("--carry", res["error"])
        self.assertIn("--base origin/release", res["error"])
        self.assertEqual([c["do"] for c in res["choices"]], ["carry", "base"])
        # 记录里有远端地址、origin 没换过：是真的漏了，没有"旧记录没记地址"那段说明
        self.assertEqual(res["no_remote_url"], [])
        self.assertNotIn("remote_url", res["error"])
        dry = self.wt(f"task/{SLUG}-b", "--base", "origin/release2", "--dry-run")
        self.assertTrue(dry["round_check"]["would_refuse"])
        self.assertEqual(len(self.creates()), before)

    def test_carry_creates_and_records_reason(self) -> None:
        """--carry 带理由 → 建在新分支上、先把缺的提交 cherry-pick 进来（head_sha 在它之后），理由记进 carry.jsonl"""
        _, sha = self.ship("release", "int", {"app/r.py": "R = 1\n"})
        top = self.new_branch("release2")
        res = self.wt(f"task/{SLUG}-b", "--base", "origin/release2", "--carry", "用户定了带上上一轮的提交")
        wt = Path(res["worktree_path"])
        self.assertEqual(res["round_check"]["status"], "missing")
        self.assertEqual([c["sha"] for c in res["carried"]], [sha])
        self.assertEqual(self.head(wt, "HEAD~1"), top)
        self.assertEqual(self.git(wt, "log", "-1", "--format=%s"), "int work")
        self.assertEqual(res["head_sha"], self.head(wt))
        self.assertEqual((wt / "app" / "r.py").read_text(), "R = 1\n")
        carry = self.records("carry.jsonl")
        self.assertEqual(len(carry), 1)
        self.assertEqual((carry[0]["reason"], carry[0]["status"], carry[0]["start"]),
                         ("用户定了带上上一轮的提交", "picked", "origin/release2"))
        # 条目的起点记的是带上之前的提交：integrate 摘条目提交时把带上的也一起摘进来
        start = self.records("starts.jsonl")[-1]
        self.assertEqual((start["branch"], start["start"], start["worktree_path"]),
                         (f"task/{SLUG}-b", top, str(wt)))

    def test_whitespace_variant_on_new_branch_refused(self) -> None:
        """新分支里只有上一轮提交的"只改了缩进"的版本（Python 里语义不同）：补丁按保留空白比，不算已包含 → 拒绝、列出缺的提交"""
        _, sha = self.ship("release", "int", {"app/fmt.py": "def f():\n        return 1\n"})
        self.git(self.up, "fetch", "-q", "origin")
        self.git(self.up, "checkout", "-q", "-b", "release2", "origin/main")
        self.commit(self.up, "fmt reindented", {"app/fmt.py": "def f():\n    return 1\n"})
        self.git(self.up, "push", "-q", "origin", "release2")
        self.git(self.up, "checkout", "-q", "main")
        res = self.wt(f"task/{SLUG}-b", "--base", "origin/release2", rc=1)
        self.assertEqual([m["sha"] for m in res["missing"]], [sha])

    def test_patch_equivalent_on_new_branch_passes(self) -> None:
        """新分支里有上一轮提交挑过去的副本（提交号不同、补丁相同）→ 按补丁内容算通过"""
        _, sha = self.ship("release", "int", {"app/r.py": "R = 1\n"})
        self.new_branch("release2", pick=sha)
        res = self.wt(f"task/{SLUG}-b", "--base", "origin/release2")
        self.assertEqual((res["round_check"]["status"], res["round_check"]["how"]), ("ok", "patch"))
        self.assertFalse(res["round_check"]["same_branch"])

    def test_two_pushed_branches_need_explicit_base(self) -> None:
        """推过两条分支（release、main）→ 不传 --base 不猜，ok:false 列出两条和各自最后一次推送；显式 --base 照常建"""
        self.ship("release", "int", {"app/r.py": "R = 1\n"})
        # main 不含推到 release 的提交：第二条分支的集成 worktree 要 --carry 才建得起来（另一条集成时各自核实）
        self.ship("main", "int2", {"app/m.py": "M = 1\n"}, "--carry", "两条分支都要有")
        res = self.wt(f"task/{SLUG}-b", rc=1)
        self.assertEqual([b["ref"] for b in res["pushed_branches"]], ["origin/release", "origin/main"])
        self.assertIn("--base", res["error"])
        ok = self.wt(f"task/{SLUG}-b", "--base", "origin/main")
        self.assertEqual(ok["round_check"]["status"], "ok")

    def test_integrate_checks_and_carry_does_not_lose_commits(self) -> None:
        """commit 模式：integrate --target release2 的 --from 里没有上一轮的提交 → 被挡住（选项是 --target release），
        带 --carry 建好、先带上；条目 worktree 用 --carry 建过的（条目分支里有带上的提交）→ 不用再 --carry，按 --from 算覆盖，
        摘条目提交时一起摘进来。推上去后 release2 上一轮的、这一轮的都有"""
        _, sha = self.ship("release", "int", {"app/r.py": "R = 1\n"})
        self.new_branch("release2")
        plain = self.tmp / "plain-wt"
        self.git(self.src, "fetch", "-q", "origin", "release2")
        self.git(self.src, "worktree", "add", "-q", "-b", f"task/{SLUG}-plain", str(plain), "origin/release2")
        self.commit(plain, "plain change", {"app/plain.py": "P = 1\n"})
        args = ["integrate", "--repo", self.REPO, "--slug", SLUG, "--target", "release2"]
        refused = self.drover_json(*args, "--from", f"task/{SLUG}-plain", rc=1)
        self.assertIn("--target release", refused["error"])
        self.assertEqual([m["sha"] for m in refused["missing"]], [sha])
        res = self.drover_json(*args, "--from", f"task/{SLUG}-plain", "--carry", "带上")
        self.assertEqual([c["sha"] for c in res["carried"]], [sha])
        self.assertEqual([p["subject"] for p in res["picked"]], ["plain change"])
        item = self.wt(f"task/{SLUG}-item", "--base", "origin/release2", "--carry", "带上")
        self.commit(Path(item["worktree_path"]), "item change", {"app/item.py": "I = 1\n"})
        more = self.drover_json(*args, "--branch", f"task/{SLUG}-int2", "--from", f"task/{SLUG}-item")
        self.assertEqual((more["round_check"]["status"], more["round_check"]["covered_by"]),
                         ("ok", [f"task/{SLUG}-item"]))
        self.assertNotIn("carried", more)
        self.assertEqual([p["subject"] for p in more["picked"]], ["int work", "item change"])
        self.check(Path(more["worktree_path"]))
        pushed = self.drover_json("push", "--wt", more["worktree_path"], "--target", "release2", "--slug", SLUG)
        self.assertTrue(pushed["verified"])
        subjects = self.git(self.tmp, "--git-dir", str(self.origin), "log", "--format=%s", "release2").splitlines()
        self.assertEqual(subjects[:3], ["item change", "int work", "release2 base"])

    def test_patch_mode_carry_does_not_lose_commits(self) -> None:
        """patch 模式：条目 worktree --carry 建、worker 不提交；补丁对 head_sha（BASE_SHA）取，只含 worker 的改动；集成 worktree
        用 wt --base origin/release2 --carry 建，叠补丁、提交、推送 → release2 里上一轮的和这一轮的都有"""
        self.ship("release", "int", {"app/r.py": "R = 1\n"})
        self.new_branch("release2")
        item = self.wt(f"task/{SLUG}-item", "--base", "origin/release2", "--carry", "带上")
        iwt = Path(item["worktree_path"])
        (iwt / "app" / "item.py").write_text("I = 2\n")
        self.git(iwt, "add", "-N", ".")
        patch = self.git(iwt, "diff", "--binary", item["head_sha"]) + "\n"
        self.assertNotIn("app/r.py", patch)
        self.wt(f"task/{SLUG}-r2", "--base", "origin/release2", rc=1)
        integ = self.wt(f"task/{SLUG}-r2", "--base", "origin/release2", "--carry", "带上")
        cwt = Path(integ["worktree_path"])
        (self.tmp / "item.patch").write_text(patch)
        self.git(cwt, "apply", "--3way", "--index", str(self.tmp / "item.patch"))
        self.git(cwt, "commit", "-q", "-m", "item via patch")
        pushed = self.drover_json("push", "--wt", str(cwt), "--target", "release2", "--unchecked", "自测：patch 模式")
        self.assertTrue(pushed["verified"])
        files = self.git(self.tmp, "--git-dir", str(self.origin), "ls-tree", "-r", "--name-only", "release2").splitlines()
        self.assertIn("app/r.py", files)
        self.assertIn("app/item.py", files)


class RemoteMovedTests(RoundStartCase):
    """origin 换了地址（这次的现场：从一个托管换到另一个，新地址是全新历史）：推送记录里记着推到的远端地址，推到别处的
    不拿来定起点、不拿来核实，也不进 --carry；没记地址的旧记录照旧核实，拒绝时说明怎么补。"""

    def test_origin_moved_to_new_history_not_refused(self) -> None:
        """上一轮推到 origin（记录里有 remote_url），之后 origin 改指一个全新历史的仓库 → wt --slug 不拒绝、从新仓库的 main 起
        （起点按第一轮的规则定），round_check 是 other_remote、列出推到别处的那次，warnings 有提示，没有 carry；--dry-run 不预测
        拒绝"""
        self.ship("main", "int", {"app/r.py": "R = 1\n"})
        old = str(self.origin)
        moved = self.move_origin()
        res = self.wt(f"task/{SLUG}-b", "--slug", SLUG)
        self.assertEqual(self.records()[0]["remote_url"], old)
        new_main = self.git(self.tmp, "--git-dir", str(moved), "rev-parse", "main")
        self.assertEqual((res["base_ref"], res["base_from"], res["head_sha"]), ("origin/main", None, new_main))
        check = res["round_check"]
        self.assertEqual(check["status"], "other_remote")
        self.assertEqual([(p["upstream"], p["remote_url"]) for p in check["other_remote"]], [("origin/main", old)])
        self.assertEqual(check["other_remote"][0]["remote_url_now"], str(moved))
        self.assertIn(f"推到的是 {old}，现在 origin 是 {moved}", check["note"])
        self.assertTrue(any(old in w and str(moved) in w for w in res["warnings"]), res["warnings"])
        self.assertNotIn("carried", res)
        dry = self.wt(f"task/{SLUG}-c", "--slug", SLUG, "--dry-run")
        self.assertEqual(dry["round_check"]["status"], "other_remote")
        self.assertNotIn("would_refuse", dry["round_check"])

    def test_origin_moved_integrate_not_refused(self) -> None:
        """同上一条的现场，integrate 新建集成 worktree：不拒绝、从新仓库的 main 起，round_check 是 other_remote，没有 carry"""
        self.ship("main", "int", {"app/r.py": "R = 1\n"})
        moved = self.move_origin()
        self.git(self.src, "fetch", "-q", "origin", "main")
        item = self.tmp / "item-wt"
        self.git(self.src, "worktree", "add", "-q", "-b", f"task/{SLUG}-item", str(item), "origin/main")
        self.commit(item, "item change", {"app/item.py": "I = 1\n"})
        integ = self.drover_json("integrate", "--repo", self.REPO, "--slug", SLUG, "--target", "main",
                                 "--from", f"task/{SLUG}-item")
        self.assertEqual(integ["round_check"]["status"], "other_remote")
        self.assertNotIn("carried", integ)
        self.assertEqual(integ["base"], self.git(self.tmp, "--git-dir", str(moved), "rev-parse", "main"))
        self.assertEqual([p["subject"] for p in integ["picked"]], ["item change"])

    def test_legacy_record_without_url_refused_with_note(self) -> None:
        """旧版本记的推送（没有 remote_url）+ origin 换了：分不清推到的是不是现在这个远端，照旧拒绝；报错说明这几次推送没记
        远端地址、换过地址就不要 --carry，给出记录所在的文件（pushes.jsonl 里的写它，哪怕记录带着转进来之前的 source；旧
        integrate-*.json 里的写那个文件），no_remote_url 是结构化的同样内容；给那条记录补上当时的地址后重跑不再拒绝"""
        _, sha = self.ship("main", "int", {"app/r.py": "R = 1\n"})
        path = self.state / SLUG / "pushes.jsonl"
        rec = self.records()[0]
        old = rec.pop("remote_url")
        rec["source"] = "integrate-shop.json"  # integrate 重建时从旧集成状态转进 pushes.jsonl 的记录带着它
        path.write_text(json.dumps(rec, ensure_ascii=False) + "\n")
        moved = self.move_origin()
        res = self.wt(f"task/{SLUG}-b", "--slug", SLUG, rc=1)
        self.assertEqual([m["sha"] for m in res["missing"]], [sha])
        self.assertEqual([(p["upstream"], p["file"], p["remote_url_now"]) for p in res["no_remote_url"]],
                         [("origin/main", str(path), str(moved))])
        for want in ("没记远端地址", "不要 --carry", f'"remote_url": "<当时的地址>"', str(path)):
            self.assertIn(want, res["error"])
        self.assertNotIn("integrate-shop.json", res["error"])
        # 记在旧集成状态（integrate-*.json 的 pushes）里的：给出那个文件
        path.unlink()
        legacy = self.state / SLUG / "integrate-shop-old.json"
        state = {"repo": str(self.src), "upstream": "origin/main", "branch": rec["branch"],
                 "pushes": [{k: rec[k] for k in ("at", "range", "commits", "remote_sha", "verified")}]}
        legacy.write_text(json.dumps(state, ensure_ascii=False))
        res = self.wt(f"task/{SLUG}-b", "--slug", SLUG, rc=1)
        self.assertEqual([p["file"] for p in res["no_remote_url"]], [str(legacy)])
        self.assertIn(str(legacy), res["error"])
        # 补上当时的地址 → 推到别处，不拿它核实，起点按第一轮的规则定
        state["pushes"][0]["remote_url"] = old
        legacy.write_text(json.dumps(state, ensure_ascii=False))
        ok = self.wt(f"task/{SLUG}-b", "--slug", SLUG)
        self.assertEqual(ok["round_check"]["status"], "other_remote")
        self.assertNotIn("carried", ok)

    def test_mixed_remotes_only_verify_pushes_here(self) -> None:
        """换地址前推过 release 和 main，换了之后推了 main：只核实推到现在这个地址的。不传 --base 从 origin/main 起（"推过
        两条以上分支"只看推到这里的），换地址前推到 main 的不算缺；推到别处的列在 other_remote、进 warnings"""
        self.ship("release", "int", {"app/r.py": "R = 1\n"})
        self.ship("main", "int2", {"app/m.py": "M = 1\n"}, "--carry", "两条分支都要有")
        old = str(self.origin)
        moved = self.move_origin()
        _, sha = self.ship("main", "int3", {"app/n.py": "N = 1\n"})
        self.assertEqual(self.records()[-1]["remote_url"], str(moved))
        res = self.wt(f"task/{SLUG}-c")
        self.assertEqual((res["base_ref"], res["base_from"], res["head_sha"]), ("origin/main", "这个任务上一轮推的分支", sha))
        check = res["round_check"]
        self.assertEqual((check["status"], check["how"], check["against"]), ("ok", "ancestor", "origin/main"))
        self.assertEqual([(p["upstream"], p["remote_url"]) for p in check["other_remote"]],
                         [("origin/release", old), ("origin/main", old)])
        self.assertTrue(any(old in w and str(moved) in w for w in res["warnings"]), res["warnings"])

    def test_same_repo_other_url_form_still_verified(self) -> None:
        """上一轮推的时候 origin 是 ssh:// 写法，之后 set-url 成同一个仓库的 scp 写法（带用户名、主机大写）：主机 + 路径相同，
        不算推到别处 → 照常核实（同一条分支看祖先），换到不含上一轮提交的分支照旧拒绝。远端是本地裸仓库，两种写法都经假 ssh
        真的 fetch / push"""
        ssh_url = self.ssh_origin(0, "")
        self.git(self.src, "remote", "set-url", "origin", ssh_url)
        _, sha = self.ship("release", "int", {"app/r.py": "R = 1\n"})
        self.assertEqual(self.records()[0]["remote_url"], ssh_url)
        self.git(self.src, "remote", "set-url", "origin", f"git@FAKEHOST:{self.origin}")
        ok = self.wt(f"task/{SLUG}-b")
        check = ok["round_check"]
        self.assertEqual((check["status"], check["how"], ok["base_ref"], ok["head_sha"]),
                         ("ok", "ancestor", "origin/release", sha))
        self.assertNotIn("other_remote", check)
        self.new_branch("release2")  # 从 main 拉，不含推到 release 的提交
        refused = self.wt(f"task/{SLUG}-c", "--base", "origin/release2", rc=1)
        self.assertEqual([m["sha"] for m in refused["missing"]], [sha])
        self.assertEqual((refused["round_check"]["status"], refused["no_remote_url"]), ("missing", []))
        self.assertNotIn("other_remote", refused["round_check"])


class RemoteUrlKeyTests(DroverTestCase):
    """remote_url_key：同一个仓库的几种地址写法比的是主机 + 路径（去掉协议、用户名、端口、末尾的 / 和 .git，主机不分大小写，
    路径分）；file:// 和本地路径比路径。地址都是虚构的。"""

    def test_forms_of_same_repo_equal(self) -> None:
        key = self.load_drover().remote_url_key
        same = ["git@example.com:team/shop.git", "example.com:/team/shop", "ssh://git@example.com/team/shop",
                "ssh://git@Example.COM:2222/team/shop.git", "https://example.com/team/shop",
                "https://someone@example.com/team/shop.git/", "http://example.com:8080/team/shop//",
                "git://example.com/team/shop.git", "  https://EXAMPLE.com/team/shop.git \n"]
        self.assertEqual({u: key(u) for u in same}, {u: "example.com/team/shop" for u in same})

    def test_other_host_or_path_differ(self) -> None:
        key = self.load_drover().remote_url_key
        base = key("git@example.com:team/shop.git")
        for other in ("git@example.org:team/shop.git", "https://example.com/team/shop2",
                      "https://example.com/Team/shop", "ssh://git@example.com/other/shop", "/team/shop"):
            self.assertNotEqual(key(other), base, other)

    def test_file_url_and_local_paths(self) -> None:
        key = self.load_drover().remote_url_key
        self.assertEqual(key("file:///x/y.git"), key("/x/y"))
        self.assertEqual((key("/x/y.git/"), key("../x/y.git")), ("/x/y", "../x/y"))
        self.assertEqual(key("/x/b:c"), "/x/b:c")  # 第一个 / 之前没有 :，是本地路径，不是 scp 写法
        self.assertNotEqual(key("/x/y"), key("/x/z"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
