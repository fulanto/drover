"""验收看证据（改动项 10、13、14）：brief 的 --evidence-cmd 落成 <slug>/<agent>.evidence.json、任务书带命令；
accept --slug --name 自动跑证据命令、默认存 accept-<agent>.json、摘要给"证据 / 有命令 / 通过 / 只能看自述"四个数；
review 按主 agent 名找存档、worktree / 提交 / 改动对不上拒绝；heavy 在条目 worktree 里每跑完一条记一行流水、accept 列出
"先失败后通过"；几个 brief 同时写状态文件不丢段、同一个 agent 再生成任务书时上一轮的文件按轮次留着。

不建图：临时 PATH 去掉了 code-review-graph（同 test_accept_cli.py）。pytest、tsc 是 selftest_fakes 的假工具：pytest 的失败
读 worktree 里的 fake-pytest.json（点名文件时只报这些文件里的）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from selftest_support import DROVER, DroverTestCase

TASK = "### 背景\n测试用。\n\n### 要做的\n什么都不做。\n\n### 不做\n不改任何文件。\n"
SLUG = "s1"


class EvidenceTestBase(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        if shutil.which("code-review-graph", path=self.env["PATH"]):
            self.skipTest("临时 PATH 里还能找到 code-review-graph，跑 accept 会真的建图")
        self.slug_dir = self.state / SLUG
        self.slug_dir.mkdir()
        self.wt = self.work / "app"
        self.base = self.init_repo(self.wt, {"src/a.py": "x = 1\n", "tests/test_a.py": "def test_x(): pass\n",
                                             "tests/test_ok.py": "def test_ok(): pass\n"})
        self.git(self.wt, "checkout", "-q", "-b", f"task/{SLUG}-w1")  # 条目分支 task/<slug>-<条目>
        self.write_spec(self.wt, "pytest", {"failures": ["tests/test_a.py::test_x"]})
        self.write_files(self.wt, {"src/a.py": "x = 2\n"})

    def brief(self, *args: str, name: str = "w1", rc: int = 0, wt: Path = None) -> dict:
        wt = wt or self.wt
        deps = self.tmp / f"deps-{name}.json"
        deps.write_text(json.dumps({"worktree_path": str(wt), "summary_for_prompt": "- 无", "items": []}))
        task = self.tmp / f"{name}.task.md"
        task.write_text(TASK)
        return self.drover_json("brief", "--slug", SLUG, "--name", name, "--kind", "claude", "--repo", str(wt),
                                "--wt", str(wt), "--deps-json", str(deps), "--task-file", str(task),
                                "--goal", "自测目标", *args, rc=rc)

    def accept(self, *args: str, rc: int = 0) -> dict:
        return self.drover_json("accept", "--wt", str(self.wt), "--base", self.base, *args, rc=rc)

    def review(self, *args: str, name: str = "w1-rev", wt: Path = None, rc: int = 0) -> dict:
        return self.drover_json("review", "--slug", SLUG, "--name", name, "--pane", "w1:p1",
                                "--wt", str(wt or self.wt), "--base", self.base, "--dry-run", *args, rc=rc)

    def heavy(self, *cmd: str, rc: int = 0, cwd: Path = None) -> subprocess.CompletedProcess:
        return self.drover("heavy", "--cwd", str(cwd or self.wt), "--", *cmd, rc=rc)


def evidence_section(prompt: str) -> str:
    start = prompt.index("## 完成证据")
    return prompt[start:prompt.index("\n## ", start + 1)]


class BriefEvidenceTests(EvidenceTestBase):
    def test_brief_writes_evidence_commands(self) -> None:
        """--evidence-cmd 挂在它前面那条 --evidence 下（可以几条）：写 <slug>/<agent>.evidence.json，任务书和状态文件的
        完成证据都带命令；任务书写明证据目录和流水的引用方式"""
        out = self.brief("--evidence", "相关测试全过", "--evidence-cmd", "pytest tests/test_ok.py -q",
                         "--evidence-cmd", "tsc --noEmit", "--evidence", "登录页手动走一遍")
        path = self.slug_dir / "w1.evidence.json"
        self.assertEqual(out["evidence_file"], str(path))
        data = json.loads(path.read_text())
        self.assertEqual((data["slug"], data["agent"], data["worktree"], data["base"]),
                         (SLUG, "w1", str(self.wt.resolve()), self.base[:12]))
        self.assertEqual(data["items"], [
            {"n": 1, "text": "相关测试全过", "commands": ["pytest tests/test_ok.py -q", "tsc --noEmit"]},
            {"n": 2, "text": "登录页手动走一遍", "commands": []}])
        prompt = (self.slug_dir / "w1.prompt.md").read_text()
        section = evidence_section(prompt)
        self.assertIn("1. 相关测试全过", section)
        self.assertIn("`pytest tests/test_ok.py -q`", section)
        self.assertIn("`tsc --noEmit`", section)
        self.assertIn("2. 登录页手动走一遍", section)
        self.assertIn(str(self.slug_dir / "evidence" / "w1"), prompt)  # 证据目录
        self.assertIn("流水", prompt)  # 完成协议：每条验证引用 heavy 流水
        state = (self.slug_dir / "dispatch.md").read_text()
        self.assertIn("`pytest tests/test_ok.py -q`", state)
        self.assertIn(str(path), state)

    def test_evidence_cmd_needs_preceding_evidence(self) -> None:
        """--evidence-cmd 写在所有 --evidence 前面、或拆不开（引号没配对）：报错，不生成任务书"""
        out = self.brief("--evidence-cmd", "pytest -q", "--evidence", "测试全过", rc=1)
        self.assertIn("--evidence-cmd", out["error"])
        out = self.brief("--evidence", "测试全过", "--evidence-cmd", "pytest 'tests", rc=1)
        self.assertIn("--evidence-cmd", out["error"])
        self.assertFalse((self.slug_dir / "w1.prompt.md").exists())

    def test_brief_without_evidence_cmd_still_works(self) -> None:
        """向后兼容：只有 --evidence 的老写法照常生成；证据清单照写（都没有命令），任务书不出现"验收会重跑\""""
        out = self.brief("--evidence", "测试全过", "--evidence", "lint 干净")
        self.assertTrue(out["ok"], out)
        data = json.loads((self.slug_dir / "w1.evidence.json").read_text())
        self.assertEqual([it["commands"] for it in data["items"]], [[], []])
        self.assertNotIn("验收会重跑", evidence_section((self.slug_dir / "w1.prompt.md").read_text()))

    def test_rebrief_keeps_previous_round(self) -> None:
        """同一个 agent 再生成任务书：上一轮的任务书、报告、摸底挪成 .r1（再来一轮是 .r2），新 brief 段写出挪到哪了"""
        self.brief("--evidence", "第一轮证据")
        first = (self.slug_dir / "w1.prompt.md").read_text()
        (self.slug_dir / "w1.md").write_text("第一轮报告\n")
        (self.slug_dir / "w1.survey.md").write_text("第一轮摸底\n")
        out = self.brief("--evidence", "第二轮证据")
        self.assertEqual((self.slug_dir / "w1.prompt.r1.md").read_text(), first)
        self.assertEqual((self.slug_dir / "w1.r1.md").read_text(), "第一轮报告\n")
        self.assertEqual((self.slug_dir / "w1.survey.r1.md").read_text(), "第一轮摸底\n")
        self.assertFalse((self.slug_dir / "w1.md").exists())
        self.assertFalse((self.slug_dir / "w1.survey.md").exists())
        self.assertIn("第二轮证据", (self.slug_dir / "w1.prompt.md").read_text())
        self.assertIn(str(self.slug_dir / "w1.prompt.r1.md"), out["previous_round"])
        state = (self.slug_dir / "dispatch.md").read_text()
        self.assertIn(str(self.slug_dir / "w1.prompt.r1.md"), state.split("## brief：w1")[-1])
        self.brief("--evidence", "第三轮证据")
        self.assertIn("第二轮证据", (self.slug_dir / "w1.prompt.r2.md").read_text())
        self.assertEqual((self.slug_dir / "w1.prompt.r1.md").read_text(), first)

    def test_parallel_briefs_keep_every_segment(self) -> None:
        """几个 brief 同时追加同一个状态文件（真实进程）：每一段都在、原有内容不丢（以前整份读写，实测 240 次丢 75 段）"""
        state = self.slug_dir / "dispatch.md"
        state.write_text("# dispatch: s1\n\n目标：并发写状态文件，原有内容不能丢\n")
        deps = self.tmp / "deps.json"
        deps.write_text(json.dumps({"worktree_path": str(self.wt), "summary_for_prompt": "- 无", "items": []}))
        task = self.tmp / "task.md"
        task.write_text(TASK)
        names = []
        for round_no in range(8):
            procs = []
            for i in range(6):
                name = f"p{round_no}x{i}"
                names.append(name)
                procs.append(subprocess.Popen(
                    [sys.executable, str(DROVER), "brief", "--slug", SLUG, "--name", name, "--kind", "claude",
                     "--repo", str(self.wt), "--wt", str(self.wt), "--deps-json", str(deps), "--task-file", str(task),
                     "--goal", "并发", "--evidence", f"证据-{name}"],
                    env=self.env, cwd=str(self.tmp), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            for p in procs:
                out, err = p.communicate(timeout=120)
                self.assertEqual(p.returncode, 0, out + err)
        text = state.read_text()
        self.assertTrue(text.startswith("# dispatch: s1\n\n目标：并发写状态文件，原有内容不能丢\n"), text[:200])
        missing = [n for n in names if f"## brief：{n}（" not in text]
        self.assertEqual(missing, [], f"{len(missing)} / {len(names)} 段没留在状态文件里")
        self.assertEqual(text.count("## brief："), len(names))


class AcceptEvidenceTests(EvidenceTestBase):
    def test_accept_name_runs_evidence_and_saves_per_agent(self) -> None:
        """accept --slug --name：证据命令当 verify 跑（排在前面，和 --verify 字面相同的只跑一次），默认存 accept-<agent>.json，
        摘要给四个数和每条证据的结论；accept-summary 读存档给同样的结论"""
        self.brief("--evidence", "相关测试全过", "--evidence-cmd", "pytest tests/test_ok.py -q",
                   "--evidence", "回归用例通过", "--evidence-cmd", "pytest tests/test_a.py -q",
                   "--evidence", "登录页手动走一遍")
        res = self.accept("--slug", SLUG, "--name", "w1", "--brief",
                          "--verify", "pytest tests/test_ok.py -q", "--verify", "tsc --noEmit")
        saved = self.slug_dir / "accept-w1.json"
        self.assertEqual(res["saved_to"], str(saved))
        self.assertEqual([v["command"] for v in res["verify"]],
                         ["pytest tests/test_ok.py -q", "pytest tests/test_a.py -q", "tsc --noEmit"])
        self.assertTrue(res["verify_failed"])
        ev = res["evidence"]
        self.assertEqual(ev["summary"], "证据 3 条 / 有命令 2 条 / 通过 1 条 / 只能看自述 1 条")
        self.assertEqual([it["status"] for it in ev["items"]], ["通过", "失败", "只能看自述"])
        self.assertEqual(ev["items"][1]["commands"][0]["command"], "pytest tests/test_a.py -q")
        self.assertFalse(ev["items"][1]["commands"][0]["ok"])
        archive = json.loads(saved.read_text())
        self.assertEqual((archive["agent"], archive["slug"]), ("w1", SLUG))
        summary = self.drover_json("accept-summary", str(saved))
        self.assertEqual(summary["evidence"], ev)

    def test_accept_name_requires_slug(self) -> None:
        """--name 不配 --slug：参数错误（不知道去哪个任务目录找证据清单）"""
        p = self.drover("accept", "--wt", str(self.wt), "--base", self.base, "--name", "w1", rc=2)
        self.assertIn("--slug", p.stderr)

    def test_accept_name_without_evidence_file(self) -> None:
        """旧版 brief 没留证据清单：只跑 --verify，证据写明"没有证据清单"，照样存 accept-<agent>.json"""
        res = self.accept("--slug", SLUG, "--name", "w1", "--verify", "tsc --noEmit")
        self.assertEqual([v["command"] for v in res["verify"]], ["tsc --noEmit"])
        self.assertIsNone(res["evidence"]["items"])
        self.assertIn("没有证据清单", res["evidence"]["summary"])
        self.assertTrue((self.slug_dir / "accept-w1.json").is_file())

    def test_accept_name_rejects_other_worktree(self) -> None:
        """证据清单是另一个 worktree 的（agent 名或 --wt 写错了）：拒绝，不跑别人的证据"""
        other = self.work / "other"
        self.init_repo(other, {"a.txt": "a\n"})
        self.brief("--evidence", "测试全过", "--evidence-cmd", "tsc --noEmit", wt=other)
        res = self.accept("--slug", SLUG, "--name", "w1", rc=1)
        self.assertIn(str(other.resolve()), res["error"])

    def test_accept_save_without_name_unchanged(self) -> None:
        """向后兼容：不带 --name 的 accept --save <任意路径> 照旧，不读证据清单、结果里没有 evidence"""
        self.brief("--evidence", "测试全过", "--evidence-cmd", "pytest tests/test_a.py -q")
        save = self.tmp / "anywhere" / "mine.json"
        res = self.accept("--save", str(save), "--verify", "tsc --noEmit")
        self.assertEqual(res["saved_to"], str(save))
        self.assertEqual([v["command"] for v in res["verify"]], ["tsc --noEmit"])
        self.assertNotIn("evidence", res)
        self.assertFalse((self.slug_dir / "accept-w1.json").exists())


class ReviewArchiveTests(EvidenceTestBase):
    def test_review_finds_archive_by_agent_name(self) -> None:
        """review --name w1-rev 用 accept-w1.json（同目录里另一个条目留下的 accept.json 不用）"""
        self.accept("--slug", SLUG, "--name", "w1", "--verify", "tsc --noEmit")
        (self.slug_dir / "accept.json").write_text(json.dumps({
            "worktree_path": "/elsewhere", "verify": [{"command": "eslint src", "ok": True, "exit_code": 0}]}))
        res = self.review()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["accept_json"], str(self.slug_dir / "accept-w1.json"))
        self.assertIn("`tsc --noEmit` 通过", res["prompt"])
        self.assertNotIn("eslint", res["prompt"])

    def test_review_rejects_archive_after_change(self) -> None:
        """验收之后 worktree 又改了（没提交，HEAD 没变）：拒绝，提示重新 accept"""
        self.accept("--slug", SLUG, "--name", "w1", "--verify", "tsc --noEmit")
        self.write_files(self.wt, {"src/a.py": "x = 3\n"})
        res = self.review(rc=1)
        self.assertIn("重新 accept", res["error"])

    def test_review_rejects_other_commit(self) -> None:
        """验收之后又提交了（HEAD 变了）：拒绝"""
        self.accept("--slug", SLUG, "--name", "w1", "--verify", "tsc --noEmit")
        self.commit(self.wt, "more")
        res = self.review(rc=1)
        self.assertIn("HEAD", res["error"])

    def test_review_rejects_other_worktree(self) -> None:
        """存档是另一个 worktree 的：拒绝"""
        save = self.tmp / "other-accept.json"
        self.accept("--save", str(save), "--verify", "tsc --noEmit")
        other = self.work / "other"
        self.init_repo(other, {"a.txt": "a\n"})
        res = self.review("--accept-json", str(save), wt=other, rc=1)
        self.assertIn(str(self.wt.resolve()), res["error"])

    def test_review_accept_json_still_works(self) -> None:
        """向后兼容：--accept-json 指定任意路径的存档（不带 --name 验收的）照常起复审"""
        save = self.tmp / "mine.json"
        self.accept("--save", str(save), "--verify", "tsc --noEmit")
        res = self.review("--accept-json", str(save))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["accept_json"], str(save))


