"""要真实 herdr server 的实测用的隔离环境（selftest/live_herdr.py）：拼出的环境、动手前和收尾的自检、拒绝条件、收尾。

这里不起真实 herdr：PATH 最前面换成一个只认隔离工具那几条命令的假 herdr（socket 按 XDG_CONFIG_HOME 算、server 真的监听、
每次调用的参数和环境记下来），lsof 也是假的。"用户的 herdr"是临时 HOME 下的 ~/.config/herdr。"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import unittest
from pathlib import Path

import selftest_fakes
from selftest_support import PKG, DroverTestCase, wait_for

import live_herdr
from live_herdr import LiveHerdr, LiveHerdrError

TOOL = PKG / "selftest" / "live_herdr.py"
TMP_ROOT_PREFIX = os.path.realpath("/tmp") + "/drover-live-"

FAKE_LIVE_HERDR = r'''#!{python}
"""live_herdr 用例的假 herdr：只认隔离工具会发的几条命令。socket 按 XDG_CONFIG_HOME 算，server 真的监听；每次调用的参数、
pid 和相关环境变量记进同目录的 live-calls.jsonl。同目录的 live-spec.json 改它的行为：sessions_outside（session list 把默认
socket 报到别处）、plugins（plugin list 返回的条目）、server_fails（server 起不来）、stop_ignored（stop 返回 0 但不停）。"""
import json
import os
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
args = sys.argv[1:]
spec_file = HERE / "live-spec.json"
spec = json.loads(spec_file.read_text()) if spec_file.exists() else {}
seen = {k: v for k, v in os.environ.items() if k.startswith(("HERDR_", "XDG_", "DROVER_STATE_DIR"))}
with open(HERE / "live-calls.jsonl", "a") as fh:
    fh.write(json.dumps({"args": args, "pid": os.getpid(), "env": seen}) + "\n")

session = None
if args[:1] == ["--session"]:
    session, args = args[1], args[2:]
config = Path(os.environ.get("XDG_CONFIG_HOME") or Path(os.environ["HOME"]) / ".config") / "herdr"


def session_dir(name):
    return config / "sessions" / name if name else config


def running(name):
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(session_dir(name) / "herdr.sock"))
        return True
    except OSError:
        return False


def info(name):
    sock = session_dir(name) / "herdr.sock"
    if spec.get("sessions_outside") and name is None:
        sock = Path("/nonexistent-user-config/herdr/herdr.sock")
    return {"default": name is None, "name": name or "default", "running": running(name),
            "session_dir": str(sock.parent), "socket_path": str(sock)}


def serve(name):
    if spec.get("server_fails"):
        sys.exit("error: fake herdr server refuses to start")
    session_dir(name).mkdir(parents=True, exist_ok=True)
    path = session_dir(name) / "herdr.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    listener.listen(8)
    while True:
        conn, _ = listener.accept()
        with conn:
            conn.settimeout(2)
            try:
                data = conn.recv(16)
            except OSError:
                data = b""
        if data.startswith(b"stop"):
            listener.close()
            path.unlink()
            sys.exit(0)


def stop(name):
    if spec.get("stop_ignored"):
        sys.exit(0)
    path = session_dir(name) / "herdr.sock"
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(path))
            s.sendall(b"stop")
    except OSError:
        sys.exit("not running")
    deadline = time.time() + 5
    while path.exists() and time.time() < deadline:
        time.sleep(0.01)
    sys.exit(0)


if args == ["server"]:
    serve(session)
if args == ["server", "stop"]:
    stop(None)
if args[:2] == ["session", "stop"] and len(args) == 3:
    stop(args[2])
if args == ["session", "list", "--json"]:
    names = sorted(p.name for p in (config / "sessions").iterdir()) if (config / "sessions").is_dir() else []
    print(json.dumps({"sessions": [info(None)] + [info(n) for n in names]}))
    sys.exit(0)
if args == ["plugin", "list", "--json"]:
    print(json.dumps({"id": "cli:plugin", "result": {"type": "plugin_list", "plugins": spec.get("plugins", [])}}))
    sys.exit(0)
sys.exit("fake live herdr: unsupported command: " + " ".join(sys.argv[1:]))
'''

FAKE_LSOF = r'''#!{python}
"""假 lsof：原样打印同目录的 lsof-output.txt（没有就什么都不打）。"""
import sys
from pathlib import Path

out = Path(__file__).resolve().parent / "lsof-output.txt"
sys.stdout.write(out.read_text() if out.exists() else "")
'''

OUTER_HERDR = {"HERDR_ENV": "1", "HERDR_SESSION": "outer", "HERDR_PANE_ID": "w9:p9", "HERDR_TAB_ID": "w9:t1",
               "HERDR_WORKSPACE_ID": "w9", "HERDR_BIN_PATH": "/opt/homebrew/bin/herdr"}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class IsolatedEnvTests(unittest.TestCase):
    def test_env_points_into_root_and_drops_inherited_herdr(self) -> None:
        """拼出的环境：继承的 HERDR_* 全去掉（含只换配置文件的 HERDR_CONFIG_PATH），XDG 两个根和 DROVER_STATE_DIR 指进临时
        根目录，关掉提示音；别的变量原样，传进来的那份不改"""
        base = {"PATH": "/bin", "HOME": "/Users/u", **OUTER_HERDR, "HERDR_SOCKET_PATH": "/Users/u/.config/herdr/herdr.sock",
                "HERDR_CLIENT_SOCKET_PATH": "/Users/u/.config/herdr/herdr-client.sock",
                "HERDR_CONFIG_PATH": "/Users/u/.config/herdr/config.toml", "XDG_CONFIG_HOME": "/Users/u/xdg",
                "XDG_STATE_HOME": "/Users/u/state", "DROVER_STATE_DIR": "/Users/u/.drover"}
        env = live_herdr.isolated_env(base, Path("/tmp/drover-live-x"))
        self.assertEqual({k: v for k, v in env.items() if k.startswith("HERDR_")}, {"HERDR_DISABLE_SOUND": "1"})
        self.assertEqual(env["XDG_CONFIG_HOME"], "/tmp/drover-live-x/config")
        self.assertEqual(env["XDG_STATE_HOME"], "/tmp/drover-live-x/state")
        self.assertEqual(env["DROVER_STATE_DIR"], "/tmp/drover-live-x/drover")
        self.assertEqual((env["PATH"], env["HOME"]), ("/bin", "/Users/u"))
        self.assertEqual(base["HERDR_ENV"], "1")
        self.assertEqual(base["XDG_CONFIG_HOME"], "/Users/u/xdg")

    def test_user_herdr_dir_follows_inherited_env(self) -> None:
        """"用户的 herdr"按继承环境算：有 XDG_CONFIG_HOME 用它，没有（或是空串）就是 HOME/.config"""
        self.assertEqual(live_herdr.user_herdr_dir({"HOME": "/Users/u"}), Path("/Users/u/.config/herdr"))
        self.assertEqual(live_herdr.user_herdr_dir({"HOME": "/Users/u", "XDG_CONFIG_HOME": ""}),
                         Path("/Users/u/.config/herdr"))
        self.assertEqual(live_herdr.user_herdr_dir({"HOME": "/Users/u", "XDG_CONFIG_HOME": "/x"}), Path("/x/herdr"))


class LiveHerdrCase(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.fakebin / "herdr").unlink()
        selftest_fakes.place(self.fakebin / "herdr", FAKE_LIVE_HERDR.replace("{python}", sys.executable, 1))
        selftest_fakes.place(self.fakebin / "lsof", FAKE_LSOF.replace("{python}", sys.executable, 1))
        self.user_herdr = self.home / ".config" / "herdr"
        self.user_herdr.mkdir(parents=True)
        (self.user_herdr / "plugins.json").write_text('[{"plugin_id": "com.user.real"}]\n')
        (self.user_herdr / "config.toml").write_text("onboarding = false\n")
        self.set_user_server(4242)
        # 外层环境照在用户 herdr 的 pane 里的样子：带着指向用户 server 的 HERDR_*
        for key in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
            self.env.pop(key, None)
        self.env.update(OUTER_HERDR, HERDR_SOCKET_PATH=str(self.user_herdr / "herdr.sock"),
                        HERDR_CLIENT_SOCKET_PATH=str(self.user_herdr / "herdr-client.sock"),
                        HERDR_CONFIG_PATH=str(self.user_herdr / "config.toml"))
        self.enter_env()
        self.assert_fake_herdr()
        self.addCleanup(self.reap)

    def reap(self) -> None:
        """用例失败时别留下假 server 和 /tmp 下的根目录。"""
        for call in self.calls():
            if call["args"][-1:] == ["server"] and alive(call["pid"]):
                os.kill(call["pid"], signal.SIGKILL)
            root = Path(call["env"].get("XDG_CONFIG_HOME", "")).parent
            if str(root).startswith(TMP_ROOT_PREFIX):
                shutil.rmtree(root, ignore_errors=True)

    def set_user_server(self, pid: int) -> None:
        """假 lsof 报：用户默认 socket 的监听进程是 pid。"""
        (self.fakebin / "lsof-output.txt").write_text(f"p{pid}\nf4\nn{self.user_herdr / 'herdr.sock'}\nf8\nn->0xabc\n")

    def spec(self, **spec) -> None:
        (self.fakebin / "live-spec.json").write_text(json.dumps(spec))

    def calls(self) -> list:
        log = self.fakebin / "live-calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def server_pids(self) -> list:
        return [c["pid"] for c in self.calls() if c["args"][-1:] == ["server"]]

    def sessions(self, h: LiveHerdr) -> dict:
        listed = json.loads(h.herdr("session", "list", "--json").stdout)["sessions"]
        return {s["name"]: (s["running"], s["socket_path"]) for s in listed}


class LifecycleTests(LiveHerdrCase):
    def test_start_command_restart_stop_and_cleanup(self) -> None:
        """起默认 server 和具名会话、发命令、重启、停；每条 herdr 命令都带隔离环境；退出后根目录没了、进程都退了"""
        with LiveHerdr() as h:
            root = h.root
            self.assertTrue(str(root).startswith(TMP_ROOT_PREFIX), root)
            self.assertTrue(root.is_dir())
            self.assertEqual((h.config_dir, h.state_dir), (root / "config" / "herdr", root / "state" / "herdr"))
            self.assertIn(f'[worktrees]\ndirectory = "{root / "worktrees"}"', (h.config_dir / "config.toml").read_text())
            first = h.start()
            h.start("two")
            self.assertEqual(self.sessions(h), {"default": (True, str(h.socket())), "two": (True, str(h.socket("two")))})
            self.assertEqual(h.socket("two"), h.config_dir / "sessions" / "two" / "herdr.sock")
            second = h.restart()
            self.assertNotEqual(first, second)
            self.assertFalse(alive(first))
            self.assertTrue(self.sessions(h)["default"][0])
            h.stop("two")
            self.assertEqual(self.sessions(h)["two"][0], False)
            self.assertTrue(self.sessions(h)["default"][0])
        self.assertFalse(root.exists())
        pids = self.server_pids()
        self.assertEqual(len(pids), 3)
        self.assertEqual([p for p in pids if alive(p)], [])
        for call in self.calls():
            self.assertEqual(call["env"]["XDG_CONFIG_HOME"], str(root / "config"), call)
            self.assertEqual(call["env"]["XDG_STATE_HOME"], str(root / "state"), call)
            self.assertEqual(call["env"]["DROVER_STATE_DIR"], str(root / "drover"), call)
            self.assertEqual({k for k in call["env"] if k.startswith("HERDR_")}, {"HERDR_DISABLE_SOUND"}, call)
        self.assertIn(["--session", "two", "server"], [c["args"] for c in self.calls()])
        self.assertIn(["session", "stop", "two"], [c["args"] for c in self.calls()])
        self.assertIn(["server", "stop"], [c["args"] for c in self.calls()])

    def test_run_gets_isolated_env_and_cannot_override_it(self) -> None:
        """h.run 带隔离环境跑任意命令，可以加自己的变量；想盖掉 XDG 根或带 HERDR_* 的拒绝"""
        show = [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"]
        with LiveHerdr() as h:
            seen = json.loads(h.run(show, env={"DROVER_IDLE_RELEASE": "0"}, capture_output=True, text=True).stdout)
            self.assertEqual(seen["XDG_CONFIG_HOME"], str(h.root / "config"))
            self.assertEqual(seen["XDG_STATE_HOME"], str(h.root / "state"))
            self.assertEqual(seen["DROVER_STATE_DIR"], str(h.root / "drover"))
            self.assertEqual(seen["DROVER_IDLE_RELEASE"], "0")
            self.assertEqual({k for k in seen if k.startswith("HERDR_")}, {"HERDR_DISABLE_SOUND"})
            self.assertEqual(h.env["XDG_CONFIG_HOME"], str(h.root / "config"))
            other = json.loads(h.run(show, env={"DROVER_STATE_DIR": "/tmp/elsewhere"}, capture_output=True,
                                     text=True).stdout)
            self.assertEqual(other["DROVER_STATE_DIR"], "/tmp/elsewhere")
            for bad in ({"XDG_CONFIG_HOME": str(self.home / ".config")}, {"XDG_STATE_HOME": "/x"},
                        {"HERDR_SOCKET_PATH": str(self.user_herdr / "herdr.sock")}, {"HERDR_SESSION": "outer"}):
                with self.assertRaises(LiveHerdrError):
                    h.run(show, env=bad, capture_output=True)

    def test_exception_inside_block_still_cleans_up(self) -> None:
        """with 里的脚本中途出错：原来的异常照样抛出，server 停了、根目录删了"""
        with self.assertRaises(ZeroDivisionError):
            with LiveHerdr() as h:
                root = h.root
                h.start()
                h.start("two")
                1 / 0
        self.assertFalse(root.exists())
        self.assertEqual(len(self.server_pids()), 2)
        self.assertEqual([p for p in self.server_pids() if alive(p)], [])

    def test_stop_falls_back_to_killing_its_own_child(self) -> None:
        """herdr 的 stop 没把 server 停掉时，杀掉自己起的那个子进程（不然根目录删不干净）"""
        with LiveHerdr() as h:
            pid = h.start()
            self.spec(stop_ignored=True)
            h.stop(timeout=0.3)
            self.assertFalse(alive(pid))
            self.spec()

    def test_start_failure_is_reported_with_server_output(self) -> None:
        """server 起不来：报错里带它的输出，不算起着"""
        with LiveHerdr() as h:
            self.spec(server_fails=True)
            with self.assertRaises(LiveHerdrError) as cm:
                h.start()
            self.assertIn("fake herdr server refuses to start", str(cm.exception))
            self.spec()
            h.start()

    def test_nothing_runs_before_entering(self) -> None:
        """还没进入 with：发命令、跑命令、起停都拒绝，一条 herdr 命令都没发（不会带着继承的环境打到用户的 herdr）；
        收尾什么都不做、不删任何目录"""
        h = LiveHerdr()
        for use in (lambda: h.herdr("session", "list"), lambda: h.run(["herdr", "session", "list"]), h.start, h.stop):
            with self.assertRaises(LiveHerdrError):
                use()
        self.assertEqual(h.close(), [])
        self.assertEqual(self.calls(), [])
        self.assertTrue(Path("/tmp").is_dir())

    def stray(self, h: LiveHerdr, session: str) -> subprocess.Popen:
        """实测自己带着隔离环境起的 server（不经 h.start()）。"""
        proc = subprocess.Popen(["herdr", "--session", session, "server"], env=h.env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        self.assertTrue(live_herdr.wait_ready(proc, h.socket(session), 10))
        return proc

    def test_stray_server_in_root_is_stopped_on_exit(self) -> None:
        """不是 start() 起的、但跑在这个根目录下的 server：收尾时一样经隔离环境停掉"""
        with LiveHerdr() as h:
            stray = self.stray(h, "side")
        self.assertEqual(stray.wait(timeout=10), 0)
        self.assertIn(["session", "stop", "side"], [c["args"] for c in self.calls()])

    def test_unstoppable_stray_is_reported(self) -> None:
        """根目录下还有停不掉的 server：收尾报错（不是自己起的子进程，不杀）"""
        with self.assertRaises(LiveHerdrError) as cm:
            with LiveHerdr() as h:
                stray = self.stray(h, "side")
                self.spec(stop_ignored=True)
        self.assertIn("side", str(cm.exception))
        self.assertIsNone(stray.poll())


class RefusalTests(LiveHerdrCase):
    def test_refuses_when_socket_resolves_outside_root(self) -> None:
        """解析出来的 socket 不在临时根目录下（隔离没生效）：进入时就拒绝，一个 server 都不起，根目录删掉"""
        self.spec(sessions_outside=True)
        h = LiveHerdr()
        with self.assertRaises(LiveHerdrError) as cm:
            h.__enter__()
        self.assertIn("/nonexistent-user-config/herdr/herdr.sock", str(cm.exception))
        self.assertFalse(h.root.exists())
        self.assertEqual(self.server_pids(), [])

    def test_refuses_plugins_visible_on_enter(self) -> None:
        """进入时这个环境里 herdr plugin list 不是空的（看得到用户的插件）：拒绝"""
        self.spec(plugins=[{"plugin_id": "com.codreamer.herdr.oncall", "plugin_root": "/Users/u/plugins/oncall"}])
        h = LiveHerdr()
        with self.assertRaises(LiveHerdrError) as cm:
            h.__enter__()
        self.assertIn("com.codreamer.herdr.oncall", str(cm.exception))
        self.assertFalse(h.root.exists())
        self.assertEqual(self.server_pids(), [])

    def test_only_plugins_inside_root_are_allowed_later(self) -> None:
        """进入之后：插件目录在根目录下的可以（实测自己 link 的假插件），在根目录外的，起 server 前拒绝、收尾时也报"""
        outside = self.tmp / "real-plugin"
        outside.mkdir()
        with self.assertRaises(LiveHerdrError) as cm:
            with LiveHerdr() as h:
                root = h.root
                (root / "plugin").mkdir()
                self.spec(plugins=[{"plugin_id": "drover.fake", "plugin_root": str(root / "plugin")}])
                h.start()
                h.stop()
                (root / "linked").symlink_to(outside)
                self.spec(plugins=[{"plugin_id": "com.user.real", "plugin_root": str(root / "linked")}])
                with self.assertRaises(LiveHerdrError) as refused:
                    h.start()
                self.assertIn("com.user.real", str(refused.exception))
        self.assertIn("com.user.real", str(cm.exception))
        self.assertEqual(len(self.server_pids()), 1)
        self.assertFalse(root.exists())

    def test_stops_only_servers_it_started(self) -> None:
        """不是它起的会话不停（命令都不发）；默认会话不用名字叫；名字不合规的不起"""
        with LiveHerdr() as h:
            h.start()
            with self.assertRaises(LiveHerdrError):
                h.stop("ghost")
            for name in ("default", "../x", "Two", ""):
                with self.assertRaises(LiveHerdrError):
                    h.start(name)
            with self.assertRaises(LiveHerdrError):
                h.start()
        self.assertNotIn(["session", "stop", "ghost"], [c["args"] for c in self.calls()])
        self.assertEqual(len(self.server_pids()), 1)

    def test_refuses_session_whose_socket_path_is_too_long(self) -> None:
        """socket 路径超过 unix socket 的长度上限的会话名：起之前拒绝"""
        with LiveHerdr() as h:
            with self.assertRaises(LiveHerdrError) as cm:
                h.start("a" * 60)
            self.assertIn("104", str(cm.exception))
        self.assertEqual(self.server_pids(), [])


class UserEnvironmentTests(LiveHerdrCase):
    def leave(self, change) -> str:
        """起一个 server、中途对"用户的 herdr"做 change，返回收尾报的错。"""
        with self.assertRaises(LiveHerdrError) as cm:
            with LiveHerdr() as h:
                root = h.root
                h.start()
                change()
        self.assertFalse(root.exists())
        self.assertEqual([p for p in self.server_pids() if alive(p)], [])
        return str(cm.exception)

    def test_unchanged_user_environment_passes(self) -> None:
        """用户的 herdr 前后没变：收尾不报错（用户的文件一个字节没动）"""
        before = {p.name: p.read_bytes() for p in self.user_herdr.iterdir()}
        with LiveHerdr() as h:
            h.start()
        self.assertEqual({p.name: p.read_bytes() for p in self.user_herdr.iterdir()}, before)

    def test_changed_plugin_registry_is_reported(self) -> None:
        """收尾时用户的 plugins.json 变了：报错"""
        error = self.leave(lambda: (self.user_herdr / "plugins.json").write_text("[]\n"))
        self.assertIn("plugins.json", error)

    def test_changed_config_is_reported(self) -> None:
        """收尾时用户的 config.toml 变了：报错"""
        error = self.leave(lambda: (self.user_herdr / "config.toml").write_text("onboarding = true\n"))
        self.assertIn("config.toml", error)

    def test_replaced_default_server_is_reported(self) -> None:
        """收尾时用户默认 socket 的监听进程换了：报错，写出前后的 pid"""
        error = self.leave(lambda: self.set_user_server(5151))
        self.assertIn("4242", error)
        self.assertIn("5151", error)

    def test_new_named_session_in_user_herdr_is_reported(self) -> None:
        """收尾时用户的 herdr 下多了具名会话（有人在用户的 herdr 里起了测试会话）：报错"""
        error = self.leave(lambda: (self.user_herdr / "sessions" / "drtest").mkdir(parents=True))
        self.assertIn("drtest", error)

    def test_exception_inside_block_is_not_masked_by_teardown_problems(self) -> None:
        """with 里已经出错、收尾自检也有问题时：抛的还是原来的异常，自检的问题写到 stderr"""
        with self.assertRaises(ZeroDivisionError):
            with LiveHerdr() as h:
                root = h.root
                (self.user_herdr / "plugins.json").write_text("[]\n")
                1 / 0
        self.assertFalse(root.exists())


class CommandLineTests(LiveHerdrCase):
    def run_tool(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(TOOL), *args], env=self.env, cwd=str(self.tmp), capture_output=True,
                              text=True, timeout=60)

    def test_run_starts_server_runs_command_and_cleans_up(self) -> None:
        """命令行 run -- <命令>：默认 server 起着、命令带隔离环境、退出码是命令的，跑完 server 停了、根目录删了"""
        child = ("import json, os, subprocess, sys; "
                 "s = json.loads(subprocess.run(['herdr', 'session', 'list', '--json'], capture_output=True, text=True).stdout); "
                 "print(json.dumps({'root': os.path.dirname(os.environ['XDG_CONFIG_HOME']), 'sessions': s['sessions'], "
                 "'herdr': sorted(k for k in os.environ if k.startswith('HERDR_'))})); sys.exit(7)")
        p = self.run_tool("run", "--", sys.executable, "-c", child)
        self.assertEqual(p.returncode, 7, p.stderr)
        seen = json.loads(p.stdout)
        self.assertTrue(seen["root"].startswith(TMP_ROOT_PREFIX), seen)
        self.assertEqual([(s["name"], s["running"]) for s in seen["sessions"]], [("default", True)])
        self.assertEqual(seen["herdr"], ["HERDR_DISABLE_SOUND"])
        self.assertFalse(Path(seen["root"]).exists())
        self.assertEqual(len(self.server_pids()), 1)
        self.assertEqual([p for p in self.server_pids() if alive(p)], [])

    def test_self_check_failure_exits_3_without_running_command(self) -> None:
        """自检不过：退出码 3、stderr 写原因，命令没跑、server 没起"""
        self.spec(sessions_outside=True)
        marker = self.tmp / "ran"
        p = self.run_tool("run", "--", sys.executable, "-c", f"open({str(marker)!r}, 'w').close()")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("/nonexistent-user-config/herdr/herdr.sock", p.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(self.server_pids(), [])

    def test_usage_error_without_command(self) -> None:
        """没给命令：参数错误，退出码 2，什么都不起"""
        p = self.run_tool("run")
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_sigterm_while_command_runs_still_cleans_up(self) -> None:
        """命令还在跑时工具被 SIGTERM 结束：照样停 server、删根目录，退出码 143"""
        marker = self.tmp / "child-started"
        child = f"import pathlib, time; pathlib.Path({str(marker)!r}).touch(); time.sleep(60)"
        p = subprocess.Popen([sys.executable, str(TOOL), "run", "--", sys.executable, "-c", child], env=self.env,
                             cwd=str(self.tmp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait()))
        self.assertTrue(wait_for(marker.exists))
        root = Path(self.calls()[0]["env"]["XDG_CONFIG_HOME"]).parent
        self.assertTrue(root.is_dir())
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=30), 143)
        self.assertFalse(root.exists())
        self.assertEqual(len(self.server_pids()), 1)
        self.assertEqual([pid for pid in self.server_pids() if alive(pid)], [])
        self.assertIn(["server", "stop"], [c["args"] for c in self.calls()])


if __name__ == "__main__":
    unittest.main(verbosity=2)
