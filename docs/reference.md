# 子命令和目录布局

## 子命令速查

脚本是 `~/.drover/bin/drover <子命令>`。除 `watch`、`heavy`（输出原样透传）、`board`、`rollup`、`rules`（输出给人看的文本，`board --json` 才是 JSON）和 `update`（默认打印几行中文，加 `--json` 才输出 JSON）外，每个子命令都向 stdout 输出一个 JSON：`ok` 表示成不成功，失败时 `error` 写明原因和下一步。`<子命令> --help` 查参数；会改动东西的子命令都支持 `--dry-run`，只列出要执行的命令。

"谁用"一栏：**你** = 日常会直接用；**主会话** = 路由时用；**编排者** = 主要由编排者（或 worker）调用，一般不用手敲。

| 子命令 | 做什么 | 谁用 |
|---|---|---|
| `orch-status` | 列出编排者：herdr 状态 + rollup 状态行和摘要（`rollup_summary`），按屏幕修正的 `screen_status`，卡在确认框上的列进 `blocked`；`unread` 是主会话还没读过这一版 rollup，`answered_after_rollup` 是 rollup 之后转过你的答复，`needs_attention` 只算未读、编排者已停下的；`--all` 含已退出的，`--with-sub` 含子编排者。编排者名被后来的任务复用时，herdr 里活着的同名 agent 只算这个名字最新那份 `orch.json` 的（按文件修改时间），更早的同名任务按已退出（`gone`）列；任务换编排者时换下来的名字（`orch.json` 的 `retired_names`）仍算这个任务占着，旧编排者还活着也不会算给更早的同名任务；`orch-start` 正在起的那一份不列 | **你**、主会话 |
| `board` | 看板，每次现场从 rollup 和 herdr 生成：一行一个任务，按等你 / 进行中 / 完成分组，一组一张表，列是 任务（中文标题）\| 当前状态 \| 要你做什么 \| 状态持续 \| 代号，完成表多一列收尾；写了提醒的任务，提醒跟在它下面（框线表里写在那个任务最后一行下面、后面几列合成一格，Markdown 每张表下面"提醒（<任务>）：…"），最底下一行"主会话在处理"（确认框、接口错误、退出按状态本身持续了多久，记在 `<slug>/agent-state.json`）。默认输出 Markdown（GFM 表格，主会话在终端里原样回给用户）；`--plain` 纯文字（飞书用，一个任务两行、写了提醒的三行，最后一行写能收尾几个）。第一行末尾是 drover 的版本号（正好在版本 tag 上是 `v0.3.0`，tag 之后 N 个提交是 `v0.3.0+N`，历史上没有版本 tag 时是 7 位 sha），更新过（自动的、手动的，两头也是版本号）、被挡住时多一行（[自动更新](auto-update.md)）。读屏的几种（文本、`--plain`、`--json`、`--live`）顺带把显示状态的变化、编排者每换一段状态补进时间线 `<slug>/timeline.jsonl`（同 `watch --main`）。`--json` 给主会话用（`tasks` 每项的 `reasons` 列出每个原因的 `kind`、`text`、`since`、`since_source`，`wrapup`、`wrapup_reason`、`deliverables`（旧 rollup 的"## 交付"，没有是空的），`title`、`ask`、`display_state`、`state_since`、`state_seconds`、`total_seconds`、`wrapup_clicked`、`note`（提醒原文，没有是 null）、`tab_id`（编排者所在的 tab，主会话收尾、换编排者时 `herdr tab close` 它；旧任务没有是 null）；顶层 `wrapup_ready` 是能一键收尾的任务，点过的也算，主会话还没读这一版的不算（收尾格写"先别：结论还没转达"）；另有 `drover` 字段：版本号（`version`）和 7 位 sha（`commit`）、最近一次更新（`auto_update.source` 是 auto / manual）、`rules_changed`）；`--line` 一行汇总（等你 / 进行中 / 完成，给状态栏，不读屏、不写文件）；`--pending [--except <slug>]...` 只列其他在等你的（给转达当脚注，没有就不输出）；`--live [--interval 15]` 循环重画（框线表，当前状态、要你做什么写长了在格子里折行、最多 3 行，比终端宽时任务列固定、其余整列左右滚，太窄退回列表），顺带补登记丢了名字的主会话（工作目录是主工作台的才补），主会话的追踪断了（"主会话在处理"不空、`watch --main` 的心跳 `main-watch-live.json` 停了 5 分钟以上）给主会话送一句"接上"（同一段断档只送一次，看板上留一行），在看板 tab 里时 tab 名跟着汇总改、能点某一行的 [收尾]（留在看板，变"收尾中"）和 [全部收尾]（送完跳回主会话；都交给飞书插件的 `send-main.mjs` 送进主会话）、点 [<] [>] 或触控板左右划、← → 键左右滚，点别处什么都不做，按住左键拖动选中、松开自动复制（OSC 52，底下显示"已复制 N 行"；按下松开在同一格才算单击，拖动中不刷新），每轮写心跳 `board-live.json`；装着的 drover 换了版本（两边都取得到、又不一样）就试跑新代码、恢复终端、原样重新执行自己（进程号不变），新代码跑不起来时看板上写原因、接着用旧代码、下一轮再试；`--tab` 在主对话所在的 workspace 里开一个跑 `--live` 的"看板"tab（不抢焦点），已经有了就复用，复用时心跳没有、不是这个 pane 的、版本和装着的不一样、超过 120 秒没更新就 `herdr pane send-keys <pane> ctrl+c` 再 `pane run` 重启（`restarted: true`、`restart_reason`），顺带关掉 `--pane` 开的旧分屏（`--dry-run`）；`--pane` 是旧的下方分屏，留着向后兼容。文本、`--plain`、`--json`、`--live` 会触发自动更新检查 | **你**、主会话 |
| `rollup` | `--slug <slug>` 或 `--name <编排者名>`（二选一；`--name` 按 orch.json 找任务，同名取最新的，`watch --main` 的事件行带的就是编排者名）：打印 rollup 原文，开头一行说和主会话上次读的相比改了什么（第一次读 / 内容没变 / 改了 N 行并列出）。主会话读（`HERDR_PANE_ID` 等于 `main-session.json` 的 `pane_id`，`main-register` 登记时记的）同时记为已读（`main-seen.json`），读到新内容时在时间线记一条；别人读（编排者、worker、普通终端）照样输出，最前面多一行"# 不是主会话（原因），没记已读"，两样都不写 | 主会话；编排者也能用 |
| `rules` | 主会话读 drover 规则的改动，不用 `/clear`（[自动更新](auto-update.md)）：`AGENTS.md` 里的 drover 区块和主会话载入时的（`main-rules.json`）比，开头一句"从现在起以下面为准"，再是改动的行；`--full` 给整个区块。主会话读完记成载入时，最近一次自动更新标成已报；不是主会话跑的（同 `rollup` 按 `HERDR_PANE_ID` 认）照样输出、开头多一行说明，什么都不记。`--hook` 给 Claude Code 的 UserPromptSubmit hook 用（只在主会话、规则变了时输出，退出码恒为 0） | 主会话 |
| `oncall` | 飞书 oncall（[飞书 oncall](oncall.md)）：`pair` 发起扫码配对，输出里的 `link` 是配对链接（Mac 桌面没弹二维码时用飞书打开它），`--wait` 等到配上再自动发测试消息；`unpair` 解绑、`test` 发测试消息、`on` / `off` 开关（交给插件执行，立即返回，真实结果看 `herdr plugin log list --plugin com.codreamer.herdr.oncall --limit 5`）；`status` 看插件版本、开关、飞书配没配、poller、`drover.env`、主会话登记了没有、飞书 oncall 归属哪个 herdr（`plugin_status.owner`；归属的不是现在这个时 `hints` 里说明后果和怎么切过来），不打印密钥 | **你** |
| `main-register` | 把当前会话在 herdr 里登记成主会话（改名为 `drover`）：已经是就什么都不做；别的 pane 已经登记了、自己叫别的名字、在编排者的 `orch` workspace 里都不改，报出原因。`--hook` 给 Claude Code 的 SessionStart hook 用（静默、退出码恒为 0）。认出主会话后记下载入时的规则（`main-rules.json`）、触发自动更新检查；登记上了写 `main-session.json`（`session_id`、`pane_id`、`source`，`rules --hook`、`watch --main`、`drover rollup` 用；Claude Code 不带 `--hook` 的兜底登记只在记的不是这个 pane 时补写，`session_id` 写 null） | 主会话（会话开始时）；你排障时手动跑 |
| `update` | 更新 drover（[检查、更新、卸载](install.md#检查更新卸载)）：按安装记录找到 clone，fetch 上游只快进（`main` 通道顺带拿回上游历史上的 tag，本地已有的同名 tag 不动），按原来的 `--mode` / `--host` 重跑 install、再 `--check`，最后几行中文说更新到哪（新旧都写版本号，同看板第一行）、改了什么、下一步（规则变了就跑 `drover rules`）；本地有改动、分叉、连不上远端时什么都不改、退出码 1；跑着的版本真的换了时和自动更新一样记进 `auto-update.json`（看板第二行"drover 已更新 <旧>→<新>（时间，手动）"）。`--dry-run` 只 fetch、看有没有新版本，`--json` 输出 JSON；`--auto` 是后台的自动更新（[自动更新](auto-update.md)：无人值守、没新提交且这一版装好了才不重跑 install，没装好的每次检查都重跑，结果写 `auto-update.json`），一般不用手敲 | **你**、主会话；`--auto` 由主会话那边的命令在后台起 |
| `watch` | 追踪 agent 状态，变化时输出一行 `EVENT <名字> <状态> @<时间>`（编排者另有 `rollup:<状态>` 事件，rollup 按内容判断改没改）；`--once` 只查一次，`--until-event` 第一个事件就退出（启动时就有 `blocked` / `api-error` / `gone` 的输出完就退出）。`--main` 是主会话专用：不写名字就自动盯所有顶层编排者，只报要处理的事（未读的 rollup，编排者写完"完成"就退出了的也报；确认框、接口错误、意外退出、停住：显示状态是进行中或已转答复、编排者停着，和看板同一个判定），另外报 drover 自己的事（`drover updated(…)`、`drover update-blocked`、`drover update-unreachable(24h)`、`/clear` 之后的 `drover session-restarted`），报过的记在磁盘上，重挂不重报（rollup 仍未读、确认框还在的 10 分钟后再报），两个同时跑也只报一次；每轮写心跳 `main-watch-live.json`（看板靠它发现追踪断了） | **你**、主会话、编排者 |
| `history` | 按仓库列出历史派发记录（结论、未决、踩坑），`--grep` 摘出相关行 | **你**、编排者 |
| `playbook` | 读团队 profile 和个人覆盖目录的规则：`general.md` 和每个 `--repo` 的 `repos/<仓库名>.md`，按层拼好；`--text` 只打印正文。配置了 profile 但目录不存在时报错 | **你**（看生效的规则）、编排者 |
| `preflight` | 检查是否在 Herdr 里、herdr server 是否在跑、常用工具装没装 | **你**（排障）、编排者 |
| `accept-summary` | 从验收存档重新打印摘要（含测试计数、证据逐条的结论、worker 的 heavy 流水），可附失败输出 | **你**（想看验收细节时）、编排者 |
| `heavy` | 经调度执行构建 / 测试命令，输出原样透传，退出码同被执行的命令。按"机器还剩多少、这条要多少"决定马上跑还是排队：容量从本机硬件值起步（Apple 芯片的性能核数，其他机器的逻辑核数），之后按真实命令的表现自动校准（同时跑明显变慢就降、接近容量还几乎不变慢就加，config 可固定），需求按这条命令自己最近几次的实测（CPU 时间 ÷ 墙钟）算，它没跑过就看同类命令（同一命令形状）、再看同程序的；放行按实时 CPU（设计底线：CPU 空着不能干等）：整机实测没满、heavy 自己实测的占用加上这条不超过容量的 1.2 倍就跑，真满了才排队；没跑过的先按 1 核，历史上 10 秒内跑完的小命令不排队，排头等太久时后来的只放能在它开跑前跑完的，内存严重紧张时只放单独一条。排队时打印这条要多少、整机实测忙多少、heavy 占多少 / 容量、在跑的是谁；`--status` 看全机调度状态。细节和来由见[目录布局](#目录布局)的 `locks/heavy/`。只执行构建 / 测试工具（跳过开头的 `env` 和 `VAR=值` 之后，程序名得是 mvn、gradle、npm、bun、vitest、tsc、python、node、pytest、make、go、cargo 这类），别的命令不执行、退出码 2：全局白名单 `Bash(~/.drover/bin/drover heavy:*)` 放行了它（本机所有会话都有，包括 worker；整个 CLI 的放行只在主工作台），它不能变成放行任意命令的口子。任务书里写成字面的 `~/.drover/bin/drover heavy -- <命令>`，worker 跑时命中白名单、不弹框。在分支是 `task/<slug>…` 的 worktree 里跑时记流水：输出改经管道原样转发（stdout / stderr 分开，子进程看到的不是终端，和 accept 截输出跑时一样），跑完往 `<slug>/heavy-runs.jsonl` 追加一行（worktree、命令、退出码、汇总行、跑之前和跑完的改动指纹），stderr 打"流水 #N"给 worker 报告引用；别的地方照旧继承终端、不记 | worker（测试、lint、类型检查、构建一律经它）、编排者；你手动跑也可以用 |
| `orch-start` | 为一个任务起编排者：在 herdr 的 `orch` workspace 里开一个 tab，启动 claude 或 codex，发开场 prompt。启动弹窗和 `agent-start` 一样自动处理（包括 Claude Code 对 `--add-dir` 加进来的状态目录弹的 "Adding a new directory" 信任框），处理完再发开场 prompt。`--model <完整模型 ID>` / `--effort` 是这个任务的默认：编排者用它起，记进 `orch.json`、写进开场 prompt，之后同宿主的 `agent-start`、`review`、子编排者不传就继承（换编排者 `resume` 时沿用前任记的）；不传时和以前一样用 config。`orch.json` 在起 agent 之前就写好（起好再补 `resume_reported_session`）：编排者名可以被后来的任务复用，活着的同名 agent 算最新那份记录的，新 agent 出现在 herdr 里时它就得是新任务的，不然会被算给同名的旧任务（`watch --main` 把旧任务的 rollup 当成新任务的报出来）。从写记录到起好这一段拿着 `locks/orch-start-<slug>.lock`，`watch --main`、看板、`orch-status`、`rollup --name` 这一段都不算这份记录（不把还没出现的编排者报成 `gone`）。起不来（herdr 报错、名字被活着的 agent 占着、起来又退出了）时恢复原样：新任务不留 `orch.json`，换编排者（`resume`）的把原来那份一字不差写回去、修改时间也还原，不记时间线；卡在不认识的启动界面上的（`ok: false`、agent 还活着）照旧留着记录。换编排者（`resume`）名字变了时，被换下的名字记进新记录的 `retired_names`（连着换几次累积）：路由规则是新的起好才关旧 tab，旧编排者这段时间还活着，它的名字仍归这个任务，不会落回更早的同名任务；`rollup --name <换下的名字>` 报找不到编排者。起好就把续命令报给 herdr（返回里的 `herdr_resume`，见 [herdr 重启后按原参数拉回](#herdr-重启后按原参数拉回)） | 主会话（上层编排者起子编排者也用它） |
| `prompt` | 把文件（或 `--text` 短消息）发给某个 agent，看到它开工（working）就返回（正常 1 秒内，最多等 30 秒），之后交给 `watch`；发给编排者成功后记一个 `answered.json`（看板靠它分出"已转答复，等它接着做"：rollup 从那时起内容没变才算），时间线记一条转答复（内容以"收尾"开头的再记一条收尾请求）；发给顶层编排者时它的 rollup 在主会话上次读之后又改过的，照发、返回里多一个 `warning` | 主会话（转达）、编排者 |
| `wt` | 建或复用 worktree。新建前先 fetch 源仓库主检出的上游，主检出干净且落后时快进（只快进，不硬覆盖），从远端最新起（主检出有改动、分叉时不动它，改从上游起并在 `warnings` 里报出；上游在远端已删当作没有上游；主检出不在远端默认分支上时提示一句；`--no-sync` 不同步）；复制 `.claude/settings.local.json`、`.env*`（源仓库没跟踪的，或在新 worktree 里被 gitignore 的），以及被 gitignore 的仓库规范文件（`CLAUDE.md` 等），以及仓库根目录 `.worktreeinclude`（Claude Code 的约定，gitignore 语法）匹配到的被忽略文件（如 Java 仓库的本地配置、本机测试页；在新 worktree 里不被忽略的不复制，列在 `worktreeinclude_skipped`）。多轮任务的新一轮（`--slug`，不传按分支名 `task/<slug>…` 归属；任务在这个仓库有推送记录时）：不传 `--base` 从上一轮推的远端分支起（推过两条以上要显式 `--base`）；建之前核实起点含上一轮推的提交（同分支看祖先，换分支按补丁内容比），不含就 `ok: false`、不建，列出缺的提交和两种做法；`--carry "<理由>"` 带上缺的提交照常建（理由记进 `carry.jsonl`）。推到别处的推送（remote 之后换过地址，按记录里的 `remote_url` 比主机 + 路径认，同一个仓库的 ssh / https / scp 写法算同一个）不拿来定起点、不核实、不进 `--carry`，列在 `round_check.other_remote`、进 `warnings`；全推到了别处时 `round_check.status` 是 `other_remote`，起点照第一轮。缺的提交来自没记地址的旧记录时，报错多一段：换过地址就别 `--carry`，给记录补上 `remote_url`（`no_remote_url` 给出记录所在的文件）。没有推送记录时起点和以前一样 | 编排者 |
| `deps` | 在 worktree 里独立安装依赖（不软链源仓库） | 编排者 |
| `agent-start` | 在 pane 里启动 claude / codex worker，自动处理启动弹窗：新目录的信任弹窗、Claude Code 对 `--add-dir` 加进来的目录弹的 "Adding a new directory" 信任框（光标默认在 "No, exit"，移到 "Yes, add it" 再确认）。codex 带关掉启动版本检查的开关，不弹升级提示。模型和 effort：`--model` / `--effort` > 任务 `orch.json` 记的（宿主和编排者相同才继承）> config > 内置默认（claude `opus[1m]` + xhigh；codex xhigh、不传模型）；claude 传 `--model`，codex 传 `-m`。起好记一份 worker 记录 `<slug>/<agent>.agent.json`（启动参数、pane、会话 ID），`release` 和续上按它；`--resume` 按记录里的会话 ID 续。起好就把续命令报给 herdr（返回里的 `herdr_resume`，见 [herdr 重启后按原参数拉回](#herdr-重启后按原参数拉回)） | 编排者 |
| `release` | 释放空闲的 worker：`--name <agent>` 退出它的 agent 进程（连同 MCP 子进程）腾出内存，pane、workspace、worktree、分支都不动；之后 `prompt` / `brief --send` 发给它会先在原 pane 按会话 ID 续上再发，`watch` 显示 `released`。只放 `agent-start` 起的、空闲（done / idle，没有确认框、接口错误、后台命令）、有会话 ID、发过话的；不放时 `ok: false`、`error` 写原因。`--idle` 是闲置扫描（空闲满 `worker_idle_release_minutes` 分钟的都放，后台维护 `upkeep` 每分钟跑它），`--idle --dry-run` 看每个候选空闲了多久、满了的为什么没放。详见下面的 [release](#release) | 编排者；`--idle` 由后台维护跑；你排查"为什么没放"时手动跑 |
| `upkeep` | 后台维护 drover 起的 agent：一次 `herdr agent list`，依次做三件事。① 批信任框：drover 起的 claude（worker 记录 / `orch.json` 对得上名字、pane、宿主）herdr 报 `blocked`、屏幕是 Claude Code 的 "Adding a new directory" 信任框、框里问的目录是 drover 起它时 `--add-dir` 加的（worker：`~/.drover/<slug>` 和记录里的 `add_dirs`；编排者：`~/.drover`），就把光标移到 "Yes, add it" 再回车（同 `agent-start`；绝不按 Esc，这个框里 Esc 就是退出），连着弹的逐个批，对不上的一律不碰；拿这个 agent 的锁（和发话、释放、`agent-start` 互斥），批了往日志写一行。② 同步续命令：编排者、子编排者、worker 的会话 ID 和上次报给 herdr 的不一样就重报（见 [herdr 重启后按原参数拉回](#herdr-重启后按原参数拉回)）。③ 闲置释放开着时跑一遍 `release --idle` 的扫描。输出 `trust_dialogs`（`approved` / `busy`）、`resume_sync`（`reported` / `failed` / `busy`）、`idle_release`。`watch`、看板、`main-register` 每满 60 秒在后台起一次（输出追加进 `~/.drover/upkeep.log`），一般不用手敲 | 后台；你排查续命令、信任框时手动跑 |
| `brief` | 按模板生成任务书（必填完成证据），可先摸底，`--send` 生成后立刻发出（和 `prompt` 一样看到开工就返回）。`--evidence-cmd` 紧跟在它所属的 `--evidence` 后面，给这条证据配验收时重跑的命令，证据清单写进 `<slug>/<agent>.evidence.json`；状态文件拿锁追加（几个同时跑不丢段）；同一个 agent 再生成时上一轮的任务书、报告、摸底挪成 `.rN`。模板里限了篇幅：摸底只写结论和证据位置，"和代码不符""需要决策"两部分写全、其余 100 行左右；报告每条 verification 一行（命令 → 汇总行 → heavy 流水号）、尽量 60 行内，worker 在对话里只回复报告路径和 status | 编排者 |
| `accept` | 验收：标记新文件、建图、改动分析、diff 概览、crapper 查这次改动碰到的函数的圈复杂度、重跑验证命令（认得出 vitest / pytest / mvn 的输出时，退出码 0 却一个测试都没跑也判不通过；每条给 通过 / 失败 / 跳过 三个数）；`--verify-baseline` 和基线比失败条目（基线上只重跑失败的文件；解析不全、提前停了、基线不可信都按失败），`--integration` 是集成检查（不建图、不跑 crapper，dryer 扫全仓、只留碰到改动的重复；跑在试集成 worktree 上时结论记进试集成状态，检查的是工作区整体状态写成的树，没叠完、有没解决的冲突时不检查），`--no-graph` 只是不建图。建图、dryer 和验证命令都经 heavy 调度；多条验证默认同时交给调度（能同时跑的同时跑，结果按传入顺序），前后有依赖的加 `--serial` 逐条跑。`--slug --name <agent>`：带命令的证据自动当 verify 跑，`evidence` 给每条证据的结论和"证据 N 条 / 有命令 M 条 / 通过 K 条 / 只能看自述 X 条"，默认存 `<slug>/accept-<agent>.json`。结果里另有 `worker_runs`（这个 worktree 的 heavy 流水：在现在的代码上通过过的、先失败后通过的）和改动指纹（review 核对用） | 编排者 |
| `integrate` | 集成 worktree：每个（仓库，目标分支）一个（第二条目标分支自动另建 `task/<slug>-<目标>`，指到别的目标的 worktree 拒绝复用），从最新上游建，复用时没有没推的提交就先快进到最新上游；只摘条目分支起点（`wt` 记在 `starts.jsonl`）之后的提交，起点带着目标分支没有的提交时停下列出来（`--carry-base` 带上），没有起点记录的旧条目照旧摘、给提示；"已应用"按 HEAD 核实（abort 过的冲突重摘、单列 `abandoned`，补丁比较保留空白），装依赖。冲突时试集成里有对得上的同一步（这个提交的父树、树和摘之前的树都一样）就照它解过的还原、接着摘（`replayed`）；返回的 `trial` 写明集成完的树和试集成验过的树对不对得上。新建时和 `wt` 一样核实起点含上一轮推的提交（`--from` 的条目分支里已有的不算缺），`--carry` 同 `wt`；目标分支远端还没有时要 `--create-from <远端>/<源分支>`；重建集成 worktree 不丢推送记录 | 编排者 |
| `trial` | 请用户批推送之前的试集成（不提交、永不推）：fetch 后从最新上游建试集成 worktree（每个（仓库，目标分支）一个，分支 `task/<slug>--trial-<目标>`，HEAD 永远停在上游），按 `--from` 的顺序把每个条目起点之后的提交、worktree 里没提交的改动（做成不挂引用的临时提交对象）逐步 `cherry-pick -n` 叠上；冲突停下，解完 `git add` 重跑接着叠；可重复执行，条目变了从它那步重叠；装依赖 | 编排者 |
| `push` | 集成 worktree 推送：上游有新提交就 rebase 并报告（这次不推）；否则核对整体检查（`accept --integration` 记进集成状态的结论：通过，检查的就是要推的内容，或者只差文档；集成 worktree 上没有对这份内容的检查时，认同任务、同仓库、同目标的试集成检查，`check_from: trial`），对上了才推送并按完整引用名 `ls-remote` 核对，没检查 / 没过 / 检查之后又改了都不推，`--unchecked "<理由>"` 才推；集成状态记的目标不是这次的就不推；每次推上去都记进任务的 `pushes.jsonl`（记下 remote 名和推送时的地址 `remote_url`、覆盖它的检查或 `--unchecked` 的理由，返回里也有），定不了任务就不推、提示加 `--slug` | 编排者 |
| `wrapup` | 收尾汇总的事实收集，不改仓库和任务记录：每个仓库每条分支的推送记录（`pushes.jsonl` 和旧任务的 `integrate-*.json`，旧版 patch 模式（没有集成状态）的也在）和远端核对（最后一次推到的是 remote 换地址之前的仓库时不核对，写明两个地址）、推送范围里的迁移 / 配置 / 部署 / 依赖文件和新增配置键线索、整体检查结果（每份覆盖了哪次推送，没被检查覆盖的推送单列）、条目分支上没推上去的提交、待清理的 worktree 和分支（子编排者的一起收）；`--draft <文件>` 生成 wrapup.md 骨架（只写这一个文件，写成了在时间线记一条收尾汇总） | 编排者 |
| `cleanup` | 用户说"清理"或"收尾"时清任务的 worktree 和分支：`--slug` 全部（含没有 worktree 的同类本地分支），`--wt` 只清指定的。逐个核实没有进行中的 git 操作、里面的 agent 不在 working / blocked、已提交的和没提交的改动都在这个任务推过的远端分支（的历史）里（先 fetch；推上去之后又被后来的轮次改过也算，内容在远端历史里就丢不了；集成时手工解过冲突、推上去的树里上下文变了的，按文件三方合并判"已包含"，冲突块先按行核、两边都改过的行再按词核；比 worktree 现有内容还旧的推送不拿来核对，三方合并的起点是没核实过的内容开始之前，所以撤回以前推过的改动、推上去之后又删掉的行、我们删掉 / 换掉而远端还留着的行都不算在远端；可执行位变化算改动；有内嵌 git 仓库（子目录自己有 `.git`）核实不了、不删，`--probe` 也不存不删），全过才 `herdr worktree remove`（有没提交改动的加 `--force`；workspace 没开用 `git worktree remove`）再删本地分支，核实不过的不动、列原因。`--target <远端>/<分支>` 给推送没落盘的老任务补核对分支；`--probe`（配 `--wt`）探测 worktree 先把本地独有的提交和改动存成 patch 到任务目录再删；`--dry-run` 只列结论。主检出永远不动 | 编排者 |
| `review` | 复审一条命令：按模板生成 prompt、分屏启动只读 codex、立刻发出。`--model` 指定 codex 的模型（`-m`），不传时用任务 `orch.json` 记的（编排者是 codex 才有，effort 也一样继承），再不传用 `~/.codex/config.toml` 的。验收存档按复审者名找（`<agent>-rev` → `<slug>/accept-<agent>.json`，没有再用旧的 `accept.json`；`--accept-json` 可指定），worktree、HEAD、改动指纹和现在的对不上就拒绝 | 编排者（带 `--review` 时） |
| `collect` | 复审结束后存档输出原文、关闭复审 pane | 编排者 |
| `review-start` | （底层）只分屏启动只读 codex，不发 prompt；一般用 `review` | 很少用 |

你可能会用到的几条：

```bash
~/.drover/bin/drover board                                       # 看板：一行一个任务，在等谁、要你做什么、这个状态待了多久
~/.drover/bin/drover board --tab                                 # 在主对话所在的 workspace 里开"看板"tab（已经有了就复用，里面是旧代码的看板就重启）
~/.drover/bin/drover board --pending --except order-filter       # 除了这个任务，其他还在等你的（主会话转达时附在末尾）
~/.drover/bin/drover rollup --slug order-filter                  # 主会话读 rollup（记为已读），开头说和上次读的相比改了什么
~/.drover/bin/drover watch --main --until-event                  # 主会话的追踪：有要处理的事才输出，输出完就退出
~/.drover/bin/drover orch-status
~/.drover/bin/drover watch orch-order-filter                     # 一直盯着，Ctrl-C 退出
~/.drover/bin/drover watch --once orch-order-filter              # 只看一眼
~/.drover/bin/drover history --repo order-service --grep 分页
~/.drover/bin/drover playbook --repo order-service --text        # 这个仓库现在生效的团队规则和 playbook
~/.drover/bin/drover accept-summary ~/.drover/<slug>/accept-<agent>.json --tail 2000
~/.drover/bin/drover heavy -- npm test
~/.drover/bin/drover heavy --status                              # 全机的构建 / 测试调度：容量、在跑的各占多少、排队的在等什么
~/.drover/bin/drover oncall status                               # 飞书 oncall 的状态
~/.drover/bin/drover main-register                               # 在主会话里：手动登记 / 看为什么没登记上
~/.drover/bin/drover update                                      # 更新 drover（见 docs/install.md）
~/.drover/bin/drover rules                                       # 主会话：读 drover 规则的改动（自动更新之后，不用 /clear）
```

另外，`/drover`（Claude Code）和 `$drover`（Codex）是编排者用的流程入口，日常不用手动敲。

## 编排者用的子命令：内部做了什么

编排者流程（`flow/dispatch.md` 主干和拆出去的几份）只写"看哪个字段、接着做什么"；子命令内部的步骤、返回的各个字段在这里。上面速查表写过的不重复。

### wt

已有同名分支的 worktree 就复用（复用时不做下面的同步）；分支已存在但没有 worktree 就自动改名为 `-2`、`-3`；否则新建，新建前先同步源仓库主检出（同一仓库的 fetch + 快进加文件锁，几个编排者同时建不会撞 git 的锁）：

1. **fetch**：主检出当前分支的上游（`@{u}`，如 `origin/master`；没配上游用 `origin/<当前分支>`，远端没有这条分支就当作没有上游）；`--base` 给的是 `<远端>/<分支>` 时也 fetch 它，同一条只 fetch 一次。配了上游、但远端已经删了这条分支（主检出停在合并后被删的旧 feature / bugfix 分支上，很常见）也当作没有上游，不算错：有显式 `--base` 照常建，没有就从主检出 HEAD 起并提示。其他 fetch 失败（网络、认证，偶发错误自动重试 3 次，仓库不存在这类永久错误不重试）→ `ok: false`，不建 worktree；detached、上游是本地分支、仓库没有远端都不算错。
2. **主检出快进**（只快进，不硬覆盖）：在分支上、没有已跟踪文件的改动、没有进行中的 merge / rebase / cherry-pick、落后上游时 `git merge --ff-only --no-overwrite-ignore`；已是最新、本地领先、有改动、分叉、detached、没有上游时不动它；快进本身失败（比如未跟踪文件、被忽略的本地文件会被上游新跟踪的同名文件覆盖）不阻断建 worktree。
3. **起点**：没传 `--base` 时，上游是主检出 HEAD 的祖先（快进了、本来就是最新、本地领先）从 HEAD 起；否则（有改动没快进、分叉、快进失败）从上游（如 `origin/master`）起，保证是远端最新，不含主检出的本地提交和改动；没有上游从主检出当前 HEAD 起。主检出不在远端默认分支上（本地记录的 `origin/HEAD`，只读本地、不联网）时提示一句。显式 `--base`：远端分支 fetch 后用，本地分支名 / sha 照用，主检出照样快进。这一条只管第一轮：任务在这个仓库推过之后，起点见 `flow/integrate.md` 的"新一轮的起点"。

新建的条目 worktree 把起点（`--carry` 带上的提交之前的 HEAD）记进 `<slug>/starts.jsonl`：集成时只摘起点之后的提交。

复制本地运行要的文件（只复制，不覆盖）：`.claude/settings.local.json` 和 `.env*` 里源仓库没跟踪的照复制；源仓库跟踪着的不复制（基线分支上有它就随 checkout 带过来了，没有就说明那条分支本来没有，复制过去会成为未跟踪文件，挡住要新增它的 cherry-pick：比如主检出在 `main`、跟踪着 `.env.example`，集成 worktree 基于没有它的 `origin/release/1.2`），除非它在新 worktree 里被 `.gitignore` 忽略（基线分支已把 `.env` 移出 git 并忽略、主检出所在的分支还跟踪着，worker 仍要这份本地配置）。仓库规范文件 `CLAUDE.md` / `CLAUDE.local.md` / `AGENTS.md` 没提交时，被 `.gitignore` 忽略的也复制过去（很多仓库忽略了 `CLAUDE.md`，不复制的话 worktree 里没有，agent 读不到仓库规范）；既没提交也没被忽略的不复制（复制过去会出现在 `git status` 里，可能被误提交），列在 `docs_not_copied`，由 `brief` 让 agent 去读源仓库的原文。仓库还可以在根目录放一份 `.worktreeinclude` 声明额外要带的本地文件（Claude Code 的约定，`claude --worktree` 也认）：gitignore 语法，匹配某条模式、在源仓库被忽略的未跟踪文件才复制，跟踪的、没被忽略的、符号链接都不带；同样只复制在新 worktree 里被忽略的，不被忽略的列在 `worktreeinclude_skipped`。典型用途是 Java 仓库被忽略的本地配置和本机测试页（比如 `order-service` 的 `src/main/resources/config/*.yml`、`tools/` 不进 worktree，worker 起不了服务）；这份文件本身可以写进 `.git/info/exclude` 只在本机忽略，drover 从源仓库读它。复用旧 worktree 时也会补复制缺的文件。

返回的 `source_sync`：主检出同步的结果。`status` 是 `fast_forwarded`（快进了）、`up_to_date`、`ahead`（本地有没推送的提交）、`dirty`（有已跟踪文件的改动，没快进）、`in_progress`（有进行中的 merge / rebase / cherry-pick）、`diverged`（和上游分叉）、`ff_failed`（`ff_stderr` 是 git 的输出）、`detached`、`no_upstream`、`upstream_gone`（配了上游，但远端已经删了这条分支）、`not_synced`（`--no-sync`）、`reused` 之一，`reason` 是中文说明，`before` / `after` 是主检出同步前后的 HEAD，`fetch` 列出每次 fetch 的结果和尝试次数，`remote_default` 是本地记录的远端默认分支（如 `origin/main`，解析不出是 null）。`base_ref` 是实际用的起点（`HEAD`、`origin/<分支>` 或显式 `--base`）。

新一轮的起点（`round_check`）：remote 名只是本地别名，换了地址以后 `origin` 指的是另一个仓库，拿推到旧地址的记录去核实会误报漏推。所以推送记录记着推送时的地址 `remote_url`，和现在同名 remote 的推送地址（`git remote get-url --push`）比主机 + 路径（去掉协议、用户名、端口、末尾的 `/` 和 `.git`，主机不分大小写：`git@github.com:o/r.git`、`ssh://git@github.com/o/r`、`https://github.com/o/r` 算同一个；`file://` 和本地路径比路径），不同，或者这个 remote 已经没了，就算推到了别处。推到别处的都列在 `round_check.other_remote`（时间、`upstream`、`remote_url`、现在的地址 `remote_url_now`）。地址只比主机 + 路径，不查 DNS、不认托管平台的大小写或改名重定向。`round_check.status` 是 `no_records`、`ok`（`how`：`ancestor` / `patch`）、`missing`（带了 `--carry`，`carried` 列出带上的提交）、`skipped`（复用）、`other_remote`（全推到了别处）之一。

### deps

- **JS**：按根目录的 lockfile 严格安装（bun / pnpm / yarn / npm，不改写 lockfile）。
- **Python**：有 `uv.lock` 用 `uv sync --frozen`；否则复刻源仓库 `.venv`（同版本解释器 + 同一份包，包括 requirements 里没写的 pytest 等），再按 worktree 的 `requirements.txt` 补装；源 venv 里指向本仓库的 `-e` 可编辑安装改为指向 worktree（改指失败时这一项报失败，摘要写明是哪个；复刻没做完的 `.venv` 再跑 deps 时删掉重新复刻，不当成已有的 `.venv` 去补装）。
- **Java**：依赖在全局 `~/.m2` / `~/.gradle`，不用处理。
- **子目录里的 lockfile**（如 `web-app` 的 `mobile/`）：不自动装，在摘要里告诉 agent。
- **worktree 里已有依赖时**：依赖清单（根目录 lockfile、各 `package.json`、`pnpm-workspace.yaml` / `.npmrc` 等安装配置，`uv.lock` / `pyproject.toml`，`requirements.txt`，以及安装命令）自上次 drover 安装以来没变才跳过（装成功后在 `node_modules` / `.venv` 里记一份 `.drover-deps.json`）；变了、或者不是 drover 装的，照常严格安装（Python 在已有 `.venv` 上 `uv sync --frozen` 或按 `requirements.txt` 补装，不重建）。`node_modules` / `.venv` 是软链的不经它装，摘要里写明指向哪、要独立安装先删掉这个链接。deps 是无人值守的：包管理器不继承调用方的 stdin（读到的是 EOF），pnpm 要清空 `node_modules` 重装时（升了版本、改了 hoist / node-linker 这类布局配置）直接清空重装，不停下来问。

### agent-start

拼好的参数：

- claude：`--model`（取值的先后见上面速查表 agent-start 那一行）、`--effort`（默认 xhigh）、`--permission-mode acceptEdits`、`--name worker-<slug>`，并把 `~/.drover/<slug>` 和每个 `--add-dir` 加为可访问目录。
- codex：`-a on-request -s workspace-write --no-daemon --no-alt-screen -c check_for_update_on_startup=false`，同样加目录，指定了模型时加 `-m <模型>`，再加 `-c model_reasoning_effort=<level>`（默认 xhigh；不传会继承 `~/.codex/config.toml` 的 ultra，实测 worker 改两个文件花了 19 分钟）。`--no-daemon` 必须带：codex 0.157 起交互模式默认连共享后台 server，实测不带会直接报错退出。`--no-alt-screen` 让输出留在滚动缓冲区，`herdr agent read` 才读得到。`check_for_update_on_startup=false` 关掉启动时的版本检查：有新版本时 codex 新起、续上都先弹 "Update available" 升级框停在那里（herdr 重启后拉回的没人按就一直卡着），实测 0.170.0 带上它新起、续上都不弹。编排者、复审、续上用的是同一组公共参数。

启动时的已知弹窗：

- **新目录信任弹窗**：Claude Code 启动时问工作目录的（"Yes / trust"），和问 `--add-dir` 目录的（"Adding a new directory: … This session hasn't worked here before"，选项"Yes, add it"），都是把光标移到 Yes 那项再回车。不能直接按回车：两种框的默认选项都是"No, exit"，回车会让 agent 直接退出。
- codex 的升级提示不在里面：带了上面的开关不会弹；万一弹了（以后的 codex 不认这个键）当成认不出的界面交给编排者，不自动按键（升级是全局安装，由用户决定）。

claude 起到 idle 以后还要等它的会话开始（herdr 报出会话 ID）才算起好，最多等 15 秒：处理完启动弹框 herdr 马上就报 idle（屏幕上有输入框了），实测 0.4～2 秒后会话才真的开始，这中间发的话会留在输入框里、回车被吞掉（herdr 报 `agent_prompt_stalled`）。没装 herdr 的 claude 状态 hook 的机器上等不到，等满照常返回。codex 第一轮对话之后才报会话，不等。

起来以后（包括卡在不认识的启动弹窗上、`ok: false` 但 agent 还活着的）写一份 worker 记录 `~/.drover/<slug>/<agent>.agent.json`，返回里的 `record` 是它的路径：

| 字段 | 含义 |
|---|---|
| `name`、`slug`、`kind` | agent 名、任务、宿主（claude / codex） |
| `model`、`effort` | 补完默认值之后实际用的（codex 没指定模型时没有 `model`）；续上时原样用，之后改 config 不影响 |
| `add_dirs` | `--add-dir` 给的目录（`~/.drover/<slug>`、codex 的 heavy 调度目录续上时照旧自动加） |
| `pane_id`、`cwd` | 起在哪个 pane、herdr 报的工作目录（`--split-from` 时是 `--wt`） |
| `started_at` | 启动时间（epoch 秒） |
| `session_id` | 宿主的会话 ID，取自 herdr 的 `agent_session`（来源见 [release](#release)）。claude 起好时就有；codex 发出第一句话之后才有（实测第一轮一开始 herdr 就有了），释放时再取 |
| `state` | `running` / `released` |
| `resume_reported_session` | 报给 herdr 的续命令用的会话 ID，herdr 收下了才有（见 [herdr 重启后按原参数拉回](#herdr-重启后按原参数拉回)）：起好报上了写；后台维护发现 herdr 现在的会话和它不一样就重报、改它；释放时删掉（进程退了 herdr 就清掉续命令），续上报了再写 |
| `prompted_at` | 最近一次经 `prompt` / `brief --send` 给它发话的时间。没有它的（还没发过话）不释放 |
| `released_at`、`release_reason`、`freed_kb` | 释放时加：时间、`manual`（`release --name`）或 `idle`（闲置扫描）、放掉的进程组的 RSS 合计（KB，`ps` 用不了时没有） |
| `resumed_at` | 续上时加 |

`--resume`（agent 意外退出后在同一个 pane 续上次会话）：这个任务里有它的记录、记着会话 ID 时按 ID 续（claude `--resume <id>`，codex `resume <id>`），没给 `--kind` 时宿主也取记录的；没有记录或没记下会话 ID 时照旧 claude `--continue`、codex `resume --last`（按工作目录取最近一次会话，同一个 worktree 里有过两个会话时会续到另一个上）。

别的任务释放了的 worker 的名字，在它的 pane 还在时不能拿来起新 agent（旧编排者再给这个名字发话，会发给新的那个）：报错让换名字。

### release

worker 做完以后进程不用留着：上下文在宿主存在磁盘上的会话里，进程退了按会话 ID 照样续得上（claude、codex 都实测过：释放后续上，答得出释放前告诉它的口令）。留着进程只占内存（`ps` 的 RSS，实测）：一个 claude worker 本体约 270～410MB，外加它起的 MCP 子进程（看项目配置，context7 的两个 node 合计约 200～280MB），端到端测试里每次释放放掉 510～590MB；codex 本体约 150～190MB，配了 MCP 时同样带着子进程，合计 470～500MB；释放后 pane 里只剩 shell，约 2MB。

**释放**（`drover release --name <agent>`）依次核对，都过了才动手：

1. 有 worker 记录（只放 `agent-start` 起的：编排者、子编排者、复审、主会话、自己开的 agent、这个版本之前起的 worker 都没有记录，不放）。
2. herdr 里这个名字还在记录的那个 pane、宿主相同。
3. 有会话 ID：herdr 这时报的优先（续上以后宿主可能换了会话），没有再用记录里的；两边都没有不放。没有会话 ID 只能按工作目录取最近一次会话续，同一个 worktree 里有过两个会话时会续错。
4. 空闲：herdr 报 done / idle，而且按屏幕修正后还是（确认框 → `blocked`、最后一个输出块是接口错误 → `api-error`、状态栏挂着后台命令或 Monitor → `waiting-background(N)`，同 watch）。挂着后台命令的不放：它会自己接着干；而且 Claude Code 的 Bash 工具命令在它们自己的进程组里（实测），发给前台进程组的信号到不了它们。
5. 经 drover 给它发过话（`prompt` / `brief --send`，记录里的 `prompted_at`）：没对过话的会话按 ID 续不上（claude 实测报 "No conversation found with session ID" 后退出），放了就起不回来。
6. pane 的前台进程组认得出是这个宿主：组号不是 shell 自己的，组里至少有一个进程叫 claude / codex（看 `herdr pane process-info` 给的 `argv0`、`name` 和 `argv` 里的脚本路径：原生安装的组长 `argv0` 就是 `claude`；npm 装的是 `node …/bin/claude`；codex 的 npm 包再起的原生二进制 `argv0` 是 `…/codex`）。认不出时不发信号，`error` 里列出看到的每个进程（pid 和命令行）。

然后把记录改成 `released`，给前台进程组发 SIGTERM，最多等 10 秒，还在就 SIGKILL。MCP 子进程跟着退（实测）：claude 的和它在同一个进程组里；codex 的各在自己的进程组里，codex 一退它们 3 秒内也都退了。记录先改、信号后发：进程一退 herdr 就清掉名字，watch 这时读到的记录已经是 `released`，不会报 `gone`。没退掉的把记录改回去。返回里有 `signal`、`session_id`、`freed_mb`；pane 是 herdr 报的 focused 时照放、`warnings` 里提一句。已经释放了的再放一次是 `ok: true`、`already_released: true`。

不用 `/exit` 退：它要往输入框里打字，输入框里有半截字时会拼成一条 prompt 发出去。

**续上**：`drover prompt`、`brief --send` 发给 herdr 里已经没有、最新记录是 `released` 的 worker 时，先在原 pane 用记录里的参数加会话 ID 把它起回来（claude 加 `--resume <id>`，codex 是 `resume <id>` 加原来的选项；启动弹窗照 `agent-start` 处理，claude 等到会话开始），起来以后把续命令重新报给 herdr（释放时 herdr 已经清掉了；返回里的 `herdr_resume`，codex 要等 herdr 有了会话 ID 由后台维护补报），再把话发出去，返回里多 `resumed: true` 和 `resume_command`。刚续上发的话偶尔会留在输入框里没提交（herdr 报 `agent_prompt_stalled`，机器忙时实测有过：话打进去了，回车被当成换行）：这时屏幕上没有确认框就补一次回车，提交了的返回里带 `submitted_with: extra_enter`，还是没提交的照 herdr 的结果报回去。发话和释放对同一个 agent 拿同一把锁（`locks/agent-<名>.lock`）：正在释放时进来的话等它放完再续上；正在发话时闲置扫描跳过它。续不上时不发、`ok: false`、`outcome: resume_failed`，`error` 写下一步：

- 原 pane 不在了（herdr 明确报 `pane_not_found`）：在它的 worktree 里新开一个 pane，`agent-start --resume`。
- 查 pane 时 herdr 调用失败（超时、一时连不上）：不当成 pane 不在了，稍后重发。
- 原 pane 里在跑别的程序：等它结束，或换一个 pane `agent-start --resume`。
- 起来又退出了（`start.pane_output` 里有宿主的报错）：宿主说这个会话续不了的，去掉 `--resume` 起一个新会话，之前的上下文没有了，任务书要重发。

按旧流程跑着的编排者不知道有释放：它照旧 `prompt` / `brief --send` 就会续上；它直接 `herdr agent read <名>` 会报 agent_not_found，这时看屏幕用 `herdr pane read <pane_id>`。

**闲置自动释放**：`agent-start` 起的 worker 连续空闲满 `worker_idle_release_minutes` 分钟（默认 10，见[配置](configuration.md)）、pane 不是 herdr 报的 focused（你可能正在看它；被 focus 了计时清零，离开后从头算），由后台维护（`drover upkeep`）里的闲置扫描放掉，核对同上。中间干过活（herdr 的状态序号变了）从头算。

- 谁触发：普通 `watch`、`watch --main` 的每一轮，`board` 文本 / `--json` / `--live`，`main-register`。它们只读一个小文件（`locks/upkeep-tick.json`），离上次满 60 秒才在后台起一次 `drover upkeep`、不等它；闲置扫描一次只跑一个。这些都没在跑时没有扫描。`worker_idle_release_minutes` 是 0、`DROVER_IDLE_RELEASE=0` 只关掉闲置扫描，后台维护照样每分钟起（续命令同步要跑）。
- 扫描一次只调一次 `herdr agent list`（和续命令同步共用），各自从什么时候开始空闲记在 `~/.drover/idle-release.json`；后台维护的输出追加进 `~/.drover/upkeep.log`（超过 512KB 清空重写）。
- 默认 10 分钟的依据：Anthropic 的 prompt 缓存默认 5 分钟有效（[Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)："By default, the cache has a 5-minute lifetime"），闲置超过以后留着进程不省 token，只省几秒启动。

**为什么没被放**：`drover release --name <agent> --dry-run` 走一遍核对、不动手，`error` 就是原因；`drover release --idle --dry-run` 列出每个候选已经空闲多久（`idle_minutes`）、满了的会放谁（`released`）、不放的和原因（`refused`）。常见的：

- 没有会话 ID。会话 ID 是 herdr 的状态 hook 报的：`herdr integration install claude`（codex 同理，install.py 会装）在宿主的 SessionStart hook 里调 herdr 的 `pane.report_agent_session`，之后 `herdr agent get <名>` 的 `agent_session`（`source` 是 `herdr:claude` 这类）才有值。没装或不是 current（`herdr integration status`）的机器上 herdr 拿不到会话 ID，闲置释放不会生效，手动 `release` 会拒绝并写明这一条；装好后新起的 agent 才有。
- 认不出进程组（少见的启动方式）：`error` 里有看到的进程。
- 还没经 drover 给它发过话：起了以后一直没派活的 worker 不放（放了续不上）。
- 没有 worker 记录：这个版本之前起的 worker 不会被放，照旧留到清理 / 收尾。
- drover 自己跑在沙箱里时没有权限给别的进程发信号（`error` 里写明）。

**释放的代价**：

- 命令级的"不再询问"存在文件里，不丢：claude 写在 worktree 的 `.claude/settings.local.json`，codex 写在全局规则文件 `~/.codex/rules/default.rules`。
- 会话里临时切过的权限模式（shift+tab、"switch to auto mode"）不留，续上后回到启动时的（claude worker 是 `acceptEdits`）。
- 续上要几秒启动，第一轮要重读上下文（缓存过期以后本来也要重读）。

### herdr 重启后按原参数拉回

herdr 重启后会把 pane 里的 claude / codex 续上，但它自己拼的续命令只有会话 ID（`claude --resume <id>`、`codex resume <id>`，herdr 源码 `src/agent_resume.rs:204-213`），`herdr agent start -- <参数>` 的参数只敲进 shell、不存：拉回来的模型、effort 回到本机默认，`--add-dir`、codex 的沙箱都没了。herdr 0.9.2 起启动方可以报一条完整续命令，存进快照，恢复时优先用它。drover 起的编排者、子编排者、worker 由 drover 报：

- 报什么：起它用的参数加上续当前会话，和 drover 自己续 worker 的形状一样（claude `claude <参数> --resume <id>`，codex `codex resume <id> <参数>`）；`herdr pane report-agent-session <pane> --source herdr:<kind> --agent <kind> --agent-session-id <id> --seq <纳秒时间戳> -- <续命令>`。会话 ID 一律取 herdr 当时报的：herdr 只把续命令绑在它当前的会话上，报别的会话会被静默丢掉。
- 什么时候报：`agent-start`（新起、`--resume`、`--split-from`）、`orch-start`、释放后发话续上，起好就报，返回里 `herdr_resume` 写报没报、报的什么（`argv`、`session_id`）或为什么没报（`reason`）。claude 起好时就有会话 ID；codex 发出第一句话之后 herdr 才有（实测第一轮一开始就有），起好时不报，由后台维护补报。报上了的会话 ID 记在 worker 记录 / `orch.json` 的 `resume_reported_session`。
- 会话变了重报：herdr 不会跟着改报过的续命令（claude `/clear` 换了会话，不重报的话重启会续回 `/clear` 之前的旧会话）。后台维护（`drover upkeep`，`watch`、看板、`main-register` 每分钟在后台起）对 herdr 里每个还活着、名字和 pane、宿主都对得上 worker 记录或 `orch.json` 的 agent 核对：herdr 现在的会话 ID 和 `resume_reported_session` 不一样（或没报过）就报、改字段。所以 codex 在 herdr 拿到会话 ID 之后最多约 1 分钟报上（前提是有这些命令在跑：编排者追 worker 的 `watch` 在 worker 干活时一直在跑）。这版之前起的、还在跑的编排者和 worker 第一次同步就会补报。闲置释放关着时照样同步。
- 释放：进程一退 herdr 就清掉续命令，`resume_reported_session` 跟着删掉；续上以后重报。
- 参数从哪来：worker 按记录（`model` / `effort` 是补完默认值的）原样重放；编排者按 `orch.json` 的 `kind` / `model` / `effort` 重建，和 `orch-start` 同一份。`orch.json` 记的是补默认值之前的（没指定是 null，它也是同宿主 worker 继承的任务默认），默认值按报的时候的 config 补：**编排者起来以后改了 config 的 `claude_model` / `claude_effort` / `codex_effort`，herdr 重启后它按新默认回来**。
- 报失败（herdr 报错、续命令不合格 `invalid_resume_argv`、`resume_not_accepted`、超时）不影响启动、发话、`watch`、看板：写进 `herdr_resume.reason` 或 `upkeep` 的 `failed`，下一轮再试。
- 复审不报：一次性的只读 codex，没有记录可以按它重建参数；herdr 重启打断了它唯一的一轮，拉回来的是裸续（沙箱回到 config 默认）、停在 idle，重起复审就是。主会话（你自己起的）drover 不知道参数，不管。

管不到的：

- 你在 pane 里自己退出 agent、又手动续同一个会话：herdr 清掉了续命令，会话 ID 却没变，drover 看不出来（herdr 的 API 读不到它存着的续命令）。重启前用 drover 续（`agent-start --resume`）或等它下一次换会话。
- claude 刚起好还没对过话、或 `/clear` 之后还没对过话时 herdr 重启：`--resume` 一个空会话会报 "No conversation found" 退出，和 herdr 自己的裸续一样。`watch` 报 `gone`，按 `gone` 处理。
- codex 起好（新起、释放后续上、`agent-start --resume`）到发出第一句话之间：herdr 那时没有会话 ID，重启后那个 pane 只回到 shell。drover 起好以后马上就发话（`brief --send`、续上时的那句话），这段一般只有几秒。
- herdr 拉回的 claude 会先弹一次 Claude Code 的 "Adding a new directory" 信任框（问 `--add-dir` 的 `~/.drover/<slug>`；编排者是 `~/.drover`），光标默认在 "No, exit"（实测：这个框的信任只管一个进程，续同一个会话也要重新批），herdr 报 `blocked`。drover 自己起、续的时候 `agent-start` 当场批；herdr 拉回的由后台维护（`upkeep`）批，只认 drover 起的 claude、只认这个框、只认 drover 给它加的目录。重启以后最早触发后台维护的是主会话的 SessionStart hook（见[排障](troubleshooting.md#herdr-重启后-agent-参数不对)），那一刻已经停在框上的当场批，还没停到框上的要等下一次 tick（满 60 秒、而且得有 `watch` / 看板 / `main-register` 在跑）。

### brief

- 宿主：`--kind` 不传时按 `--name` 这个 agent 在 herdr 里的实际宿主写（agent 还没起时用 orch.json 的 `default_kind`，再是调用方）；传了却和活着的 agent 对不上会进 `warnings`。
- 按模板拼出完整 prompt：开头是 `--goal` 给的用户目标（必填），然后是任务段、完成证据（编号列出，带命令的下面写"验收会重跑"；报告的 verification 要逐条对应，每条引用 heavy 打的流水号）、工作目录（分支不传就读 worktree 当前分支，并写出基线 commit；在 `~/.herdr/worktrees/` 下时加"这个位置是允许的，不要挪动"）、依赖（取 deps 存档的 `summary_for_prompt`，按 JS / Python 给出在 worktree 里装依赖的写法）、约束（不提交不推送、不 fetch / rebase、只写迁移文件和测试、不执行迁移、不写共享 / 生产数据库、不在用户的 herdr 里起测试会话 / 测试 server / 测试 agent（要真实 herdr server 的实测用 `~/.drover/pkg/selftest/live_herdr.py`）、只跑相关测试不跑全量、测试 / lint / 类型检查 / 构建一律经 `~/.drover/bin/drover heavy -- <命令>` 执行（按机器余量调度，轻的不排队、重的才排；写成字面的 `~` 路径，命中安装器加的白名单不弹框；heavy 只执行构建 / 测试工具，别的命令退出码 2，直接执行就行）、审批友好写法、逐条自证、需要决策时怎么办）、完成协议（报告的篇幅要求，对话里只回复报告路径和 status）和报告路径 `~/.drover/<slug>/<agent名>.md`。源仓库有、worktree 里没有的规范文件（`CLAUDE.md` / `AGENTS.md` 等没提交的），约束里写明源仓库原文的路径，让 agent 先读（源仓库在主工作台下时，读权限一般已由 `Read(<workspace>/**)` 这类规则放行）；其中被忽略、本该已复制的会进 `warnings`，重跑一次 `wt`（同分支复用）就会补上。
- 编码规范：先追加包默认的 `flow/coding-rules.md`（"drover 默认"一节；config 的 `default_coding_rules` 为 `false` 时整份不加），再是团队 profile（及 `playbook_dirs`）的 `coding-rules.md`，每层一节，后面的优先；claude、codex 都加（哪个宿主都不会自动加载它），标题写明来源；配置了 profile 但目录不存在时进 `warnings`。包默认那份按语言只带相关的：通用原则总带；Java 一节在任一 worktree 根目录有 `pom.xml` / `build.gradle` / `build.gradle.kts` 时带；MySQL 一节在任一 worktree 有被 git 跟踪的 `.sql` 文件、或依赖清单（`pom.xml`、`build.gradle*`、`package.json`、`requirements*.txt`、`pyproject.toml`）里出现 mysql / mariadb 字样时带（只有 ORM 不算）。会带哪几节，`drover playbook --repo` 输出的 `default_coding_rules.sections` 里有。
- `--kind codex`：约束改成 codex 的沙箱说明，并追加全局 CLAUDE.md 的"编码规范"一节原文（codex 不会自动加载 CLAUDE.md；没有这个文件或这一节就不加）。
- 写到 `<slug>/<agent名>.prompt.md`，证据清单（每条证据和它的命令）写到 `<slug>/<agent名>.evidence.json`；在状态文件末尾追加 `## brief：<agent名>（时间，kind，摸底：是/否）` 段，列出完成证据和命令。状态文件拿锁追加，几个 brief 同时跑也不丢段。重新生成会再追加一段，以最后一段为准；同一个 agent 上一轮的任务书、报告、摸底先挪成 `<agent名>.prompt.rN.md` / `<agent名>.rN.md` / `<agent名>.survey.rN.md`（新 brief 段写出挪到哪了）。

### watch

`released`：herdr 里没有这个名字、它最新的 worker 记录是 `released`、释放时的 pane 还在。只在启动时和 `--once` 显示；done / idle 变成 `released` 不输出、不算事件（`--until-event` 不因为它退出），续上以后的状态照常报。释放时的 pane 被关了（原地续不了）报 `gone`。pane 还在不在是每轮一次 `herdr pane list` 查的（盯着的名字里没有已释放的就不查），这次调用失败时不知道在不在：沿用上一轮显示的，启动那一轮按 `released`，不报 `gone`。普通 watch 每一轮也触发后台维护（续命令同步、闲置扫描，见 [release](#release)、[herdr 重启后按原参数拉回](#herdr-重启后按原参数拉回)）。

`blocked` 的重报：同一个 agent 可能连着报两次 `blocked`：herdr 的状态序号变了（中间那下 working 没轮询到）、弹框内容换了、或者一直有弹框而 herdr 的原始状态变了（比如 blocked → working），watch 都会重报。两个弹框一字不差（同一条命令连着两次）且中间那下没轮询到时，watch 认不出是新的。herdr 报 done、但屏幕底部是确认弹框时（比如 Claude Code 的 WebFetch 域名授权，herdr 有时认不出、报成 done），watch 也报 `blocked`；herdr 报 working、但屏幕底部是后台子 agent 的确认框时（主 agent 停在 "✻ Waiting for N background agent(s) to finish"，herdr 按这一行报 working；watch 在 working 时每 10 秒看一次屏幕），watch 也报 `blocked`。

### accept

依次执行：记录 `git status --porcelain` → `git add -N .` 把新文件纳入 diff → 在 worktree 里全量建图 → `detect-changes --brief` → `git diff <BASE_SHA> --stat` → crapper 算这次改动碰到的函数的圈复杂度 → 在 worktree 里重跑 `--verify`。带 `--integration` 时不建图（`graph.skipped: true`；图谱结论只在条目验收时用，大仓库建一次要几十秒）、不跑 crapper、改跑 dryer（也交给调度，大仓库单核约 85 秒）。存档还记着 worktree、HEAD 和改动指纹，`review` 从这里取图谱摘要和验证结果，并核对它就是要审的这份代码。`--integration` 跑在试集成 worktree 上时怎么记结论见下面的 trial。

负载超时复核：vitest 有失败、而且失败全是 "Test/Hook timed out" 时，把失败的文件单独重跑一次；单独跑通过（同样要真的跑到测试），这条 verify 就判通过，带 `flaky_timeouts`（文件列表）和 `note`，`exit_code` 保留第一次的结果。只要有一条不是超时的失败（汇总里的未处理错误 `Errors  N error` 也算），就不复核、照常判失败。

和基线比（`--verify-baseline`，集成检查用）：
- 基线上只重跑当前失败所在的文件：vitest / pytest 按失败用例所在的测试文件，eslint 按报错的文件（选项保留，原来的目录参数去掉；vitest 的 `--changed` 也去掉）；tsc 没法拆，和拆不出文件的命令（经 make 之类包装、失败路径对不上 worktree 里的文件）一样跑整条。在 `--base` 上临时建一个 detached worktree（`~/.drover/baselines/` 下，复制 .env、装依赖，跑完就删）。`baseline.targets` 是基线上跑了的文件（没有这个字段就是跑了整条）；当前失败的文件 BASE 上没有（新加的测试）的列在 `baseline.not_on_base`，不放进基线命令、失败直接算新增，一个都不剩就不建基线 worktree。
- 失败条目：vitest、pytest（`FAILED` / `ERROR` 行）、tsc（文件 + 错误码）、eslint（文件 + 规则，只算 error）。条目不带行号，改动挪了行也对得上。认不出条目格式的错误行也算条目，不会被"都是旧失败"盖掉：vitest 汇总里的未处理错误记 `(未处理错误)`，tsc 不带文件的全局错误记 `(全局) TSxxxx`，eslint 没有规则名的（语法错误）记 `文件 (无规则)`。
- 基线结果按"仓库 + BASE + 命令 + 重跑的文件"缓存在 `~/.drover/baselines/`，同一个基线、同一批文件只跑一次。（基线上每次跑整条命令的话，大仓库每换一个上游就要多跑一整套 vitest。）基线 worktree 装依赖失败、基线命令没跑成（超时、起不来）或基线输出同样解析不全时，算基线不可信：不写缓存、按失败处理。两个验收同时比同一个基线时，后到的等前一个跑完、直接用它的缓存。
- 只有"在基线上跑命令"那一步进 heavy 调度：建基线 worktree、复制本地文件、装依赖不占预留。

### heavy 的调度

- 容量从本机的性能核数起步，之后按真实命令自动校准（同时跑明显变慢就降、接近容量还几乎不变慢就加，config 可固定，见下面「目录布局」的 `locks/heavy/`）；需求是这条命令最近几次的实测（CPU 时间 ÷ 墙钟）。
- 放行看实测：整机实测（所有进程）没满、heavy 在跑的进程树实测占用加上这条不超过容量的 1.2 倍就跑；预留只算刚放行、还没在实测里显出来的那几秒。"独占"的（全量测试）收尾、I/O 阶段实测空出来就放别的进来。
- 没跑过的（同仓库同程序也没跑过）先按 1 核，跑起来按实测；历史上 10 秒内跑完的小命令不排队；排头等了 2 分钟还没开始，后来的只放能在它预计开跑前跑完的（在跑的超过了历史最长墙钟、或没有历史时，按超出 / 已跑多久再估多久，不会一直挡着轻命令）。
- 原因：实测几条命令合计不超过性能核数时同跑几乎不变慢；超过后每条慢 1.8～2.9 倍、全量 vitest 超时。"同一时间只跑一个"会让 0.5 秒的命令排在全量测试后面等十几分钟；只看预留的话，全量测试收尾时 CPU 已经空了、没跑过的轻命令按独占，照样挡人。底线是不能在 CPU 空闲的时候白白干等。集成检查的几条默认同时交给调度：轻的（tsc、lint、i18n）一起跑，全量测试这类占满机器的等实测空出来再跑、它收尾时别的也能进来（严格串行的话，三条各占半个核的集成检查也要串行跑 7.4 分钟）。
- `accept --no-lock` 不进调度（调度后轻命令本来就不排队，一般不用加）；codex worker 由 `agent-start` 放行调度目录。
- 流水：heavy 在 `task/<slug>…` 分支的 worktree 里每跑完一条就往 `<slug>/heavy-runs.jsonl` 记一行：worktree、命令、退出码、汇总行、跑之前和跑完的改动指纹（accept 的 `worker_runs` 读它）。

### integrate

从最新的 `origin/<上游分支>` 建集成 worktree：每个（仓库，目标分支）一个，第一条目标分支用 `task/<slug>`，同一轮推第二条分支只换 `--target`，自动另建 `task/<slug>-<目标>`（`branch_note` 写明；不复用第一条的，`--branch` 指到别的目标的也拒绝）；已有这个目标的就复用，复用时没有没推的提交（上一轮推完了）先快进到最新上游、`base` 跟着更新（`synced`）。按 `--from` 的顺序 cherry-pick 每个条目分支起点之后、集成分支里还没有的提交（补丁比较保留空白；状态文件 `<slug>/integrate-<仓库名>@<目标>.json` 里的记录按 HEAD 核实过才算已应用），最后装依赖。新建集成 worktree 时 `wt` 同样会快进源仓库主检出（目标分支已经 fetch 过，不重复；主检出停在上游已被删的旧分支上也不影响集成）。

cherry-pick 停在冲突上时，先看同任务、同目标的试集成状态（`<slug>/trial-<仓库名>@<目标>.json`）：有一步的父树、树和这个提交的父提交、提交的树相同，之前的树和摘之前 HEAD 的树相同，就把 index 和工作区还原成那一步解完的树（`git restore --source=<树> --staged --worktree :/`）再 `cherry-pick --continue`，状态记 `resolved` 和 `replayed_from_trial`，返回 `replayed`（提交、冲突文件）；对不上照旧停下。不用 rerere：rr-cache 在仓库公共目录，一旦建了，用户在这个仓库里自己的 merge / rebase / cherry-pick 也会默认套旧解法（[git-config 的 rerere.enabled](https://git-scm.com/docs/git-config)："By default, git-rerere is enabled if there is an rr-cache directory under the $GIT_DIR"）。有试集成时返回 `trial`：`tree`（试集成检查过的树，没检查过是叠完的树）、`checked`（检查通过没有）、`matches_head`、`differs`（和 HEAD 的树差的文件）；`next` 跟着变：树相同就是直接 push，只差几个文件就先补"集成修正"。

### trial

批推送之前的试集成（flow/integrate.md"试集成"）。fetch 目标分支（远端还没有的同 integrate 要 `--create-from`），用 `wt` 建或复用试集成 worktree：分支 `task/<slug>--trial-<目标>`（双横线：条目可能就叫 trial），起点是最新上游，HEAD 之后永远不动（分支上没有新提交）。步骤和正式 integrate 一一对应：每个 `--from` 条目分支起点（`starts.jsonl`）之后、上游没有的提交（补丁比较同 integrate），再加条目 worktree 里没提交的改动：用临时 index（`read-tree HEAD` → `add -A` → `write-tree`）把条目工作区的整体状态写成树，`commit-tree -p <条目 HEAD>` 做成临时提交对象（作者 / 提交者 `drover trial`，不挂任何分支、tag、reflog，没人引用，git gc 按默认两周后清；条目的 index 和文件不动）。每步先 `add -A`、再 `cherry-pick -n`（只改 index 和工作区、不提交；合并和正式 integrate 的 cherry-pick 同一套 merge-ort），叠完 `add -A` + `write-tree` 记这一步之后的树；冲突记 `conflict`、报错停下（解完 `git add` 重跑，那一步记成 `resolved` 和解完的树）；git 没开始就拒绝的记 `failed`。起点带着目标分支没有的提交时和 integrate 一样拒绝（`foreign`），不支持 `--carry` / `--carry-base`。

可重复执行：记下的步骤按（条目、父树、树）和这次的计划比，对得上的不重叠，全对上就什么都不动；对不上的（条目之后又改了、上次停在 `failed`）从那一步起重叠，叠完之后在试集成里做过的改动先存成 `<slug>/trial-fixes-<仓库名>-<时间>.patch`（`saved_fixes`）再退回。上游动了不换起点、报 `upstream_moved`；`--restart` 从最新上游重来（同样先存 patch）。试集成 worktree 的 HEAD 不在记下的起点上（被提交、reset 过）就拒绝、要 `--restart`。最后装依赖（`deps-trial-<仓库名>.json`）。返回 `steps`（每步的条目、提交、状态、冲突文件）、`applied`（这次叠上的）、`resolved`（解过冲突的）、`tree`（叠完的树）、`fixes`（叠完之后改过的文件，合并才有的修正）、`next`（整体检查的命令，存档 `accept-trial-<仓库名>.json`）。

在试集成 worktree 上跑 `accept --integration`：没叠完、index 里有没解决的冲突就拒绝、什么都不动（accept 接着要 `git add -N .`，它会把冲突的 stage 1/2/3 抹掉）；否则在标记新文件、跑验证命令之前用临时 index 取工作区整体状态写成的树，结论（`tree` 是这棵树，`head` 是试集成那时的上游）记进试集成状态的 `check`，通过 = 有 verify、全过。永不推：试集成 worktree 有没提交的改动、没有集成状态，`push` 两道检查都拒。

### push

核对整体检查时，集成状态的 `check`（旧版本没有时按存档认，`check_from: archive`）不是对现在这份内容下的结论（没有、检查的是别的树、检查时不干净）就看同任务、同仓库、同目标的试集成检查：通过、检查时的树和 HEAD 的树相同或只差文档（和集成检查同一条）就算覆盖，返回和推送记录里的 `check` 带 `check_from: trial`（不带 `head`：试集成那时的上游不是检查的提交）；集成 worktree 上对同一份内容的检查没过的，不拿试集成的盖掉。都盖不住时 `check_gap` 在原来的原因后面写明试集成的树和 HEAD 差几个、哪些文件。

rebase 记在集成状态的 `rebases` 里。推送记录（`<slug>/pushes.jsonl`，不管有没有集成状态，旧版 patch 模式的推送也记）每行有：目标分支、远端名和推送时的地址（`remote_url`，`git remote get-url --push` 的结果，取不到记 null）、推送时的本地分支和 worktree、推送前的远端头、远端提交、范围、提交列表、时间、是否核对过、覆盖它的整体检查（`check`）或 `--unchecked` 的理由。集成状态按目标分支各一份，一个仓库推两条分支不用改名归档；推送记录不挂在集成状态上，integrate 重建集成 worktree 也不丢（旧任务记在集成状态里的推送，重建前会转进 `pushes.jsonl`）。

### wrapup

`--draft` 只写那一个文件，写成了在任务的时间线 `timeline.jsonl` 记一条收尾汇总的时间（统计用）。推送记录读 `pushes.jsonl` 和旧任务 `integrate-*.json` 里的，每个仓库每条目标分支一行（同一个任务推了两条分支的各占一行，旧版 patch 模式的推送也在），并用 `ls-remote` 核对远端是否还包含推送的提交（最后一次推送推到了别处，即 remote 之后换过地址的，不拿新地址核对：`remote.checked` 是 false，`reason` 写明两个地址）。旧版本 patch 模式的推送没落盘，"提交"一节按状态文件手写。

### cleanup

逐个 worktree 和没有 worktree 的同类本地分支核实：没有进行中的 merge / rebase / cherry-pick；里面的 agent（在它的 workspace 里，或 cwd 在 worktree 下）不在 working / blocked（按屏幕修正过的状态，idle / done 才算没在干活）；已提交的都在这个任务推过的某条远端分支里（祖先，或补丁内容相同、挑过去的也算）；没提交的改动（含没被忽略的未跟踪文件）都在那条远端分支的历史里（推上去之后又被后来的轮次改过也算：先对分支头、再对推上去的提交和之后碰过这些文件的提交逐个试，整份对不上就逐个文件找，每个文件可以落在不同的提交上；每个文件最多往回查 200 个提交，查不完的算核实不了、不删；比 worktree 现有内容还旧的提交（HEAD 的祖先）不拿来核对：在那上面对得上只说明把后来推的东西退回去了）。上面严格比对不上的（集成时补丁 `--3way` 叠上去、冲突手工解过，或者 cherry-pick 解过冲突，推上去的树里上下文变了；已提交那侧的合并提交、解过冲突的 cherry-pick 也一样），按文件三方合并再判：base 是没核实过的内容开始之前（提交都核实过是 HEAD，有没核实的提交是最老那个的父提交，其中有合并提交才用分叉点；一律用分叉点时，推上去之后又删掉自己推过的行会判成已包含），冲突块先按行核、再对两边都改过的行按词核：我们新写 / 改过的每一行，远端这一处有一模一样的就过，没有的得是两边都改过的同一行，按词核对我们改的每一处都在（新写的词远端同一处都有、删掉 / 换掉的词远端也删了 / 换了，远端自己另改的词不管，同一个词两边改成不同的算不包含）；我们删掉 / 换掉的行远端原样留着不算（我们这边的行在远端连续出现也不算：换掉一行、远端新旧两行都在不是包含），远端这一处和它最像的那一行也得带着我们的改动（我们删了、对方改了几个字还留着的不算）。不冲突的地方要整行相同，所以"加的一行在文件别处出现过"不算。可执行位变化算改动（远端那个提交里的模式也得一样）；worktree 里有内嵌 git 仓库（子目录自己有 `.git`）的核实不了、不删，`--probe` 也不存不删。（先按行的原因：表格相邻几行两边各改、我们还在中间插了两行时，整块按词对齐会让插的两行和远端错开几个词，误判成不在远端。）

有没提交改动的 worktree（比如旧版 patch 模式留下的条目 worktree）删时 cleanup 自己加 `--force`：git 对有改动的 worktree 一律拒，cleanup 已逐个文件核实这些改动都在远端；干净的不加。

### review

验收存档按复审者名找：`<agent名>-rev` → `<slug>/accept-<agent名>.json`，没有再用旧的 `<slug>/accept.json`，`--accept-json` 可直接指定。复审 prompt 存到 `<slug>/<agent名>-rev.prompt.md`，模板里包括：全部改动的 diff 命令、取自验收存档的图谱摘要、code-review-graph 工具用法、"只读沙箱里不要跑测试"、按 High/Medium/Low 输出、最后说明用了哪些图谱工具。codex 以 `-a on-request -s read-only --no-daemon --no-alt-screen -c model_reasoning_effort=xhigh` 启动。

## 目录布局

### 包里的

```
drover/
├── bin/drover                      # drover CLI（Python 标准库，子命令输出 JSON）
├── flow/
│   ├── dispatch.md                 # 编排者流程的主干 = Claude Code 的 /drover 命令；附录 A 是 Claude Code，附录 B 是 Codex
│   ├── integrate.md                # 拆出去、走到那一步才读的：集成与推送（含新一轮的起点）
│   ├── wrapup.md                   # 同上：收尾与清理
│   ├── sub-orch.md                 # 同上：子编排者
│   ├── review.md                   # 同上：复审
│   ├── coding-rules.md             # 包默认的编码规范：brief 追加进每份 worker 任务书（config 的 default_coding_rules 可关）
│   └── routing.md                  # 主工作台的路由规则，安装时渲染进 <workspace>/AGENTS.md 的标记区块
├── profiles/                       # 团队 profile（见 docs/configuration.md）
│   ├── README.md                   # 三层关系、目录布局、怎么写新的团队 profile
│   └── example/                    # 示例团队 profile（虚构的团队和仓库），照着写你自己的
├── third_party/ub-mode/            # Robert C. Martin 的 ub-mode 原文和许可证（MIT），编码规范的通用原则改编自它
├── codex/skills/drover/SKILL.md    # Codex 的 $drover 技能：一层壳，让 codex 去读 flow/dispatch.md
├── hooks/cd_git_guard.py           # Claude Code 的 PreToolUse hook：拦下 cd <目录> && git …，提示改成 git -C（install 装进 ~/.claude/settings.json）
├── install.sh                      # 一键安装入口：检查 git / python3，再把参数交给 install.py
├── install.py                      # 安装脚本（Claude Code / Codex，link / copy；缺 herdr 时可以替你装；飞书 oncall 插件、主会话登记 hook）
├── tests/                          # install.py、install.sh、cd+git hook 和 drover update 的测试
├── selftest/                       # bin/drover 的自测：假 herdr、本地裸仓库，不碰真实状态；run.py 并行跑全部自测（见 docs/development.md）
├── README.md                       # 首页：是什么、怎么装、怎么开始、主要概念
├── LICENSE                         # MIT
└── docs/
    ├── architecture.html           # 技术架构与流程说明
    ├── agent-install.md            # 写给 agent 的安装步骤（README 开头让 agent 先读它）
    ├── install.md                  # 依赖、安装参数、install 会改哪些地方、检查 / 更新 / 卸载
    ├── auto-update.md              # 自动更新
    ├── configuration.md            # config.json 和团队 profile
    ├── quality-tools.md            # 验收工具 crapper、dryer
    ├── usage.md                    # 日常用法、Codex 注意事项
    ├── oncall.md                   # 飞书 oncall
    ├── reference.md                # 子命令和目录布局（本文）
    ├── troubleshooting.md          # 排障
    ├── development.md              # 设计取舍、开发约定、发版、自测
    └── images/                     # 插画（等距科技风）；architecture-overview.svg、dispatch-sequence.svg 是从 architecture.html 抽出的两张图
```

流程正文只有一份，两个宿主共用：主干 `flow/dispatch.md`（每个任务都走的部分，编排者开场整份读），加上走到那一步才读的 `integrate.md`、`wrapup.md`、`sub-orch.md`、`review.md`（主干在那一步写了先读哪份）；宿主不同的做法（怎么追踪、怎么提问、沙箱）放在主干的附录里。

### 装好后本机的

```
~/.drover/
├── src/                                          # 默认的 clone 位置（clone 放在别处时没有这个目录）
├── pkg -> <你的 clone>                           # link 模式软链到 clone；copy 模式下是副本，根目录有 .drover-copy 标记（记着 source、commit）
├── bin/drover -> ~/.drover/pkg/bin/drover
├── config.json                                   # workspace、repo_dirs、profile、playbook_dirs、oncall
├── drover-install.json                           # 安装记录：宿主、新建的文件、加的规则和 hook、备份、飞书 oncall（--uninstall / --check 用）
├── oncall-images/                                # 飞书里发给主会话的图片（飞书 oncall 插件存的，7 天后自动删；卸载时保留）
├── tools/                                        # 验收工具（见 docs/quality-tools.md，卸载时删）：crapper/、dryer/（钉提交的 clone + .venv）、grammars/（tree-sitter 语法包）；
│                                                 #   本机没有 Python 3.11+ 时还有 python/（uv 装的），没有 uv 时还有 bin/uv
└── backups/codex-skills/                         # Codex 技能目录的备份（有替换时才有）

~/.claude/                                        # --host 含 claude
├── commands/drover.md -> ~/.drover/pkg/flow/dispatch.md
├── skills/herdr/SKILL.md
└── settings.json                                 # 合并进去的权限规则、cd+git 拦截 hook（hooks.PreToolUse）、主会话登记 hook（hooks.SessionStart）、读新规则 hook（hooks.UserPromptSubmit）

~/.codex/skills/                                  # --host 含 codex
├── drover -> ~/.drover/pkg/codex/skills/drover
└── herdr

<herdr 插件配置目录>/drover.env                     # 飞书 oncall（herdr plugin config-dir com.codreamer.herdr.oncall 给的目录，
                                                  # macOS 上一般是 ~/.config/herdr/plugins/config/com.codreamer.herdr.oncall/）；
                                                  # 同目录的 .env 是你的飞书配对，drover 不碰；插件本身由 herdr 管理

<workspace>/
├── AGENTS.md                                     # <!-- drover:routing:begin --> … <!-- drover:routing:end --> 区块
├── CLAUDE.md                                     # 含 @AGENTS.md
└── .claude/settings.local.json                   # 合并进去的权限规则
```

每一项的说明见[install 会改哪些地方](install.md#install-会改哪些地方)。

### 运行时的

```
~/.drover/
├── <slug>/                          # 一个任务一个目录（slug 不能和固定目录重名：pkg、src、bin、backups、baselines、locks、oncall-images、tools）
│   ├── request.md                   # 主会话写的需求：你的原话、目标（复述）、标题（看板的任务列）、@仓库和参数、已知上下文
│   ├── dispatch.md                  # 编排者的状态文件：目标、计划、每一步的 ID 和结果、最终汇报（history 从这里摘）
│   ├── rollup.md                    # 一页汇报（≤ 40 行）：状态 / 标题 / 摘要 / 要你做 / 收尾 / 提醒 / 需要你决定 / 当前进度 / 结论
│   ├── orch.json                    # 编排者名、宿主（claude / codex）、default_kind（主会话的宿主，没写 --agent 的 worker 用它）、model / effort（任务默认，orch-start 给的或继承来的，没指定是 null；同宿主的 worker、复审、子编排者不传时继承）、tab / pane、resume_reported_session（报给 herdr 的续命令用的会话 ID，没报是 null，后台维护会改它）、retired_names（这个任务换下来的编排者名，一个列表：换编排者（resume）名字变了时把前任的名字加进去，连着换几次累积，同名再起不记；这些名字仍归这个任务——旧编排者在关 tab 之前还活着，不能落回更早的同名任务——但不拿去对活 agent，之后有更新的任务再用这个名字就归它），orch-status 靠它；orch-start 起 agent 之前写、起不来就恢复原样；几个任务的编排者同名时，活着的那个算修改时间最新的那份的
│   ├── main-seen.json               # 主会话读过的那一版 rollup（内容哈希、规范化后的行、读的时间），主会话跑 drover rollup 时写（别人跑不写），判断未读靠它
│   ├── main-watch.json              # watch --main 报过的事和报的时间（rollup、弹框指纹：仍未读 / 还在的 10 分钟后再报；api-error、gone、停住的起点），重挂不重报
│   ├── agent-state.json             # 编排者现在的状态（按屏幕修正后的 shown、herdr 的序号 seq、弹框指纹）和它从什么时候开始（since），watch --main 和看板每一轮记，看板的"卡在确认框 N 分钟"、停了没更新靠它
│   ├── answered.json                # 主会话最近一次把答复转给编排者的时间和当时 rollup 的哈希，prompt 写，看板靠它分出"已转答复"（rollup 内容没变才算）
│   ├── timeline.jsonl               # 时间线（统计数据，只追加）：派发 / 换编排者、转答复、收尾请求、收尾汇总、主会话读 rollup（命令当场记），
│   │                                #   显示状态变化、编排者每换一段状态、停了没更新（watch --main 和看板补，同时跑只记一次）；看板的"状态持续"按它算
│   ├── wrapup-clicked.json          # 看板上点过收尾的时间和送的那句话："收尾中"靠它（rollup 在点了之后重写、过了 30 分钟就不算了）
│   ├── parent.json                  # 只有子编排者有：上层任务是谁
│   ├── deps-<仓库>.json              # 依赖安装结果
│   ├── <agent>.task.md              # 编排者写的任务段（背景 / 要做的 / 不做 / 线索）
│   ├── <agent>.prompt.md            # brief 生成、实际发给 worker 的完整任务书
│   ├── <agent>.evidence.json        # brief 写的证据清单：每条完成证据和验收重跑它的命令（--evidence-cmd），accept --name 读
│   ├── <agent>.survey.md            # 摸底结果（走摸底的条目才有）
│   ├── <agent>.md                   # worker 的报告（=== DISPATCH RESULT === 块）
│   ├── <agent>.agent.json           # worker 记录（agent-start 写）：启动参数、pane、会话 ID、state（running / released）、报给 herdr 的会话 ID，release、续上和续命令同步按它，字段见 agent-start
│   ├── <agent>.prompt.rN.md         # 同一个 agent 再 brief 时挪走的上一轮任务书（报告、摸底同样是 <agent>.rN.md、<agent>.survey.rN.md）
│   ├── evidence/<agent>/            # 证据产物：截图、录屏、接口响应、能重跑的检查脚本（worker 放，报告列路径）
│   ├── heavy-runs.jsonl             # heavy 流水：条目 worktree 里经 heavy 跑的每条命令一行（worktree、命令、退出码、汇总行、改动指纹）
│   ├── accept-<agent>.json          # 条目验收存档（accept --slug --name 的默认路径；review 按 <agent>-rev 找它）
│   ├── accept.json                  # 旧文档的条目验收存档路径（几个条目会互相覆盖，review 找不到 accept-<agent>.json 时才用）
│   ├── <agent>-rev.prompt.md        # 复审的 prompt（带 --review 时）
│   ├── <agent>-rev.result.md        # 复审输出原文
│   ├── integrate-<仓库>@<目标>.json  # 集成状态，每个（仓库，目标分支）一份（目标里的 / 换成 ~）：集成分支和 worktree、base、已 cherry-pick 的提交（摘之前的 HEAD）、rebase 记录、整体检查结论 check（push 按它核对）、新建远程分支的 create_from。旧版本的 integrate-<仓库>.json 按里面记的目标照用（旧任务的推送记录也在这里）
│   ├── accept-integrate-<仓库>.json  # 集成后整体检查的存档
│   ├── trial-<仓库>@<目标>.json      # 试集成状态，每个（仓库，目标分支）一份（命名同集成状态）：试集成分支和 worktree、起点 base / base_tree、每一步（条目、提交或没提交的改动、父树、树、之前 / 之后的树、状态、冲突文件；integrate 按它还原解过的冲突）、叠完的树 tree、整体检查结论 check（push 认它：check_from: trial）
│   ├── accept-trial-<仓库>.json      # 试集成上整体检查的存档（wrapup 的整体检查一节也列它）
│   ├── deps-trial-<仓库>.json        # 试集成 worktree 的依赖安装结果（集成 worktree 的是 deps-integrate-<仓库>.json）
│   ├── trial-fixes-<仓库>-<时间>.patch # trial 重叠 / --restart 前存下的、叠完之后在试集成里做过的改动
│   ├── pushes.jsonl                 # 推送记录：每次 drover push 推上去追加一行（目标分支、远端名和推送时的地址、本地分支、远端提交、范围、提交、时间、核对过没有；覆盖它的整体检查 check，或 --unchecked 的理由和差在哪）
│   ├── starts.jsonl                 # wt 新建条目 worktree 时的起点（--carry 之前的 HEAD）：integrate 只摘起点之后的提交
│   ├── carry.jsonl                  # wt / integrate --carry 带上上一轮提交、integrate --carry-base 带上起点带着的提交（kind: base）的记录：理由、起点、带上的提交
│   ├── probe-<分支>-<时间>.*.patch   # cleanup --probe 删探测 worktree 前存的 patch（commits：本地独有的提交；worktree：没提交的改动）
│   └── wrapup.md                    # 收尾汇总：变更、提交、数据库、配置、上线、清理（说"收尾"时写）
├── locks/heavy/                     # heavy 调度（构建 / 测试命令按资源排队）：sched.lock（读改调度状态时的锁）、
│                                    #   run-*.json（每条在跑 / 排队的命令一张票，属主进程一直持着它的 flock，进程被杀、崩溃后下一个人当场回收）、
│                                    #   history.json（按"命令形状"、按"同一条命令"分别记最近 5 次实测：墙钟、CPU 时间、进程树内存峰值、退出码；
│                                    #   各最多几百条，按最近使用留）。命令形状 = 仓库（git 公共目录，各 worktree 共用）+ 子目录 + 命令（选项原样，
│                                    #   文件参数按个数分档，带通配符的值不算文件）；同一条命令 = 仓库 + 子目录 + 参数一个不少（路径按 worktree
│                                    #   规范化）。需求先看这条命令自己的实测，没有用同形状的，再没有用同仓库同程序的。容量从性能核起步：在一台
│                                    #   Apple M4（4 性能核 + 6 能效核）上实测，几条命令合计
│                                    #   不超过 4 核时同跑几乎不变慢、总用时降 29～37%，超过后每条慢 1.8～2.9 倍、全量 vitest 超时
│                                    #   （同样的活 CPU 时间涨 2 倍多，挤到了能效核上），内存是次要因素。这条只是这台机器的结论，所以容量
│                                    #   会按真实命令自动校准：命令跑完时看它跑的这段时间里有没有别的非小命令重叠——没有就把墙钟记成这条命令
│                                    #   "单独跑"的基准（最近 3 次）；有、且同一条命令单独跑过至少 2 次、几次之间最慢 ÷ 最快 ≤ 1.2（`heavy_calib_fast`）
│                                    #   就是一个样本，变慢倍数 = 墙钟 ÷ 基准的中位数，负载 = 这段时间 heavy 命令的实测占用合计（按时间加权、
│                                    #   含自己；不用预留合计：放行按实测以后，"独占"的收尾和别的同跑会被算成满负载）。
│                                    #   基准只认同一条命令：按命令形状认的话，`-p a.py` 和 `-p b.py` 互为基准（单独跑 3.1～35.8 秒的
│                                    #   几个文件共用一个基准），会把容量误降一核；旧版本按命令形状攒下的校准状态升级后作废、从硬件值重新校准。
│                                    #   连续 2 个明显变慢（≥ 1.5 倍，负载 ≥ 容量一半）降一核、不低于 1；连续 3 个
│                                    #   几乎不变慢（≤ 1.2 倍，负载 ≥ 容量 − 1）加一核、不超过逻辑核数；其他样本不算数。一次一步，动完重新攒；
│                                    #   降下来过的值再升回去，要的连续样本随降过的次数增加（3、6、9，最多 12），免得在两个值之间来回试。
│                                    #   被杀、超时、小命令不当样本。校准值和最近的样本存在 history.json，`drover heavy --status` 看得到；
│                                    #   不想校准就在 config 写 `heavy_cpu_capacity` 固定。
│                                    #   放行按实时 CPU（设计底线：不能在 CPU 空闲的时候白白干等）：排队的每 0.25 秒看一次整机实测
│                                    #   （macOS 的 host_processor_info 各核相加——host_statistics 对普通进程全机限速、每秒只给 2～10 次新数据，
│                                    #   别的返回缓存，几个进程一起读就常常读到旧值；Linux 的 /proc/stat。最近 2 秒的平均，所有进程都算）和各条在跑命令的进程树
│                                    #   实测（属主每秒用 ps 采一次写进票里）。同时满足就放：heavy 的占用 + 需求 ≤ 容量 × 1.2（`heavy_calib_fast`，
│                                    #   校准眼里"几乎不变慢"的界线；占用不超过整机实测），整机忙 + 需求 ≤ 逻辑核数（调度外的负载占满了就不往上加）；
│                                    #   没有 heavy 在跑时总放一条。容量只管 heavy 自己、整机只看有没有满：本机调度外的负载常年 2～6 核
│                                    #   （Spotlight、WindowServer、浏览器），算进容量里机器空着一大半就开始排队。刚放行的命令在
│                                    #   `heavy_grace_seconds`（5）秒内按预留算（还没在实测里显出来）；没跑过的先按 1 核；"独占"
│                                    #   只是需求 ≥ 容量，它的收尾、I/O 阶段实测空出来就放别的进来。防饿死：排头等了 2 分钟，后来的只放能在它
│                                    #   预计开跑前跑完的（在跑的按历史最长墙钟算结束，超过了、没有历史的按超出 / 已跑多久再估多久；EASY
│                                    #   backfill），这是 CPU 可以空着还有人排队的唯一情形。codex 沙箱里
│                                    #   ps 不让执行、整机实测读得到：没有进程树实测的票按预留算、但不超过整机实测；整机也读不到时退回按预留
├── locks/update.lock                # 更新锁：同一时间只跑一个 drover update（自动的、手动的）
├── locks/agent-<名>.lock            # 对同一个 agent 互斥：release、闲置扫描、prompt、brief --send、agent-start（起 agent 到写完记录）、后台维护的续命令同步和批信任框都拿
├── locks/idle-release.lock          # 闲置扫描一次只跑一个
├── locks/orch-start-<slug>.lock     # orch-start 从写 orch.json 到起好编排者拿着：这一段 watch --main、看板、orch-status 不算这份记录（进程退出自动放开）
├── locks/upkeep-tick.json           # 上次起后台维护（drover upkeep）的时间（满 60 秒才再起）
├── idle-release.json                # 闲置扫描记的候选：每个空闲着的 worker 的 pane、herdr 状态序号、从什么时候开始空闲
├── upkeep.log                       # 后台维护的输出（每次一个 JSON：trust_dialogs 的 approved / busy，resume_sync 的 reported / failed / busy，idle_release 的 released、refused、idle_minutes；批了信任框另有一行"upkeep：批了 <名>…"；超过 512KB 清空重写）
├── board-tab.json                   # 看板 tab 是哪个（tab、pane、workspace）、主会话在哪个 pane，board --tab 复用、--live 认出自己在看板 tab 里、补登记靠它
├── board-live.json                  # 看板 tab 里的 board --live 每轮写的心跳（pid、pane、它跑的代码的版本、时间）；board --tab 靠它认出旧代码的看板、重启
├── board-pane.json                  # 旧的看板分屏（board --pane）是哪个 pane、开在哪个主会话 pane 下面；board --tab 关掉分屏时一起删
├── board-mouse.log                  # 看板 tab 收到的最近 50 条原始鼠标序列（核对触控板左右划的编号用；拖动不记）
├── observe.json                     # watch --main / 看板最后一次看的时间
├── observe-gaps.jsonl               # 没人看的时段（一行 from / to，超过 60 秒才记）：这段时间时间线里的确认框、接口错误记不到
├── locks/observe.lock               # 读改 observe.json 时的锁
├── auto-update.json                 # 自动更新（见 docs/auto-update.md）：最近一次尝试的时间和结果、最近一次更新（from / to / 标题 / 规则变没变 / 自动还是手动 / 报过没有）
├── auto-update.log                  # 后台自动更新的日志
├── main-session.json                # 登记上的主会话：session_id、pane、source（startup / clear / compact / resume），main-register 写；drover rollup 按 pane 认主会话
├── main-rules.json                  # 主会话载入时的 drover 区块，drover rules 拿它比、读完更新
├── main-watch.json                  # watch --main 报过的 drover 自己的事（会话重开），多个 watch 只报一次靠它
├── main-watch-live.json             # watch --main 每轮写的心跳（pid、时间）；看板看到它停了 5 分钟、"主会话在处理"又不空，就给主会话送"接上"
├── board-nudge.json                 # 看板为哪一段断档（心跳的时间）送过"接上"、什么时候、送成没有：同一段只送一次
└── baselines/                       # 基线比对缓存（按仓库 + 基线 commit + 命令 + 重跑的文件；只存可信的结果），同一个键的跨进程锁 <键>.lock，以及临时的基线 worktree

~/.herdr/worktrees/<仓库>/…          # Herdr 管理的 worktree，条目的和集成的都在这里
```

任务目录不会被自动清理，它也是 `history` 的数据来源：以后派发到同一个仓库时，编排者会从这里找以前的决定和踩过的坑。
