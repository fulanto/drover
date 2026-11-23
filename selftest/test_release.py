"""释放空闲的 worker：drover release（手动）、release --idle（闲置扫描）和它的 tick、prompt / brief --send 自动续上、
watch 的 released、agent-start 记的 worker 记录。

herdr 是假的（selftest_fakes）。worker 背后是用例自己起的真进程组（组长加一个子进程，像 agent 和它的 MCP 子进程；经一个
立刻退出的 launcher 起，父进程是 launchd：被杀以后马上被回收，不留僵尸——僵尸还算"进程组活着"）。假 herdr 里的 agent 记着
这个 pgid：进程组没了，假 herdr 就当它退出了——名字清掉、pane 回到 shell，和真的 herdr 一样。
命令在进程里跑 main()，模块的 time 换成假时钟：闲置的分钟数、等锁、SIGTERM 之后的等待都按假时间走；每次 sleep 另外真睡
一小会儿（SleepyClock），让被杀的进程来得及真的退掉。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import unittest

from selftest_screens import BASH_NAMED, IDLE_AFTER_START
from selftest_support import DROVER, DroverTestCase, FakeClock, events, run_cli, wait_for

MINUTE = 60
TASK = "### 背景\n测试用。\n\n### 要做的\n什么都不做。\n\n### 不做\n不改任何文件。\n"
BACKGROUND_SCREEN = IDLE_AFTER_START + "\n  ⏵⏵ accept edits on (shift+tab to cycle) · 1 shell\n"
API_ERROR_SCREEN = "⏺ API Error: 503 upstream connect error\n" + IDLE_AFTER_START
# 假 agent：可选忽略 SIGTERM（子进程继承），起一个子进程，把两个 pid 写进 ready 文件，然后一直睡。
FAKE_AGENT = r'''import os, signal, subprocess, sys, time
ready, ignore_term = sys.argv[1], sys.argv[2] == "1"
if ignore_term:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, "-c", "import time\ntime.sleep(600)"])
with open(ready + ".tmp", "w") as f:
    f.write("%d %d" % (os.getpid(), child.pid))
os.replace(ready + ".tmp", ready)
time.sleep(600)
'''
# 起假 agent 就退出：假 agent 自己是新会话、新进程组的组长，父进程变成 launchd。
LAUNCHER = r'''import subprocess, sys
subprocess.Popen([sys.executable, *sys.argv[1:]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, start_new_session=True)
'''
# 假的后台扫描：把自己的会话 ID 写进标记文件、打一行到 stdout（进日志）。
FAKE_SCAN = r'''import json, os, sys
print("fake scan ran", flush=True)
with open(sys.argv[1] + ".tmp", "w") as f:
    f.write(json.dumps({"sid": os.getsid(0)}))
os.replace(sys.argv[1] + ".tmp", sys.argv[1])
'''


class SleepyClock(FakeClock):
    """假时钟，每次 sleep 另外真睡一小会儿（最多 0.02 秒）：等进程退出的轮询要给它真实的时间。"""

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        time.sleep(min(seconds, 0.02))


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # macOS：组里只剩刚被杀、还没被回收的进程时报 EPERM，回收完才是 ESRCH
        return True
    return True


def kill_group(pgid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)


class ReleaseCase(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.assert_fake_herdr()
        self.agent_script = self.tmp / "fake_agent.py"
        self.agent_script.write_text(FAKE_AGENT)
        self.groups = 0
        self.d = self.load_drover()
        self.clock = self.d.time = SleepyClock()

    # ---- 进程组、假 herdr

    def spawn_group(self, ignore_term: bool = False) -> tuple:
        """起一个真的进程组，返回 (pgid, [组长 pid, 子进程 pid])；用例结束时还在就杀掉。"""
        self.groups += 1
        ready = self.tmp / f"group-{self.groups}.ready"
        subprocess.run([sys.executable, "-c", LAUNCHER, str(self.agent_script), str(ready), "1" if ignore_term else "0"],
                       check=True, timeout=30)
        self.assertTrue(wait_for(ready.exists, timeout=15), "假 agent 没起来")
        leader, child = (int(x) for x in ready.read_text().split())
        self.addCleanup(kill_group, leader)
        return leader, [leader, child]

    def agent_spec(self, kind: str = "claude", ignore_term: bool = False, **fields) -> dict:
        """假 herdr 里一个 agent 的样子：空闲、有会话 ID、背后是一个新起的真进程组（原生安装的样子：组长 argv0 是宿主名）。"""
        pgid, pids = self.spawn_group(ignore_term)
        spec = {"status": "idle", "agent": kind, "session": "sess-1", "screen": IDLE_AFTER_START, "cwd": str(self.work),
                "pgid": pgid, "pids": pids,
                "procs": [{"pid": pids[0], "argv0": kind, "name": "2.1.330", "argv": [kind, "--model", "m"],
                           "cmdline": f"{kind} --model m"},
                          {"pid": pids[1], "argv0": "node", "name": "node", "argv": ["node", "/x/context7-mcp"],
                           "cmdline": "node /x/context7-mcp"}]}
        spec.update(fields)
        return {k: v for k, v in spec.items() if v is not None}

    def starting(self, name: str, **fields) -> dict:
        """下一次 herdr agent start <name> 起出来的 agent（agent_spec）。"""
        spec = self.agent_spec(**fields)
        state = self.herdr_state()
        state.setdefault("starting", {})[name] = spec
        self.set_herdr_state(state)
        return spec

    def start_worker(self, name: str = "w1", pane: str = "w1:p1", slug: str = "s1", args: tuple = (),
                     prompted: bool = True, **fields) -> dict:
        """经 drover agent-start 起一个 worker（写 worker 记录走的就是真的那条路），返回假 herdr 里它的样子。
        prompted：再经 drover prompt 给它发一次话、让它回到原来的状态（干完了第一份活的样子；没发过话的不会被释放）。"""
        spec = self.starting(name, **fields)
        self.cli("agent-start", "--name", name, "--kind", spec["agent"], "--slug", slug, "--pane", pane, *args)
        if prompted:
            self.cli("prompt", "--name", name, "--text", "第一份活")
            state = self.herdr_state()
            state["agents"][name]["status"] = spec["status"]
            state["prompts"] = []
            self.set_herdr_state(state)
        return spec

    def set_agent(self, name: str, **fields) -> None:
        state = self.herdr_state()
        state["agents"][name].update(fields)
        self.set_herdr_state(state)

    def set_state(self, **fields) -> None:
        state = self.herdr_state()
        state.update(fields)
        self.set_herdr_state(state)

    def calls(self, *prefix: str) -> list:
        return [c for c in self.herdr_calls() if c[:len(prefix)] == list(prefix)]

    # ---- drover

    def cli(self, *argv: str, rc: int = 0) -> dict:
        code, out, err = run_cli(self.d, *argv)
        self.assertEqual(code, rc, f"drover {' '.join(argv)}\nstdout:\n{out}\nstderr:\n{err}")
        return json.loads(out)

    def record(self, name: str = "w1", slug: str = "s1") -> dict:
        return json.loads((self.state / slug / f"{name}.agent.json").read_text())

    def release(self, name: str = "w1", *args: str, rc: int = 0) -> dict:
        return self.cli("release", "--name", name, *args, rc=rc)

    def released_worker(self, name: str = "w1", pane: str = "w1:p1", **fields) -> dict:
        """起一个 worker 再手动放掉，返回它原来在假 herdr 里的样子。"""
        spec = self.start_worker(name, pane, **fields)
        self.release(name)
        return spec

    def hold_lock(self, name: str):
        """测试自己拿住 locks/<name>（flock），返回打开的文件；关掉就放开。"""
        path = self.state / "locks" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+")
        fcntl.flock(fh, fcntl.LOCK_EX)
        self.addCleanup(fh.close)
        return fh


# ---------------------------------------------------------------- agent-start 记的 worker 记录


class WorkerRecordTests(ReleaseCase):
    def test_agent_start_writes_record(self) -> None:
        """agent-start 起好以后写 <slug>/<名>.agent.json：宿主、补完默认值的 model / effort、--add-dir、pane、herdr 报的
        工作目录和会话 ID、state running；返回里带 record 路径"""
        extra = self.tmp / "other-wt"
        spec = self.starting("w1")
        out = self.cli("agent-start", "--name", "w1", "--kind", "claude", "--slug", "s1", "--pane", "w1:p1",
                       "--model", "sonnet", "--add-dir", str(extra))
        self.assertEqual(out["record"], str(self.state / "s1" / "w1.agent.json"))
        rec = self.record()
        self.assertGreater(rec.pop("started_at"), 0)
        self.assertEqual(rec, {"name": "w1", "slug": "s1", "kind": "claude", "model": "sonnet", "effort": "xhigh",
                               "add_dirs": [str(extra)], "pane_id": "w1:p1", "cwd": spec["cwd"],
                               "session_id": "sess-1", "state": "running"})

    def test_stuck_start_still_recorded(self) -> None:
        """卡在不认识的启动弹窗上（ok 是 false，agent 还活着）也写记录：编排者处理完它照常干活，之后要能被释放"""
        self.starting("w1", status="blocked", screen=BASH_NAMED)
        out = self.cli("agent-start", "--name", "w1", "--kind", "claude", "--slug", "s1", "--pane", "w1:p1", rc=1)
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(self.record()["state"], "running")

    def test_claude_ready_only_after_session_starts(self) -> None:
        """claude 起到 idle 以后还要等会话开始（herdr 报出会话 ID）才算就绪：处理完启动弹框 herdr 就报 idle 了，这时发的话
        会留在输入框里。等到了记进记录；没装状态 hook 的机器上一直等不到，等满上限照常返回、记录里没有会话 ID"""
        self.starting("w1", session="sess-late", session_after=4)
        out = self.cli("agent-start", "--name", "w1", "--kind", "claude", "--slug", "s1", "--pane", "w1:p1")
        self.assertEqual((out["ok"], out["session_id"], self.record()["session_id"]), (True, "sess-late", "sess-late"))
        self.assertGreaterEqual(len(self.calls("agent", "get", "w1")), 4)
        self.d.SESSION_START_WAIT = 3
        self.starting("w2", session="")
        before = self.clock.elapsed()
        out = self.cli("agent-start", "--name", "w2", "--kind", "claude", "--slug", "s1", "--pane", "w1:p2")
        self.assertTrue(out["ok"])
        self.assertNotIn("session_id", self.record("w2"))
        self.assertGreaterEqual(self.clock.elapsed() - before, 3)

    def test_resume_flag_restarts_exited_worker(self) -> None:
        """worker 自己退出了（gone）：agent-start --resume 在原 pane 按记下的会话 ID 起回来；发过话这件事跟着会话留在记录里"""
        spec = self.start_worker("w1", session="sess-1")
        kill_group(spec["pgid"])
        self.assertTrue(wait_for(lambda: not group_alive(spec["pgid"]), timeout=5))
        self.starting("w1", session="sess-1")
        out = self.cli("agent-start", "--name", "w1", "--slug", "s1", "--pane", "w1:p1", "--resume")
        self.assertEqual((out["ok"], out["kind"], out["kind_source"]), (True, "claude", "record"))
        self.assertEqual(self.calls("agent", "start", "w1")[-1][-2:], ["--resume", "sess-1"])
        self.assertGreater(self.record()["prompted_at"], 0)
        self.assertTrue(self.release()["ok"])

    def test_resume_flag_uses_recorded_session(self) -> None:
        """agent-start --resume：这个任务里记着会话 ID 的按 ID 续（claude --resume <id>、codex resume <id>，宿主也取记录的）；
        没有记录的照旧 --continue / resume --last"""
        self.start_worker("w1", session="sess-claude")
        self.start_worker("w2", "w1:p2", kind="codex", session="sess-codex")
        planned = {}
        for name, kind in (("w1", ()), ("w2", ()), ("w3", ("--kind", "claude")), ("w4", ("--kind", "codex"))):
            out = self.cli("agent-start", "--name", name, "--slug", "s1", "--pane", "w9:p1", "--resume", "--dry-run", *kind)
            planned[name] = out["planned"][0]
        self.assertTrue(planned["w1"].endswith("--resume sess-claude"), planned["w1"])
        self.assertIn("--kind codex --pane w9:p1 --timeout 120000 -- resume sess-codex -a on-request", planned["w2"])
        self.assertTrue(planned["w3"].endswith("--continue"), planned["w3"])
        self.assertIn("-- resume --last -a on-request", planned["w4"])

    def test_released_name_not_reusable_by_other_task(self) -> None:
        """别的任务释放了的 worker 的名字：它的 pane 还在时不让另一个任务用（旧编排者再发话会发给新的那个）；pane 在不在
        查不到（herdr 调用失败）时也不让、报错写的是调用失败；pane 已经不在了才放行"""
        self.released_worker("w1")
        self.starting("w1")
        args = ("agent-start", "--name", "w1", "--kind", "claude", "--slug", "s2", "--pane", "w2:p1")
        out = self.cli(*args, rc=1)
        self.assertIn("任务 s1 释放了的 worker", out["error"])
        self.set_state(fail={"pane process-info": {"code": "server_unavailable", "message": "boom"}})
        out = self.cli(*args, rc=1)
        self.assertIn("server_unavailable", out["error"])
        self.assertIn("稍后重试", out["error"])
        self.assertEqual(self.calls("agent", "start", "w1")[1:], [])  # 只有最开始 s1 的那一次
        self.set_state(fail={}, shells=[])
        self.assertTrue(self.cli(*args)["ok"])
        self.assertEqual(self.record("w1", "s2")["pane_id"], "w2:p1")


# ---------------------------------------------------------------- 手动释放


class ManualReleaseTests(ReleaseCase):
    def test_release_stops_group_keeps_pane(self) -> None:
        """空闲的 worker：整个前台进程组（agent 和它的子进程）都退了，pane 还在、回到 shell，herdr 里名字清掉；记录是
        released，会话 ID 取 herdr 这时报的（起来以后换过会话的也对）；不关 pane、不删 worktree。再放一次什么都不做"""
        spec = self.start_worker("w1")
        self.set_agent("w1", session="sess-2")
        out = self.release()
        self.assertEqual((out["ok"], out["signal"], out["session_id"], out["reason"], out["pane_id"]),
                         (True, "SIGTERM", "sess-2", "manual", "w1:p1"))
        self.assertNotIn("warnings", out)
        self.assertTrue(wait_for(lambda: not group_alive(spec["pgid"]), timeout=5))
        for pid in spec["pids"]:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        state = self.herdr_state()
        self.assertNotIn("w1", state["agents"])
        self.assertEqual(state["shells"], ["w1:p1"])
        rec = self.record()
        self.assertEqual((rec["state"], rec["release_reason"], rec["session_id"], rec["pane_id"]),
                         ("released", "manual", "sess-2", "w1:p1"))
        self.assertGreater(rec["released_at"], 0)
        self.assertEqual(self.calls("pane", "close") + self.calls("worktree", "remove"), [])
        again = self.release()
        self.assertEqual((again["ok"], again.get("already_released"), again["session_id"]), (True, True, "sess-2"))

    def test_refused_unless_idle(self) -> None:
        """在干活、卡在确认框、挂着后台命令、停在接口错误上：都拒绝并写明原因，进程不动、记录不变"""
        spec = self.start_worker("w1")
        cases = (({"status": "working"}, "working", "在干活"),
                 ({"status": "idle", "screen": BASH_NAMED}, "blocked", "卡在确认框上"),
                 ({"status": "done", "screen": BACKGROUND_SCREEN}, "waiting-background(1)", "挂着后台命令"),
                 ({"status": "idle", "screen": API_ERROR_SCREEN}, "api-error", "接口错误"))
        for fields, status, why in cases:
            with self.subTest(status):
                self.set_agent("w1", **fields)
                out = self.release(rc=1)
                self.assertEqual((out["ok"], out["status"]), (False, status))
                self.assertIn(why, out["error"])
                self.assertTrue(group_alive(spec["pgid"]))
                self.assertEqual(self.record()["state"], "running")

    def test_no_session_refused(self) -> None:
        """herdr 没报会话 ID、agent-start 也没记下：不放（放了只能按工作目录取最近一次会话续，会续错），原因里写怎么查；
        之后 herdr 报了会话 ID（codex 跑完第一轮）就能放"""
        spec = self.start_worker("w1", kind="codex", session=None)
        self.assertNotIn("session_id", self.record())
        out = self.release(rc=1)
        self.assertFalse(out["ok"])
        for text in ("没有会话 ID", "herdr integration status", "herdr integration install codex"):
            self.assertIn(text, out["error"])
        self.assertTrue(group_alive(spec["pgid"]))
        self.assertEqual(self.record()["state"], "running")
        self.set_agent("w1", session="sess-late")
        self.assertEqual(self.release()["session_id"], "sess-late")

    def test_never_prompted_not_released(self) -> None:
        """没经 drover 发过话的 worker 不放：会话里没有内容，按 ID 续不上（claude 报 No conversation found）；发过话以后才放"""
        spec = self.start_worker("w1", prompted=False)
        out = self.release(rc=1)
        self.assertIn("还没经 drover 给 w1 发过话", out["error"])
        self.assertTrue(group_alive(spec["pgid"]))
        self.cli("prompt", "--name", "w1", "--text", "第一份活")
        self.assertGreater(self.record()["prompted_at"], 0)
        self.set_agent("w1", status="done")
        self.assertTrue(self.release()["ok"])

    def test_unrecognized_group_refused(self) -> None:
        """pane 的前台进程组里认不出这个宿主（herdr 的认定过时了，pane 里在跑别的）：不发信号，原因里列出看到的进程"""
        spec = self.start_worker("w1")
        self.set_agent("w1", procs=[{"pid": spec["pgid"], "argv0": "vim", "name": "vim", "argv": ["vim", "notes.txt"],
                                     "cmdline": "vim notes.txt"}])
        for extra in ((), ("--dry-run",)):
            out = self.release("w1", *extra, rc=1)
            self.assertIn("认不出 claude", out["error"])
            self.assertIn(f"pid {spec['pgid']} vim notes.txt", out["error"])
        self.assertTrue(group_alive(spec["pgid"]))
        self.assertEqual(self.record()["state"], "running")

    def test_host_group_install_variants(self) -> None:
        """进程组核对不看装法：原生安装（组长 argv0 是宿主名）、npm 装的（node 跑 …/bin/claude）、codex 的 npm 包再起原生
        二进制（子进程 argv0 是 …/codex）都认；pane 在 shell、组里没有这个宿主、宿主不对不认"""
        def info(*procs: dict, pgid: int = 500) -> dict:
            return {"shell_pid": 400, "foreground_process_group_id": pgid, "foreground_processes": list(procs)}

        native = {"pid": 500, "argv0": "claude", "name": "2.1.330", "argv": ["claude", "--model", "opus"]}
        npm = {"pid": 500, "argv0": "node", "name": "node", "argv": ["node", "/usr/local/bin/claude", "--model", "opus"]}
        flags = {"pid": 500, "argv0": "node", "name": "node", "argv": ["node", "--no-warnings", "/opt/bin/claude"]}
        wrapper = {"pid": 500, "argv0": "node", "name": "node", "argv": ["node", "/opt/homebrew/bin/codex"]}
        vendored = {"pid": 501, "argv0": "/opt/lib/node_modules/@openai/codex/vendor/codex/codex", "name": "codex"}
        mcp = {"pid": 502, "argv0": "context7-mcp", "name": "node"}  # 真的 herdr 有的进程没有 argv
        cases = ((info(native, mcp), "claude", 500), (info(npm), "claude", 500), (info(flags), "claude", 500),
                 (info(wrapper, vendored), "codex", 500), (info(mcp, vendored), "codex", 500),
                 (info(native), "codex", None), (info(mcp), "claude", None),
                 (info({"pid": 500, "argv0": "vim", "name": "vim", "argv": ["vim", "x"]}), "claude", None),
                 (info(native, pgid=400), "claude", None), ({}, "claude", None))
        for i, (process_info, kind, expected) in enumerate(cases):
            with self.subTest(i):
                self.assertEqual(self.d.host_group(process_info, kind), expected)

    def test_sigkill_when_sigterm_ignored(self) -> None:
        """SIGTERM 之后等满还在（进程组忽略它）：SIGKILL 整个组"""
        spec = self.start_worker("w1", ignore_term=True)
        self.d.RELEASE_TERM_SECONDS = 2  # 假时间；每 0.2 秒看一次，真实时间里每次 sleep 在 macOS 上可能被拉长到 0.1 秒以上
        out = self.release()
        self.assertEqual(out["signal"], "SIGKILL")
        self.assertTrue(wait_for(lambda: not group_alive(spec["pgid"]), timeout=5))
        self.assertGreaterEqual(self.clock.elapsed(), 2)

    def test_record_released_before_signal(self) -> None:
        """记录先写成 released 再发信号（进程一退名字就没了，watch 这时读到的已经是 released）；没退掉的把记录改回原样"""
        spec = self.start_worker("w1")
        before = self.record()
        seen = []
        real = self.d.stop_group

        def stop(pgid: int) -> str:
            seen.append((self.record()["state"], group_alive(pgid)))
            raise self.d.DroverError("没退掉")

        self.d.stop_group = stop
        out = self.release(rc=1)
        self.assertEqual(seen, [("released", True)])
        self.assertEqual(out["error"], "没退掉")
        self.assertEqual(self.record(), before)
        self.assertTrue(group_alive(spec["pgid"]))
        self.d.stop_group = real
        self.assertTrue(self.release()["ok"])

    def test_only_agent_start_workers(self) -> None:
        """没有 agent-start 记录的（编排者、复审、自己开的 agent）不放；名字现在指的不是记录的那个 pane / 宿主也不碰"""
        orch = self.agent_spec()
        self.set_agents(**{"orch-t1": dict(orch, pane_id="w9:p1")})
        out = self.release("orch-t1", rc=1)
        self.assertIn("没有 agent-start 记录", out["error"])
        spec = self.start_worker("w1")
        self.set_agent("w1", pane_id="w7:p3")
        out = self.release(rc=1)
        self.assertIn("不是 agent-start 记下的那个", out["error"])
        self.assertTrue(group_alive(orch["pgid"]) and group_alive(spec["pgid"]))

    def test_herdr_failure_refused(self) -> None:
        """herdr pane process-info 调用失败：不当成 pane 回到 shell 或不在了，直接拒绝、写明是调用失败；agent get 失败同样"""
        spec = self.start_worker("w1")
        for command in ("pane process-info", "agent get"):
            with self.subTest(command):
                self.set_state(fail={command: {"code": "server_unavailable", "message": "boom"}})
                out = self.release(rc=1)
                self.assertIn("server_unavailable", out["error"])
                self.assertIn("稍后重试", out["error"])
        self.assertTrue(group_alive(spec["pgid"]))
        self.assertEqual(self.record()["state"], "running")

    def test_dry_run_and_exited_worker(self) -> None:
        """--dry-run 只列要做的事，不发信号、不改记录；worker 自己退出了（不是释放的）时报没有可放的进程"""
        spec = self.start_worker("w1")
        out = self.release("w1", "--dry-run")
        self.assertTrue(out["ok"] and out["dry_run"])
        self.assertIn(f"kill -TERM -{spec['pgid']}", " ".join(out["planned"]))
        self.assertTrue(group_alive(spec["pgid"]))
        self.assertEqual(self.record()["state"], "running")
        kill_group(spec["pgid"])
        self.assertTrue(wait_for(lambda: not group_alive(spec["pgid"]), timeout=5))
        self.assertIn("已经退出了", self.release(rc=1)["error"])

    def test_usage(self) -> None:
        """--name 和 --idle 必须且只能给一个；--idle 不和 --slug 一起用"""
        for argv in (("release",), ("release", "--name", "w1", "--idle"), ("release", "--idle", "--slug", "s1")):
            with self.subTest(argv):
                self.assertEqual(run_cli(self.d, *argv)[0], 2)


# ---------------------------------------------------------------- 发话时自动续上


class ResumeTests(ReleaseCase):
    def start_calls(self, name: str = "w1") -> list:
        """herdr agent start <name> 每次交给宿主的参数（-- 后面的）。"""
        return [c[c.index("--") + 1:] for c in self.calls("agent", "start", name)]

    def prompts(self) -> list:
        return self.herdr_state().get("prompts", [])

    def test_prompt_resumes_in_same_pane(self) -> None:
        """prompt 发给已释放的 worker：先在原 pane 起回来——参数和 agent-start 时一字不差，加上 --resume <释放时的会话 ID>
        ——等它的会话开始了再把话发出去；返回 resumed，记录回到 running。它在的时候再发不会再起一次"""
        extra = self.tmp / "other-wt"
        self.released_worker("w1", args=("--model", "sonnet", "--effort", "low", "--add-dir", str(extra)))
        self.starting("w1", session="sess-1", session_after=5)
        out = self.cli("prompt", "--name", "w1", "--text", "接着做")
        self.assertEqual((out["ok"], out["outcome"], out["resumed"]), (True, "started", True))
        calls = self.herdr_calls()
        restart = max(i for i, c in enumerate(calls) if c[:3] == ["agent", "start", "w1"])
        sent = next(i for i, c in enumerate(calls) if c[:3] == ["agent", "prompt", "w1"] and c[3] == "接着做")
        self.assertGreaterEqual(sum(1 for c in calls[restart:sent] if c[:3] == ["agent", "get", "w1"]), 5)
        first, second = self.start_calls()
        self.assertEqual(first, ["--model", "sonnet", "--effort", "low", "--permission-mode", "acceptEdits",
                                 "--name", "worker-s1", "--add-dir", str(self.state / "s1"), "--add-dir", str(extra)])
        self.assertEqual(second, [*first, "--resume", "sess-1"])
        self.assertEqual([c[c.index("--pane") + 1] for c in self.calls("agent", "start", "w1")], ["w1:p1", "w1:p1"])
        self.assertEqual(self.prompts(), [["w1", "接着做"]])
        rec = self.record()
        self.assertEqual(rec["state"], "running")
        self.assertGreater(rec["resumed_at"], 0)
        again = self.cli("prompt", "--name", "w1", "--text", "还有一件")
        self.assertNotIn("resumed", again)
        self.assertEqual(len(self.start_calls()), 2)
        self.assertEqual(self.prompts(), [["w1", "接着做"], ["w1", "还有一件"]])

    def test_unsubmitted_prompt_after_resume_gets_enter(self) -> None:
        """刚续上就发的话留在输入框里没提交（herdr 报 agent_prompt_stalled，agent 还是 idle）：补一次回车提交它，算发出去了；
        屏幕上是确认框时不按回车、照原样报没发成；没续过的 agent 发话没成也不动它（照旧把 herdr 的结果报回去）"""
        enter = ["agent", "send-keys", "w1", "enter"]
        self.released_worker("w1")
        self.starting("w1", stall_prompts=1)
        out = self.cli("prompt", "--name", "w1", "--text", "接着做")
        self.assertEqual((out["ok"], out["outcome"], out["resumed"], out["submitted_with"]),
                         (True, "started", True, "extra_enter"))
        self.assertEqual((self.prompts(), self.herdr_calls().count(enter)), ([["w1", "接着做"]], 1))
        self.set_agent("w1", status="idle", stall_prompts=1)
        out = self.cli("prompt", "--name", "w1", "--text", "它在的时候", rc=1)
        self.assertEqual((out["outcome"], self.herdr_calls().count(enter)), ("agent_prompt_stalled", 1))
        self.set_agent("w1", pending_input=None)
        self.release()
        self.starting("w1", stall_prompts=1, screen=BASH_NAMED)
        out = self.cli("prompt", "--name", "w1", "--text", "弹着框", rc=1)
        self.assertEqual((out["ok"], out["outcome"], out["resumed"]), (False, "agent_prompt_stalled", True))
        self.assertEqual((self.prompts(), self.herdr_calls().count(enter)), ([["w1", "接着做"]], 1))

    def test_codex_resume_command(self) -> None:
        """codex worker 续上：codex resume <会话 ID> 加原来的选项"""
        self.released_worker("w1", kind="codex", session="sess-cx")
        self.starting("w1", kind="codex", session="sess-cx")
        self.assertTrue(self.cli("prompt", "--name", "w1", "--text", "接着做")["resumed"])
        first, second = self.start_calls()
        self.assertEqual(second, ["resume", "sess-cx", *first])
        self.assertEqual(first[:4], ["-a", "on-request", "-s", "workspace-write"])

    def test_session_follows_each_release(self) -> None:
        """连着两轮：续上以后宿主换了会话 ID，第二次释放记的是新的，第二次续按新的续"""
        self.released_worker("w1", session="sess-1")
        self.starting("w1", session="sess-2")
        self.cli("prompt", "--name", "w1", "--text", "第一轮")
        self.set_agent("w1", status="idle")
        self.assertEqual(self.release()["session_id"], "sess-2")
        self.starting("w1", session="sess-2")
        self.cli("prompt", "--name", "w1", "--text", "第二轮")
        self.assertEqual([args[-2:] for args in self.start_calls()[1:]], [["--resume", "sess-1"], ["--resume", "sess-2"]])
        self.assertEqual([text for _, text in self.prompts()], ["第一轮", "第二轮"])

    def test_brief_send_resumes(self) -> None:
        """brief --send 发给已释放的 worker：不写 --kind 时宿主取 worker 记录的（herdr 里暂时没有它），发之前先续上"""
        wt = self.work / "app"
        self.init_repo(wt, {"a.py": "x = 1\n"})
        self.released_worker("w1", kind="codex", session="sess-cx")
        self.starting("w1", kind="codex", session="sess-cx")
        deps, task = self.tmp / "deps.json", self.tmp / "task.md"
        deps.write_text(json.dumps({"worktree_path": str(wt), "summary_for_prompt": "- 无", "items": []}))
        task.write_text(TASK)
        out = self.cli("brief", "--slug", "s1", "--name", "w1", "--repo", str(wt), "--wt", str(wt), "--deps-json", str(deps),
                       "--task-file", str(task), "--goal", "自测目标", "--evidence", "跑一遍：通过", "--send")
        self.assertEqual((out["kind"], out["kind_source"]), ("codex", "record"))
        self.assertEqual((out["sent"]["ok"], out["sent"]["resumed"]), (True, True))
        self.assertEqual(self.start_calls()[1][:2], ["resume", "sess-cx"])
        self.assertEqual([name for name, _ in self.prompts()], ["w1"])

    def test_pane_gone(self) -> None:
        """原 pane 不在了（herdr 明确报 pane_not_found）：不发、报错，写清在 worktree 里新开 pane 用 agent-start --resume"""
        self.released_worker("w1")
        self.set_state(shells=[])
        out = self.cli("prompt", "--name", "w1", "--text", "接着做", rc=1)
        self.assertEqual((out["ok"], out["outcome"], out["status"]), (False, "resume_failed", "released"))
        self.assertIn("pane w1:p1，这个 pane 已经不在了", out["error"])
        self.assertIn("drover agent-start --name w1 --slug s1 --kind claude --pane <pane> --resume", out["error"])
        self.assertEqual((len(self.start_calls()), self.prompts()), (1, []))

    def test_herdr_failure_is_not_pane_gone(self) -> None:
        """查 pane 时 herdr 调用失败：不说 pane 不在了，报的是调用失败、稍后重试；herdr 好了再发照常续上"""
        self.released_worker("w1")
        self.starting("w1")
        self.set_state(fail={"pane process-info": {"code": "server_unavailable", "message": "boom"}})
        out = self.cli("prompt", "--name", "w1", "--text", "接着做", rc=1)
        self.assertEqual(out["outcome"], "resume_failed")
        self.assertNotIn("不在了", out["error"])
        self.assertIn("server_unavailable", out["error"])
        self.assertIn("稍后重试", out["error"])
        self.assertEqual((len(self.start_calls()), self.prompts()), (1, []))
        self.set_state(fail={})
        self.assertTrue(self.cli("prompt", "--name", "w1", "--text", "接着做")["resumed"])

    def test_agent_get_failure_does_not_resume(self) -> None:
        """判断"它是不是已经退了"要 herdr 明确说没有这个名字：agent get 失败时不去续，照常发（发不出去按 herdr 的错报）"""
        self.released_worker("w1")
        self.starting("w1")
        self.set_state(fail={"agent get": {"code": "server_unavailable", "message": "boom"}})
        out = self.cli("prompt", "--name", "w1", "--text", "接着做", rc=1)
        self.assertEqual(out["outcome"], "agent_not_found")
        self.assertEqual(len(self.start_calls()), 1)

    def test_pane_busy(self) -> None:
        """原 pane 里在跑别的程序：不在上面起 agent，报错里列出看到的进程"""
        self.released_worker("w1")
        self.set_state(pane_procs={"w1:p1": {"pgid": 777, "procs": [{"pid": 777, "argv0": "vim", "cmdline": "vim a.txt"}]}})
        out = self.cli("prompt", "--name", "w1", "--text", "接着做", rc=1)
        self.assertIn("在跑别的程序（pid 777 vim a.txt）", out["error"])
        self.assertEqual((len(self.start_calls()), self.prompts()), (1, []))

    def test_workers_without_record_unaffected(self) -> None:
        """没有 worker 记录的 agent（编排者、这版之前起的 worker）发话照旧，不查 pane、不起 agent"""
        self.set_agents(old={"status": "idle", "agent": "claude"})
        out = self.cli("prompt", "--name", "old", "--text", "你好")
        self.assertEqual((out["ok"], out["outcome"]), (True, "started"))
        self.assertEqual(self.calls("pane", "process-info") + self.calls("agent", "start"), [])


# ---------------------------------------------------------------- 释放和发话互斥


class LockTests(ReleaseCase):
    def test_prompt_waits_for_release(self) -> None:
        """正在释放（锁被拿着）时进来的 prompt 等它放完再续上、再发；一直等不到就不发，返回 locked"""
        self.released_worker("w1")
        self.starting("w1")
        fh = self.hold_lock("agent-w1.lock")
        self.d.AGENT_LOCK_WAIT = 0.3
        out = self.cli("prompt", "--name", "w1", "--text", "接着做", rc=1)
        self.assertEqual((out["ok"], out["outcome"]), (False, "locked"))
        self.assertEqual(self.herdr_state().get("prompts", []), [])
        self.d.AGENT_LOCK_WAIT = 120
        released_at = []
        timer = threading.Timer(0.3, lambda: (released_at.append(time.time()), fh.close()))
        timer.start()
        self.addCleanup(timer.cancel)
        out = self.cli("prompt", "--name", "w1", "--text", "接着做")
        self.assertTrue(out["resumed"])
        self.assertEqual(len(released_at), 1)  # 锁放开以后才往下走的
        self.assertEqual(self.herdr_state()["prompts"], [["w1", "接着做"]])

    def test_release_waits_for_prompt(self) -> None:
        """正在给它发话（锁被拿着）：手动 release 等不到锁就报错、不放；闲置扫描跳过它，不放也不算拒绝"""
        spec = self.start_worker("w1")
        self.hold_lock("agent-w1.lock")
        self.d.RELEASE_LOCK_WAIT = 0.3
        out = self.release(rc=1)
        self.assertIn("正在给 w1 发话或续上", out["error"])
        os.environ["DROVER_IDLE_RELEASE"] = "1"
        self.cli("release", "--idle")
        self.clock.sleep(11 * MINUTE)
        out = self.cli("release", "--idle")
        self.assertEqual((out["released"], out["refused"], list(out["idle_minutes"])), ([], [], ["w1"]))
        self.assertTrue(group_alive(spec["pgid"]))


# ---------------------------------------------------------------- 闲置自动释放


class IdleCase(ReleaseCase):
    def setUp(self) -> None:
        super().setUp()
        os.environ["DROVER_IDLE_RELEASE"] = "1"  # enter_env 换过 os.environ，用例结束还原

    def scan(self, *args: str, after: float = 0) -> dict:
        """过 after 分钟（假时间）再扫一遍。"""
        self.clock.sleep(after * MINUTE)
        return self.cli("release", "--idle", *args)

    def released_names(self, out: dict) -> list:
        return [r["name"] for r in out["released"]]


class IdleScanTests(IdleCase):
    def test_released_after_n_minutes(self) -> None:
        """连续空闲满 10 分钟（默认）才放、没满不放；放掉的 release_reason 是 idle"""
        spec = self.start_worker("w1")
        self.assertEqual(self.scan()["idle_minutes"], {"w1": 0.0})
        out = self.scan(after=9)
        self.assertEqual((out["released"], out["idle_minutes"]), ([], {"w1": 9.0}))
        self.assertTrue(group_alive(spec["pgid"]))
        out = self.scan(after=1.5)
        self.assertEqual(self.released_names(out), ["w1"])
        self.assertEqual(out["released"][0]["reason"], "idle")
        self.assertTrue(wait_for(lambda: not group_alive(spec["pgid"]), timeout=5))
        rec = self.record()
        self.assertEqual((rec["state"], rec["release_reason"], rec["session_id"]), ("released", "idle", "sess-1"))
        self.assertEqual(self.scan(after=1)["idle_minutes"], {})

    def test_configured_minutes_and_off(self) -> None:
        """worker_idle_release_minutes 改 N；0 是关，环境变量 DROVER_IDLE_RELEASE=0 也是关：扫描什么都不做、tick 不起进程"""
        spec = self.start_worker("w1")
        self.d.CONFIG["worker_idle_release_minutes"] = 3
        self.scan()
        self.assertEqual(self.scan(after=2)["released"], [])
        self.d.CONFIG["worker_idle_release_minutes"] = 0
        self.assertEqual((self.scan(after=60).get("off"), self.d.idle_release_tick()), (True, "off"))
        self.d.CONFIG["worker_idle_release_minutes"] = 3
        os.environ["DROVER_IDLE_RELEASE"] = "0"
        self.assertEqual((self.scan(after=60).get("off"), self.d.idle_release_tick()), (True, "off"))
        self.assertTrue(group_alive(spec["pgid"]))
        os.environ["DROVER_IDLE_RELEASE"] = "1"
        self.assertEqual(self.released_names(self.scan()), ["w1"])

    def test_focus_and_activity_restart_the_count(self) -> None:
        """pane 被 focus（用户可能在看）时不算空闲，离开后从头算；中间干过活（herdr 的状态序号变了）也从头算"""
        spec = self.start_worker("w1")
        self.scan()
        self.set_agent("w1", focused=True)
        self.assertEqual(self.scan(after=11)["idle_minutes"], {})
        self.set_agent("w1", focused=False)
        self.assertEqual(self.scan(after=1)["idle_minutes"], {"w1": 0.0})
        self.set_agent("w1", seq=7)  # 干了一轮活又停下
        self.assertEqual(self.scan(after=9)["idle_minutes"], {"w1": 0.0})
        self.assertEqual(self.scan(after=9)["released"], [])
        self.assertTrue(group_alive(spec["pgid"]))
        self.assertEqual(self.released_names(self.scan(after=1.5)), ["w1"])

    def test_only_recorded_workers_in_their_pane(self) -> None:
        """没有 agent-start 记录的不放：编排者（有 orch.json）、复审、用户自己开的 agent；名字被别的 pane 里的 agent 占着
        （和记录的 pane 对不上）也不放。空闲多久都一样"""
        self.start_worker("w1")
        self.start_worker("w2", "w1:p2")
        (self.state / "t1").mkdir()
        (self.state / "t1" / "orch.json").write_text(json.dumps({"name": "orch-t1", "slug": "t1", "kind": "claude"}))
        others = {name: dict(self.agent_spec(), pane_id=pane)
                  for name, pane in (("orch-t1", "w9:p1"), ("w1-rev", "w1:p9"), ("mine", "w8:p1"))}
        state = self.herdr_state()
        state["agents"].update(others)
        state["agents"]["w2"]["pane_id"] = "w5:p5"
        self.set_herdr_state(state)
        self.scan()
        out = self.scan(after=60)
        self.assertEqual((self.released_names(out), out["refused"]), (["w1"], []))
        self.assertEqual(list(self.scan(after=60)["idle_minutes"]), [])
        for name in ("orch-t1", "w1-rev", "mine", "w2"):
            self.assertTrue(group_alive(self.herdr_state()["agents"][name]["pgid"]), name)

    def test_refusals_and_dry_run(self) -> None:
        """满了 N 分钟却不能放的进 refused、写原因（没有会话 ID、没发过话、进程组认不出并列出看到的进程、挂着后台命令），
        下一轮接着是候选；--dry-run 报同样的结果，不发信号、不记候选"""
        ok = self.start_worker("ok")
        self.start_worker("nosess", "w1:p2", kind="codex", session=None)
        vim = self.start_worker("vimmed", "w1:p3")
        self.set_agent("vimmed", procs=[{"pid": vim["pgid"], "argv0": "vim", "cmdline": "vim notes.txt"}])
        self.start_worker("bg", "w1:p4", screen=BACKGROUND_SCREEN)
        self.start_worker("fresh", "w1:p5", prompted=False)
        names = ["bg", "fresh", "nosess", "ok", "vimmed"]
        self.assertEqual(self.scan("--dry-run")["idle_minutes"], dict.fromkeys(names, 0.0))
        self.assertFalse((self.state / "idle-release.json").exists())
        self.scan()
        dry = self.scan("--dry-run", after=11)
        self.assertEqual(self.released_names(dry), ["ok"])
        self.assertTrue(dry["released"][0]["dry_run"])
        reasons = {r["name"]: r["error"] for r in dry["refused"]}
        self.assertEqual(sorted(reasons), ["bg", "fresh", "nosess", "vimmed"])
        self.assertIn("挂着后台命令", reasons["bg"])
        self.assertIn("没有会话 ID", reasons["nosess"])
        self.assertIn("还没经 drover 给 fresh 发过话", reasons["fresh"])
        self.assertIn(f"pid {vim['pgid']} vim notes.txt", reasons["vimmed"])
        self.assertTrue(group_alive(ok["pgid"]))
        self.assertEqual(self.record("ok")["state"], "running")
        real = self.scan()
        self.assertEqual(self.released_names(real), ["ok"])
        self.assertEqual({r["name"]: r["error"] for r in real["refused"]}, reasons)
        self.assertEqual(sorted(self.scan(after=1)["idle_minutes"]), ["bg", "fresh", "nosess", "vimmed"])
        for name in ("nosess", "vimmed", "bg", "fresh"):
            self.assertTrue(group_alive(self.herdr_state()["agents"][name]["pgid"]), name)

    def test_one_scan_at_a_time(self) -> None:
        """扫描一次只跑一个：扫描锁被占着时这次不扫"""
        spec = self.start_worker("w1")
        self.scan()
        self.hold_lock("idle-release.lock")
        out = self.scan(after=11)
        self.assertIn("另一个闲置扫描正在跑", out["skipped"])
        self.assertTrue(group_alive(spec["pgid"]))


class IdleTickTests(IdleCase):
    def setUp(self) -> None:
        super().setUp()
        self.marker = self.tmp / "scan-ran.json"
        fake = self.tmp / "fake_scan.py"
        fake.write_text(FAKE_SCAN)
        self.d.IDLE_RELEASE_ARGV = [sys.executable, str(fake), str(self.marker)]
        self.log = self.state / "idle-release.log"

    def wait_scan(self) -> dict:
        self.assertTrue(wait_for(self.marker.exists, timeout=15), "后台扫描没跑起来")
        info = json.loads(self.marker.read_text())
        self.marker.unlink()
        return info

    def test_tick_spawns_scan_once_a_minute(self) -> None:
        """tick 到点了在后台起扫描（新会话，不等它）、输出进 idle-release.log；一分钟里再调不起；满一分钟再起"""
        self.assertEqual(self.d.idle_release_tick(), "started")
        self.assertNotEqual(self.wait_scan()["sid"], os.getsid(0))
        self.assertTrue(wait_for(lambda: "fake scan ran" in self.log.read_text(), timeout=5))
        self.clock.sleep(59)
        self.assertEqual(self.d.idle_release_tick(), "not_due")
        self.clock.sleep(2)
        self.assertEqual(self.d.idle_release_tick(), "started")
        self.wait_scan()

    def test_log_is_capped(self) -> None:
        """后台扫描的日志超过上限，下次起扫描时先清空（不会无限长）"""
        self.log.write_text("x" * (self.d.BACKGROUND_LOG_MAX + 1))
        self.assertEqual(self.d.idle_release_tick(), "started")
        self.wait_scan()
        self.assertTrue(wait_for(lambda: "fake scan ran" in self.log.read_text(), timeout=5))
        self.assertLess(self.log.stat().st_size, 1000)

    def test_tick_callers(self) -> None:
        """源码里调 idle_release_tick() 的：普通 watch 的每一轮，加上调自动更新 tick 的那四处（watch --main、board 文本 /
        --json / --live、main-register）"""
        callers, current = set(), None
        for line in DROVER.read_text().splitlines():
            m = re.match(r"def (\w+)\(", line)
            if m:
                current = m.group(1)
            elif "idle_release_tick()" in line and not line.lstrip().startswith("#"):
                callers.add(current)
        self.assertEqual(callers, {"cmd_watch", "main_session_seen", "watch_main", "board_live", "cmd_board"})


class IdleEndToEndTests(ReleaseCase):
    def test_watch_tick_runs_real_scan(self) -> None:
        """整条路（真进程）：普通 watch 的 tick → 后台起这份 drover 的 release --idle → 第一遍记下开始空闲的时间，
        下一次 tick 的那一遍把空闲满了的放掉（release_reason idle）；之后 watch 显示 released"""
        spec = self.start_worker("w1")
        config = json.loads((self.state / "config.json").read_text())
        (self.state / "config.json").write_text(json.dumps({**config, "worker_idle_release_minutes": 0.001}))
        env = {"DROVER_IDLE_RELEASE": "1"}
        idle_file, tick_file = self.state / "idle-release.json", self.state / "locks" / "idle-release-tick.json"
        self.drover("watch", "--once", "w1", env=env)
        self.assertTrue(wait_for(idle_file.exists, timeout=20), "第一遍扫描没记下候选")
        self.assertIn("w1", json.loads(idle_file.read_text())["agents"])
        self.assertTrue(group_alive(spec["pgid"]))
        time.sleep(0.2)  # 0.001 分钟 = 0.06 秒
        tick_file.write_text(json.dumps({"at": time.time() - 61}))  # 当作上次 tick 是一分钟前
        self.drover("watch", "--once", "w1", env=env)
        self.assertTrue(wait_for(lambda: self.record().get("state") == "released", timeout=20), "第二遍扫描没放掉")
        self.assertEqual(self.record()["release_reason"], "idle")
        self.assertTrue(wait_for(lambda: not group_alive(spec["pgid"]), timeout=5))
        self.assertEqual(events(self.drover("watch", "--once", "w1", env=env).stdout), ["EVENT w1 released"])


# ---------------------------------------------------------------- watch 的 released


class WatchTests(ReleaseCase):
    FAST = ("--interval", "1")

    def fake_release(self, name: str, slug: str = "s1") -> None:
        """两轮 watch 之间发生的释放：照 drover 的顺序，记录先写成 released，再让进程组退掉。"""
        path = self.state / slug / f"{name}.agent.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "state": "released"}))
        pgid = self.herdr_state()["agents"][name]["pgid"]
        kill_group(pgid)
        self.assertTrue(wait_for(lambda: not group_alive(pgid), timeout=5))

    def test_released_shown_not_gone(self) -> None:
        """已释放的 worker：--once 和启动时显示 released，不是 gone；--until-event 不因为它在启动时就退出（等到 --timeout）"""
        self.released_worker("w1")
        self.assertEqual(self.watch("--once", "w1"), (0, ["EVENT w1 released"], 0))
        code, evs, seconds = self.watch("--until-event", "--timeout", "30", *self.FAST, "w1")
        self.assertEqual((code, evs), (0, ["EVENT w1 released", "EVENT timeout"]))
        self.assertGreaterEqual(seconds, 30)

    def test_becoming_released_is_quiet(self) -> None:
        """idle → released 不输出、不算事件（--until-event 不退出）；续上以后的 working 照常报"""
        self.start_worker("w1")
        code, first, rest = self.watch_then(["--until-event", "--timeout", "20", *self.FAST, "w1"], 1,
                                            lambda: self.fake_release("w1"))
        self.assertEqual((code, first, rest), (0, ["EVENT w1 idle"], ["EVENT timeout"]))

        def resume() -> None:
            state = self.herdr_state()
            state["agents"]["w1"] = {"status": "working", "agent": "claude", "pane_id": "w1:p1", "seq": 9}
            state["shells"] = []
            self.set_herdr_state(state)

        code, first, rest = self.watch_then(["--until-event", "--timeout", "20", *self.FAST, "w1"], 1, resume)
        self.assertEqual((code, first, rest), (0, ["EVENT w1 released"], ["EVENT w1 working"]))

    def test_gone_when_not_released_or_pane_closed(self) -> None:
        """自己退出的 worker（记录不是 released）照旧报 gone、--until-event 启动时就退出；释放时的 pane 被关了（原地续不了）
        也是 gone"""
        spec = self.start_worker("w1")
        kill_group(spec["pgid"])
        self.assertTrue(wait_for(lambda: not group_alive(spec["pgid"]), timeout=5))
        code, evs, seconds = self.watch("--until-event", "--timeout", "30", *self.FAST, "w1")
        self.assertEqual((code, evs), (0, ["EVENT w1 gone"]))
        self.assertLess(seconds, 30)
        self.released_worker("w2", "w1:p2")
        self.assertEqual(self.watch("--once", "w2")[1], ["EVENT w2 released"])
        code, first, rest = self.watch_then(["--until-event", "--timeout", "20", *self.FAST, "w2"], 1,
                                            lambda: self.set_state(shells=[]))
        self.assertEqual((first, rest), (["EVENT w2 released"], ["EVENT w2 gone"]))

    def test_herdr_failure_is_not_gone(self) -> None:
        """查 pane 还在不在时 herdr 调用失败：不知道，不报 gone——启动时按 released，中途沿用上一轮显示的"""
        self.released_worker("w1")
        broken = {"pane list": {"code": "server_unavailable", "message": "boom"}}
        self.set_state(fail=broken)
        self.assertEqual(self.watch("--once", "w1")[1], ["EVENT w1 released"])
        self.set_state(fail={})
        code, first, rest = self.watch_then(["--until-event", "--timeout", "20", *self.FAST, "w1"], 1,
                                            lambda: self.set_state(fail=broken))
        self.assertEqual((first, rest), (["EVENT w1 released"], ["EVENT timeout"]))

    def watch_calls(self, names: list) -> dict:
        """盯 names 跑 10 轮，返回这段时间里各种 herdr 调用的次数。"""
        before = len(self.herdr_calls())
        code, evs, _ = self.watch("--timeout", "9.5", *self.FAST, *names)
        self.assertEqual((code, evs), (0, [f"EVENT {n} released" for n in names] + ["EVENT timeout"]))
        counts: dict = {}
        for call in self.herdr_calls()[before:]:
            counts[" ".join(call[:2])] = counts.get(" ".join(call[:2]), 0) + 1
        return counts

    def test_calls_per_round_do_not_grow(self) -> None:
        """watch 每轮的 herdr 调用次数不随已释放 worker 的个数涨：盯 2 个和盯 6 个都是每轮一次 agent list、一次 pane list"""
        names = [f"w{i}" for i in range(1, 7)]
        for i, name in enumerate(names, 1):
            self.released_worker(name, f"w1:p{i}")
        few, many = self.watch_calls(names[:2]), self.watch_calls(names)
        self.assertEqual(few, {"agent list": 10, "pane list": 10})
        self.assertEqual(many, few)


if __name__ == "__main__":
    unittest.main(verbosity=2)
