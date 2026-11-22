"""自动更新在 bin/drover 里的部分：tick、主会话规则（drover rules / rules --hook）、
watch --main 的 drover 事件、看板上的 drover 状态。update --auto 本身（假 clone + 本地 bare 远端，跑真的 install）在
tests/test_update.py；main-register 记规则和 main-session.json 在 test_main_register.py；看板 --live 补登记在 test_board.py。

自测环境默认关着自动更新（selftest_support 设了 DROVER_AUTO_UPDATE=0）。测 tick 的用例在进程里打开它，并把后台命令
（AUTO_UPDATE_ARGV）换成一个假脚本：它只把自己的会话 ID、stdin 是什么写进标记文件，不真的更新。
watch --main 的用例在进程里跑、用假时钟（selftest_support 的 watch）；要两个真进程的（会话重开、两个 watch 同时跑）起子进程。
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import unittest

from selftest_support import DROVER, MAIN_PANE, PKG, DroverTestCase, events, wait_for

FAST = ["--interval", "0.2"]
HOUR = 3600
SHA_A, SHA_B, SHA_C = "a" * 40, "b" * 40, "c" * 40  # 不在任何仓库里：版本号算不出来，退回 7 位 sha
BEGIN, END = "<!-- drover:routing:begin -->", "<!-- drover:routing:end -->"
REPLACE = "从现在起以下面为准，替换你上下文里 AGENTS.md drover 区块的旧版本。"
# 假的后台更新：一起来先写 <标记>.started；然后被挡住，等测试放行（<标记>.release 出现，最多 10 秒）——tick 要是等它，
# 就一直等不回来（证明 tick 不等它，不靠"睡得比 1 秒长"）；放行后打一行到 stdout（进日志），把会话 ID、stdin 是不是 /dev/null、
# 是不是被放行的写进标记文件。
FAKE_UPDATE = r'''import json, os, sys, time
marker = sys.argv[1]
open(marker + ".started", "w").close()
deadline = time.time() + 10
while not os.path.exists(marker + ".release") and time.time() < deadline:
    time.sleep(0.01)
print("fake update ran", flush=True)
info = {"sid": os.getsid(0), "stdin_devnull": os.fstat(0).st_rdev == os.stat("/dev/null").st_rdev, "cwd": os.getcwd(),
        "released": os.path.exists(marker + ".release")}
tmp = marker + ".tmp"
with open(tmp, "w") as f:
    f.write(json.dumps(info))
os.replace(tmp, marker)
'''


def rules_block(changed: str = "", extra: int = 0) -> str:
    """一份像样的区块：几节、每节几条规则（一行一条）；changed 换掉"追踪"一节的第二条，extra 在末尾多加几条。"""
    lines = [BEGIN, "<!-- 这一段由 drover 的 install.py 渲染 -->", "", "# drover 路由规则", "",
             "## 会话刚开始", *[f"- 开场第 {i} 条规则，内容不变" for i in range(1, 6)], "",
             "## 追踪与转达", "- 追踪第 1 条", changed or "- 追踪第 2 条：旧的写法",
             *[f"- 追踪第 {i} 条" for i in range(3, 8)], "",
             "## 宿主差异", *[f"- 宿主第 {i} 条" for i in range(1, 6)],
             *[f"- 新加的第 {i} 条" for i in range(extra)], "", END]
    return "\n".join(lines)


class AutoUpdateCase(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.agents_md = self.work / "AGENTS.md"
        self.auto_file = self.state / "auto-update.json"
        self.script = None  # rules / 看板跑哪份 bin/drover：None 是本 worktree 的，make_clone 以后是临时 clone 里的

    def make_clone(self) -> list:
        """临时 clone（link 模式装的样子）：bin/drover 是本 worktree 的，三个提交，第一个打附注 tag v0.1.0，HEAD 是 v0.1.0+2；
        之后 rules / 看板跑它的 bin/drover，版本号按这个 clone 算（不跟本 worktree 的 tag 走）。返回三个提交（完整 sha）。"""
        self.clone = self.tmp / "clone"
        c1 = self.init_repo(self.clone, {"bin/drover": DROVER.read_text()})
        self.git(self.clone, "tag", "-a", "v0.1.0", "-m", "release v0.1.0")
        c2 = self.commit(self.clone, "第 2 个提交", {"a.txt": "2\n"})
        c3 = self.commit(self.clone, "第 3 个提交", {"a.txt": "3\n"})
        self.script = self.clone / "bin" / "drover"
        return [c1, c2, c3]

    def write_block(self, block: str) -> None:
        self.agents_md.write_text("# 我的个人规则\n- 回答要短\n\n" + block + "\n\n## 尾部\n- 个人内容\n")

    def auto_record(self, **fields) -> None:
        self.auto_file.write_text(json.dumps(fields))

    def auto(self) -> dict:
        return json.loads(self.auto_file.read_text()) if self.auto_file.exists() else {}

    def main_rules(self) -> dict:
        return json.loads((self.state / "main-rules.json").read_text())

    def rules(self, *args: str, rc: int = 0, env: dict = None) -> str:
        """drover rules，默认在主会话的 pane 里跑（HERDR_PANE_ID 是 main-session.json 记的 pane；还没有记录就先登记 MAIN_PANE）：
        只有主会话跑的才记成载入时、标成已报。env 给了就用它（别的 pane、普通终端）。"""
        if env is None:
            path = self.state / "main-session.json"
            pane = (json.loads(path.read_text()) if path.exists() else {}).get("pane_id")
            if not pane:
                pane = MAIN_PANE
                path.write_text(json.dumps({"session_id": None, "pane_id": pane, "source": None, "at": time.time()}))
            env = {"HERDR_ENV": "1", "HERDR_PANE_ID": pane}
        return self.drover("rules", *args, rc=rc, cwd=self.work, env=env, script=self.script).stdout


# ---------------------------------------------------------------- tick：开关、间隔、锁、只起后台进程不等它


class TickTests(AutoUpdateCase):
    def setUp(self) -> None:
        super().setUp()
        self.env["DROVER_AUTO_UPDATE"] = "1"
        self.marker = self.tmp / "spawned.json"
        self.started_file = self.tmp / "spawned.json.started"
        self.release_file = self.tmp / "spawned.json.release"
        fake = self.tmp / "fake_update.py"
        fake.write_text(FAKE_UPDATE)
        self.d = self.load_drover()
        self.d.AUTO_UPDATE_ARGV = [sys.executable, str(fake), str(self.marker)]

    def wait_spawned(self) -> dict:
        """等后台进程起来、放行它、等它写标记，返回标记内容（删掉这几个文件，下一次 tick 重新等）。"""
        self.assertTrue(wait_for(self.started_file.exists, timeout=15), "后台进程没跑起来")
        self.release_file.touch()
        self.assertTrue(wait_for(self.marker.exists, timeout=15), "后台进程没写标记")
        info = json.loads(self.marker.read_text())
        self.assertTrue(info["released"], info)  # 是被放行的，不是等满上限自己走的
        for path in (self.marker, self.started_file, self.release_file):
            path.unlink()
        return info

    def test_switch(self) -> None:
        """开关：DROVER_AUTO_UPDATE=0（false / off 也认）、config.json 的 "auto_update": false 都关；关着时不起进程、不写记录"""
        for value in ("0", "false", "OFF"):
            os.environ["DROVER_AUTO_UPDATE"] = value
            self.assertEqual(self.d.auto_update_tick(), "off")
        os.environ["DROVER_AUTO_UPDATE"] = "1"
        self.d.CONFIG["auto_update"] = False
        self.assertEqual(self.d.auto_update_tick(), "off")
        self.assertIn("auto_update", self.d.auto_update_switch())
        time.sleep(0.3)
        self.assertFalse(self.started_file.exists())
        self.assertFalse(self.auto_file.exists())

    def test_spawn_detached_without_waiting(self) -> None:
        """到时间了：tick 起后台进程就返回（假进程被挡着、等测试放行，tick 照样不到 1 秒返回），先写 last_attempt；后台进程
        在新会话里（start_new_session）、stdin 是 /dev/null、stdout 进 auto-update.log、工作目录是状态目录"""
        started = time.time()
        self.assertEqual(self.d.auto_update_tick(), "started")
        self.assertLess(time.time() - started, 1.0)
        self.assertFalse(self.marker.exists())  # 后台进程还被挡着
        self.assertGreaterEqual(self.auto()["last_attempt"], started)
        info = self.wait_spawned()
        self.assertNotEqual(info["sid"], os.getsid(0))
        self.assertTrue(info["stdin_devnull"])
        self.assertEqual(os.path.realpath(info["cwd"]), os.path.realpath(self.state))
        self.assertTrue(wait_for(lambda: "fake update ran" in (self.state / "auto-update.log").read_text()))

    def test_interval(self) -> None:
        """间隔：刚试过 → not_due；满 30 分钟（默认）→ 再起；auto_update_minutes 改成 60 时 31 分钟前的还不到；
        记录的时间在将来（改过系统时间）当作到了"""
        self.assertEqual(self.d.auto_update_tick(), "started")
        self.wait_spawned()
        self.assertEqual(self.d.auto_update_tick(), "not_due")
        self.auto_record(last_attempt=time.time() - 31 * 60, result="up_to_date")
        self.assertEqual(self.d.auto_update_tick(), "started")
        self.wait_spawned()
        self.assertEqual(self.auto()["result"], "up_to_date")  # 只改 last_attempt，别的字段留着
        self.d.CONFIG["auto_update_minutes"] = 60
        self.auto_record(last_attempt=time.time() - 31 * 60)
        self.assertEqual(self.d.auto_update_tick(), "not_due")
        self.auto_record(last_attempt=time.time() + HOUR)
        self.assertEqual(self.d.auto_update_tick(), "started")
        self.wait_spawned()

    def test_lock_busy(self) -> None:
        """机器级的锁被占着（有 update 在跑）：running，不起进程，也不写 last_attempt"""
        lock = self.state / "locks" / "update.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            self.assertEqual(self.d.auto_update_tick(), "running")
        time.sleep(0.3)
        self.assertFalse(self.started_file.exists())
        self.assertFalse(self.auto_file.exists())
        self.assertEqual(self.d.auto_update_tick(), "started")  # 锁放开了就能起
        self.wait_spawned()


# ---------------------------------------------------------------- 谁调 tick


class CallSiteTests(AutoUpdateCase):
    def setUp(self) -> None:
        super().setUp()
        self.env.update(HERDR_ENV="1", HERDR_PANE_ID="w1:p1", HERDR_WORKSPACE_ID="w1", DROVER_MAIN_REGISTER_WAIT="0.5")
        state = self.herdr_state()
        state["panes"] = {"w1:p1": {"session": "s1"}}
        state["workspaces"] = [{"workspace_id": "w1", "label": "work"}]
        self.set_herdr_state(state)
        self.write_block(rules_block())
        self.d = self.load_drover()
        self.calls = []
        self.d.auto_update_tick = lambda: self.calls.append((self.state / "main-rules.json").exists()) or "off"

    def board(self, **kw) -> None:
        args = dict(json=False, line=False, pending=False, live=False, pane=False, tab=False, plain=False, excepts=[],
                    interval=0, iterations=1, dry_run=False)
        args.update(kw)
        with contextlib.redirect_stdout(io.StringIO()):
            self.d.cmd_board(argparse.Namespace(**args))

    def test_main_session_commands_tick(self) -> None:
        """调 tick 的：board 文本 / --json / --live、watch --main 每一轮、main-register（hook 里先记下载入时的规则再触发）；
        board --line / --pending 不调"""
        self.board()
        self.board(json=True)
        self.board(live=True)
        self.assertEqual(len(self.calls), 3)
        self.board(line=True)
        self.board(pending=True)
        self.assertEqual(len(self.calls), 3)
        with contextlib.redirect_stdout(io.StringIO()):
            self.d.watch_main(argparse.Namespace(main=True, agents=[], idle_minutes=10, interval=0.1,
                                                 until_event=False, timeout=0.35))
        self.assertGreaterEqual(len(self.calls), 4)
        self.calls.clear()
        self.assertFalse((self.state / "main-rules.json").exists())
        self.d.main_register(True, {"cwd": str(self.work), "session_id": "s1", "source": "startup"})
        self.assertEqual(self.calls, [True])  # tick 被调时 main-rules.json 已经写好了

    def test_only_main_session_commands_call_tick(self) -> None:
        """源码里调 auto_update_tick() 的只有这几个函数：编排者、worker 用的命令（wt、brief、accept、heavy、orch-status……）
        一律不调"""
        text = DROVER.read_text()
        callers = set()
        current = None
        for line in text.splitlines():
            m = re.match(r"def (\w+)\(", line)
            if m:
                current = m.group(1)
            elif "auto_update_tick()" in line and not line.lstrip().startswith("#"):
                callers.add(current)
        self.assertEqual(callers, {"main_session_seen", "watch_main", "board_live", "cmd_board"})


# ---------------------------------------------------------------- drover rules


class RulesTests(AutoUpdateCase):
    def setUp(self) -> None:
        super().setUp()
        self.commits = self.make_clone()  # rules 跑临时 clone 里的 bin/drover：HEAD 是 v0.1.0+2

    def test_first_same_diff_full(self) -> None:
        """第一次（没有载入时的记录）给整个区块；没变说一句；改了一条只给差别（@@ 所在的节、- 旧、+ 新，没变的不列）；
        --full 给整个区块；改动超过一半给整个区块。每次读完都把载入时更新成现在的。开头写跑着的 drover 的版本号（v0.1.0+2），
        main-rules.json 里照旧存完整 sha"""
        v1 = rules_block()
        self.write_block(v1)
        out = self.rules()
        head = out.splitlines()[0]
        self.assertEqual(head, f"drover v0.1.0+2 的主会话规则（没有载入时的记录）：{REPLACE}")
        self.assertIn(v1, out)
        self.assertNotIn("我的个人规则", out)  # 只给区块，区块外的个人内容不给
        rules = self.main_rules()
        self.assertEqual(rules["digest"], hashlib.sha1(v1.encode()).hexdigest())
        self.assertEqual((rules["text"], rules["source"], rules["version"]), (v1, "rules", self.commits[2]))

        same = self.rules().splitlines()
        self.assertEqual(len(same), 1, same)
        self.assertRegex(same[0], r"^drover v0\.1\.0\+2：主会话规则和载入时（\d\d-\d\d \d\d:\d\d）一样，不用替换。$")

        v2 = rules_block("- 追踪第 2 条：新的写法")
        self.write_block(v2)
        lines = self.rules().splitlines()
        self.assertEqual(lines[0], f"drover v0.1.0+2 更新了主会话规则：{REPLACE}")
        self.assertIn("只列改动的行", lines[1])
        self.assertEqual(lines[3:], ["@@ ## 追踪与转达", "- - 追踪第 2 条：旧的写法", "+ - 追踪第 2 条：新的写法"])
        self.assertEqual(self.main_rules()["text"], v2)

        full = self.rules("--full")
        self.assertIn("一样，不用替换", full.splitlines()[0])
        self.assertIn(v2, full)
        v3 = rules_block("- 追踪第 2 条：第三版")
        self.write_block(v3)
        full = self.rules("--full")
        self.assertIn("更新了主会话规则", full.splitlines()[0])
        self.assertIn(v3, full)

        v4 = rules_block("- 追踪第 2 条：第三版", extra=40)  # 改动比区块的一半还多
        self.write_block(v4)
        out = self.rules()
        self.assertIn("下面是整个区块", out.splitlines()[1])
        self.assertIn(v4, out)

    def test_head_from_to_versions(self) -> None:
        """主会话载入时的版本和现在跑着的不一样：开头写"drover <旧>→<新> 更新了主会话规则"，两头是版本号（main-rules.json 里存的
        完整 sha 现算）：载入时是 v0.1.0 那个提交 → v0.1.0→v0.1.0+2；载入时的提交不在这个仓库里（算不出来）→ 那一头退回 7 位 sha"""
        self.write_block(rules_block())
        self.rules()
        for loaded, want, block in ((self.commits[0], "v0.1.0→v0.1.0+2", rules_block("- 追踪第 2 条：新的写法")),
                                    (SHA_A, "aaaaaaa→v0.1.0+2", rules_block("- 追踪第 2 条：第三版"))):
            with self.subTest(want=want):
                rules = self.main_rules()
                rules["version"] = loaded
                (self.state / "main-rules.json").write_text(json.dumps(rules))
                self.write_block(block)
                self.assertEqual(self.rules().splitlines()[0], f"drover {want} 更新了主会话规则：{REPLACE}")

    def test_marks_update_announced(self) -> None:
        """读完把 auto-update.json 里最近一次更新标成 announced（Codex 看 board --json 的 announced 决定要不要读）；
        已经报过的再读不改它（幂等）"""
        self.write_block(rules_block())
        self.auto_record(result="updated", update={"from": SHA_A, "to": SHA_B, "announced": False, "routing_changed": True})
        self.rules()
        upd = self.auto()["update"]
        self.assertTrue(upd["announced"])
        at = upd["announced_at"]
        self.rules()
        self.assertEqual(self.auto()["update"]["announced_at"], at)

    def test_not_main_session_records_nothing(self) -> None:
        """不是主会话跑的（普通终端、别的 pane）：照样输出改动，开头多一行说明没记；不写 main-rules.json、不把更新标成已报。
        以前谁跑都记成主会话已读：你在终端里看一眼规则，主会话之后再跑只会得到"和载入时一样"，新规则就到不了它手里"""
        self.write_block(rules_block())
        self.rules()  # 主会话载入了 v1
        loaded = self.main_rules()
        v2 = rules_block("- 追踪第 2 条：新的写法")
        self.write_block(v2)
        self.auto_record(result="updated", update={"from": SHA_A, "to": SHA_B, "announced": False, "routing_changed": True})
        for env in ({}, {"HERDR_ENV": "1", "HERDR_PANE_ID": "w9:p9"}):
            with self.subTest(env=env):
                out = self.rules(env=env)
                self.assertTrue(out.splitlines()[0].startswith("# 不是主会话（"), out)
                self.assertIn("没记成主会话已读", out.splitlines()[0])
                self.assertIn("+ - 追踪第 2 条：新的写法", out)
                self.assertEqual(self.main_rules(), loaded)
                self.assertFalse(self.auto()["update"]["announced"])
        out = self.rules()  # 主会话自己跑：拿到同样的改动，记下
        self.assertTrue(out.startswith("drover "), out)
        self.assertIn("+ - 追踪第 2 条：新的写法", out)
        self.assertEqual(self.main_rules()["text"], v2)
        self.assertTrue(self.auto()["update"]["announced"])

    def test_errors(self) -> None:
        """AGENTS.md 里没有区块、标记不成对：报错（JSON），退出码 1，不写 main-rules.json；没配主工作台：报错让先跑 install"""
        out = json.loads(self.rules(rc=1))
        self.assertIn("没有 drover 区块", out["error"])
        self.agents_md.write_text(f"{BEGIN}\n只有开头\n")
        self.assertIn("没有 drover 区块", json.loads(self.rules(rc=1))["error"])
        self.assertFalse((self.state / "main-rules.json").exists())
        (self.state / "config.json").write_text("{}")
        self.assertIn("没有配置主工作台", json.loads(self.rules(rc=1))["error"])

    def test_markers_match_install(self) -> None:
        """区块标记和 install.py 的 BLOCK_BEGIN / BLOCK_END 一样"""
        install = (PKG / "install.py").read_text()
        self.assertIn(f'BLOCK_BEGIN = "{BEGIN}"', install)
        self.assertIn(f'BLOCK_END = "{END}"', install)
        d = self.load_drover()
        self.assertIn(re.escape(BEGIN), d.ROUTING_BEGIN_RE.pattern)
        self.assertIn(re.escape(END), d.ROUTING_END_RE.pattern)


# ---------------------------------------------------------------- rules --hook（Claude Code 的 UserPromptSubmit hook）


class RulesHookTests(AutoUpdateCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_clone()  # hook 跑临时 clone 里的 bin/drover：版本号是 v0.1.0+2
        self.write_block(rules_block())
        self.rules()  # 记下载入时
        (self.state / "main-session.json").write_text(json.dumps({"session_id": "s1", "pane_id": "w1:p1",
                                                                  "source": "startup", "at": time.time()}))

    def hook(self, session="s1", raw: str = None):
        payload = raw if raw is not None else json.dumps(
            {"session_id": session, "hook_event_name": "UserPromptSubmit", "prompt": "看板", "cwd": str(self.work)})
        p = self.drover("rules", "--hook", rc=None, cwd=self.work, input=payload, script=self.script)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_outputs_changes_once_for_main_session(self) -> None:
        """别的会话：什么都不输出；主会话、规则没变：不输出；变了：输出和 drover rules 一样的开头（写版本号）和改动，记成载入时、
        标成 announced，下一条消息不再输出。一次 herdr 都不调"""
        self.assertEqual(self.hook(), "")
        self.write_block(rules_block("- 追踪第 2 条：新的写法"))
        self.auto_record(result="updated", update={"from": SHA_A, "to": SHA_B, "announced": False})
        self.assertEqual(self.hook("s-worker"), "")
        out = self.hook()
        self.assertEqual(out.splitlines()[0], f"drover v0.1.0+2 更新了主会话规则：{REPLACE}")
        self.assertIn("+ - 追踪第 2 条：新的写法", out)
        self.assertTrue(self.auto()["update"]["announced"])
        self.assertEqual(self.main_rules()["text"], rules_block("- 追踪第 2 条：新的写法"))
        self.assertEqual(self.hook(), "")
        self.assertEqual(self.herdr_calls(), [])

    def test_bad_input_is_quiet(self) -> None:
        """stdin 不是 JSON、是空的、没有 session_id，main-session.json 没有、session_id 是 null（Codex 写的）：
        退出码 0、不输出"""
        self.write_block(rules_block("- 追踪第 2 条：新的写法"))
        for raw in ("不是 JSON", "", "{}", "[1, 2]", json.dumps({"session_id": None})):
            self.assertEqual(self.hook(raw=raw), "", raw)
        (self.state / "main-session.json").write_text(json.dumps({"session_id": None}))
        self.assertEqual(self.hook(raw=json.dumps({"session_id": None})), "")
        (self.state / "main-session.json").unlink()
        self.assertEqual(self.hook(), "")

    def test_common_path_runs_no_git(self) -> None:
        """常见路径（不是主会话、规则没变）不跑 git、不调 herdr：在进程里把 git / herdr 换成只记调用的（hook 吞掉所有异常，
        所以不能靠抛错来发现）"""
        d = self.load_drover()
        called = []
        d.git = d.herdr = lambda *args, **kw: called.append(args)
        for sid in ("s-other", "s1"):
            d.hook_input = lambda sid=sid: {"session_id": sid}
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(d.cmd_rules_hook(argparse.Namespace(hook=True, full=False)), 0)
            self.assertEqual(out.getvalue(), "")
        self.assertEqual(called, [])


# ---------------------------------------------------------------- watch --main 的 drover 事件


class WatchDroverEventTests(AutoUpdateCase):
    def main_watch(self, timeout: str = "2", setup=None) -> list:
        rc, evs, _ = self.watch("--main", "--until-event", *FAST, "--timeout", timeout, setup=setup)
        self.assertEqual(rc, 0, evs)
        return evs

    def test_updated_once_across_rehang(self) -> None:
        """没报过的更新：EVENT drover updated(<旧>..<新>) rules（routing 变了才带 rules），标上 announced；重挂不再报；
        下一次更新再报。两头是版本号，在跑着的 drover 所在的仓库里按记录存的完整 sha 现算（同看板）：正好在 tag 上写 tag，之后 N 个
        提交写 vX.Y.Z+N，不在这个仓库里（算不出来）写 7 位 sha；记录里的 from_tag / to_tag 不再拿来显示"""
        c1, c2, c3 = self.make_clone()

        def in_clone(d) -> None:  # 进程里跑的 drover 当成装在临时 clone 里（link 模式）
            d.PKG_ROOT = self.clone

        def pending(frm: str, to: str, routing: bool) -> None:
            record = self.auto()
            record["update"] = {"from": frm, "to": to, "from_tag": "v9.9.9", "to_tag": None, "routing_changed": routing,
                                "announced": False}
            self.auto_file.write_text(json.dumps(record))

        self.auto_record(result="updated", update={"from": SHA_A, "to": SHA_B, "count": 2, "routing_changed": True,
                                                   "announced": False, "at": time.time()})
        self.assertEqual(self.main_watch(setup=in_clone), ["EVENT drover updated(aaaaaaa..bbbbbbb) rules"])
        self.assertTrue(self.auto()["update"]["announced"])
        self.assertEqual(self.main_watch("1", setup=in_clone), ["EVENT timeout"])
        pending(c1, c3, False)
        self.assertEqual(self.main_watch(setup=in_clone), ["EVENT drover updated(v0.1.0..v0.1.0+2)"])
        self.assertEqual(self.main_watch("1", setup=in_clone), ["EVENT timeout"])
        self.git(self.clone, "tag", "-a", "v0.2.0", "-m", "release v0.2.0", c3)
        pending(c2, c3, True)
        self.assertEqual(self.main_watch(setup=in_clone), ["EVENT drover updated(v0.1.0+1..v0.2.0) rules"])

    def test_blocked_once_per_reason(self) -> None:
        """被挡住：EVENT drover update-blocked，同一个原因只报一次（文案里的提交数变了也算同一个）、重挂不重报；换了原因再报；
        失败（failed）同样报"""
        self.auto_record(result="blocked", blocked_status="dirty", error="clone 里有已跟踪文件的本地改动……")
        self.assertEqual(self.main_watch(), ["EVENT drover update-blocked"])
        self.assertEqual(self.main_watch("1"), ["EVENT timeout"])
        record = self.auto()
        record["error"] = "clone 里有已跟踪文件的本地改动（落后 5 个提交）……"
        self.auto_file.write_text(json.dumps(record))
        self.assertEqual(self.main_watch("1"), ["EVENT timeout"])
        record.update(blocked_status="diverged", error="clone 和 origin/main 分叉了")
        self.auto_file.write_text(json.dumps(record))
        self.assertEqual(self.main_watch(), ["EVENT drover update-blocked"])
        record = self.auto()
        record.update(result="failed", error="找不到 drover 的 clone")
        record.pop("blocked_status")
        self.auto_file.write_text(json.dumps(record))
        self.assertEqual(self.main_watch(), ["EVENT drover update-blocked"])

    def test_install_failed_blocked_not_updated(self) -> None:
        """快进了但 install 没装好（result install_failed）：报 update-blocked（同一个原因一次），不报 updated、不标已报——没装好
        不算更新完；重跑 install 装好以后（result 回到 up_to_date），这时才报 updated(…) rules"""
        self.auto_record(result="install_failed", error="安装：没改动；失败：主工作台路由区块（没法处理）",
                         update={"from": SHA_A, "to": SHA_B, "routing_changed": True, "announced": False,
                                 "at": time.time()})
        self.assertEqual(self.main_watch(), ["EVENT drover update-blocked"])
        self.assertFalse(self.auto()["update"]["announced"])
        self.assertEqual(self.main_watch("1"), ["EVENT timeout"])
        record = self.auto()
        record["result"] = "up_to_date"
        record.pop("error")
        self.auto_file.write_text(json.dumps(record))
        self.assertEqual(self.main_watch(), ["EVENT drover updated(aaaaaaa..bbbbbbb) rules"])

    def test_unreachable_only_after_24h(self) -> None:
        """连不上：不满 24 小时不报；满了报一次 EVENT drover update-unreachable(24h)，重挂不重报；新的一段重新计时"""
        self.auto_record(result="unreachable", unreachable_since=time.time() - 23 * HOUR, error="连不上远端")
        self.assertEqual(self.main_watch("1"), ["EVENT timeout"])
        since = time.time() - 25 * HOUR
        self.auto_record(result="unreachable", unreachable_since=since, error="连不上远端")
        self.assertEqual(self.main_watch(), ["EVENT drover update-unreachable(24h)"])
        self.assertEqual(self.auto()["unreachable_reported"], since)
        self.assertEqual(self.main_watch("1"), ["EVENT timeout"])
        record = self.auto()
        record["unreachable_since"] = time.time() - HOUR
        self.auto_file.write_text(json.dumps(record))
        self.assertEqual(self.main_watch("1"), ["EVENT timeout"])

    def session(self, sid: str, source: str) -> None:
        path = self.state / "main-session.json"
        tmp = path.with_name("main-session.json.tmp")
        tmp.write_text(json.dumps({"session_id": sid, "pane_id": "w1:p1", "source": source, "at": time.time()}))
        os.replace(tmp, path)

    def rounds(self) -> int:
        return sum(c == ["agent", "list"] for c in self.herdr_calls())

    def test_session_restarted(self) -> None:
        """watch 起来时记下 main-session.json 的 session_id；之后变成 compact 的新 session 不报，变成 clear 的报
        EVENT drover session-restarted 并退出（--until-event）；startup 同样报；session_id 没变不报"""
        self.session("s1", "startup")
        p = self.start_watch("--main", "--until-event", *FAST, "--timeout", "15")
        self.assertTrue(wait_for(lambda: self.rounds() >= 1))
        self.session("s2", "compact")
        n = self.rounds()
        self.assertTrue(wait_for(lambda: self.rounds() >= n + 2))
        self.assertIsNone(p.poll())  # compact 不算重开
        self.session("s3", "clear")
        out, _ = p.communicate(timeout=30)
        self.assertEqual(events(out), ["EVENT drover session-restarted"])

        self.assertEqual(self.main_watch("1"), ["EVENT timeout"])  # 新挂的 watch 从 s3 起，不报
        p = self.start_watch("--main", "--until-event", *FAST, "--timeout", "15")
        n = self.rounds()
        self.assertTrue(wait_for(lambda: self.rounds() >= n + 1))
        self.session("s4", "startup")
        out, _ = p.communicate(timeout=30)
        self.assertEqual(events(out), ["EVENT drover session-restarted"])

    def test_two_watchers_report_once(self) -> None:
        """两个 watch --main 同时跑（/clear 之后新对话又挂了一个、旧的还没退）：会话重开、自动更新、编排者的未读 rollup，
        每件事只有一个进程报"""
        self.session("s1", "startup")
        d = self.state / "t-a"
        d.mkdir()
        (d / "orch.json").write_text(json.dumps({"slug": "t-a", "name": "orch-a", "kind": "claude"}))
        (d / "rollup.md").write_text("# t-a\n状态：进行中\n")
        self.set_agents(**{"orch-a": {"status": "working"}})
        procs = [self.start_watch("--main", "--until-event", "--interval", "0.05", "--timeout", "4") for _ in range(2)]
        self.assertTrue(wait_for(lambda: self.rounds() >= 4))
        (d / "rollup.md").write_text("# t-a\n状态：等你决定\n\n## 需要你决定\n- 选 A 还是 B\n")
        self.auto_record(result="updated", update={"from": SHA_A, "to": SHA_B, "announced": False})
        self.session("s2", "clear")
        outs = [events(p.communicate(timeout=30)[0]) for p in procs]
        got = sorted(e for out in outs for e in out if e != "EVENT timeout")
        self.assertEqual(got, ["EVENT drover session-restarted", "EVENT drover updated(aaaaaaa..bbbbbbb)",
                               "EVENT orch-a rollup:等你决定"], outs)


# ---------------------------------------------------------------- 版本号：提交 → v0.3.0 / v0.3.0+N / 7 位 sha


class VersionLabelTests(AutoUpdateCase):
    def test_version_label(self) -> None:
        """提交 → 版本号（version_label：看板、update、rules、watch --main 都用它）：历史上一个版本 tag 都没有 → 7 位 sha；正好在
        tag 上 → tag（轻量、附注都认；几个取版本号最大的，按数字比：v0.10.0 比 v0.9.0 新；历史上有更大的也写正好在的那个）；tag
        之后 N 个提交 → vX.Y.Z+N，基准是这个提交历史上版本号最大的严格 vX.Y.Z tag（之后打的预发布版 v0.11.0-rc1、别的分支上的
        v9.9.9 不算）；算不出来（不是 git 仓库、仓库里没有这个提交、没给仓库）→ 7 位 sha；没有提交 → None"""
        d = self.load_drover()
        repo = self.tmp / "repo"
        c1 = self.init_repo(repo, {"a.txt": "1\n"})
        self.assertEqual(d.version_label(str(repo), c1), c1[:7])
        self.git(repo, "tag", "v0.9.0")
        self.git(repo, "tag", "-a", "v0.10.0", "-m", "release v0.10.0")
        self.commit(repo, "第 2 个提交", {"a.txt": "2\n"})
        c3 = self.commit(repo, "第 3 个提交", {"a.txt": "3\n"})
        self.git(repo, "tag", "-a", "v0.11.0-rc1", "-m", "预发布")
        self.git(repo, "switch", "-q", "-c", "side", c1)
        side = self.commit(repo, "别的分支上的提交", {"b.txt": "side\n"})
        self.git(repo, "tag", "v9.9.9")
        for sha, want in ((c1, "v0.10.0"), (c3, "v0.10.0+2"), (side, "v9.9.9")):
            self.assertEqual(d.version_label(str(repo), sha), want)
        self.git(repo, "tag", "v0.9.1", c3)  # 正好在 tag 上就写它，哪怕历史上有更大的
        self.assertEqual(d.version_label(str(repo), c3), "v0.9.1")
        for where, sha in ((str(self.work), c3), (str(repo), SHA_A), (None, c3)):
            self.assertEqual(d.version_label(where, sha), sha[:7], where)
        self.assertIsNone(d.version_label(str(repo), None))


# ---------------------------------------------------------------- 看板上的 drover 状态


class BoardDroverTests(AutoUpdateCase):
    def setUp(self) -> None:
        super().setUp()
        self.commits = self.make_clone()  # 看板跑临时 clone 里的 bin/drover：HEAD 是 v0.1.0+2

    def board(self, *args: str) -> str:
        return self.drover("board", *args, script=self.script).stdout

    def board_lines(self) -> list:
        """默认的 Markdown 看板去掉空行（各段之间空一行）。"""
        return [line for line in self.board().splitlines() if line]

    def test_version_and_lines(self) -> None:
        """第一行末尾 · drover <版本号>（这份代码所在的 clone 在 v0.1.0 之后 2 个提交：v0.1.0+2）；最近 24 小时自动更新过的加一行
        "drover 已自动更新 <旧>→<新>（MM-DD HH:MM）"，两头是版本号（记录里存的完整 sha 现算；不在这个仓库里的算不出来，退回 7 位
        sha），括号里不再单独写 tag；被挡住的加一行"drover 自动更新暂停：<原因>"；更新是 25 小时前的不显示"""
        c1, _, c3 = self.commits
        lines = self.board_lines()
        self.assertTrue(lines[0].endswith(" · drover v0.1.0+2"), lines[0])
        self.assertEqual(len(lines), 1)
        at = time.time() - HOUR
        stamp = time.strftime('%m-%d %H:%M', time.localtime(at))
        self.auto_record(result="blocked", blocked_status="dirty", error="clone 里有已跟踪文件的本地改动（README.md）",
                         update={"from": c1, "to": c3, "at": at, "announced": True})
        lines = self.board_lines()
        self.assertEqual(lines[1], f"drover 已自动更新 v0.1.0→v0.1.0+2（{stamp}）")
        self.assertEqual(lines[2], "drover 自动更新暂停：clone 里有已跟踪文件的本地改动（README.md）")
        self.assertIn(lines[2], self.board("--plain").splitlines())  # 飞书的纯文字看板也有
        self.auto_record(result="up_to_date", update={"from": SHA_A, "to": SHA_B, "to_tag": "v0.2.0", "at": at})
        self.assertEqual(self.board_lines()[1:], [f"drover 已自动更新 aaaaaaa→bbbbbbb（{stamp}）"])
        self.auto_record(result="up_to_date", update={"from": SHA_A, "to": SHA_B, "at": time.time() - 25 * HOUR})
        self.assertEqual(len(self.board_lines()), 1)

    def test_copy_mode_version_from_clone(self) -> None:
        """copy 模式：副本没有 .git，版本号在副本标记（.drover-copy）的 source（clone）里按标记的 commit 算——复制时 clone 在
        v0.1.0+1，之后 clone 又多了提交，第一行照样是副本那一版 · drover v0.1.0+1（不是 clone 的 HEAD）；clone 里给这个提交打了
        tag 就写 tag。--json 的 version、commit 跟着"""
        c2 = self.commits[1]
        pkg = self.tmp / "pkg"
        shutil.copytree(self.clone, pkg, ignore=shutil.ignore_patterns(".git"))
        (pkg / ".drover-copy").write_text(json.dumps({"source": str(self.clone), "commit": c2,
                                                      "copied_at": "2026-11-22T20:00:00"}))
        self.script = pkg / "bin" / "drover"
        self.assertTrue(self.board_lines()[0].endswith(" · drover v0.1.0+1"), self.board_lines()[0])
        self.git(self.clone, "tag", "-a", "v0.1.1", "-m", "release v0.1.1", c2)
        self.assertTrue(self.board_lines()[0].endswith(" · drover v0.1.1"), self.board_lines()[0])
        out = json.loads(self.board("--json"))["drover"]
        self.assertEqual((out["version"], out["commit"]), ("v0.1.1", c2[:7]))

    def test_manual_update_line(self) -> None:
        """手动 drover update 换了版本（update 的 source 是 manual）：第二行写"drover 已更新 b→c（MM-DD HH:MM，手动）"，
        不接自动更新的 error（那是上一次自动检查的）；两头是版本号（v0.1.0→v0.1.0+2，算不出来的退回 7 位 sha）；自动的（source
        auto，或旧记录没有 source）照旧"drover 已自动更新 a→b（…）"，安装有失败项的接上 error；--plain 一样；手动的也是 24 小时
        以后不显示"""
        at = time.time() - HOUR
        stamp = time.strftime('%m-%d %H:%M', time.localtime(at))
        error = "安装：改了 1 项；失败：插件（超时）"
        self.auto_record(result="updated", error=error,
                         update={"from": SHA_B, "to": SHA_C, "at": at, "announced": True, "source": "manual"})
        self.assertEqual(self.board_lines()[1:], [f"drover 已更新 bbbbbbb→ccccccc（{stamp}，手动）"])
        self.assertIn(f"drover 已更新 bbbbbbb→ccccccc（{stamp}，手动）", self.board("--plain").splitlines())
        self.auto_record(result="updated", error=error,
                         update={"from": self.commits[0], "to": self.commits[2], "to_tag": None, "at": at,
                                 "announced": True, "source": "manual"})
        self.assertEqual(self.board_lines()[1:], [f"drover 已更新 v0.1.0→v0.1.0+2（{stamp}，手动）"])
        for source in ("auto", None):
            with self.subTest(source=source):
                upd = {"from": SHA_A, "to": SHA_B, "at": at, "announced": False, **({"source": source} if source else {})}
                self.auto_record(result="updated", error=error, update=upd)
                self.assertEqual(self.board_lines()[1:], [f"drover 已自动更新 aaaaaaa→bbbbbbb（{stamp}），{error}"])
        self.auto_record(result="up_to_date",
                         update={"from": SHA_B, "to": SHA_C, "at": time.time() - 25 * HOUR, "source": "manual"})
        self.assertEqual(len(self.board_lines()), 1)

    def test_install_failed_line(self) -> None:
        """快进了但 install 没装好（result install_failed）：不写"已自动更新"，写"drover 自动更新装失败（下次自动检查重试）：
        <失败项>"，--plain 一样；装好以后（up_to_date）回到"已自动更新 a→b（…）\""""
        at = time.time() - HOUR
        error = "安装：没改动；失败：主工作台路由区块（没法处理）"
        upd = {"from": SHA_A, "to": SHA_B, "at": at, "announced": False}
        self.auto_record(result="install_failed", error=error, update=upd)
        want = f"drover 自动更新装失败（下次自动检查重试）：{error}"
        self.assertEqual(self.board_lines()[1:], [want])
        self.assertIn(want, self.board("--plain").splitlines())
        self.auto_record(result="up_to_date", update=upd)
        self.assertEqual(self.board_lines()[1:],
                         [f"drover 已自动更新 aaaaaaa→bbbbbbb（{time.strftime('%m-%d %H:%M', time.localtime(at))}）"])

    def test_json_drover_field(self) -> None:
        """--json 的 drover：version（版本号，同第一行）、commit（7 位 sha，给机器 / 排查用）、auto_update（enabled、result、
        last_attempt、error、from、to（7 位 sha：提交字段，不换成版本号）、from_tag、to_tag、count、titles、routing_changed、
        flow_changed、updated_at、announced、source）、rules_changed（区块和载入时的不同；没有载入时的记录算没变）。source：没有
        更新记录是 null，旧记录没有这个键算 auto，手动 update 写的是 manual；from_tag / to_tag 是那个提交上的版本 tag，旧记录没有
        是 null"""
        c1, _, c3 = self.commits
        out = json.loads(self.board("--json"))["drover"]
        self.assertEqual(set(out), {"version", "commit", "auto_update", "rules_changed"})
        self.assertEqual((out["version"], out["commit"]), ("v0.1.0+2", c3[:7]))
        self.assertEqual(set(out["auto_update"]), {"enabled", "result", "last_attempt", "error", "from", "to", "from_tag",
                                                   "to_tag", "count", "titles", "routing_changed", "flow_changed",
                                                   "updated_at", "announced", "source"})
        self.assertFalse(out["auto_update"]["enabled"])  # 自测环境 DROVER_AUTO_UPDATE=0
        self.assertIsNone(out["auto_update"]["announced"])
        self.assertIsNone(out["auto_update"]["source"])
        self.assertFalse(out["rules_changed"])

        self.write_block(rules_block())
        self.rules()
        self.write_block(rules_block("- 追踪第 2 条：新的写法"))
        self.auto_record(last_attempt=time.time(), result="updated",
                         update={"from": c1, "to": c3, "from_tag": "v0.1.0", "to_tag": None, "count": 3,
                                 "titles": ["t1", "t2", "t3"], "routing_changed": True, "flow_changed": False,
                                 "at": time.time(), "announced": False})
        auto = json.loads(self.board("--json"))["drover"]
        self.assertTrue(auto["rules_changed"])
        a = auto["auto_update"]
        self.assertEqual((a["result"], a["from"], a["to"], a["count"], a["titles"], a["routing_changed"], a["announced"],
                          a["source"]), ("updated", c1[:7], c3[:7], 3, ["t1", "t2", "t3"], True, False, "auto"))
        self.assertEqual((a["from_tag"], a["to_tag"]), ("v0.1.0", None))
        self.assertRegex(a["updated_at"], r"^\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertRegex(a["last_attempt"], r"^\d\d-\d\d \d\d:\d\d:\d\d$")
        self.rules()
        after = json.loads(self.board("--json"))["drover"]
        self.assertEqual((after["rules_changed"], after["auto_update"]["announced"]), (False, True))
        record = self.auto()
        record["update"]["source"] = "manual"
        self.auto_file.write_text(json.dumps(record))
        self.assertEqual(json.loads(self.board("--json"))["drover"]["auto_update"]["source"], "manual")

    def test_manual_update_change(self) -> None:
        """手动 update 记不记、记什么（manual_update_change，按跑着的版本 v0 → v1 算）：v0、v1 一样不记（copy 模式 install 没复制成：
        clone 新了、副本没变）；v0..v1 就是 clone 的 before..after 用 update 的结果；不一样（上次 install 失败、这次重跑）在 clone 上
        现算，算不出来（clone 里没有这两个提交）0 条、空标题、没有文件，routing 看这次 install 改了的项；取不到版本时退回按
        clone 的 HEAD（before、after 不同才记）。现算时两头的版本 tag 也在 clone 上现算（clone 不是 git 仓库：没有 tag）"""
        d = self.load_drover()
        res = {"clone": str(self.tmp), "before": SHA_A, "after": SHA_B, "new_commits": {"count": 2, "titles": ["t2", "t1"]},
               "changed_files": ["flow/dispatch.md"], "install": {"changed_keys": ["routing"]}}
        self.assertIsNone(d.manual_update_change(res, SHA_A, SHA_A))
        self.assertIs(d.manual_update_change(res, SHA_A, SHA_B), res)
        self.assertEqual(d.manual_update_change(res, SHA_C, SHA_B),
                         {"before": SHA_C, "after": SHA_B, "from_tag": None, "to_tag": None,
                          "new_commits": {"count": 0, "titles": []}, "changed_files": [],
                          "install": {"changed_keys": ["routing"]}})
        self.assertIs(d.manual_update_change(res, None, SHA_B), res)
        self.assertIsNone(d.manual_update_change(dict(res, after=SHA_A), SHA_A, None))
        self.assertIsNone(d.manual_update_change({"before": SHA_A}, None, None))  # 被挡住：没有 after


if __name__ == "__main__":
    unittest.main(verbosity=2)
