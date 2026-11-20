# 自动更新

装好之后 drover 自己更新：有新版本就直接装，不用你确认；主会话开着好几天不 `/clear`、不 `/new` 也照样更新，新规则不用重开会话就用上。"新版本"默认指作者发布的版本（git tag），见下面"更新通道"。

**更新通道：跟发布的版本还是跟 main**

`~/.drover/config.json` 的 `update_channel`，手动 `update`（[检查、更新、卸载](install.md#检查更新卸载)）和自动更新都按它：

| 通道 | 装什么 | 给谁 |
|---|---|---|
| `release`（默认，没写这个键也是它） | 上游（一般是 `origin/main`）历史上最新的版本 tag；main 上没打 tag 的新提交不装 | 用 drover 的人 |
| `main` | 上游最新的提交，推上去就装 | 开发 drover 的人（作者本机：推完就装上） |

- 版本 tag 只认 `vX.Y.Z`（三段数字，按数字比大小：`v0.10.0` 比 `v0.9.0` 新）。带后缀的预发布版（`v1.0.0-rc1`）、不在上游历史上的 tag（打在别的分支上的）不算。
- `release` 通道的 clone 停在 tag 那个提交上，落后 `origin/main` 是正常的，不算落后；clone 比最新的 tag 还新（比如以前跟过 `main`）也不回退，等下一个更新的版本。
- 上游上一个版本 tag 都没有时不更新：看板上"drover 自动更新暂停"那一行写原因，手动 `update` 退出码 1。
- 切通道：编辑 `config.json`，比如加一行 `"update_channel": "main"`，下一次 `update` / 自动更新就按新的走，不用重装。从 `main` 切回 `release` 时 clone 多半已经比最新版本新，等下一个版本才会再动。
- 写了别的值（比如拼错了）：手动 `update` 报错、自动更新记成失败，什么都不改。

作者怎么发版见[怎么发版（作者）](development.md#怎么发版作者)。

**什么时候查**

- 主会话那边的命令顺带查：会话开始时的登记（Claude Code 的 SessionStart hook，Codex 第一条消息时的 `main-register`）、`watch --main` 的每一轮（Claude Code 主会话一直在后台挂着它）、看板（`board`、`board --json`，看板 tab 里的 `board --live` 每 15 秒一轮；Codex 主会话每条消息都跑 `board --json`）。编排者、worker 用的命令不触发。
- 离上次尝试满 30 分钟才查（`config.json` 的 `auto_update_minutes` 可改），同一时间只跑一个（机器级的锁，手动 `update` 也拿它）。
- 查和装都在一个后台进程里跑（`drover update --auto`），触发它的命令立刻返回，不卡会话、不卡会话启动（SessionStart hook 只有 15 秒）。结果写 `~/.drover/auto-update.json`，日志在 `~/.drover/auto-update.log`。

**装什么、什么时候不装**

- 和 `drover update`（[检查、更新、卸载](install.md#检查更新卸载)）一样按更新通道只快进，保护全部沿用：clone 有已跟踪的本地改动又落后、和目标分叉、merge / rebase 做到一半、没有上游、上游上还没有发布的版本、连不上远端，都什么都不改，只把原因记下来。
- 无人值守：fetch 只试 1 次、最多 60 秒，git 不在终端上问账号密码（`GIT_TERMINAL_PROMPT=0`），ssh 不问密码、不问主机确认（`BatchMode=yes`，连不上 20 秒就放弃）。你自己配了 `GIT_SSH_COMMAND` 或 `core.sshCommand` 的不覆盖。笔记本离线、代理没开时连不上很常见，所以连不上只记下来，连续 24 小时都连不上才告诉主会话一次。
- 没有要装的新版本时不重跑 install（修安装还是手动 `update` 的事）；有的时候照[检查、更新、卸载](install.md#检查更新卸载)重跑 install、`--check`。

**怎么关**

- 这台机器一直关：`~/.drover/config.json` 里写 `"auto_update": false`。
- 临时关：环境变量 `DROVER_AUTO_UPDATE=0`。它要在主会话（和看板 tab）的环境里才管用，比如在启动 herdr 的 shell 里设。
- 关了也能随时手动 `~/.drover/bin/drover update`。

**更新完，主会话不重开就用上新规则**

drover 记着主会话"载入时"的规则（`~/.drover/main-rules.json`：会话开始时的登记记一份），`drover rules` 把现在 `AGENTS.md` 里 drover 区块和它的差别送进主会话的上下文（开头一句"从现在起以下面为准，替换你上下文里 AGENTS.md drover 区块的旧版本"），再把"载入时"更新成现在的。

- **Claude Code**：后台更新装完，`watch --main` 报一行 `EVENT drover updated(<旧>..<新>) rules`（两头是版本 tag，比如 `updated(v0.1.0..v0.2.0)`；那个提交上没有 tag 时是 7 位 sha），主会话跑 `drover rules` 读入新规则，再跑一次 `drover board --tab`（看板 tab 里还是旧代码的看板就重启，见下面"看板换新代码"），再用一句话告诉你更新了什么。万一那条没走到（没挂 watch），**你的下一条消息就带上新规则**：install 装的 UserPromptSubmit hook（`drover rules --hook`）在你提交消息时把改动一起送进上下文，飞书发来的消息也算。两条路用同一份"载入时"记录去重，不会读两遍。之后 `/compact` 时 Claude Code 本来就会从磁盘重读 `AGENTS.md`。
- **Codex**：没有后台任务，也不装 hook（[install 会改哪些地方](install.md#install-会改哪些地方)）。主会话每条消息先跑 `board --json`，看到 `drover.rules_changed`（或者 `drover.auto_update.announced` 是 `false`）就跑 `drover rules`，所以新规则在你下一条消息时用上；你说"盯着"时挂着的 `watch --main` 同样会报 `drover updated`。

**`/clear`、`/new` 之后接上**

- **Claude Code：`/clear` 之后自动接上**，不用你先发消息。SessionStart hook 登记时把这次的 `session_id` 写进 `~/.drover/main-session.json`；`/clear` 之前挂着的后台 `watch --main` 会活到 `/clear` 之后，它看到 `session_id` 换了（source 是 clear），报一行 `EVENT drover session-restarted` 退出，这个通知叫醒新对话，新对话按"会话刚开始"接上：看板 tab 复用、先处理看板最底下"主会话在处理"那一行的事、重新挂 watch、一两句话告诉你现在的任务（最多 5 秒，watch 一轮的间隔）。`/compact` 不算重开，不报。新开终端（不是 `/clear`）之前没有 watch 在跑，没人能叫醒它，要等你第一条消息。
- **Codex：要等你第一条消息**（随便一句，或者"接上"）：Codex 没有后台任务，`/new` 之后什么都不会自己跑。看板在自己的 tab 里，照常每 15 秒刷新，任务状态一直看得到。`/new` 会让 herdr 清掉 `drover` 这个名字，飞书消息就送不进主会话；看板的 `--live` 每一轮发现主会话 pane 里的 agent 还在、名字没了、也没有别的 agent 叫 `drover`，就替它补登记（看板上留一行"已补登记主会话"），所以你在手机上发的消息也能当成第一条消息接上。

**看板换新代码**

- 看板 tab 里的 `board --live` 是常驻进程，代码是启动时加载的。以前自动更新以后它第一行显示新的版本号（每轮现读），画表的还是旧代码，要关掉 tab 重开才是新的。现在它启动时记下自己这份代码的版本，每 15 秒一轮和磁盘上装着的比（link 模式是 clone 的 HEAD，copy 模式是副本 `.drover-copy` 里记的提交——update 先快进 clone 再重装副本，中间不会换到旧副本上），变了就先试跑一下新代码，没问题就恢复终端、原样重新执行自己：进程号不变，tab、tab 名照旧，最多 15 秒就是新的看板。取不到版本时不换；新代码跑不起来（比如有语法错误）时看板上多一行"drover 看板没换成新代码 <新>：<原因>；接着用 <旧>，下一轮再试"，接着用旧代码。
- 自己没换过来的（看板停了、卡住了）有兜底：看板 tab 里的 `--live` 每轮写心跳 `~/.drover/board-live.json`（pid、pane、版本、时间）；`board --tab` 复用 tab 时看到没有心跳、心跳不是这个 pane 的、版本和装着的不一样、或者 2 分钟没更新，就在那个 pane 里按 ctrl+c 再重跑看板（返回 `restarted: true` 和原因）。主会话在会话开始、每次 `drover updated` 读完新规则之后都跑 `board --tab`，旧看板就在那时换掉。

**看得到什么**

- 看板第一行末尾是 drover 的版本（`· drover <7 位 sha>`）。最近 24 小时更新过的，下面一行"drover 已自动更新 a→b（时间）"，手动 `drover update` 换了版本的写"drover 已更新 a→b（时间，手动）"；新版本那个提交上有版本 tag 的，括号里先写 tag，比如"drover 已自动更新 a1b2c3d→e4f5a6b（v0.2.0，11-20 10:30）"（手动更新也记进 `auto-update.json`，source 记成 manual；按跑着的这份 drover 的版本算，和第一行对得上：copy 模式 install 没复制成、副本还是旧的就不记，之后重跑成功记旧副本→新版本；跑的人看到输出了，记成已告知，`watch --main` 不再为它叫醒主会话；之前有一次自动更新还没报给主会话的，并成一次照样报）；被挡住或失败的，一行"drover 自动更新暂停：<原因>"，原因里写了怎么处理（比如先提交或 stash clone 里的改动），处理完等下一次检查或手动 `update`。
- `watch --main` 只报一次：`drover updated(…)`、`drover update-blocked`（同一个原因一次）、`drover update-unreachable(24h)`，重挂不重报。`board --json` 的 `drover` 字段：`version`、`auto_update`（`result`、`from`、`to`、`from_tag`、`to_tag`、`titles`、`routing_changed`、`announced` 等）、`rules_changed`。

**正在跑的编排者和 worker**

- 不因为有任务在跑就推迟更新：几乎一直有编排者活着，推迟了就几乎永远更新不了。
- link 模式下，它们的下一条 `drover` 命令跑的就是新代码（copy 模式 install 重写了副本，一样）；已经在跑的 Python 进程（`watch`、`heavy`、`accept`）是旧代码，跑完为止。
- `flow/dispatch.md` 变了的，正在跑的编排者照旧按它上下文里的旧流程做，新起的用新流程。所以 CLI 必须向后兼容，见[开发约定](development.md#开发约定)。
