"""按任务指定模型：agent-start、orch-start、review 的 --model（和 --effort 一样的优先级）。

优先级：命令行 > 任务默认（orch.json 记的 model / effort：orch-start 时给的，或子编排者从上层、换上的编排者从前任继承来的）
> config.json（claude_model / claude_effort / codex_effort）> 内置默认（claude：opus[1m] + xhigh；codex：xhigh、不传模型）。
任务默认只对编排者那个宿主有效：模型 ID 只对一种宿主有效，宿主不同的 agent（跑 codex 的复审、主会话是 codex 时的
claude worker）不继承。codex 的模型参数是 `-m`（`codex --help` 和 `codex resume --help` 都写着 `-m, --model <MODEL>`）。
没人给 --model 时启动参数和以前一字不差。

dry-run 看计划里 herdr agent start 的 -- 后面那段；要真起的（orch-start 写 orch.json、review 只有真起才拼参数）在进程里跑
（drover_in_process），herdr 是假的（selftest_fakes：agent start 把 -- 后面的参数记进 state 的 agents[名].args）。
"""

from __future__ import annotations

import json
import shlex
import unittest

from selftest_screens import IDLE_AFTER_START
from selftest_support import DroverTestCase

FABLE = "claude-fable-5-2"
GPT = "gpt-5.5"


class ModelFlagTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.request = self.tmp / "request.md"
        self.request.write_text("# 请求\n\n用户原话：测试\n")
        for path in (self.home / ".claude" / "commands" / "drover.md",
                     self.home / ".codex" / "skills" / "drover" / "SKILL.md"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\nname: drover\n---\n")
        state = self.herdr_state()
        state.update(workspaces=[{"label": "orch", "workspace_id": "w9"}], panes={"w1:p1": {"present": False}},
                     starting={})
        self.set_herdr_state(state)
        self.accept = self.tmp / "accept.json"
        self.accept.write_text(json.dumps({"verify": [{"command": "pytest", "ok": True, "exit_code": 0, "seconds": 1,
                                                       "summary_line": "3 passed"}], "graph": {"skipped": True}}))

    # ---- 工具

    def configure(self, **keys: str) -> None:
        config = self.state / "config.json"
        config.write_text(json.dumps({**json.loads(config.read_text()), **keys}))

    def write_orch(self, slug: str, **meta) -> None:
        (self.state / slug).mkdir(parents=True, exist_ok=True)
        (self.state / slug / "orch.json").write_text(json.dumps({"slug": slug, **meta}))

    def orch_meta(self, slug: str) -> dict:
        return json.loads((self.state / slug / "orch.json").read_text())

    def will_start(self, name: str, kind: str) -> None:
        """假 herdr：agent start 这个名字后是空闲的 kind agent"""
        state = self.herdr_state()
        state["starting"][name] = {"status": "idle", "agent": kind, "screen": IDLE_AFTER_START}
        self.set_herdr_state(state)

    def started_args(self, name: str) -> list:
        return self.herdr_state()["agents"][name]["args"]

    def agent_args(self, kind: str, *extra: str, slug: str = "s1") -> list:
        """agent-start --dry-run 计划里交给 claude / codex 的参数"""
        out = self.drover_json("agent-start", "--name", "w1", "--kind", kind, "--slug", slug, "--pane", "w1:p1",
                               "--dry-run", *extra)
        return shlex.split(out["planned"][0].split(" -- ", 1)[1])

    def orch_args(self, slug: str, *extra: str) -> list:
        """orch-start --dry-run 计划里交给 claude / codex 的参数"""
        out = self.drover_json("orch-start", "--slug", slug, "--name", f"orch-{slug}", "--request-file",
                               str(self.request), "--dry-run", *extra)
        return shlex.split(next(p for p in out["planned"] if p.startswith("herdr agent start")).split(" -- ", 1)[1])

    def orch_start(self, slug: str, name: str, kind: str, *extra: str) -> dict:
        """真起编排者（进程里、假 herdr），返回 orch-start 的输出"""
        self.will_start(name, kind)
        p = self.drover_in_process("orch-start", "--slug", slug, "--name", name, "--kind", kind, "--request-file",
                                   str(self.request), *extra)
        out = json.loads(p.stdout)
        self.assertTrue(out["ok"], out)
        return out

    def kickoff(self, name: str) -> str:
        return next(text for who, text in self.herdr_state()["prompts"] if who == name)

    def review_args(self, slug: str, *extra: str) -> list:
        """真起复审（进程里、假 herdr；分屏换成假的，同 test_orch 的 ModelEffortConfigTests），返回交给 codex 的参数"""
        name = f"rev{len(self.herdr_state()['agents'])}"
        self.will_start(name, "codex")
        p = self.drover_in_process("review", "--slug", slug, "--name", name, "--pane", "w1:p1", "--wt", str(self.work),
                                   "--base", "HEAD", "--accept-json", str(self.accept), *extra,
                                   setup=lambda d: setattr(d, "split_pane", lambda pane, wt: "w1:p9"))
        out = json.loads(p.stdout)
        self.assertTrue(out["ok"], out)
        return self.started_args(name)

    # ---- 用例

    def test_model_flag_reaches_launch_args(self) -> None:
        """三个命令带 --model：claude 传 --model <ID>，codex 传 -m <ID>（放在 effort 前面，codex resume 也认）"""
        self.assertEqual(self.agent_args("claude", "--model", FABLE)[:4], ["--model", FABLE, "--effort", "xhigh"])
        self.assertEqual(self.agent_args("codex", "--model", GPT)[-4:],
                         ["-m", GPT, "-c", "model_reasoning_effort=xhigh"])
        resumed = self.agent_args("codex", "--model", GPT, "--resume")
        self.assertEqual((resumed[:2], resumed[-4:]), (["resume", "--last"], ["-m", GPT, "-c", "model_reasoning_effort=xhigh"]))
        self.assertEqual(self.orch_args("t1", "--kind", "claude", "--model", FABLE)[:4],
                         ["--model", FABLE, "--effort", "xhigh"])
        self.assertEqual(self.orch_args("t1", "--kind", "codex", "--model", GPT)[-4:],
                         ["-m", GPT, "-c", "model_reasoning_effort=xhigh"])
        self.assertEqual(self.review_args("s1", "--model", GPT)[-4:], ["-m", GPT, "-c", "model_reasoning_effort=xhigh"])

    def test_precedence(self) -> None:
        """命令行 > 任务 orch.json > config > 内置默认，模型和 effort 各自按这个顺序；claude、codex 各走一遍"""
        cases = {
            "claude": (dict(claude_model="sonnet", claude_effort="medium"), FABLE, "high", ("opus", "max"),
                       lambda a: a[:4], lambda m, e: ["--model", m, "--effort", e],
                       ("sonnet", "medium"), ("opus[1m]", "xhigh")),
            "codex": (dict(codex_effort="medium"), GPT, "low", ("o3", "high"),
                      lambda a: a[a.index("-c") - 2:] if "-m" in a else a[-2:],
                      lambda m, e: [*(["-m", m] if m else []), "-c", f"model_reasoning_effort={e}"],
                      (None, "medium"), (None, "xhigh")),
        }
        for kind, (config, task_model, task_effort, flags, pick, want, by_config, builtin) in cases.items():
            with self.subTest(kind):
                self.configure(**{"claude_model": "", "claude_effort": "", "codex_effort": "", **config})
                self.write_orch("s1", kind=kind, default_kind=kind, model=task_model, effort=task_effort)
                self.assertEqual(pick(self.agent_args(kind)), want(task_model, task_effort))
                self.assertEqual(pick(self.agent_args(kind, "--model", flags[0], "--effort", flags[1])), want(*flags))
                self.write_orch("s1", kind=kind, default_kind=kind)  # 任务没指定
                self.assertEqual(pick(self.agent_args(kind)), want(*by_config))
                self.configure(claude_model="", claude_effort="", codex_effort="")
                self.assertEqual(pick(self.agent_args(kind)), want(*builtin))

    def test_orch_start_records_and_same_host_inherits(self) -> None:
        """orch-start --model --effort：编排者用它起，记进 orch.json，开场 prompt 写明；之后同宿主（claude）的 worker
        不给 --model / --effort 就继承（--kind 不给、按 default_kind 也一样）；codex worker、复审（codex）不继承"""
        self.orch_start("t1", "orch-t1", "claude", "--model", FABLE, "--effort", "max")
        self.assertEqual(self.started_args("orch-t1")[:4], ["--model", FABLE, "--effort", "max"])
        meta = self.orch_meta("t1")
        self.assertEqual((meta["model"], meta["effort"]), (FABLE, "max"))
        self.assertIn(f"--model {FABLE} --effort max", self.kickoff("orch-t1"))
        self.assertEqual(self.agent_args("claude", slug="t1")[:4], ["--model", FABLE, "--effort", "max"])
        out = self.drover_json("agent-start", "--name", "w1", "--slug", "t1", "--pane", "w1:p1", "--dry-run")
        self.assertEqual(shlex.split(out["planned"][0].split(" -- ", 1)[1])[:4], ["--model", FABLE, "--effort", "max"])
        codex = self.agent_args("codex", slug="t1")
        self.assertNotIn("-m", codex)
        self.assertNotIn(FABLE, codex)
        self.assertEqual(codex[-2:], ["-c", "model_reasoning_effort=xhigh"])
        review = self.review_args("t1")
        self.assertNotIn("-m", review)
        self.assertEqual(review[-2:], ["-c", "model_reasoning_effort=xhigh"])

    def test_codex_task_model_reaches_codex_workers_and_review(self) -> None:
        """codex 编排者 --model --effort：codex worker 和复审继承 -m <ID> 和 effort，claude worker 不继承（照旧 config / 内置）"""
        self.orch_start("t2", "orch-t2", "codex", "--model", GPT, "--effort", "high")
        self.assertEqual(self.started_args("orch-t2")[-4:], ["-m", GPT, "-c", "model_reasoning_effort=high"])
        self.assertEqual(self.orch_meta("t2")["model"], GPT)
        self.assertEqual(self.agent_args("codex", slug="t2")[-4:], ["-m", GPT, "-c", "model_reasoning_effort=high"])
        self.assertEqual(self.review_args("t2")[-4:], ["-m", GPT, "-c", "model_reasoning_effort=high"])
        self.assertEqual(self.agent_args("claude", slug="t2")[:4], ["--model", "opus[1m]", "--effort", "xhigh"])
        self.assertEqual(self.review_args("t2", "--model", "o3")[-4:], ["-m", "o3", "-c", "model_reasoning_effort=high"])

    def test_sub_orch_and_replacement_inherit(self) -> None:
        """子编排者（--parent）不给 --model 继承上层的，并记进自己的 orch.json；给了用自己的；宿主不同不继承。
        同一个任务换编排者（--args "resume <slug>"）不给 --model 沿用前任记的"""
        (self.state / "t1").mkdir()
        (self.state / "t1" / "dispatch.md").write_text("# dispatch: t1\n")
        self.orch_start("t1", "orch-t1", "claude", "--model", FABLE, "--effort", "high")
        self.orch_start("t1-shop", "orch-t1-shop", "claude", "--parent", "t1")
        self.assertEqual(self.started_args("orch-t1-shop")[:4], ["--model", FABLE, "--effort", "high"])
        self.assertEqual((self.orch_meta("t1-shop")["model"], self.orch_meta("t1-shop")["effort"]), (FABLE, "high"))
        self.assertIn(f"--model {FABLE}", self.kickoff("orch-t1-shop"))
        self.assertEqual(self.agent_args("claude", slug="t1-shop")[:2], ["--model", FABLE])
        self.assertEqual(self.orch_args("t1-web", "--kind", "claude", "--parent", "t1", "--model", "opus")[:2],
                         ["--model", "opus"])
        other = self.orch_args("t1-api", "--kind", "codex", "--parent", "t1")
        self.assertNotIn("-m", other)
        self.assertEqual(other[-2:], ["-c", "model_reasoning_effort=xhigh"])
        self.orch_start("t1", "orch-t1b", "claude", "--args", "resume t1")
        self.assertEqual(self.started_args("orch-t1b")[:4], ["--model", FABLE, "--effort", "high"])
        self.assertEqual((self.orch_meta("t1")["name"], self.orch_meta("t1")["model"]), ("orch-t1b", FABLE))

    def test_without_model_launch_args_unchanged(self) -> None:
        """没人给 --model（任务 orch.json 没有 model / effort，或是旧版写的没这两个键）：四种启动参数和改之前一字不差，
        开场 prompt 里没有模型那句"""
        s1, heavy = str(self.state / "s1"), str(self.state / "locks" / "heavy")
        want = {
            "agent claude": ["--model", "opus[1m]", "--effort", "xhigh", "--permission-mode", "acceptEdits",
                             "--name", "worker-s1", "--add-dir", s1],
            "agent codex": ["-a", "on-request", "-s", "workspace-write", "--no-daemon", "--no-alt-screen",
                            "--add-dir", s1, "--add-dir", heavy, "-c", "model_reasoning_effort=xhigh"],
            "orch claude": ["--model", "opus[1m]", "--effort", "xhigh", "--permission-mode", "acceptEdits",
                            "--name", "orch-t1", "--add-dir", str(self.state)],
            "orch codex": ["-s", "danger-full-access", "-a", "on-request", "--no-daemon", "--no-alt-screen",
                           "-c", "model_reasoning_effort=xhigh"],
            "review": ["-a", "on-request", "-s", "read-only", "--no-daemon", "--no-alt-screen",
                       "-c", "model_reasoning_effort=xhigh"],
        }

        def actual() -> dict:
            return {"agent claude": self.agent_args("claude"), "agent codex": self.agent_args("codex"),
                    "orch claude": self.orch_args("t1", "--kind", "claude"),
                    "orch codex": self.orch_args("t1", "--kind", "codex"), "review": self.review_args("s1")}

        self.assertEqual(actual(), want)
        self.write_orch("s1", name="orch-s1", kind="claude", default_kind="claude")  # 旧版 orch.json：没有 model / effort
        self.assertEqual(self.agent_args("claude"), want["agent claude"])
        self.orch_start("t3", "orch-t3", "claude")
        self.assertEqual(self.started_args("orch-t3"), want["orch claude"][:7] + ["orch-t3", "--add-dir", str(self.state)])
        self.assertNotIn("--model", self.kickoff("orch-t3"))
        self.assertEqual(self.agent_args("claude", slug="t3"), want["agent claude"][:7] + ["worker-t3", "--add-dir",
                                                                                          str(self.state / "t3")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
