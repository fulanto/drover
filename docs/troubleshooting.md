# 排障

## 没装 herdr

`./install.sh` 计划的第一项是"安装 herdr"。在终端里直接跑，正式安装前会和别的缺的依赖一起问你一句 `[Y/n]`；在管道、CI 或者 `--json` 下不会装，计划里那一项是 `✗`，提示里给出官方命令，退出码 1。处理：

```bash
./install.sh --install-deps                      # 不问，直接用官方脚本装（连同选定宿主缺的 claude / codex），装完接着把剩下的装好
# 或者自己装，再重跑 ./install.sh：
curl -fsSL https://herdr.dev/install.sh | sh     # 官方安装脚本，装到 ~/.local/bin/herdr
brew install herdr                               # 或者 Homebrew
```

- 装完 `~/.local/bin` 不在 PATH 里：你同意过的，install 已经往 shell 配置里加了 drover 那一段，开一个新终端就好；没让加的（答了 n、非交互），重跑 `./install.sh --install-deps` 会加，或者自己把 `export PATH="$HOME/.local/bin:$PATH"` 加进 `~/.zshrc`（bash 用 `~/.bashrc`）再开新终端。drover CLI 按名字调用 herdr，不在 PATH 里会报"找不到 herdr 命令"。
- 安装脚本失败（报 `curl` 退出码、下载超时、校验和不对）：多半是网络，换个网络或用 `brew install herdr`。官方脚本要 `curl`、`awk` 和一个算 sha256 的工具，没有 `curl` 时 install 会直接提示你先装 `curl` 或改用 Homebrew。
- 内网有 herdr 的镜像时，可以设环境变量 `DROVER_HERDR_INSTALLER="<你的安装命令>"`，install 会用它代替官方命令（照样要你同意或带 `--install-deps`）。claude、codex、Node 同理（见[最短路径](install.md#最短路径)）。
- 装好 herdr 之后还要在 Herdr 里开主会话，见下一条和[开主会话](usage.md#开主会话)。

## 没装 claude / codex，或者缺 git、python3

- 你选的宿主（`--host`）还没装：和 herdr 一起问你、或者带 `--install-deps` 时用官方安装脚本装上（[最短路径](install.md#最短路径)）。装不上（网络）只提示，drover 的入口照样装，按提示里的官方命令装好就行，不用重跑 install。装好后登录要你自己来：`claude` 第一次运行时按浏览器提示登录，Codex 先 `codex login`。
- 两个宿主都没检测到、又没传 `--host`：install 退出码 2，提示你用 `--host claude|codex|both` 说要哪个，也列出 Claude Code（`curl -fsSL https://claude.ai/install.sh | bash`）和 Codex（`curl -fsSL https://chatgpt.com/codex/install.sh | sh`）的官方安装命令。
- `./install.sh` 报"缺 git""缺 python3"或者"python3 版本太旧"：按它打印的装法装好再重跑。macOS 一般 `xcode-select --install` 弹出的系统框点一下就都有了；Linux 上那几条要 sudo，install 不替你跑，你自己在终端里装。

## 不在 Herdr 里

`drover preflight` 报"不在 Herdr 中（HERDR_ENV 不是 1）"、"herdr server 没在运行"或"找不到 herdr 命令"。

主会话必须开在 Herdr 的 pane 里：先跑 `herdr` 进去，在里面的 pane 里 `cd` 到主工作台再启动 claude / codex（一步步的做法见[开主会话](usage.md#开主会话)）。在普通终端里开的会话起不了编排者。

## agent 状态不对、一直是 unknown

Herdr 靠 agent 状态 hook 判断 working / idle / blocked，hook 没装或版本旧了，状态就不准。

```bash
herdr integration status            # claude、codex 应该是 current
herdr integration install claude    # 不是 current 就重装（codex 同理），或者重跑 python3 install.py
```

重装 hook 后，已经在跑的 agent 重启一下最稳妥。

## herdr 重启后 agent 参数不对

herdr 重启后拉回的编排者、worker 换了模型 / effort、少了附加目录、codex 沙箱变只读，或者续回了 `/clear` 之前的旧会话：herdr 用的是它自己拼的裸续命令（只有会话 ID），没用 drover 报的完整续命令。drover 起的 agent 起好就报，会话变了由后台维护每分钟重报（[herdr 重启后按原参数拉回](reference.md#herdr-重启后按原参数拉回)）。查：

```bash
~/.drover/bin/drover upkeep                       # 手动同步一次：resume_sync 的 reported 是这次补报的，failed 里有报不上的原因
herdr integration status                          # claude、codex 要是 current：会话 ID 是这个 hook 报的，没有会话 ID 就没法报
```

- worker 记录（`~/.drover/<slug>/<agent>.agent.json`）或 `orch.json` 的 `resume_reported_session` 和 `herdr agent get <名>` 的 `agent_session` 一样，才算报过当前会话。
- 编排者起来以后改了 config 的 `claude_model` / `claude_effort` / `codex_effort`：它报的续命令按新默认补，重启后按新的回来（按设计）。
- 你在 pane 里自己退出又手动续了同一个会话：herdr 清掉了报过的续命令，drover 看不出来（会话 ID 没变）。用 `drover agent-start --resume` 续，或者等它下一次换会话。
- claude 起好或 `/clear` 之后还没对过话就重启：`--resume` 一个空会话会报 "No conversation found" 退出（herdr 自己的裸续也一样），`watch` 报 `gone`，按 `gone` 重起。
- codex 起好以后、发出第一句话之前就重启：herdr 那时还没有它的会话 ID，pane 只回到 shell；用 `drover agent-start --resume`（加上原来的参数）续。
- 拉回来的 claude 停在 "Adding a new directory" 信任框上（herdr 报 `blocked`）：它问的是 `--add-dir` 加进来的任务状态目录（worker 是 `~/.drover/<slug>`，编排者是 `~/.drover`），这个框的信任只管一个进程，续同一个会话也要重新批。后台维护（`upkeep`）会自动批：只认 drover 起的 claude、只认这个框、只认 drover 给它加的目录，光标移到 "Yes, add it" 再回车，`~/.drover/upkeep.log` 里有一行"upkeep：批了 <名>…"。多久放行：
  - 重启后最早触发后台维护的是主会话的 SessionStart hook：主会话被 herdr 拉回、会话一开始，hook 跑 `main-register --hook`，它在后台起一次 `upkeep`。前提是主会话是装了 drover hook 的 Claude Code（install.py 装的）、开在主工作台、在 Herdr 里，而且离上一次后台维护满 60 秒（重启前那次一般早就过了）。Codex 主会话没有这个 hook，要等它下一条消息跑看板 / main-register。主会话这条路是读代码确认的，没在真实重启里测过（测试会话里没有主会话）。
  - 那一次只批到当时已经停在框上的。herdr 按 workspace / tab 的顺序每隔 100 毫秒拉起一个 agent（herdr 配置 `session.startup_per_agent_delay_ms`，默认 100），实测一个 claude worker 拉回后约 4.6 秒停到框上，后台维护起来后约 2 秒批完、回到 idle。比主会话晚停到框上的，要等下一次后台维护：满 60 秒以后，而且得有 `watch`、看板或 `main-register` 在跑（重启后通常是你给主会话发下一条消息、它重新挂上 `watch --main` 或跑看板的时候）。
  - 等不及就手动跑一次 `~/.drover/bin/drover upkeep`。自己批也行：读屏确认是这个框，按 ↓ 移到 "Yes, add it" 再回车；直接回车或按 Esc 会让 claude 退出（退出了就用 `agent-start --resume` 加原来的参数续）。
- 主会话（你自己起的）drover 不管，参数按你起的时候的。

## codex 编排者 / 主会话连不上 Herdr

报 `Operation not permitted`：跑在 `workspace-write` 沙箱里了，见[Codex 注意事项](usage.md#codex-注意事项)。Codex 编排者（主会话是 Codex 时默认就是，或者写了 `--orch codex`）由 `orch-start` 起，会自动带 `danger-full-access`；主会话要你自己用 `codex -s danger-full-access` 启动。`python3 install.py --check` 也会提示 `sandbox_mode`。

## 启动卡在信任 / MCP 弹窗

- worker 在新 worktree 里启动时的信任弹窗、Claude Code 对 `--add-dir` 加进来的目录（任务状态目录）弹的 "Adding a new directory" 信任框，`agent-start` 和 `orch-start` 会自动处理（信任弹窗会把光标移到"Yes"再确认：Claude Code 的默认选项是"No, exit"，直接回车 agent 就退出了）。codex 的升级提示不会弹：drover 起 codex 都带 `-c check_for_update_on_startup=false`；要是还弹了（新版 codex 不认这个键），agent 停在 `blocked`，按 Esc 跳过，升级你自己决定。
- 其他弹窗（比如 MCP server 的批准）会让 agent 停在 `blocked`：worker 的由编排者看屏幕后处理，编排者的由主会话处理，破坏性的才问你。
- 编排者的工作目录就是主工作台：**先在主工作台里手动开一次 claude / codex，把信任和 MCP 弹窗点掉**，之后起编排者就不会卡在这里。
- 实在卡住了：`herdr agent focus <名字>` 过去手动选。

## 权限弹窗太多

```bash
python3 install.py --check    # 看权限规则装上没有
```

- 规则在 `~/.claude/settings.json` 和 `<workspace>/.claude/settings.local.json`，缺了重跑 `python3 install.py`。全局只放行 `drover heavy`，整个 drover CLI 只在主工作台放行：worker 跑 `drover push`、`accept`、`cleanup` 之类会弹框，这是故意的（worker 只该用 heavy），别批"don't ask again"。
- Claude Code 的编排者和 worker 跑在 `acceptEdits` 下：写文件自动放行，bash 命令要命中白名单前缀才不弹。有一类弹窗没有"don't ask again"选项（`$(...)` 命令替换、heredoc、`cd X && git …` 这类写法会触发），只能靠不写出这种命令来避免；流程和任务书里已经要求 agent 一条命令只做一件事、脚本先写成文件再执行。其中 `cd X && git …`（git 在前、cd 在后的也算）由 install 装的 cd+git 拦截 hook 兜底（子代理也管得到）：agent 会收到"改成 `git -C X …`"的拒绝理由，自己改写重试，不用你批。`--check` 里"Claude Code 的 cd+git 拦截 hook"一项是 `✗` 时重跑 install。
- 遇到新的弹窗类型，选"don't ask again"时优先选带 `*` 的宽前缀那项（前缀是 drover、git 写操作这类会改状态的程序时只批单次）；编排者也会在汇报里提出来，补进规则。

## `--check` 报某项没装对

`--check` 的输出里，`✗` 是没装对（退出码 1），`!` 只是提示。每一项都带"要做"的说明，照着处理就行；一般重跑 `python3 install.py` 就好（已经装对的项会跳过；copy 模式记得带 `--mode copy`）。常见原因：

- clone 挪了位置或删了（link 模式软链断了）：在新位置重跑 install，或者改用 `--mode copy`；
- 某个位置被别的工具换成了别的文件：重跑 install 会先备份成 `.bak-drover-*` 再换回来；
- 只装了一个宿主，现在要用另一个：`python3 install.py --host both`；
- 必需依赖缺失（herdr、git、python3，claude / codex 一个都没有）：照那一行的"装法"装上；缺 herdr、缺你选的宿主命令行也可以重跑 `./install.sh --install-deps`（见上面"没装 herdr"）；
- `!` 路由区块过期：`flow/routing.md` 更新过、install 没重跑（比如只 `git pull` 了），跑一次 `~/.drover/bin/drover update`（或重跑 install）刷新区块；
- `!` codex 的 `sandbox_mode`：见[Codex 注意事项](usage.md#codex-注意事项)。

安装时报"没检测到 Claude Code 或 Codex"（退出码 2）：PATH 里没有 `claude` / `codex`，也没有 `~/.claude`、`~/.codex`。按提示里的官方命令装一个，或者用 `--host` 指定（见上面"没装 claude / codex"）。

第一次安装报要用 `--workspace` 指定主工作台（退出码 2）：是在非交互环境里跑的（agent 的 shell、管道、CI、`--json`），带上 `--workspace <目录>` 重跑（选哪个目录见[主工作台和仓库目录](install.md#主工作台和仓库目录--workspace----repo-dir)）。

起编排者时 `orch-start` 报 `/drover` 命令或 `$drover` 技能没装（`~/.claude/commands/drover.md`、`~/.codex/skills/drover/SKILL.md` 不存在）：编排者要用的那个宿主没装上，`python3 install.py --host claude`（或 `codex`、`both`）重跑。

报主工作台不能是家目录（退出码 2）：`--workspace` 给了家目录或它的上级（含 `/`），或者已有 `config.json` 里的 workspace 就是家目录（这时 `--check` 给 `!` 提示）。原因见[主工作台和仓库目录](install.md#主工作台和仓库目录--workspace----repo-dir)。换成放代码仓库的那个上级目录，`python3 install.py --workspace <目录>` 重跑；原来家目录 `AGENTS.md` 里那段 drover 区块不会自动删，手动删掉两个标记之间的内容。

## 找不到仓库

报"找不到仓库 xxx：既不是仓库路径，也不在 … 下"：仓库名是按 `config.json` 的 `repo_dirs` 顺序找的。把仓库所在目录加进 `repo_dirs`（见[配置](configuration.md)，改完立即生效），或者 `@` 后面直接写路径，比如 `@~/oss/some-repo`。

仓库不在 `workspace` 下面时，Herdr 建 worktree 要求信任仓库，脚本不会自动替你信任，编排者会先问你。

## 装完 `AGENTS.md` 里规则重复了

原来的 `AGENTS.md` 有内容、没有 drover 标记时，install 把区块追加到末尾，并提示"已有内容可能和路由规则重复，请检查"。它不去猜哪段是通用规则。处理：

- 把区块外和路由规则重复的通用内容删掉，只留你个人的部分；
- 想让区块放在别的位置，就把那对标记连同中间的内容挪过去。以后重跑 install 只整段替换标记之间的内容，不再追加。

还没装的话，按[install 会改哪些地方](install.md#install-会改哪些地方)的建议，先放一对空标记再装。

## 想回滚

```bash
python3 install.py --uninstall --dry-run    # 先看会撤掉什么
python3 install.py --uninstall
```

撤什么、留什么见[检查、更新、卸载](install.md#检查更新卸载)：只撤 install 加的东西，`config.json`、任务记录 `~/.drover/<slug>/`、worktree、herdr hook 都保留。安装时备份的 `*.bak-drover-<时间>` 不会自动还原，卸载时会列出来；也可以自己找：

```bash
find ~/.claude ~/.codex ~/.drover <主工作台> -maxdepth 3 -name "*.bak-drover-*"
```

`<主工作台>` 换成你的主工作台目录。要恢复哪个，就把它挪回原名。Codex 技能目录的备份在 `~/.drover/backups/codex-skills/` 里，恢复时挪回 `~/.codex/skills/` 并去掉 `.bak-drover-<时间>` 后缀。

## 飞书收不到消息

1. `~/.drover/bin/drover oncall status`，按 `hints` 处理：插件在 herdr 里被禁用（`herdr plugin enable com.codreamer.herdr.oncall`）、插件自己的开关关着（`drover oncall on`）、飞书还没配对（`drover oncall pair`）、`drover.env` 不在或过期（重跑 `python3 install.py`）、飞书 oncall 归属另一个 herdr（见下一条）。
2. `hints` 说飞书 oncall 归属另一个 herdr（`plugin_status.owner.current_session` 是 `false`、`owner.socket_path` 不空）：飞书 oncall 同一时间只属于一个 herdr，开过第二个 herdr 会话时它可能留在那边（`owner.running` 是 `false` 说明那个 herdr 已经没在运行了）。这时这个 herdr 里的主会话收不到飞书消息和按钮，`poller.alive` 是 `true` 也一样：收信进程活着，但连的不是这个 herdr。在要用的这个 herdr 里跑 `herdr plugin action invoke poll --plugin com.codreamer.herdr.oncall` 切过来。
3. `plugin_status.poller.alive` 是 `false`：插件收发飞书的常驻进程没起来。herdr 重启后会自动拉起；也可以手动重启它：`herdr plugin action invoke poll --plugin com.codreamer.herdr.oncall`。
4. 能收到推送、但飞书里回复没反应：`plugin_status.channels.feishu.replies_supported` 是 `false`，Node 低于 22，升级 Node（`brew install node`），再重启 herdr。
5. 只收到主会话的、编排者 / worker 的确认框没推：这是设计（[飞书 oncall](oncall.md) 的通知规则），它们卡满 5 / 10 分钟没人批才推。
6. 插件真实做了什么，看它的日志：`herdr plugin log list --plugin com.codreamer.herdr.oncall --limit 20`。

## 飞书配对失败

- **没弹二维码**（没装 Xcode 命令行工具、面板没编译，或者不是 macOS）：用 `drover oncall pair` 输出里的 `link`，在手机飞书里打开它，效果和扫码一样（终端里装时问的那一句也会打印这个链接）。想要桌面面板，`xcode-select --install` 后重跑 install。
- `link` 是空的、`plugin_message` 里有报错：按报错处理；更多看 `herdr plugin log list --plugin com.codreamer.herdr.oncall --limit 5`。
- 扫完不知道成没成：`drover oncall status` 看 `plugin_status.channels.feishu.paired`；或者一开始就用 `drover oncall pair --wait`，配上了它会自动发一条测试消息。链接有效期十来分钟，过期了重跑 `pair`。
- 已经配对过的机器再跑一次只会回"已配对"，不会新建机器人；要换账号先 `drover oncall unpair` 再 `pair`。

## 飞书租户不让建个人机器人

扫码配对要在你的飞书里建一个只属于你的应用（机器人）。有的企业租户关掉了个人建应用的权限，这时扫码后会提示没有权限：找飞书管理员开通，或者用个人飞书账号配对；实在不行就只用桌面面板（macOS，不配飞书也照常提醒），或者 `python3 install.py --no-oncall` 不接飞书（已经装上的插件不卸）。

## 主会话没登记上

飞书里不引用发的消息回"没找到主会话"，或者 `drover oncall status` 里 `drover.main_agent.registered` 是 `false`：

- 先看 `drover oncall status` 的 `hints`：说飞书 oncall 归属另一个 herdr 的，插件是在那个 herdr 里找主会话，这边登记得再对也找不到，照[飞书收不到消息](#飞书收不到消息)第 2 条切过来。
- 在主会话里跑一次 `~/.drover/bin/drover main-register`，它会说为什么没登记：`conflict`（另一个 pane 已经登记成 `drover`，比如开了第二个主会话：关掉那个再跑）、`named`（这个会话已经有别的名字）、`orch_workspace`（在编排者的 `orch` workspace 里开的会话，不算主会话）、`not_in_herdr`（不是在 Herdr 的 pane 里开的）。
- Claude Code：SessionStart hook 在不在，`python3 ~/.drover/pkg/install.py --check` 里"主会话登记 hook"一项；主会话要在主工作台目录本身开（子目录不算）。
- Codex：没有 hook，主会话在你发第一条消息时按路由规则登记；刚开的 Codex 会话还没说过话时，飞书消息找不到它。
- `/clear` 或退出重开后，herdr 会清掉名字，hook 会在一两秒内重新登记；这期间发的飞书消息会回"没找到主会话"，再发一次就好。

## 验收工具没装上，或者验收里报"测不了"

- install 的汇总里写"只记警告"、`--check` 里"验收工具 crapper / dryer"或"验收工具用的 Python 3.11+"是 `!`：提示里写了哪一步失败（git clone、uv、`pip install -e`）和原因，多半是访问不了 GitHub 或 PyPI。网络好了以后重跑 install，或者 `~/.drover/bin/drover update`，会按钉的版本补装。
- 没有 Python 3.11+、也装不上 uv：自己装一个 3.11+（`brew install python@3.12`，或 python.org 的安装包）再重跑 install。
- 验收结果里 `complexity` / `duplicates` 写"测不了：…"：不算验收不通过。"没装"按上一条处理；带 `DownloadError` 的，是第一次分析这种语言时语法包从 GitHub 下不来，下次验收会再试。
- 不想要这两步：`~/.drover/config.json` 写 `"quality_tools": false`（见[验收工具](quality-tools.md)）。

## 其他

- **编排者停了，rollup 却还是"进行中"**：主会话会看一眼它的屏幕，判断是在等后台任务还是卡住了；你也可以 `herdr agent focus <编排者名>` 自己看。
- **worker 好像卡住了**：`watch` 会报 `working-no-output(Nm)`（屏幕几分钟没动）或 `working-no-diff(Nm)`（worktree 很久没改动），编排者会去看、必要时打断并催报告。你不用盯。
- **worker 的 pane 里只剩 shell、`watch` 显示 `released`**：它做完后闲置 10 分钟，进程被自动释放了（腾内存），不是崩了。再给它派活时会按会话 ID 自动续上。不想自动释放：config 的 `worker_idle_release_minutes` 写 `0`。**做完的 worker 一直不退、内存越占越多**：`~/.drover/bin/drover release --idle --dry-run` 看每个 worker 为什么没放，最常见的是 herdr 拿不到会话 ID（`herdr integration status` 里 claude / codex 不是 current）。细节见 [release](reference.md#release)。
- **卡在确认上**：`watch` 报 `blocked`，编排者看屏幕后批，破坏性的才问你。worker 连着弹两个确认时会报两次：同一状态下 herdr 的状态序号变了（中间那下 working 没被轮询到），或者弹框内容换了，`watch` 都会重报，不会再出现"批完第一个、第二个没人管"的空等。Claude Code 的 WebFetch 域名授权弹框（"Do you want to allow Claude to fetch this content?"）herdr 有时报成 done，`watch` 看到屏幕底部是确认弹框就改报 `blocked`。编排者 / worker 派的后台子 agent（Explore 等）弹确认时，主 agent 停在 "Waiting for N background agent(s) to finish"，herdr 按这一行报 working；`watch` 在 working 时也每 10 秒看一次屏幕，底部是确认框就报 `blocked`，子 agent 连着弹的每一个都会报（一直有弹框、herdr 的状态在 blocked 和 working 之间变了，也当作换了弹框重报一次）。唯一认不出的是两个弹框一字不差、中间那下又没轮询到，所以批完都会再看一眼屏幕。
- **被接口错误打断**：`watch` 报 `api-error`，屏幕上最后一个输出块是行首的 `API Error: 503 …`（Claude Code）或 `■ stream error …`（codex）；正文里提到这几个字、或者更早报过错但之后又有正常输出，都不算。这一轮被接口错误打断了，agent 自己不会再动。主会话会等一两分钟再让编排者接着做，编排者也会续上它的 worker。连续 3 次还报错，多半是 API 服务端（或你用的代理）的问题，会告诉你。
- **编排者写完 rollup**：`watch` 对编排者另外单独报一行 `rollup:<状态>`（状态变了就报；"等你决定 / 完成 / 受阻 / 需要换编排者"时内容变了也报，"进行中"只改进度不报）。改没改按内容判断：每行去掉首尾空白、删掉空行后比较，原样重写、只改空白空行不报；不看编排者手写的"更新："行（手写的时间不准，状态没变时还会漏报；这一行现在可以不写）。主会话用的 `watch --main` 再多一层：只报主会话还没用 `drover rollup` 读过的那一版，报过一次就记在磁盘上，重挂、`/clear` 都不重报。编排者写完"等你决定"后会继续挂着 watch 盯 worker（状态是 `waiting-background(N)`），主会话靠这行事件去读 rollup 转给你。
- **想知道某次为什么这么做**：读 `~/.drover/<slug>/dispatch.md`（状态文件），编排者每一步的决定和理由都记在里面。
