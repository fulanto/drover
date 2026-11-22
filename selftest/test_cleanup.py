"""cleanup：用户说"清理"或"收尾"时编排者跑，逐个 worktree 核实内容都在这个任务
推过的远端分支里、里面的 agent 没在干活、没有进行中的 git 操作才删（herdr worktree remove 关掉 workspace 和 agent，再删本地
分支），核实不过的不动、列原因；--dry-run 只列；--probe 先存 patch 再删；--target 给推送记录缺的老任务补核对分支。

仓库布局同 integrate（selftest_repos）：本地裸仓库当 origin。worktree 由 drover wt（假 herdr 真的 git worktree add）建，推送由
真的 integrate / push 写记录；假 herdr 的 worktree remove 真的 git worktree remove（有改动要 --force）并关掉 workspace 里的 agent。
"""

from __future__ import annotations

import argparse
import os
import unittest
from pathlib import Path

from selftest_repos import IntegrationCase

SLUG = "c1"
# 三方合并用例的文件：远端先有它们（别人推的），条目各改一处，远端最后一个提交是"手工解过冲突"后的样子
CODE = "def f():\n    x = 1\n    y = 2\n    return x\n\n\ndef g():\n    return None\n"
TABLE = ("| 文件 | 测什么 |\n|---|---|\n| `test_a.py` | a 的用例 |\n| `test_b.py` | b 的用例 |\n"
         "| `test_c.py` | c 的用例 |\n")
NEG = ("def f():\n    a = 1\n    b = 2\n    return a\n\n\ndef g():\n    c = 3\n    return None\n\n\n"
       "def h():\n    d = 4\n    e = 5\n    return d\n")
# 很长的一行：两边都改了它（远端解冲突时同一行里两边的改动都留着）
LONG = "# 说明\n\n| `test_x.py` | 重试 3 次，每次等 5 秒；看板一行一个任务 |\n\n结尾\n"
# 照一次真实收尾时误留的 README 自测表：条目改了 push（前半）、wrapup 两行，在 wrapup 后面插了
# round_start、cleanup 两行；上游改了旁边的 integrate、wt_sync、heavy（改在行首）、heavy_sched，还在 push 那一行行尾加了一句；
# 集成时两边合在一起。整块按词对齐时，条目插的两行在条目那边和远端那边错开几个词（" |\n| `" 归到上一行末尾还是下一行开头）
REAP_ROWS = (("integrate", "integrate：从上游建、按顺序挑、冲突停下"), ("wt_sync", "wt 同步主检出：落后就快进"),
             ("push", "push：直接推并核对；上游改了就 rebase"),
             ("wrapup", "wrapup：两份记录两行；子编排者的推送记录带上，没有推送记录给提醒、没有状态文件报错"),
             ("heavy", "heavy：两条同时跑，第二条排队并打印在跑的那条"), ("heavy_sched", "heavy 按资源调度：放行判定、需求取实测"))
REAP_UPSTREAM = {"integrate": ("从上游建", "从最新上游建"), "wt_sync": ("落后就快进", "落后就快进，分叉不动"),
                 "push": ("上游改了就 rebase", "上游改了就 rebase；push 遇到永久错误只试 1 次"),
                 "heavy": ("两条同时跑，第二条排队并打印在跑的那条", "整机满了时第二条排队、第一条结束后开始"),
                 "heavy_sched": ("heavy 按资源调度：放行判定、需求取实测", "heavy 按实时 CPU 调度：放行判定")}


def reap_table(rows) -> str:
    """自测表（文件末尾另一节里还有一行 test_misc）。"""
    return ("# 自测\n\n| 文件 | 测什么 |\n|---|---|\n" + "".join(f"| `test_{k}.py` | {v} |\n" for k, v in rows)
            + "\n## 别的\n\n| `test_misc.py` | 杂项 |\n")


def reap_item(push_word: str = "pushes.jsonl", after_round: tuple = (), after_cleanup: tuple = (),
              drop_heavy: bool = False) -> list:
    """条目那边的行：push 改前半、wrapup 改中间、wrapup 后面插两行；参数造反例（push 写成别的词、插的两行中间 / 后面再加行、
    删掉 heavy 那一行）。"""
    rows = []
    for k, v in REAP_ROWS:
        if k == "heavy":
            rows += [("round_start", "新一轮的起点：从上一轮推的分支起"), *after_round, ("cleanup", "cleanup：核实后删"),
                     *after_cleanup]
            if drop_heavy:
                continue
        rows.append((k, v.replace("直接推并核对；", f"直接推并核对、推送记进 `{push_word}`；")
                     .replace("两份记录两行；", "两份记录两行，同一上游的合成一行；")))
    return rows


def reap_upstream(rows) -> list:
    """上游那一轮的改动叠到这些行上。"""
    return [(k, v.replace(*REAP_UPSTREAM[k]) if k in REAP_UPSTREAM else v) for k, v in rows]


def quick(ref: str, sha: str) -> dict:
    return {"ref": ref, "commit": sha[:12], "how": "快路径"}


def merged_at(ref: str, sha: str) -> dict:
    return {"ref": ref, "commit": sha[:12], "how": "三方合并"}


