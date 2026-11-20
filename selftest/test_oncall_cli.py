"""drover oncall：飞书 oncall（内置的 herdr-oncall 插件）的薄封装。

- pair / unpair / test / on / off → `herdr plugin action invoke <id> --plugin com.codreamer.herdr.oncall`（contract 第 6 节）；
- status → 直接跑插件的 `bin/run-node.sh src/actions/status.mjs`（contract 第 5 节），透传 JSON，补上 drover 视角
  （插件版本够不够、herdr 层面启用没有、drover.env 对不对、主会话登记了没有）和提示；
- 没装插件、插件太旧（没有 status 脚本）时 ok: false 并说清楚怎么办；
- 输出里不出现密钥：drover 不读插件的 .env，透传前把键名像密钥的字符串值换成 <redacted>。
假插件目录：bin/run-node.sh 打印同目录的 status.json。
drover oncall 在进程里跑、用假时钟（drover_in_process）：等插件日志（0.5 秒一轮）、--wait 的轮询、DROVER_PAIR_LOG_WAIT 的
上限都按假时间，不用真等；假 herdr、假插件脚本照常是子进程。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest

from selftest_support import PKG, DroverTestCase

PLUGIN = "com.codreamer.herdr.oncall"
ACTIONS = {"pair": "pair-feishu", "unpair": "unpair-feishu", "test": "test", "on": "enable", "off": "disable"}
ENV_SECRET = "envfile-secret-7f3a9c"
STATUS_SECRET = "status-secret-2b8d41"

FAKE_RUN_NODE = """#!/bin/bash
# 假的 run-node.sh：不跑 node，打印同目录上一级的 status.json（模拟 src/actions/status.mjs 的一行 JSON）；
# 有 paired-flag（假 herdr 在 pair-feishu 时写的，模拟用户扫码确认）就打印 status-paired.json。
[ "$1" = "src/actions/status.mjs" ] || { echo "unexpected script $1" >&2; exit 3; }
root="$(dirname "$0")/.."
if [ -f "$root/paired-flag" ]; then cat "$root/status-paired.json"; else cat "$root/status.json"; fi
"""
PAIR_URL = "https://example.invalid/pair?user_code=ABCD-1234"
PAIR_STDOUT = (f"scan with the Feishu app, expires in 10 min:\nopen: {PAIR_URL}\n"
               "waiting for the scan in the background\nworker 4242 logs to poller.log\n")
PAIRED_STDOUT = "feishu: already paired · app cli_fake · open_id ou_" + "already-secret-55aa\n"


def contract_status(**over) -> dict:
    status = {
        "ok": True, "version": "0.7.0", "enabled": True,
        "channels": {"selected": ["feishu"], "feishu": {"paired": True, "replies_supported": True},
                     "telegram": {"paired": False}},
        "node": {"version": "22.11.0"}, "poller": {"alive": True}, "panel": "ready",
        "drover_env": {"present": True, "path": "/x/drover.env"},
        "roles": {"main_agent_name": "drover", "supervisor_prefix": "orch-", "supervisor_escalate_sec": 300,
                  "supervisor_escalate_sec_codex": 120, "supervised_escalate_sec": 600, "bare_message_target": "main",
                  "main_done_ping": "relay", "main_relay_marker": "【转达】"},
        "main_agent": {"found": True, "pane_id": "w1:p1", "agent": "claude"},
    }
    status.update(over)
    return status


class OncallCliTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.env.update(DROVER_PAIR_LOG_WAIT="2", DROVER_PAIR_POLL="0.2")
        self.root = self.tmp / "plugin-root"
        (self.root / "bin").mkdir(parents=True)
        (self.root / "bin" / "run-node.sh").write_text(FAKE_RUN_NODE)
        self.config_dir = self.home / ".config" / "herdr" / "plugins" / "config" / PLUGIN
        self.config_dir.mkdir(parents=True)
        # 插件自己的 .env（飞书配对的密钥在这里）：drover 不能读它，输出里不能出现里面的值
        (self.config_dir / ".env").write_text(f"CHANNEL=feishu\nFEISHU_APP_SECRET={ENV_SECRET}\n")
        self.d = self.load_drover()  # 只用它的常量（ONCALL_ENV_TEXT 等）
        self.set_plugin(version="0.7.0", source="github", status=contract_status())
        self.set_main(registered=True)

    def drover(self, *args: str, rc=0):
        """drover oncall 在进程里跑（见模块说明），退出码检查同 DroverTestCase.drover。"""
        return self.drover_in_process(*args, rc=rc)

    def set_plugin(self, version: str, source: str, status=None, enabled: bool = True, actions=None, **extra) -> None:
        script = self.root / "src" / "actions" / "status.mjs"
        if status is None:
            if script.exists():
                script.unlink()
        else:
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("// fake\n")
            (self.root / "status.json").write_text(json.dumps(status, ensure_ascii=False) + "\n")
        state = self.herdr_state()
        state["plugins"] = [{"plugin_id": PLUGIN, "version": version, "enabled": enabled, "plugin_root": str(self.root),
                             "source": {"kind": source},
                             "actions": [{"id": a} for a in (actions if actions is not None else
                                                             [*ACTIONS.values(), "toggle", "poll", "pair"])]}]
        state["plugin_config_dir"] = str(self.config_dir)
        state.update(extra)
        self.set_herdr_state(state)

    def set_main(self, registered: bool) -> None:
        state = self.herdr_state()
        state["panes"] = {"w1:p1": {"session": "s1", **({"name": "drover"} if registered else {})}}
        self.set_herdr_state(state)

    def write_drover_env(self, text=None) -> None:
        (self.config_dir / "drover.env").write_text(self.d.ONCALL_ENV_TEXT if text is None else text)

    def assert_no_secret(self, text: str) -> None:
        self.assertNotIn(ENV_SECRET, text)
        self.assertNotIn(STATUS_SECRET, text)

    # ---- 动作

    def test_actions_map_to_contract(self) -> None:
        """五个动作各自交给插件的 action（contract 第 6 节）；unpair / test / on / off 立即返回，提示去 plugin log 看真实结果"""
        for action, action_id in ACTIONS.items():
            p = self.drover("oncall", action)
            out = json.loads(p.stdout)
            self.assertTrue(out["ok"])
            self.assertEqual(out["plugin_action"], action_id)
            self.assertEqual(out["command"], f"herdr plugin action invoke {action_id} --plugin {PLUGIN}")
            invokes = [c for c in self.herdr_calls() if c[:3] == ["plugin", "action", "invoke"]]
            self.assertEqual(invokes[-1], ["plugin", "action", "invoke", action_id, "--plugin", PLUGIN])
            if action != "pair":
                self.assertIn(f"herdr plugin log list --plugin {PLUGIN} --limit 5", out["hint"])
            self.assert_no_secret(p.stdout)

    def set_action_stdout(self, **stdout: str) -> None:
        state = self.herdr_state()
        state["action_stdout"] = {k.replace("_", "-"): v for k, v in stdout.items()}
        self.set_herdr_state(state)

    def set_state(self, **values) -> None:
        state = self.herdr_state()
        state.update(values)
        self.set_herdr_state(state)

    def log_lists(self) -> int:
        return len([c for c in self.herdr_calls() if c[:3] == ["plugin", "log", "list"]])

    def test_pair_waits_until_action_finished(self) -> None:
        """真机：herdr 先记一条 status: running、没有 stdout 的条目，动作跑完（已配对的机器要 20 多秒）才写输出。
        要等到跑完再读；等的时候往 stderr 打一句进度，stdout 只有最后的 JSON"""
        self.set_action_stdout(pair_feishu=PAIR_STDOUT)
        self.set_state(action_running_polls=3)
        p = self.drover("oncall", "pair")
        out = json.loads(p.stdout)
        self.assertEqual((out["link"], out["already_paired"]), (PAIR_URL, False))
        self.assertNotIn("still_running", out)
        self.assertGreaterEqual(self.log_lists(), 4)
        self.assertIn("已配对的机器要先向飞书核对", p.stderr)

    def test_pair_already_paired_chinese_line(self) -> None:
        """插件只给中文那行"已经配对过飞书……"也算已配对"""
        self.set_action_stdout(pair_feishu="已经配对过飞书（机器人 cli_fake），不用再配对。如需重新配对，请先解绑：drover oncall unpair\n")
        self.set_state(action_running_polls=2)
        out = self.drover_json("oncall", "pair")
        self.assertEqual((out["already_paired"], out["link"]), (True, None))
        self.assertIn("已配对", out["hint"])

    def test_pair_still_running_at_limit(self) -> None:
        """到上限还在 running：不报错，提示插件还在处理、稍后看 herdr plugin log list"""
        self.env["DROVER_PAIR_LOG_WAIT"] = "1"
        self.set_action_stdout(pair_feishu=PAIR_STDOUT)
        self.set_state(action_never_finishes=True)
        out = self.drover_json("oncall", "pair")
        self.assertEqual((out["ok"], out["link"], out["already_paired"], out["still_running"]), (True, None, False, True))
        self.assertIn("插件还在处理", out["hint"])
        self.assertIn(f"herdr plugin log list --plugin {PLUGIN}", out["hint"])
        self.assertNotIn("plugin_message", out)

    def test_pair_reads_link_from_plugin_log(self) -> None:
        """pair：调完动作从插件日志里读出这次的 open: <url>，放进 link 和提示（桌面没弹二维码就用飞书打开它）"""
        self.set_action_stdout(pair_feishu=PAIR_STDOUT)
        out = self.drover_json("oncall", "pair")
        self.assertEqual((out["ok"], out["link"], out["already_paired"]), (True, PAIR_URL, False))
        self.assertIn("二维码", out["hint"])
        self.assertIn("link", out["hint"])
        self.assertNotIn("paired", out)  # 没带 --wait 不等
        self.assertIn(["plugin", "log", "list", "--plugin", PLUGIN, "--limit", "20"], self.herdr_calls())

    def test_pair_already_paired(self) -> None:
        """已配对：说已配对；插件那行带 open_id，不能出现在输出里"""
        self.set_action_stdout(pair_feishu=PAIRED_STDOUT)
        p = self.drover("oncall", "pair")
        out = json.loads(p.stdout)
        self.assertEqual((out["already_paired"], out["link"]), (True, None))
        self.assertIn("已配对", out["hint"])
        self.assertNotIn("already-secret", p.stdout)
        out = self.drover_json("oncall", "pair", "--wait", "1")
        self.assertIs(out["paired"], True)
        self.assertFalse([c for c in self.herdr_calls() if c[:4] == ["plugin", "action", "invoke", "test"]])

    def test_pair_without_link_shows_safe_lines(self) -> None:
        """插件没给出链接（比如出错）：给出能看的几行，带 open_id / secret 的行去掉"""
        self.set_action_stdout(pair_feishu="feishu pairing error: tenant forbids app creation\nopen_id ou_" + "leak-9f\n")
        p = self.drover("oncall", "pair")
        out = json.loads(p.stdout)
        self.assertIsNone(out["link"])
        self.assertEqual(out["plugin_message"], ["feishu pairing error: tenant forbids app creation"])
        self.assertNotIn("leak-9f", p.stdout)

    def test_pair_wait_until_paired_then_test(self) -> None:
        """--wait：等到插件 status 显示飞书配上，自动发一条测试消息"""
        self.set_action_stdout(pair_feishu=PAIR_STDOUT)
        state = self.herdr_state()
        state["pair_flag_file"] = str(self.root / "paired-flag")
        self.set_herdr_state(state)
        paired = contract_status()
        status = contract_status()
        status["channels"]["feishu"]["paired"] = False
        self.set_plugin(version="0.7.0", source="github", status=status)
        (self.root / "status-paired.json").write_text(json.dumps(paired) + "\n")
        out = self.drover_json("oncall", "pair", "--wait", "10")
        self.assertEqual((out["ok"], out["paired"], out["link"]), (True, True, PAIR_URL))
        self.assertEqual(out["test"]["command"], f"herdr plugin action invoke test --plugin {PLUGIN}")
        self.assertIn("测试消息", out["hint"])
        invokes = [c[3] for c in self.herdr_calls() if c[:3] == ["plugin", "action", "invoke"]]
        self.assertEqual(invokes, ["pair-feishu", "test"])

    def test_pair_wait_timeout(self) -> None:
        """--wait 到时间还没配上：ok: false 报出来，不发测试消息"""
        self.set_action_stdout(pair_feishu=PAIR_STDOUT)
        status = contract_status()
        status["channels"]["feishu"]["paired"] = False
        self.set_plugin(version="0.7.0", source="github", status=status)
        out = self.drover_json("oncall", "pair", "--wait", "1", rc=1)
        self.assertEqual((out["ok"], out["paired"], out["link"]), (False, False, PAIR_URL))
        self.assertIn("还没配对上", out["error"])
        self.assertFalse([c for c in self.herdr_calls() if c[:4] == ["plugin", "action", "invoke", "test"]])

    def test_wait_only_for_pair(self) -> None:
        out = self.drover_json("oncall", "test", "--wait", "5", rc=1)
        self.assertIn("只给 pair 用", out["error"])
        self.assertFalse([c for c in self.herdr_calls() if c[:3] == ["plugin", "action", "invoke"]])

    def test_status_hides_unpaired_telegram(self) -> None:
        """status：drover 只引导飞书，没配过的 Telegram 不报；用户自己配过的照报"""
        self.write_drover_env()
        p = self.drover("oncall", "status")
        out = json.loads(p.stdout)
        self.assertNotIn("telegram", out["plugin_status"]["channels"])
        self.assertNotIn("telegram", p.stdout.lower())  # 提示里也不提
        status = contract_status()
        status["channels"]["telegram"] = {"paired": True}
        self.set_plugin(version="0.7.0", source="github", status=status)
        out = self.drover_json("oncall", "status")
        self.assertEqual(out["plugin_status"]["channels"]["telegram"], {"paired": True})

    def test_action_missing_in_old_plugin(self) -> None:
        """插件的 action 列表里没有要的动作（版本太旧）：不调 invoke，提示更新"""
        self.set_plugin(version="0.5.0", source="github", status=None, actions=["toggle", "pair"])
        out = self.drover_json("oncall", "pair", rc=1)
        self.assertIn("没有 pair-feishu", out["error"])
        self.assertFalse([c for c in self.herdr_calls() if c[:3] == ["plugin", "action", "invoke"]])

    def test_action_failure_reported(self) -> None:
        self.set_plugin(version="0.7.0", source="github", status=contract_status(), plugin_action_fail=True)
        out = self.drover_json("oncall", "test", rc=1)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "plugin_action_failed")

    def test_not_installed(self) -> None:
        """没装插件：动作和 status 都 ok: false，提示重跑 install.py --oncall（飞书 oncall 是选装的）"""
        state = self.herdr_state()
        state["plugins"] = []
        self.set_herdr_state(state)
        for action in ("status", "pair"):
            out = self.drover_json("oncall", action, rc=1)
            self.assertFalse(out["ok"])
            self.assertIn("没装飞书 oncall", out["error"])
            self.assertIn("install.py --oncall", out["error"])
        self.assertFalse([c for c in self.herdr_calls() if c[:3] == ["plugin", "action", "invoke"]])

    # ---- status

    def test_status_passes_through_and_adds_drover_view(self) -> None:
        """status：插件 status 脚本的 JSON 原样放进 plugin_status，加上 drover 视角；键名像密钥的值被换掉"""
        status = contract_status()
        status["channels"]["feishu"].update(app_secret=STATUS_SECRET, open_id="ou_" + STATUS_SECRET,
                                            token_present=True)
        self.set_plugin(version="0.7.0", source="github", status=status)
        self.write_drover_env()
        p = self.drover("oncall", "status")
        out = json.loads(p.stdout)
        self.assertTrue(out["ok"])
        ps = out["plugin_status"]
        for key in ("version", "enabled", "channels", "node", "poller", "panel", "drover_env", "roles", "main_agent"):
            self.assertIn(key, ps)
        self.assertEqual(ps["roles"], status["roles"])
        self.assertEqual(ps["channels"]["feishu"]["app_secret"], "<redacted>")
        self.assertEqual(ps["channels"]["feishu"]["open_id"], "<redacted>")
        self.assertIs(ps["channels"]["feishu"]["token_present"], True)  # 布尔值（有没有）照常透传
        view = out["drover"]
        self.assertEqual(view["plugin"], {"version": "0.7.0", "min_version": "0.7.0", "version_ok": True,
                                          "source": "github", "herdr_enabled": True, "plugin_root": str(self.root)})
        self.assertEqual(view["drover_env"], {"path": str(self.config_dir / "drover.env"), "present": True,
                                              "up_to_date": True})
        self.assertEqual(view["main_agent"], {"registered": True, "pane_id": "w1:p1", "agent": "claude"})
        self.assertEqual(out["hints"], [])
        self.assert_no_secret(p.stdout)
        self.assertFalse([c for c in self.herdr_calls() if c[:3] == ["plugin", "action", "invoke"]])  # 不走 action

    def test_status_hints(self) -> None:
        """status 的提示：drover.env 不在、主会话没登记、插件自己的开关关着、herdr 里被禁用、飞书没配对"""
        status = contract_status(enabled=False)
        status["channels"]["feishu"]["paired"] = False
        self.set_plugin(version="0.7.0", source="github", status=status, enabled=False)
        self.set_main(registered=False)
        out = self.drover_json("oncall", "status")
        hints = "\n".join(out["hints"])
        for word in ("drover.env 不在", "main-register", "drover oncall on", f"herdr plugin enable {PLUGIN}",
                     "drover oncall pair"):
            self.assertIn(word, hints)
        self.assertEqual((out["drover"]["drover_env"]["present"], out["drover"]["main_agent"]["registered"]),
                         (False, False))
        self.assertIs(out["drover"]["plugin"]["herdr_enabled"], False)
        self.write_drover_env("MAIN_AGENT_NAME=drover\n")
        out = self.drover_json("oncall", "status")
        self.assertIn("刷新", "\n".join(out["hints"]))
        self.assertFalse(out["drover"]["drover_env"]["up_to_date"])

    def test_status_old_plugin_without_script(self) -> None:
        """插件太旧、没有 status 脚本：ok: false，按来源给提示（本地链接 → git pull；GitHub → 重跑 install）"""
        self.set_plugin(version="0.6.6", source="local", status=None)
        self.write_drover_env()
        out = self.drover_json("oncall", "status", rc=1)
        self.assertIn("太旧", out["error"])
        self.assertIn("git pull", out["error"])
        self.assertFalse(out["drover"]["plugin"]["version_ok"])
        self.assertTrue(out["drover"]["drover_env"]["present"])
        self.set_plugin(version="0.6.6", source="github", status=None)
        out = self.drover_json("oncall", "status", rc=1)
        self.assertIn("install.py", out["error"])

    def test_status_script_failure(self) -> None:
        """status 脚本没给出 JSON：ok: false，带退出码说明"""
        self.set_plugin(version="0.7.0", source="github", status=contract_status())
        (self.root / "status.json").write_text("not json\n")
        out = self.drover_json("oncall", "status", rc=1)
        self.assertIn("没给出 JSON", out["error"])

    # ---- 和 install.py 的常量一致

    def test_constants_match_install_py(self) -> None:
        """插件 id、最低版本、drover.env 的内容：bin/drover 和 install.py 各写了一份，必须一样"""
        spec = importlib.util.spec_from_file_location("drover_install_consts", PKG / "install.py")
        inst = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = inst  # install.py 里有 dataclass，加载时要能从 sys.modules 找到自己
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(inst)
        self.assertEqual(self.d.ONCALL_PLUGIN_ID, inst.ONCALL_PLUGIN_ID)
        self.assertEqual(self.d.ONCALL_MIN_VERSION, inst.ONCALL_MIN_VERSION)
        self.assertEqual(self.d.ONCALL_ENV_TEXT, inst.ONCALL_ENV_TEXT)
        self.assertEqual(self.d.ONCALL_ENV_NAME, inst.ONCALL_ENV_NAME)
        self.assertEqual(self.d.ONCALL_MIN_VERSION, "0.7.0")
        lines = [line for line in self.d.ONCALL_ENV_TEXT.splitlines() if line and not line.startswith("#")]
        self.assertEqual(lines, ["MAIN_AGENT_NAME=drover", "SUPERVISOR_PREFIX=orch-", "SUPERVISOR_ESCALATE_SEC=300",
                                 "SUPERVISOR_ESCALATE_SEC_CODEX=120", "SUPERVISED_ESCALATE_SEC=600",
                                 "BARE_MESSAGE_TARGET=main", "MAIN_DONE_PING=relay", "MAIN_RELAY_MARKER=【转达】",
                                 "CHAT_IMAGE_DIR=~/.drover/oncall-images"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
