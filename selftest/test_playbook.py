#!/usr/bin/env python3
"""drover playbook 和 brief 编码规范的自测（标准库 unittest，自包含）。

跑法：python3 -m unittest discover -s selftest -p test_playbook.py -v

每个用例一个临时目录：DROVER_STATE_DIR（config.json 放这里）、HOME（~/.claude/CLAUDE.md 由用例控制）、
仓库目录（git init 的小仓库）、团队 / 个人 profile 目录。用 subprocess 跑本 worktree 的 bin/drover。
不碰真实 ~/.drover 和真实 HOME；PATH 最前面是假 herdr（selftest_fakes，去掉了 HERDR_ 开头的环境变量）：brief 用
herdr agent get 查收任务书的 agent 的宿主，不碰真 herdr；playbook 不调用 herdr。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import selftest_fakes

PKG = Path(__file__).resolve().parent.parent
DROVER = PKG / "bin" / "drover"
DEFAULT_RULES = PKG / "flow" / "coding-rules.md"
DEFAULT_HEADING = "## 编码规范（drover 默认；必须遵守，仓库规范冲突时按仓库的）\n"
UB_MODE = PKG / "third_party" / "ub-mode"
# unclebob/ub-mode 提交 3da62bf3fcfd2034de41f30d920d2739e566171f 里这两个文件的 sha256（取来的原文，一字未改）
UB_MODE_SHA256 = {
    "SKILL.md": "4120873db2e3681ea9b7de4d7105fc9d3406657c9ada91ce77b9b3c4962f3202",
    "LICENSE": "cf496364b10d049ba2b04ac4c04f29ab1a34b54be96209cb32666fac9eba6bf3",
}
# ub-mode SKILL.md 的各节：前两组收进通用原则（节名后括号里留英文原名），Practice 一组不收
UB_MODE_KEPT = ["Laziness Protocol", "Foundational Thinking", "TDD", "Redesign From First Principles", "Attack the Premise",
                "Subtract Before You Add", "Outcome-Oriented Execution", "Build the Lever", "Architecture",
                "Model the Domain", "Boundary Discipline", "Type System Discipline", "Make Operations Idempotent",
                "Separate Before Serializing Shared State", "Fix Root Causes", "Sequence work into verifiable units",
                "Test Behavior, Not Implementation", "Crapper", "Dryer", "Mutator", "Refactoring"]
UB_MODE_DROPPED = ["Keep a Log", "Guard the Context Window", "Worktree Cleanup", "Commit and Push"]
POM = "<project><artifactId>app</artifactId></project>\n"
GIT_ID = ["-c", "user.name=drover-selftest", "-c", "user.email=selftest@example.invalid"]
TASK = "### 背景\n测试用。\n\n### 要做的\n什么都不做。\n\n### 不做\n不改任何文件。\n"
USER_RULES = "# 全局\n\n## 编码规范（所有项目，强制）\n\n- USER-RULE-MARKER：用户全局规则\n\n## 其他\n\n- 不相关\n"


class PlaybookTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="drover-playbook-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = self.tmp / "state"
        self.home = self.tmp / "home"
        self.repos = self.tmp / "repos"
        self.fakebin = self.tmp / "fakebin"
        for d in (self.state, self.home, self.repos):
            d.mkdir()
        selftest_fakes.install(self.fakebin, sys.executable)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("DROVER_STATE_DIR", "CODEX_HOME", "GIT_DIR") and not k.startswith("HERDR_")}
        self.env.update(HOME=str(self.home), DROVER_STATE_DIR=str(self.state), PYTHONDONTWRITEBYTECODE="1",
                        PATH=os.pathsep.join([str(self.fakebin), os.environ.get("PATH", "")]))

    # ---- 工具

    def set_herdr(self, state: dict) -> None:
        """假 herdr 的状态（selftest_fakes 的格式：agents 按名字、panes 按 pane ID）。"""
        self.write(self.fakebin / "herdr-state.json", json.dumps(state))

    def git(self, *args: str) -> None:
        subprocess.run(["git", *GIT_ID, *args], env=self.env, check=True, capture_output=True, text=True)

    def make_repo(self, name: str, files: dict = None) -> Path:
        """files：{相对路径: 内容}，写进去并提交（被 git 跟踪）。"""
        path = self.repos / name
        self.git("init", "-q", str(path))
        for rel, text in (files or {}).items():
            self.write(path / rel, text)
        self.git("-C", str(path), "add", "-A")
        self.git("-C", str(path), "commit", "-q", "--allow-empty", "-m", "init")
        return path

    def write(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def write_config(self, **extra) -> None:
        cfg = {"workspace": str(self.repos), "repo_dirs": [str(self.repos)], **extra}
        self.write(self.state / "config.json", json.dumps(cfg, ensure_ascii=False))

    def make_profile(self, name: str, general: str = None, coding: str = None, repos: dict = None) -> Path:
        root = self.tmp / name
        root.mkdir()
        if general is not None:
            self.write(root / "general.md", general)
        if coding is not None:
            self.write(root / "coding-rules.md", coding)
        for repo, text in (repos or {}).items():
            self.write(root / "repos" / f"{repo}.md", text)
        return root

    def drover(self, *args: str, rc: int = 0) -> subprocess.CompletedProcess:
        p = subprocess.run([sys.executable, str(DROVER), *args], env=self.env, cwd=str(self.tmp),
                           capture_output=True, text=True, timeout=120)
        if rc is not None and p.returncode != rc:
            self.fail(f"drover {' '.join(args)} 退出码 {p.returncode}（期望 {rc}）\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
        return p

    def playbook(self, *args: str, rc: int = 0) -> dict:
        return json.loads(self.drover("playbook", *args, rc=rc).stdout)

    def brief(self, kind, repo: Path, *extra: str, task: str = TASK, rc: int = 0, dry_run: bool = True) -> dict:
        """kind 为 None 时不传 --kind。"""
        deps = self.write(self.tmp / "deps.json", json.dumps({
            "worktree_path": str(repo), "summary_for_prompt": "- 测试：JS 依赖已装好。", "items": [{"kind": "js"}]}))
        task_file = self.write(self.tmp / "task.md", task)
        out = json.loads(self.drover(
            "brief", "--slug", "selftest-playbook", "--name", "st-agent", *(["--kind", kind] if kind else []),
            "--repo", str(repo), "--wt", str(repo), "--deps-json", str(deps), "--task-file", str(task_file),
            "--goal", "自测目标", "--evidence", "自测证据", *(["--dry-run"] if dry_run else []), *extra, rc=rc).stdout)
        self.assertEqual(out["ok"], rc == 0, out)
        if dry_run:  # dry-run 不写文件
            self.assertFalse((self.state / "selftest-playbook" / "st-agent.prompt.md").exists())
            self.assertFalse((self.state / "selftest-playbook" / "dispatch.md").exists())
        return out


# ---------------------------------------------------------------- playbook


class PlaybookTests(PlaybookTestBase):
    def test_no_profile_configured(self) -> None:
        self.write_config()
        self.make_repo("alpha")
        out = self.playbook("--repo", "alpha")
        self.assertTrue(out["ok"])
        self.assertIsNone(out["profile"]["name"])
        self.assertIsNone(out["profile"]["dir"])
        self.assertEqual(out["files"], [])
        self.assertEqual(out["text"], "")
        self.assertEqual(out["available_repos"], [])
        self.assertIn("只有包默认规则", out["note"])
        p = self.drover("playbook", "--repo", "alpha", "--text")
        self.assertEqual(p.stdout, "")
        self.assertIn("只有包默认规则", p.stderr)

    def test_profile_by_name_uses_package_profile(self) -> None:
        self.write_config(profile="example")
        self.make_repo("example-app")
        out = self.playbook("--repo", "example-app")
        pkg_profile = PKG / "profiles" / "example"
        self.assertEqual(out["profile"]["name"], "example")
        self.assertEqual(Path(out["profile"]["dir"]), pkg_profile)
        self.assertTrue(out["profile"]["exists"])
        self.assertEqual([(Path(f["path"]), f["layer"], f["kind"], f["repo"]) for f in out["files"]], [
            (pkg_profile / "general.md", "profile", "general", None),
            (pkg_profile / "repos" / "example-app.md", "profile", "repo", "example-app"),
        ])
        self.assertTrue(out["repos"][0]["has_playbook"])
        self.assertIn("## 2. 迁移授权例外", out["text"])
        self.assertIn(f"=== 来源：{pkg_profile / 'general.md'}（团队 profile：example；所有仓库）===", out["text"])
        self.assertNotIn("<!--", out["text"])  # 给维护者的注释不进输出
        self.assertEqual(out["available_repos"], [{"layer": "profile", "name": "example", "repos": ["example-app"]}])
        self.assertEqual([Path(c) for c in out["coding_rules"]], [DEFAULT_RULES, pkg_profile / "coding-rules.md"])

    def test_default_coding_rules_listed_and_switch(self) -> None:
        # 包默认的编码规范（flow/coding-rules.md）：没写开关时 coding_rules 里排第一，default_coding_rules 标明它；
        # 开关 false 时不列，团队 profile 的照旧。files / text 不放它（编码规范由 brief 追加，不进 playbook 正文）
        team = self.make_profile("team", coding="- TEAM-RULE\n")
        self.make_repo("alpha")
        self.write_config(profile=str(team))
        out = self.playbook("--repo", "alpha")
        self.assertEqual([Path(c) for c in out["coding_rules"]], [DEFAULT_RULES, team / "coding-rules.md"])
        self.assertEqual(out["default_coding_rules"], {"enabled": True, "path": str(DEFAULT_RULES), "sections": ["general"]})
        self.assertEqual(out["files"], [])
        self.write_config(profile=str(team), default_coding_rules=True)
        self.assertEqual(self.playbook("--repo", "alpha")["coding_rules"], [str(DEFAULT_RULES), str(team / "coding-rules.md")])
        self.write_config(profile=str(team), default_coding_rules=False)
        out = self.playbook("--repo", "alpha")
        self.assertEqual([Path(c) for c in out["coding_rules"]], [team / "coding-rules.md"])
        self.assertEqual(out["default_coding_rules"], {"enabled": False, "path": str(DEFAULT_RULES), "sections": []})
        # 没配 profile 也列包默认的
        self.write_config()
        self.assertEqual(self.playbook("--repo", "alpha")["coding_rules"], [str(DEFAULT_RULES)])

    def test_default_rules_sections_by_repo(self) -> None:
        # default_coding_rules.sections：按这几个 --repo 算 brief 会带包默认编码规范的哪几节，给编排者验收用。
        # general 总带，按文件里的顺序（MySQL 在 Java 前）；任一仓库满足就带；不给 --repo 只有 general；开关关掉为空
        self.write_config()
        self.make_repo("plain")
        self.make_repo("javaapp", {"pom.xml": POM})
        self.make_repo("sqlapp", {"db/V1__init.sql": "CREATE TABLE t (id INT);\n"})
        self.make_repo("pyorm", {"pyproject.toml": '[project]\ndependencies = ["sqlalchemy>=2"]\n'})

        def sections(*names: str) -> list:
            return self.playbook(*[arg for n in names for arg in ("--repo", n)])["default_coding_rules"]["sections"]

        self.assertEqual(sections("plain"), ["general"])
        self.assertEqual(sections("javaapp"), ["general", "java"])
        self.assertEqual(sections("sqlapp"), ["general", "mysql"])
        self.assertEqual(sections("pyorm"), ["general"])
        self.assertEqual(sections("javaapp", "sqlapp"), ["general", "mysql", "java"])
        self.assertEqual(sections(), ["general"])
        self.write_config(default_coding_rules=False)
        self.assertEqual(sections("javaapp", "sqlapp"), [])


    def test_profile_by_path(self) -> None:
        team = self.make_profile("team-x", general="TEAM-GENERAL", repos={"alpha": "TEAM-ALPHA"})
        self.make_repo("alpha")
        self.write_config(profile=str(team))
        out = self.playbook("--repo", "alpha")
        self.assertEqual(out["profile"]["name"], "team-x")
        self.assertEqual(Path(out["profile"]["dir"]), team)
        self.assertIn("TEAM-GENERAL", out["text"])
        self.assertIn("TEAM-ALPHA", out["text"])
        # ~ 开头的写法按（临时）HOME 展开
        moved = self.home / "profiles" / "team-y"
        shutil.copytree(team, moved)
        self.write_config(profile="~/profiles/team-y")
        out = self.playbook("--repo", "alpha")
        self.assertEqual(Path(out["profile"]["dir"]), moved)
        self.assertEqual(out["profile"]["name"], "team-y")
        self.assertIn("TEAM-ALPHA", out["text"])

    def test_profile_dir_missing_is_error(self) -> None:
        self.make_repo("alpha")
        for value in ("no-such-team", "~/gone/profile"):
            self.write_config(profile=value)
            out = self.playbook("--repo", "alpha", rc=1)
            self.assertFalse(out["ok"])
            self.assertIn(value, out["error"])
            self.assertIn("不存在", out["error"])
            self.assertIn("install.py --profile", out["error"])
            self.assertIn("profile 写包里有的名字（example）", out["error"])  # 列出包里有的 profile
            self.assertFalse(out["profile"]["exists"])
            p = self.drover("playbook", "--repo", "alpha", "--text", rc=1)  # --text 也不能静默
            self.assertFalse(json.loads(p.stdout)["ok"])

    def test_repo_with_and_without_playbook(self) -> None:
        team = self.make_profile("team", general="TEAM-GENERAL", repos={"alpha": "ALPHA-PLAYBOOK", "gamma": "G"})
        alpha = self.make_repo("alpha")
        self.make_repo("beta")
        self.write_config(profile=str(team))
        out = self.playbook("--repo", "alpha", "--repo", "beta")
        self.assertEqual([(r["name"], r["has_playbook"]) for r in out["repos"]], [("alpha", True), ("beta", False)])
        self.assertEqual([(f["kind"], f["repo"]) for f in out["files"]], [("general", None), ("repo", "alpha")])
        self.assertIn("ALPHA-PLAYBOOK", out["text"])
        self.assertEqual(out["available_repos"][0]["repos"], ["alpha", "gamma"])
        # 只有没 playbook 的仓库：general.md 照样输出
        only_beta = self.playbook("--repo", "beta")
        self.assertEqual([f["kind"] for f in only_beta["files"]], ["general"])
        self.assertNotIn("ALPHA-PLAYBOOK", only_beta["text"])
        # worktree 路径按所属源仓库算
        wt = self.tmp / "wt-alpha"
        self.git("-C", str(alpha), "worktree", "add", "-q", "-b", "task/x", str(wt))
        via_wt = self.playbook("--repo", str(wt))
        self.assertEqual(via_wt["repos"][0]["name"], "alpha")
        self.assertIn("ALPHA-PLAYBOOK", via_wt["text"])
        # 各层都没有内容：text 为空并给 note
        empty = self.make_profile("empty")
        self.write_config(profile=str(empty))
        out = self.playbook("--repo", "alpha")
        self.assertEqual(out["text"], "")
        self.assertIn("available_repos", out["note"])

    def test_playbook_dirs_after_team(self) -> None:
        team = self.make_profile("team", general="TEAM-GENERAL", repos={"alpha": "TEAM-ALPHA"})
        mine = self.make_profile("mine", general="MINE-GENERAL", repos={"alpha": "MINE-ALPHA", "delta": "D"})
        self.make_repo("alpha")
        self.write_config(profile=str(team), playbook_dirs=[str(mine), str(self.tmp / "missing")])
        out = self.playbook("--repo", "alpha")
        self.assertEqual([(f["layer"], f["kind"]) for f in out["files"]],
                         [("profile", "general"), ("profile", "repo"), ("personal", "general"), ("personal", "repo")])
        text = out["text"]
        order = [text.index(m) for m in ("TEAM-GENERAL", "TEAM-ALPHA", "MINE-GENERAL", "MINE-ALPHA")]
        self.assertEqual(order, sorted(order))  # 团队在前，个人覆盖在后（后读的优先）
        self.assertIn(f"（个人覆盖：{mine}；仓库 alpha）", text)
        self.assertEqual([(a["layer"], a["repos"]) for a in out["available_repos"]],
                         [("profile", ["alpha"]), ("personal", ["alpha", "delta"])])
        self.assertEqual([d["exists"] for d in out["playbook_dirs"]], [True, False])
        self.assertTrue(any("missing" in w for w in out["warnings"]))  # 不存在的个人目录只提示，不报错


# ---------------------------------------------------------------- brief 的编码规范


class BriefCodingRulesTests(PlaybookTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.make_repo("alpha")
        self.write(self.home / ".claude" / "CLAUDE.md", USER_RULES)
        team = self.make_profile("team", coding="<!-- 维护说明 HIDDEN-COMMENT -->\n- TEAM-RULE-MARKER：团队规则\n")
        mine = self.make_profile("mine", coding="- MINE-RULE-MARKER：个人规则\n")
        self.write_config(profile=str(team), playbook_dirs=[str(mine)])

    def default_body(self, prompt: str) -> str:
        """任务书里"drover 默认"那一节的正文（到下一个 ## 小节为止）。"""
        start = prompt.index(DEFAULT_HEADING) + len(DEFAULT_HEADING)
        return prompt[start:prompt.index("\n\n## ", start)]

    def test_claude_gets_team_rules_but_not_claude_md(self) -> None:
        prompt = self.brief("claude", self.repo)["prompt"]
        self.assertIn("## 编码规范（团队 profile：team；必须遵守）\n- TEAM-RULE-MARKER", prompt)
        self.assertIn(f"## 编码规范（个人覆盖：{self.tmp / 'mine'}；必须遵守）\n- MINE-RULE-MARKER", prompt)
        self.assertLess(prompt.index("TEAM-RULE-MARKER"), prompt.index("MINE-RULE-MARKER"))
        self.assertNotIn("USER-RULE-MARKER", prompt)  # claude 自动加载 CLAUDE.md，不重复追加
        self.assertNotIn("HIDDEN-COMMENT", prompt)
        self.assertLess(prompt.index("## 约束"), prompt.index("TEAM-RULE-MARKER"))
        self.assertLess(prompt.index("TEAM-RULE-MARKER"), prompt.index("## 完成协议"))

    def test_default_rules_first_by_default(self) -> None:
        # 没写 default_coding_rules：包默认的编码规范（flow/coding-rules.md）一节排在团队 profile、个人覆盖前面
        # （后面的优先），claude、codex 都加；codex 的全局 CLAUDE.md 那节照旧在最后
        for kind in ("claude", "codex"):
            prompt = self.brief(kind, self.repo)["prompt"]
            self.assertEqual(prompt.count(DEFAULT_HEADING), 1)
            start = prompt.index(DEFAULT_HEADING)
            self.assertLess(prompt.index("## 约束"), start)
            self.assertLess(start, prompt.index("## 编码规范（团队 profile：team"))
            self.assertLess(prompt.index("TEAM-RULE-MARKER"), prompt.index("MINE-RULE-MARKER"))
            section = prompt[start:prompt.index("\n## ", start + 1)]
            self.assertIn("### 通用原则", section)
            self.assertIn("**修根因**（Fix Root Causes）", section)
            self.assertNotIn("<!--", section)  # 给维护者的注释不进任务书
            if kind == "codex":
                self.assertLess(prompt.index("MINE-RULE-MARKER"), prompt.index("USER-RULE-MARKER"))

    def test_default_rules_content(self) -> None:
        # flow/coding-rules.md 的正文：开头一句说只在改到相关代码时适用、冲突时按仓库 / 团队 / 个人的；通用原则改编自 ub-mode，
        # 收进来的每节都在（节名后括号里留英文原名），Practice 四节不在；不写 ## 级标题（brief 已经加了小节标题），可以有 ###。
        # 文件开头的注释写明出处和许可证（不进任务书）
        raw = DEFAULT_RULES.read_text()
        self.assertIn("改编自 unclebob/ub-mode（MIT，Copyright (c) 2026 Robert C. Martin）", raw)
        self.assertIn("third_party/ub-mode/", raw)
        self.assertNotRegex(raw, r"(?m)^## ")
        self.assertEqual(re.findall(r"(?m)^<!-- drover:section (\S+) -->$", raw), ["mysql", "java"])
        body = self.default_body(self.brief("claude", self.repo)["prompt"])
        self.assertTrue(body.startswith("这是 drover 默认的编码规范，只在改到相关代码时适用；"
                                        "仓库自己的 `AGENTS.md` / `CLAUDE.md`、团队 profile、个人覆盖和这里冲突时按它们的。"), body)
        self.assertIn("\n### 通用原则\n", body)
        for name in UB_MODE_KEPT:
            self.assertIn(f"（{name}", body)
        for name in UB_MODE_DROPPED:
            self.assertNotIn(name, raw)
        self.assertIn("写不出诚实的测试，就不写坏测试，在报告里说明", body)
        self.assertIn("drover 默认装了 unclebob/crapper、dryer，验收时自动跑", body)
        self.assertIn("手动跑的按原阈值：CRAP < 10、变异测试的存活者要杀掉", body)
        self.assertNotIn("\n## ", body)
        self.assertNotIn("npmmirror", body)
        self.assertNotIn("<!--", body)
        self.assertNotIn("drover:section", body)

    def test_ub_mode_sections_all_accounted(self) -> None:
        # 和原文逐节对照：SKILL.md 的每个 ## / ### 节要么收（UB_MODE_KEPT），要么不收（UB_MODE_DROPPED），不漏不多
        headings = re.findall(r"(?m)^##+ (.+)$", (UB_MODE / "SKILL.md").read_text())
        self.assertEqual(sorted(headings), sorted(UB_MODE_KEPT + UB_MODE_DROPPED))
        self.assertEqual(set(headings[-4:]), set(UB_MODE_DROPPED))  # 不收的正好是最后的 Practice 一组

    def test_ub_mode_originals_unchanged(self) -> None:
        # third_party/ub-mode/ 的 SKILL.md、LICENSE 和上游提交里的原文字节一致；ub-mode 自己的 README 没拷
        for name, digest in UB_MODE_SHA256.items():
            self.assertEqual(hashlib.sha256((UB_MODE / name).read_bytes()).hexdigest(), digest, name)
        self.assertIn("Copyright (c) 2026 Robert C. Martin", (UB_MODE / "LICENSE").read_text())
        ours = (UB_MODE / "README.md").read_text()
        self.assertIn("https://github.com/unclebob/ub-mode", ours)
        self.assertIn("3da62bf3fcfd2034de41f30d920d2739e566171f", ours)

    def test_default_rules_sections_by_worktree(self) -> None:
        # 通用原则总带。Java 一节只在 worktree 根目录有 pom.xml / build.gradle / build.gradle.kts 时带（子目录的不算）；
        # MySQL 一节在有被 git 跟踪的 .sql 文件、或依赖清单（任意一层）里有 mysql / mariadb 字样时带，只有 ORM、.sql 没被跟踪的
        # 不带；两节都带时 MySQL 在前。分节标记和注释不进任务书
        cases = {
            "plain": ({}, False, False),
            "maven": ({"pom.xml": POM}, True, False),
            "gradle-kts": ({"build.gradle.kts": "plugins { java }\n"}, True, False),
            "sub-pom": ({"service/pom.xml": POM}, False, False),
            "sql": ({"db/migration/V1__init.sql": "CREATE TABLE t (id INT);\n"}, False, True),
            "pymysql": ({"requirements-dev.txt": "PyMySQL==1.1.0\n"}, False, True),
            "mariadb": ({"build.gradle": "dependencies { runtimeOnly 'org.mariadb.jdbc:mariadb-java-client:3.3.0' }\n"},
                        True, True),
            "nested-js": ({"web/package.json": '{"dependencies": {"mysql2": "^3.9.0"}}\n'}, False, True),
            "orm-only": ({"pyproject.toml": '[project]\ndependencies = ["sqlalchemy>=2", "alembic"]\n',
                          "package.json": '{"dependencies": {"prisma": "^5.0.0"}}\n'}, False, False),
        }
        for name, (files, java, mysql) in cases.items():
            with self.subTest(name):
                prompt = self.brief("claude", self.make_repo(name, files))["prompt"]
                body = self.default_body(prompt)
                self.assertIn("### 通用原则", body)
                self.assertIn("**测行为，不测实现**（Test Behavior, Not Implementation）", body)
                self.assertEqual("\n### Java\n" in body, java)
                self.assertEqual("**不用嵌套类和内部类**（写 Java 时）" in body, java)
                self.assertEqual("\n### MySQL\n" in body, mysql)
                self.assertEqual("**MySQL 唯一索引名以 `uniq_` 开头**（改动含 MySQL DDL 时）" in body, mysql)
                if java and mysql:
                    self.assertLess(body.index("### MySQL"), body.index("### Java"))
                self.assertNotIn("drover:section", prompt)
                self.assertNotIn("<!--", body)
                self.assertNotIn("\n\n\n", body)  # 去掉的节不留成串的空行
        # .sql 没被 git 跟踪（没提交、被忽略的）不算
        loose = self.make_repo("loose-sql")
        self.write(loose / "scratch.sql", "SELECT 1;\n")
        self.assertNotIn("### MySQL", self.default_body(self.brief("claude", loose)["prompt"]))

    def test_default_rules_sections_any_worktree(self) -> None:
        # 任务书涉及的任一 worktree 满足就带：主 worktree 什么都没有，--wt-extra 的仓库有 pom.xml → 带 Java 一节
        java = self.make_repo("beta", {"pom.xml": POM})
        out = self.brief("claude", self.repo, "--wt-extra", f"beta={java}")
        body = self.default_body(out["prompt"])
        self.assertIn("\n### Java\n", body)
        self.assertNotIn("### MySQL", body)

    def test_team_rules_sections(self) -> None:
        # 团队 profile 的 coding-rules.md 用同样的分节标记：第一个标记前的总带，java 节按同样的判断，认不得的名字不带
        team = self.make_profile("team-sec", coding="- TEAM-ALL\n\n<!-- drover:section java -->\n- TEAM-JAVA\n\n"
                                                   "<!-- drover:section go -->\n- TEAM-GO\n")
        self.write_config(profile=str(team))
        plain = self.brief("claude", self.repo)["prompt"]
        self.assertIn("## 编码规范（团队 profile：team-sec；必须遵守）\n- TEAM-ALL\n\n## ", plain)
        self.assertNotIn("TEAM-JAVA", plain)
        java = self.brief("claude", self.make_repo("javaapp", {"pom.xml": POM}))["prompt"]
        self.assertIn("- TEAM-ALL\n\n- TEAM-JAVA\n\n## ", java)
        self.assertNotIn("TEAM-GO", java)

    def test_default_rules_switch_off(self) -> None:
        # default_coding_rules: false 整份去掉包默认这一节（Java、MySQL 仓库也一样），团队 profile、个人覆盖、
        # codex 的全局 CLAUDE.md 照旧
        self.write_config(profile=str(self.tmp / "team"), playbook_dirs=[str(self.tmp / "mine")], default_coding_rules=False)
        repo = self.make_repo("full", {"pom.xml": "<dependency>com.mysql:mysql-connector-j</dependency>\n"})
        for kind in ("claude", "codex"):
            prompt = self.brief(kind, repo)["prompt"]
            self.assertNotIn("drover 默认", prompt)
            self.assertNotIn("Fix Root Causes", prompt)
            self.assertNotIn("uniq_", prompt)
            self.assertNotIn("不内联全限定类名", prompt)
            self.assertIn("## 编码规范（团队 profile：team；必须遵守）\n- TEAM-RULE-MARKER", prompt)
            self.assertIn(f"## 编码规范（个人覆盖：{self.tmp / 'mine'}；必须遵守）\n- MINE-RULE-MARKER", prompt)
            self.assertEqual("USER-RULE-MARKER" in prompt, kind == "codex")
            # 只关编码规范：不执行迁移那条固定约束还在
            self.assertIn("不执行迁移", prompt)

    def test_no_migration_constraint(self) -> None:
        # 固定约束：只写迁移文件和测试、不执行迁移、不写共享 / 生产数据库，迁移由编排者执行；claude、codex，
        # 能不能提交都有
        line = "- 只写迁移文件和测试，不执行迁移，不对任何共享 / 生产数据库做写操作；迁移由编排者按仓库规则执行。"
        for kind in ("claude", "codex"):
            for extra in ((), ("--commit-allowed",)):
                prompt = self.brief(kind, self.repo, *extra)["prompt"]
                rules = prompt[prompt.index("## 约束"):prompt.index("\n## ", prompt.index("## 约束"))]
                self.assertIn(line, rules)

    def test_heavy_path_is_literal_tilde(self) -> None:
        # 任务书里 heavy 写成字面的 ~/.drover/bin/drover（命中 install.py 的白名单 Bash(~/.drover/bin/drover:*)，
        # 展开成绝对路径时 worker 每跑一次都弹框）：不展开成 HOME 的绝对路径，也不指向 DROVER_STATE_DIR
        for kind in ("claude", "codex"):
            prompt = self.brief(kind, self.repo)["prompt"]
            self.assertIn("`~/.drover/bin/drover heavy -- <命令>`", prompt)
            self.assertNotIn(str(self.home / ".drover" / "bin"), prompt)
            self.assertNotIn(str(self.state / "bin"), prompt)

    def test_claude_approval_rules_name_assignment_prefix(self) -> None:
        # 审批友好写法里有"不用变量赋值前缀，路径写全"；带 VAR=值 的构建命令套 heavy 执行
        prompt = self.brief("claude", self.repo)["prompt"]
        self.assertIn("不用变量赋值前缀（`W=/path; grep … $W/…`），路径写全", prompt)
        self.assertIn("`~/.drover/bin/drover heavy -- JAVA_HOME=… mvn …`", prompt)

    def test_brief_names_orchestrator(self) -> None:
        # worker 任务书里称呼编排者，协议串和状态文件名（派发这个动作的名字）不变
        for kind in ("claude", "codex"):
            prompt = self.brief(kind, self.repo)["prompt"]
            self.assertIn("由编排者派发和追踪", prompt)
            self.assertIn("基线由编排者统一管理", prompt)
            self.assertIn("=== DISPATCH RESULT ===", prompt)
            self.assertNotIn("Orchestr" + "ator", prompt)

    def test_codex_gets_team_rules_and_claude_md(self) -> None:
        prompt = self.brief("codex", self.repo)["prompt"]
        self.assertIn("TEAM-RULE-MARKER", prompt)
        self.assertIn("MINE-RULE-MARKER", prompt)
        self.assertIn("## 编码规范（用户全局规则；codex 不会自动加载，必须遵守）\n- USER-RULE-MARKER", prompt)
        self.assertNotIn("不相关", prompt)  # 只取"编码规范"那一节
        self.assertLess(prompt.index("MINE-RULE-MARKER"), prompt.index("USER-RULE-MARKER"))

    def test_no_rules_no_section(self) -> None:
        # 没配 profile、没有 CLAUDE.md：只有包默认那一节；再关掉 default_coding_rules 就一节编码规范都没有
        self.write_config()
        (self.home / ".claude" / "CLAUDE.md").unlink()
        for kind in ("claude", "codex"):
            prompt = self.brief(kind, self.repo)["prompt"]
            self.assertEqual(prompt.count("## 编码规范"), 1)
            self.assertIn(DEFAULT_HEADING, prompt)
        self.write_config(default_coding_rules=False)
        for kind in ("claude", "codex"):
            prompt = self.brief(kind, self.repo)["prompt"]
            self.assertNotIn("## 编码规范", prompt)
            self.assertNotIn("Fix Root Causes", prompt)

    def test_package_example_rules_and_missing_profile_warning(self) -> None:
        self.write_config(profile="example")
        prompt = self.brief("claude", self.repo)["prompt"]
        self.assertIn("## 编码规范（团队 profile：example；必须遵守）\n- **新增依赖要锁定版本**", prompt)
        self.assertIn("**不提交密钥**", prompt)
        self.assertNotIn("示例团队 profile 的编码规范", prompt)  # 文件开头给维护者的注释不进任务书
        self.write_config(profile="no-such-team")
        out = self.brief("claude", self.repo)
        self.assertNotIn("## 编码规范（团队 profile", out["prompt"])
        self.assertIn(DEFAULT_HEADING, out["prompt"])  # 团队 profile 读不到不影响包默认那一节
        self.assertTrue(any("团队编码规范没有追加" in w for w in out["warnings"]), out["warnings"])