class CleanupCase(IntegrationCase):
    SLUG = SLUG

    def setUp(self) -> None:
        super().setUp()
        d = self.state / SLUG
        d.mkdir(parents=True, exist_ok=True)
        (d / "dispatch.md").write_text(f"# dispatch: {SLUG}\n\n涉及仓库：shop\n目标：测清理\n")

    def wt(self, name: str, base: str = "origin/main", slug: str = SLUG) -> Path:
        res = self.drover_json("wt", "--repo", str(self.src), "--branch", f"task/{slug}-{name}",
                               "--label", f"shop:{slug}-{name}", "--base", base)
        return Path(res["worktree_path"])

    def cleanup(self, *extra: str, rc: int = 0) -> dict:
        return self.drover_json("cleanup", "--slug", SLUG, *extra, rc=rc)

    def ship_commits(self, *branches: str) -> Path:
        """commit 模式：integrate 摘条目分支的提交到集成 worktree，再 push。"""
        args = ["integrate", "--repo", self.REPO, "--slug", SLUG, "--target", "main"]
        for b in branches:
            args += ["--from", b]
        wt = Path(self.drover_json(*args)["worktree_path"])
        self.assertTrue(self.drover_json("push", "--wt", str(wt), "--target", "main",
                                         "--unchecked", "自测：cleanup 用例不测整体检查")["verified"])
        return wt

    def ship_patch(self, item: Path, name: str = "r1") -> Path:
        """patch 模式：条目 worktree 不提交，git diff --binary 导补丁，叠到 wt 建的集成 worktree 里提交、push。"""
        self.git(item, "add", "-N", ".")
        (self.tmp / f"{name}.patch").write_text(self.git(item, "diff", "--binary", "HEAD") + "\n")
        wt = self.wt(name)
        self.git(wt, "apply", "--index", str(self.tmp / f"{name}.patch"))
        self.git(wt, "commit", "-q", "-m", f"ship {name}")
        self.assertTrue(self.drover_json("push", "--wt", str(wt), "--target", "main",
                                         "--unchecked", "自测：cleanup 用例不测整体检查")["verified"])
        return wt

    def remote_commit(self, message: str, files: dict) -> str:
        """别人往远端 main 推一个提交（upstream 克隆先追上远端），返回 sha。"""
        self.git(self.up, "pull", "-q", "--ff-only", "origin", "main")
        return self.upstream_commit(message, files)

    def ws_of(self, wt: Path) -> str:
        return next(w["workspace_id"] for w in self.herdr_state()["worktrees"] if w["path"] == str(wt))

    def removes(self) -> list:
        return [c for c in self.herdr_calls() if c[:2] == ["worktree", "remove"]]

    def has_branch(self, name: str) -> bool:
        return bool(self.git(self.src, "branch", "--list", name))

    @staticmethod
    def by_path(res: dict) -> dict:
        return {Path(e["path"]): e for e in res["worktrees"]}


