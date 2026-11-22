# 安装

首页 [README](../README.md#安装) 是最短路径；这里是完整说明：依赖、install 的参数、它会改哪些地方、检查、更新和卸载。装好之后的自动更新见[自动更新](auto-update.md)。

## 依赖

**支持哪些系统**：drover 这一版只支持 macOS（Linux 能装，但未实测）。Windows 暂不支持：原生 Windows 上 drover 起不来（用到了只有 Unix 才有的文件锁和终端控制），herdr 的插件在 Windows 上也还是预览。想先试的，可以在 WSL2 的 Ubuntu 里按 Linux 的方式装（未实测，出了问题欢迎反馈）。

| 系统 | 这一版 | 说明 |
|---|---|---|
| macOS | 完整支持 | 依赖照下表一起装齐 |
| Linux | 能装，未实测 | 代码和 macOS 共用；缺 git、python3 时要 sudo，install.sh 只给命令、你自己装 |
| Windows | 暂不支持 | 原生 Windows 起不来；WSL2 的 Ubuntu 里按 Linux 的方式试（未实测）：管理员 PowerShell 里 `wsl --install`、重启、建好 Linux 用户，再在 Ubuntu 里 clone 和安装，仓库放在 WSL 自己的文件系统里 |

**将来的路线**：先在 WSL2 的 Ubuntu 里把 Linux 这条路完整实测一遍；通过了，就把 WSL2 写成 Windows 的支持方式，同时写清用户自己要做的事（管理员装 WSL2、重启、装 Windows Terminal、仓库放在 WSL 的文件系统里）。原生 Windows 等 herdr 插件在 Windows 上转正、评估完移植量（文件锁、终端控制、进程管理都要换做法）再说。

| | 工具 | 用途 | 没装时 |
|---|---|---|---|
| 必需 | `herdr` | 编排者和 worker 都起在 Herdr 的 pane 里，worktree 也由它建。**主会话必须在 Herdr 里打开**，否则 `drover preflight` 直接报错 | install 一起装（官方安装脚本，装到 `~/.local/bin`；先问你，见[最短路径](#最短路径)） |
| 必需 | `git` | 每个条目一个 git worktree | `./install.sh` 给出装法后停下（macOS 点命令行工具的系统框；Linux 要 sudo，你自己装） |
| 必需 | `python3`（3.9+） | `bin/drover` 和 `install.py` 都只用标准库，不用装包 | `./install.sh` 给出装法后停下（同上） |
| 必需（你选的那个） | `claude` / `codex` | 主会话、编排者、worker 的宿主，两个都装可以混用 | install 只装 `--host` 选的、又没装的那个（官方安装脚本，先问你，见[最短路径](#最短路径)）；装不上只提示。登录要你自己来 |
| 飞书 oncall 要（选装，`--oncall`） | `node`（22+） | 内置的飞书 oncall（herdr-oncall 插件）跑在 Node 上：18～21 能推送，但收不到你在飞书里的回复；没有或低于 18 插件跑不起来 | 一个 node 都没有时 install 用 nvm 装一份 Node 22 到 `~/.nvm`（先问你，不改 shell 配置）；已有但版本低只提示升级（插件先用 PATH 上的 node，另装一份也用不上）。插件这一项按 Node 判断，不满足时报失败、其余照装 |
| 可选 | Xcode 命令行工具（macOS） | 飞书 oncall 的桌面面板（弹框提醒、扫码配对的二维码）装插件时现场编译，要用到它 | 没有时只是没有桌面面板，飞书照常 |
| 可选 | `code-review-graph` | 验收时建图：看改动的风险、找出没有测试覆盖的函数。没装时验收照常，只是少了图谱这一项 | 不管 |
| 默认装（不用你装） | 验收工具 crapper、dryer | 条目验收查改动函数的复杂度、集成检查查碰到改动的重复（[验收工具](quality-tools.md)）。要 Python 3.11+：本机没有时 install 用 uv 装一份进 `~/.drover/tools`，连 uv 也没有就先装一份 drover 私有的 uv | install 装；装不上只记警告、不影响安装，验收时报"测不了" |
| 可选 | `uv` | Python 仓库在 worktree 里装依赖（有 `uv.lock` 用 `uv sync --frozen`，否则复刻源仓库的 `.venv`） | 不管 |
| 可选 | `bun` / `pnpm` | JS 仓库按 lockfile 在 worktree 里装依赖（`yarn`、`npm` 的 lockfile 也认） | 不管 |

各自的官方安装方式（install 里用的就是这些命令；装到哪、改不改 shell 配置都按脚本原文核实过）：

| 工具 | 官方命令 | 出处 |
|---|---|---|
| herdr | `curl -fsSL https://herdr.dev/install.sh \| sh`（装到 `~/.local/bin`，可用 `HERDR_INSTALL_DIR` 改；不用 sudo，不改 shell 配置）；或 `brew install herdr` | [herdr.dev/tui/docs/install](https://herdr.dev/tui/docs/install/) |
| Claude Code | `curl -fsSL https://claude.ai/install.sh \| bash`（官方推荐的原生安装；启动器在 `~/.local/bin/claude`，不用 sudo，不改 shell 配置）；或 `brew install --cask claude-code` | [code.claude.com/docs/en/setup](https://code.claude.com/docs/en/setup) |
| Codex CLI | `curl -fsSL https://chatgpt.com/codex/install.sh \| sh`（装到 `~/.local/bin/codex`，不用 sudo；`~/.local/bin` 不在它进程的 PATH 里时会往 shell 配置末尾追加一段带标记的 PATH，脚本没有关掉它的开关。install 替你装时先把这个目录加进 PATH，它就不加了）；或 `npm install -g @openai/codex`、`brew install --cask codex` | [github.com/openai/codex](https://github.com/openai/codex) |
| git | macOS：`xcode-select --install`（Apple 命令行工具）或 `brew install git`；Linux：`sudo apt-get install git` / `sudo dnf install git` 等 | [git-scm.com/install](https://git-scm.com/install/) |
| python3 | macOS：命令行工具自带，或 python.org 的安装包、`brew install python`；Linux：多数发行版自带 | [docs.python.org/3/using](https://docs.python.org/3/using/mac.html) |
| Node（22+） | `brew install node`，或官网安装包；用 nvm / fnm / mise 管理的也认。install 替你装时用 nvm：`curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh \| PROFILE=/dev/null bash`，再 `nvm install 22` | [nodejs.org](https://nodejs.org/)、[github.com/nvm-sh/nvm](https://github.com/nvm-sh/nvm) |

装完以后可以用 `./install.sh --check`（或 `python3 install.py --check`）一次看清缺什么（见[检查、更新、卸载](#检查更新卸载)）。

## 最短路径

默认 clone 到 `~/.drover/src`（drover 自己的目录 `~/.drover`，配置和任务记录也放在这里；隐藏目录，不占你的代码目录；想放别处也行，换掉下面的路径即可）。默认的 link 模式会把 `~/.drover/pkg` 软链到这份 clone，装完别随手挪、别删。然后一条命令：

```bash
git clone https://github.com/fulanto/drover.git ~/.drover/src
cd ~/.drover/src
./install.sh --dry-run   # 先看计划：每一项的路径、现状、要做什么、会不会备份
./install.sh             # 正式安装
./install.sh --check     # 检查安装状态和依赖
```

**主工作台怎么定**（`--workspace`，含义见[主工作台和仓库目录](#主工作台和仓库目录--workspace----repo-dir)）：

- 推荐用你放代码仓库的那个上级目录：仓库都在 `~/code/xxx`，主工作台就是 `~/code`。在这里开主会话，输入框的 `@` 能直接补全仓库名；路由规则也只覆盖这个目录。
- **不能是家目录**（或它的上级）：路由规则会进到家目录下所有的 claude / codex 会话，权限设置还会落进 `~/.claude/`。install 会直接拒绝（退出码 2）。
- 第一次安装（还没有 `~/.drover/config.json`）又没传 `--workspace` 时，终端里会先问一句，**没有默认值**，要自己输入。dry-run 不写配置，所以 dry-run 和正式安装各问一次。
- 不想被问，就每条都带上同一个 `--workspace`，比如 `./install.sh --dry-run --workspace ~/code`。
- 不在交互终端里跑（agent 的 shell、管道、CI、`--json`）时不会问：直接退出码 2，提示你用 `--workspace` 指定。
- 已经有 `config.json` 时沿用里面的 workspace，不问。

`./install.sh` 是一层很薄的入口：

1. 检查系统：macOS 往下走；Linux 提示一句"能装，但没有实测"再往下走；其他（比如 Windows）停下，说明暂不支持、可以在 WSL2 的 Ubuntu 里试（见[依赖](#依赖)）。
2. 检查 `git`、`python3`（3.9 及以上）能不能用。缺了就打印上表里的装法，**停下、不替你装**（要管理员权限或弹系统对话框）：macOS 上点一下命令行工具的安装框（`xcode-select --install`）两样就都有了；Linux 上照它给的 sudo 命令自己装。装好后重跑。macOS 上没装命令行工具时，`/usr/bin/git`、`/usr/bin/python3` 只是占位程序，install.sh 会真的跑一下来判断，不会被骗过去。
3. 都在就执行 `python3 install.py <你给的参数>`，参数原样透传。所以下面所有 `python3 install.py …` 的写法都可以换成 `./install.sh …`。

**缺依赖时一次装齐**：往 `~/.drover` 以外装的几样——herdr、你选的宿主（`--host`）缺的那个命令行（claude / codex）、接飞书（`--oncall`）时一个都没有的 Node——放在计划的最前面：装依赖的目录（`~/.local/bin`）不在 PATH 里时，先把它加进 shell 配置，再按 herdr → claude / codex → Node 的顺序装。都用官方安装脚本、都装在你的家目录、都不用 sudo；dry-run 里每一样写清要跑的命令、装到哪、改不改 shell 配置。接着装 drover 自己的各项，最后一项是安装记录。验收工具（它们要的 uv、Python → crapper、dryer）放在整个计划的最后：最慢、装不上也只是警告，放最后，被打断时 drover 自己的各项已经装好。

| 怎么跑的 | 会怎样 |
|---|---|
| 在终端里直接跑 `./install.sh` | 正式安装前列出这次要装的几样，一句 `现在装吗？[Y/n]` 问完：回车或 `y` 全装，`n` 一样都不装、只给命令 |
| 加 `--install-deps` | 不问，直接装（agent 帮你装时，你看过 dry-run 说"装"，它就加这个） |
| 非交互（agent 的 shell、管道、CI、`--json`），没加 `--install-deps` | 不装，计划里给出命令；缺 herdr 时退出码 1 |
| `--dry-run` | 只列出会执行的命令，不装 |

| 依赖 | 装到哪 | 改不改 shell 配置 | 没装上（或你没让装）时 |
|---|---|---|---|
| herdr | `~/.local/bin/herdr`（设了 `HERDR_INSTALL_DIR` 就装到那里） | 安装脚本本身不改；目录不在 PATH 里时，install 会在你同意后往 shell 启动文件加一段带标记的 PATH（见表下，`--uninstall` 会删掉） | 必需项：这一项失败（退出码 1），用到 herdr 的几项（herdr 技能、状态 hook）跟着失败，其余照装 |
| Claude Code | `~/.local/bin/claude`（程序在 `~/.local/share/claude`） | 同上 | 只提示，drover 的入口照样装，退出码不变；装好它之后不用重跑 install |
| Codex CLI | `~/.local/bin/codex`（程序在 `~/.codex/packages/standalone`） | 同上。它的安装脚本看自己进程的 PATH 里有没有 `~/.local/bin`，没有就往 profile 末尾加它自己那段（关不掉）；install 先加了 PATH、跑它时目录已在 PATH 里，它就不加了 | 同上，只提示 |
| Node 22（nvm） | `~/.nvm` | 不改（`PROFILE=/dev/null`）；飞书插件自己会去 `~/.nvm` 找 Node，终端里要用 node 时自己 `. ~/.nvm/nvm.sh` | Node 这一项和插件那一项失败（和以前没有 Node 时一样），其余照装 |

**PATH 也一起弄好**：herdr、claude、codex 的官方脚本都装到 `~/.local/bin`，全新的 Mac 上这个目录不在 PATH 里，新开的终端里 `herdr` 找不到，herdr 的 pane 里 `claude` 也找不到（drover 在 Herdr 里也是按名字调它们）。所以要往这个目录里装东西、或者里面已经有它们但不在 PATH 里时，计划的第一项是"把装依赖的目录加进 PATH"，和上面几样在同一次同意里：

- 往 shell 启动文件末尾加一段带 `# >>> drover PATH >>>` / `# <<< drover PATH <<<` 标记的块，块里先判断目录已在 PATH 里就不加（同一个文件 source 两次也不会叠加）；改之前备份；重跑 install 不重复写；dry-run 写清改哪个文件、加的是哪几行。
- 写哪个文件，要让"新开的终端"和"herdr 的 pane"两边都读得到：herdr 的 pane 用 `$SHELL`，macOS 上起 login shell、Linux 上起非 login 的交互 shell（herdr 的 `terminal.shell_mode` 默认 `auto`）；macOS 的终端 App 也起 login shell。所以 zsh 写 `~/.zshrc`（设了 `ZDOTDIR` 就是 `$ZDOTDIR/.zshrc`；zsh 的交互 shell 不管 login 与否都读它）；bash 在 macOS 上写 `~/.bash_profile`（已有 `~/.bash_login` 或 `~/.profile` 时写那个，login shell 只读其中第一个），在 Linux 上写 `~/.bashrc`；别的 shell（fish 等）只提示，你自己加。
- 同一次运行里，后面的安装命令 PATH 里也带上这个目录。
- 加好了，汇总的"下一步"第 1 条就是"开一个新终端（让 PATH 生效），运行 herdr"。没让加的（答了 n、非交互没带 `--install-deps`、`drover update`）只提示：照提示把 `export PATH="$HOME/.local/bin:$PATH"` 加进 shell 配置，再开新终端。
- `--uninstall` 只删这一段（先备份，文件其余内容不动）。

其余：

- 装完在同一次运行里接着生成 herdr 技能、装 agent 状态 hook、装飞书插件，不用重跑。PATH 没刷新也找得到刚装的 herdr。
- 已经装了的跳过（PATH 上有，或者在官方安装目录里，只是不在 PATH 上）；不想用脚本装，也可以先自己 `brew install herdr` 之类，再跑 `./install.sh`。
- 登录要你自己来：claude 第一次运行时按浏览器提示登录；codex 先 `codex login`。
- 本机有 Node 但低于 18：不替你换，只提示升级（`brew install node` 或 nodejs.org）。
- `drover update` 重跑 install 时不装这些（非交互、不带 `--install-deps`），缺了只在结果里提示。
- `--uninstall` 不删这几样（你也可能单独用它们；删 herdr 还会断掉正在跑的会话），只列出来、给出手动删法（见[检查、更新、卸载](#检查更新卸载)）。
- 测试或内网镜像可以用环境变量换掉安装命令：`DROVER_HERDR_INSTALLER`、`DROVER_CLAUDE_INSTALLER`、`DROVER_CODEX_INSTALLER`、`DROVER_NODE_INSTALLER`（照样要你同意或带 `--install-deps`）。

**两个宿主都没检测到、又没传 `--host` 时**：退出码 2，提示你用 `--host claude|codex|both` 说要哪个（install 会把它一起装上），也列出两个的官方安装命令。

**验收工具**：install 默认还会装两个工具 crapper、dryer 到 `~/.drover/tools`（本机有 Python 3.11+ 时每个约 18MB、十几秒；没有时还会下一份 drover 自己的 Python 和 uv，全新环境实测共 137MB、114 秒，随网速变），要从 GitHub clone、从 PyPI 装依赖，要 Python 3.11+（本机没有时自动用 uv 装一份进同一个目录）。装不上（没网、pip 失败）只记警告，不影响 drover 本身，也不改 install 的退出码；不想要就在 `~/.drover/config.json` 写 `"quality_tools": false`。详见[验收工具](quality-tools.md)。

**装完之后**，汇总的"下一步"按编号写好了怎么开始用（和[开主会话](usage.md#开主会话)一样）：开一个新终端运行 herdr（install 刚把 `~/.local/bin` 加进 shell 配置时，新终端里才生效；没让加的，给出 `~/.local/bin/herdr` 的完整路径和手动加 PATH 的写法）→ 在自动开好的 workspace 里点一下 pane、`cd` 到主工作台 → 启动 `claude`（第一次按浏览器提示登录；Codex 先 `codex login`，再 `codex -s danger-full-access`）→ 把信任框、MCP 弹窗点掉 → 离开时 `ctrl+b` 再 `q`。drover 只能在 Herdr 的 pane 里用。

**配飞书**（选装）：飞书 oncall 默认不装。要的话安装时加 `--oncall`（要 Node 18+，见[飞书 oncall](oncall.md)；记进 `config.json`，以后重跑、`drover update` 都照旧）；以前已经装上的（安装记录里有、或者 herdr 里装着插件）不用加，重跑 install、`drover update` 照旧保留、照旧更新。没装又没选过的，汇总的"下一步"里会提一句可以加 `--oncall`。带 `--oncall` 在终端里直接跑 `./install.sh` 的，汇总之后会问一句"现在用飞书扫码配对吗？[Y/n]"，回车就是配：终端里打印配对链接（装了 `qrencode` 还会画二维码），Mac 桌面也会弹二维码，用手机飞书扫码、点确认，配上后自动发一条测试消息（见[飞书 oncall](oncall.md)）。已经配对过的只提一句"飞书已配对"。不在交互终端里跑（agent 帮装、管道、`--json`）时不问，"下一步"里提示 `~/.drover/bin/drover oncall pair`。

完整参数：

```
python3 install.py [--host claude|codex|both] [--mode link|copy] [--workspace DIR] [--repo-dir DIR]... [--profile NAME|DIR] [--oncall|--no-oncall] [--install-deps] [--dry-run] [--check] [--uninstall] [--json]
```

- `--check` 和 `--uninstall` 二选一；`--dry-run` 可以和安装、`--uninstall` 一起用。
- `--oncall`：接飞书 oncall（选装，见[飞书 oncall](oncall.md)），记在 `config.json` 里，以后重跑也照旧；`--no-oncall` 不接，同样记住（已经装上的插件不卸）。
- `--install-deps`：缺的 herdr、选定宿主的 claude / codex 命令、接飞书时的 Node 不问、直接装，只对安装有效（不带时终端里一句 [Y/n] 问完，非交互只给命令）。
- `--json` 输出一个 JSON 对象、不打印文字，给脚本和测试用。
- `python3 install.py --help` 有每个参数和安装布局的完整说明。

## 选宿主：`--host`

不传时按本机装了什么自动判断：

- PATH 里有 `claude` 命令，或者 `~/.claude` 存在 → 装 Claude Code 那一套；
- PATH 里有 `codex` 命令，或者 `$CODEX_HOME`（默认 `~/.codex`）存在 → 装 Codex 那一套；
- 都满足就都装；都没检测到时退出码 2，提示你用 `--host` 指定。

也可以指定：

```bash
python3 install.py --host claude   # 只装 Claude Code 那一套
python3 install.py --host codex    # 只装 Codex 那一套
python3 install.py --host both     # 两边都装：主会话、编排者、worker 可以混用
```

## 选安装方式：`--mode link|copy`

| | `link`（默认） | `copy` |
|---|---|---|
| 怎么装 | `~/.drover/pkg` 软链到你的 clone | 把包复制到 `~/.drover/pkg` |
| 更新 | `~/.drover/bin/drover update` | `~/.drover/bin/drover update` |
| clone 能不能删、能不能挪 | 不能，软链会断 | 可以 |
| 适合谁 | 大多数人；想改流程、给 drover 提改动的人 | 不想保留 clone 的人 |

```bash
python3 install.py --mode copy
```

- 副本排除 `.git`、`__pycache__` 等，根目录放一个 `.drover-copy` 标记文件，卸载时靠它认出这是 install 复制的；里面还记着复制自哪个 clone（`source`）、clone 当时的提交（`commit`），副本没有 `.git`，drover 的版本号（看板第一行、看板换新代码）读的是这个 `commit`。
- link 和 copy 可以随时互相切换：重跑时带上新的 `--mode`，切换不留备份。
- **copy 模式重跑 install 时每次都要带 `--mode copy`**。不带就按默认的 link 装，软链到你这次运行的那份 clone。用 `drover update` 更新不用管，它按原来的模式重跑。

## 主工作台和仓库目录：`--workspace` / `--repo-dir`

- `--workspace DIR`：**主工作台**，也就是你平时开主会话的目录，编排者的工作目录也是它。它本身不必是 git 仓库。没有默认值：已有 `config.json` 时沿用里面的；第一次安装没传时，终端里会问，非交互时退出码 2、要你用 `--workspace` 指定（见[最短路径](#最短路径)）。
  - 推荐用放代码仓库的那个上级目录（仓库在 `~/code/xxx` 就用 `~/code`）：在这里开主会话，输入框的 `@` 补全出来的是相对它的路径，派发时能直接 `@仓库名`；路由规则只覆盖这个目录，在具体仓库里开的会话虽然会往上读到它，但区块里写明了要忽略。
  - 不能是家目录或它的上级（含 `/`）：Claude Code 会往上找 `CLAUDE.md`、Codex 会加载 `AGENTS.md`，路由规则会进到家目录下所有会话；`.claude/settings.local.json` 也会落进 `~/.claude/`。install 直接拒绝（退出码 2），dry-run 也一样。
  - 仓库分散在几个目录时：仓库最多的那个当主工作台，其余的用 `--repo-dir` 补进去（照样能按名字派发，只是输入框的 `@` 补全不出来）。
- `--repo-dir DIR`：按名字找仓库的目录，可以写多个，按顺序找（你说 `@order-service` 时，依次看 `<目录>/order-service`）。默认就是 workspace。

```bash
# 主工作台是 ~/work，仓库分散在 ~/work/company 和 ~/work 下
python3 install.py --workspace ~/work --repo-dir ~/work/company --repo-dir ~/work
```

上面这条写进 `config.json` 就是 workspace=`~/work`，repo_dirs 两项：`~/work/company` 在前，`~/work` 在后（[配置](configuration.md)的示例就是它）。团队有自己的 profile 时再加 `--profile <名字或目录>`（可选，见[团队 profile 和 playbook](configuration.md#团队-profile-和-playbook)）。

已经有配置（`~/.drover/config.json`）时，重跑 install 不会覆盖它。显式传了 `--workspace` / `--repo-dir` / `--profile` 时，只改传了的那个键：比如只传 `--repo-dir`，就只改 `repo_dirs`，其他键保留。改之前先备份一份。

## install 会改哪些地方

先跑 `--dry-run` 看计划，下面是安装布局（具体规则以 dry-run 的输出为准）：

**两边共用**

- 缺的外部依赖，你同意（或带了 `--install-deps`）时用官方安装脚本装：herdr 到 `~/.local/bin/herdr`，你选的宿主缺的那个命令行到 `~/.local/bin/claude` 或 `~/.local/bin/codex`，接飞书又一个 node 都没有时 nvm 和 Node 22 到 `~/.nvm`（见[最短路径](#最短路径)）。这几样是 install 往 `~/.drover` 以外装的全部东西；都在家目录、都不用 sudo。目录不在 PATH 里时，同一次同意里还会往 shell 启动文件（zsh：`~/.zshrc`；bash：macOS 是 `~/.bash_profile`，Linux 是 `~/.bashrc`）末尾加一段带 `# >>> drover PATH >>>` 标记的 PATH（改之前备份，见[最短路径](#最短路径)）。装过哪几样、PATH 加在哪个文件都记在安装记录里；`--uninstall` 不删这几样、只列出来，PATH 那一段会删掉
- `~/.drover/tools/`：验收工具 crapper、dryer（钉提交，各自一个 `.venv`），本机没有 Python 3.11+ 时还有 uv 装的 `python/`（没有 uv 时还有 drover 私有的 `bin/uv`），第一次分析某种语言时下载的 tree-sitter 语法包 `grammars/`。装不上只记警告（见[验收工具](quality-tools.md)）；`"quality_tools": false` 时不装
- `~/.drover/pkg` → 包根目录（link：软链；copy：副本）
- `~/.drover/bin/drover` → `~/.drover/pkg/bin/drover`（所有文档和权限规则里写的都是这个路径）
- `~/.drover/config.json`：`workspace`、`repo_dirs`，传了 `--profile` 时还有 `profile`，传了 `--oncall` / `--no-oncall` 时还有 `oncall: true` / `false`（见[配置](configuration.md)）
- `~/.drover/drover-install.json`：安装记录，记着装了哪些宿主、新建了哪些文件、加了哪些权限规则和 hook、做了哪些备份，以及飞书 oncall 插件是不是 drover 装的、`drover.env` 在哪。`--uninstall` 按它撤；`--check` 不给 `--host` 时按它查
- `<workspace>/AGENTS.md` 里一段带标记的区块：

  ```
  <!-- drover:routing:begin -->
  …（由包里的 flow/routing.md 渲染：主会话的路由规则）
  <!-- drover:routing:end -->
  ```

  Claude Code 通过 `CLAUDE.md` 里的 `@AGENTS.md` 读它，Codex 直接加载它。分三种情况：
  - 文件不存在：新建，里面只有这个区块。
  - 已有区块：整段替换，**区块外你自己写的内容原样保留**。
  - 有内容但没有标记：区块追加到末尾，并提示"已有内容可能和路由规则重复，请检查"。

  > 已经有 `AGENTS.md` 的话，建议装之前先在里面放一对空标记，位置你自己挑：
  >
  > ```
  > <!-- drover:routing:begin -->
  > <!-- drover:routing:end -->
  > ```
  >
  > install 就会把区块填进这里（整段替换），不再追加到末尾。原来那份里和路由规则重复的通用内容，可以趁这次删掉，只留你个人的部分。

- 飞书 oncall（drover 内置的 herdr-oncall 插件，见[飞书 oncall](oncall.md)；选装：传了 `--oncall`、或者以前已经接上的（安装记录里有、或者 herdr 里装着插件）才做这几项，其余跳过）：
  - 查 Node：一个 node 都没有时用 nvm 装 Node 22（和上面的外部依赖一起问你；没让装、没装上时这一项失败）；有但低于 18 只提示升级（这一项失败）；18～21 提示收不到飞书回复；
  - herdr 插件 `com.codreamer.herdr.oncall`：没装时执行 `herdr plugin install fulanto/herdr-oncall --ref stable --yes`（从 GitHub 装，之后插件每几个小时自己跟 `stable` 更新）；GitHub 装的低于 0.7.0 时执行 `herdr plugin update com.codreamer.herdr.oncall --yes`；你自己本地链接的（开发机）不动，版本低时提示去那个目录 `git pull`；在 herdr 里被禁用、或者插件自己的开关关着的，不替你打开，只提示；
  - 插件配置目录（`herdr plugin config-dir com.codreamer.herdr.oncall` 给的目录）里的 `drover.env`：drover 给插件的几项配置（主会话叫 `drover`、编排者名字以 `orch-` 开头、编排者 / worker 卡多久才推、不引用的飞书消息送主会话、主会话的回答只推飞书提问的回答和以 `【转达】` 开头的转达、飞书发来的图片存到 `~/.drover/oncall-images`），每次整份覆盖。**同目录的 `.env`（你的飞书配对、密钥）drover 不读不写**；要改某一项，写进 `.env`，它优先；
  - 这一项失败（访问不了 GitHub、没有 Node、herdr 没在运行）只记这一项失败、写明原因，其余照装，退出码 1（和没装 herdr 时一样）。

**Claude Code（`--host` 含 claude）**

- `~/.claude/commands/drover.md` → `~/.drover/pkg/flow/dispatch.md`（`/drover` 命令，编排者用的流程主干；拆出去的 `integrate.md` 等几份在同一目录，编排者走到那一步按路径读）
- `~/.claude/skills/herdr/SKILL.md`：没有时用 `herdr --skill` 生成
- 权限规则追加进 `~/.claude/settings.json` 的 `permissions.allow`（这里的规则对本机所有 Claude Code 会话生效，包括 worker）：drover CLI 只放行 `heavy`（`Bash(~/.drover/bin/drover heavy:*)`：worker 只用它，它只执行构建 / 测试工具），`~/.drover` 和 `~/.herdr/worktrees` 的读写（`Read(~/.drover/**)`、`Edit(~/.drover/**)` 等），`herdr agent` / `pane` / `worktree` / `workspace list` / `tab list` / `status`。以前的版本在这里放行整个 drover CLI（`Bash(~/.drover/bin/drover:*)`），worker 跑 `accept --verify "<任意命令>"`、`push`、`cleanup` 也不弹框；重跑 install（自动更新也会跑）时把它从这里删掉、挪到主工作台
- `~/.claude/settings.json` 的 `hooks.PreToolUse` 加一项 cd+git 拦截 hook（matcher `Bash`，跑装好的包里的 `~/.drover/pkg/hooks/cd_git_guard.py`，timeout 5 秒）：拒绝同一条命令里既有 cd 又有 git 的命令（不分先后：`cd <目录> && git …`、`git … && cd <目录>` 都算，和 Claude Code 内置检查的范围一样），把 `git -C <目录> …` 的改法（`cd <目录> && git …` 这种形状直接给出整条命令）回给 agent，让它自己改写重试。为什么要它：这种写法会触发 Claude Code 内置的安全确认，这个确认不走白名单、也没有 "don't ask again"，每条都要人批；编排者和 worker 派出去的子代理（Explore 等）看不到 CLAUDE.md 和任务书里"用 `git -C`"的规则，最爱这么写。hook 对子代理也生效，而且在内置确认之前执行。你已有的其他 hook（比如 herdr 的 `SessionStart`）、其他 `PreToolUse` 项原样保留；hook 自己出任何问题都放行，不会挡住正常命令
- `~/.claude/settings.json` 的 `hooks.SessionStart` 加一项主会话登记 hook（不设 matcher，跑 `~/.drover/pkg/bin/drover main-register --hook`，timeout 15 秒）：每次会话开始（新开、`/clear`、resume），把主会话在 herdr 里改名为 `drover`，飞书 oncall 靠这个名字认主会话。为什么每次都跑：herdr 的名字跟着 pane 里当前的 agent 走，`/clear`、退出重开都会被清掉。不是主会话的（工作目录不是主工作台、在编排者的 `orch` workspace 里、没在 herdr 里）直接跳过；它不往会话里输出任何东西，出错也一律放过
- `~/.claude/settings.json` 的 `hooks.UserPromptSubmit` 加一项主会话读新规则 hook（不设 matcher，跑 `~/.drover/pkg/bin/drover rules --hook`，timeout 10 秒）：[自动更新](auto-update.md)改了 `AGENTS.md` 里的 drover 规则后，你的下一条消息提交时把规则的改动一起送进主会话，不用 `/clear`。本机每个 Claude Code 会话的每条消息都会跑它：不是主会话（`session_id` 不是 `~/.drover/main-session.json` 记的）、规则没变时什么都不输出，常见路径不调 herdr、不跑 git；出错也一律放过，退出码恒为 0，不会拦你的消息
- 权限规则追加进 `<workspace>/.claude/settings.local.json`：整个 drover CLI（`Bash(~/.drover/bin/drover:*)`：主会话和编排者都在主工作台里起，拿得到这条；worker 在 worktree 里，拿不到）、`Skill(drover)`、`Skill(herdr)`、`Bash(herdr workspace *)`、`Bash(herdr tab *)`
- `<workspace>/CLAUDE.md` 里要有 `@AGENTS.md` 这一行（文件没有就新建，有但缺这一行就追加）
- `herdr integration install claude`：装 agent 状态 hook，Herdr 靠它判断 agent 是 working、idle 还是 blocked。已经是 current 时不执行

**Codex（`--host` 含 codex；设了 `CODEX_HOME` 时用它代替 `~/.codex`）**

- `~/.codex/skills/drover` → `~/.drover/pkg/codex/skills/drover`（`$drover` 技能，入口壳，让 codex 去读同一份流程正文）
- `~/.codex/skills/herdr`（`$herdr` 技能）：已有就不动；没有时，有 `~/.claude/skills/herdr` 就软链过去，否则用 `herdr --skill` 写一份
- `herdr integration install codex`：已经是 current 时不执行
- `~/.codex/config.toml` 的 `sandbox_mode` 不是 `danger-full-access` 时**只打印提示，不改**（原因见[Codex 注意事项](usage.md#codex-注意事项)）
- 不装主会话登记 hook 和读新规则 hook：codex 的 `SessionStart` hook 在第一条消息时才跑，新加的 hook 还要你手动信任，信任之前所有 codex 会话（包括 drover 起的 codex 编排者、worker）启动都会卡在 "Hooks need review" 上。Codex 主会话按路由规则在第一条消息时跑 `drover main-register` 登记，每条消息先看一眼 `board --json` 的 `drover` 字段（新规则怎么用上见[自动更新](auto-update.md)）

**备份和幂等**

- 要替换的位置原来是实体文件 / 目录（或者指向别处的软链），而且内容和要装的不同：先备份成 `<原名>.bak-drover-<YYYYmmdd-HHMMSS>` 再换。内容相同的直接换成软链，不备份。
- 改 settings、`CLAUDE.md`、往没有标记的 `AGENTS.md` 追加区块之前，先复制一份备份。替换已有区块不备份，区块归 drover 管。
- Codex 技能目录的备份**不放在原地**，而是放到 `~/.drover/backups/codex-skills/`。Codex 会递归扫描 skills 目录，备份放在那里会多出一个同名的 drover 技能。
- 权限规则只追加缺的，不删、不重排你已有的规则，JSON 里的其他键原样保留（唯一会删的是 drover 以前加进全局的 `Bash(~/.drover/bin/drover:*)`，见上）。cd+git 拦截 hook、主会话登记 hook、读新规则 hook 同理：只追加这一项，脚本路径变了就更新那一项。同一个文件（`~/.claude/settings.json` 的规则和 hook）一次只备份一份。
- 已经装对的项跳过：重跑 install 是幂等的，不会产生新备份，也不会重复加规则。

## 检查、更新、卸载

```bash
python3 install.py --check
python3 ~/.drover/pkg/install.py --check    # 装好以后在哪都能这样跑
```

- 逐项列出装没装对（`✓` 对、`!` 提示、`✗` 没装对），以及依赖齐不齐。缺必需依赖时，那一行下面给出官方装法（`装法：…`）；herdr 装了但不在 PATH 里也算缺，会告诉你把哪个目录加进 PATH。
- 不给 `--host` 时，按安装记录里的宿主查。
- 必需依赖缺失，或者有项没装对，退出码 1。飞书 oncall 这几项：没有 Node、插件没装、`drover.env` 不在算没装对（没接飞书的不查，不算失败）。
- 下面几种只算提示，不影响退出码：路由区块过期（和当前的 `flow/routing.md` 渲染结果不同）、copy 副本过期、codex 的 `sandbox_mode` 不是 `danger-full-access`、Node 低于 22、插件版本低于 0.7.0、插件在 herdr 里被禁用或自己的开关关着、`drover.env` 内容过期、验收工具没装 / 版本不对 / `.venv` 坏了 / 没有 Python 3.11+。

**更新**

```bash
~/.drover/bin/drover update             # 装新版本（默认只装发布的版本，见 docs/auto-update.md"更新通道"）、按原来的模式重跑 install、检查，最后告诉你下一步
~/.drover/bin/drover update --dry-run   # 只看有没有新版本：会 fetch，但不快进、不跑 install、不写文件
```

link、copy 模式都是这一条。在主会话里说"更新 drover"也行，它替你跑，再把结果和下一步转告你。

- **找哪份**：按安装记录（`~/.drover/drover-install.json`）。link 模式是 `~/.drover/pkg` 指向的那份 clone，copy 模式是安装时那份 clone；宿主也按记录，不会因为本机有 `~/.codex` 就多装一套。
- **装哪一版、只快进**：按更新通道（[自动更新](auto-update.md)）定目标：默认的 `release` 是上游（一般是 `origin/main`）历史上最新的版本 tag，`main` 是上游最新的提交。fetch 上游（`release` 顺带拉版本 tag）再快进到目标，不硬覆盖；本地比目标新（有没推送的提交，或者以前跟过 `main`）照常往下、不回退，提一句。这几种什么都不改、不重跑 install，说明原因、退出码 1：有已跟踪的本地改动又落后、和目标分叉、merge / rebase 做到一半、detached 或没有上游、上游上还没有发布的版本（`release`）、连不上远端、`update_channel` 写了不认识的值。
- **重跑 install、再检查**：每次都按原来的 `--mode` / `--host` 重跑（已经装对的项跳过；不碰飞书配对的 `.env`、`AGENTS.md` 区块外的内容、`settings.json` 里已有的规则），以前只 `git pull`、没重跑 install 留下的过期路由区块顺带刷新；然后跑一次 `--check`。
- **最后几行中文**：从哪个版本更新到哪个版本（写版本号：正好在版本 tag 上是 tag，tag 之后 N 个提交是 `vX.Y.Z+N`，历史上没有版本 tag 时是 7 位 sha，比如 `drover 已更新：v0.1.0→v0.2.0（3 个新提交）`）、几个新提交和标题、install 改了哪几项、检查结果和下一步。路由区块这次改了 → 主会话跑 `drover rules` 读入改动就生效，不用 `/clear`（[自动更新](auto-update.md)；也可以 `/clear`，Codex 用 `/new`）；`flow/dispatch.md` 变了 → 正在跑的编排者还按旧流程，新起的用新流程（拆出去的 `integrate.md` 等几份是走到那一步时现读的，正在跑的编排者读到的就是新版）；都不涉及就不用做别的。加 `--json` 输出一个 JSON 对象（`channel`、`release` 是这次的通道和目标 tag，`from_tag`、`to_tag` 是更新前后那个提交上的版本 tag）。`--dry-run` 报更新通道和目标、落后几个提交和标题、本地能不能快进、`flow/routing.md` / `install.py` / `flow/dispatch.md` 有没有变。
- **手动 `update` 和后台的自动更新（[自动更新](auto-update.md)）用同一把机器级的锁**：自动更新正在跑时，手动的先说一句再等它跑完，两个 install 不会同时改 `settings.json`、`AGENTS.md`。
- **copy 模式没留 clone 的**：`update` 报错并给出手动步骤，不替你 clone。重新 clone 一份到临时目录，在里面跑 `python3 install.py --mode copy`，跑完那份 clone 就可以删了。

**卸载**

```bash
python3 install.py --uninstall --dry-run    # 先看会撤掉什么
python3 install.py --uninstall              # 两个宿主都撤
python3 install.py --uninstall --host codex # 只撤 Codex 那一套
```

按安装记录 `drover-install.json` 撤，只撤 install 自己加的东西：

- **撤掉**：
  - 指向 drover 的软链（指向别处的不动）；
  - copy 模式的副本（有 `.drover-copy` 标记的才删）；
  - `AGENTS.md` 里的 drover 区块（安装记录里的每个主工作台都撤）；
  - install 新建的 herdr 技能和 `CLAUDE.md`（`CLAUDE.md` 只在内容没改过时删）；
  - 权限规则里 install 加的那几条（你原来就有的不删）；
  - install 加的 cd+git 拦截 hook、主会话登记 hook、读新规则 hook（只撤安装记录里的这几项，别的 hook 不动；`PreToolUse`、`SessionStart`、`UserPromptSubmit`、`hooks` 撤空了就去掉）；
  - 飞书 oncall 的 `drover.env`；插件本身只在安装记录里记着"是 drover 装的"时才 `herdr plugin uninstall`，你原来就装着的不动；
  - `~/.drover/tools` 整个目录（验收工具、它们用的 Python / uv、语法包）；
  - shell 配置里 drover 加的 PATH 那一段（按安装记录找文件，只删这一段，改之前备份；文件是 install 新建的、删完是空的就整个删）；
  - 安装记录本身。
- **保留**：`config.json`（重装时沿用，不要了可以手动删）、任务记录 `~/.drover/<slug>/`、worktree、herdr 的 agent 状态 hook、插件配置目录里的 `.env`（飞书配对还在，以后重装不用重新扫码）。
- **drover 替你装过的 herdr、claude / codex、nvm / Node 不删**：你也可能单独用它们，删 herdr 还会断掉正在跑的会话。卸载的输出里列出这几样（安装记录里记着哪些是 drover 装的）和手动删法，比如 herdr 是先 `herdr server stop` 再删 `~/.local/bin/herdr`，Claude Code 照官方步骤删 `~/.local/bin/claude` 和 `~/.local/share/claude`，nvm 删 `~/.nvm`。shell 配置里 drover 加的 PATH 那一段会删掉，还要在新终端里直接用它们，就自己把 `~/.local/bin` 加回 PATH。
- **带 `--host`** 时只撤那个宿主的项。另一个宿主还装着的话，共用的包、脚本入口、路由区块、飞书 oncall 都保留。
- `.bak-drover-*` 备份**不会自动还原**，只列出来让你自己决定（见[想回滚](troubleshooting.md#想回滚)）。