# ---------------------------------------------------------------- brief 的摸底模板和完成协议


class BriefTemplateTests(PlaybookTestBase):
    """中间文档减量：摸底只写结论和证据位置、不符 / 决策两部分写全、其余 100 行左右；报告每条 verification 一行、
    60 行内、对话里只回复路径和 status（不再把报告块写第二遍）。"""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.make_repo("alpha")
        self.write_config()

    def test_protocol_with_and_without_survey(self) -> None:
        plain = self.brief("claude", self.repo)["prompt"]
        survey = self.brief("claude", self.repo, "--survey",
                            task=TASK + "\n### 摸底时要写清\n只点影响决策的问题。\n")["prompt"]
        for prompt in (plain, survey):
            protocol = prompt[prompt.index("## 完成协议"):]
            self.assertIn("每条一行：命令加结果汇总行（如 `Tests run: 218, Failures: 0`、`164 passed`）", protocol)
            self.assertIn("修 bug 的写明回归测试改之前失败、改之后通过（命令和结果）", protocol)
            self.assertIn("整份报告尽量 60 行以内；verification 不贴命令输出、不点名测试类、不重述摸底", protocol)
            self.assertIn(f"在对话里只回复一行：报告路径和 status（如 `{self.state / 'selftest-playbook' / 'st-agent.md'} "
                          "status: DONE`），不要把报告块再写一遍", protocol)
            self.assertNotIn("回复同样的报告块", prompt)
        self.assertNotIn("## 第一步：先摸底", plain)
        head = survey[survey.index("## 第一步：先摸底"):survey.index("## 任务")]
        self.assertIn("只写结论和证据位置（文件:行），不贴代码和命令输出（超过 5 行的一律不贴", head)
        self.assertIn("任务书写对的部分一句\"其余和任务书一致\"带过，不逐条确认", head)
        self.assertIn("第 1、4 部分写全、不限长；第 2、3 部分合计控制在 100 行左右", head)
        self.assertIn("报告里不重述摸底内容", head)

    def test_survey_section_template_hint(self) -> None:
        # 带 --survey 却没写"摸底时要写清"：报错带的模板里提醒只点影响决策的问题；"要做的"提示没核实的根因只写嫌疑
        out = self.brief("claude", self.repo, "--survey", rc=1)
        self.assertIn("只点影响决策的问题，不要让 worker 逐条确认任务书写对的部分", out["template"])
        self.assertIn("根因核实过的写出做法，没核实的写嫌疑、由摸底定", out["template"])

    def test_root_fix_rules(self) -> None:
        # 根治不打补丁：约束（claude、codex，摸底与否都有）、摸底第 3 部分先写根因链、报告带 root_cause
        # 证据标准：①从目标推；注释、命名、调用方一侧只算线索；不在 worktree 里的仓库写明没读；root_cause 附证据位置
        # 事实判断的证据约束（网关探针只报"网关忽略"、OCR 把 AI 的核对当结论）：现象带原因、外部平台查官方文档、
        # 先核实、识别类结果未经用户确认；摸底第 3 部分补外部平台一句；root_cause 要写为什么
        survey_task = TASK + "\n### 摸底时要写清\n只点影响决策的问题。\n"
        for kind in ("claude", "codex"):
            plain = self.brief(kind, self.repo)["prompt"]
            survey = self.brief(kind, self.repo, "--survey", task=survey_task)["prompt"]
            for prompt in (plain, survey):
                rules = prompt[prompt.index("## 约束"):prompt.index("\n## ", prompt.index("## 约束"))]
                self.assertIn("改在问题产生的那一层，不在症状出现的地方加判断、特例、兜底或词表；能删就不加，"
                              "能复用现有链路就不新建", rules)
                self.assertIn("任务书指定的做法只治了症状的，在摸底或报告里指出来、给出源头的改法（标 NEED_INPUT），不要照做", rules)
                self.assertIn("报告、摸底、注释、文档里写的事实判断都要有证据（代码 文件:行、实测、外部平台的官方文档链接和段落）", rules)
                self.assertIn("现象（不生效、不支持、报错）连原因一起写，查不到写\"原因未查明\"和已排除的可能", rules)
                self.assertIn("经过网关、中转测到的现象定不了是哪一层", rules)
                self.assertIn("一两次查询（读源码、查官方文档、花费很小的探针）能核实的先核实，不拿推断交差", rules)
                self.assertIn("识别类结果（OCR、识图、转写）你自己的核对只算初步，写明未经用户确认", rules)
                protocol = prompt[prompt.index("## 完成协议"):]
                self.assertIn("root_cause: <修 bug 或改设计时一句：问题产生在哪一层、为什么（附证据位置）、改在哪一层；"
                              "新增的特例 / 兜底 / 回落逐个列出", protocol)
            head = survey[survey.index("## 第一步：先摸底"):survey.index("## 任务")]
            self.assertIn("先写根因链（应该怎样：从目标推，不从现有实现推 → 问题在哪一层产生、为什么，附证据 → 改在哪一层；", head)
            self.assertIn("证据写仓库 文件:行或实测，注释、命名、文档、调用方那一侧只算线索，"
                          "链路经过、不在你 worktree 里的仓库写明没读，不推断它的行为；"
                          "外部平台（厂商 API、云模型平台）读不到代码的，查它的官方文档（写链接和段落）），"
                          "根因落在任务书\"不做\"的范围里也照写，不标\"不在范围\"", head)

    def test_send_waits_until_working(self) -> None:
        # --send 的 prompt 等到 agent 开工（working）或停下就返回，和 prompt 子命令、orch-start 用同一组参数
        planned = self.brief("claude", self.repo, "--send")["planned"]
        self.assertEqual(planned[-1], f"herdr agent prompt st-agent <{self.state / 'selftest-playbook' / 'st-agent.prompt.md'}>"
                                      " --wait --until working --until idle --until done --until blocked --timeout 30000")