class CleanupTests(CleanupCase):
    def test_commit_mode_and_clean_integration_removed(self) -> None:
        """commit 模式：条目提交挑到集成分支推了（提交号变了、补丁相同）→ 条目 worktree 删、分支删、workspace 里的 agent 关；
        集成 worktree 干净且 HEAD 在远端 → 也删。都干净，不加 --force"""
        a = self.wt("a")
        self.commit(a, "item a", {"app/a.py": "A = 1\n"})
        integ = self.ship_commits(f"task/{SLUG}-a")
        ws = self.ws_of(a)
        self.set_agents(**{"c1-a": {"status": "idle", "cwd": str(a), "workspace_id": ws}})
        res = self.cleanup()
        self.assertTrue(res["ok"])
        e = self.by_path(res)
        self.assertEqual((e[a]["result"], e[a]["force"]), ("removed", False))
        self.assertEqual(e[a]["remove_command"], f"herdr worktree remove --workspace {ws}")
        self.assertEqual(e[a]["agents"], ["c1-a"])
        self.assertEqual((e[integ]["result"], e[integ]["force"]), ("removed", False))
        self.assertFalse(a.exists() or integ.exists())
        self.assertFalse(self.has_branch(f"task/{SLUG}-a") or self.has_branch(f"task/{SLUG}"))
        self.assertNotIn("c1-a", self.herdr_state()["agents"])
        self.assertIn(ws, self.herdr_state()["removed_workspaces"])
        self.assertEqual(res["refs"][str(self.src)], [{"ref": "origin/main", "source": "推送记录",
                                                      "sha": self.remote_head()}])

    def test_patch_mode_uncommitted_but_pushed_removed_with_force(self) -> None:
        """patch 模式：条目 worktree 改动没提交（已跟踪文件改了、新文件 add -N 过）但内容已推 → 删，带 --force（D2）；
        集成 worktree 干净 → 不带"""
        p = self.wt("p")
        (p / "app" / "core.py").write_text("VALUE = 2\n")
        (p / "app" / "new.py").write_text("N = 1\n")
        integ = self.ship_patch(p)
        res = self.cleanup()
        e = self.by_path(res)
        self.assertEqual((e[p]["result"], e[p]["force"]), ("removed", True))
        self.assertTrue(e[p]["remove_command"].endswith("--force"))
        self.assertEqual(sorted(e[p]["uncommitted"]), ["app/core.py", "app/new.py"])
        self.assertEqual((e[integ]["result"], e[integ]["force"]), ("removed", False))
        self.assertFalse(p.exists())
        self.assertEqual([c[-1] for c in self.removes()].count("--force"), 1)

    def test_kept_with_reasons(self) -> None:
        """不删的：没推的改动（列出文件）、没推的提交（列出 sha 和标题）、agent 在 working（cwd 在里面）、agent blocked
        （在它的 workspace 里，cwd 在别处）、有进行中的 cherry-pick。都不调 herdr worktree remove、分支都在"""
        p = self.wt("p")
        (p / "app" / "core.py").write_text("VALUE = 2\n")
        self.ship_patch(p)
        (p / "app" / "extra.py").write_text("E = 1\n")
        c = self.wt("c")
        sha = self.commit(c, "unpushed work", {"app/c.py": "C = 1\n"})
        w, b, o = self.wt("w"), self.wt("b"), self.wt("o")
        self.set_agents(**{"c1-w": {"status": "working", "cwd": str(w / "app")},
                           "c1-b": {"status": "blocked", "cwd": str(self.tmp), "workspace_id": self.ws_of(b)}})
        self.git(o, "update-ref", "CHERRY_PICK_HEAD", "HEAD")
        res = self.cleanup()
        e = self.by_path(res)
        for path in (p, c, w, b, o):
            self.assertEqual(e[path]["result"], "kept", e[path])
            self.assertTrue(path.is_dir())
        self.assertEqual(e[p]["reasons"], ["没提交的改动 app/extra.py：在 origin/main 的历史里找不到包含这些改动的提交"])
        self.assertEqual(len(e[c]["reasons"]), 2)
        self.assertIn(f"{sha[:9]} unpushed work", e[c]["reasons"][0])
        self.assertEqual(e[c]["reasons"][1], "已提交的改动 app/c.py：在 origin/main 的历史里找不到包含这些改动的提交")
        self.assertIn("agent c1-w 在 working", e[w]["reasons"][0])
        self.assertIn("agent c1-b 在 blocked", e[b]["reasons"][0])
        self.assertIn("CHERRY_PICK_HEAD", e[o]["reasons"][0])
        removed = {c[c.index("--workspace") + 1] for c in self.removes()}
        self.assertFalse(removed & {self.ws_of(x) for x in (p, c, w, b, o)})
        for name in ("p", "c", "w", "b", "o"):
            self.assertTrue(self.has_branch(f"task/{SLUG}-{name}"))
        self.assertEqual(set(self.herdr_state()["agents"]), {"c1-w", "c1-b"})

    def test_uncommitted_in_remote_history_after_later_rounds(self) -> None:
        """patch 模式条目推上去之后，后来的轮次又改了远端的同一处 → 分支头上对不上，但推上去的那个提交（推送记录）的树里有 →
        would_remove / removed；从没推过的照旧 kept，原因写"在它们的历史里也没找到\""""
        p = self.wt("p")
        (p / "app" / "core.py").write_text("VALUE = 2\n")
        pushed = self.head(self.ship_patch(p))
        self.git(self.up, "pull", "-q", "--ff-only", "origin", "main")
        self.upstream_commit("later round", {"app/core.py": "VALUE = 3\n"})
        never = self.wt("n")
        (never / "app" / "core.py").write_text("VALUE = 7\n")
        dry = self.by_path(self.cleanup("--dry-run"))
        self.assertEqual(dry[p]["result"], "would_remove")
        self.assertEqual(dry[p]["uncommitted_found_in"], {"*": quick("origin/main", pushed)})
        self.assertEqual(dry[never]["result"], "kept")
        self.assertEqual(dry[never]["reasons"], ["没提交的改动 app/core.py：在 origin/main 的历史里找不到包含这些改动的提交"])
        e = self.by_path(self.cleanup())
        self.assertEqual((e[p]["result"], e[p]["force"]), ("removed", True))
        self.assertEqual(e[never]["result"], "kept")
        self.assertFalse(p.exists())
        self.assertTrue(never.is_dir())

    def test_uncommitted_split_across_commits_and_history_limit(self) -> None:
        """推送没落盘（只给 --target）时按碰过这些文件的提交往回找：两处改动分两次推、之后其中一处又被改（没有哪个提交的树
        两处都有）→ 逐个文件落在各自的提交上 → 删。往回查有上限：碰过这个文件的提交超过上限、查过的都没对上 → 核实不了、不删"""
        s = self.wt("s")
        (s / "app" / "core.py").write_text("VALUE = 2\n")
        (s / "app" / "two.py").write_text("T = 1\n")
        lim = self.wt("lim")
        (lim / "shared.txt").write_text("mine\n")
        self.git(self.up, "pull", "-q", "--ff-only", "origin", "main")
        x = self.upstream_commit("push core", {"app/core.py": "VALUE = 2\n", "shared.txt": "mine\n"})
        self.upstream_commit("push two, core moves on", {"app/two.py": "T = 1\n", "app/core.py": "VALUE = 4\n"})
        z = self.upstream_commit("shared moves on", {"shared.txt": "later\n"})
        d = self.load_drover()
        d.CLEANUP_HISTORY_LIMIT = 1
        res = d.cmd_cleanup(argparse.Namespace(slug=SLUG, wt=[str(lim)], target=["origin/main"], probe=False,
                                               dry_run=True))
        e = self.by_path(res)
        self.assertEqual(e[lim]["result"], "kept")
        self.assertIn("核实不了", e[lim]["reasons"][0])
        self.assertIn("超过 1 个", e[lim]["reasons"][0])
        # 上限照默认的 200：lim 在历史里（x）找得到；s 逐个文件：core.py 在 x，two.py 分支头（z）上就有 → 都删
        full = self.by_path(self.cleanup("--target", "origin/main", "--wt", str(s), "--wt", str(lim)))
        self.assertEqual((full[s]["result"], full[lim]["result"]), ("removed", "removed"))
        self.assertEqual(full[s]["uncommitted_found_in"], {"app/core.py": quick("origin/main", x),
                                                            "app/two.py": quick("origin/main", z)})
        self.assertEqual(full[lim]["uncommitted_found_in"], {"*": quick("origin/main", x)})

    def test_three_way_contained_removed(self) -> None:
        """严格反向应用对不上（推上去的树里上下文变了）、按文件三方合并判包含 → 删：远端在改动旁边加了行；README 表格式，
        两个条目在相邻处各加一行、远端两行都在；条目改过的那一行，远端解冲突时又在同一行里插了字；同一长行，条目改了 A 处，
        远端 A 处一样、又改了 B 处（换掉了条目那边还留着的 base 原词）"""
        self.remote_commit("seed", {"app/code.py": CODE, "docs/table.md": TABLE, "docs/long.md": LONG})
        ctx, ta, tb, inl, long = self.wt("ctx"), self.wt("ta"), self.wt("tb"), self.wt("inl"), self.wt("long")
        (ctx / "app" / "code.py").write_text(CODE.replace("    y = 2", "    y = 3"))
        row_a = "| `test_a.py` | a 的用例 |\n"
        (ta / "docs" / "table.md").write_text(TABLE.replace(row_a, row_a + "| `test_a2.py` | a2 的用例 |\n"))
        (tb / "docs" / "table.md").write_text(TABLE.replace(row_a, row_a + "| `test_b0.py` | b0 的用例 |\n"))
        (inl / "docs" / "table.md").write_text(TABLE.replace("c 的用例", "c 的用例和边界"))
        (long / "docs" / "long.md").write_text(LONG.replace("一个任务 |", "一个任务，带提醒 |"))
        final = self.remote_commit("integrate round", {
            "app/code.py": CODE.replace("    x = 1\n    y = 2", "    x = 1\n    z = 0\n    y = 3"),
            "docs/table.md": TABLE.replace(row_a, row_a + "| `test_a2.py` | a2 的用例 |\n| `test_b0.py` | b0 的用例 |\n")
            .replace("c 的用例", "自测（2.7）：c 的用例和边界"),
            "docs/long.md": LONG.replace("重试 3 次，每次等 5 秒", "只试 1 次，等 2 秒").replace("一个任务 |", "一个任务，带提醒 |")})
        res = self.cleanup("--target", "origin/main", "--dry-run")
        e = self.by_path(res)
        for wt, path in ((ctx, "app/code.py"), (ta, "docs/table.md"), (tb, "docs/table.md"), (inl, "docs/table.md"),
                         (long, "docs/long.md")):
            self.assertEqual(e[wt]["result"], "would_remove", e[wt])
            self.assertEqual(e[wt]["uncommitted_found_in"], {path: merged_at("origin/main", final)})
        done = self.by_path(self.cleanup("--target", "origin/main"))
        self.assertEqual({done[w]["result"] for w in (ctx, ta, tb, inl, long)}, {"removed"})

    def test_three_way_not_contained_kept(self) -> None:
        """三方合并也判不包含的照旧 kept（远端都在改动旁边动过，走的是三方合并那条路）：加的一行在文件别处出现过、这一处没有；
        改了一处、远端同一处改成了别的；删掉的行远端还在；同一长行里同一个词两边改成不同的；条目在长行里新写的词远端没有
        （远端只改了这一行别的地方）"""
        self.remote_commit("seed", {"app/neg.py": NEG, "docs/long.md": LONG})
        dup, other, dele, word, miss = self.wt("dup"), self.wt("other"), self.wt("dele"), self.wt("word"), self.wt("miss")
        (dup / "app" / "neg.py").write_text(NEG.replace("    a = 1\n", "    a = 1\n    return None\n"))
        (other / "app" / "neg.py").write_text(NEG.replace("    c = 3", "    c = 30"))
        (dele / "app" / "neg.py").write_text(NEG.replace("    e = 5\n", ""))
        (word / "docs" / "long.md").write_text(LONG.replace("等 5 秒", "等 6 秒"))
        (miss / "docs" / "long.md").write_text(LONG.replace("一个任务 |", "一个任务，带提醒 |"))
        self.remote_commit("neighbours moved", {
            "app/neg.py": NEG.replace("    a = 1\n", "    a = 1\n    a2 = 1\n").replace("    c = 3", "    c = 31")
            .replace("    d = 4\n", "    d = 4\n    d2 = 4\n"),
            "docs/long.md": LONG.replace("重试 3 次", "只试 1 次").replace("等 5 秒", "等 7 秒")})
        e = self.by_path(self.cleanup("--target", "origin/main"))
        for wt, path in ((dup, "app/neg.py"), (other, "app/neg.py"), (dele, "app/neg.py"), (word, "docs/long.md"),
                         (miss, "docs/long.md")):
            self.assertEqual(e[wt]["result"], "kept", e[wt])
            self.assertEqual(e[wt]["reasons"], [f"没提交的改动 {path}：在 origin/main 的历史里找不到包含这些改动的提交"])
            self.assertTrue(wt.is_dir())

    def test_three_way_adjacent_table_rows(self) -> None:
        """照一次真实收尾时误留的 README 自测表：表格相邻几行两边各改、其中一行两边都改（条目改
        前半、远端在行尾加一句），条目还在中间插了两行，上游那一轮先推、集成时合在一起推上去 → 冲突块先按行核、两边都改的那
        一行按词核 → would_remove / removed（以前整块按词对齐，插的两行和远端错开几个词，判不包含）。同一张表的反例照旧 kept：
        加的一行在文件别处有、这一处没有；同一行两边改成不同的；条目新写的行远端没有；删掉的行远端还在（远端改过几个字）"""
        self.remote_commit("seed", {"docs/selftest.md": reap_table(REAP_ROWS)})
        items = {"reap": reap_item(), "dup": reap_item(after_cleanup=(("misc", "杂项"),)),
                 "word": reap_item(push_word="history.jsonl"), "extra": reap_item(after_round=(("extra", "额外"),)),
                 "dele": reap_item(drop_heavy=True)}
        wts = {name: self.wt(name) for name in items}
        for name, rows in items.items():
            (wts[name] / "docs" / "selftest.md").write_text(reap_table(rows))
        self.remote_commit("heavy round", {"docs/selftest.md": reap_table(reap_upstream(REAP_ROWS))})
        final = self.remote_commit("integrate round", {"docs/selftest.md": reap_table(reap_upstream(reap_item()))})
        reap = wts.pop("reap")
        dry = self.by_path(self.cleanup("--target", "origin/main", "--dry-run"))
        self.assertEqual(dry[reap]["result"], "would_remove", dry[reap])
        self.assertEqual(dry[reap]["uncommitted_found_in"], {"docs/selftest.md": merged_at("origin/main", final)})
        done = self.by_path(self.cleanup("--target", "origin/main"))
        self.assertEqual((done[reap]["result"], done[reap]["force"]), ("removed", True))
        self.assertFalse(reap.exists())
        for name, wt in wts.items():
            self.assertEqual(done[wt]["result"], "kept", (name, done[wt]))
            self.assertEqual(done[wt]["reasons"],
                             ["没提交的改动 docs/selftest.md：在 origin/main 的历史里找不到包含这些改动的提交"])
            self.assertTrue(wt.is_dir())

    def test_commit_mode_conflict_resolved_cherry_pick_removed(self) -> None:
        """commit 模式：条目提交挑到集成分支时解过冲突（远端先在旁边加了行，补丁内容变了、git cherry 判 "+"）→ 已提交那侧
        按文件三方合并核实 → 删，found_in 写在哪个提交上对上的；没 worktree 的同类分支同样"""
        self.remote_commit("seed", {"app/code.py": CODE})
        a = self.wt("a")
        self.commit(a, "item a", {"app/code.py": CODE.replace("    y = 2", "    y = 3")})
        self.git(self.src, "branch", f"task/{SLUG}-a-copy", f"task/{SLUG}-a")
        self.remote_commit("upstream adds z", {"app/code.py": CODE.replace("    x = 1\n", "    x = 1\n    z = 0\n")})
        r = self.wt("r1")
        pushed = self.commit(r, "item a", {"app/code.py": CODE.replace("    x = 1\n    y = 2",
                                                                       "    x = 1\n    z = 0\n    y = 3")})
        self.assertTrue(self.drover_json("push", "--wt", str(r), "--target", "main",
                                         "--unchecked", "自测：cleanup 用例不测整体检查")["verified"])
        res = self.cleanup("--dry-run")
        e = self.by_path(res)
        self.assertEqual(e[a]["result"], "would_remove", e[a])
        self.assertEqual(e[a]["found_in"], {"app/code.py": merged_at("origin/main", pushed)})
        self.assertEqual(e[r]["result"], "would_remove")
        b = {x["branch"]: x for x in res["branches"]}[f"task/{SLUG}-a-copy"]
        self.assertEqual((b["result"], b["found_in"]), ("would_remove", {"app/code.py": merged_at("origin/main", pushed)}))

    def test_revert_of_earlier_push_kept(self) -> None:
        """撤回以前推过的改动：第二轮加的 X 第三轮去掉了（没集成）。第一轮推的那个提交比 worktree 现有的内容还旧，改动在它
        上面对得上只说明退回了后来的推送，不说明推上去了 → 提交了的、没提交的都不删，写原因"""
        f = "app/f.py"
        self.commit(self.wt("a"), "round1: add f", {f: "A\nB\nC\n"})
        self.ship_commits(f"task/{SLUG}-a")
        self.commit(self.wt("b"), "round2: add X", {f: "A\nB\nX1\nX2\nC\n"})
        self.ship_commits(f"task/{SLUG}-b")
        c, d = self.wt("c"), self.wt("d")
        sha = self.commit(c, "round3: drop X", {f: "A\nB\nC\n"})
        (d / f).write_text("A\nB\nC\n")
        e = self.by_path(self.cleanup())
        self.assertEqual({"提交了": e[c]["result"], "没提交": e[d]["result"]}, {"提交了": "kept", "没提交": "kept"})
        self.assertIn(f"{sha[:9]} round3: drop X", e[c]["reasons"][0])
        self.assertEqual(e[c]["reasons"][1:], [f"已提交的改动 {f}：在 origin/main 的历史里找不到包含这些改动的提交"])
        self.assertEqual(e[d]["reasons"], [f"没提交的改动 {f}：在 origin/main 的历史里找不到包含这些改动的提交"])
        self.assertTrue(c.is_dir() and d.is_dir() and self.has_branch(f"task/{SLUG}-c"))

    def test_dropping_own_pushed_lines_kept(self) -> None:
        """推上去之后又删掉自己推过的行：条目提交加了 5 行、挑到集成分支推了（上游先动过，推上去的提交号和条目的不同），之后
        在同一个 worktree 里删掉几行。对比起点是没核实过的内容开始之前（提交都核实过就是 HEAD），相对分叉点只是"加得比远端
        少" → 没提交删一行、没提交全删回去、提交了删两行都不删"""
        f = "app/feat.py"
        self.remote_commit("base file", {f: "BASE\n"})
        w = self.wt("w")
        self.commit(w, "add feature lines", {f: "BASE\nL1\nL2\nL3\nL4\nL5\n"})
        self.remote_commit("others move main", {"docs/x.md": "x\n"})
        self.ship_commits(f"task/{SLUG}-w")
        self.assertNotEqual(self.head(w), self.remote_head())
        got = {}
        for name, text in (("没提交删一行", "BASE\nL1\nL2\nL4\nL5\n"), ("没提交全删回去", "BASE\n")):
            (w / f).write_text(text)
            got[name] = self.by_path(self.cleanup("--dry-run"))[w]
        sha = self.commit(w, "drop L2 L3", {f: "BASE\nL1\nL4\nL5\n"})
        got["提交了删两行"] = self.by_path(self.cleanup())[w]
        self.assertEqual({k: v["result"] for k, v in got.items()}, dict.fromkeys(got, "kept"))
        for name in ("没提交删一行", "没提交全删回去"):
            self.assertEqual(got[name]["reasons"], [f"没提交的改动 {f}：在 origin/main 的历史里找不到包含这些改动的提交"])
        self.assertIn(f"{sha[:9]} drop L2 L3", got["提交了删两行"]["reasons"][0])
        self.assertEqual(got["提交了删两行"]["reasons"][1:],
                         [f"已提交的改动 {f}：在 origin/main 的历史里找不到包含这些改动的提交"])
        self.assertTrue(w.is_dir())

    def test_dropped_or_replaced_line_still_in_remote_kept(self) -> None:
        """我们删掉 / 换掉的那行远端还留着（集成时只带上了旁边的改动）：冲突块里我们这边的行作为连续一段出现在远端那边也不算
        包含 → 不删。删掉一条危险配置、改了下一行，推上去的只有下一行；把 import legacy_auth 换成 import new_auth，远端两行都在"""
        conf, auth = "conf/app.yml", "app/auth.py"
        conf_text = "debug: false\nallow_all_origins: true\ntimeout: 30\nretries: 3\n"
        auth_text = "import os\nimport legacy_auth\n\n\ndef login(u):\n    return legacy_auth.check(u)\n"
        self.remote_commit("seed", {conf: conf_text, auth: auth_text})
        y, z = self.wt("y"), self.wt("z")
        (y / conf).write_text("debug: false\ntimeout: 60\nretries: 3\n")
        (z / auth).write_text(auth_text.replace("legacy_auth", "new_auth"))
        r = self.wt("r1")
        self.commit(r, "ship neighbours only", {
            conf: conf_text.replace("timeout: 30", "timeout: 60"),
            auth: auth_text.replace("legacy_auth\n", "legacy_auth\nimport new_auth\n").replace("legacy_auth.", "new_auth.")})
        self.assertTrue(self.drover_json("push", "--wt", str(r), "--target", "main",
                                         "--unchecked", "自测：cleanup 用例不测整体检查")["verified"])
        e = self.by_path(self.cleanup())
        self.assertEqual({"删掉": e[y]["result"], "换掉": e[z]["result"]}, {"删掉": "kept", "换掉": "kept"})
        for wt, path in ((y, conf), (z, auth)):
            self.assertEqual(e[wt]["reasons"], [f"没提交的改动 {path}：在 origin/main 的历史里找不到包含这些改动的提交"])
            self.assertTrue(wt.is_dir())

    def test_nested_repo_and_mode_change_kept(self) -> None:
        """worktree 里自己 git init 的子目录（里面有自己的提交）：临时 index 只记得下一个提交号，里面的东西核实不了、也存不进
        patch → 不删，--probe 也不存不删。可执行位算改动、要核实（git apply 不看模式）：只 chmod +x 了一个文件、新加的脚本
        带可执行位，远端内容一样但模式不同 → 不删"""
        nest, probe, m, x = self.wt("nest"), self.wt("probe"), self.wt("m"), self.wt("x")
        for wt in (nest, probe):
            lib = wt / "vendor" / "lib"
            lib.mkdir(parents=True)
            self.git(lib, "init", "-q", "-b", "main")
            self.commit(lib, "local only work", {"work.py": "IMPORTANT = 1\n"})
        os.chmod(m / "app" / "core.py", 0o755)
        (x / "run.sh").write_text("echo hi\n")
        os.chmod(x / "run.sh", 0o755)
        r = self.wt("r1")
        self.commit(r, "ship run.sh without the exec bit", {"run.sh": "echo hi\n"})
        self.assertTrue(self.drover_json("push", "--wt", str(r), "--target", "main",
                                         "--unchecked", "自测：cleanup 用例不测整体检查")["verified"])
        p = self.by_path(self.cleanup("--probe", "--wt", str(probe)))[probe]
        e = self.by_path(self.cleanup())
        self.assertEqual({"内嵌仓库": e[nest]["result"], "内嵌仓库 --probe": p["result"], "只改可执行位": e[m]["result"],
                          "新脚本的可执行位没推": e[x]["result"]},
                         dict.fromkeys(("内嵌仓库", "内嵌仓库 --probe", "只改可执行位", "新脚本的可执行位没推"), "kept"))
        for entry in (e[nest], p):
            self.assertIn("内嵌 git 仓库 vendor/lib", entry["reasons"][0])
        for wt, path in ((m, "app/core.py"), (x, "run.sh")):
            self.assertEqual(e[wt]["reasons"], [f"没提交的改动 {path}：在 origin/main 的历史里找不到包含这些改动的提交"])
        self.assertTrue((nest / "vendor" / "lib" / "work.py").exists())
        self.assertNotIn("patches", p)
        self.assertEqual(list((self.state / SLUG).glob("probe-*.patch")), [])
        self.assertTrue((probe / "vendor" / "lib" / "work.py").exists())

    def test_submodule_pointer_change_kept(self) -> None:
        """子模块指针（160000）在对象库里没有内容：按它指向的提交号比，不能两边都取不到就当一样。没有 worktree 的分支上一个没推
        的提交把指针从 A 改成 B → 不删，写原因（建指针用 update-index：没有子模块目录，不能 add -A）"""
        old = self.git(self.src, "rev-parse", "HEAD")
        self.git(self.up, "update-index", "--add", "--cacheinfo", f"160000,{old},sub")
        self.git(self.up, "commit", "-q", "-m", "add submodule sub")
        self.git(self.up, "push", "-q", "origin", "main")
        new = self.git(self.up, "rev-parse", "HEAD")
        self.git(self.src, "fetch", "-q", "origin")
        self.git(self.src, "checkout", "-q", "-b", f"task/{SLUG}-bump", "origin/main")
        self.git(self.src, "update-index", "--cacheinfo", f"160000,{new},sub")
        self.git(self.src, "commit", "-q", "-m", "bump sub")
        sha = self.git(self.src, "rev-parse", "HEAD")
        self.git(self.src, "checkout", "-q", "main")
        res = self.cleanup("--target", "origin/main")
        got = {x["branch"]: x for x in res["branches"]}[f"task/{SLUG}-bump"]
        self.assertEqual(got["result"], "kept", got)
        self.assertIn(f"{sha[:9]} bump sub", got["reasons"][0])
        self.assertEqual(got["reasons"][1:], ["已提交的改动 sub：在 origin/main 的历史里找不到包含这些改动的提交"])
        self.assertTrue(self.has_branch(f"task/{SLUG}-bump"))

    def test_dry_run_lists_only(self) -> None:
        """--dry-run：结论和真跑一样（would_remove / kept），不调 herdr worktree remove、不删分支"""
        a = self.wt("a")
        self.commit(a, "item a", {"app/a.py": "A = 1\n"})
        integ = self.ship_commits(f"task/{SLUG}-a")
        c = self.wt("c")
        self.commit(c, "unpushed work", {"app/c.py": "C = 1\n"})
        res = self.cleanup("--dry-run")
        e = self.by_path(res)
        self.assertEqual({k: v["result"] for k, v in e.items()}, {a: "would_remove", integ: "would_remove", c: "kept"})
        self.assertEqual(self.removes(), [])
        self.assertTrue(a.is_dir() and integ.is_dir())
        self.assertTrue(self.has_branch(f"task/{SLUG}-a") and self.has_branch(f"task/{SLUG}"))
        self.assertIn("去掉 --dry-run", res["next"])

    def test_probe_saves_patches_then_removes(self) -> None:
        """--probe：本地独有的提交存成 format-patch、没提交和没被忽略的未跟踪改动存成 diff（到任务目录），存成了再删；
        两份 patch 能在远端起点上还原。里面 agent 在干活的探测 worktree 不删、不存"""
        pr = self.wt("probe")
        self.commit(pr, "probe commit", {"tools/probe.py": "P = 1\n"})
        (pr / "app" / "core.py").write_text("VALUE = 9\n")
        (pr / "notes.txt").write_text("measured 3s\n")
        busy = self.wt("probe2")
        (busy / "x.txt").write_text("x\n")
        self.set_agents(**{"c1-probe2": {"status": "working", "cwd": str(busy)}})
        res = self.cleanup("--probe", "--wt", str(pr), "--wt", str(busy))
        e = self.by_path(res)
        self.assertEqual((e[pr]["result"], e[pr]["force"]), ("removed", True))
        patches = {p["kind"]: Path(p["path"]) for p in e[pr]["patches"]}
        self.assertEqual(set(patches), {"commits", "worktree"})
        for f in patches.values():
            self.assertEqual(f.parent, self.state / SLUG)
            self.assertTrue(f.name.startswith(f"probe-task-{SLUG}-probe-"))
        self.assertFalse(pr.exists() or self.has_branch(f"task/{SLUG}-probe"))
        restore = self.tmp / "restore"
        self.git(self.src, "worktree", "add", "-q", "--detach", str(restore), "origin/main")
        self.git(restore, "am", "-q", str(patches["commits"]))
        self.git(restore, "apply", "--check", str(patches["worktree"]))
        self.git(restore, "apply", str(patches["worktree"]))
        self.assertEqual(((restore / "tools" / "probe.py").read_text(), (restore / "app" / "core.py").read_text(),
                          (restore / "notes.txt").read_text()), ("P = 1\n", "VALUE = 9\n", "measured 3s\n"))
        self.assertEqual(e[busy]["result"], "kept")
        self.assertNotIn("patches", e[busy])
        self.assertTrue(busy.is_dir())
        self.assertEqual(len(list((self.state / SLUG).glob("probe-*.patch"))), 2)
        # --probe 不配 --wt 不让跑
        self.assertIn("--probe 要配 --wt", self.drover("cleanup", "--slug", SLUG, "--probe", rc=2).stderr)

    def test_closed_workspace_uses_git_worktree_remove(self) -> None:
        """workspace 没开（herdr worktree list 的 open_workspace_id 是 null，D3）→ 核实通过后直接 git worktree remove"""
        a = self.wt("a")
        self.commit(a, "item a", {"app/a.py": "A = 1\n"})
        self.ship_commits(f"task/{SLUG}-a")
        state = self.herdr_state()
        next(w for w in state["worktrees"] if w["path"] == str(a))["closed"] = True
        self.set_herdr_state(state)
        res = self.cleanup("--wt", str(a))
        e = self.by_path(res)[a]
        self.assertEqual((e["result"], e["workspace_id"]), ("removed", None))
        self.assertTrue(e["remove_command"].startswith(f"git -C {self.src} worktree remove"))
        self.assertFalse(a.exists() or self.has_branch(f"task/{SLUG}-a"))
        self.assertEqual(self.removes(), [])
        self.assertEqual(res["branches"], [])  # --wt 只清指定的

    def test_target_for_tasks_without_push_records(self) -> None:
        """推送没落盘的老任务：没有推送记录 → 核实不了、不删；--target origin/main 补上核对分支 → 内容在里面的删；
        --target 写错（远端没有这条分支）→ 不拿来核对、不删。输出写清核对分支的来源"""
        o = self.wt("o")
        (o / "app" / "core.py").write_text("VALUE = 5\n")
        r = self.wt("r1")
        (r / "app" / "core.py").write_text("VALUE = 5\n")
        self.git(r, "commit", "-q", "-am", "old push")
        self.git(r, "push", "-q", "origin", "HEAD:main")  # 旧做法：推送没记下
        self.assertFalse((self.state / SLUG / "pushes.jsonl").exists())
        none = self.by_path(self.cleanup())
        self.assertEqual({k: v["result"] for k, v in none.items()}, {o: "kept", r: "kept"})
        self.assertIn("没有推送记录", none[o]["reasons"][0])
        wrong = self.cleanup("--target", "origin/nope")
        self.assertEqual(wrong["refs"][str(self.src)][0]["error"], "远端没有这条分支")
        self.assertIn("没有可核对的远端分支", self.by_path(wrong)[o]["reasons"][0])
        res = self.cleanup("--target", "origin/main")
        e = self.by_path(res)
        self.assertEqual({k: v["result"] for k, v in e.items()}, {o: "removed", r: "removed"})
        self.assertEqual(res["refs"][str(self.src)], [{"ref": "origin/main", "source": "--target", "sha": self.remote_head()}])
        self.assertTrue(e[o]["force"])

    def test_branches_without_worktree(self) -> None:
        """没有 worktree 的本地分支：提交已推（挑到集成分支）的删，有没推提交的不删、列出提交"""
        a = self.wt("a")
        self.commit(a, "item a", {"app/a.py": "A = 1\n"})
        self.ship_commits(f"task/{SLUG}-a")
        self.git(self.src, "worktree", "remove", str(a))
        self.git(self.src, "checkout", "-q", "-b", f"task/{SLUG}-stale")
        stale = self.commit(self.src, "stale work", {"app/s.py": "S = 1\n"})
        self.git(self.src, "checkout", "-q", "main")
        res = self.cleanup()
        b = {x["branch"]: x for x in res["branches"]}
        self.assertEqual(b[f"task/{SLUG}-a"]["result"], "removed")
        self.assertEqual(b[f"task/{SLUG}-stale"]["result"], "kept")
        self.assertIn(f"{stale[:9]} stale work", b[f"task/{SLUG}-stale"]["reasons"][0])
        self.assertFalse(self.has_branch(f"task/{SLUG}-a"))
        self.assertTrue(self.has_branch(f"task/{SLUG}-stale"))

    def test_main_checkout_and_other_tasks_untouched(self) -> None:
        """--wt 指到源仓库主检出、别的任务的 worktree → 不动、说明原因"""
        (self.state / "zz").mkdir()
        other = self.wt("x", slug="zz")
        res = self.cleanup("--wt", str(self.src), "--wt", str(other))
        e = self.by_path(res)
        self.assertEqual(e[self.src]["reasons"], ["源仓库的主检出，永远不动"])
        self.assertIn("不是这个任务的 worktree", e[other]["reasons"][0])
        self.assertTrue(other.is_dir())
        self.assertEqual(self.removes(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
