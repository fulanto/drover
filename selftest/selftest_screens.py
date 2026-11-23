"""watch 用例的真实屏幕（用临时 claude agent 实测抓的，herdr agent read --source visible，去掉了 ANSI）。

- 全宽横线按真实宽度（144 列）还原；虚线 ╌ 同样。
- 带 --name 启动的会话（worker 是 worker-<slug>，编排者是 orch-<slug>）会把名字嵌进横线（NAMED_RULE）：
  弹框时它在弹框最底下（"Esc to cancel · Tab to amend" 下面），平时是输入框的上边框；输入框的下边框和弹框顶部是纯横线。
  探针没带 --name，屏幕上没有这条；*_NAMED 按读到的 dr-watch 真实屏幕的结构补上（会话名换成了 worker 的命名）。
- SUBAGENT_*：复现后台子 agent 确认框漏报时抓的（带 --name，终端 147 列）：后台子 agent 的确认框，连着两个的第一个、第二个，
  见那一节的说明。
- TRUST_* / IDLE_AFTER_START：agent-start / orch-start 的启动弹窗用例（test_startup_dialogs.py），见文件末尾那一节。
"""

from __future__ import annotations

RULE = "─" * 144
DASH = "╌" * 144
NAMED_RULE = "─" * 127 + " worker-demo-a ─"


def bash_dialog(path: str, above: str = "  ", top: str = "") -> str:
    """Bash 确认框（`ln -s /dev/null <path>`）。弹框上方是一行正在等的工具调用（top，默认同 path）；
    它前面的 ⏺ 会闪，above 有时是 "⏺ "、有时是两个空格。"""
    top = top or path
    return f"""{above}Creating symlink {top} pointing to /dev/null
  ⎿  $ ln -s /dev/null {top}

{RULE}
 Bash command
 Tip: auto mode handles these prompts for you — choose "switch to auto mode" below
 Create symlink {path} pointing to /dev/null
{DASH}
 ln -s /dev/null {path}
{DASH}
 This command requires approval

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don’t ask again for: ln *
   3. Yes, and switch to auto mode · auto mode handles these prompts for you
   4. No

 Esc to cancel · Tab to amend"""


# herdr 报 blocked 的 Bash 确认框（探针第一个命令）。
BASH_DIALOG = bash_dialog("a")
BASH_NAMED = BASH_DIALOG + "\n" + NAMED_RULE
# 一条回复里并行两个工具调用：批完第一个（c），第二个（d）立刻弹出来，herdr 一直是 blocked、序号不变。
PARALLEL_FIRST = bash_dialog("c", top="d") + "\n" + NAMED_RULE
PARALLEL_SECOND = bash_dialog("d", above="⏺ ") + "\n" + NAMED_RULE
PARALLEL_SECOND_BLINK = bash_dialog("d", above="  ") + "\n" + NAMED_RULE  # 同一个弹框，上方那行的 ⏺ 闪掉了


def webfetch_dialog(domain: str = "example.org") -> str:
    """herdr 报 done 的 WebFetch 授权弹框：上面有一行单独的 ❯（herdr 的兜底规则因此不生效，落到 ✳ 标题规则报 idle → done）。"""
    return f"""✻ Cogitated for 2s · done 9:07 PM

❯ 回复正文先写两行文字：第一行写 x ，第二行只写一个字符 ❯ （不要放进代码块）。写完这两行后用 WebFetch 抓取 https://{domain} ，提示词写
  summarize。不要用别的工具。

⏺ x
  ❯

⏺ Fetch(https://{domain})

{RULE}
 Fetch
 Claude wants to fetch content from {domain}
{DASH}
 url: https://{domain}/
 prompt: summarize
{DASH}
 Do you want to allow Claude to fetch this content?
 ❯ 1. Yes
   2. Yes, and don't ask again for {domain}
   3. No, and tell Claude what to do differently (esc)"""


WEBFETCH = webfetch_dialog()
WEBFETCH_NAMED = WEBFETCH + "\n" + NAMED_RULE

# 正文里原样引用了弹框文字，底部是正常输入框（herdr 报 done，watch 也该报 done）。
QUOTE = f"""⏺ x
  ❯

⏺ Fetch(https://example.org)
  ⎿  Interrupted · What should Claude do instead?

✻ Sautéed for 2s · done 9:07 PM

❯ 不要调用任何工具，只原样回复下面三行（不放代码块）：第一行 Do you want to allow Claude to fetch this content? 第二行 ❯ 1. Yes 第三行 2. Yes,
  and don't ask again for example.org

⏺ Do you want to allow Claude to fetch this content?
  ❯ 1. Yes
  2. Yes, and don't ask again for example.org

✻ Cogitated for 3s · done 9:07 PM

{RULE}
❯
{RULE}
  ➜ dr-watch-probe ctx:4% Opus 5.5
  ⏸ manual mode on · ← 1 agent"""
