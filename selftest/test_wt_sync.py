"""wt 新建前同步源仓库主检出（几次发布后主目录停在旧提交，新 worktree 从旧代码起步），和 .env*、.worktreeinclude
的复制规则。

仓库布局同 integrate（selftest_repos）：本地裸仓库当 origin，tmp/upstream 模拟别人往上游推，假 herdr 的 worktree create
真的建 git worktree。setUp 给源仓库的 main 配上上游 origin/main（真实 clone 出来的仓库都有）；没配上游的回退单独测。
"没 fetch"用源仓库里 origin/main 的记录没变来判断，"没快进"用主检出 HEAD 没变来判断。
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import subprocess
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from selftest_repos import IntegrationCase

BRANCH = "task/w1-a"
LABEL = "shop:w1-a"


class WtSyncCase(IntegrationCase):
    def setUp(self) -> None:
        super().setUp()
        self.git(self.src, "branch", "-q", "--set-upstream-to=origin/main")
        # 本地记录远端默认分支 origin/HEAD → origin/main（真实 clone 出来的仓库都有；只写本地 ref，不联网）
        self.git(self.src, "remote", "set-head", "origin", "main")
        self.old = self.head(self.src)

    def push_feature(self, name: str) -> str:
        """主检出切到新分支 name、提交一次、push -u（配上上游 origin/<name>），返回这个提交的 sha。"""
        self.git(self.src, "checkout", "-q", "-b", name)
        sha = self.commit(self.src, f"{name} work", {f"app/{name}.py": "F = 1\n"})
        self.git(self.src, "push", "-q", "-u", "origin", name)
        return sha

    def wt(self, *extra: str, rc: int = 0, branch: str = BRANCH) -> dict:
        return self.drover_json("wt", "--repo", str(self.src), "--branch", branch, "--label", LABEL, *extra, rc=rc)

    def ns(self, **kw) -> argparse.Namespace:
        args = dict(repo=str(self.src), branch=BRANCH, label=LABEL, path=None, base=None, no_sync=False, dry_run=False)
        args.update(kw)
        return argparse.Namespace(**args)

    def remote_branch(self, name: str, files: dict, start: str = "main") -> str:
        """别人在上游推一条新分支 name（从 upstream 克隆里的 start 拉），返回它的提交 sha。"""
        self.git(self.up, "checkout", "-q", "-b", name, start)
        sha = self.commit(self.up, f"{name} work", files)
        self.git(self.up, "push", "-q", "origin", name)
        self.git(self.up, "checkout", "-q", "main")
        return sha

    def creates(self) -> list:
        return [c for c in self.herdr_calls() if c[:2] == ["worktree", "create"]]

    def create_base(self) -> str:
        calls = self.creates()
        self.assertEqual(len(calls), 1)
        return calls[0][calls[0].index("--base") + 1]

    def tracking(self, ref: str = "origin/main") -> str:
        """源仓库本地记录的远端分支 sha（没有就是空串）。"""
        return self.git(self.src, "rev-parse", "--verify", "--quiet", ref, check=False)


class WtSyncTests(WtSyncCase):
    def test_behind_fast_forwards_and_starts_from_latest(self) -> None:
        """主目录干净、落后上游 → fetch、快进主目录，worktree 从最新起（起点 HEAD），没有 warnings"""
        latest = self.upstream_commit("release 1", {"app/core.py": "VALUE = 2\n"})
        res = self.wt()
        sync = res["source_sync"]
        self.assertEqual(sync["status"], "fast_forwarded")
        self.assertEqual((sync["branch"], sync["upstream"]), ("main", "origin/main"))
        self.assertEqual((sync["before"], sync["after"], sync["behind"]), (self.old, latest, 1))
        self.assertEqual([(f["ref"], f["ok"], f["tries"]) for f in sync["fetch"]], [("origin/main", True, 1)])
        self.assertEqual(self.head(self.src), latest)
        self.assertEqual(res["head_sha"], latest)
        self.assertEqual(res["base_ref"], "HEAD")
        self.assertEqual(self.create_base(), "HEAD")
        self.assertEqual(res["warnings"], [])
        self.assertEqual((Path(res["worktree_path"]) / "app" / "core.py").read_text(), "VALUE = 2\n")

    def test_up_to_date(self) -> None:
        """主目录已是最新 → 不动，worktree 从 HEAD 起"""
        res = self.wt()
        self.assertEqual(res["source_sync"]["status"], "up_to_date")
        self.assertEqual(res["source_sync"]["after"], self.old)
        self.assertEqual((res["head_sha"], res["base_ref"], res["warnings"]), (self.old, "HEAD", []))

    def test_dirty_source_untouched_starts_from_upstream(self) -> None:
        """主目录有已跟踪文件的改动 → 不快进、改动还在，worktree 从 origin/main 起，warnings 写明原因"""
        latest = self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        (self.src / "README.md").write_text("# shop (local edit)\n")
        res = self.wt()
        sync = res["source_sync"]
        self.assertEqual(sync["status"], "dirty")
        self.assertEqual(sync["dirty_files"], ["README.md"])
        self.assertEqual(self.head(self.src), self.old)
        self.assertEqual((self.src / "README.md").read_text(), "# shop (local edit)\n")
        self.assertEqual(self.tracking(), latest)  # fetch 过
        self.assertEqual((res["head_sha"], res["base_ref"]), (latest, "origin/main"))
        self.assertEqual(self.create_base(), "origin/main")
        self.assertEqual(len(res["warnings"]), 1)
        self.assertIn("主目录没更新", res["warnings"][0])
        self.assertIn("已跟踪文件的改动", res["warnings"][0])
        self.assertIn("worktree 从 origin/main 起", res["warnings"][0])

    def test_diverged_source_untouched_starts_from_upstream(self) -> None:
        """主目录和上游分叉（本地有没推送的提交，上游也有新提交）→ 不动主目录，worktree 从 origin/main 起"""
        local = self.commit(self.src, "local only", {"app/local.py": "L = 1\n"})
        latest = self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        res = self.wt()
        sync = res["source_sync"]
        self.assertEqual((sync["status"], sync["ahead"], sync["behind"]), ("diverged", 1, 1))
        self.assertEqual(self.head(self.src), local)
        self.assertEqual((res["head_sha"], res["base_ref"]), (latest, "origin/main"))
        self.assertIn("分叉", res["warnings"][0])
        self.assertIn("不含主目录的本地提交", res["warnings"][0])

    def test_ahead_starts_from_head_with_warning(self) -> None:
        """主目录领先上游（有没推送的提交）→ 从 HEAD 起，warnings 提醒 worktree 带着这些提交"""
        local = self.commit(self.src, "local only", {"app/local.py": "L = 1\n"})
        res = self.wt()
        self.assertEqual((res["source_sync"]["status"], res["source_sync"]["ahead"]), ("ahead", 1))
        self.assertEqual((res["head_sha"], res["base_ref"]), (local, "HEAD"))
        self.assertEqual(len(res["warnings"]), 1)
        self.assertIn("1 个没推送的提交", res["warnings"][0])

    def test_ff_failed_on_untracked_file_starts_from_upstream(self) -> None:
        """快进被主目录的未跟踪文件挡住（上游新增了同名文件）→ 记 ff_failed 带 stderr，不阻断，worktree 从 origin/main 起"""
        latest = self.upstream_commit("add example", {"app/new.py": "N = 1\n"})
        (self.src / "app" / "new.py").write_text("mine\n")
        res = self.wt()
        sync = res["source_sync"]
        self.assertEqual(sync["status"], "ff_failed")
        self.assertIn("untracked", sync["ff_stderr"])
        self.assertEqual(self.head(self.src), self.old)
        self.assertEqual((res["head_sha"], res["base_ref"]), (latest, "origin/main"))
        self.assertIn("merge --ff-only 失败", res["warnings"][0])
        self.assertIn("worktree 从 origin/main 起", res["warnings"][0])

    def test_ff_failed_on_ignored_local_file_keeps_it(self) -> None:
        """上游把主目录里一个被 .gitignore 忽略的本地文件同名提交进了仓库（git merge 默认会覆盖被忽略的文件）：不覆盖
        用户的本地文件，按快进失败处理——主目录不动，worktree 从 origin/main 起，warnings 写原因（审核 LC-13 E10）"""
        before = self.commit(self.src, "ignore local.cfg", {".gitignore": "local.cfg\n"})
        self.git(self.src, "push", "-q", "origin", "main")
        (self.src / "local.cfg").write_text("用户本机的配置\n")
        self.git(self.up, "pull", "-q", "--ff-only", "origin", "main")
        (self.up / "local.cfg").write_text("仓库里的模板\n")
        self.git(self.up, "add", "-f", "local.cfg")
        latest = self.commit(self.up, "track local.cfg", {})
        self.git(self.up, "push", "-q", "origin", "main")
        res = self.wt()
        sync = res["source_sync"]
        self.assertEqual(sync["status"], "ff_failed")
        self.assertIn("local.cfg", sync["ff_stderr"])
        self.assertEqual(self.head(self.src), before)
        self.assertEqual((self.src / "local.cfg").read_text(), "用户本机的配置\n")
        self.assertEqual((res["head_sha"], res["base_ref"]), (latest, "origin/main"))
        self.assertIn("merge --ff-only 失败", res["warnings"][0])
        self.assertIn("worktree 从 origin/main 起", res["warnings"][0])

    def test_explicit_remote_base_fetched_first(self) -> None:
        """--base origin/<别的分支>：先 fetch 它（本地原来没有），worktree 从它的最新起；主目录照样快进"""
        rel = self.remote_branch("release", {"app/rel.py": "R = 1\n"})
        latest = self.upstream_commit("main moves", {"docs/guide.md": "guide\n"})
        self.assertEqual(self.tracking("origin/release"), "")
        res = self.wt("--base", "origin/release")
        sync = res["source_sync"]
        self.assertEqual([(f["ref"], f["ok"]) for f in sync["fetch"]], [("origin/main", True), ("origin/release", True)])
        self.assertEqual(sync["status"], "fast_forwarded")
        self.assertEqual(self.head(self.src), latest)
        self.assertEqual((res["head_sha"], res["base_ref"]), (rel, "origin/release"))
        self.assertEqual(self.create_base(), "origin/release")
        self.assertEqual(res["warnings"], [])

    def test_explicit_base_same_as_upstream_fetched_once(self) -> None:
        """--base 和主检出的上游是同一条 → 只 fetch 一次"""
        latest = self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        res = self.wt("--base", "origin/main")
        self.assertEqual([f["ref"] for f in res["source_sync"]["fetch"]], ["origin/main"])
        self.assertEqual((res["head_sha"], res["base_ref"]), (latest, "origin/main"))

    def test_fetch_failure_stops_before_herdr(self) -> None:
        """有上游但 fetch 失败 → ok: false，提示 --no-sync，不调 herdr worktree create、主目录不动；之后加 --no-sync 能用本地
        代码建。这里是远端仓库没了（"does not appear to be a git repository"，不会自己好）：只试 1 次、不等"""
        self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        self.git(self.src, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        d = self.load_drover()
        sleeps = []
        d.time = SimpleNamespace(sleep=sleeps.append, time=time.time, strftime=time.strftime)
        with self.assertRaises(d.DroverError) as cm:
            d.cmd_wt(self.ns())
        self.assertIn("试了 1 次", cm.exception.message)
        self.assertIn("--no-sync", cm.exception.message)
        fetch = cm.exception.extra["source_sync"]["fetch"]
        self.assertEqual([(f["ref"], f["ok"], f["tries"]) for f in fetch], [("origin/main", False, 1)])
        self.assertIn("does not appear to be a git repository", fetch[0]["stderr"])
        self.assertEqual(sleeps, [])
        self.assertEqual(self.creates(), [])
        self.assertEqual(self.head(self.src), self.old)
        res = d.cmd_wt(self.ns(no_sync=True))
        self.assertEqual((res["source_sync"]["status"], res["head_sha"]), ("not_synced", self.old))

    def test_transient_fetch_failure_retried(self) -> None:
        """fetch 遇到偶发的网络错误（ssh 握手时连接被重置）→ 隔 5 秒重试；前两次失败、第 3 次成功 → 照常快进、从最新建。
        远端经假 ssh 访问（前两次打 ssh 的报错原文，之后在本机执行 git-upload-pack），sleep 换成只记下来"""
        latest = self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        url = self.ssh_origin(2, "kex_exchange_identification: read: Connection reset by peer")
        self.git(self.src, "remote", "set-url", "origin", url)
        d = self.load_drover()
        sleeps = []
        d.time = SimpleNamespace(sleep=sleeps.append, time=time.time, strftime=time.strftime)
        res = d.cmd_wt(self.ns())
        sync = res["source_sync"]
        self.assertEqual([(f["ref"], f["ok"], f["tries"]) for f in sync["fetch"]], [("origin/main", True, 3)])
        self.assertEqual(sleeps, [5, 5])
        self.assertEqual(len(self.ssh_calls()), 3)
        self.assertEqual((sync["status"], res["head_sha"]), ("fast_forwarded", latest))
        self.assertEqual(self.head(self.src), latest)

    def test_no_sync_skips_fetch_and_ff(self) -> None:
        """--no-sync：不 fetch、不快进，起点是主目录当前 HEAD（旧行为），输出标明没同步"""
        self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        res = self.wt("--no-sync")
        self.assertEqual(self.tracking(), self.old)
        self.assertEqual(self.head(self.src), self.old)
        self.assertEqual(res["source_sync"]["status"], "not_synced")
        self.assertEqual((res["head_sha"], res["base_ref"]), (self.old, "HEAD"))
        self.assertIn("--no-sync", res["warnings"][0])

    def test_dry_run_reports_state_without_side_effects(self) -> None:
        """--dry-run：不 fetch、不快进、不调 herdr 建 / 开；报主检出现状和会执行的命令"""
        self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        res = self.wt("--dry-run")
        self.assertEqual(self.tracking(), self.old)
        self.assertEqual(self.head(self.src), self.old)
        self.assertEqual([c[:2] for c in self.herdr_calls()], [["worktree", "list"]])
        sync = res["source_sync"]
        self.assertEqual(sync["status"], "dry_run")
        self.assertEqual((sync["branch"], sync["upstream"], sync["head"], sync["upstream_sha_local"], sync["dirty"]),
                         ("main", "origin/main", self.old, self.old, False))
        planned = res["planned"]
        self.assertIn(f"git -C {self.src} fetch origin main", planned[0])
        self.assertIn(f"git -C {self.src} merge --ff-only --no-overwrite-ignore origin/main", planned[1])
        self.assertTrue(planned[-1].startswith("herdr worktree create"))
        self.assertIn("--base HEAD", planned[-1])
        self.assertEqual(res["base_ref"], "HEAD")

    def test_reuse_does_not_sync(self) -> None:
        """复用已有 worktree：不 fetch、不快进（herdr worktree open）"""
        first = self.wt()
        self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        res = self.wt()
        self.assertTrue(res["reused"])
        self.assertEqual(res["source_sync"]["status"], "reused")
        self.assertIsNone(res["base_ref"])
        self.assertEqual(self.tracking(), self.old)
        self.assertEqual(self.head(self.src), self.old)
        self.assertEqual(res["head_sha"], first["head_sha"])
        self.assertEqual(len([c for c in self.herdr_calls() if c[:2] == ["worktree", "open"]]), 1)

    def test_no_configured_upstream_falls_back_to_origin_branch(self) -> None:
        """主检出没配上游 → 用 origin/<当前分支>，照样 fetch、快进"""
        self.git(self.src, "branch", "--unset-upstream")
        latest = self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        res = self.wt()
        self.assertEqual((res["source_sync"]["status"], res["source_sync"]["upstream"]), ("fast_forwarded", "origin/main"))
        self.assertEqual((res["head_sha"], self.head(self.src)), (latest, latest))

    def test_local_branch_without_remote_counterpart(self) -> None:
        """主检出在本地分支上、没配上游、远端也没有这条分支 → 没有上游（不算错），从 HEAD 起并提示"""
        self.git(self.src, "checkout", "-q", "-b", "feat-local")
        res = self.wt()
        sync = res["source_sync"]
        self.assertEqual((sync["status"], sync["upstream"]), ("no_upstream", None))
        self.assertEqual([(f["ref"], f["ok"], f.get("note")) for f in sync["fetch"]],
                         [("origin/feat-local", False, "远端没有这条分支")])
        self.assertEqual((res["head_sha"], res["base_ref"]), (self.old, "HEAD"))
        self.assertIn("没法判断上游", res["warnings"][0])

    def test_detached_head_has_no_upstream(self) -> None:
        """主检出 detached → 不 fetch、不动，从 HEAD 起并提示没法判断上游"""
        self.git(self.src, "checkout", "-q", "--detach")
        self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        res = self.wt()
        self.assertEqual((res["source_sync"]["status"], res["source_sync"]["fetch"]), ("detached", []))
        self.assertEqual(self.tracking(), self.old)
        self.assertEqual((res["head_sha"], res["base_ref"]), (self.old, "HEAD"))
        self.assertIn("detached", res["warnings"][0])

    def test_configured_upstream_gone_starts_from_head(self) -> None:
        """配了上游、但远端已经删了这条分支（合并后常被删，不少仓库的主检出就停在这种分支上）→ 不算错：
        upstream_gone，从 HEAD 起，warnings 写明原因"""
        feat = self.push_feature("feature_x")
        self.git(self.up, "push", "-q", "origin", "--delete", "feature_x")
        self.assertEqual(self.tracking("origin/feature_x"), feat)  # 本地还留着过期的远端跟踪分支
        res = self.wt()
        sync = res["source_sync"]
        self.assertEqual((sync["status"], sync["upstream"]), ("upstream_gone", None))
        self.assertEqual(sync["reason"], "feature_x 的上游 origin/feature_x 在远端已经没有了")
        self.assertEqual([(f["ref"], f["ok"], f.get("note")) for f in sync["fetch"]],
                         [("origin/feature_x", False, "远端没有这条分支")])
        self.assertEqual((res["head_sha"], res["base_ref"]), (feat, "HEAD"))
        self.assertEqual(self.head(self.src), feat)
        self.assertIn("没法判断上游", res["warnings"][0])
        self.assertIn("feature_x 的上游 origin/feature_x 在远端已经没有了", res["warnings"][0])

    def test_configured_upstream_gone_in_chinese_locale(self) -> None:
        """同上，用户的 locale 是中文（LC_ALL=zh_CN.UTF-8）：git 自己的话会被翻译（"无法找到远程引用"），drover 跑联网 git
        时固定 C locale，照样认出"远端没有这条分支"、按 upstream_gone 继续。本机 git 没有中文翻译就跳过"""
        feat = self.push_feature("feature_x")
        self.git(self.up, "push", "-q", "origin", "--delete", "feature_x")
        self.env.update(LANG="zh_CN.UTF-8", LC_ALL="zh_CN.UTF-8")
        probe = subprocess.run(["git", "-C", str(self.src), "fetch", "origin", "feature_x"], env=self.env,
                               capture_output=True, text=True)
        if "无法找到远程引用" not in probe.stderr:
            self.skipTest(f"本机 git 在 zh_CN 下没有中文输出：{probe.stderr.strip()}")
        res = self.wt()
        sync = res["source_sync"]
        self.assertEqual((sync["status"], sync["upstream"]), ("upstream_gone", None))
        self.assertEqual([(f["ref"], f["ok"], f.get("note")) for f in sync["fetch"]],
                         [("origin/feature_x", False, "远端没有这条分支")])
        self.assertEqual((res["head_sha"], res["base_ref"]), (feat, "HEAD"))

    def test_configured_upstream_gone_with_explicit_base(self) -> None:
        """同上，但带 --base origin/<别的分支>（integrate 就是这样传的）→ 照常建，先 fetch 它、从它的最新起"""
        self.push_feature("feature_x")
        self.git(self.up, "push", "-q", "origin", "--delete", "feature_x")
        rel = self.remote_branch("release", {"app/rel.py": "R = 1\n"})
        res = self.wt("--base", "origin/release")
        sync = res["source_sync"]
        self.assertEqual(sync["status"], "upstream_gone")
        self.assertEqual([(f["ref"], f["ok"]) for f in sync["fetch"]], [("origin/feature_x", False), ("origin/release", True)])
        self.assertEqual((res["head_sha"], res["base_ref"]), (rel, "origin/release"))
        self.assertEqual(res["warnings"], [])

    def test_hint_when_not_on_remote_default_branch(self) -> None:
        """主检出在 feature 分支上（远端默认分支 main）→ warnings 提示一句（--dry-run 也有），不切分支、起点照旧；
        带显式 --base 不提示"""
        feat = self.push_feature("feature_y")
        hint = ("主目录在 feature_y 上，不是 origin 的默认分支 main；worktree 跟着主目录的分支走，"
                "要从别的分支起请传 --base origin/<分支>")
        plan = self.wt("--dry-run")
        self.assertEqual(plan["warnings"], [hint])
        self.assertEqual(plan["source_sync"]["remote_default"], "origin/main")
        res = self.wt()
        self.assertEqual(res["warnings"], [hint])
        self.assertEqual((res["source_sync"]["status"], res["source_sync"]["remote_default"]),
                         ("up_to_date", "origin/main"))
        self.assertEqual((res["head_sha"], res["base_ref"]), (feat, "HEAD"))
        self.assertEqual(self.git(self.src, "rev-parse", "--abbrev-ref", "HEAD"), "feature_y")
        explicit = self.wt("--base", "origin/main", branch="task/w1-b")
        self.assertEqual(explicit["warnings"], [])

    def test_no_hint_on_default_branch_or_without_origin_head(self) -> None:
        """主检出在 main 上 → 不提示；本地分支名不同但上游是 origin/main → 按上游分支名比，不提示；
        本地没有 origin/HEAD 记录 → 解析不出，不提示"""
        on_main = self.wt()
        self.assertEqual(on_main["source_sync"]["remote_default"], "origin/main")
        self.assertEqual(on_main["warnings"], [])
        self.git(self.src, "checkout", "-q", "-b", "my-main", "--track", "origin/main")
        renamed = self.wt(branch="task/w1-b")
        self.assertEqual(renamed["source_sync"]["upstream"], "origin/main")
        self.assertEqual(renamed["warnings"], [])
        self.git(self.src, "remote", "set-head", "origin", "--delete")
        self.push_feature("feature_z")
        res = self.wt(branch="task/w1-c")
        self.assertIsNone(res["source_sync"]["remote_default"])
        self.assertEqual(res["warnings"], [])

    def test_concurrent_sync_waits_for_repo_lock(self) -> None:
        """同一仓库的 fetch + 快进加文件锁：锁被占着时 wt 等着（不 fetch、不建），放开后照常完成并报出等了多久。
        wt 在进程里的另一个线程跑（flock 按打开的文件算，同一进程里另开一次照样要等）；包一层 repo_sync_lock，确认 wt 已经走到
        拿锁这一步再开始计时，不靠"子进程 1.5 秒内总该走到了"（机器负载 100 以上时子进程还没走到锁，测试误报过）"""
        self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        d = self.load_drover()
        lock_path = d.repo_sync_lock_path(str(self.src))
        self.assertEqual(lock_path.parent, self.state / "locks")
        self.assertNotEqual(lock_path, d.repo_sync_lock_path(str(self.up)))
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = open(lock_path, "a+")
        self.addCleanup(holder.close)
        fcntl.flock(holder, fcntl.LOCK_EX)
        reached = threading.Event()
        real_lock = d.repo_sync_lock

        @contextlib.contextmanager
        def watched_lock(repo: str):
            reached.set()
            with real_lock(repo) as waited:
                yield waited

        d.repo_sync_lock = watched_lock
        outcome: dict = {}

        def run_wt() -> None:
            try:
                outcome["res"] = d.cmd_wt(self.ns())
            except Exception as exc:  # 交给主线程报
                outcome["error"] = exc

        worker = threading.Thread(target=run_wt)
        worker.start()
        self.addCleanup(worker.join, 60)
        self.addCleanup(fcntl.flock, holder, fcntl.LOCK_UN)  # 中途失败时先放锁，再等线程结束
        self.assertTrue(reached.wait(60), "wt 没走到拿锁那一步")
        time.sleep(1.2)
        self.assertTrue(worker.is_alive())  # 还在等锁
        self.assertEqual(self.tracking(), self.old)
        self.assertEqual(self.creates(), [])
        fcntl.flock(holder, fcntl.LOCK_UN)
        worker.join(60)
        self.assertNotIn("error", outcome, outcome.get("error"))
        res = outcome["res"]
        self.assertEqual(res["source_sync"]["status"], "fast_forwarded")
        self.assertGreaterEqual(res["source_sync"]["lock_waited_seconds"], 1)


class EnvCopyTests(WtSyncCase):
    """.env* / .claude/settings.local.json 的复制规则：源仓库跟踪的不复制，除非在新 worktree 里被忽略；没跟踪的照旧复制。"""

    def test_tracked_env_absent_on_base_not_copied(self) -> None:
        """踩过的坑：主检出跟踪着 .env.example，基线分支上没有 → 不复制（否则成了未跟踪文件，挡住 cherry-pick）"""
        self.remote_branch("release/1.2", {"app/old.py": "O = 1\n"})
        self.commit(self.src, "add env example", {".env.example": "KEY=\n"})
        self.git(self.src, "push", "-q", "origin", "main")
        plan = self.wt("--base", "origin/release/1.2", "--dry-run")
        self.assertIn(".env.example（源仓库跟踪着，在新 worktree 里被 .gitignore 忽略才复制）", plan["would_copy"])
        res = self.wt("--base", "origin/release/1.2")
        wt = Path(res["worktree_path"])
        self.assertNotIn(".env.example", res["copied"])
        self.assertFalse((wt / ".env.example").exists())
        self.assertEqual(self.git(wt, "status", "--porcelain"), "")

    def test_tracked_env_ignored_on_base_copied(self) -> None:
        """主检出还跟踪着 .env，基线分支已把它移出 git 并忽略 → 照复制（worker 仍要这份本地配置），worktree 里是干净的"""
        self.commit(self.src, "track env", {".env": "A=1\n"})
        self.git(self.src, "push", "-q", "origin", "main")
        self.git(self.up, "fetch", "-q", "origin")
        self.git(self.up, "checkout", "-q", "-b", "release", "origin/main")
        self.git(self.up, "rm", "-q", "--cached", ".env")
        self.commit(self.up, "untrack env", {".gitignore": ".env\n"})
        self.git(self.up, "push", "-q", "origin", "release")
        res = self.wt("--base", "origin/release")
        wt = Path(res["worktree_path"])
        self.assertIn(".env", res["copied"])
        self.assertEqual((wt / ".env").read_text(), "A=1\n")
        self.assertEqual(self.git(wt, "status", "--porcelain"), "")

    def test_untracked_local_files_copied(self) -> None:
        """源仓库没跟踪的 .env.local、.claude/settings.local.json 照旧复制；未跟踪文件不算改动，不妨碍快进"""
        latest = self.upstream_commit("release 1", {"docs/guide.md": "guide\n"})
        self.write_files(self.src, {".env.local": "B=2\n", ".claude/settings.local.json": "{}\n"})
        res = self.wt()
        wt = Path(res["worktree_path"])
        self.assertEqual(res["source_sync"]["status"], "fast_forwarded")
        self.assertEqual(res["head_sha"], latest)
        self.assertEqual(sorted(res["copied"]), [".claude/settings.local.json", ".env.local"])
        self.assertEqual((wt / ".env.local").read_text(), "B=2\n")
        self.assertEqual((wt / ".claude" / "settings.local.json").read_text(), "{}\n")


CONFIG = "src/main/resources/config"
DOCS_NOTE = "以及被 .gitignore 忽略的 CLAUDE.md / CLAUDE.local.md / AGENTS.md"


class WorktreeIncludeTests(WtSyncCase):
    """仓库根目录 .worktreeinclude（Claude Code 的约定，gitignore 语法）声明的额外本地文件：匹配某条模式、在源仓库被忽略的
    未跟踪文件才复制，且只复制在新 worktree 里同样被忽略的。布局照一个 Java 服务：本地启动配置
    src/main/resources/config/*.yml 被 .gitignore 忽略（oss.yml 跟踪着，*.example 是模板），本地测试页 tools/ 和
    .worktreeinclude 本身靠 .git/info/exclude 只在本机忽略（各 worktree 共用这份 exclude）。"""

    def setUp(self) -> None:
        super().setUp()
        self.commit(self.src, "local config layout", {
            ".gitignore": f"{CONFIG}/*.yml\n!{CONFIG}/oss.yml\n.env.local\n",
            f"{CONFIG}/oss.yml": "oss: tracked\n",
            f"{CONFIG}/asr.yml.example": "asr: example\n",
        })
        self.git(self.src, "push", "-q", "origin", "main")
        (self.src / ".git" / "info" / "exclude").write_text("/tools/\n/.worktreeinclude\n")
        self.write_files(self.src, {
            f"{CONFIG}/asr.yml": "asr: local\n",
            f"{CONFIG}/redis.yml": "redis: local\n",
            f"{CONFIG}/secret.yml": "secret: local\n",
            "tools/asr-ws-test.html": "<html>test</html>\n",
            "tools/sub/helper.js": "H = 1\n",
            "notes/todo.md": "todo\n",
            ".env.local": "B=2\n",
        })
        (self.src / ".worktreeinclude").write_text("\n".join([
            "# 本机 worktree 要带的本地启动配置和测试页",
            f"{CONFIG}/*.yml",
            f"!{CONFIG}/secret.yml",
            "tools/",
            "notes/*.md",
            ".env.local",
        ]) + "\n")

    def test_dir_and_glob_patterns_copied(self) -> None:
        """目录模式 tools/ 展开成其中的文件、通配模式 config/*.yml 都复制；跟踪着的 oss.yml、模板 *.example、
        ! 取反的 secret.yml、匹配了但没被忽略的 notes/todo.md 都不复制；和 .env* 重复的只复制一次；worktree 是干净的"""
        res = self.wt()
        wt = Path(res["worktree_path"])
        self.assertEqual(sorted(res["copied"]), [".env.local", f"{CONFIG}/asr.yml", f"{CONFIG}/redis.yml",
                                                 "tools/asr-ws-test.html", "tools/sub/helper.js"])
        self.assertEqual(res["copied"].count(".env.local"), 1)
        self.assertEqual((wt / CONFIG / "asr.yml").read_text(), "asr: local\n")
        self.assertEqual((wt / "tools" / "asr-ws-test.html").read_text(), "<html>test</html>\n")
        self.assertEqual((wt / "tools" / "sub" / "helper.js").read_text(), "H = 1\n")
        self.assertEqual((wt / CONFIG / "oss.yml").read_text(), "oss: tracked\n")  # 随 checkout 来的
        self.assertFalse((wt / CONFIG / "secret.yml").exists())
        self.assertFalse((wt / "notes").exists())
        self.assertFalse((wt / ".worktreeinclude").exists())
        self.assertNotIn("worktreeinclude_skipped", res)
        self.assertEqual(self.git(wt, "status", "--porcelain"), "")

    def test_not_ignored_in_worktree_skipped_and_reported(self) -> None:
        """源仓库忽略 config/*.yml，基线分支的 .gitignore 没忽略 → 不复制（否则成了未跟踪文件），列进 worktreeinclude_skipped
        并写原因；tools/ 靠共用的 .git/info/exclude 忽略，照复制"""
        self.git(self.up, "fetch", "-q", "origin")
        self.git(self.up, "checkout", "-q", "-b", "release", "origin/main")
        self.commit(self.up, "stop ignoring config", {".gitignore": ".env.local\n"})
        self.git(self.up, "push", "-q", "origin", "release")
        res = self.wt("--base", "origin/release")
        wt = Path(res["worktree_path"])
        self.assertEqual(sorted(res["copied"]), [".env.local", "tools/asr-ws-test.html", "tools/sub/helper.js"])
        skipped = res["worktreeinclude_skipped"]
        self.assertEqual([s["path"] for s in skipped], [f"{CONFIG}/asr.yml", f"{CONFIG}/redis.yml"])
        self.assertTrue(all("没被 .gitignore 忽略" in s["reason"] for s in skipped))
        self.assertFalse((wt / CONFIG / "asr.yml").exists())
        self.assertEqual(self.git(wt, "status", "--porcelain"), "")

    def test_symlink_skipped(self) -> None:
        """源路径是符号链接 → 不复制，列进 worktreeinclude_skipped"""
        (self.src / "tools" / "link.html").symlink_to("asr-ws-test.html")
        res = self.wt()
        wt = Path(res["worktree_path"])
        self.assertNotIn("tools/link.html", res["copied"])
        self.assertIn("tools/asr-ws-test.html", res["copied"])
        self.assertFalse((wt / "tools" / "link.html").is_symlink() or (wt / "tools" / "link.html").exists())
        self.assertEqual(res["worktreeinclude_skipped"], [{"path": "tools/link.html", "reason": "源仓库里是符号链接，不复制"}])

    def test_reuse_fills_missing_without_overwrite(self) -> None:
        """复用已有 worktree：补复制源仓库新加的匹配文件（--dry-run 先列出来），worktree 里已有的同名文件不覆盖"""
        first = self.wt()
        wt = Path(first["worktree_path"])
        (wt / "tools" / "asr-ws-test.html").write_text("mine\n")
        self.write_files(self.src, {"tools/new.html": "new\n"})
        plan = self.wt("--dry-run")
        self.assertTrue(plan["reused"])
        self.assertEqual(plan["would_copy"], ["tools/new.html"])
        self.assertFalse((wt / "tools" / "new.html").exists())
        res = self.wt()
        self.assertTrue(res["reused"])
        self.assertEqual(res["copied"], ["tools/new.html"])
        self.assertEqual((wt / "tools" / "new.html").read_text(), "new\n")
        self.assertEqual((wt / "tools" / "asr-ws-test.html").read_text(), "mine\n")
        self.assertEqual(self.git(wt, "status", "--porcelain"), "")

    def test_dry_run_lists_matched_files(self) -> None:
        """新建的 --dry-run：would_copy 逐个列出源仓库这边匹配到的文件（和 .env* 重复的不重复列），不建 worktree"""
        plan = self.wt("--dry-run")
        self.assertEqual(plan["would_copy"],
                         f"（新 worktree 建好后复制：.env.local；{DOCS_NOTE}；以及 .worktreeinclude 匹配到的"
                         f"（在新 worktree 里同样被忽略才复制）：{CONFIG}/asr.yml、{CONFIG}/redis.yml、"
                         "tools/asr-ws-test.html、tools/sub/helper.js）")
        self.assertEqual(self.creates(), [])

    def test_without_worktreeinclude_unchanged(self) -> None:
        """没有 .worktreeinclude：would_copy、copied 和改动前一样，config/*.yml、tools/ 都不复制"""
        (self.src / ".worktreeinclude").unlink()
        plan = self.wt("--dry-run")
        self.assertEqual(plan["would_copy"], f"（新 worktree 建好后复制：.env.local；{DOCS_NOTE}）")
        res = self.wt()
        wt = Path(res["worktree_path"])
        self.assertEqual(res["copied"], [".env.local"])
        self.assertNotIn("worktreeinclude_skipped", res)
        self.assertFalse((wt / CONFIG / "asr.yml").exists())
        self.assertFalse((wt / "tools").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
