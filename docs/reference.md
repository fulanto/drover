# 子命令和目录布局

## 子命令速查

脚本是 `~/.drover/bin/drover <子命令>`。除 `watch`、`heavy`（输出原样透传）、`board`、`rollup`、`rules`（输出给人看的文本，`board --json` 才是 JSON）和 `update`（默认打印几行中文，加 `--json` 才输出 JSON）外，每个子命令都向 stdout 输出一个 JSON：`ok` 表示成不成功，失败时 `error` 写明原因和下一步。`<子命令> --help` 查参数；会改动东西的子命令都支持 `--dry-run`，只列出要执行的命令。

"谁用"一栏：**你** = 日常会直接用；**主会话** = 路由时用；**编排者** = 主要由编排者（或 worker）调用，一般不用手敲。

| 子命令 | 做什么 | 谁用 |
|---|---|---|
| `orch-status` | 列出编排者：herdr 状态 + rollup 状态行和摘要（`rollup_summary`），按屏幕修正的 `screen_status`，卡在确认框上的列进 `blocked`；`unread` 是主会话还没读过这一版 rollup，`answered_after_rollup` 是 rollup 之后转过你的答复，`needs_attention` 只算未读、编排者已停下的；`--all` 含已退出的，`--with-sub` 含子编排者 | **你**、主会话 |
| `board` | 看板，每次现场从 rollup 和 herdr 生成：一行一个任务，按等你 / 进行中 / 完成分组，一组一张表，列是 任务（中文标题）\| 当前状态 \| 要你做什么 \| 状态持续 \| 代号，完成表多一列收尾；写了提醒的任务，提醒跟在它下面（框线表里写在那个任务最后一行下面、后面几列合成一格，Markdown 每张表下面"提醒（<任务>）：…"），最底下一行"主会话在处理"（确认框、接口错误、退出按状态本身持续了多久，记在 `<slug>/agent-state.json`）。默认输出 Markdown（GFM 表格，主会话在终端里原样回给用户）；`--plain` 纯文字（飞书用，一个任务两行、写了提醒的三行，最后一行写能收尾几个）。第一行末尾是 drover 的版本，更新过（自动的、手动的）、被挡住时多一行（[自动更新](auto-update.md)）。读屏的几种（文本、`--plain`、`--json`、`--live`）顺带把显示状态的变化、编排者每换一段状态补进时间线 `<slug>/timeline.jsonl`（同 `watch --main`）。`--json` 给主会话用（`tasks` 每项的 `reasons` 列出每个原因的 `kind`、`text`、`since`、`since_source`，`wrapup`、`wrapup_reason`、`deliverables`（旧 rollup 的"## 交付"，没有是空的），`title`、`ask`、`display_state`、`state_since`、`state_seconds`、`total_seconds`、`wrapup_clicked`、`note`（提醒原文，没有是 null）；顶层 `wrapup_ready` 是能一键收尾的任务，点过的也算；另有 `drover` 字段：版本、最近一次更新（`auto_update.source` 是 auto / manual）、`rules_changed`）；`--line` 一行汇总（等你 / 进行中 / 完成，给状态栏，不读屏、不写文件）；`--pending [--except <slug>]...` 只列其他在等你的（给转达当脚注，没有就不输出）；`--live [--interval 15]` 循环重画（框线表，当前状态、要你做什么写长了在格子里折行、最多 3 行，比终端宽时任务列固定、其余整列左右滚，太窄退回列表），顺带补登记丢了名字的主会话，在看板 tab 里时 tab 名跟着汇总改、能点某一行的 [收尾]（留在看板，变"收尾中"）和 [全部收尾]（送完跳回主会话；都交给飞书插件的 `send-main.mjs` 送进主会话）、点 [<] [>] 或触控板左右划、← → 键左右滚，点别处什么都不做，按住左键拖动选中、松开自动复制（OSC 52，底下显示"已复制 N 行"；按下松开在同一格才算单击，拖动中不刷新），每轮写心跳 `board-live.json`；装着的 drover 换了版本（两边都取得到、又不一样）就试跑新代码、恢复终端、原样重新执行自己（进程号不变），新代码跑不起来时看板上写原因、接着用旧代码、下一轮再试；`--tab` 在主对话所在的 workspace 里开一个跑 `--live` 的"看板"tab（不抢焦点），已经有了就复用，复用时心跳没有、不是这个 pane 的、版本和装着的不一样、超过 120 秒没更新就 `herdr pane send-keys <pane> ctrl+c` 再 `pane run` 重启（`restarted: true`、`restart_reason`），顺带关掉 `--pane` 开的旧分屏（`--dry-run`）；`--pane` 是旧的下方分屏，留着向后兼容。文本、`--plain`、`--json`、`--live` 会触发自动更新检查 | **你**、主会话 |
| `rollup` | `--slug <slug>` 或 `--name <编排者名>`（二选一；`--name` 按 orch.json 找任务，同名取最新的，`watch --main` 的事件行带的就是编排者名）：打印 rollup 原文，开头一行说和主会话上次读的相比改了什么（第一次读 / 内容没变 / 改了 N 行并列出）。主会话读（`HERDR_PANE_ID` 等于 `main-session.json` 的 `pane_id`，`main-register` 登记时记的）同时记为已读（`main-seen.json`），读到新内容时在时间线记一条；别人读（编排者、worker、普通终端）照样输出，最前面多一行"# 不是主会话（原因），没记已读"，两样都不写 | 主会话；编排者也能用 |
| `rules` | 主会话读 drover 规则的改动，不用 `/clear`（[自动更新](auto-update.md)）：`AGENTS.md` 里的 drover 区块和主会话载入时的（`main-rules.json`）比，开头一句"从现在起以下面为准"，再是改动的行；`--full` 给整个区块。读完记成载入时，最近一次自动更新标成已报。`--hook` 给 Claude Code 的 UserPromptSubmit hook 用（只在主会话、规则变了时输出，退出码恒为 0） | 主会话 |
| `oncall` | 飞书 oncall（[飞书 oncall](oncall.md)）：`pair` 发起扫码配对，输出里的 `link` 是配对链接（Mac 桌面没弹二维码时用飞书打开它），`--wait` 等到配上再自动发测试消息；`unpair` 解绑、`test` 发测试消息、`on` / `off` 开关（交给插件执行，立即返回，真实结果看 `herdr plugin log list --plugin com.codreamer.herdr.oncall --limit 5`）；`status` 看插件版本、开关、飞书配没配、poller、`drover.env`、主会话登记了没有，不打印密钥 | **你** |
| `main-register` | 把当前会话在 herdr 里登记成主会话（改名为 `drover`）：已经是就什么都不做；别的 pane 已经登记了、自己叫别的名字、在编排者的 `orch` workspace 里都不改，报出原因。`--hook` 给 Claude Code 的 SessionStart hook 用（静默、退出码恒为 0）。认出主会话后记下载入时的规则（`main-rules.json`）、触发自动更新检查；登记上了写 `main-session.json`（`session_id`、`pane_id`、`source`，`rules --hook`、`watch --main`、`drover rollup` 用；Claude Code 不带 `--hook` 的兜底登记只在记的不是这个 pane 时补写，`session_id` 写 null） | 主会话（会话开始时）；你排障时手动跑 |
| `update` | 更新 drover（[检查、更新、卸载](install.md#检查更新卸载)）：按安装记录找到 clone，fetch 上游只快进，按原来的 `--mode` / `--host` 重跑 install、再 `--check`，最后几行中文说更新到哪、改了什么、下一步（规则变了就跑 `drover rules`）；本地有改动、分叉、连不上远端时什么都不改、退出码 1；跑着的版本真的换了时和自动更新一样记进 `auto-update.json`（看板第二行"drover 已更新 a→b（时间，手动）"）。`--dry-run` 只 fetch、看有没有新版本，`--json` 输出 JSON；`--auto` 是后台的自动更新（[自动更新](auto-update.md)：无人值守、没新提交不重跑 install，结果写 `auto-update.json`），一般不用手敲 | **你**、主会话；`--auto` 由主会话那边的命令在后台起 |
| `watch` | 追踪 agent 状态，变化时输出一行 `EVENT <名字> <状态> @<时间>`（编排者另有 `rollup:<状态>` 事件，rollup 按内容判断改没改）；`--once` 只查一次，`--until-event` 第一个事件就退出。`--main` 是主会话专用：不写名字就自动盯所有顶层编排者，只报要处理的事（未读的 rollup、确认框、接口错误、意外退出、停住），另外报 drover 自己的事（`drover updated(…)`、`drover update-blocked`、`drover update-unreachable(24h)`、`/clear` 之后的 `drover session-restarted`），报过的记在磁盘上，重挂不重报，两个同时跑也只报一次 | **你**、主会话、编排者 |
| `history` | 按仓库列出历史派发记录（结论、未决、踩坑），`--grep` 摘出相关行 | **你**、编排者 |
| `playbook` | 读团队 profile 和个人覆盖目录的规则：`general.md` 和每个 `--repo` 的 `repos/<仓库名>.md`，按层拼好；`--text` 只打印正文。配置了 profile 但目录不存在时报错 | **你**（看生效的规则）、编排者 |
| `preflight` | 检查是否在 Herdr 里、herdr server 是否在跑、常用工具装没装 | **你**（排障）、编排者 |
| `accept-summary` | 从验收存档重新打印摘要（含测试计数），可附失败输出 | **你**（想看验收细节时）、编排者 |
| `heavy` | 经调度执行构建 / 测试命令，输出原样透传，退出码同被执行的命令。按"机器还剩多少、这条要多少"决定马上跑还是排队：容量从本机硬件值起步（Apple 芯片的性能核数，其他机器的逻辑核数），之后按真实命令的表现自动校准（同时跑明显变慢就降、接近容量还几乎不变慢就加，config 可固定），需求按这条命令自己最近几次的实测（CPU 时间 ÷ 墙钟）算，它没跑过就看同类命令（同一命令形状）、再看同程序的；放行按实时 CPU（设计底线：CPU 空着不能干等）：整机实测没满、heavy 自己实测的占用加上这条不超过容量的 1.2 倍就跑，真满了才排队；没跑过的先按 1 核，历史上 10 秒内跑完的小命令不排队，排头等太久时后来的只放能在它开跑前跑完的，内存严重紧张时只放单独一条。排队时打印这条要多少、整机实测忙多少、heavy 占多少 / 容量、在跑的是谁；`--status` 看全机调度状态。细节和来由见[目录布局](#目录布局)的 `locks/heavy/`。只执行构建 / 测试工具（跳过开头的 `env` 和 `VAR=值` 之后，程序名得是 mvn、gradle、npm、bun、vitest、tsc、python、node、pytest、make、go、cargo 这类），别的命令不执行、退出码 2：白名单 `Bash(~/.drover/bin/drover:*)` 放行了 heavy，它不能变成放行任意命令的口子。任务书里写成字面的 `~/.drover/bin/drover heavy -- <命令>`，worker 跑时命中白名单、不弹框 | worker（测试、lint、类型检查、构建一律经它）、编排者；你手动跑也可以用 |
| `orch-start` | 为一个任务起编排者：在 herdr 的 `orch` workspace 里开一个 tab，启动 claude 或 codex，发开场 prompt。启动弹窗和 `agent-start` 一样自动处理（包括 Claude Code 对 `--add-dir` 加进来的状态目录弹的 "Adding a new directory" 信任框），处理完再发开场 prompt | 主会话（上层编排者起子编排者也用它） |
| `prompt` | 把文件（或 `--text` 短消息）发给某个 agent，看到它开工（working）就返回（正常 1 秒内，最多等 30 秒），之后交给 `watch`；发给编排者成功后记一个 `answered.json`（看板靠它分出"已转答复，等它接着做"），时间线记一条转答复（内容以"收尾"开头的再记一条收尾请求） | 主会话（转达）、编排者 |
| `wt` | 建或复用 worktree。新建前先 fetch 源仓库主检出的上游，主检出干净且落后时快进（只快进，不硬覆盖），从远端最新起（主检出有改动、分叉时不动它，改从上游起并在 `warnings` 里报出；上游在远端已删当作没有上游；主检出不在远端默认分支上时提示一句；`--no-sync` 不同步）；复制 `.claude/settings.local.json`、`.env*`（源仓库没跟踪的，或在新 worktree 里被 gitignore 的），以及被 gitignore 的仓库规范文件（`CLAUDE.md` 等），以及仓库根目录 `.worktreeinclude`（Claude Code 的约定，gitignore 语法）匹配到的被忽略文件（如 Java 仓库的本地配置、本机测试页；在新 worktree 里不被忽略的不复制，列在 `worktreeinclude_skipped`）。多轮任务的新一轮（`--slug`，不传按分支名 `task/<slug>…` 归属；任务在这个仓库有推送记录时）：不传 `--base` 从上一轮推的远端分支起（推过两条以上要显式 `--base`）；建之前核实起点含上一轮推的提交（同分支看祖先，换分支按补丁内容比），不含就 `ok: false`、不建，列出缺的提交和两种做法；`--carry "<理由>"` 带上缺的提交照常建（理由记进 `carry.jsonl`）。推到别处的推送（remote 之后换过地址，按记录里的 `remote_url` 比主机 + 路径认，同一个仓库的 ssh / https / scp 写法算同一个）不拿来定起点、不核实、不进 `--carry`，列在 `round_check.other_remote`、进 `warnings`；全推到了别处时 `round_check.status` 是 `other_remote`，起点照第一轮。缺的提交来自没记地址的旧记录时，报错多一段：换过地址就别 `--carry`，给记录补上 `remote_url`（`no_remote_url` 给出记录所在的文件）。没有推送记录时起点和以前一样 | 编排者 |
| `deps` | 在 worktree 里独立安装依赖（不软链源仓库） | 编排者 |
| `agent-start` | 在 pane 里启动 claude / codex worker，自动处理启动弹窗：新目录的信任弹窗、Claude Code 对 `--add-dir` 加进来的目录弹的 "Adding a new directory" 信任框（光标默认在 "No, exit"，移到 "Yes, add it" 再确认）、codex 的升级提示 | 编排者 |
| `brief` | 按模板生成任务书（必填完成证据），可先摸底，`--send` 生成后立刻发出（和 `prompt` 一样看到开工就返回）。模板里限了篇幅：摸底只写结论和证据位置，"和代码不符""需要决策"两部分写全、其余 100 行左右；报告每条 verification 一行、尽量 60 行内，worker 在对话里只回复报告路径和 status | 编排者 |
| `accept` | 验收：标记新文件、建图、改动分析、diff 概览、crapper 查这次改动碰到的函数的圈复杂度、重跑验证命令（认得出 vitest / pytest / mvn 的输出时，退出码 0 却一个测试都没跑也判不通过；每条给 通过 / 失败 / 跳过 三个数）；`--verify-baseline` 和基线比失败条目（基线上只重跑失败的文件；解析不全、提前停了、基线不可信都按失败），`--integration` 是集成检查（不建图、不跑 crapper，dryer 扫全仓、只留碰到改动的重复），`--no-graph` 只是不建图。建图、dryer 和验证命令都经 heavy 调度；多条验证默认同时交给调度（能同时跑的同时跑，结果按传入顺序），前后有依赖的加 `--serial` 逐条跑 | 编排者 |
| `integrate` | 集成 worktree：从最新上游建（或复用），依次 cherry-pick 条目分支，装依赖。新建时和 `wt` 一样核实起点含上一轮推的提交（`--from` 的条目分支里已有的不算缺），`--carry` 同 `wt`；重建集成 worktree 不丢推送记录 | 编排者 |
| `push` | 集成 worktree 推送：上游有新提交就 rebase 并报告（这次不推），否则推送并 `ls-remote` 核对；每次推上去都记进任务的 `pushes.jsonl`（有没有集成状态都记；记下 remote 名和推送时的地址 `remote_url`，返回里也有），定不了任务就不推、提示加 `--slug` | 编排者 |
| `wrapup` | 收尾汇总的事实收集，不改仓库和任务记录：每个仓库每条分支的推送记录（`pushes.jsonl` 和旧任务的 `integrate-*.json`，patch 模式的也在）和远端核对（最后一次推到的是 remote 换地址之前的仓库时不核对，写明两个地址）、推送范围里的迁移 / 配置 / 部署 / 依赖文件和新增配置键线索、整体检查结果、待清理的 worktree 和分支（子编排者的一起收）；`--draft <文件>` 生成 wrapup.md 骨架（只写这一个文件，写成了在时间线记一条收尾汇总） | 编排者 |
| `cleanup` | 用户说"清理"或"收尾"时清任务的 worktree 和分支：`--slug` 全部（含没有 worktree 的同类本地分支），`--wt` 只清指定的。逐个核实没有进行中的 git 操作、里面的 agent 不在 working / blocked、已提交的和没提交的改动都在这个任务推过的远端分支（的历史）里（先 fetch；推上去之后又被后来的轮次改过也算，内容在远端历史里就丢不了；集成时手工解过冲突、推上去的树里上下文变了的，按文件三方合并判"已包含"，冲突块先按行核、两边都改过的行再按词核），全过才 `herdr worktree remove`（有没提交改动的加 `--force`；workspace 没开用 `git worktree remove`）再删本地分支，核实不过的不动、列原因。`--target <远端>/<分支>` 给推送没落盘的老任务补核对分支；`--probe`（配 `--wt`）探测 worktree 先把本地独有的提交和改动存成 patch 到任务目录再删；`--dry-run` 只列结论。主检出永远不动 | 编排者 |
| `review` | 复审一条命令：按模板生成 prompt、分屏启动只读 codex、立刻发出 | 编排者（带 `--review` 时） |
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
~/.drover/bin/drover accept-summary ~/.drover/<slug>/accept.json --tail 2000
~/.drover/bin/drover heavy -- npm test
~/.drover/bin/drover heavy --status                              # 全机的构建 / 测试调度：容量、在跑的各占多少、排队的在等什么
~/.drover/bin/drover oncall status                               # 飞书 oncall 的状态
~/.drover/bin/drover main-register                               # 在主会话里：手动登记 / 看为什么没登记上
~/.drover/bin/drover update                                      # 更新 drover（见 docs/install.md）
~/.drover/bin/drover rules                                       # 主会话：读 drover 规则的改动（自动更新之后，不用 /clear）
```

另外，`/drover`（Claude Code）和 `$drover`（Codex）是编排者用的流程入口，日常不用手动敲。

## 目录布局

### 包里的

```
drover/
├── bin/drover                      # drover CLI（Python 标准库，子命令输出 JSON）
├── flow/
│   ├── dispatch.md                 # 流程正文 = Claude Code 的 /drover 命令；附录 A 是 Claude Code，附录 B 是 Codex
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

流程正文只有 `flow/dispatch.md` 这一份，两个宿主共用；宿主不同的做法（怎么追踪、怎么提问、沙箱）放在它的附录里。

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
│   ├── orch.json                    # 编排者名、宿主（claude / codex）、default_kind（主会话的宿主，没写 --agent 的 worker 用它）、tab / pane，orch-status 靠它
│   ├── main-seen.json               # 主会话读过的那一版 rollup（内容哈希、规范化后的行、读的时间），主会话跑 drover rollup 时写（别人跑不写），判断未读靠它
│   ├── main-watch.json              # watch --main 报过的事（rollup、弹框指纹、api-error、gone、停住的起始时间），重挂不重报
│   ├── agent-state.json             # 编排者现在的状态（按屏幕修正后的 shown、herdr 的序号 seq、弹框指纹）和它从什么时候开始（since），watch --main 和看板每一轮记，看板的"卡在确认框 N 分钟"靠它
│   ├── answered.json                # 主会话最近一次把答复转给编排者的时间和当时 rollup 的哈希，prompt 写，看板靠它分出"已转答复"
│   ├── timeline.jsonl               # 时间线（统计数据，只追加）：派发 / 换编排者、转答复、收尾请求、收尾汇总、主会话读 rollup（命令当场记），
│   │                                #   显示状态变化、编排者每换一段状态、停了没更新（watch --main 和看板补，同时跑只记一次）；看板的"状态持续"按它算
│   ├── wrapup-clicked.json          # 看板上点过收尾的时间和送的那句话："收尾中"靠它（rollup 在点了之后重写、过了 30 分钟就不算了）
│   ├── parent.json                  # 只有子编排者有：上层任务是谁
│   ├── deps-<仓库>.json              # 依赖安装结果
│   ├── <agent>.task.md              # 编排者写的任务段（背景 / 要做的 / 不做 / 线索）
│   ├── <agent>.prompt.md            # brief 生成、实际发给 worker 的完整任务书
│   ├── <agent>.survey.md            # 摸底结果（走摸底的条目才有）
│   ├── <agent>.md                   # worker 的报告（=== DISPATCH RESULT === 块）
│   ├── accept.json                  # 验收存档（accept --save）
│   ├── <agent>-rev.prompt.md        # 复审的 prompt（带 --review 时）
│   ├── <agent>-rev.result.md        # 复审输出原文
│   ├── integrate-<仓库>.json         # 集成状态：base、已 cherry-pick 的提交、rebase 记录（旧任务的推送记录也在这里）
│   ├── accept-integrate-<仓库>.json  # 集成后整体检查的存档
│   ├── pushes.jsonl                 # 推送记录：每次 drover push 推上去追加一行（目标分支、远端名和推送时的地址、本地分支、远端提交、范围、提交、时间、核对过没有）
│   ├── carry.jsonl                  # wt / integrate --carry 带上上一轮提交的记录：理由、起点、带上的提交
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
└── baselines/                       # 基线比对缓存（按仓库 + 基线 commit + 命令 + 重跑的文件；只存可信的结果），同一个键的跨进程锁 <键>.lock，以及临时的基线 worktree

~/.herdr/worktrees/<仓库>/…          # Herdr 管理的 worktree，条目的和集成的都在这里
```

任务目录不会被自动清理，它也是 `history` 的数据来源：以后派发到同一个仓库时，编排者会从这里找以前的决定和踩过的坑。