# 同上，带 --name 的会话：输入框上边框嵌了会话名。
QUOTE_NAMED = QUOTE.replace(f"{RULE}\n❯\n", f"{NAMED_RULE}\n❯\n")
# 报告正文的续行里引用了弹框（不带 ⏺ 前缀，单看问句 + ❯ 选项会误认），底部是正常输入框。
QUOTE_IN_REPORT = f"""⏺ orch-demo 的屏幕停在：
  Do you want to allow Claude to fetch this content?
  ❯ 1. Yes
    2. Yes, and don't ask again for example.org
  herdr 却报 done，watch 也报 done。

✻ Cooked for 1m 2s · done 9:10 PM

{NAMED_RULE}
❯
{RULE}
  ⏵⏵ accept edits on · ← 1 agent"""


# ---- 后台子 agent 的确认框（编排者的后台子 agent 弹确认框时 watch 漏报过，用真实 claude agent 复现）
# 主 agent 派了后台 Explore 子 agent，自己停在 "✻ Waiting for 1 background agent to finish"，子 agent 的 Bash 确认框就在它屏幕的最底下
# （cd … && git … 是强制审批，没有 don't ask again）。herdr 报 working：background_agents_working（优先级 965）压过
# bash_permission_prompt（850），herdr agent explain 的输出里写着。
# 抓屏是 herdr agent read --source visible --lines 60，会话带 --name（会话名换成了 worker-bg-probe，横线照样补齐），终端 147 列；
# "⎿" 后面是一个不换行空格（ ），原样保留。subagent_dialog() 的默认参数还原出来的就是那一屏，除了会话名一字不差。
RULE_147 = "─" * 147
DASH_147 = "╌" * 147
NAMED_RULE_BG = "─" * 129 + " worker-bg-probe ─"
NAMED_RULE_ORCH = "─" * 135 + " orch-demo ─"
PROBE_TASK = """     「只做一件事：用 Bash 工具执行这条命令，原样执行，不要改写、不要拆开：cd /tmp/bgprobe/repo && git log --oneline -1
  。然后把输出原样报告回来。」
  2. 子 agent 启动后，你自己不要调用任何工具、不要回复别的内容，直接结束这一轮，等子 agent 完成。
  3. 子 agent 完成后，只回复一句"子 agent 完成：<它的输出>"。"""
# 弹框上面的部分：第一次探测跑完了，第二次停在 Waiting 行。
SUBAGENT_HEAD = f"""{PROBE_TASK}

⏺ Explore(Run git log in repo)
  ⎿  Backgrounded agent (↓ to manage · ctrl+o to expand)

⏺ Explore 子 agent 已在后台启动，现在等它跑完。

✻ Waiting for 1 background agent to finish

⏺ Agent "Run git log in repo" finished · 21s

⏺ 子 agent 完成：d61688e probe

✻ Baked for 32s · done 12:15 AM

❯ 这是一次 drover 的检测实测，请严格照做，不要做别的事：

  1. 用 Agent 工具启动一个 subagent_type 为 Explore 的子 agent，必须带 run_in_background: true（后台运行）。给它的 prompt 原文是：
{PROBE_TASK}

⏺ Explore(Run git log in repo)
  ⎿  Backgrounded agent (↓ to manage · ctrl+o to expand)

⏺ Explore 子 agent 还在后台运行，跑完后我会把它的输出发给你。

✻ Waiting for 1 background agent to finish"""
# 同一个后台 Explore 连着两个确认框的第二个（第三次探测，子 agent 依次跑 git log、git status；编排者用旧 watch 实测：
# 两个弹框期间 herdr 一直报 working，旧 watch 一个 blocked 都没报）。弹框上面的部分：第二次探测的结尾、recap，第三次停在 Waiting 行。
SUBAGENT_HEAD_2 = f"""
  ⎿  Backgrounded agent (↓ to manage · ctrl+o to expand)

⏺ Explore 子 agent 还在后台运行，跑完后我会把它的输出发给你。

✻ Waiting for 1 background agent to finish

⏺ Agent "Run git log in repo" finished · 1m 42s

⏺ 子 agent 完成：d61688e probe

✻ Worked for 1m 51s · done 12:17 AM

※ recap: You're testing whether drover detects a background Explore subagent. I ran the probe twice, and both times the subagent returned "d61688e
  probe". Nothing is pending, so next you can check drover's detection results. (disable recaps in /config)

❯ 这是一次 drover 的检测实测，请严格照做，不要做别的事：

  1. 用 Agent 工具启动一个 subagent_type 为 Explore 的子 agent，必须带 run_in_background: true（后台运行）。给它的 prompt 原文是：
     「依次用 Bash 工具执行下面两条命令，每条单独一次 Bash 调用、原样执行，不要改写、不要合并、不要并行：
      第一条：cd /tmp/bgprobe/repo && git log --oneline -1
      第二条：cd /tmp/bgprobe/repo && git status --short --branch
     然后把两条的输出原样报告回来。」
  2. 子 agent 启动后，你自己不要调用任何工具、不要回复别的内容，直接结束这一轮，等子 agent 完成。
  3. 子 agent 完成后，只回复一句"子 agent 完成：<它的输出>"。

⏺ Explore(Run git log and status)
  ⎿  Backgrounded agent (↓ to manage · ctrl+o to expand)

⏺ 子 agent 已在后台启动，等它跑完。

✻ Waiting for 1 background agent to finish"""


