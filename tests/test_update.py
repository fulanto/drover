#!/usr/bin/env python3
"""drover update 的测试（标准库 unittest）：python3 tests/test_update.py

复用 tests/test_install.py 的 InstallTestBase（临时 HOME、假 herdr、假 node），DROVER_STATE_DIR 设成临时 HOME 下的 .drover。
"远端"用当前 worktree 的文件内容建：复制文件 → git init → 提交 → 做成 bare 远端 → clone 到临时 HOME 的 ~/.drover/src。
不能直接 git clone 这个 worktree：没提交的改动（update 本身）不在里面，clone 出来是旧代码。
update 经 ~/.drover/bin/drover 跑，跑的是 clone（copy 模式是副本）里的代码。绝不碰真实 HOME。
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_install import BEGIN, END, PKG, InstallTestBase  # noqa: E402

GIT_ID = ["-c", "user.name=drover-test", "-c", "user.email=drover-test@example.invalid", "-c", "commit.gpgsign=false"]
MARK = "UPD-MARK-7f3"
MARK_LINE = f"\n- update 测试新加的一条规则 {MARK}\n"
ENV_TEXT = "CHANNEL=feishu\nFEISHU_APP_SECRET=update-test-secret\n"


def clean_git_env(home: Path) -> dict:
    """不读真实的 git 全局 / 系统配置（签名、hook、别名），也不继承外面的 GIT_* 变量。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1")
    return env


def run_git(cwd: Path, *args: str, env: dict) -> str:
    p = subprocess.run(["git", "-C", str(cwd), *GIT_ID, *args], env=env, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败（{cwd}）：{p.stderr}")
    return p.stdout.strip()


def dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)[:4000]