class HeavyRunLogTests(EvidenceTestBase):
    def runs(self) -> list:
        path = self.slug_dir / "heavy-runs.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_heavy_records_runs_and_accept_lists_red_then_green(self) -> None:
        """条目 worktree 里经 heavy 跑：每条一行流水（worktree、命令、退出码、汇总行、指纹），stderr 打流水号，输出照样透传；
        先失败（改之前）、修好后同一条命令通过 → accept 列进 red_then_green，通过那次在现在的代码上"""
        red = self.heavy("pytest", "tests/test_a.py", "-q", rc=1)
        self.assertIn("1 failed", red.stdout)
        self.assertIn("流水 #1", red.stderr)
        self.write_spec(self.wt, "pytest", {"failures": []})  # 修好了
        self.write_files(self.wt, {"src/a.py": "x = 4\n"})
        green = self.heavy("pytest", "tests/test_a.py", "-q")
        self.assertIn("10 passed", green.stdout)
        self.assertIn("流水 #2", green.stderr)
        first, second = self.runs()
        self.assertEqual((first["worktree"], first["branch"], first["command"], first["exit_code"]),
                         (os.path.realpath(self.wt), f"task/{SLUG}-w1", "pytest tests/test_a.py -q", 1))
        self.assertIn("1 failed", first["summary_line"])
        self.assertEqual(second["exit_code"], 0)
        self.assertIn("10 passed", second["summary_line"])
        self.assertIn("10 passed", second["counts"][-1])  # 计数字段同 accept 的 verify（tally_fields）
        self.assertEqual((first["tests"], second["tests"]),
                         ({"passed": 10, "failed": 1, "skipped": 0}, {"passed": 10, "failed": 0, "skipped": 0}))
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        res = self.accept("--slug", SLUG, "--name", "w1", "--brief", "--verify", "tsc --noEmit")
        runs = res["worker_runs"]
        self.assertEqual(runs["runs"], 2)
        self.assertEqual(len(runs["passed_on_current"]), 1)
        self.assertIn("#2", runs["passed_on_current"][0])
        (rg,) = runs["red_then_green"]
        self.assertIn("pytest tests/test_a.py -q", rg)
        self.assertIn("#1", rg)
        self.assertIn("#2", rg)
        self.assertIn("现在的代码", rg)

    def test_heavy_streams_stay_separate(self) -> None:
        """记流水时 stdout、stderr 分开原样转发，退出码照旧"""
        p = self.heavy(sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)",
                       rc=3)
        self.assertEqual(p.stdout, "out\n")
        self.assertIn("err\n", p.stderr)
        self.assertIn("流水 #1", p.stderr)
        (row,) = self.runs()
        self.assertEqual((row["exit_code"], row["counts"], "tests" in row), (3, [], False))  # 认不出测试框架：没有 tests

    def test_no_run_log_outside_task_worktree(self) -> None:
        """分支不是 task/<slug>… 的仓库、不在 git 里的目录：不记流水，accept 写明没有流水的原因"""
        self.git(self.wt, "checkout", "-q", "-b", "feature/x")
        self.heavy("pytest", "tests/test_ok.py", "-q")
        self.heavy("pytest", "tests/test_ok.py", "-q", cwd=self.tmp)
        self.assertEqual(list(self.state.glob("*/heavy-runs.jsonl")), [])
        res = self.accept("--slug", SLUG, "--name", "w1", "--verify", "tsc --noEmit")
        self.assertEqual(res["worker_runs"]["runs"], 0)
        self.assertIn("task/", res["worker_runs"]["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