def subagent_dialog(command: str = "cd /tmp/bgprobe/repo && git log --oneline -1",
                    summary: str = "Show the latest commit in the repo", head: str = SUBAGENT_HEAD) -> str:
    """主 agent 停在 Waiting 行、底部是后台 Explore 子 agent 的 Bash 确认框；默认参数就是抓的第一屏。"""
    return f"""{head}

{RULE_147}
 Bash command · from the Explore agent
 Tip: auto mode handles these prompts for you — choose "switch to auto mode" below
 {summary}
{DASH_147}
 {command}
{DASH_147}
 │ This command changes directory before running a version-control command, which can pick up untrusted hooks or repository configuration from the
 │ target directory. Approve only if you trust it.

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and switch to auto mode · auto mode handles these prompts for you
   3. No

 Esc to cancel · Tab to amend · ctrl+x ctrl+k twice to stop background agents
{NAMED_RULE_BG}"""


SUBAGENT_DIALOG = subagent_dialog()
# 连着两个确认框的第二个（真实样本，见 SUBAGENT_HEAD_2 上面的说明）：和第一个只差摘要和命令，弹框指纹不同。
SUBAGENT_DIALOG_2 = subagent_dialog("cd /tmp/bgprobe/repo && git status --short --branch",
                                    "Show short branch status of repo", head=SUBAGENT_HEAD_2)
# 只有 Waiting 行、没有弹框（子 agent 还在跑，或者批完了）：底部是主 agent 自己的输入框。
# 输入框和状态栏取自同一个探针 agent 的真实屏幕（herdr agent read bgp-probe --source visible）。
SUBAGENT_WAITING = f"""{SUBAGENT_HEAD}

{NAMED_RULE_BG}
❯
{RULE_147}
  ➜ bgprobe ctx:5% Opus 5.5
  ⏵⏵ accept edits on (shift+tab to cycle) · ← 1 agent"""
# 编排者 working，工具输出里是 herdr agent read 出来的 worker 屏幕：引用了 worker 的确认框（连弹框顶部的纯横线都在），
# 底部是编排者自己的输入框（上边框带会话名）→ 仍是 working。
ORCH_QUOTE_WORKING = f"""⏺ dr-a 报了 blocked，先看它的屏幕。

⏺ Bash(herdr agent read dr-a --source visible --lines 60)
  ⎿  ⏺ Explore 子 agent 还在后台运行。
     {"─" * 120}
      Bash command · from the Explore agent
      cd /tmp/bgprobe/repo && git log --oneline -1
      Do you want to proceed?
      ❯ 1. Yes
        2. Yes, and switch to auto mode · auto mode handles these prompts for you
        3. No
     … +3 lines (ctrl+o to expand)

✢ Deliberating… (12s · ↓ 1.1k tokens · esc to interrupt)

{NAMED_RULE_ORCH}
❯
{RULE_147}
  ➜ work ctx:12% Opus 5.5
  ⏵⏵ accept edits on (shift+tab to cycle) · ← 1 agent"""