class UpdateTestBase(InstallTestBase):
    # 更新通道（config.json 的 update_channel）。下面这些类测的是"拉上游、只快进、重跑 install"本身，按 main 通道跑（作者本机
    # 的用法，行为和加通道之前一样）；UpdateReleaseTests 是 None：不写这个键，按默认的 release 通道（跟版本 tag）。
    # 模板远端没打过版本 tag：这些类里输出、看板、watch --main 写的版本号都退回 7 位 sha（UpdateReleaseTests 的远端有 tag）。
    channel = "main"

    @classmethod
    def setUpClass(cls) -> None:
        """一次性做一个 bare 远端模板：内容是当前 worktree 的文件（跟踪的 + 没被忽略的新文件），每个用例复制一份用。"""
        super().setUpClass()
        cls.seed_tmp = Path(tempfile.mkdtemp(prefix="drover-update-seed-"))
        env = clean_git_env(cls.seed_tmp)
        seed = cls.seed_tmp / "seed"
        listed = subprocess.run(["git", "-C", str(PKG), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                env=env, capture_output=True, check=True, timeout=60).stdout.decode()
        for rel in filter(None, listed.split("\0")):
            src = PKG / rel
            if not os.path.lexists(src) or (src.is_dir() and not src.is_symlink()):
                continue  # 工作区里删掉的文件、子模块
            dst = seed / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst, follow_symlinks=False)
        subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], env=env, check=True, timeout=60)
        run_git(seed, "add", "-A", env=env)
        run_git(seed, "commit", "-q", "-m", "seed：当前 worktree 的内容", env=env)
        cls.template = cls.seed_tmp / "template.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(cls.template)], env=env, check=True, timeout=60)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.seed_tmp, True)
        super().tearDownClass()

    def setUp(self) -> None:
        super().setUp()
        self.env = {k: v for k, v in self.env.items() if not k.startswith("GIT_")}
        self.env.update(DROVER_STATE_DIR=str(self.state_home), GIT_CONFIG_NOSYSTEM="1")
        self.remote = self.tmp / "remote.git"
        shutil.copytree(self.template, self.remote, symlinks=True)
        self.clone = self.state_home / "src"  # README「安装」里默认 clone 的位置
        self.git(self.tmp, "clone", "-q", str(self.remote), str(self.clone))
        self.dev = self.tmp / "dev"  # 往"远端"推新提交用的另一份 clone
        self.git(self.tmp, "clone", "-q", str(self.remote), str(self.dev))
        if self.channel:  # install.py 保留 config.json 里它不认识的键（test_install 里有用例）
            self.write_json(self.config, {"update_channel": self.channel})

    # ---- 工具

    def git(self, cwd: Path, *args: str) -> str:
        return run_git(cwd, *args, env=self.env)

    def head(self, repo: Path) -> str:
        return self.git(repo, "rev-parse", "HEAD")

    def push_change(self, rel: str, append: str, message: str, branch: str = "main") -> str:
        """远端加一个提交：在 dev clone 里给 rel 追加内容、提交、推到 branch。返回新提交的 sha。"""
        path = self.dev / rel
        path.write_text(path.read_text() + append)
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", message)
        self.git(self.dev, "push", "-q", "origin", f"HEAD:{branch}")
        return self.head(self.dev)

    def clone_install(self, *args: str) -> dict:
        """从 clone 安装（照 README「安装」装的那样）。"""
        p = subprocess.run([sys.executable, str(self.clone / "install.py"), *args, *self.ws_args(), "--json"],
                           env=self.env, cwd=str(self.tmp), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        out = json.loads(p.stdout)
        self.assertTrue(out["ok"], dump(out))
        return out

    def update(self, *args: str, rc: int = 0, cli: Path = None) -> subprocess.CompletedProcess:
        p = subprocess.run([str(cli or self.bin), "update", *args], env=self.env, cwd=str(self.tmp),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
        if p.returncode != rc:
            self.fail(f"drover update {' '.join(args)} 退出码 {p.returncode}（期望 {rc}）\n"
                      f"stdout:\n{p.stdout[-4000:]}\nstderr:\n{p.stderr[-4000:]}")
        return p

    def update_json(self, *args: str, rc: int = 0) -> dict:
        return json.loads(self.update("--json", *args, rc=rc).stdout)

    def write_user_env(self) -> tuple:
        """插件配置目录里用户自己的 .env（飞书配对）：update 不能碰。返回 (内容, mtime)。"""
        env_file = self.oncall_dir / ".env"
        self.write(env_file, ENV_TEXT)
        return env_file.read_bytes(), env_file.stat().st_mtime_ns

    def assert_env_untouched(self, saved: tuple) -> None:
        env_file = self.oncall_dir / ".env"
        self.assertEqual((env_file.read_bytes(), env_file.stat().st_mtime_ns), saved)

    def load_cli(self, script: Path = None):
        """在进程里加载 clone 里的 bin/drover（script 给了就加载它，比如 copy 模式的副本；模块级常量按临时环境算），用例结束
        还原 os.environ。要等的地方（重试间隔、等锁的上限、联网超时）在进程里才能换成假的 / 改短。"""
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        os.environ.clear()
        os.environ.update(self.env)
        loader = importlib.machinery.SourceFileLoader("drover_update_auto", str(script or self.clone / "bin" / "drover"))
        spec = importlib.util.spec_from_loader("drover_update_auto", loader)
        mod = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode, saved_flag = True, sys.dont_write_bytecode
        try:
            loader.exec_module(mod)
        finally:
            sys.dont_write_bytecode = saved_flag
        self.assertEqual(mod.STATE_HOME, self.state_home)
        return mod

    @staticmethod
    def run_update(d, **args) -> tuple:
        """在进程里跑加载好的 d 的 cmd_update（参数默认同 drover update），返回 (退出码, stdout)。"""
        ns = dict(auto=False, dry_run=False, json=False)
        ns.update(args)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = d.cmd_update(argparse.Namespace(**ns))
        return rc, out.getvalue()


# ---------------------------------------------------------------- 正常更新


class UpdateLinkTests(UpdateTestBase):
    def test_link_update_refreshes_block_keeps_personal(self) -> None:
        """link 模式：远端改了 flow/routing.md → clone 快进、AGENTS.md 区块刷新、区块外 / settings / .env 原样、提示 /clear"""
        self.write(self.agents, "# 我的个人规则\n- 回答要短\n")
        self.write_json(self.settings, {"permissions": {"allow": ["Bash(git:*)"]}})
        self.clone_install("--host", "claude")
        self.assert_link(self.pkg_dir, Path(os.path.realpath(self.clone)))
        self.write(self.agents, self.agents.read_text() + "\n## 尾部个人内容\n- 别动我\n")
        saved_env = self.write_user_env()
        old = self.head(self.clone)
        new = self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")

        out = self.update_json()
        self.assertTrue(out["ok"], dump(out))
        self.assertEqual((out["mode"], out["hosts"]), ("link", ["claude"]))
        self.assertEqual((out["before"], out["after"], out["status"]), (old, new, "fast_forwarded"))
        self.assertEqual(self.head(self.clone), new)
        self.assertEqual(out["new_commits"], {"count": 1, "titles": ["routing：加一条规则"]})
        self.assertEqual(out["changed_files"], ["flow/routing.md"])
        self.assertIn("routing", out["install"]["changed_keys"], dump(out))
        self.assertNotIn("state", out["install"]["changed_keys"])
        self.assertEqual(out["install"]["failed"], [])
        self.assertTrue(out["check"]["ok"], dump(out))
        self.assertEqual(out["check"]["failed"], [])

        text = self.agents.read_text()
        self.assertTrue(text.startswith("# 我的个人规则\n- 回答要短\n"), text[:200])
        self.assertTrue(text.endswith("\n## 尾部个人内容\n- 别动我\n"), text[-200:])
        self.assertEqual((text.count(BEGIN), text.count(END)), (1, 1))
        self.assertIn(MARK, text[text.index(BEGIN):text.index(END)])
        self.assertIn("Bash(git:*)", self.load(self.settings)["permissions"]["allow"])
        self.assert_env_untouched(saved_env)
        self.assert_link(self.pkg_dir, Path(os.path.realpath(self.clone)))
        self.assertTrue(any("/clear" in s and "AGENTS.md" in s for s in out["next_steps"]), out["next_steps"])

    def test_no_new_commits_is_up_to_date(self) -> None:
        """没有新提交：已是最新、install 各项都没改（安装记录不重写）、下一步不用做别的"""
        self.clone_install("--host", "claude")
        head = self.head(self.clone)
        mtime = self.state.stat().st_mtime_ns
        out = self.update_json()
        self.assertTrue(out["ok"], dump(out))
        self.assertEqual((out["before"], out["after"], out["status"]), (head, head, "up_to_date"))
        self.assertEqual(out["new_commits"]["count"], 0)
        self.assertEqual(out["changed_files"], [])
        self.assertEqual((out["install"]["changed"], out["install"]["failed"]), ([], []))
        self.assertEqual(out["next_steps"], [])
        self.assertEqual(self.state.stat().st_mtime_ns, mtime)

        p = self.update()
        lines = p.stdout.splitlines()
        self.assertEqual(lines[0], f"drover 已是最新（{head[:7]}）", p.stdout)
        self.assertIn("安装：都已是最新", lines)
        self.assertIn("检查：安装完整", p.stdout)
        self.assertEqual(lines[-1], "下一步：不用做别的")
        self.assertEqual(self.state.stat().st_mtime_ns, mtime)

    def test_local_ahead_continues(self) -> None:
        """本地有没推送的提交（开发机常见）：照常重跑 install，输出里提一句"""
        self.clone_install("--host", "claude")
        readme = self.clone / "README.md"
        readme.write_text(readme.read_text() + "\n本地提交\n")
        self.git(self.clone, "commit", "-q", "-am", "本地提交，没推送")
        out = self.update_json()
        self.assertTrue(out["ok"], dump(out))
        self.assertEqual((out["status"], out["ahead"], out["new_commits"]["count"]), ("ahead", 1, 0))
        first = self.update().stdout.splitlines()[0]
        self.assertTrue(first.startswith("drover 已是最新（"), first)
        self.assertIn("本地领先上游 1 个提交", first)

    def test_hosts_from_record_not_autodetected(self) -> None:
        """安装记录只有 claude，临时 HOME 里也有 ~/.codex：update 带 --host claude，不多装 codex 那套"""
        self.clone_install("--host", "claude")
        self.codex.mkdir()
        self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        out = self.update_json()
        self.assertTrue(out["ok"], dump(out))
        self.assertIn("--host claude", out["install"]["command"])
        self.assertFalse((self.codex / "skills" / "drover").exists())
        self.assertFalse((self.codex / "skills").exists())
        self.assertNotIn("integration install codex", self.herdr_calls())
        self.assertEqual(self.load(self.state)["hosts"], ["claude"])

    def test_update_does_not_install_external_deps(self) -> None:
        """drover update 重跑 install 是非交互、不带 --install-deps：宿主命令行没了也只提示，外部依赖的安装命令一次都不跑
        （基类把 herdr / claude / codex / Node 的安装命令都换成了"禁止安装"，跑了用例就报错）"""
        self.clone_install("--host", "claude")
        (self.fakebin / "claude").unlink()
        other = self.tmp / "other-bin"
        other.mkdir()
        (other / "codex").write_text("#!/bin/sh\nexit 0\n")  # claude / codex 至少有一个：--check 不因为这个算缺依赖
        (other / "codex").chmod(0o755)
        dirs = [d for d in self.env["PATH"].split(os.pathsep)
                if d and not any(os.path.exists(os.path.join(d, n)) for n in ("claude", "codex"))]
        self.env["PATH"] = os.pathsep.join([str(other), *dirs])
        self.assertIsNone(shutil.which("claude", path=self.env["PATH"]))
        self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        out = self.update_json()
        self.assertTrue(out["ok"], dump(out))
        self.assertNotIn("--install-deps", out["install"]["command"])
        self.assertEqual(out["install"]["failed"], [])
        self.assertTrue(any("没找到 claude 命令" in n for n in out["install"]["notes"]), dump(out))
        self.assertFalse((self.home / ".local" / "bin" / "claude").exists())
        # claude 其实装在 ~/.local/bin、只是不在 PATH 里：update 也不往 shell 配置里加 PATH，只提示
        local_bin = self.home / ".local" / "bin"
        local_bin.mkdir(parents=True)
        (local_bin / "claude").write_text("#!/bin/sh\nexit 0\n")
        (local_bin / "claude").chmod(0o755)
        out = self.update_json()
        self.assertTrue(any("export PATH=" in n for n in out["install"]["notes"]), dump(out))
        self.assertFalse((self.home / ".zshrc").exists())

    def test_text_output_is_short(self) -> None:
        """默认文字输出：第一行"已更新"，标题最多 5 条加"…等 N 条"，总共不超过 10 行"""
        self.clone_install("--host", "claude")
        old = self.head(self.clone)
        self.push_change("flow/routing.md", MARK_LINE, "routing：第 1 条")
        for i in range(2, 8):
            new = self.push_change("README.md", f"\n第 {i} 条\n", f"readme：第 {i} 条")
        p = self.update()
        lines = p.stdout.splitlines()
        self.assertLessEqual(len(lines), 10, p.stdout)
        self.assertEqual(lines[0], f"drover 已更新：{old[:7]}→{new[:7]}（7 个新提交）")
        self.assertEqual(sum(line.startswith("  · ") for line in lines), 5, p.stdout)
        self.assertIn("  …等 7 条", lines)
        self.assertTrue(any(line.startswith("安装：改了") and "主工作台路由区块" in line for line in lines), p.stdout)
        self.assertIn("检查：安装完整", p.stdout)
        self.assertTrue(lines[-1].startswith("下一步：") and "/clear" in lines[-1], p.stdout)
        self.assertEqual(self.head(self.clone), new)


class UpdateCopyTests(UpdateTestBase):
    def test_copy_mode_stays_copy(self) -> None:
        """copy 模式：远端新提交 → update 后 ~/.drover/pkg 仍是副本、内容是新的、安装记录的 mode 仍是 copy；副本标记里的
        commit 是新的（drover_version 在 copy 模式读它）"""
        self.clone_install("--host", "claude", "--mode", "copy")
        self.assertFalse(self.pkg_dir.is_symlink())
        new = self.push_change("flow/dispatch.md", "\n<!-- update 测试改了流程 -->\n", "dispatch：改流程")
        out = self.update_json()
        self.assertTrue(out["ok"], dump(out))
        self.assertEqual((out["mode"], out["after"]), ("copy", new))
        self.assertEqual(out["clone"], os.path.realpath(self.clone))
        self.assertIn("pkg", out["install"]["changed_keys"], dump(out))
        self.assertIn("--mode copy", out["install"]["command"])
        self.assertFalse(self.pkg_dir.is_symlink())
        self.assertTrue((self.pkg_dir / ".drover-copy").is_file())
        self.assertEqual(json.loads((self.pkg_dir / ".drover-copy").read_text())["commit"], new)
        self.assertFalse((self.pkg_dir / ".git").exists())
        dispatch = (self.pkg_dir / "flow" / "dispatch.md").read_text()
        self.assertIn("update 测试改了流程", dispatch)
        self.assertEqual(dispatch, (self.clone / "flow" / "dispatch.md").read_text())
        self.assertEqual(self.load(self.state)["mode"], "copy")
        self.assertTrue(any("编排者" in s for s in out["next_steps"]), out["next_steps"])

    def test_copy_mode_clone_deleted_gives_manual_steps(self) -> None:
        """copy 模式装完把 clone 删了：退出码 1，给出手动步骤（clone 到临时目录、--mode copy、删掉），不自动 clone"""
        self.clone_install("--host", "claude", "--mode", "copy")
        shutil.rmtree(self.clone)
        mtime = self.state.stat().st_mtime_ns
        p = self.update(rc=1)
        self.assertTrue(p.stdout.startswith("drover 没更新："), p.stdout)
        for part in ("git clone", "/tmp/drover-update", "install.py --mode copy", "删掉"):
            self.assertIn(part, p.stdout)
        out = self.update_json(rc=1)
        self.assertFalse(out["ok"])
        self.assertIn("--mode copy", out["error"])
        self.assertFalse(self.clone.exists())
        self.assertEqual(self.state.stat().st_mtime_ns, mtime)


# ---------------------------------------------------------------- 不能更新：什么都不改


class UpdateBlockedTests(UpdateTestBase):
    def test_dirty_and_behind_does_not_install(self) -> None:
        """clone 有已跟踪改动且落后：不快进、不跑 install（安装记录 mtime 不变）、退出码 1、原因里提到本地改动"""
        self.clone_install("--host", "claude")
        self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        readme = self.clone / "README.md"
        readme.write_text(readme.read_text() + "\n本地改动，没提交\n")
        head, mtime, agents = self.head(self.clone), self.state.stat().st_mtime_ns, self.agents.read_text()
        out = self.update_json(rc=1)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "dirty")
        self.assertIn("本地改动", out["error"])
        self.assertIn("stash", out["error"])
        self.assertNotIn("install", out)
        self.assertEqual(self.head(self.clone), head)
        self.assertEqual(self.state.stat().st_mtime_ns, mtime)
        self.assertEqual(self.agents.read_text(), agents)
        p = self.update(rc=1)
        self.assertIn("没重跑 install", p.stdout.splitlines()[0])

    def test_fetch_failure_changes_nothing(self) -> None:
        """fetch 失败（远端没了）：退出码 1，说连不上远端、什么都没改；确实什么都没改。git 的原因行是 "does not appear to be
        a git repository"（不会自己好，结尾那句通用的 "Could not read from remote repository" 不算原因），只试 1 次、不等：
        在进程里跑，sleep 换成只记下来，核对没有重试"""
        self.clone_install("--host", "claude")
        shutil.rmtree(self.remote)
        watched = (self.ws, self.claude, self.state, self.config, self.pkg_dir, self.bin)
        before, head = self.snapshot_of(*watched), self.head(self.clone)
        d = self.load_cli()
        sleeps = []
        d.time = SimpleNamespace(sleep=sleeps.append, time=time.time, strftime=time.strftime, localtime=time.localtime)
        rc, text = self.run_update(d, json=True)
        self.assertEqual(rc, 1, text)
        out = json.loads(text)
        self.assertFalse(out["ok"])
        self.assertIn("连不上远端", out["error"])
        self.assertIn("什么都没改", out["error"])
        self.assertIn("试了 1 次", out["error"])
        self.assertNotIn("worktree", out["error"])  # 不是 wt 的文案
        self.assertEqual(sleeps, [])
        self.assertEqual(self.snapshot_of(*watched), before)
        self.assertEqual(self.head(self.clone), head)

    def test_not_installed(self) -> None:
        """没有安装记录也没有 ~/.drover/pkg：报没装过、指向 README 的「安装」一节"""
        p = self.update(rc=1, cli=PKG / "bin" / "drover")
        self.assertIn("没装过 drover", p.stdout)
        self.assertIn("README 的「安装」一节", p.stdout)


# ---------------------------------------------------------------- --dry-run


class UpdateDryRunTests(UpdateTestBase):
    def test_dry_run_only_fetches(self) -> None:
        """--dry-run：报落后的提交数和标题、关键文件变没变；clone HEAD、安装记录、AGENTS.md 都不变"""
        self.clone_install("--host", "claude")
        self.push_change("flow/routing.md", MARK_LINE, "routing 改了")
        upstream = self.push_change("README.md", "\nmore\n", "readme 改了")
        head, mtime, agents = self.head(self.clone), self.state.stat().st_mtime_ns, self.agents.read_text()

        out = self.update_json("--dry-run")
        self.assertTrue(out["ok"], dump(out))
        self.assertEqual((out["dry_run"], out["status"], out["behind"]), (True, "behind", 2))
        self.assertEqual(out["upstream_commits"], {"count": 2, "titles": ["readme 改了", "routing 改了"]})
        self.assertEqual(out["watch_files"], {"flow/routing.md": True, "install.py": False, "flow/dispatch.md": False})
        self.assertTrue(out["can_fast_forward"])
        self.assertNotIn("install", out)
        self.assertEqual(self.head(self.clone), head)
        self.assertEqual(self.git(self.clone, "rev-parse", "origin/main"), upstream)  # 只动了远端跟踪分支
        self.assertEqual(self.state.stat().st_mtime_ns, mtime)
        self.assertEqual(self.agents.read_text(), agents)

        p = self.update("--dry-run")
        self.assertIn("落后 2 个提交", p.stdout)
        self.assertIn("flow/routing.md 变了", p.stdout)
        self.assertIn("去掉 --dry-run 会快进并重跑 install（装对的项跳过）", p.stdout)
        self.assertEqual(self.head(self.clone), head)

    def test_help_mentions_fetch(self) -> None:
        p = subprocess.run([str(PKG / "bin" / "drover"), "update", "--help"], env=self.env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        flat = " ".join(p.stdout.split())  # argparse 按终端宽度折行
        self.assertIn("会联网 git fetch", flat)
        self.assertIn("--json", flat)
        self.assertIn("--auto", flat)


# ---------------------------------------------------------------- --auto：后台的自动更新

# 假 ssh：记下参数和 GIT_TERMINAL_PROMPT，然后按 FAKE_SSH_MODE 报"连不上"退出，或者关掉 stderr 一直挂着（模拟网络不通时
# 卡在建连接上；关掉 stderr 是为了 git 被杀后它不再占着 drover 读的管道）。
FAKE_SSH = r'''#!{python}
import json, os, sys, time
with open({log!r}, "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "prompt": os.environ.get("GIT_TERMINAL_PROMPT")}) + "\n")
if os.environ.get("FAKE_SSH_MODE") == "hang":
    os.close(2)
    time.sleep(30)
sys.stderr.write("ssh: connect to host git.fake.invalid port 22: Operation timed out\n")
sys.exit(255)
'''


class UpdateAutoTests(UpdateTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.env.pop("DROVER_AUTO_UPDATE", None)
        self.record_file = self.state_home / "auto-update.json"

    def auto(self, rc: int = 0) -> subprocess.CompletedProcess:
        return self.update("--auto", rc=rc)

    def record(self) -> dict:
        return json.loads(self.record_file.read_text()) if self.record_file.exists() else {}

    def fake_ssh(self) -> Path:
        log = self.tmp / "ssh-calls.log"
        ssh = self.fakebin / "ssh"
        ssh.write_text(FAKE_SSH.replace("{python}", sys.executable).replace("{log!r}", repr(str(log))))
        ssh.chmod(0o755)
        self.git(self.clone, "remote", "set-url", "origin", "ssh://git.fake.invalid/drover.git")
        return log

    @staticmethod
    def ssh_calls(log: Path) -> list:
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_fast_forward_records_update(self) -> None:
        """有新提交：快进、重跑 install（区块刷新），auto-update.json：result updated、update 的 from / to / count / titles /
        routing_changed / flow_changed、announced: false、last_attempt / finished；日志第一行写结果"""
        self.clone_install("--host", "claude")
        old = self.head(self.clone)
        new = self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        started = time.time()
        p = self.auto()
        self.assertRegex(p.stdout.splitlines()[0], r"^\[\d\d-\d\d \d\d:\d\d:\d\d\] drover update --auto：updated$")
        self.assertEqual(self.head(self.clone), new)
        self.assertIn(MARK, self.agents.read_text())
        rec = self.record()
        self.assertEqual(rec["result"], "updated")
        self.assertGreaterEqual(rec["last_attempt"], started - 1)
        self.assertGreaterEqual(rec["finished"], rec["last_attempt"])
        self.assertNotIn("error", rec)
        upd = rec["update"]
        self.assertEqual({k: upd[k] for k in ("from", "to", "count", "titles", "routing_changed", "flow_changed",
                                              "announced")},
                         {"from": old, "to": new, "count": 1, "titles": ["routing：加一条规则"], "routing_changed": True,
                          "flow_changed": False, "announced": False})

        # 主会话还没知道又来一次：并成一次（from 留第一次的），routing_changed 取或
        newer = self.push_change("flow/dispatch.md", "\n<!-- auto 测试改了流程 -->\n", "dispatch：改流程")
        self.auto()
        upd = self.record()["update"]
        self.assertEqual((upd["from"], upd["to"], upd["count"], upd["titles"], upd["routing_changed"],
                          upd["flow_changed"]),
                         (old, newer, 2, ["dispatch：改流程", "routing：加一条规则"], True, True))

    def test_up_to_date_skips_install(self) -> None:
        """没有新提交：result up_to_date，不重跑 install（安装记录 mtime 不变），没有 update 记录"""
        self.clone_install("--host", "claude")
        mtime = self.state.stat().st_mtime_ns
        p = self.auto()
        self.assertIn("up_to_date", p.stdout.splitlines()[0])
        self.assertIn("没重跑 install", p.stdout)
        rec = self.record()
        self.assertEqual(rec["result"], "up_to_date")
        self.assertNotIn("update", rec)
        self.assertEqual(self.state.stat().st_mtime_ns, mtime)

    def board_and_events(self) -> tuple:
        """装着的那份在进程里看：(看板第一行下面 drover 自己的那几行, watch --main 这一轮会报的 drover 事件)。只算不记。"""
        d = self.load_cli()
        events: list = []
        d.drover_take_events(self.record(), time.time(), events)
        return d.drover_board(time.time())[1], events

    def test_install_failure_retried_until_fixed(self) -> None:
        """快进以后 install 失败（AGENTS.md 里区块标记不成对，路由区块那一项装不上）：result install_failed、error 写失败项，
        update 记着 from→to；看板写"装失败"不写"已自动更新"，watch --main 报 update-blocked 不报 updated。下一次检查（没有
        新提交）照样重跑 install（安装记录里装好的提交还是旧的），还失败就接着留着 error。修好以后再查：重跑 install 装好，
        result up_to_date、error 清掉，update 还是 from→to、没报过、routing_changed 是这次装上的，这时看板写"已自动更新"、
        watch --main 报 updated(…) rules。再查一次就不重跑 install 了"""
        self.clone_install("--host", "claude")
        old = self.head(self.clone)
        new = self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        good = self.agents.read_text()
        self.agents.write_text(good + BEGIN + "\n多出来的开头标记\n")
        self.auto(rc=1)
        rec = self.record()
        self.assertEqual(rec["result"], "install_failed", dump(rec))
        self.assertIn("主工作台路由区块", rec["error"])
        self.assertEqual((rec["update"]["from"], rec["update"]["to"], rec["update"]["announced"]), (old, new, False))
        self.assertEqual(self.head(self.clone), new)
        notes, events = self.board_and_events()
        self.assertEqual(notes, [f"drover 自动更新装失败（下次自动检查重试）：{rec['error']}"])
        self.assertEqual(events, ["update-blocked"])

        p = self.auto(rc=1)  # 没有新提交，install 还是失败：照样重跑，error 留着
        self.assertNotIn("没重跑 install", p.stdout)
        self.assertIn("上次没装好，重跑了 install", p.stdout)
        rec = self.record()
        self.assertEqual(rec["result"], "install_failed", dump(rec))
        self.assertIn("主工作台路由区块", rec["error"])

        self.agents.write_text(good)
        self.auto()
        rec = self.record()
        self.assertEqual(rec["result"], "up_to_date", dump(rec))
        self.assertNotIn("error", rec)
        self.assertIn(MARK, self.agents.read_text())
        upd = rec["update"]
        self.assertEqual((upd["from"], upd["to"], upd["count"], upd["announced"], upd["routing_changed"]),
                         (old, new, 1, False, True))
        notes, events = self.board_and_events()
        stamp = time.strftime("%m-%d %H:%M", time.localtime(upd["at"]))
        self.assertEqual(notes, [f"drover 已自动更新 {old[:7]}→{new[:7]}（{stamp}）"])
        self.assertEqual(events, [f"updated({old[:7]}..{new[:7]}) rules"])

        mtime = self.state.stat().st_mtime_ns
        self.assertIn("没重跑 install", self.auto().stdout)
        self.assertEqual(self.state.stat().st_mtime_ns, mtime)

    def test_dirty_and_diverged_blocked(self) -> None:
        """有已跟踪改动又落后 / 和上游分叉：result blocked、blocked_status、error 说原因，HEAD、AGENTS.md、安装记录都不动"""
        self.clone_install("--host", "claude")
        self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        readme = self.clone / "README.md"
        original = readme.read_text()
        readme.write_text(original + "\n本地改动，没提交\n")
        head, mtime, agents = self.head(self.clone), self.state.stat().st_mtime_ns, self.agents.read_text()
        self.auto(rc=1)
        rec = self.record()
        self.assertEqual((rec["result"], rec["blocked_status"]), ("blocked", "dirty"))
        self.assertIn("本地改动", rec["error"])
        self.assertEqual((self.head(self.clone), self.state.stat().st_mtime_ns, self.agents.read_text()),
                         (head, mtime, agents))

        readme.write_text(original + "\n本地提交\n")
        self.git(self.clone, "commit", "-q", "-am", "本地提交，和上游分叉")
        head = self.head(self.clone)
        self.auto(rc=1)
        rec = self.record()
        self.assertEqual((rec["result"], rec["blocked_status"]), ("blocked", "diverged"))
        self.assertIn("分叉", rec["error"])
        self.assertEqual((self.head(self.clone), self.state.stat().st_mtime_ns, self.agents.read_text()),
                         (head, mtime, agents))

    def test_unreachable_changes_nothing(self) -> None:
        """远端没了（连不上）：result unreachable、unreachable_since、error 说连不上；clone、安装、AGENTS.md 什么都没改。
        再连不上一次，unreachable_since 不变；连上了就清掉"""
        self.clone_install("--host", "claude")
        moved = self.tmp / "remote-away.git"
        os.rename(self.remote, moved)
        watched = (self.ws, self.claude, self.state, self.config, self.pkg_dir, self.bin)
        before, head = self.snapshot_of(*watched), self.head(self.clone)
        self.auto(rc=1)
        rec = self.record()
        self.assertEqual(rec["result"], "unreachable")
        self.assertIn("连不上远端", rec["error"])
        self.assertEqual((self.snapshot_of(*watched), self.head(self.clone)), (before, head))
        since = rec["unreachable_since"]
        self.auto(rc=1)
        self.assertEqual(self.record()["unreachable_since"], since)
        os.rename(moved, self.remote)
        self.auto()
        self.assertNotIn("unreachable_since", self.record())

    def test_unattended_ssh_one_try(self) -> None:
        """无人值守：ssh 带 BatchMode=yes 和 ConnectTimeout、GIT_TERMINAL_PROMPT=0，报"Operation timed out"（偶发错误的
        样子）也只试 1 次；用户自己配了 core.sshCommand 的不覆盖"""
        self.clone_install("--host", "claude")
        log = self.fake_ssh()
        self.auto(rc=1)
        calls = self.ssh_calls(log)
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("BatchMode=yes", calls[0]["argv"])
        self.assertTrue(any(a.startswith("ConnectTimeout=") for a in calls[0]["argv"]), calls[0]["argv"])
        self.assertEqual(calls[0]["prompt"], "0")
        self.assertEqual(self.record()["result"], "unreachable")

        self.git(self.clone, "config", "core.sshCommand", "ssh -o UserKnob=yes")
        self.auto(rc=1)
        argv = self.ssh_calls(log)[-1]["argv"]
        self.assertIn("UserKnob=yes", argv)
        self.assertNotIn("BatchMode=yes", argv)

    def test_hanging_fetch_times_out(self) -> None:
        """连接一直挂着（网络不通）：fetch 到超时就放弃、记 unreachable，只试 1 次。在进程里跑，把 60 秒的超时换成 2 秒。
        只断言和机器快慢无关的结果：报错里的"命令超时（2 秒）"只有 fetch 被这个超时掐掉时才有（超时值就在里面），说明用的是
        无人值守的超时、到点就放弃。不数假 ssh 被调了几次、不量墙钟：机器忙时 2 秒内 git 可能还没起到假 ssh（实测负载约
        200 时 16 次里有 2 次假 ssh 一次都没被调到），fetch 照样是挂着被掐掉的"""
        self.clone_install("--host", "claude")
        self.fake_ssh()
        self.env["FAKE_SSH_MODE"] = "hang"
        d = self.load_cli()
        d.AUTO_UPDATE_NET_TIMEOUT = 2
        rc, out = self.run_update(d, auto=True)
        self.assertEqual(rc, 1, out)
        rec = self.record()
        self.assertEqual(rec["result"], "unreachable")
        self.assertIn("命令超时（2 秒）", rec["error"])
        self.assertIn("试了 1 次", rec["error"])

    def test_disabled_and_locked(self) -> None:
        """DROVER_AUTO_UPDATE=0：不 fetch（远端跟踪分支不动），记 result disabled，退出码 0；别的 update 拿着机器级的锁：
        等满上限（AUTO_UPDATE_LOCK_WAIT）还拿不到 → 这次不跑、不写记录。等锁这段在进程里跑，上限从 5 秒改成 0.3 秒"""
        self.clone_install("--host", "claude")
        self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        tracking = self.git(self.clone, "rev-parse", "origin/main")
        self.env["DROVER_AUTO_UPDATE"] = "0"
        self.assertIn("自动更新关着", self.auto().stdout)
        self.assertEqual(self.git(self.clone, "rev-parse", "origin/main"), tracking)
        self.assertEqual(self.record()["result"], "disabled")
        self.assertIn("DROVER_AUTO_UPDATE=0", self.record()["error"])

        self.env.pop("DROVER_AUTO_UPDATE")
        self.record_file.unlink()
        lock = self.state_home / "locks" / "update.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        d = self.load_cli()
        d.AUTO_UPDATE_LOCK_WAIT = 0.3
        with open(lock, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            rc, out = self.run_update(d, auto=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("另一个 drover update 正在跑", out)
        self.assertFalse(self.record_file.exists())
        self.assertEqual(self.git(self.clone, "rev-parse", "origin/main"), tracking)


# ---------------------------------------------------------------- 手动 update 也记更新


class UpdateManualRecordTests(UpdateTestBase):
    """手动 drover update 真的换了版本也写 auto-update.json 的 update（以前只有自动更新写，看板第二行
    还写着上一次自动更新、跟不上手动的）：source manual；跑的人看到输出了，announced 记 true；之前有一次自动更新还没报给主会话的
    并成一次、announced 保持 false（watch --main 照样报）。result / last_attempt / finished / error 说的是最近一次自动检查，不动。"""

    def setUp(self) -> None:
        super().setUp()
        self.env.pop("DROVER_AUTO_UPDATE", None)
        self.record_file = self.state_home / "auto-update.json"

    def record(self) -> dict:
        return json.loads(self.record_file.read_text()) if self.record_file.exists() else {}

    def test_manual_update_records(self) -> None:
        """换了版本：update 是 from / to / count / titles / routing_changed / flow_changed / at、source manual、announced true 和
        announced_at；别的键（上一次自动检查的 result、blocked_status、error、last_attempt、finished）原样。之后没有新提交再跑、
        --dry-run 有要拉的、被挡住（本地改动）、update_run 抛错（没装过之类）都不写；上一次已经报过的不并：--json 再更新一次，
        from 是上一次的 to"""
        self.clone_install("--host", "claude")
        auto = {"last_attempt": 1000.0, "finished": 1001.0, "result": "blocked", "blocked_status": "dirty",
                "error": "clone 里有已跟踪文件的本地改动（README.md）"}
        self.record_file.write_text(json.dumps(auto))
        old = self.head(self.clone)
        new = self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        started = time.time()
        self.assertTrue(self.update().stdout.startswith(f"drover 已更新：{old[:7]}→{new[:7]}"))
        rec = self.record()
        upd = rec.pop("update")
        self.assertEqual(rec, auto)
        self.assertEqual({k: v for k, v in upd.items() if k not in ("at", "announced_at")},
                         {"from": old, "to": new, "from_tag": None, "to_tag": None, "count": 1,
                          "titles": ["routing：加一条规则"], "routing_changed": True,
                          "flow_changed": False, "announced": True, "source": "manual"})
        self.assertGreaterEqual(upd["at"], started)
        self.assertGreaterEqual(upd["announced_at"], started)
        saved = self.record_file.read_text()

        self.assertTrue(self.update().stdout.startswith("drover 已是最新"))  # 没有新提交
        newer = self.push_change("flow/dispatch.md", "\n<!-- 手动更新测试改了流程 -->\n", "dispatch：改流程")
        self.update("--dry-run")
        readme = self.clone / "README.md"
        original = readme.read_text()
        readme.write_text(original + "\n本地改动，没提交\n")
        self.update(rc=1)  # 被挡住：没快进
        readme.write_text(original)
        d = self.load_cli()

        def broken(a):
            raise d.DroverError("update_run 抛错")

        d.update_run = broken
        self.assertEqual(self.run_update(d)[0], 1)
        self.assertEqual(self.record_file.read_text(), saved)

        out = self.update_json()  # 上一次已经报过：不并，from 是上一次的 to
        self.assertEqual((out["before"], out["after"]), (new, newer))
        upd = self.record()["update"]
        self.assertEqual((upd["from"], upd["to"], upd["count"], upd["titles"], upd["flow_changed"], upd["announced"],
                          upd["source"]), (new, newer, 1, ["dispatch：改流程"], True, True, "manual"))

    def test_merges_unannounced_auto_update(self) -> None:
        """后台自动更新了一次（source auto、announced false），主会话还没知道，又手动更新：并成一次——from 留自动那次的、提交数
        相加、标题新的在前、routing_changed 取或，announced 还是 false、没有 announced_at（watch --main 照样报），source 是这次的
        manual；自动检查记的 result / last_attempt / finished 不动"""
        self.clone_install("--host", "claude")
        old = self.head(self.clone)
        self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        self.update("--auto")
        rec = self.record()
        self.assertEqual((rec["result"], rec["update"]["source"], rec["update"]["announced"]), ("updated", "auto", False))
        auto = {k: rec[k] for k in ("result", "last_attempt", "finished")}
        newer = self.push_change("flow/dispatch.md", "\n<!-- 手动更新测试改了流程 -->\n", "dispatch：改流程")
        self.update()
        rec = self.record()
        upd = rec["update"]
        self.assertEqual({k: rec[k] for k in auto}, auto)
        self.assertEqual((upd["from"], upd["to"], upd["count"], upd["titles"], upd["routing_changed"], upd["flow_changed"],
                          upd["announced"], upd["source"]),
                         (old, newer, 2, ["dispatch：改流程", "routing：加一条规则"], True, True, False, "manual"))
        self.assertNotIn("announced_at", upd)

    def board_lines(self) -> tuple:
        """装着的那份（~/.drover/pkg，copy 模式是副本）在进程里看：(第一行写的版本，drover_version；第二行起 drover 自己的那几行)。"""
        d = self.load_cli(self.pkg_dir / "bin" / "drover")
        return d.drover_version(), d.drover_board(time.time())[1]

    def test_copy_install_failure_then_rerun(self) -> None:
        """copy 模式：update 先快进 clone、再跑 install 复制副本。复制失败（clone 里有一个读不了的未跟踪文件，copytree 报错，旧副本
        不动）：clone 新了、副本标记还是旧的，跑着的版本没变 → 不记，看板第一行还是旧版本、第二行没有"已更新"（以前按 clone 的
        HEAD 会记成 a→b，和第一行对不上）。去掉那个文件再手动 update：clone 的 before == after、副本换成新的 → 记 from 旧副本版本、
        to 新版本，提交和标题按这一段现算（res 里是 0 条），flow_changed 按这一段改了的文件；看板第一行、第二行对得上"""
        self.clone_install("--host", "claude", "--mode", "copy")
        old = self.head(self.clone)
        new = self.push_change("flow/dispatch.md", "\n<!-- 手动更新测试改了流程 -->\n", "dispatch：改流程")
        blocker = self.clone / "flow" / "unreadable-for-test.md"
        blocker.write_text("读不了\n")
        blocker.chmod(0)
        self.addCleanup(lambda: blocker.exists() and blocker.chmod(0o644))
        out = self.update_json(rc=1)
        self.assertEqual((out["before"], out["after"], out["install"]["ok"]), (old, new, False), dump(out))
        self.assertEqual(json.loads((self.pkg_dir / ".drover-copy").read_text())["commit"], old)
        self.assertFalse(self.record_file.exists())
        version, notes = self.board_lines()
        self.assertEqual((version, [x for x in notes if "已更新" in x]), (old, []))

        blocker.chmod(0o644)
        blocker.unlink()
        out = self.update_json()
        self.assertEqual((out["before"], out["after"], out["new_commits"]["count"]), (new, new, 0), dump(out))
        self.assertEqual(json.loads((self.pkg_dir / ".drover-copy").read_text())["commit"], new)
        upd = self.record()["update"]
        self.assertEqual((upd["from"], upd["to"], upd["count"], upd["titles"], upd["flow_changed"], upd["source"],
                          upd["announced"]), (old, new, 1, ["dispatch：改流程"], True, "manual", True))
        version, notes = self.board_lines()
        stamp = time.strftime("%m-%d %H:%M", time.localtime(upd["at"]))
        self.assertEqual((version, notes), (new, [f"drover 已更新 {old[:7]}→{new[:7]}（{stamp}，手动）"]))


# ---------------------------------------------------------------- 更新通道 release（默认）：只装发布的版本


class UpdateReleaseTests(UpdateTestBase):
    """config.json 里没有 update_channel 时按 release 走：fetch 上游时带上版本 tag，只快进到上游历史上最新的版本 tag（vX.Y.Z，
    按数字比大小；带后缀的预发布版、别的分支上的 tag 不算），上游上没打 tag 的新提交不装；一个能用的版本 tag 都没有时不更新、
    说清楚（no_release，走被挡住那条路）。本地分支停在 tag 上、落后上游是常态，不算落后；本地改动、分叉照旧被挡住。
    远端和 GitHub 上一样：初始提交（seed）就是 v0.1.0。"""
    channel = None

    def setUp(self) -> None:
        super().setUp()
        self.env.pop("DROVER_AUTO_UPDATE", None)
        self.record_file = self.state_home / "auto-update.json"
        self.release("v0.1.0")

    def release(self, tag: str) -> str:
        """作者发版：dev clone 的 HEAD 打附注 tag、推 tag。返回 tag 指向的提交。"""
        self.git(self.dev, "tag", "-a", tag, "-m", f"release {tag}")
        self.git(self.dev, "push", "-q", "origin", f"refs/tags/{tag}")
        return self.head(self.dev)

    def side_release(self, tag: str) -> None:
        """在别的分支（side，从 main 当前的提交分出去）上加一个提交、打 tag 推上去，再切回 main：这个 tag 不在 origin/main 的历史上。"""
        self.git(self.dev, "switch", "-q", "-c", "side")
        self.push_change("README.md", "\n侧分支上的改动\n", "side：别的分支", branch="side")
        self.release(tag)
        self.git(self.dev, "switch", "-q", "main")

    def record(self) -> dict:
        return json.loads(self.record_file.read_text()) if self.record_file.exists() else {}

    def tracking(self) -> str:
        return self.git(self.clone, "rev-parse", "origin/main")

    def test_fast_forwards_to_latest_release_tag(self) -> None:
        """选 tag：v0.9.0、v0.10.0（按数字比，v0.10.0 新）、之后没打 tag 的提交上的 v0.11.0-rc1（预发布版不算）、别的分支上的
        v9.9.9（不在上游历史上）→ 装 v0.10.0。--dry-run 报目标 tag 和落后的提交，当前版本写版本号 v0.1.0；更新后 clone 停在 v0.10.0，
        origin/main 是更新的提交；再跑是已是最新（不因为落后 origin/main 误报）；输出、看板第二行的两头都写版本号（v0.1.0→v0.10.0），
        记录里照旧存完整 sha 和两头的 tag"""
        self.clone_install("--host", "claude")
        old = self.head(self.clone)
        self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        self.release("v0.9.0")
        self.push_change("README.md", "\n第 2 条\n", "readme：第 2 条")
        v010 = self.release("v0.10.0")
        self.side_release("v9.9.9")
        latest = self.push_change("README.md", "\n还没发版\n", "readme：还没发版")
        self.release("v0.11.0-rc1")

        out = self.update_json("--dry-run")
        self.assertTrue(out["ok"], dump(out))
        self.assertEqual((out["channel"], out["release"], out["status"], out["behind"]), ("release", "v0.10.0", "behind", 2))
        self.assertEqual((out["target_sha"], out["upstream_sha"]), (v010, latest))
        self.assertEqual(out["upstream_commits"], {"count": 2, "titles": ["readme：第 2 条", "routing：加一条规则"]})
        dry = self.update("--dry-run").stdout
        self.assertIn("当前 v0.1.0，", dry)
        self.assertIn("更新通道 release：跟上游上最新的版本 tag（v0.10.0）", dry)
        self.assertIn("落后 2 个提交（到 v0.10.0）：", dry)
        self.assertEqual(self.head(self.clone), old)

        lines = self.update().stdout.splitlines()
        self.assertEqual(lines[0], "drover 已更新：v0.1.0→v0.10.0（2 个新提交）")
        self.assertEqual((self.head(self.clone), self.tracking()), (v010, latest))
        self.assertIn(MARK, self.agents.read_text())
        upd = self.record()["update"]
        self.assertEqual((upd["from"], upd["to"], upd["from_tag"], upd["to_tag"], upd["count"]),
                         (old, v010, "v0.1.0", "v0.10.0", 2))
        d = self.load_cli(self.pkg_dir / "bin" / "drover")
        stamp = time.strftime("%m-%d %H:%M", time.localtime(upd["at"]))
        self.assertEqual(d.drover_board(time.time())[1], [f"drover 已更新 v0.1.0→v0.10.0（{stamp}，手动）"])

        out = self.update_json()
        self.assertTrue(out["ok"], dump(out))
        self.assertEqual((out["status"], out["before"], out["after"], out["to_tag"]), ("up_to_date", v010, v010, "v0.10.0"))
        self.assertEqual(self.update().stdout.splitlines()[0], "drover 已是最新（v0.10.0）")
        self.assertEqual(self.head(self.clone), v010)

    def test_auto_untagged_commits_not_installed(self) -> None:
        """--auto：上游上没打 tag 的新提交不装（up_to_date，日志写当前版本号 v0.1.0；HEAD、AGENTS.md、安装记录都不动，没有 update
        记录）；作者打了 tag 之后才装，记录的 from_tag / to_tag 是 v0.1.0 / v0.2.0，watch --main 报 updated(v0.1.0..v0.2.0) rules"""
        self.clone_install("--host", "claude")
        old, mtime = self.head(self.clone), self.state.stat().st_mtime_ns
        new = self.push_change("flow/routing.md", MARK_LINE, "routing：还没发版")
        p = self.update("--auto")
        self.assertIn("up_to_date（v0.1.0，没有要装的新版本，没重跑 install）", p.stdout.splitlines()[0])
        self.assertEqual((self.head(self.clone), self.tracking()), (old, new))
        self.assertNotIn(MARK, self.agents.read_text())
        self.assertEqual(self.state.stat().st_mtime_ns, mtime)
        rec = self.record()
        self.assertEqual(rec["result"], "up_to_date")
        self.assertNotIn("update", rec)

        self.release("v0.2.0")
        p = self.update("--auto")
        self.assertIn("drover 已更新：v0.1.0→v0.2.0（1 个新提交）", p.stdout.splitlines())
        self.assertEqual(self.head(self.clone), new)
        self.assertIn(MARK, self.agents.read_text())
        rec = self.record()
        self.assertEqual(rec["result"], "updated")
        upd = rec["update"]
        self.assertEqual((upd["from"], upd["to"], upd["from_tag"], upd["to_tag"], upd["count"], upd["announced"]),
                         (old, new, "v0.1.0", "v0.2.0", 1, False))
        d = self.load_cli()
        self.assertEqual(d.drover_main_events(time.time()), ["updated(v0.1.0..v0.2.0) rules"])

    def test_no_release_tag(self) -> None:
        """一个能用的版本 tag 都没有（远端删了 v0.1.0；只有别的分支上的 v9.9.9）：no_release，不快进、不跑 install，说清楚
        还没有发布的版本、要跟分支最新怎么配；--auto 记 blocked / no_release"""
        self.git(self.dev, "push", "-q", "origin", ":refs/tags/v0.1.0")
        self.side_release("v9.9.9")
        self.clone_install("--host", "claude")
        self.push_change("flow/routing.md", MARK_LINE, "routing：没有发版")
        head, mtime, agents = self.head(self.clone), self.state.stat().st_mtime_ns, self.agents.read_text()
        out = self.update_json(rc=1)
        self.assertEqual((out["status"], out["release"], out["channel"]), ("no_release", None, "release"))
        self.assertIn("origin/main 上还没有发布的版本", out["error"])
        self.assertIn('"update_channel": "main"', out["error"])
        self.assertNotIn("install", out)
        self.assertIn("没重跑 install", self.update(rc=1).stdout.splitlines()[0])
        self.update("--auto", rc=1)
        rec = self.record()
        self.assertEqual((rec["result"], rec["blocked_status"]), ("blocked", "no_release"))
        self.assertIn("还没有发布的版本", rec["error"])
        self.assertEqual((self.head(self.clone), self.state.stat().st_mtime_ns, self.agents.read_text()),
                         (head, mtime, agents))

    def test_dirty_diverged_blocked_and_ahead_kept(self) -> None:
        """有已跟踪改动又落后 tag / 和 tag 分叉：照旧被挡住（blocked_status dirty / diverged，原因里说落后的是 v0.2.0），什么都不改。
        clone 比最新的 tag 还新（以前跟过 main）：ahead，不回退，说比最新版本新几个提交"""
        self.clone_install("--host", "claude")
        self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        self.release("v0.2.0")
        readme = self.clone / "README.md"
        readme.write_text(readme.read_text() + "\n本地改动，没提交\n")
        head, mtime, agents = self.head(self.clone), self.state.stat().st_mtime_ns, self.agents.read_text()
        self.update("--auto", rc=1)
        rec = self.record()
        self.assertEqual((rec["result"], rec["blocked_status"]), ("blocked", "dirty"))
        self.assertIn("又落后 v0.2.0 1 个提交", rec["error"])
        self.assertEqual((self.head(self.clone), self.state.stat().st_mtime_ns, self.agents.read_text()),
                         (head, mtime, agents))

        self.git(self.clone, "commit", "-q", "-am", "本地提交，和 v0.2.0 分叉")
        head = self.head(self.clone)
        out = self.update_json(rc=1)
        self.assertEqual(out["status"], "diverged")
        self.assertIn("clone 和 v0.2.0 分叉了", out["error"])
        self.assertEqual((self.head(self.clone), self.state.stat().st_mtime_ns), (head, mtime))

        newest = self.push_change("README.md", "\n还没发版\n", "readme：还没发版")
        self.git(self.clone, "fetch", "-q", "origin")
        self.git(self.clone, "reset", "-q", "--hard", "origin/main")
        out = self.update_json()
        self.assertTrue(out["ok"], dump(out))
        self.assertEqual((out["status"], out["ahead"], out["after"], out["to_tag"]), ("ahead", 1, newest, None))
        first = self.update().stdout.splitlines()[0]
        self.assertEqual(first, "drover 已是最新（v0.2.0+1）；本地比最新版本 v0.2.0 新 1 个提交，不回退")
        self.assertEqual(self.head(self.clone), newest)

    def test_main_channel_follows_branch(self) -> None:
        """config.json 写 update_channel: main（作者本机）：和加通道之前一样跟上游最新，没打 tag 的提交照样装。fetch 多给一条有目的端
        的分支 refspec，git 自动跟进上游历史上的 tag：远端新打的 v0.2.0 进了 clone（--dry-run 那次 fetch 就带回来了）；不强制拉
        tag，作者本地改过、还没推的同名 tag（v0.1.0 换成本地的附注 tag）不被远端的盖掉，update 照常成功。输出两头写版本号：
        --dry-run 的当前 v0.1.0，已更新 v0.1.0→v0.2.0+1，再跑已是最新（v0.2.0+1）"""
        self.clone_install("--host", "claude")
        self.write_json(self.config, {**self.load(self.config), "update_channel": "main"})
        self.git(self.clone, "tag", "-a", "v0.1.0", "-m", "作者本地改过、还没推")
        local = self.git(self.clone, "rev-parse", "refs/tags/v0.1.0")
        self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        v020 = self.release("v0.2.0")
        new = self.push_change("README.md", "\n还没发版\n", "readme：还没发版")
        self.assertEqual(self.git(self.clone, "tag", "--list"), "v0.1.0")

        dry = self.update("--dry-run").stdout
        self.assertIn("当前 v0.1.0，", dry)
        self.assertIn("落后 2 个提交：", dry)
        self.assertEqual(self.git(self.clone, "tag", "--list").splitlines(), ["v0.1.0", "v0.2.0"])
        lines = self.update().stdout.splitlines()
        self.assertEqual(lines[0], "drover 已更新：v0.1.0→v0.2.0+1（2 个新提交）")
        self.assertEqual(self.head(self.clone), new)
        self.assertIn(MARK, self.agents.read_text())
        self.assertEqual(self.git(self.clone, "rev-parse", "v0.2.0^{commit}"), v020)
        self.assertEqual(self.git(self.clone, "rev-parse", "refs/tags/v0.1.0"), local)  # 本地改过的没被盖掉
        self.assertNotEqual(self.git(self.dev, "rev-parse", "refs/tags/v0.1.0"), local)
        out = self.update_json()
        self.assertTrue(out["ok"], dump(out))
        self.assertEqual((out["channel"], out["target"], out["status"], out["after"], out["release"], out["to_tag"]),
                         ("main", "origin/main", "up_to_date", new, None, None))
        self.assertEqual(self.update().stdout.splitlines()[0], "drover 已是最新（v0.2.0+1）")

    def test_unknown_channel_changes_nothing(self) -> None:
        """update_channel 写了不认识的值：报错（说只能是 release / main），不 fetch、不快进；--auto 记 failed"""
        self.clone_install("--host", "claude")
        self.write_json(self.config, {**self.load(self.config), "update_channel": "stable"})
        self.push_change("flow/routing.md", MARK_LINE, "routing：加一条规则")
        self.release("v0.2.0")
        tracking, head = self.tracking(), self.head(self.clone)
        p = self.update(rc=1)
        self.assertTrue(p.stdout.startswith('drover 没更新：~/.drover/config.json 里的 "update_channel" 是 "stable"'),
                        p.stdout)
        self.assertIn("只能是 release", p.stdout)
        self.update("--auto", rc=1)
        rec = self.record()
        self.assertEqual(rec["result"], "failed")
        self.assertIn("update_channel", rec["error"])
        self.assertEqual((self.tracking(), self.head(self.clone)), (tracking, head))


if __name__ == "__main__":
    unittest.main(verbosity=2)