# ---------------------------------------------------------------- brief 的宿主


class BriefKindTests(PlaybookTestBase):
    """--kind 不给时任务书按收它的 agent 的实际宿主写（herdr 里 --name 这个 agent 的 agent 字段）；agent 还没起时同
    agent-start（orch.json 的 default_kind，再调用方 pane）；显式 --kind 和活着的 agent 对不上进 warnings、照用 --kind。"""

    CODEX_RULE = "你跑在 workspace-write 沙箱里"

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.make_repo("alpha")
        self.write_config()

    def test_kind_from_live_agent(self) -> None:
        self.set_herdr({"agents": {"st-agent": {"status": "idle", "agent": "codex"}}})
        out = self.brief(None, self.repo)
        self.assertEqual((out["kind"], out["kind_source"]), ("codex", "agent"))
        self.assertIn(self.CODEX_RULE, out["prompt"])
        self.assertEqual([w for w in out["warnings"] if "对不上" in w], [])

    def test_explicit_kind_mismatch_warns(self) -> None:
        self.set_herdr({"agents": {"st-agent": {"status": "idle", "agent": "codex"}}})
        out = self.brief("claude", self.repo)
        self.assertEqual((out["kind"], out["kind_source"]), ("claude", "explicit"))
        self.assertNotIn(self.CODEX_RULE, out["prompt"])  # 照用 --kind
        self.assertTrue(any("--kind claude 和 agent st-agent 实际的宿主 codex 对不上" in w for w in out["warnings"]),
                        out["warnings"])
        same = self.brief("codex", self.repo)
        self.assertEqual([w for w in same["warnings"] if "对不上" in w], [])

    def test_agent_not_started_uses_task_default(self) -> None:
        """agent 还没起：orch.json 的 default_kind → 调用方 pane → 都没有就报错让加 --kind"""
        orch = self.write(self.state / "selftest-playbook" / "orch.json", json.dumps({"default_kind": "codex"}))
        out = self.brief(None, self.repo)
        self.assertEqual((out["kind"], out["kind_source"]), ("codex", "orch"))
        self.assertIn(self.CODEX_RULE, out["prompt"])
        orch.unlink()
        self.env["HERDR_PANE_ID"] = "w0:p1"
        self.set_herdr({"panes": {"w0:p1": {"agent": "claude"}}})
        out = self.brief(None, self.repo)
        self.assertEqual((out["kind"], out["kind_source"]), ("claude", "caller"))
        del self.env["HERDR_PANE_ID"]
        out = self.brief(None, self.repo, rc=1)
        self.assertIn("herdr 里没有 agent st-agent", out["error"])
        self.assertIn("加 --kind claude|codex", out["error"])

    def test_state_file_records_resolved_kind(self) -> None:
        # 不只是返回值：写进状态文件的 brief 段的也是解析出来的宿主（6.1 验收按它核对）
        self.set_herdr({"agents": {"st-agent": {"status": "idle", "agent": "codex"}}})
        out = self.brief(None, self.repo, dry_run=False)
        self.assertEqual(out["kind"], "codex")
        self.assertIn(self.CODEX_RULE, Path(out["prompt_file"]).read_text())
        self.assertRegex(Path(out["state_file"]).read_text(), r"## brief：st-agent（[^，]+，codex，摸底：否）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
