"""selftest 用的假可执行文件：假 herdr、假 vitest / tsc / eslint / pytest。install() 把它们放进每个用例的临时目录
（每个进程写一次、各用例硬链接，见 place）。假 ssh 只有测联网重试的用例按需装（install_ssh，见 FAKE_SSH）；
假 pnpm / npm / uv 只有 deps 的用例按需装（install_pkg_tools，见 FAKE_PKG）。

假 herdr：按同目录的 herdr-state.json 返回 JSON 和屏幕内容，每次调用记进 herdr-calls.log，不连任何 server。
  - agent list / get / read：state["agents"][名字] = {status, cwd, seq（state_change_seq，默认 0）,
    screen | screens（每读一次往后翻一屏）, grow（每读一次多一行输出）, agent（herdr 的 agent 字段 claude / codex，
    agent list / get 都输出，不设就不输出）, read_fail（agent read 报错退出，模拟读屏失败）, workspace_id / foreground_cwd
    （设了 agent list 就输出）}
  - worktree list / create / open / remove：create 真的 `git worktree add -b <分支> <路径> <base>`，路径在
    state["worktree_root"] 下；list 带 open_workspace_id（记录里 closed 为真时是 null）；remove --workspace W [--force]
    真的 `git worktree remove [--force]`（有改动不带 --force 会被 git 拒，同真的 herdr），删掉记录、关掉 workspace_id 是 W
    的 agent，W 记进 state["removed_workspaces"]
  - pane list：返回固定的假 ID；workspace list：state["workspaces"]（默认空列表）；status：server 在运行
  - 按 pane 登记的 agent（main-register 用）：state["panes"][pane_id] = {name（没有 = 没名字）, workspace_id, agent,
    session（agent_session.value，没有就不输出）, present（False = herdr 里还没有这个 agent）, pending（每次 agent get
    之前先弹出一项 update 进记录，模拟 /clear 后 herdr 过一会儿才换 session、清名字）}。agent list 同时列出它们，
    agent get <pane 或名字> 查得到，agent rename <pane> <名字> 改 name（名字被别的 agent 占着时报错）
  - pane get / split / run / close（board --pane / --tab 用）：pane get <id> 返回 state["panes"][id] 的 pane_id、tab_id、
    workspace_id、scroll.viewport_rows（rows，默认 50），不在 panes 里报 pane_not_found；pane split --pane <id> 在同一个 tab
    里加一个没有 agent 的 pane（present: False，id 是 <workspace>:p<序号>）；pane run <id> <命令> 记进 state["pane_runs"]
    （pane 不存在时报错，state["pane_run_fail"] 为真时也报错）；pane send-keys <id> <键>... 记进 state["pane_keys"]（pane 不存在
    时报错）；pane close <id> 删掉它、记进 state["pane_closes"]。
    panes 里写了 tab_id 的，agent list 也带上 tab_id
  - tab（board --tab 用）：tab create --workspace W 记进 state["tabs"]（workspace_id、label、focus：带没带 --focus），
    新 tab 的根 pane 记进 panes；第一个返回 W:t2 / W:p2，之后序号往后排（不复用）。tab get / rename / close <id>（不在 tabs 里
    报 tab_not_found；close 连它的 pane 一起删），tab focus <id> 记进 state["tab_focus"]
  - plugin list --json [--plugin ID]：state["plugins"]；plugin action invoke <id> --plugin ID：记一条插件日志（stdout 取
    state["action_stdout"][id]，默认 "<id> (fake)"）后返回 ok（state["plugin_action_fail"] 为真时报错）；pair-feishu 时
    state["pair_flag_file"] 设了就写这个文件（模拟用户扫码确认）；plugin log list --plugin ID --limit N：最近 N 条插件日志，
    state["action_running_polls"] = N 时新动作的条目前 N 次查询显示成 status: running、没有 stdout（和真的 herdr 一样：
    动作跑完才写输出），state["action_never_finishes"] 为真时一直是 running；
    plugin config-dir ID：建 state["plugin_config_dir"] 并打印它（和真的一样会建目录）
  - 起 agent（agent-start / orch-start 用）：agent start <名> --pane P -- <参数> 把 state["starting"][名] 挪进 agents
    （记下 pane_id 和 args，starting 里没有就报错）；agent send-keys <名> <键> 把键记进这个 agent 的 keys，按到现在的整串
    （空格连起来，如 "down enter"）命中 on_keys 里的键时把对应的值合进 agent（换 screen / status；gone 为真表示 agent 退出了，
    删掉）；agent wait 直接返回当前状态；agent prompt <名> <文字> 记进 state["prompts"]、agent 变 working；
    tab create 见上面的 tab
  - 其他子命令一律报错退出（误用一眼能看出来）
假测试工具：按调用名（vitest / tsc / eslint / pytest）打印对应格式的输出，失败条目读当前目录的 fake-<工具>.json，
调用（工具名、参数、cwd）记进 tool-calls.log。vitest / pytest / eslint 的参数里带文件时只报这些文件里的失败
（基线子集、超时复核）；vitest 的超时单独跑时通过，rerun_fail 里的文件除外。tsc 不看参数。
spec 里有 output 时原样回放（真实工具的输出样例），退出码取 exit（默认 0）；带 sleep 的先睡这么多秒，再重读一次 spec
（真工具边跑边读文件：跑的过程中目录被删了就读不到、报错退出）。vitest 另有 unhandled（汇总里 N 个未处理错误，没有 FAIL 行；
只在整条跑时出现）、rerun_no_tests（复核点名文件时 "No test files found"、退出码 0）。
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

GITCONFIG = """[user]
\tname = drover selftest
\temail = selftest@example.invalid
[init]
\tdefaultBranch = main
[commit]
\tgpgsign = false
[advice]
\tdetachedHead = false
"""

FAKE_HERDR = r'''#!{python}
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "herdr-state.json"
args = sys.argv[1:]
with (HERE / "herdr-calls.log").open("a") as f:
    f.write(json.dumps(args) + "\n")
state = json.loads(STATE.read_text()) if STATE.exists() else {}


def save():
    tmp = STATE.with_name(STATE.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    os.replace(tmp, STATE)


def ok(result):
    print(json.dumps({"id": "fake", "result": result}, ensure_ascii=False))
    sys.exit(0)


def fail(code, message):
    print(json.dumps({"error": {"code": code, "message": message}}), file=sys.stderr)
    sys.exit(1)


def opt(name, default=None):
    if name in args and args.index(name) + 1 < len(args):
        return args[args.index(name) + 1]
    return default


agents = state.setdefault("agents", {})
worktrees = state.setdefault("worktrees", [])
panes = state.setdefault("panes", {})
cmd = args[:2]


def pane_record(pane_id, p):
    rec = {"pane_id": pane_id, "workspace_id": p.get("workspace_id", pane_id.split(":")[0]),
           "agent": p.get("agent", "claude"), "agent_status": p.get("status", "idle")}
    if p.get("name"):
        rec["name"] = p["name"]
    if p.get("tab_id"):
        rec["tab_id"] = p["tab_id"]
    if p.get("session"):
        rec["agent_session"] = {"agent": rec["agent"], "kind": "id", "source": "herdr:" + rec["agent"],
                                "value": p["session"]}
    return rec


def find_pane(target):
    if target in panes:
        return target
    return next((k for k, p in panes.items() if p.get("name") == target and p.get("present", True)), None)


if cmd == ["agent", "list"]:
    ok({"agents": [{"name": n, "agent_status": a.get("status", "unknown"), "cwd": a.get("cwd", ""),
                    "state_change_seq": a.get("seq", 0), **({"agent": a["agent"]} if "agent" in a else {}),
                    **{k: a[k] for k in ("pane_id", "workspace_id", "foreground_cwd") if k in a}}
                   for n, a in agents.items()]
                  + [pane_record(k, p) for k, p in panes.items() if p.get("present", True)]})
if cmd == ["agent", "get"] and len(args) >= 3 and args[2] not in agents and find_pane(args[2]):
    key = find_pane(args[2])
    p = panes[key]
    if p.get("pending"):
        p.update(p["pending"].pop(0))
        save()
    if not p.get("present", True):
        fail("agent_not_found", "agent target %s not found" % args[2])
    ok({"agent": pane_record(key, p), "type": "agent_info"})
if cmd == ["agent", "rename"]:
    key = find_pane(args[2]) if len(args) >= 4 else None
    if key is None or not panes[key].get("present", True):
        fail("agent_not_found", "fake herdr: no agent in that pane")
    new = args[3]
    if new in agents or any(p.get("name") == new and k != key for k, p in panes.items()):
        fail("agent_name_taken", "fake herdr: name already used")
    panes[key]["name"] = new
    save()
    ok({"agent": pane_record(key, panes[key]), "type": "agent_info"})
if cmd == ["plugin", "list"]:
    want = opt("--plugin")
    ok({"plugins": [p for p in state.get("plugins", []) if want is None or p.get("plugin_id") == want],
        "type": "plugin_list"})
if args[:3] == ["plugin", "action", "invoke"]:
    if state.get("plugin_action_fail"):
        fail("plugin_action_failed", "fake herdr: action failed")
    action = args[3] if len(args) > 3 else ""
    now = int(time.time() * 1000)
    state.setdefault("plugin_logs", []).append({
        "log_id": "plugin-log-%d" % len(state["plugin_logs"]), "plugin_id": opt("--plugin"), "action_id": action,
        "command": ["/bin/bash", "bin/run-node.sh", "src/actions/%s.mjs" % action],
        "started_unix_ms": now, "finished_unix_ms": now, "status": "succeeded", "exit_code": 0,
        "stdout": state.get("action_stdout", {}).get(action, action + " (fake)\n"), "stderr": "",
        "_running": state.get("action_running_polls", 0)})
    if action == "pair-feishu" and state.get("pair_flag_file"):
        Path(state["pair_flag_file"]).write_text("paired\n")  # 模拟用户扫码确认
    save()
    ok({"type": "ok", "action_id": action})
if args[:3] == ["plugin", "log", "list"]:
    # 和真的 herdr 一样：动作没跑完时条目是 status: running，没有 stdout / stderr / exit_code / finished_unix_ms。
    shown = []
    for entry in state.get("plugin_logs", [])[-int(opt("--limit", "50")):]:
        running = entry.get("_running", 0)
        if running or state.get("action_never_finishes"):
            shown.append({k: v for k, v in entry.items()
                          if k not in ("_running", "stdout", "stderr", "exit_code", "finished_unix_ms")}
                         | {"status": "running"})
            entry["_running"] = max(0, running - 1)
        else:
            shown.append({k: v for k, v in entry.items() if k != "_running"})
    save()
    ok({"logs": shown, "type": "plugin_log_list"})
if cmd == ["plugin", "config-dir"]:
    folder = Path(state["plugin_config_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    print(folder)
    sys.exit(0)
if cmd == ["agent", "get"]:
    if len(args) < 3 or args[2] not in agents:
        fail("agent_not_found", "fake herdr: no such agent")
    a = agents[args[2]]
    ok({"agent": {"name": args[2], "agent_status": a.get("status", "unknown"), "state_change_seq": a.get("seq", 0),
                  **({"agent": a["agent"]} if "agent" in a else {})}})
if cmd == ["agent", "read"]:
    if len(args) < 3 or args[2] not in agents:
        fail("agent_not_found", "fake herdr: no such agent")
    a = agents[args[2]]
    if a.get("read_fail"):
        fail("read_failed", "fake herdr: read failed")
    reads = a.get("reads", 0)
    screens = a.get("screens")
    text = screens[min(reads, len(screens) - 1)] if screens else a.get("screen", "")
    if a.get("grow"):
        text += "\n" + "\n".join("• Edited " + "x" * (i + 1) for i in range(reads + 1))
    a["reads"] = reads + 1
    save()
    print("\n".join(text.splitlines()[-int(opt("--lines", "40")):]))
    sys.exit(0)
if cmd == ["agent", "start"]:
    name = args[2] if len(args) > 2 else ""
    spec = state.get("starting", {}).pop(name, None)
    if spec is None:
        fail("agent_start_failed", "fake herdr: nothing in state.starting for " + name)
    agents[name] = dict(spec, pane_id=opt("--pane"), args=args[args.index("--") + 1:] if "--" in args else [])
    save()
    ok({"agent": {"name": name, "agent_status": agents[name].get("status", "unknown")}, "type": "agent_started"})
if cmd in (["agent", "send-keys"], ["agent", "wait"], ["agent", "prompt"]) and (len(args) < 3 or args[2] not in agents):
    fail("agent_not_found", "fake herdr: no such agent")
if cmd == ["agent", "send-keys"]:
    a = agents[args[2]]
    a["keys"] = a.get("keys", []) + args[3:]
    update = a.get("on_keys", {}).get(" ".join(a["keys"]))
    if update and update.get("gone"):
        del agents[args[2]]
    elif update:
        a.update(update)
    save()
    ok({"type": "ok"})
if cmd == ["agent", "wait"]:
    ok({"agent": {"name": args[2], "agent_status": agents[args[2]].get("status", "unknown")}})
if cmd == ["agent", "prompt"]:
    state.setdefault("prompts", []).append([args[2], args[3]])
    agents[args[2]]["status"] = "working"
    save()
    ok({"agent": {"name": args[2], "agent_status": "working"}, "type": "agent_prompted"})
if cmd == ["tab", "create"]:
    # 第一个是 W:t2 / W:p2（orch-start 的用例按这个写），之后按序号往后排（关掉的 ID 不复用，同真的 herdr）。新 tab 和它的
    # pane 记进 tabs / panes（pane 没有 agent：present False，agent list 不列）。
    ws = opt("--workspace")
    tabs = state.setdefault("tabs", {})
    seq = state.setdefault("tab_seq", {})
    n = seq[ws] = seq.get(ws, 1) + 1
    tab_id, pane_id = "%s:t%d" % (ws, n), "%s:p%d" % (ws, n)
    tabs[tab_id] = {"workspace_id": ws, "label": opt("--label"), "focus": "--focus" in args}
    panes[pane_id] = {"tab_id": tab_id, "workspace_id": ws, "present": False}
    save()
    ok({"root_pane": {"pane_id": pane_id}, "tab": {"tab_id": tab_id}})
if args[:2] == ["tab", "focus"]:
    state.setdefault("tab_focus", []).append(args[2])
    save()
    ok({"type": "ok"})
if args[:2] in (["tab", "get"], ["tab", "rename"], ["tab", "close"]):
    tabs = state.setdefault("tabs", {})
    if len(args) < 3 or args[2] not in tabs:
        fail("tab_not_found", "fake herdr: no such tab")
    if args[1] == "rename":
        tabs[args[2]]["label"] = args[3]
    elif args[1] == "close":
        del tabs[args[2]]
        for k in [k for k, p in panes.items() if p.get("tab_id") == args[2]]:
            del panes[k]
    save()
    ok({"tab": dict(tabs.get(args[2], {}), tab_id=args[2]), "type": "tab_info"})
if cmd == ["worktree", "list"]:
    # 和真的 herdr 一样带 open_workspace_id（workspace 没开的，closed 为真，写成 null）
    repo = os.path.realpath(opt("--cwd", "."))
    ok({"worktrees": [dict(w, open_workspace_id=None if w.get("closed") else w["workspace_id"])
                      for w in worktrees if w["repo"] == repo]})
if cmd == ["worktree", "remove"]:
    # 真的 herdr：跑 git worktree remove（有改动的要 --force，不删分支），关掉 workspace 和里面的 agent
    ws = opt("--workspace")
    w = next((w for w in worktrees if w["workspace_id"] == ws and not w.get("closed")), None)
    if w is None:
        fail("workspace_not_found", "fake herdr: no open worktree workspace " + str(ws))
    p = subprocess.run(["git", "-C", w["repo"], "worktree", "remove", *(["--force"] if "--force" in args else []),
                        w["path"]], capture_output=True, text=True)
    if p.returncode != 0:
        fail("git_worktree_remove_failed", p.stderr.strip())
    worktrees.remove(w)
    for name in [n for n, a in agents.items() if a.get("workspace_id") == ws]:
        del agents[name]
    state.setdefault("removed_workspaces", []).append(ws)
    save()
    ok({"type": "worktree_removed", "workspace_id": ws, "forced": "--force" in args})
if cmd == ["worktree", "create"]:
    repo = os.path.realpath(opt("--cwd", "."))
    branch = opt("--branch")
    path = opt("--path") or str(Path(state["worktree_root"]) / Path(repo).name / branch.replace("/", "-"))
    p = subprocess.run(["git", "-C", repo, "worktree", "add", "-q", "-b", branch, path, opt("--base", "HEAD")],
                       capture_output=True, text=True)
    if p.returncode != 0:
        fail("git_worktree_add_failed", p.stderr.strip())
    w = {"repo": repo, "branch": branch, "path": path, "workspace_id": "w%d" % (len(worktrees) + 1),
         "label": opt("--label")}
    worktrees.append(w)
    save()
    ok({"workspace": {"workspace_id": w["workspace_id"], "label": w["label"]}, "worktree": w})
if cmd == ["worktree", "open"]:
    repo = os.path.realpath(opt("--cwd", "."))
    for w in worktrees:
        if w["repo"] == repo and w["branch"] == opt("--branch"):
            ok({"workspace": {"workspace_id": w["workspace_id"], "label": w["label"]}, "worktree": w})
    fail("worktree_not_found", "fake herdr: no such worktree")
if cmd == ["pane", "list"]:
    ok({"panes": [{"pane_id": "%s:p1" % opt("--workspace"), "workspace_id": opt("--workspace")}]})


def pane_view(pane_id):
    p = panes[pane_id]
    return {"pane_id": pane_id, "tab_id": p.get("tab_id"), "workspace_id": p.get("workspace_id", pane_id.split(":")[0]),
            "scroll": {"viewport_rows": p.get("rows", 50)}}


if cmd == ["pane", "get"]:
    if len(args) < 3 or args[2] not in panes:
        fail("pane_not_found", "fake herdr: no such pane")
    ok({"pane": pane_view(args[2]), "type": "pane_info"})
if cmd == ["pane", "split"]:
    target = opt("--pane")
    if target not in panes:
        fail("pane_not_found", "fake herdr: no such pane")
    ws = panes[target].get("workspace_id", target.split(":")[0])
    new = "%s:p%d" % (ws, 100 + len(panes))
    panes[new] = {"tab_id": panes[target].get("tab_id"), "workspace_id": ws, "present": False, "rows": 10}
    save()
    ok({"pane": pane_view(new), "type": "pane_info"})
if cmd == ["pane", "run"]:
    if len(args) < 4 or args[2] not in panes:
        fail("pane_not_found", "fake herdr: no such pane")
    if state.get("pane_run_fail"):
        fail("pane_run_failed", "fake herdr: pane run failed")
    state.setdefault("pane_runs", []).append([args[2], args[3]])
    save()
    ok({"type": "ok"})
if cmd == ["pane", "send-keys"]:
    if len(args) < 4 or args[2] not in panes:
        fail("pane_not_found", "fake herdr: no such pane")
    state.setdefault("pane_keys", []).append(args[2:])
    save()
    ok({"type": "ok"})
if cmd == ["pane", "close"]:
    if len(args) < 3 or args[2] not in panes:
        fail("pane_not_found", "fake herdr: no such pane")
    del panes[args[2]]
    state.setdefault("pane_closes", []).append(args[2])
    save()
    ok({"type": "ok"})
if cmd == ["workspace", "list"]:
    ok({"workspaces": state.get("workspaces", [])})
if args == ["status"]:  # preflight 只看 server 的 status 行
    print("server:\n  status: running")
    sys.exit(0)
fail("unsupported", "fake herdr: unsupported command: " + " ".join(args))
'''

FAKE_TOOL = r'''#!{python}
import json
import os
import re
import sys
import time
from pathlib import Path

TOOL = Path(sys.argv[0]).name
args = sys.argv[1:]
with (Path(__file__).resolve().parent / "tool-calls.log").open("a") as f:
    f.write(json.dumps({"tool": TOOL, "args": args, "cwd": os.getcwd()}) + "\n")
spec_file = Path("fake-%s.json" % TOOL)
spec = json.loads(spec_file.read_text()) if spec_file.exists() else {}
TEST_FILE_RE = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$")
if "output" in spec:
    if spec.get("sleep"):
        time.sleep(spec["sleep"])
        spec = json.loads(spec_file.read_text())
    sys.stdout.write(spec["output"])
    sys.exit(spec.get("exit", 0))


def vitest():
    picked = [a for a in args if TEST_FILE_RE.search(a)]
    if picked and spec.get("rerun_no_tests"):  # 复核点名的文件一个都没匹配上，命令又带 passWithNoTests（vitest 4.1.11 的样子）
        print(" RUN  v4.1.11 %s\n\nNo test files found, exiting with code 0\n" % os.getcwd())
        return 0
    failures = spec.get("failures", [])
    if picked:  # 只跑指定的文件（基线子集、超时复核）：断言失败照旧失败，超时单独跑时通过；rerun_fail 里的文件还失败（按断言失败打印）
        bad = set(spec.get("rerun_fail", []))
        failures = [f if f.get("kind", "assert") == "assert" else dict(f, kind="assert") for f in failures
                    if f["file"] in picked and (f.get("kind", "assert") == "assert" or f["file"] in bad)]
        files = picked
    else:
        files = sorted({f["file"] for f in failures} | set(spec.get("passing_files", ["src/ok.test.ts"])))
    prefix = spec.get("path_prefix", "")
    # project_labels 模拟配了 test.projects（.ts 在 project "ts"、.tsx 在 "tsx"）的 vitest 4：plain 是不带颜色的 "|ts| "，
    # color 是带 ANSI 的色块（FAIL 行整行按 vitest 4.1.7 的原样着色）；不写就是没有 projects 的旧输出。
    labels = spec.get("project_labels")
    color = labels == "color"

    def label(path):
        name = "tsx" if path.endswith("x") else "ts"
        return {"plain": "|%s| " % name, "color": "\x1b[30m\x1b[43m %s \x1b[49m\x1b[39m " % name}.get(labels, "")

    fail_head = "\x1b[41m\x1b[1m FAIL \x1b[22m\x1b[49m " if color else " FAIL  "
    sep = "\x1b[2m > \x1b[22m" if color else " > "
    failed_files = sorted({f["file"] for f in failures})
    out = [("\x1b[1m\x1b[30m\x1b[46m RUN \x1b[49m\x1b[39m\x1b[22m \x1b[36mv4.1.7 \x1b[39m" if color else " RUN  v1.6.0 ")
           + os.getcwd(), ""]
    out += [" %s %s%s%s (2 tests)" % ("❯" if f in failed_files else "✓", label(f), prefix, f) for f in files]
    if failures:
        out += ["", "⎯⎯⎯⎯⎯⎯ Failed Tests %d ⎯⎯⎯⎯⎯⎯" % len(failures)]
    for f in failures:
        path = prefix + f["file"]
        kind = f.get("kind", "assert")
        if kind == "file-timeout":  # 整个文件失败（beforeAll 超时）
            out += ["", fail_head + label(f["file"]) + path + ("\x1b[2m [ %s ]\x1b[22m" if color else " [ %s ]") % path,
                    "Error: Hook timed out in 10000ms."]
        else:
            out += ["", fail_head + label(f["file"]) + path + sep + sep.join(f["test"].split(" > ")),
                    "Error: Test timed out in 5000ms." if kind == "timeout"
                    else "AssertionError: expected 1 to be 2 // Object.is equality"]
        out.append(" ❯ %s:3:5" % path)
    unhandled = 0 if picked else spec.get("unhandled", 0)
    plural = "s" if unhandled > 1 else ""
    if unhandled:  # vitest 4.1.11：Unhandled Errors 一节没有 FAIL 行，汇总多一行 "Errors  N error(s)"，退出码 1
        out += ["", "⎯⎯⎯⎯⎯⎯ Unhandled Errors ⎯⎯⎯⎯⎯⎯", "",
                "Vitest caught %d unhandled error%s during the test run." % (unhandled, plural),
                "", "⎯⎯⎯⎯ Unhandled Rejection ⎯⎯⎯⎯⎯", "Error: unhandled boom",
                'This error originated in "src/ok.test.ts" test file.']
    test_failed = spec.get("tests_failed", sum(1 for f in failures if f.get("kind") != "file-timeout"))
    n = len(files)
    out.append("")
    out.append(" Test Files  %d failed | %d passed (%d)" % (len(failed_files), n - len(failed_files), n)
               if failed_files else " Test Files  %d passed (%d)" % (n, n))
    out.append("      Tests  %d failed | 10 passed (%d)" % (test_failed, 10 + test_failed)
               if test_failed else "      Tests  10 passed (10)")
    if unhandled:
        out.append("     Errors  %d error%s" % (unhandled, plural))
    print("\n".join(out))
    return 1 if failures or unhandled else 0


def pytest():
    fails, errors = spec.get("failures", []), spec.get("errors", [])
    picked = [a for a in args if a.endswith(".py") or "::" in a]
    if picked:  # 只跑指定的文件 / node id：只留落在这些文件里的失败
        files = {a.split("::", 1)[0] for a in picked}
        fails = [t for t in fails if t.split("::", 1)[0] in files]
        errors = [t for t in errors if t.split("::", 1)[0] in files]
    out = ["=" * 29 + " test session starts " + "=" * 30, "collected 12 items", ""]
    if fails or errors:
        out.append("=" * 27 + " short test summary info " + "=" * 28)
        out += ["FAILED %s - assert 1 == 2" % t for t in fails]
        out += ["ERROR %s - fixture 'db' not found" % t for t in errors]
    parts = (["%d failed" % len(fails)] if fails else []) + ["10 passed"] + (
        ["%d errors" % len(errors)] if errors else [])
    out.append("=" * 20 + " %s in 0.12s " % ", ".join(parts) + "=" * 20)
    print("\n".join(out))
    return spec.get("exit", 1 if fails or errors else 0)


def tsc():
    fails = spec.get("failures", [])
    for path, code in fails:
        print("%s(3,5): error %s: Type 'string' is not assignable to type 'number'." % (path, code))
    if fails:
        print("\nFound %d errors in %d files." % (len(fails), len({p for p, _ in fails})))
    return 2 if fails else 0


def eslint():
    errors, warnings = spec.get("failures", []), spec.get("warnings", [])
    picked = [a for a in args if not a.startswith("-") and os.path.isfile(a)]  # --ext .ts 的值、目录都不算
    if picked:  # 只查指定的文件
        errors = [e for e in errors if e[0] in picked]
        warnings = [w for w in warnings if w[0] in picked]
    for path in sorted({p for p, _ in errors + warnings}):
        print("\n" + str(Path.cwd() / path))  # eslint 默认打印绝对路径
        for p, rule in errors:
            if p == path:
                print("  3:7  error    'x' is assigned a value but never used  " + rule)
        for p, rule in warnings:
            if p == path:
                print("  5:1  warning  Unexpected console statement            " + rule)
    total = len(errors) + len(warnings)
    if total:
        print("\n✖ %d problems (%d errors, %d warnings)" % (total, len(errors), len(warnings)))
    return 1 if errors else 0


sys.exit({"vitest": vitest, "pytest": pytest, "tsc": tsc, "eslint": eslint}[TOOL]())
'''

# 假 ssh（按需装，见 install_ssh）：同目录 fake-ssh.json 的 fail 还大于 0 时，打一行 message（ssh 的报错原文）、退出 255，
# fail 减一（模拟偶发的网络失败）；之后把远端命令（git-upload-pack '<路径>' 这类）在本机执行，等于 ssh 到一台装了原版 git
# 的主机。每次调用的参数记进 fake-ssh-calls.log。
FAKE_SSH = r'''#!{python}
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = HERE / "fake-ssh.json"
args = sys.argv[1:]
with (HERE / "fake-ssh-calls.log").open("a") as f:
    f.write(json.dumps(args) + "\n")
spec = json.loads(SPEC.read_text())
if spec["fail"] > 0:
    spec["fail"] -= 1
    SPEC.write_text(json.dumps(spec))
    sys.stderr.write(spec["message"] + "\n")
    sys.exit(255)
while args and args[0].startswith("-"):  # git 给 ssh 的选项：-o SendEnv=GIT_PROTOCOL、-p 端口等
    args = args[2:] if args[0] in ("-o", "-p", "-i", "-F", "-l") else args[1:]
sys.exit(subprocess.call(["sh", "-c", " ".join(args[1:])]))
'''

# 假包管理器 pnpm / npm / uv（按需装，见 install_pkg_tools；deps 的用例用）：每次调用的工具名、参数和 stdin 的样子记进
# pkg-calls.log（stdin：tty 是终端，eof 是马上读到 EOF，open 是没关、读不到东西的管道，data 是有内容，closed 是没有 stdin；
# 只看不读，不会卡住）；同目录 fake-pkg.json 的 fail 里列了这个工具名时打一行错误、退出 1，什么都不建。装出来的样子：
#   pnpm install --dir <wt> …        在 <wt>/node_modules 里写 fake-installed（第几次装的）
#   npm ci --prefix <wt>             和真的 npm ci 一样先删掉已有的 node_modules，再写 fake-installed
#   uv sync --frozen --project <wt>  在 <wt>/.venv 里写 fake-installed（已有的 .venv 不删）
#   uv venv --seed --python <版本> <目录>   建 <目录>/bin/python 和 pyvenv.cfg
#   uv pip freeze --python <解释器>   打印两行固定的包和一行 -e file://<源仓库>（解释器往上三级，源 venv 里可编辑安装的本仓库）；
#   uv pip install 只记日志
FAKE_PKG = r'''#!{python}
import json
import os
import select
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = Path(sys.argv[0]).name
args = sys.argv[1:]


def stdin_state():
    try:
        if os.isatty(0):
            return "tty"
        ready, _, _ = select.select([0], [], [], 0)
    except OSError:
        return "closed"
    if not ready:
        return "open"
    return "eof" if os.read(0, 1) == b"" else "data"


with (HERE / "pkg-calls.log").open("a") as f:
    f.write(json.dumps({"tool": TOOL, "args": args, "stdin": stdin_state()}) + "\n")
spec_file = HERE / "fake-pkg.json"
spec = json.loads(spec_file.read_text()) if spec_file.exists() else {}
if TOOL in spec.get("fail", []):
    sys.stderr.write("fake %s: install failed\n" % TOOL)
    sys.exit(1)


def opt(name):
    return args[args.index(name) + 1]


def mark(folder):
    folder.mkdir(parents=True, exist_ok=True)
    m = folder / "fake-installed"
    m.write_text(str(int(m.read_text()) + 1) if m.exists() else "1")


if TOOL == "pnpm":
    mark(Path(opt("--dir")) / "node_modules")
elif TOOL == "npm":
    shutil.rmtree(Path(opt("--prefix")) / "node_modules", ignore_errors=True)
    mark(Path(opt("--prefix")) / "node_modules")
elif args[:1] == ["sync"]:
    mark(Path(opt("--project")) / ".venv")
elif args[:1] == ["venv"]:
    venv = Path(args[-1])
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    (venv / "pyvenv.cfg").write_text("version = %s\n" % opt("--python"))
elif args[:2] == ["pip", "freeze"]:
    print("requests==2.31.0\npytest==8.0.0\n-e file://%s" % Path(opt("--python")).parents[2])
'''
PKG_TOOLS = ("pnpm", "npm", "uv")
TOOLS = ("vitest", "tsc", "eslint", "pytest")
_shared: dict = {}  # 脚本内容 -> 这个进程里写好的那一份
_shared_dir: Optional[Path] = None  # 放这些脚本的进程级临时目录，第一次用时建、进程退出时删


def place(path: Path, body: str) -> None:
    """把内容是 body 的可执行脚本放到 path：每个进程只真正写一次（写进进程级的临时目录），之后各用例的 path 是它的硬链接。
    macOS 上新写出来的可执行文件第一次执行要多花 0.07～0.3 秒（实测，同一个文件之后再执行没有这笔开销），每个用例都
    新写一份的话，每个用例都要付一次。硬链接不改路径：脚本按自己所在的目录（fakebin）找状态文件，各用例互不影响。
    用例要换某个假工具的内容时先 unlink 再写（直接写会改到同一个 inode，同进程后面的用例也跟着变）。
    链不上（跨文件系统）时退回复制。"""
    global _shared_dir
    src = _shared.get(body)
    if src is None:
        if _shared_dir is None:
            _shared_dir = Path(tempfile.mkdtemp(prefix="drover-selftest-fakes-"))
            atexit.register(shutil.rmtree, _shared_dir, True)
        src = _shared_dir / f"fake-{len(_shared)}"
        src.write_text(body)
        src.chmod(0o755)
        _shared[body] = src
    try:
        os.link(src, path)
    except OSError:
        shutil.copy2(src, path)


def install(fakebin: Path, python: str) -> None:
    """把假 herdr 和假测试工具放进 fakebin（shebang 用当前解释器）。"""
    fakebin.mkdir(parents=True, exist_ok=True)
    scripts = {"herdr": FAKE_HERDR, **{name: FAKE_TOOL for name in TOOLS}}
    for name, body in scripts.items():
        place(fakebin / name, body.replace("{python}", python, 1))


def install_pkg_tools(fakebin: Path, python: str) -> None:
    """把假 pnpm / npm / uv 放进 fakebin（见 FAKE_PKG）：PATH 里排在本机真的包管理器前面，不联网、不装东西。"""
    for name in PKG_TOOLS:
        place(fakebin / name, FAKE_PKG.replace("{python}", python, 1))


def install_ssh(fakebin: Path, python: str, fail: int, message: str) -> None:
    """把假 ssh 放进 fakebin：前 fail 次打 message、退出 255，之后在本机执行远端命令（见 FAKE_SSH）。"""
    (fakebin / "fake-ssh.json").write_text(json.dumps({"fail": fail, "message": message}))
    place(fakebin / "ssh", FAKE_SSH.replace("{python}", python, 1))
