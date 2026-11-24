"""wrapup：收尾汇总的事实。本地裸仓库当 origin，集成记录由真的 integrate / push 写（归档名靠手工改名，和实际做法一样）。

  两份集成记录（不同 upstream，一份是归档名）→ 两行；子编排者（parent.json 指向本 slug）的记录带上、行里有 slug；
  没有集成记录 / 没有状态文件；远端核对 true / true（远端又有新提交，--fetch 后）/ false（远端被改成不含推送的提交）/ 跳过 /
  不核对（origin 换了地址，最后一次推的是旧地址）；
  改动文件分类和新增配置键引用（删除行不算）；--draft 骨架和拒绝覆盖；待清理的 worktree / 分支和 dirty。
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from selftest_repos import IntegrationCase

DISPATCH = "# dispatch: {slug}\n\n涉及仓库：shop\n目标：{goal}\n\n## 执行记录\n\n- 目标：这行不是开头那行，不取\n"
SECTIONS = ["## 变更总结", "## 提交", "## 数据库", "## 配置", "## 上线", "## 验证", "## 未决", "## 清理"]


class WrapupCase(IntegrationCase):
    GOAL = "给 shop 加个功能"

    def setUp(self) -> None:
        super().setUp()
        self.task_dir(self.SLUG, self.GOAL)
        (self.state / self.SLUG / "orch.json").write_text(json.dumps({"slug": self.SLUG, "name": "orch-s1"}))

    def task_dir(self, slug: str, goal: str = "子任务", parent: str = "") -> Path:
        d = self.state / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "dispatch.md").write_text(DISPATCH.format(slug=slug, goal=goal))
        if parent:
            (d / "parent.json").write_text(json.dumps({"parent": parent, "name": f"orch-{slug}", "tab_id": "w1:t2",
                                                       "pane_id": "w1:p2", "started": "11-07 10:00"}))
        return d

    def wrapup(self, *extra: str, slug: str = "", rc: int = 0) -> dict:
        return self.drover_json("wrapup", "--slug", slug or self.SLUG, *extra, rc=rc)

    def ship(self, target: str, *items: str, slug: str = "", branch: str = "") -> Path:
        """integrate 这些条目到 origin/<target>，整体检查，再 push（核对过），返回集成 worktree。"""
        args = ["integrate", "--repo", self.REPO, "--slug", slug or self.SLUG, "--target", target]
        if branch:
            args += ["--branch", branch]
        for b in items:
            args += ["--from", b]
        res = self.drover_json(*args)
        wt = Path(res["worktree_path"])
        self.check(wt)
        pushed = self.drover_json("push", "--wt", str(wt), "--target", target)
        self.assertTrue(pushed["pushed"] and pushed["verified"], pushed)
        return wt

    def remote_head_of(self, branch: str) -> str:
        return self.git(self.tmp, "ls-remote", str(self.origin), f"refs/heads/{branch}").split()[0]

    def sync_upstream(self) -> None:
        """upstream 克隆追上 origin/main（integrate / push 之后它落后了）。"""
        self.git(self.up, "pull", "-q", "--ff-only", "origin", "main")

    @staticmethod
    def by_upstream(res: dict) -> dict:
        return {line["upstream"]: line for line in res["lines"]}


class WrapupTests(WrapupCase):
    def test_two_records_same_repo_two_lines(self) -> None:
        """同一仓库推两条分支：集成状态按目标分支各一份（integrate-shop@release.json / @main.json），不用改名归档 → 两行
        （不同 upstream），各自的 last_push；远端核对都是 true"""
        self.git(self.src, "push", "-q", "origin", "main:release")
        a = self.item("a", {"app/a.py": "A = 1\n"})
        rel_wt = self.ship("release", a, branch="task/s1-release")
        self.upstream_commit("upstream docs", {"docs/x.md": "x\n"})
        main_wt = self.ship("main", a)
        res = self.wrapup()
        self.assertTrue(res["ok"])
        self.assertEqual((res["goal"], res["repos"], res["orchestrator"]), (self.GOAL, ["shop"], "orch-s1"))
        self.assertEqual(len(res["lines"]), 2)
        lines = self.by_upstream(res)
        self.assertEqual(set(lines), {"origin/release", "origin/main"})
        rel, main = lines["origin/release"], lines["origin/main"]
        self.assertEqual(Path(rel["state_file"]).name, "integrate-shop@release.json")
        self.assertEqual(Path(main["state_file"]).name, "integrate-shop@main.json")
        self.assertEqual((rel["target"], rel["branch"]), ("release", "task/s1-release"))
        self.assertEqual(rel["last_push"]["remote_sha"], self.head(rel_wt))
        self.assertEqual(rel["last_push"]["remote_sha"], self.remote_head_of("release"))
        self.assertEqual(main["last_push"]["remote_sha"], self.head(main_wt))
        self.assertNotEqual(rel["last_push"]["remote_sha"], main["last_push"]["remote_sha"])
        for line in (rel, main):
            self.assertEqual(line["slug"], "s1")
            self.assertTrue(line["pushed"])
            self.assertIs(line["remote"]["contains"], True)
            self.assertEqual([f["path"] for f in line["files"]], ["app/a.py"])
        self.assertEqual(res["warnings"], [])
        # 同一仓库同一 upstream 的集成记录有两份（手工复制的归档）→ 合成一行，state_files 列出两份，推送只算一次
        (self.state / "s1" / "integrate-shop-copy.json").write_text(self.state_file().read_text())
        dup = self.wrapup("--no-remote")
        self.assertEqual(len(dup["lines"]), 2)
        main_line = self.by_upstream(dup)["origin/main"]
        self.assertEqual(sorted(Path(f).name for f in main_line["state_files"]),
                         ["integrate-shop-copy.json", "integrate-shop@main.json"])
        self.assertEqual(len(main_line["pushes"]), 1)

    def test_rebuild_and_legacy_records_keep_pushes(self) -> None:
        """旧版本的集成状态（文件名不带目标分支 integrate-shop.json，推送记在它的 pushes 里）：wrapup 照样读出那次推送；
        集成另一条分支另起一份状态、不动它 → 两行；同一目标换了集成 worktree（--branch 重建）时，旧推送先转进 pushes.jsonl
        （带上旧的 upstream 和来源文件），不丢"""
        self.git(self.src, "push", "-q", "origin", "main:release")
        a = self.item("a", {"app/a.py": "A = 1\n"})
        rel_wt = self.ship("release", a, branch="task/s1-release")
        records = self.state / "s1" / "pushes.jsonl"
        release_push = json.loads(records.read_text().splitlines()[0])
        # 假装这次推送是旧代码记的：状态文件用旧名字、推送只记在集成状态里
        records.unlink()
        legacy = self.state / "s1" / "integrate-shop.json"
        state = self.load_state("release")
        state["pushes"] = [{k: release_push[k] for k in ("at", "range", "commits", "remote_sha", "verified")}]
        legacy.write_text(json.dumps(state))
        self.state_file("release").unlink()
        self.assertEqual(len(self.wrapup("--no-remote")["lines"]), 1)
        self.upstream_commit("upstream docs", {"docs/x.md": "x\n"})
        main_wt = self.ship("main", a)
        self.assertIn("pushes", json.loads(legacy.read_text()))
        lines = self.by_upstream(self.wrapup())
        self.assertEqual(set(lines), {"origin/release", "origin/main"})
        self.assertEqual(lines["origin/release"]["last_push"]["remote_sha"], self.head(rel_wt))
        self.assertEqual(lines["origin/main"]["last_push"]["remote_sha"], self.head(main_wt))
        self.assertEqual(Path(lines["origin/release"]["state_file"]), legacy)
        self.assertIs(lines["origin/release"]["remote"]["contains"], True)
        rebuilt = self.drover_json("integrate", "--repo", self.REPO, "--slug", self.SLUG, "--target", "release",
                                   "--branch", "task/s1-release2", "--from", a)
        self.assertEqual(Path(rebuilt["state_file"]), legacy)
        self.assertNotIn("pushes", json.loads(legacy.read_text()))
        migrated = [json.loads(x) for x in records.read_text().splitlines()]
        self.assertEqual([(p["upstream"], p.get("source")) for p in migrated],
                         [("origin/main", None), ("origin/release", "integrate-shop.json")])
        self.assertEqual(self.by_upstream(self.wrapup("--no-remote"))["origin/release"]["last_push"]["remote_sha"],
                         self.head(rel_wt))

    def test_patch_mode_pushes_listed(self) -> None:
        """patch 模式（集成 worktree 是 wt 建的、没有集成状态文件）的推送（带 --unchecked）也列出来：一行，分支是推送时的
        本地分支，改动文件按推送范围取，远端核对照做；骨架的提交表有这一行，不用手写；没有整体检查覆盖它，warnings 带上
        --unchecked 的理由"""
        wt = Path(self.drover_json("wt", "--repo", str(self.src), "--branch", "task/s1-r1", "--label", "shop:s1-r1",
                                   "--base", "origin/main")["worktree_path"])
        sha = self.commit(wt, "patch round", {"app/p.py": "P = 1\n"})
        self.assertTrue(self.drover_json("push", "--wt", str(wt), "--target", "main",
                                         "--unchecked", "patch 模式")["verified"])
        self.assertFalse(list((self.state / "s1").glob("integrate-*.json")))
        draft = self.tmp / "wrapup.md"
        res = self.wrapup("--draft", str(draft))
        self.assertEqual(len(res["lines"]), 1)
        line = res["lines"][0]
        self.assertEqual((line["upstream"], line["branch"], line["state_file"], line["pushed"]),
                         ("origin/main", "task/s1-r1", None, True))
        self.assertEqual(line["last_push"]["remote_sha"], sha)
        self.assertEqual([f["path"] for f in line["files"]], ["app/p.py"])
        self.assertIs(line["remote"]["contains"], True)
        self.assertFalse(any("推送记录" in w for w in res["warnings"]), res["warnings"])
        self.assertTrue(any("没有整体检查覆盖" in w and "patch 模式" in w for w in res["warnings"]), res["warnings"])
        self.assertIn(f"| shop | main | {sha[:9]} |", draft.read_text())

    def test_child_orchestrator_records_included(self) -> None:
        """子编排者（另一个任务目录的 parent.json 指向本 slug）的推送记录带上，行里有 slug；子任务自己收尾只看到自己的"""
        self.task_dir("s1-shop", parent="s1")
        self.task_dir("s2")  # 不相关的任务
        x = self.item("shop-x", {"app/x.py": "X = 1\n"})
        self.ship("main", x, slug="s1-shop")
        res = self.wrapup()
        self.assertEqual(res["children"], ["s1-shop"])
        self.assertEqual(len(res["lines"]), 1)
        line = res["lines"][0]
        self.assertEqual((line["slug"], line["branch"]), ("s1-shop", "task/s1-shop"))
        self.assertEqual(Path(line["state_file"]).parent.name, "s1-shop")
        self.assertIs(line["remote"]["contains"], True)
        self.assertFalse(any("9.1" in w for w in res["warnings"]))
        wts = {w["branch"]: w for w in res["worktrees"]}
        self.assertEqual((wts["task/s1-shop"]["slug"], wts["task/s1-shop"]["matched_by"]), ("s1-shop", "record"))
        child = self.wrapup("--no-remote", slug="s1-shop")
        self.assertEqual((child["children"], len(child["lines"])), ([], 1))

    def test_no_records_and_no_state_file(self) -> None:
        """没有推送记录 → ok、lines 为空、有 warning，骨架的提交表留（待填）；没有 dispatch.md → ok: false"""
        res = self.wrapup()
        self.assertTrue(res["ok"])
        self.assertEqual(res["lines"], [])
        self.assertTrue(any("没有任何推送记录" in w for w in res["warnings"]), res["warnings"])
        draft = self.tmp / "wrapup.md"
        self.wrapup("--draft", str(draft))
        text = draft.read_text()
        self.assertIn("| （待填） | （待填） | （待填） | （待填） | （待填） |", text)
        self.assertIn("这个任务没有推送记录", text)
        missing = self.wrapup(slug="nope", rc=1)
        self.assertFalse(missing["ok"])
        self.assertIn("dispatch.md", missing["error"])

    def test_remote_contains(self) -> None:
        """远端核对：远端头 = 推送的提交 → true；远端又有新提交（本地没有）→ null 提示 --fetch，--fetch 后 true；
        远端被改成不含推送的提交 → false 并有 warning；--no-remote 不核对"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        pushed = self.head(self.ship("main", a))
        same = self.wrapup()["lines"][0]["remote"]
        self.assertEqual((same["remote_head"], same["contains"]), (pushed, True))
        self.sync_upstream()
        newer = self.upstream_commit("later", {"docs/later.md": "later\n"})
        res = self.wrapup()
        r = res["lines"][0]["remote"]
        self.assertEqual(r["remote_head"], newer)
        self.assertIsNone(r["contains"])
        self.assertIn("--fetch", r["note"])
        self.assertTrue(any("远端核对没做完" in w for w in res["warnings"]))
        fetched = self.wrapup("--fetch")["lines"][0]["remote"]
        self.assertEqual((fetched["contains"], fetched["fetched"]), (True, True))
        self.assertIs(self.wrapup()["lines"][0]["remote"]["contains"], True)  # 对象已在本地，不用 --fetch
        base = self.load_state()["base"]
        self.git(self.up, "push", "-q", "-f", "origin", f"{base}:main")
        gone = self.wrapup()
        r = gone["lines"][0]["remote"]
        self.assertEqual((r["remote_head"], r["contains"]), (base, False))
        self.assertTrue(any("不含推送的" in w for w in gone["warnings"]), gone["warnings"])
        self.assertEqual(self.wrapup("--no-remote")["lines"][0]["remote"], {"checked": False, "reason": "--no-remote"})

    def test_remote_moved_not_checked(self) -> None:
        """最后一次推送推到的是 origin 换地址之前的仓库 → 不拿新地址核对：remote.checked 是 false、reason 写明两个地址，
        没有"不含推送的提交"之类的误报（--fetch 也一样，以前会 fetch 新地址再报不含）；骨架的远端核对那格照 reason 写"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        self.ship("main", a)
        old = str(self.origin)
        moved = self.move_origin()
        draft = self.tmp / "wrapup.md"
        res = self.wrapup("--fetch", "--draft", str(draft))
        r = res["lines"][0]["remote"]
        self.assertFalse(r["checked"])
        self.assertIn(f"推到的是 {old}，现在 origin 是 {moved}", r["reason"])
        self.assertFalse(any("不含" in w or "远端核对" in w for w in res["warnings"]), res["warnings"])
        self.assertIn(f"未核对：{r['reason']}", draft.read_text())

    def test_categories_and_config_refs(self) -> None:
        """推送范围里的迁移 / 配置 / 部署 / 依赖文件进各自类别；新增行里的 os.getenv / process.env / @Value / .env 键
        进 config_refs，删除行（改掉的一行、整个删掉的文件）里的引用不算；骨架里各成候选行"""
        self.commit(self.src, "old refs", {"app/old.py": 'import os\nOLD = os.getenv("OLD")\n',
                                           "web/gone.ts": "export const g = process.env.GONE\n"})
        self.git(self.src, "push", "-q", "origin", "main")
        branch = "task/s1-cfg"
        self.git(self.src, "checkout", "-q", "-b", branch, "main")
        (self.src / "web" / "gone.ts").unlink()
        self.commit(self.src, "item cfg", {
            "db/migrations/0002_add_col.sql": "ALTER TABLE t ADD COLUMN c INT NOT NULL DEFAULT 0 COMMENT 'c';\n",
            ".env.example": "NEW_KEY=\n",
            "config/application.yml": "a:\n  b: 1\n",
            "package.json": "{}\n",
            "Dockerfile": "FROM python:3.12\n",
            "app/settings.py": 'import os\nX = os.getenv("X")\n',
            "web/env.ts": "export const y = process.env.Y\n",
            "src/main/java/Conf.java": 'class Conf {\n  @Value("${a.b:1}")\n  private int ab;\n}\n',
            "app/old.py": "VALUE = 0\n",
        })
        self.git(self.src, "checkout", "-q", "main")
        self.ship("main", branch)
        draft = self.tmp / "wrapup.md"
        line = self.wrapup("--no-remote", "--draft", str(draft))["lines"][0]
        cats = {k: [f["path"] for f in v] for k, v in line["categories"].items()}
        self.assertEqual(cats, {"migrations": ["db/migrations/0002_add_col.sql"],
                                "config": [".env.example", "config/application.yml"],
                                "deploy": ["Dockerfile"], "deps": ["package.json"]})
        refs = {(r["key"], r["file"]) for r in line["config_refs"]}
        self.assertEqual(refs, {("X", "app/settings.py"), ("Y", "web/env.ts"), ("a.b", "src/main/java/Conf.java"),
                                ("NEW_KEY", ".env.example")})
        statuses = {f["path"]: f["status"] for f in line["files"]}
        self.assertEqual((statuses["app/old.py"], statuses["web/gone.ts"]), ("M", "D"))
        self.assertEqual(line["files_total"], 10)
        text = draft.read_text()
        self.assertIn("| shop | db/migrations/0002_add_col.sql（新增，分支 main） | （待填） |", text)
        self.assertIn("config/application.yml（配置文件，新增）", text)
        self.assertIn("Dockerfile（部署文件，新增）", text)
        self.assertIn("| a.b |", text)
        self.assertIn("| NEW_KEY |", text)
        self.assertIn("- 依赖有变化：shop package.json", text)
        self.assertNotIn("OLD", text)
        self.assertNotIn("GONE", text)

    def test_draft_and_refuse_overwrite(self) -> None:
        """--draft：模板各节、提交表格行、远端核对、整体检查摘要、待清理 worktree、（待填）；已有文件不带 --force 拒绝，带了覆盖"""
        locales = {f"web/locales/{lang}.json": "{}\n" for lang in ("en", "zh", "ja", "ko")}
        a = self.item("a", {"app/a.py": "A = 1\n", **locales})
        wt = self.ship("main", a)
        (self.state / "s1" / "accept-integrate-shop.json").write_text(json.dumps({
            "ok": True, "worktree_path": str(wt), "base": "0" * 40, "diff_stat": " 5 files changed, 5 insertions(+)",
            "verify": [{"command": "pytest -q", "ok": True, "exit_code": 0, "summary_line": "10 passed"},
                       {"command": "eslint .", "ok": True, "exit_code": 1, "note": "失败 2 条，全部是基线上本来就有的，没有新增"}],
            "verify_failed": False}))
        draft = self.state / "s1" / "wrapup.md"
        res = self.wrapup("--draft", str(draft))
        text = draft.read_text()
        sha = self.head(wt)[:9]
        for want in ["# 收尾汇总：s1", f"目标：{self.GOAL}", "编排者：orch-s1；涉及仓库：shop", "收尾：", *SECTIONS,
                     "- shop：app/a.py、web/locales/ 下 4 个文件（共 5 个文件）",
                     "| 仓库 | 分支 | 提交 | 范围 | 远端核对 |", f"| shop | main | {sha} |",
                     f"仍在 origin/main（远端头 {sha}）", "- 没推送的：无", "2/2 条通过",
                     "eslint . 退出码 1（失败 2 条", "其余 1 条退出码 0：pytest -q", str(wt), "（待填）"]:
            self.assertIn(want, text)
        self.assertEqual(res["draft"]["path"], str(draft))
        self.assertGreater(res["draft"]["todo"], 0)
        self.assertEqual(res["checks"][0]["upstream"], "origin/main")
        self.assertEqual(res["checks"][0]["verify"][0]["command"], "pytest -q")
        refused = self.wrapup("--draft", str(draft), rc=1)
        self.assertFalse(refused["ok"])
        self.assertIn("已有", refused["error"])
        self.assertIn("--force", refused["error"])
        self.assertEqual(draft.read_text(), text)
        draft.write_text("stale\n")
        self.wrapup("--draft", str(draft), "--force")
        self.assertTrue(draft.read_text().startswith("# 收尾汇总：s1\n"))

    def test_worktrees_and_branches(self) -> None:
        """集成分支的 worktree（按记录）、task/s1-xxx 的 worktree（按前缀，dirty 正确）、没有 worktree 的条目分支；
        别的任务（s10、s1x、不是子任务的 s1-other）的分支和源仓库主检出不算"""
        self.task_dir("s1-other")  # 没有 parent.json：不是子任务
        a = self.item("a", {"app/a.py": "A = 1\n"})
        wt = self.ship("main", a)
        extra = self.tmp / "extra-wt"
        self.git(self.src, "worktree", "add", "-q", "-b", "task/s1-extra", str(extra), "main")
        (extra / "scratch.txt").write_text("x\n")
        for b in ("task/s10-x", "task/s1x", "task/s1-other-y"):
            self.git(self.src, "branch", b, "main")
        res = self.wrapup("--no-remote")
        wts = {w["branch"]: w for w in res["worktrees"]}
        self.assertEqual(set(wts), {"task/s1", "task/s1-extra"})
        self.assertEqual((wts["task/s1"]["matched_by"], wts["task/s1"]["dirty"]), ("record", False))
        self.assertEqual(os.path.realpath(wts["task/s1"]["path"]), os.path.realpath(wt))
        self.assertEqual((wts["task/s1-extra"]["matched_by"], wts["task/s1-extra"]["dirty"]), ("prefix", True))
        self.assertEqual(wts["task/s1-extra"]["dirty_count"], 1)
        self.assertEqual({b["branch"]: b["matched_by"] for b in res["branches"]}, {a: "record"})
        self.assertTrue(any("未提交的改动" in w for w in res["warnings"]))

    def test_unpushed_item_commits_listed(self) -> None:
        """条目 a 集成并推了；条目 b 提交了但没集成、没推 → "没推送的"不再写"无"：列出 b 上没推上去的提交（不联网，按推送
        记录里推上去的提交核对，从起点算起），warnings 也有；a 的提交是挑过去的（提交号不同、补丁相同）不算"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        self.upstream_commit("upstream docs", {"docs/x.md": "x\n"})
        self.ship("main", a)
        b = self.item("b", {"app/b.py": "B = 1\n"})
        draft = self.tmp / "wrapup.md"
        res = self.wrapup("--no-remote", "--draft", str(draft))
        self.assertEqual([(u["branch"], [c["subject"] for c in u["commits"]]) for u in res["unpushed_items"]],
                         [(b, ["item b"])])
        line = [x for x in draft.read_text().splitlines() if x.startswith("- 没推送的")][0]
        self.assertIn(b, line)
        self.assertIn("item b", line)
        self.assertNotIn("无（", line)
        self.assertTrue(any(b in w and "没推上去" in w for w in res["warnings"]), res["warnings"])

    def test_conflict_resolved_item_commits_pushed_or_not(self) -> None:
        """条目提交集成时解过冲突（内容改写了，补丁对不上）：摘出来的新提交推上去了 → 不算没推；另一个条目集成了、解了冲突
        但还没推 → 算，列出来"""
        a = self.item("a", {"shared.txt": "from a\n"})
        self.upstream_commit("upstream shared", {"shared.txt": "from upstream\n"})
        wt = self.integrate_resolved(a, {"shared.txt": "from upstream and a\n"})
        self.check(wt)
        self.assertTrue(self.push(wt)["verified"])
        self.assertEqual(self.wrapup("--no-remote")["unpushed_items"], [])
        b = self.item("b", {"app/core.py": "VALUE = 2\n"})
        self.sync_upstream()
        self.upstream_commit("upstream core", {"app/core.py": "VALUE = 3\n"})
        self.integrate_resolved(b, {"app/core.py": "VALUE = 23\n"})
        self.assertEqual([e["status"] for e in self.load_state()["picked"]], ["resolved", "resolved"])
        res = self.wrapup("--no-remote")
        self.assertEqual([(u["branch"], [c["subject"] for c in u["commits"]]) for u in res["unpushed_items"]],
                         [(b, ["item b"])])

    def test_resolved_item_pushed_after_rebase_not_listed(self) -> None:
        """集成时解过冲突的条目提交，push 时上游又有新提交：drover rebase 了集成分支（摘出来的提交换了提交号，集成状态记的
        还是 rebase 之前的那个）再推上去 → 按补丁内容认出它推了，不算没推"""
        a = self.item("a", {"shared.txt": "from a\n"})
        self.upstream_commit("upstream shared", {"shared.txt": "from upstream\n"})
        wt = self.integrate_resolved(a, {"shared.txt": "from upstream and a\n"})
        self.check(wt)
        before = self.head(wt)
        self.upstream_commit("upstream docs", {"docs/notes.md": "notes\n"})
        self.assertTrue(self.push(wt)["rebased"])
        self.assertTrue(self.push(wt)["verified"])
        self.assertNotEqual(self.head(wt), before)
        self.assertEqual(self.load_state()["picked"][0]["new_sha"], before)
        self.assertEqual(self.wrapup("--no-remote")["unpushed_items"], [])

    def test_integrated_records_keep_every_new_commit(self) -> None:
        """集成记录读成 {条目提交: [新提交…]}：同一个条目提交摘到两条集成分支上的两个都留着（一条推了、另一条没推时不被后读
        的那份盖掉）；没记新提交的（空提交）记 None；没应用的（conflict）不在里面"""
        d = self.load_drover()
        main = {"repo": str(self.src), "branch": "task/s1", "picked": [
            {"sha": "x", "status": "resolved", "new_sha": "y1"}, {"sha": "e", "status": "empty"},
            {"sha": "c", "status": "conflict"}]}
        release = {"repo": str(self.src), "branch": "task/s1-release", "picked": [
            {"sha": "x", "status": "picked", "new_sha": "y2"}, {"sha": "x", "status": "picked", "new_sha": "y2"}]}
        integ, picked = d.integrated_records([{"states": [(None, main), (None, release)], "pushes": []}])
        self.assertEqual(picked, {"x": ["y1", "y2"], "e": [None]})
        self.assertEqual({b for _, b in integ}, {"task/s1", "task/s1-release"})

    def test_check_coverage_of_pushes(self) -> None:
        """整体检查覆盖推上去的提交：push 记下的检查和存档的 head 都对得上 → 骨架的检查行写"覆盖推到 origin/main 的 <sha>"；
        之后又带 --unchecked 推了一次 → 那次推送没有检查覆盖，warnings 和骨架都写明，带上理由"""
        a = self.item("a", {"app/a.py": "A = 1\n"})
        wt = self.ship("main", a)
        first = self.head(wt)
        draft = self.tmp / "wrapup.md"
        res = self.wrapup("--no-remote", "--draft", str(draft))
        self.assertEqual(res["checks"][0]["covers"], [f"origin/main {first[:9]}"])
        self.assertIn(f"覆盖推到 origin/main 的 {first[:9]}", draft.read_text())
        self.assertFalse(any("没有整体检查覆盖" in w for w in res["warnings"]), res["warnings"])
        b = self.item("b", {"app/b.py": "B = 1\n"})
        self.integrate(a, b)
        self.assertTrue(self.push(wt, "--unchecked", "用户要先上线、检查之后补")["verified"])
        second = self.head(wt)
        res2 = self.wrapup("--no-remote", "--draft", str(draft), "--force")
        self.assertTrue(any("没有整体检查覆盖" in w and second[:9] in w and "用户要先上线" in w
                            for w in res2["warnings"]), res2["warnings"])
        text = draft.read_text()
        self.assertIn(f"{second[:9]}（--unchecked：用户要先上线、检查之后补）", text)
        self.assertIn(f"覆盖推到 origin/main 的 {first[:9]}", text)
        self.assertEqual(res2["checks"][0]["covers"], [f"origin/main {first[:9]}"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