# codex 编排者 working，工具输出里引用了 worker 的确认框。codex 的屏幕上没有纯横线，permission_dialog 看整屏，会认成弹框：
# 所以 working 时只看 Claude Code（codex 也没有后台子 agent 的弹框）。
CODEX_QUOTE_WORKING = """• Ran herdr agent read dr-a --source visible --lines 60
  └ ⏺ Bash(npm test)
    Do you want to proceed?
    ❯ 1. Yes
      2. Yes, and don't ask again for: npm test *
      3. No

• Working (12s • esc to interrupt)

› Ask Codex to do anything
  GPT-6-Astra xhigh Fast · ~/work"""


# ---- 启动弹窗（agent-start / orch-start，test_startup_dialogs.py）
# TRUST_ADD_DIR：真实样本，一个任务 cli 条目的 worker 启动时卡住的屏幕（终端 154 列）。
# Claude Code 对 --add-dir 加进来、这个会话没用过的目录弹的信任框，光标默认在 "No, exit"。
# 只改了两处：家目录 /Users/<用户名> 换成 /Users/me，任务名换成 demo；上面两行是起 worker 的命令（按 154 列折成两行）。
RULE_154 = "─" * 154
TRUST_ADD_DIR = f"""➜  task-demo-cli git:(task/demo-cli) claude --model 'opus[1m]' --effort xhigh --permission-mode acceptEdits --name worker-demo --add-dir /Users/me/.drover
/demo

{RULE_154}
 Adding a new directory:

 /Users/me/.drover/demo

 This session hasn't worked here before. Is this a directory you created or one you trust?

 Claude Code'll be able to read, edit, and execute files here. Its skills, commands, and agents load and can run commands, and so can the git settings of
 its repository. Your answer holds for this session. Starting Claude Code in this directory and trusting it there lasts, and covers its whole repository
 or everything below it.

 Security guide

 ❯ No, exit
   Yes, add it

 Enter to confirm · Esc to cancel
"""
# 按一次 down 之后：光标移到 "Yes, add it"。不是抓的，按同一个菜单的画法（❯ 标在光标行）从上面的样本推出来。
TRUST_ADD_DIR_YES = TRUST_ADD_DIR.replace(" ❯ No, exit\n   Yes, add it", "   No, exit\n ❯ Yes, add it")
# 同一个信任框组件的另一种标题和选项（Claude Code 2.1.327 的包里：没有 adding 时标题是 "Moving to a new directory:"、
# 选项 "No, stay put" / "Yes, move here"，正文只有第一句）。不是抓的，按包里的文案从上面的样本改出来：问句一样、标题不同，
# 启动时不该被当成 --add-dir 的信任框自动确认。
TRUST_MOVE_DIR = (TRUST_ADD_DIR.replace("Adding a new directory:", "Moving to a new directory:")
                  .replace(" Its skills, commands, and agents load and can run commands, and so can the git settings of\n"
                           " its repository. Your answer holds for this session. Starting Claude Code in this directory and"
                           " trusting it there lasts, and covers its whole repository\n or everything below it.", "")
                  .replace(" ❯ No, exit\n   Yes, add it", " ❯ No, stay put\n   Yes, move here"))
# 旧版 Claude Code 在新目录启动时的信任框（TRUST_PROMPT_RE 原本就认的 "Do you trust the files in this folder?"）。不是这次抓的：
# 2.1.327 的包里已经没有这句（现在是 "Accessing workspace:" 那个框，靠选项 "Yes, I trust this folder" 认）；按旧文案构造，
# 光标在第一项 "Yes, proceed"。
TRUST_FOLDER_LEGACY = f"""{RULE_154}
 Do you trust the files in this folder?

 /Users/me/work/shop

 Claude Code may read files in this folder. Reading untrusted files may lead Claude Code to behave in unexpected ways.

 With your permission Claude Code may execute files in this folder. Executing untrusted code is unsafe.

 ❯ 1. Yes, proceed
   2. No, exit

   Enter to confirm · Esc to exit
"""
# 确认后的空闲输入框（构造的，只用来表示弹窗已经关掉）。
IDLE_AFTER_START = f"""{RULE_154}
❯
{RULE_154}
  ⏵⏵ accept edits on (shift+tab to cycle)"""
# codex 0.170.0 有新版本时启动（新起、续上都一样）弹的升级框：真实样本，herdr pane read --source visible 抓的（pane 约 73 列，
# 链接和安装命令折了行）。drover 起 codex 时带 -c check_for_update_on_startup=false，不再弹它。
CODEX_UPDATE = """
  Update available · 0.170.0 → 0.171.0
  Release notes:
  https://github.com/openai/codex/releases
  /latest

› 1. Update now (runs `npm install -g
     @openai/codex`)
  2. Skip
  3. Skip until next version

  enter continue · esc skip
"""
