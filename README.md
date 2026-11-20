# drover

## 让你的 agent 来安装

**如果你是 agent，用户让你安装 drover**：先完整读 [`docs/agent-install.md`](docs/agent-install.md)，按它一步步做。还没 clone 的话，先 clone 到 `mktemp -d` 建的临时目录里读，正式位置要问用户。不要直接跑 `./install.sh`：首次安装在非交互的 shell 里必须带 `--workspace`，这个目录要先问用户；安装还会改用户的 `~/.claude`、`~/.codex`、`~/.drover` 和主工作台。问完就停下等用户回答；dry-run 的输出原样给用户看，用户明确说装才装。

**如果你是人**：把下面这段话发给你正在用的 Claude Code 或 Codex：

```
帮我安装 drover：https://github.com/fulanto/drover 。先 clone 到临时目录里读说明，按仓库 README 开头给 agent 的说明来，问完问题先停下等我回答，安装前把演练结果原样给我看。
```

为什么要整段发：实测 Claude Code 只给地址也能按说明装好，Codex 不带后半句容易不等你确认就装，所以两个都建议整段发。它会先问你几个问题（drover 默认放在 `~/.drover/src`；以后在哪个文件夹打开 agent 提需求，会让你选），给你看演练结果，你确认后才装。想自己装，看[第 2 节](#2-安装)。

---

![drover](docs/images/01-cover.jpg)

drover 是一套多 agent 派发体系，跑在 [Herdr](https://herdr.dev) 上，Claude Code 和 Codex 都能用。名字取"赶牧群的人"：Herdr 管牧场，drover 负责把一群 agent 赶到该去的地方。drover 既是这套体系的名字，也是它的命令（Claude Code 的 `/drover`、Codex 的 `$drover`）和 CLI（`~/.drover/bin/drover`）的名字。

**它解决的问题：人是瓶颈。** 一个 agent 开一个终端的时候，人要盯着每个窗口：拆需求、开分支、贴 prompt、批弹窗、看报告、跑测试、合代码，agent 越多人越忙。drover 把这些活交给每个任务自己的编排者（orchestrator），人只剩三件事：

1. **提需求**：在主工作台的会话里直接说，不用写命令；
2. **看一页汇报**：每个任务一份不超过 40 行的 rollup；
3. **拍板**：只有要你决定的事（范围、方向、推送、删东西）才会来找你。

**离开电脑也不断线：内置飞书 oncall（选装）。** 安装时加 `--oncall` 就装上，装完扫一次码配对。之后只在主会话需要你时推到你的飞书：主会话的确认框、你在飞书里提问的那一轮的回答、以 `【转达】` 开头的转达（编排者等你决定 / 完成 / 受阻）；编排者和 worker 的确认框平时由上一层批，卡 5 / 10 分钟没人批才升级到你。反过来，在飞书里不引用、直接发消息，就是在跟主会话说话：派活、问进度、拍板、说"提交推送""收尾"都行。详见 [4.9](#49-离开电脑飞书-oncall内置)。

一共三层：**主会话**就是 drover 本身，负责路由、问答、审核、转达；**编排者**负责一个任务的全程编排：分析、拆条目、建 worktree、起 worker、追踪、按证据验收、集成和推送；**worker** 在各自的 worktree 里写代码。

```
你 ── 提需求 / 看一页汇报 / 拍板
 │
 ▼
主会话 = drover 本身（在主工作台开的 claude 或 codex）
 │   路由、问答、审核、转达：写 request.md、起编排者、读 rollup.md、审过再转
 ▼
编排者 orch-<名字>（一个任务一个，在 herdr 的 orch workspace 里占一个 tab）
 │   分析 → 拆条目 → 建 worktree → 起 worker → 追踪 → 按证据验收 → 写 rollup
 ├── worker 1（claude 或 codex，一个 pane） ── worktree，分支 task/<slug>-<条目1>
 ├── worker 2                               ── worktree，分支 task/<slug>-<条目2>
 └── worker N                               ── worktree，分支 task/<slug>-<条目N>
                    │
                    ▼  你说"提交推送"之后
   集成 worktree（每个仓库一个，分支 task/<slug>）：同步上游 → 整体检查 → 迁移 → 推送
```

它是一步步长出来的：一个 agent 一个终端 → 引入 Herdr 管多个 agent → Herdr 多 worktree 并行 → 把重复操作收进脚本（drover 的前身 dispatch：流程 + 脚本）→ 交给编排者（当时叫协调者），主会话只做路由 → Codex 也能当宿主 → 打包成独立的 drover 包，clone 下来就能装 → 离开电脑：内置飞书 oncall。每一步为什么这么改，见：

- [`docs/architecture.html`](docs/architecture.html)：技术架构与流程说明（配图）

文档里的插画在 `docs/images/`（等距科技风，11 张，`12-oncall.jpg` 是飞书 oncall），用相对路径引用，直接用浏览器打开 HTML 就能看。

## 目录

1. [依赖](#1-依赖)
2. [安装](#2-安装)
3. [配置](#3-配置)
4. [日常用法](#4-日常用法)
5. [子命令速查](#5-子命令速查)
6. [目录布局](#6-目录布局)
7. [Codex 注意事项](#7-codex-注意事项)
8. [排障](#8-排障)
9. [设计取舍与致谢](#9-设计取舍与致谢)
10. [自测](#10-自测)

## 1. 依赖

| | 工具 | 用途 | 没装时 |
|---|---|---|---|
| 必需 | `herdr` | 编排者和 worker 都起在 Herdr 的 pane 里，worktree 也由它建。**主会话必须在 Herdr 里打开**，否则 `drover preflight` 直接报错 | install 可以替你装（官方安装脚本，先问你；见 2.1） |
| 必需 | `git` | 每个条目一个 git worktree | `./install.sh` 给出装法后停下 |
| 必需 | `python3`（3.9+） | `bin/drover` 和 `install.py` 都只用标准库，不用装包 | `./install.sh` 给出装法后停下 |
| 必需（至少一个） | `claude` / `codex` | 主会话、编排者、worker 的宿主，两个都装可以混用 | 只给官方安装命令，不替你装 |
| 飞书 oncall 要（选装，`--oncall`） | `node`（22+） | 内置的飞书 oncall（herdr-oncall 插件）跑在 Node 上：18～21 能推送，但收不到你在飞书里的回复；没有或低于 18 插件跑不起来 | 接了飞书时 install 只检查不安装：不满足时插件这一项报失败、其余照装；装好 Node 再重跑，或者 `--no-oncall` 不接了 |
| 可选 | Xcode 命令行工具（macOS） | 飞书 oncall 的桌面面板（弹框提醒、扫码配对的二维码）装插件时现场编译，要用到它 | 没有时只是没有桌面面板，飞书照常 |
| 可选 | `code-review-graph` | 验收时建图：看改动的风险、找出没有测试覆盖的函数。没装时验收照常，只是少了图谱这一项 | 不管 |
| 可选 | `uv` | Python 仓库在 worktree 里装依赖（有 `uv.lock` 用 `uv sync --frozen`，否则复刻源仓库的 `.venv`） | 不管 |
| 可选 | `bun` / `pnpm` | JS 仓库按 lockfile 在 worktree 里装依赖（`yarn`、`npm` 的 lockfile 也认） | 不管 |

各自的官方安装方式（install 里用的就是这些命令）：

| 工具 | 官方命令 | 出处 |
|---|---|---|
| herdr | `curl -fsSL https://herdr.dev/install.sh \| sh`（装到 `~/.local/bin`，可用 `HERDR_INSTALL_DIR` 改；不用 sudo，不改 shell 配置）；或 `brew install herdr` | [herdr.dev/tui/docs/install](https://herdr.dev/tui/docs/install/) |
| Claude Code | `curl -fsSL https://claude.ai/install.sh \| bash`（官方推荐的原生安装）；或 `brew install --cask claude-code` | [code.claude.com/docs/en/setup](https://code.claude.com/docs/en/setup) |
| Codex CLI | `curl -fsSL https://chatgpt.com/codex/install.sh \| sh`；或 `npm install -g @openai/codex`、`brew install --cask codex` | [github.com/openai/codex](https://github.com/openai/codex) |
| git | macOS：`xcode-select --install`（Apple 命令行工具）或 `brew install git`；Linux：`apt-get install git` / `dnf install git` 等 | [git-scm.com/install](https://git-scm.com/install/) |
| python3 | macOS：命令行工具自带，或 python.org 的安装包、`brew install python`；Linux：多数发行版自带 | [docs.python.org/3/using](https://docs.python.org/3/using/mac.html) |
| Node（22+） | `brew install node`，或官网安装包；用 nvm / fnm / mise 管理的也认 | [nodejs.org](https://nodejs.org/) |

装完以后可以用 `./install.sh --check`（或 `python3 install.py --check`）一次看清缺什么（见下一节）。

## 2. 安装

### 2.1 最短路径

默认 clone 到 `~/.drover/src`（drover 自己的目录 `~/.drover`，配置和任务记录也放在这里；隐藏目录，不占你的代码目录；想放别处也行，换掉下面的路径即可）。默认的 link 模式会把 `~/.drover/pkg` 软链到这份 clone，装完别随手挪、别删。然后一条命令：

```bash
git clone https://github.com/fulanto/drover.git ~/.drover/src
cd ~/.drover/src
./install.sh --dry-run   # 先看计划：每一项的路径、现状、要做什么、会不会备份
./install.sh             # 正式安装
./install.sh --check     # 检查安装状态和依赖
```

**主工作台怎么定**（`--workspace`，含义见 2.4）：

- 推荐用你放代码仓库的那个上级目录：仓库都在 `~/code/xxx`，主工作台就是 `~/code`。在这里开主会话，输入框的 `@` 能直接补全仓库名；路由规则也只覆盖这个目录。
- **不能是家目录**（或它的上级）：路由规则会进到家目录下所有的 claude / codex 会话，权限设置还会落进 `~/.claude/`。install 会直接拒绝（退出码 2）。
- 第一次安装（还没有 `~/.drover/config.json`）又没传 `--workspace` 时，终端里会先问一句，**没有默认值**，要自己输入。dry-run 不写配置，所以 dry-run 和正式安装各问一次。
- 不想被问，就每条都带上同一个 `--workspace`，比如 `./install.sh --dry-run --workspace ~/code`。
- 不在交互终端里跑（agent 的 shell、管道、CI、`--json`）时不会问：直接退出码 2，提示你用 `--workspace` 指定。
- 已经有 `config.json` 时沿用里面的 workspace，不问。

`./install.sh` 是一层很薄的入口：

1. 检查系统是 macOS 或 Linux（Windows 请在 WSL 里装）。
2. 检查 `git`、`python3`（3.9 及以上）能不能用。缺了就打印上表里的装法，**停下、不替你装**（要管理员权限或弹系统对话框），装好后重跑。macOS 上没装命令行工具时，`/usr/bin/git`、`/usr/bin/python3` 只是占位程序，install.sh 会真的跑一下来判断，不会被骗过去。
3. 都在就执行 `python3 install.py <你给的参数>`，参数原样透传。所以下面所有 `python3 install.py …` 的写法都可以换成 `./install.sh …`。

**没装 herdr 时**，install.py 计划的第一项就是"安装 herdr"，用官方安装脚本 `curl -fsSL https://herdr.dev/install.sh | sh`：

| 怎么跑的 | 会怎样 |
|---|---|
| 在终端里直接跑 `./install.sh` | 先问一句 `现在执行它安装 herdr 吗？[Y/n]`，回车或 `y` 就装，`n` 不装 |
| 加 `--install-deps` | 不问，直接装 |
| 非交互（管道、CI、`--json`） | 不装，计划里给出命令，退出码 1 |
| `--dry-run` | 只列出会执行的命令，不装 |

- 装完在同一次运行里接着生成 herdr 技能、装 agent 状态 hook，不用重跑。官方脚本装到 `~/.local/bin/herdr`（设了 `HERDR_INSTALL_DIR` 就装到那里），PATH 没刷新也找得到。
- 这个目录不在 PATH 里时，汇总会提示把 `export PATH="$HOME/.local/bin:$PATH"` 加进 `~/.zshrc`（bash 用 `~/.bashrc`）再开新终端：drover CLI 是按名字调用 herdr 的，不在 PATH 里会报找不到。
- 不想用脚本装，也可以先自己 `brew install herdr`，再跑 `./install.sh`。
- 选了不装、或者装失败了：不需要 herdr 的项（软链、配置、权限规则、路由区块）照样装好，用到 herdr 的几项（herdr 技能、状态 hook）报失败。装好 herdr 后重跑一次就行，已经装对的项会跳过。

**没装 claude / codex 时**：install 不替你装，只给官方命令（上表）。两个都没检测到、又没传 `--host` 时，退出码 2，提示里列出两个的官方安装命令；传了 `--host`（比如先装好 Claude Code 这一套）但命令还没装时，只提示一句，drover 的入口照样装，之后装好命令不用重跑 install。

**装完之后**，汇总的"下一步"会提醒你：drover 只能在 Herdr 的 pane 里用，所以要在 Herdr 里开主会话：终端里运行 `herdr`，在它的 pane 里 `cd` 到主工作台，再启动 `claude`（或 `codex -s danger-full-access`），见第 4.1 节。

**配飞书**（选装）：飞书 oncall 默认不装。要的话安装时加 `--oncall`（要 Node 18+，见 4.9；记进 `config.json`，以后重跑、`drover update` 都照旧）；以前已经装上的（安装记录里有、或者 herdr 里装着插件）不用加，重跑 install、`drover update` 照旧保留、照旧更新。没装又没选过的，汇总的"下一步"里会提一句可以加 `--oncall`。带 `--oncall` 在终端里直接跑 `./install.sh` 的，汇总之后会问一句"现在用飞书扫码配对吗？[Y/n]"，回车就是配：终端里打印配对链接（装了 `qrencode` 还会画二维码），Mac 桌面也会弹二维码，用手机飞书扫码、点确认，配上后自动发一条测试消息（见 4.9）。已经配对过的只提一句"飞书已配对"。不在交互终端里跑（agent 帮装、管道、`--json`）时不问，"下一步"里提示 `~/.drover/bin/drover oncall pair`。

完整参数：

```
python3 install.py [--host claude|codex|both] [--mode link|copy] [--workspace DIR] [--repo-dir DIR]... [--profile NAME|DIR] [--oncall|--no-oncall] [--install-deps] [--dry-run] [--check] [--uninstall] [--json]
```

- `--check` 和 `--uninstall` 二选一；`--dry-run` 可以和安装、`--uninstall` 一起用。
- `--oncall`：接飞书 oncall（选装，见 4.9），记在 `config.json` 里，以后重跑也照旧；`--no-oncall` 不接，同样记住（已经装上的插件不卸）。
- `--install-deps`：缺 herdr 时不问、直接装，只对安装有效。
- `--json` 输出一个 JSON 对象、不打印文字，给脚本和测试用。
- `python3 install.py --help` 有每个参数和安装布局的完整说明。

### 2.2 选宿主：`--host`

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

### 2.3 选安装方式：`--mode link|copy`

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

### 2.4 主工作台和仓库目录：`--workspace` / `--repo-dir`

- `--workspace DIR`：**主工作台**，也就是你平时开主会话的目录，编排者的工作目录也是它。它本身不必是 git 仓库。没有默认值：已有 `config.json` 时沿用里面的；第一次安装没传时，终端里会问，非交互时退出码 2、要你用 `--workspace` 指定（见 2.1）。
  - 推荐用放代码仓库的那个上级目录（仓库在 `~/code/xxx` 就用 `~/code`）：在这里开主会话，输入框的 `@` 补全出来的是相对它的路径，派发时能直接 `@仓库名`；路由规则只覆盖这个目录，在具体仓库里开的会话虽然会往上读到它，但区块里写明了要忽略。
  - 不能是家目录或它的上级（含 `/`）：Claude Code 会往上找 `CLAUDE.md`、Codex 会加载 `AGENTS.md`，路由规则会进到家目录下所有会话；`.claude/settings.local.json` 也会落进 `~/.claude/`。install 直接拒绝（退出码 2），dry-run 也一样。
  - 仓库分散在几个目录时：仓库最多的那个当主工作台，其余的用 `--repo-dir` 补进去（照样能按名字派发，只是输入框的 `@` 补全不出来）。
- `--repo-dir DIR`：按名字找仓库的目录，可以写多个，按顺序找（你说 `@order-service` 时，依次看 `<目录>/order-service`）。默认就是 workspace。

```bash
# 主工作台是 ~/work，仓库分散在 ~/work/company 和 ~/work 下
python3 install.py --workspace ~/work --repo-dir ~/work/company --repo-dir ~/work
```

上面这条写进 `config.json` 就是 workspace=`~/work`，repo_dirs 两项：`~/work/company` 在前，`~/work` 在后（第 3 节的示例就是它）。团队有自己的 profile 时再加 `--profile <名字或目录>`（可选，见第 3.1 节）。

已经有配置（`~/.drover/config.json`）时，重跑 install 不会覆盖它。显式传了 `--workspace` / `--repo-dir` / `--profile` 时，只改传了的那个键：比如只传 `--repo-dir`，就只改 `repo_dirs`，其他键保留。改之前先备份一份。

### 2.5 install 会改哪些地方

先跑 `--dry-run` 看计划，下面是安装布局（具体规则以 dry-run 的输出为准）：

**两边共用**

- 没装 herdr、而且你同意（或带了 `--install-deps`）时：用官方安装脚本装到 `~/.local/bin/herdr`（见 2.1）。这是 install 唯一会替你装的依赖
- `~/.drover/pkg` → 包根目录（link：软链；copy：副本）
- `~/.drover/bin/drover` → `~/.drover/pkg/bin/drover`（所有文档和权限规则里写的都是这个路径）
- `~/.drover/config.json`：`workspace`、`repo_dirs`，传了 `--profile` 时还有 `profile`，传了 `--oncall` / `--no-oncall` 时还有 `oncall: true` / `false`（见第 3 节）
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

- 飞书 oncall（drover 内置的 herdr-oncall 插件，见 4.9；选装：传了 `--oncall`、或者以前已经接上的（安装记录里有、或者 herdr 里装着插件）才做这几项，其余跳过）：
  - 查 Node：只检查不安装（没有或低于 18 这一项失败；18～21 提示收不到飞书回复）；
  - herdr 插件 `com.codreamer.herdr.oncall`：没装时执行 `herdr plugin install fulanto/herdr-oncall --ref stable --yes`（从 GitHub 装，之后插件每几个小时自己跟 `stable` 更新）；GitHub 装的低于 0.7.0 时执行 `herdr plugin update com.codreamer.herdr.oncall --yes`；你自己本地链接的（开发机）不动，版本低时提示去那个目录 `git pull`；在 herdr 里被禁用、或者插件自己的开关关着的，不替你打开，只提示；
  - 插件配置目录（`herdr plugin config-dir com.codreamer.herdr.oncall` 给的目录）里的 `drover.env`：drover 给插件的几项配置（主会话叫 `drover`、编排者名字以 `orch-` 开头、编排者 / worker 卡多久才推、不引用的飞书消息送主会话、主会话的回答只推飞书提问的回答和以 `【转达】` 开头的转达、飞书发来的图片存到 `~/.drover/oncall-images`），每次整份覆盖。**同目录的 `.env`（你的飞书配对、密钥）drover 不读不写**；要改某一项，写进 `.env`，它优先；
  - 这一项失败（访问不了 GitHub、没有 Node、herdr 没在运行）只记这一项失败、写明原因，其余照装，退出码 1（和没装 herdr 时一样）。

**Claude Code（`--host` 含 claude）**

- `~/.claude/commands/drover.md` → `~/.drover/pkg/flow/dispatch.md`（`/drover` 命令，编排者用的流程正文）
- `~/.claude/skills/herdr/SKILL.md`：没有时用 `herdr --skill` 生成
- 权限规则追加进 `~/.claude/settings.json` 的 `permissions.allow`：drover CLI（`Bash(~/.drover/bin/drover:*)`），`~/.drover` 和 `~/.herdr/worktrees` 的读写（`Read(~/.drover/**)`、`Edit(~/.drover/**)` 等），`herdr agent` / `pane` / `worktree` / `workspace list` / `tab list` / `status`
- `~/.claude/settings.json` 的 `hooks.PreToolUse` 加一项 cd+git 拦截 hook（matcher `Bash`，跑装好的包里的 `~/.drover/pkg/hooks/cd_git_guard.py`，timeout 5 秒）：拒绝 `cd <目录> && git …` 这类"先 cd 再跑 git"的命令，把 `git -C <目录> …` 的改法（能改写时直接给出整条命令）回给 agent，让它自己改写重试。为什么要它：这种写法会触发 Claude Code 内置的安全确认，这个确认不走白名单、也没有 "don't ask again"，每条都要人批；编排者和 worker 派出去的子代理（Explore 等）看不到 CLAUDE.md 和任务书里"用 `git -C`"的规则，最爱这么写。hook 对子代理也生效，而且在内置确认之前执行。你已有的其他 hook（比如 herdr 的 `SessionStart`）、其他 `PreToolUse` 项原样保留；hook 自己出任何问题都放行，不会挡住正常命令
- `~/.claude/settings.json` 的 `hooks.SessionStart` 加一项主会话登记 hook（不设 matcher，跑 `~/.drover/pkg/bin/drover main-register --hook`，timeout 15 秒）：每次会话开始（新开、`/clear`、resume），把主会话在 herdr 里改名为 `drover`，飞书 oncall 靠这个名字认主会话。为什么每次都跑：herdr 的名字跟着 pane 里当前的 agent 走，`/clear`、退出重开都会被清掉。不是主会话的（工作目录不是主工作台、在编排者的 `orch` workspace 里、没在 herdr 里）直接跳过；它不往会话里输出任何东西，出错也一律放过
- `~/.claude/settings.json` 的 `hooks.UserPromptSubmit` 加一项主会话读新规则 hook（不设 matcher，跑 `~/.drover/pkg/bin/drover rules --hook`，timeout 10 秒）：自动更新（2.7）改了 `AGENTS.md` 里的 drover 规则后，你的下一条消息提交时把规则的改动一起送进主会话，不用 `/clear`。本机每个 Claude Code 会话的每条消息都会跑它：不是主会话（`session_id` 不是 `~/.drover/main-session.json` 记的）、规则没变时什么都不输出，常见路径不调 herdr、不跑 git；出错也一律放过，退出码恒为 0，不会拦你的消息
- 权限规则追加进 `<workspace>/.claude/settings.local.json`：`Skill(drover)`、`Skill(herdr)`、`Bash(herdr workspace *)`、`Bash(herdr tab *)`
- `<workspace>/CLAUDE.md` 里要有 `@AGENTS.md` 这一行（文件没有就新建，有但缺这一行就追加）
- `herdr integration install claude`：装 agent 状态 hook，Herdr 靠它判断 agent 是 working、idle 还是 blocked。已经是 current 时不执行

**Codex（`--host` 含 codex；设了 `CODEX_HOME` 时用它代替 `~/.codex`）**

- `~/.codex/skills/drover` → `~/.drover/pkg/codex/skills/drover`（`$drover` 技能，入口壳，让 codex 去读同一份流程正文）
- `~/.codex/skills/herdr`（`$herdr` 技能）：已有就不动；没有时，有 `~/.claude/skills/herdr` 就软链过去，否则用 `herdr --skill` 写一份
- `herdr integration install codex`：已经是 current 时不执行
- `~/.codex/config.toml` 的 `sandbox_mode` 不是 `danger-full-access` 时**只打印提示，不改**（原因见第 7 节）
- 不装主会话登记 hook 和读新规则 hook：codex 的 `SessionStart` hook 在第一条消息时才跑，新加的 hook 还要你手动信任，信任之前所有 codex 会话（包括 drover 起的 codex 编排者、worker）启动都会卡在 "Hooks need review" 上。Codex 主会话按路由规则在第一条消息时跑 `drover main-register` 登记，每条消息先看一眼 `board --json` 的 `drover` 字段（新规则怎么用上见 2.7）

**备份和幂等**

- 要替换的位置原来是实体文件 / 目录（或者指向别处的软链），而且内容和要装的不同：先备份成 `<原名>.bak-drover-<YYYYmmdd-HHMMSS>` 再换。内容相同的直接换成软链，不备份。
- 改 settings、`CLAUDE.md`、往没有标记的 `AGENTS.md` 追加区块之前，先复制一份备份。替换已有区块不备份，区块归 drover 管。
- Codex 技能目录的备份**不放在原地**，而是放到 `~/.drover/backups/codex-skills/`。Codex 会递归扫描 skills 目录，备份放在那里会多出一个同名的 drover 技能。
- 权限规则只追加缺的，不删、不重排你已有的规则，JSON 里的其他键原样保留。cd+git 拦截 hook、主会话登记 hook、读新规则 hook 同理：只追加这一项，脚本路径变了就更新那一项。同一个文件（`~/.claude/settings.json` 的规则和 hook）一次只备份一份。
- 已经装对的项跳过：重跑 install 是幂等的，不会产生新备份，也不会重复加规则。

### 2.6 检查、更新、卸载

```bash
python3 install.py --check
python3 ~/.drover/pkg/install.py --check    # 装好以后在哪都能这样跑
```

- 逐项列出装没装对（`✓` 对、`!` 提示、`✗` 没装对），以及依赖齐不齐。缺必需依赖时，那一行下面给出官方装法（`装法：…`）；herdr 装了但不在 PATH 里也算缺，会告诉你把哪个目录加进 PATH。
- 不给 `--host` 时，按安装记录里的宿主查。
- 必需依赖缺失，或者有项没装对，退出码 1。飞书 oncall 这几项：没有 Node、插件没装、`drover.env` 不在算没装对（没接飞书的不查，不算失败）。
- 下面几种只算提示，不影响退出码：路由区块过期（和当前的 `flow/routing.md` 渲染结果不同）、copy 副本过期、codex 的 `sandbox_mode` 不是 `danger-full-access`、Node 低于 22、插件版本低于 0.7.0、插件在 herdr 里被禁用或自己的开关关着、`drover.env` 内容过期。

**更新**

```bash
~/.drover/bin/drover update             # 装新版本（默认只装发布的版本，见 2.7"更新通道"）、按原来的模式重跑 install、检查，最后告诉你下一步
~/.drover/bin/drover update --dry-run   # 只看有没有新版本：会 fetch，但不快进、不跑 install、不写文件
```

link、copy 模式都是这一条。在主会话里说"更新 drover"也行，它替你跑，再把结果和下一步转告你。

- **找哪份**：按安装记录（`~/.drover/drover-install.json`）。link 模式是 `~/.drover/pkg` 指向的那份 clone，copy 模式是安装时那份 clone；宿主也按记录，不会因为本机有 `~/.codex` 就多装一套。
- **装哪一版、只快进**：按更新通道（2.7）定目标：默认的 `release` 是上游（一般是 `origin/main`）历史上最新的版本 tag，`main` 是上游最新的提交。fetch 上游（`release` 顺带拉版本 tag）再快进到目标，不硬覆盖；本地比目标新（有没推送的提交，或者以前跟过 `main`）照常往下、不回退，提一句。这几种什么都不改、不重跑 install，说明原因、退出码 1：有已跟踪的本地改动又落后、和目标分叉、merge / rebase 做到一半、detached 或没有上游、上游上还没有发布的版本（`release`）、连不上远端、`update_channel` 写了不认识的值。
- **重跑 install、再检查**：每次都按原来的 `--mode` / `--host` 重跑（已经装对的项跳过；不碰飞书配对的 `.env`、`AGENTS.md` 区块外的内容、`settings.json` 里已有的规则），以前只 `git pull`、没重跑 install 留下的过期路由区块顺带刷新；然后跑一次 `--check`。
- **最后几行中文**：更新到哪个提交（那个提交上有版本 tag 时写出来，比如 `drover 已更新：a1b2c3d→e4f5a6b（v0.2.0，3 个新提交）`）、几个新提交和标题、install 改了哪几项、检查结果和下一步。路由区块这次改了 → 主会话跑 `drover rules` 读入改动就生效，不用 `/clear`（2.7；也可以 `/clear`，Codex 用 `/new`）；`flow/dispatch.md` 变了 → 正在跑的编排者还按旧流程，新起的用新流程；都不涉及就不用做别的。加 `--json` 输出一个 JSON 对象（`channel`、`release` 是这次的通道和目标 tag，`from_tag`、`to_tag` 是更新前后那个提交上的版本 tag）。`--dry-run` 报更新通道和目标、落后几个提交和标题、本地能不能快进、`flow/routing.md` / `install.py` / `flow/dispatch.md` 有没有变。
- **手动 `update` 和后台的自动更新（2.7）用同一把机器级的锁**：自动更新正在跑时，手动的先说一句再等它跑完，两个 install 不会同时改 `settings.json`、`AGENTS.md`。
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
  - 安装记录本身。
- **保留**：`config.json`（重装时沿用，不要了可以手动删）、任务记录 `~/.drover/<slug>/`、worktree、herdr 的 agent 状态 hook、插件配置目录里的 `.env`（飞书配对还在，以后重装不用重新扫码）。
- **带 `--host`** 时只撤那个宿主的项。另一个宿主还装着的话，共用的包、脚本入口、路由区块、飞书 oncall 都保留。
- `.bak-drover-*` 备份**不会自动还原**，只列出来让你自己决定（见第 8 节"想回滚"）。

### 2.7 自动更新

装好之后 drover 自己更新：有新版本就直接装，不用你确认；主会话开着好几天不 `/clear`、不 `/new` 也照样更新，新规则不用重开会话就用上。"新版本"默认指作者发布的版本（git tag），见下面"更新通道"。

**更新通道：跟发布的版本还是跟 main**

`~/.drover/config.json` 的 `update_channel`，手动 `update`（2.6）和自动更新都按它：

| 通道 | 装什么 | 给谁 |
|---|---|---|
| `release`（默认，没写这个键也是它） | 上游（一般是 `origin/main`）历史上最新的版本 tag；main 上没打 tag 的新提交不装 | 用 drover 的人 |
| `main` | 上游最新的提交，推上去就装 | 开发 drover 的人（作者本机：推完就装上） |

- 版本 tag 只认 `vX.Y.Z`（三段数字，按数字比大小：`v0.10.0` 比 `v0.9.0` 新）。带后缀的预发布版（`v1.0.0-rc1`）、不在上游历史上的 tag（打在别的分支上的）不算。
- `release` 通道的 clone 停在 tag 那个提交上，落后 `origin/main` 是正常的，不算落后；clone 比最新的 tag 还新（比如以前跟过 `main`）也不回退，等下一个更新的版本。
- 上游上一个版本 tag 都没有时不更新：看板上"drover 自动更新暂停"那一行写原因，手动 `update` 退出码 1。
- 切通道：编辑 `config.json`，比如加一行 `"update_channel": "main"`，下一次 `update` / 自动更新就按新的走，不用重装。从 `main` 切回 `release` 时 clone 多半已经比最新版本新，等下一个版本才会再动。
- 写了别的值（比如拼错了）：手动 `update` 报错、自动更新记成失败，什么都不改。

**怎么发版（作者）**

要发的提交推到 main 之后，在它上面打附注 tag，再推 tag：

```bash
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

- 推了 tag，`release` 通道的机器在主会话开着时最晚下一个检查间隔（30 分钟）里装上。只推 main、不打 tag，只有 `main` 通道的机器会装。
- 不要挪已经推出去的 tag：挪到更新的提交，用户照着快进过去；挪到更旧的提交，已经装了的不回退。发错了就修好再发一个新版本号。

**什么时候查**

- 主会话那边的命令顺带查：会话开始时的登记（Claude Code 的 SessionStart hook，Codex 第一条消息时的 `main-register`）、`watch --main` 的每一轮（Claude Code 主会话一直在后台挂着它）、看板（`board`、`board --json`，看板 tab 里的 `board --live` 每 15 秒一轮；Codex 主会话每条消息都跑 `board --json`）。编排者、worker 用的命令不触发。
- 离上次尝试满 30 分钟才查（`config.json` 的 `auto_update_minutes` 可改），同一时间只跑一个（机器级的锁，手动 `update` 也拿它）。
- 查和装都在一个后台进程里跑（`drover update --auto`），触发它的命令立刻返回，不卡会话、不卡会话启动（SessionStart hook 只有 15 秒）。结果写 `~/.drover/auto-update.json`，日志在 `~/.drover/auto-update.log`。

**装什么、什么时候不装**

- 和 `drover update`（2.6）一样按更新通道只快进，保护全部沿用：clone 有已跟踪的本地改动又落后、和目标分叉、merge / rebase 做到一半、没有上游、上游上还没有发布的版本、连不上远端，都什么都不改，只把原因记下来。
- 无人值守：fetch 只试 1 次、最多 60 秒，git 不在终端上问账号密码（`GIT_TERMINAL_PROMPT=0`），ssh 不问密码、不问主机确认（`BatchMode=yes`，连不上 20 秒就放弃）。你自己配了 `GIT_SSH_COMMAND` 或 `core.sshCommand` 的不覆盖。笔记本离线、代理没开时连不上很常见，所以连不上只记下来，连续 24 小时都连不上才告诉主会话一次。
- 没有要装的新版本时不重跑 install（修安装还是手动 `update` 的事）；有的时候照 2.6 重跑 install、`--check`。

**怎么关**

- 这台机器一直关：`~/.drover/config.json` 里写 `"auto_update": false`。
- 临时关：环境变量 `DROVER_AUTO_UPDATE=0`。它要在主会话（和看板 tab）的环境里才管用，比如在启动 herdr 的 shell 里设。
- 关了也能随时手动 `~/.drover/bin/drover update`。

**更新完，主会话不重开就用上新规则**

drover 记着主会话"载入时"的规则（`~/.drover/main-rules.json`：会话开始时的登记记一份），`drover rules` 把现在 `AGENTS.md` 里 drover 区块和它的差别送进主会话的上下文（开头一句"从现在起以下面为准，替换你上下文里 AGENTS.md drover 区块的旧版本"），再把"载入时"更新成现在的。

- **Claude Code**：后台更新装完，`watch --main` 报一行 `EVENT drover updated(<旧>..<新>) rules`（两头是版本 tag，比如 `updated(v0.1.0..v0.2.0)`；那个提交上没有 tag 时是 7 位 sha），主会话跑 `drover rules` 读入新规则，再跑一次 `drover board --tab`（看板 tab 里还是旧代码的看板就重启，见下面"看板换新代码"），再用一句话告诉你更新了什么。万一那条没走到（没挂 watch），**你的下一条消息就带上新规则**：install 装的 UserPromptSubmit hook（`drover rules --hook`）在你提交消息时把改动一起送进上下文，飞书发来的消息也算。两条路用同一份"载入时"记录去重，不会读两遍。之后 `/compact` 时 Claude Code 本来就会从磁盘重读 `AGENTS.md`。
- **Codex**：没有后台任务，也不装 hook（2.5）。主会话每条消息先跑 `board --json`，看到 `drover.rules_changed`（或者 `drover.auto_update.announced` 是 `false`）就跑 `drover rules`，所以新规则在你下一条消息时用上；你说"盯着"时挂着的 `watch --main` 同样会报 `drover updated`。

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
- `flow/dispatch.md` 变了的，正在跑的编排者照旧按它上下文里的旧流程做，新起的用新流程。所以 CLI 必须向后兼容，见下面的开发约定。

**开发约定**

- CLI 改动保持向后兼容：改参数、改子命令、改输出字段时，旧写法至少再留一个版本（新旧都认），下一个版本再删。正在跑的编排者和 worker 按它们上下文里的旧流程敲旧写法，新代码不认就会在半路出错。
- **发了版（推了版本 tag），用户的机器很快就会装上**：主会话开着时最晚在下一个检查间隔（30 分钟）里。发错了只能再发一个修复版本。所以发版之前照旧在集成 worktree 里跑全量自测（`flow/dispatch.md` 第 9.1 节）。只推 main 不打 tag 时，只有 `main` 通道的机器会装。不想自动更新的可以用上面的开关关掉。

## 3. 配置

配置文件是 `~/.drover/config.json`，由 install 写，也可以直接编辑：

```json
{
  "workspace": "~/work",
  "repo_dirs": ["~/work/company", "~/work"],
  "profile": "example",
  "playbook_dirs": []
}
```

| 字段 | 含义 | 没有配置文件时 |
|---|---|---|
| `workspace` | 主工作台目录：主会话和编排者的工作目录，`AGENTS.md` 路由区块就写在这里。它也是**可信仓库根目录**：在它下面的仓库，Herdr 建 worktree 要求信任时脚本自动处理；别处的仓库会先问你 | 没有默认值。install 第一次会问，非交互时要 `--workspace`（见 2.1）；不能是家目录或它的上级。`bin/drover` 不自动信任任何目录，`orch-start` 报错让你先跑 install |
| `repo_dirs` | 按名字找仓库的目录列表，按顺序找第一个存在的 `<目录>/<仓库名>`。`@` 后面写路径（带 `/`）时直接按路径找，不看这个列表 | 没有默认值。`bin/drover` 只按路径找仓库；install 没传 `--repo-dir` 时写成 `[workspace]` |
| `profile` | 团队 profile（团队规则、编码规范、各仓库的 playbook，见第 3.1 节）。写名字用包里的 `profiles/<名字>/`；写路径（带 `/` 或以 `~` 开头）用那个目录。配置了但目录不存在时 `drover playbook` 报错、`install.py --check` 报 ✗ | `null`（只有包默认规则） |
| `playbook_dirs` | 个人覆盖目录列表，布局和 profile 一样，排在团队 profile 之后读（后读的优先）。install 没有对应参数，手动编辑 | `[]` |
| `default_coding_rules` | 包默认的编码规范（`flow/coding-rules.md`）追加不追加进 worker 任务书，排在团队 profile、个人覆盖的编码规范前面。它分三部分：通用原则（改编自 Robert C. Martin 的 ub-mode：先定数据结构、改行为先写失败的测试、修根因不加兜底、测行为不测实现等，总带）、MySQL 建表的命名 / 注释 / 默认值 / 唯一索引前缀（仓库里有 `.sql` 文件或依赖里有 mysql / mariadb 时才带）、Java 的 import 和不用内部类（仓库根目录有 `pom.xml` / `build.gradle` 时才带），每条只在改到相关代码时适用。`false` 时整份不追加，团队 profile、个人覆盖、`~/.claude/CLAUDE.md` 的照旧；只管这一份编码规范，worker 不执行迁移、数据库写操作升级给用户这些安全和流程规则不跟着关。install 没有对应参数，手动编辑 | 没有这个键 = 开 |
| `oncall` | 接不接飞书 oncall（4.9，选装）。`true` 时 install 装插件、写 `drover.env`；`false` 时不装（已经装上的插件不卸）。`install.py --oncall` 写 `true`，`--no-oncall` 写 `false` | 没有这个键 = 没选过：以前已经接上的（安装记录里有、或者 herdr 里装着插件）照旧接，其余不接 |
| `claude_model` | Claude Code 编排者和 worker 的模型（`claude --model` 的值）。账号没有 1M 上下文档位、`opus[1m]` 起不来时改成 `opus` 等 | `opus[1m]` |
| `claude_effort` | Claude Code 编排者和 worker 的 effort（`claude --effort`：low / medium / high / xhigh / max）。派发时写了 `--effort` 以那个为准 | `xhigh` |
| `codex_effort` | Codex 编排者、worker 和复审的 effort（`-c model_reasoning_effort=…`，比 claude 多一个 ultra）。派发时写了 `--effort` 以那个为准；codex 的模型不传，用 `~/.codex/config.toml` 的默认 | `xhigh` |
| `auto_update` | 自动更新（2.7）。`false` 时这台机器不自动更新（手动 `drover update` 照常）；环境变量 `DROVER_AUTO_UPDATE=0` 也能关。install 没有对应参数，手动编辑 | 没有这个键 = 开 |
| `auto_update_minutes` | 自动更新多久查一次（分钟），离上次尝试满这么久才查 | 30 |
| `update_channel` | 更新通道（2.7）：`release` 只装发布的版本（上游历史上最新的 `vX.Y.Z` tag），`main` 跟上游最新（开发 drover 用）。手动 `update` 和自动更新都按它；写了别的值时 `update` 报错、什么都不改。install 没有对应参数，手动编辑 | `release` |
| `heavy_cpu_capacity` | heavy 调度的 CPU 容量（核）：几条构建 / 测试合计占用不超过它就同时跑。默认从硬件值起步、按真实命令的表现自动校准（第 6 节 `locks/heavy/`），一般不用写；写了就固定用它、不再校准（比如机器还要留给别的重活，或者 Linux 容器里 CPU 配额比看到的核数少） | 不写：Apple 芯片从性能核数（`hw.perflevel0.logicalcpu`）、其他从逻辑核数起步，之后自动校准，不超过逻辑核数 |
| `heavy_calib_slow` / `heavy_calib_fast` / `heavy_calib_down` / `heavy_calib_up` | 容量校准的阈值：同时跑的命令比同一条命令单独跑慢到 `slow` 倍算明显变慢、快于 `fast` 倍算几乎不变慢；连续 `down` 个明显变慢降一核，连续 `up` 个几乎不变慢（负载接近容量时）加一核。`fast` 也是单独跑基准的抖动界线：同一条命令几次单独跑最慢 ÷ 最快超过它，不拿它当基准（同时跑的变慢和它自己的抖动分不开） | 1.5 / 1.2 / 2 / 3 |
| `heavy_small_seconds` | 历史上最长也在这么多秒内跑完的命令算小命令：不排队、不计入占用 | 10 |
| `heavy_starve_seconds` | 排队的命令等了这么多秒还没开始，后来的只放能在它预计开跑前跑完的（防止它一直被插队） | 120 |
| `heavy_grace_seconds` | 刚放行的命令按预留算的秒数（之后按实测）：整机实测取最近 2 秒的平均、进程树实测要两次 ps 采样，再加上命令爬到稳定占用的时间 | 5 |
| `heavy_mem_psi` | 只对 Linux：`/proc/pressure/memory` 的 `some avg10` 到这个百分比算内存紧张（只放单独一条）。macOS 看系统的内存压力，到"严重"才算 | 10 |

路径里的 `~` 会展开。

**改了配置要不要重装？**

- 只改 `repo_dirs`：不用重装。drover CLI 每次执行都会重新读配置，下一条命令就生效。可以直接编辑文件，也可以 `python3 install.py --repo-dir <目录> [--repo-dir <目录>]...`：只改这个键，改之前先备份。`auto_update`、`auto_update_minutes`、`update_channel`、`heavy_*` 同样下一条命令就生效（已经在跑的 `watch --main`、`board --live`、在排队的 `heavy` 用的是启动时读到的配置，重开后才认）。`claude_model`、`claude_effort`、`codex_effort` 从下一个起的编排者、worker、复审开始用，已经起来的不变。
- 改 `profile` / `playbook_dirs` / `default_coding_rules`：也不用重装，下一条命令就生效（已经发出去的任务书不变）。`python3 install.py --profile <名字|目录>` 只改 `profile` 这个键（改之前先备份，解析不到目录时直接报错、不写），`--profile none` 去掉它。
- 改 `workspace`：要重跑 `python3 install.py --workspace <新目录>`。路由区块、`CLAUDE.md` 和 `.claude/settings.local.json` 都写在主工作台里，新目录里要有这些；以后也在新目录里开主会话。
  - 只传 `--workspace` 时 `repo_dirs` 保持原样，仓库目录也要变就一起传 `--repo-dir`。
  - 旧主工作台 `AGENTS.md` 里那段 drover 区块不会自动删，不想留就手动删掉两个标记之间的内容。`--uninstall` 会把安装记录里所有主工作台的区块都撤掉。

### 3.1 团队 profile 和 playbook

规则分三层，后面的覆盖前面的：

1. **包默认**：`flow/dispatch.md` 和 `bin/drover` 里的通用流程，和团队、仓库无关，包括几条安全规则（worker 只写迁移文件和测试、不执行迁移、不写共享 / 生产数据库；数据库写操作默认升级给用户）。需要团队或仓库规则的地方（worktree 位置、迁移授权例外和执行步骤、基线本来就有失败的命令、推送目标分支）只挂一个"按 playbook 做"的钩子；迁移有授权、仓库又没写步骤时用默认步骤（dispatch.md 第 9.1 节第 4 步）。另有一份包默认的编码规范 `flow/coding-rules.md`（通用原则，加上 MySQL 建表、Java 的几条，只在改到相关代码时适用；MySQL、Java 两节只给用得上的仓库带），不配 profile 也追加进每份 worker 任务书；不要就在 `config.json` 写 `"default_coding_rules": false`，只关这一份。
2. **团队 profile**（config 的 `profile`，可选）：团队稳定的规则，一般放在团队自己的仓库里（`profile` 写那个目录），也可以随包放在 `profiles/<团队>/`（包里带了一份示例 `profiles/example/`）。团队成员装上包、设好 profile 就拿到同一套规则，Claude Code 和 Codex 都一样。
3. **个人覆盖**（config 的 `playbook_dirs`）：只对你自己成立的补充或改写。

经常变的个人约定（比如各仓库这阵子推哪个分支）不进 profile，profile 里只写"推送前确认目标分支"。

团队 profile 和个人覆盖目录的布局相同，文件都可选：

```
<profile 目录>/
├── general.md            # 对所有仓库适用、编排者读的规则
├── coding-rules.md       # 编码规范：brief 追加进每份 worker 任务书（claude、codex 都加）
└── repos/<仓库目录名>.md  # 仓库专属 playbook（如 repos/order-service.md），编排者读
```

- 编排者分析任务时跑 `drover playbook --repo <仓库>`：按层（团队 profile → `playbook_dirs` 依次）拼出 `general.md` 和这个仓库的 playbook，仓库约定写进计划，给 worker 的约束写进任务书的"不做"。
- `brief` 自动追加编码规范，每份一节、标题写明来源：先是包默认的 `flow/coding-rules.md`（`default_coding_rules` 没关时），再是各层的 `coding-rules.md`，后面的优先。包默认那份的通用原则总带，MySQL、Java 两节按任务书涉及的 worktree 判断带不带（`drover playbook --repo` 输出的 `default_coding_rules.sections` 写明会带哪几节）。另外 `~/.claude/CLAUDE.md` 的"## 编码规范"一节只给 codex worker 追加（claude 会自动加载 CLAUDE.md）。都没有就不加这一节。
- 看现在生效的规则：`~/.drover/bin/drover playbook --repo <仓库> --text`。

**切换 profile**：`python3 install.py --profile <名字|目录>`，或者直接编辑 `config.json`；`--profile none` 只用包默认规则。

**写个人覆盖**：建一个目录（比如 `~/my-playbooks`），按上面的布局只放你要补充或改写的文件，再手动把它加进 `config.json` 的 `playbook_dirs`。同一件事说法不同时按后读的（你的）做。

怎么写一个新的团队 profile、每个文件写什么，见包里的 [`profiles/README.md`](profiles/README.md)；`profiles/example/` 可以当例子。

## 4. 日常用法

下面都是"在主工作台的会话里说话"。你面对的始终是主会话，编排者和 worker 在背后干活。

### 4.1 开主会话

在 Herdr 里开一个 pane，进主工作台，启动 claude 或 codex：

```bash
herdr                          # 启动或接上 Herdr
# 在 Herdr 的 pane 里：
cd <主工作台>                   # install 时定的 --workspace，比如 ~/code
claude                         # 或者：codex -s danger-full-access（原因见第 7 节）
```

主会话会读到 `AGENTS.md` 里的路由规则，知道自己是"主会话"：负责路由、问答、审核和转达。它还会在 herdr 里把自己登记成 `drover`（Claude Code 由 SessionStart hook 在会话开始时登记，Codex 在你的第一条消息时登记），飞书 oncall 靠这个名字认出主会话。同一时间只能有一个主会话登记成 `drover`，再开第二个它会告诉你。

### 4.2 提需求：直接说，不用写 `/drover`

```
你：@order-service @web-admin 订单列表加一个"按渠道筛选"，后端接口和前端页面都要改
```

主会话的第一条回复会：

1. **先复述**：用自己的话说它理解的问题和要做成的事；原话能有多种理解时，说明它按哪种理解；浅读看得出时，加一句预计能满足目标的最小改动。
2. **接着就交给新的编排者**，不等你确认：写好 `~/.drover/<slug>/request.md`（你的原话、复述的目标、@仓库和参数、它已知的上下文），用 `drover orch-start` 起编排者。
3. 告诉你编排者叫什么，比如 `orch-order-filter`，以及可以 `herdr agent focus orch-order-filter` 直接去找它。

复述理解错了？**直接纠正就行**，比如"不是加筛选，是加排序"。主会话会把你的原话转给编排者，由它改目标和计划。复述就是这个任务的"目标"：每份 worker 任务书和最终汇报都以它开头。

只有一种情况主会话会停下：浅读就看出你要的改动行不通（比如要改的东西根本不存在），它会把证据给你看，等你定。

**哪些事交给编排者、哪些主会话直接做**：

- **交给编排者**：要改代码的需求（新功能、修 bug、重构、补测试、跨仓库改动）；不改代码的调研、排查、出方案、写文档、改 drover 自身，只要不是几次工具调用就能答完的，也一样复述后交给编排者。主会话会在 request.md 里写明任务类型、交付物、要不要改代码（不改就写"只读"），编排者把结论写进 rollup，主会话转达。
- **主会话直接做的只限轻量的**：问答和讲解（凭已有的知识、rollup，或者看一两个文件就能答的）；查进度、转达；本机配置的一两处小改（比如改一行设置、更新一条记忆）。预计要读 3 个以上文件、调用 5 次以上工具、跨仓库，或者要来回好几轮的，就派发。
- **拿不准**：默认派发，主会话用一句话说明为什么派发，不停下来等你确认。你说"直接改""在这里改"才直接做；说"派发"就派发。

这么分是因为主会话的上下文最稀缺：调研、排查、出方案也会很快把它撑满，所以这些也交出去（规则原文在 [`flow/routing.md`](flow/routing.md) 的"任务路由"）。

### 4.3 参数

写在需求里就行，主会话会原样转给编排者：

| 写法 | 含义 | 默认 |
|---|---|---|
| `@仓库` | 涉及的仓库，可以写多个。仓库名、相对路径、绝对路径、仓库里的子目录或文件都行（指到子路径时，子路径当作改动位置的线索） | 没写就由编排者从需求推断，推断不出会问你 |
| `--agent claude\|codex` | worker 用哪个宿主 | 主会话的宿主（主会话跑在 Codex 里就是 codex，跑在 Claude Code 里就是 claude） |
| `--orch claude\|codex` | 编排者用哪个宿主 | 主会话的宿主 |
| `--review` | 验收通过后让 codex 做一次 code review（流程里叫"复审"），High / Medium 问题交回 worker 修一轮 | 不复审 |
| `--effort <level>` | worker 的推理强度。claude：low / medium / high / xhigh / max；codex 多一个 ultra | xhigh（`config.json` 的 `claude_effort` / `codex_effort` 可改，第 3 节） |
| `--name <分支>` | 指定 worktree 分支名 | `task/<slug>` |

例子：

```
@order-service 分页接口 page=0 时报 500，修一下 --agent codex --review
@web-admin/src/pages/orders 列表页加载太慢，查一下原因并优化 --orch codex
```

claude 的编排者和 worker 都用 Opus（1M 上下文），在 `acceptEdits` 下运行；codex 的编排者和 worker 怎么跑见第 7 节。

### 4.4 编排者在背后做什么

1. 读 `request.md`，读相关仓库的 `CLAUDE.md` / `README` 和代码，查这个仓库的历史派发记录（`drover history`）。
2. 把需求拆成条目：**一个条目一个 agent 一个 worktree**，互不重叠的条目同时派发，同一个仓库里也不排队。每个条目写清"完成证据"：用什么命令、看到什么结果算通过。
3. 范围不清、要先盘点的条目，先让 worker "摸底"（只读代码、核实任务书里的事实），事实偏差编排者直接定，影响范围或产品方向的交给你。
4. 追踪每个 worker 直到有结果，中间的审批弹窗、小故障自己处理。
5. 按证据验收：脚本在 worktree 里重跑相关测试、类型检查、lint，建图看风险和没有测试覆盖的函数，读 diff 看是否改在产生问题的那一层、新增的特例和兜底有没有依据，不只看 worker 的自述。不通过就把缺口发回去补。
6. 写 rollup 汇报，停下来等你。

流程细节在 [`flow/dispatch.md`](flow/dispatch.md)（编排者读的就是它），图解在 [`docs/architecture.html`](docs/architecture.html)。

### 4.5 查进度

- **看板**：主会话开会话时在主对话所在的 workspace 里开一个和主对话同级的"看板"tab（`drover board --tab`，里面跑 `drover board --live`，每 15 秒重画），不占主对话的屏幕（笔记本一屏也够用）。主会话 `/clear`、重开都不影响它。drover 自动更新以后它自己换成新代码（2.7"看板换新代码"），不用关掉重开。
  - **看什么**：一张正常的表，一行一个任务，回答：有哪些任务、各自走到哪、要你做什么、在这个状态上待了多久。按状态分组，一组一张表（空的组不画）：**等你**（等你决定、受阻）、**进行中**（进行中；转过你的答复、编排者还没更新的"已转答复，等它接着做"；要换编排者的）、**完成**。组内在这个状态上待得久的在前。主会话自己会处理的（确认框、未读、接口错误）收成最底下一行"主会话在处理：…"。
  - **列**：任务（中文标题）| 当前状态 | 要你做什么 | 状态持续 | 代号（英文任务名，说"收尾 <代号>"、找编排者时用）；完成那张表多一列"收尾"。每格写短；放不下时每种样子各有办法（见下一条），截断的末尾都是 ASCII 的"..."（按显示宽度算，中文两格，"..."算 3 格；以前的"…"宽度不定，中文字体里画成两格，看板 tab 里那一行的竖线就错开了）。表格（终端里的 Markdown 和看板 tab 的框线表）的格子里，rollup 原文带的宽度不定的字（Unicode 的 East Asian Width 为 A，编排者写中文常用的"…""—"就是）先换成宽度确定的再画：… → ...、— – → -（"——" → "--"）、“ ” → "、‘ ’ → '、· → -、→ ← ↑ ↓ → -> <- ^ v、× → x、① 到 ⑳ → (1) 到 (20)，还有 ≤ ≥ ≠ ± ÷ √ ※ ★ ☆ ● ○ 几个；没列到的取 Unicode 的兼容写法（Ⅱ → II、№ → No、é → e），再没有就写 "?"（希腊字母、° 之类）。为什么：这些字 drover 和 herdr 的终端网格都算一格，外层终端、中文字体却常画成两格，herdr 往外层重绘之后 drover 管不到，只能不让它进表格。飞书的纯文字、转达脚注（`--pending`）、`--json` 是原文。提醒（rollup 的"提醒："）跟着它的任务写，一眼看得出是谁的（几个任务写了同一句各自都写）：看板 tab 的框线表里写在那个任务最后一行下面，任务列空着、后面几列合成一格，和任务行之间有一条线，合并的几列在线上收成 ┴（一眼看得出是合并的格子），按这一格的宽度折行（最多 3 行），左右滚以后跟着看得见的宽度重新折、不会被滚出去，表宽不变；飞书的纯文字、看板 tab 太窄退回的列表是这个任务的第三行；终端里的 Markdown 表格不能合并格子，每张表下面写"提醒（<任务>）：…"。
  - **三种样子**：终端里问进度，主会话跑 `drover board` 给你 Markdown（每组一张表；表格折不折行由 Claude Code / Codex 渲染，drover 按上限截：任务 24 格（12 个汉字）、当前状态 40 格、要你做什么 30 格、代号 24 格，状态持续和收尾不截）；飞书里发"看板""进度"，主会话跑 `drover board --plain` 回纯文字（飞书消息是纯文本，表格对不齐）：每组一个标题，每个任务两行"· 标题（代号）"、"  当前状态｜要你：…｜已持续 …"，当前状态、要你做什么不截（手机自己会换行），任务名截到 24 格；看板 tab 里是 drover 自己画的框线表，按显示宽度对齐，任务之间有横线，组之间空一行，几张表列宽一致、竖线上下对齐。框线表里当前状态、要你做什么写长了就在格子里折行：每行最多 28 格 / 22 格、最多 3 行（再多第 3 行末尾截成"..."），英文单词、数字、路径、sha 不从中间断（放不下整个挪到下一行，比一行还长才硬断），中文哪里都能断，但守中文排版的禁则：句末标点（，。；：！？、）」』】》〉和英文的 , . ; : ! ? ) ] }，"…"换成的"..."也算）不放在行首，开括号、开引号（（「『【《〈 ( [ {）不放在行尾——遇到就把上一行最后一个字（或词）一起带到下一行，带完上一行空了才照原来断（"drover 三件修好、集成检查过；等 web-app 那件"不这么带，就会折成第二行以"；"开头）；一个任务占几行时，同一个任务的几行之间不画线，任务名、状态持续、代号、[收尾] 写在第一行；任务、代号照旧截到 24 格。写得短的照旧一行。
  - **窄了怎么办**：看板 tab 里表格比终端宽时，任务列固定，其余整列左右滚（几张表一起滚，不会切半个字；一个任务占几行，滚到哪几列都占这么多行，表不会上下跳），表下面一行写左右还剩哪几列。触控板左右划、← → 键，或者点那一行两头的 [<] [>] 都能滚（以前是 ◀ ▶，它们宽度不定，画成两格时点不准）；放得下时这一行就没了。窄到任务列加一列都放不下（大约 60 格）时改成飞书那样的列表。触控板左右划是 herdr 0.7.4 起转给看板的横向滚轮事件（按 xterm 的编法是 66 往左、67 往右），实际编号还没在真机上核对过：看板把收到的最近 50 条鼠标序列记在 `~/.drover/board-mouse.log`，划一下看这个文件就知道；划不动时 ← → 和 [<] [>] 照样能用。
  - **怎么看**：tab 名就是汇总，比如"看板·等你2·完成1"（为 0 的不写，都为 0 就叫"看板"），扫一眼标签栏就知道有没有事；点这个 tab 看全部。开 tab 不抢焦点，你一直停在主对话里。
  - **一键收尾**：看板 tab 里，"完成"那张表的"收尾"列：能收尾的写着 [收尾]，组标题后面是 [全部收尾（N 个）]（N 是能收尾、还没点过的）。点某一行的 [收尾]，看板把"收尾：<代号>"交给飞书插件（herdr-oncall 的 `send-main.mjs`）送进主会话，消息开头是 `[看板] `，你留在看板上，这一格变成"收尾中"、不能再点；点 [全部收尾]，送"全部收尾：…"，这几行都变"收尾中"，然后跳回主会话的 tab 看它收尾。主会话卡在确认框、输入框里有没发的内容时插件会排队，发完自动送，看板上显示 8 秒插件回的提示。主会话收到后照规则给每个编排者发"收尾"，全部完成后合成一张总表转给你。在终端、飞书里说"全部收尾"或"收尾 <代号>"效果一样（飞书看板最后一行就写着能收几个）。能收尾的是：完成、编排者在 rollup 里写了"收尾：可以"（没写时完成就算可以）、编排者还开着；写了"收尾：先别，<原因>"的不收，收尾格写"先别：<原因>"（比如"有没推的改动"），收尾做完的写"已收尾"；收尾做完但留了东西的（你定先留着，或者 `drover cleanup` 核实不过、你没让删；编排者写"收尾：先别，已经收尾过了，留 <留了什么>"）写"已收尾，留 …"（比如"已收尾，留 1 个 worktree"），不能点、不算进 [全部收尾]：再点只会让 cleanup 再跑一遍、照样留下，要删就说"清理 <任务>"，清完了变回"已收尾"。"收尾中"等编排者重写 rollup 后按 rollup 显示；点了 30 分钟 rollup 还没动（比如主会话没收这个任务、告诉了你原因）就变回 [收尾]。点过的记在 `~/.drover/<代号>/wrapup-clicked.json`。飞书插件没装或版本太旧（没有 `send-main.mjs`）时，点了显示"点不了：飞书插件没装或版本太旧，在主对话里说'全部收尾'"，按钮留着。
  - **怎么回**：点标签栏上主对话的 tab。点看板上按钮以外的地方什么都不做；只有点 [全部收尾] 会跳回主会话。
  - **选中复制**：看板 tab 里和别的 pane 一样，按住左键拖动就是选中（反色），松开自动复制，看板底下显示 8 秒"已复制 N 行"。按阅读顺序选：第一行从按下的那一列到行尾，中间几行整行，最后一行到松开的那一列，反着拖一样；中文字盖住一半算整个字。复制的是看板上画出来的样子（框线一起，横向滚过就是滚过以后的样子），每行去掉行尾空格。按下和松开在同一格才算单击（点 [收尾]、[<] [>] 照旧），拖过的不算点了按钮；拖着的时候看板不刷新，松开再刷新。为什么要看板自己做：看板为了能点按钮、收触控板左右划开了鼠标上报，herdr 的规则是开了鼠标上报的 pane 里左键按下、拖动、松开都交给程序，herdr 自己不选中，也没有按住哪个键绕过的办法；看板选好以后用 OSC 52 把字交给 herdr，herdr 写进本机剪贴板（和它自己选中复制走同一个函数，实测过）。双击选词、Ctrl+C 复制没有；要用键盘选，herdr 的复制模式（prefix+[，v 开始选、y 复制）照样能用。
  - **内容从哪来**：编排者在 rollup 开头写"标题："（主会话派发时写在 request.md 里，编排者照抄）、"摘要："（当前状态一句话）、"要你做："、"收尾："和可选的"提醒："（写法在 [`flow/dispatch.md`](flow/dispatch.md) 的 rollup 格式里）。还在按旧格式写的编排者：没有标题取 request.md 的"标题："，再没有就显示代号；没有摘要取正文那一行的第一句；没有"要你做："时等你决定取"需要你决定"里"答法："那一句（没有写"看转达，回你的决定"），受阻写"看转达，定下一步"，别的写"无"。以前的"## 交付"一节看板不再读。
  - **时长怎么算**："状态持续"是任务在现在这个显示状态上待了多久（不到 1 分钟"刚刚"，不到 1 小时"N 分钟"，不到 10 小时"1 小时 50 分"，再长"29 小时""2 天 3 小时"）：drover 把显示状态每次变化记进任务的时间线（`<slug>/timeline.jsonl`，下面"统计数据"），时间用 rollup 的修改时间（编排者直接写文件，晚看到也准），转过你的答复用转答复的时间；装上之前就在跑的任务按 rollup 最后修改的时间估。最底下那行每个原因各带各的时长：卡在确认框、接口错误、编排者已退出是这个状态本身持续了多久，从 drover 第一次看到它算起（`watch --main` 每 5 秒、看板每 15 秒看一次，记在 `<slug>/agent-state.json`；批完一个确认框又弹一个算新的一段），刚看到的写"（刚发现）"，不编时长；"新的等你决定（未读，N 分钟前写的）""要换编排者（N 分钟前写的）"按 rollup 写的时间；"停了 N 分钟没更新"自己带时长。
  - **统计数据**：总耗时这类数据看板上不显示，但一直记着，以后用来改进 drover（`drover stats` 还没做）。每个任务一个 `~/.drover/<slug>/timeline.jsonl`，一行一件事，只追加：drover 自己发起的在那条命令里当场记——派发和换编排者（`orch-start`）、转答复和收尾请求（`drover prompt`）、看板上点收尾、收尾汇总（`wrapup --draft`）、主会话读到新的 rollup（`drover rollup`）；只能看到的由 `watch --main` 和看板补——显示状态变了、编排者每换一段状态（干活、停下、卡在确认框、接口错误、退出）、停了没更新，两边同时在跑也只记一次。没人在看的时候（没挂 watch、看板 tab 也没开）这段时间的确认框、接口错误记不到，中间改过几次状态的只剩最后一次；观察者重新开始时在 `~/.drover/observe-gaps.jsonl` 记一行"从几点到几点没人看"，统计时知道这段数据不全。`board --json` 每个任务带 `total_seconds`（派发到现在，完成的算到完成）、`state_seconds` 等字段。
  - 以前的 `board --pane`（主对话下面的分屏）还能用；新会话跑 `board --tab` 时会顺带把它关掉。
- **直接问主会话**："进度怎么样""order-filter 那个到哪了"。它跑 `drover board` 把看板原样给你，问到某个任务时读编排者的 rollup 回答，不去翻 worker 的报告和 diff。在飞书里发"看板""进度"也一样，回的是 `drover board --plain` 的纯文字版，按手机一屏排好。
- 自己查一下所有编排者：

  ```bash
  ~/.drover/bin/drover board                # 看板（Markdown 表格）：一行一个任务，等你 / 进行中 / 完成各一张表
  ~/.drover/bin/drover board --plain        # 同一份内容的纯文字版（飞书用）
  ~/.drover/bin/drover board --tab          # 在主对话所在的 workspace 里开"看板"tab（已经有了就复用，里面是旧代码的看板就重启）
  ~/.drover/bin/drover board --line         # 一行汇总，比如"drover 等你 2 · 进行中 1 · 完成 1"，可以放进状态栏
  ~/.drover/bin/drover orch-status          # 活着的编排者：herdr 状态 + rollup 状态行和摘要，needs_attention 是主会话还没读的，blocked 是卡在确认框上的
  ~/.drover/bin/drover orch-status --all    # 连已经退出的也列出来
  ```

  herdr 报的状态（`agent_status`）原样保留；按屏幕修正过的放在 `screen_status`（和原值不同时才有，规则同 `watch`，比如 herdr 报 working、屏幕底部却是编排者后台子 agent 的确认框 → `blocked`）。卡在确认框上的编排者另外列进顶层的 `blocked`（没有就不出这个键），主会话看屏幕后会替你批，破坏性的才问你。每行还有 `rollup_summary`（rollup 的"摘要："行）、`unread`（状态要处理、主会话还没用 `drover rollup` 读过这一版）、`answered_after_rollup`（rollup 写完之后主会话转过你的答复）。`needs_attention` 是 rollup 状态以 等你决定 / 完成 / 受阻 / 需要换编排者 开头、`unread`、编排者已经停下（done / idle / gone）的；已经退出、rollup 是完成的不算。

  Claude Code 的状态栏也能放看板：在你自己的状态栏脚本（`~/.claude/settings.json` 里 `statusLine` 的 `command`）里加一句 `~/.drover/bin/drover board --line`，`statusLine` 里再配上 `refreshInterval`（秒），主会话空闲时也会定时刷新（[官方文档](https://code.claude.com/docs/en/statusline)："re-runs your command every N seconds in addition to the event-driven updates"）。drover 不替你改状态栏。

- 看某个任务的一页汇报：`~/.drover/<slug>/rollup.md`（主会话用 `drover rollup --slug <slug>` 读，读了就记为已读；你自己直接打开，或者在别的终端、编排者里跑 `drover rollup` 都不影响：只有登记的主会话读才算）。
- 想看细节，或者想直接和编排者谈：

  ```bash
  herdr agent focus <编排者名>
  ```

  在它的窗口里说话，它会直接回答，并把结论同步进 rollup。

rollup 的状态：

| 状态 | 意思 |
|---|---|
| 进行中 | 正在干活，"当前进度"里最多 5 行 |
| 等你决定 | 有事要你拍板，见下一节 |
| 完成 | "结论"里有目标、应该怎样、根因（哪一层、为什么）和改在哪一层、依据（修 bug、设计类）、改动范围、验证结果、未决问题、下一步；收尾的"完成"是精简版的收尾汇总（见 4.7 节） |
| 受阻 | 做不下去了，写了原因和建议 |
| 需要换编排者 | 编排者上下文快满了，主会话会起一个新编排者用 `resume <slug>` 接着做，你不用管 |

### 4.6 要你拍板的事

编排者遇到要你决定的事，会写进 rollup 的"需要你决定"（问题、选项、它的建议和理由），状态标"等你决定"，然后停下。主会话先审再转：依据不够、否决理由没核实、链路上的仓库没读、只有现象没有原因、能查却标了未核实的，先打回编排者补；审过才把它转给你（第一行以 `【转达】` 开头，选项明确的用提问框；配了飞书的，提问框推到飞书就是一张带按钮的卡片），**你的答复会原样转回编排者**。

不经你点头，编排者不会做这些：推送、删 worktree 或分支（你说"清理"或"收尾"就是点头：内容都已在推过的远端分支里的直接删，见 4.7）、改 git 历史、数据库写操作（迁移、DDL、改数据）、部署和发布、发消息、改全局配置或其他仓库、`sudo`、全局安装或升级工具。日常的文件读写、装依赖、跑测试和构建它自己批。

### 4.7 提交推送、收尾

**说"提交推送"**（不说就不会提交，最好说清推到哪个分支）：

1. 每个条目在自己的 worktree 里按仓库规范提交。
2. 编排者从最新的上游建**集成 worktree**（每个仓库一个，分支 `task/<slug>`），把各条目的提交依次 cherry-pick 进来，装好依赖。
3. 在集成 worktree 里**整体检查一次**：类型检查、构建、全量测试、lint，这一步不建图。全量测试只在这里跑；上游本来就有的失败会和基线比对，只看有没有新增（基线上只重跑当前失败的那几个文件，不再整套重跑）。之后又补了条目、修了一处，再跑检查按多了什么定范围：只多了代码提交就跑受影响的测试和类型检查，只改了文档不跑，依赖或测试配置变了才再跑全量。
4. 有数据库迁移的仓库，在检查通过后、推送前执行（数据库写操作要你点头，见上一节）。
5. 推送前再同步一次上游：上游有新提交就先 rebase，报告上游改了什么，需要的话补检查，再推；推完用 `ls-remote` 核对远端确实是这个提交。

跨仓任务按依赖顺序推：一般先推提供接口的一方，迁移在依赖它的代码之前。

每次推送都记进任务的 `~/.drover/<slug>/pushes.jsonl`（推到哪条分支、哪些提交、核对过没有），不管走没走集成状态（drover 自己的任务用补丁叠到集成 worktree，也记）。收尾汇总的"提交"一节、下一轮从哪起、清理时核对什么，都按它来。

**多轮任务的下一轮**（推送之后接着改）：新一轮从上一轮推的那条远端分支（fetch 到最新）新起 worktree，不从主检出所在的分支起。建之前命令先核实上一轮推上去的提交在起点里；换了分支（比如新的 release 分支）而缺了上一轮的提交，会停下来列出缺的提交，问你：带上这些提交在新分支上起，还是在旧分支上接着改。一轮推两条分支的，起点按这一轮主要的那条，另一条集成时各自核实。

**说"清理"**（"清理 <任务>"）：编排者跑 `drover cleanup`，逐个核实 worktree 里的东西都已在这个任务推过的远端分支的历史里（推上去之后又被后来的轮次改过也算）、里面的 worker 没在干活，才删 worktree、本地分支、关掉 worker；核实不过的（有没推的改动、worker 还在干活）不动，列出原因问你。不说就不删，推送之后也不自动删。探测、调研用的 worktree（不交付改动）例外：报告记下就删，改过的实验代码先存成 patch 放进任务目录，不用问你。

**说"收尾"**：编排者先出一份**收尾汇总**，再清理。

- 汇总包括：变更总结（改了什么、涉及哪些仓库和主要文件）；提交（每个仓库推到了哪条分支、提交号，同一个任务推了两条分支的各列一行，并核对远端还在）；数据库迁移和它在测试环境、生产的执行情况；配置（配置中心的键、环境变量等：新增、修改还是只核对，哪个环境，改了没有；不需要改配置的也写明为什么）；上线顺序和上线前后要做的事；验证结果和未决的事。
- 放在 `~/.drover/<slug>/wrapup.md`，一个任务一份，留档。编排者先用 `drover wrapup` 生成带事实的骨架（推送记录、推送范围里的迁移和配置文件、新增的配置键引用、整体检查结果），再把说明补全。
- 然后清理（同上面"说清理"）：`drover cleanup` 核实没有未提交的改动、内容都已经在远端、里面的 worker 没在干活，删掉条目和集成 worktree、本地分支，关掉它开的 pane，在汇总的"清理"一节记下删了什么、留了什么。还有没推送的东西、清理被拒绝时，汇总照写，并把没推送的列出来。
- 主会话转给你的是 rollup 里的精简版（提交、数据库、配置、上线各几行，加上 wrapup.md 的路径），之后关掉编排者所在的 tab。几个任务一起收尾（"这几个都收尾"）时，主会话把各任务的精简版合成一张总表：数据库、配置变更、要部署的版本和上线顺序、上线后要做的。
- 只要汇总、先不清理：说"出一份汇总""把变更和配置列一下"，编排者只写 wrapup.md，不删 worktree 和分支，编排者的 tab 也留着。

### 4.8 已有任务的后续

"把上次那个也修了""再加一个导出按钮""刚才那个推一下"——主会话会交给**原来的编排者**，带上你的原话。原来的编排者已经关掉时，主会话会起一个新编排者用 `resume <slug>` 接手（宿主沿用原来的）。

找不到是哪个任务时，主会话会查 `orch-status --all`、各任务的 rollup，或者按仓库查历史：

```bash
~/.drover/bin/drover history --repo order-service --grep 分页
```

### 4.9 离开电脑：飞书 oncall（内置）

飞书 oncall 是选装的（装 drover 时加 `--oncall`；要 Node 18+，收飞书回复要 22+），装上以后离开电脑也不断线：

- **该你的事推到手机**：主会话卡在确认框上、编排者要你拍板 / 做完了 / 卡住了（主会话转达给你的那几句，开头是 `【转达】`），推到你的飞书；卡片上有按钮的，点一下就批了。每条转达末尾附上"其他在等你的"（最多 3 条），前面的待定不会被后面的转达冲掉。
- **在飞书里跟主会话说话**：直接发消息（不引用任何通知），就和在终端里跟主会话说一样：派新活、问"进度怎么样"（发"看板"回的是纯文字版看板）、回答它的问题、说"提交推送""收尾""全部收尾"。主会话对这条消息的回答会挂在你那条消息下面。
- **其余的不吵你**：主会话被编排者状态叫醒后的内部处理、你在终端里跟它的普通对话，都不推。编排者和 worker 的确认框平时有人批（worker 由编排者批，编排者由主会话批），卡太久没人批才升级推给你。飞书是用来接收审批和下达命令的，不是把所有对话都转一遍。

**配对**（一次就行）：在终端里跑 `./install.sh --oncall` 装的，装完会直接问一句"现在用飞书扫码配对吗？[Y/n]"，回车就开始：打印配对链接（装了 `qrencode` 还会在终端里画出二维码），Mac 桌面也会弹二维码；用手机飞书扫码、点确认，配上后自动发一条测试消息（最多等 5 分钟，Ctrl-C 跳过）。让 agent 帮你装的，它会在最后带你配。之后要补配或者看状态：

```bash
~/.drover/bin/drover oncall pair          # 发起配对：输出里的 link 是配对链接；Mac 桌面会弹二维码，没弹就用手机飞书打开 link
~/.drover/bin/drover oncall pair --wait   # 同上，再等你扫码确认（最多 5 分钟），配上后自动发一条测试消息
~/.drover/bin/drover oncall status        # 配上没有（plugin_status.channels.feishu.paired）、插件版本、开关、主会话登记了没有（不打印密钥）
~/.drover/bin/drover oncall test          # 发一条测试消息
```

每扫一次码会在你的飞书里建一个只属于你的机器人；已经配对过的机器再跑 `pair` 只回"已配对"，不会再建。`pair` 要等插件这次的动作跑完才拿得到链接，一般几秒，已配对的机器要先向飞书核对，可能二三十秒（最多等 45 秒，还没跑完就提示你稍后看 `herdr plugin log list`）。

不配飞书也有用：macOS 上的桌面面板按同样的规则提醒（只为主会话弹）。

**通知规则**：

| 谁 | 确认框 / 提问 | 回答完一轮 |
|---|---|---|
| 主会话（herdr 里叫 `drover`） | 桌面面板马上弹，一会儿没答再推飞书；这一轮是飞书发起的，直接推带按钮的卡片 | 只推两种：你在飞书里提问的那一轮的回答（挂在你那条消息下面）、以 `【转达】` 开头的转达（编排者等你决定 / 完成 / 受阻）；被接口错误打断也推一句。其余不推 |
| 编排者（`orch-*`） | 不弹也不推；同一个确认框卡 5 分钟没人批才推（主会话跑在 Codex 里时 2 分钟） | 不推（主会话会转达） |
| worker（其他有名字的） | 不弹也不推；卡 10 分钟没人批才推 | 不推 |
| 你自己开的、没名字的 pane | 照插件原来的规则 | 照插件原来的规则 |

**飞书里怎么说话**：

- 不引用、直接发 → 主会话。送进去时前面加上 `[飞书] `，主会话知道这是你从飞书发的，和你在终端里说的话一样处理；它对这条消息的回答挂在你那条消息下面（插件按会话记录找到那一轮的回答，只发正文），尽量控制在手机一屏，有选项的会变成卡片按钮。
- 图片、图文一起发（富文本）也行：插件把图片下载到 `~/.drover/oncall-images`，在原来的位置换成一行 `[图片] <本机路径>` 和文字一起送进去，主会话自己打开看。放在这个目录是因为 drover 装的 Claude Code 权限规则已经放行了 `Read(~/.drover/**)`，主会话读图不用你逐张批准；放在插件自己的状态目录会每张弹一次读文件确认。图片 7 天后自动删。下载失败的那张会在回执里说明；文件、语音、视频、表情包、合并转发、卡片暂不支持，会回一句"暂不支持…，没送进去"。带图片的消息不能用来回答确认框（引用确认卡片回复时整条不送，回执会说明），确认框只能用文字或按钮回答。
- 引用某条通知回复、点卡片上的按钮 → 那条通知对应的 agent（比如升级上来的 worker 确认框）。
- `/off` 停掉所有推送和送达，`/on` 恢复（终端里是 `drover oncall off` / `on`）。以 `/` 开头的消息由插件自己处理，不进主会话。

**安全**：插件只认配对时扫码的那个人、只认单聊。但如果你的飞书账号被别人拿到，他就能像你本人一样指挥主会话：派任务、提交推送、收尾删 worktree。飞书来的消息和你在终端里说的一样，不会再额外确认，这是有意的（不然离开电脑时什么都批不了）。手机丢了或者觉得不对劲：飞书里发 `/off`，或者在电脑上 `~/.drover/bin/drover oncall off`；要彻底解绑，`~/.drover/bin/drover oncall unpair`。

**装和关**：要就 `python3 install.py --oncall`（记在 `config.json`，以后重跑、`drover update` 都照旧；以前装 drover 时已经装上的不用加，照旧保留、照旧更新）。不要了：`python3 install.py --no-oncall`（同样记住，以后重跑也不装；已经装上的插件不会被卸，要卸就 `herdr plugin uninstall com.codreamer.herdr.oncall`）。

基于 [herdr-oncall](https://github.com/fulanto/herdr-oncall) 插件，接了飞书时 install 自动安装和更新（之后插件每几个小时自己跟 `stable` 更新）。你以前单独装过它的，install 把它算作已经接上：保留你的安装和飞书配对，只补一个 `drover.env`（不想让 drover 动它就 `--no-oncall`）。

## 5. 子命令速查

脚本是 `~/.drover/bin/drover <子命令>`。除 `watch`、`heavy`（输出原样透传）、`board`、`rollup`、`rules`（输出给人看的文本，`board --json` 才是 JSON）和 `update`（默认打印几行中文，加 `--json` 才输出 JSON）外，每个子命令都向 stdout 输出一个 JSON：`ok` 表示成不成功，失败时 `error` 写明原因和下一步。`<子命令> --help` 查参数；会改动东西的子命令都支持 `--dry-run`，只列出要执行的命令。

"谁用"一栏：**你** = 日常会直接用；**主会话** = 路由时用；**编排者** = 主要由编排者（或 worker）调用，一般不用手敲。

| 子命令 | 做什么 | 谁用 |
|---|---|---|
| `orch-status` | 列出编排者：herdr 状态 + rollup 状态行和摘要（`rollup_summary`），按屏幕修正的 `screen_status`，卡在确认框上的列进 `blocked`；`unread` 是主会话还没读过这一版 rollup，`answered_after_rollup` 是 rollup 之后转过你的答复，`needs_attention` 只算未读、编排者已停下的；`--all` 含已退出的，`--with-sub` 含子编排者 | **你**、主会话 |
| `board` | 看板，每次现场从 rollup 和 herdr 生成：一行一个任务，按等你 / 进行中 / 完成分组，一组一张表，列是 任务（中文标题）\| 当前状态 \| 要你做什么 \| 状态持续 \| 代号，完成表多一列收尾；写了提醒的任务，提醒跟在它下面（框线表里写在那个任务最后一行下面、后面几列合成一格，Markdown 每张表下面"提醒（<任务>）：…"），最底下一行"主会话在处理"（确认框、接口错误、退出按状态本身持续了多久，记在 `<slug>/agent-state.json`）。默认输出 Markdown（GFM 表格，主会话在终端里原样回给用户）；`--plain` 纯文字（飞书用，一个任务两行、写了提醒的三行，最后一行写能收尾几个）。第一行末尾是 drover 的版本，更新过（自动的、手动的）、被挡住时多一行（2.7）。读屏的几种（文本、`--plain`、`--json`、`--live`）顺带把显示状态的变化、编排者每换一段状态补进时间线 `<slug>/timeline.jsonl`（同 `watch --main`）。`--json` 给主会话用（`tasks` 每项的 `reasons` 列出每个原因的 `kind`、`text`、`since`、`since_source`，`wrapup`、`wrapup_reason`、`deliverables`（旧 rollup 的"## 交付"，没有是空的），`title`、`ask`、`display_state`、`state_since`、`state_seconds`、`total_seconds`、`wrapup_clicked`、`note`（提醒原文，没有是 null）；顶层 `wrapup_ready` 是能一键收尾的任务，点过的也算；另有 `drover` 字段：版本、最近一次更新（`auto_update.source` 是 auto / manual）、`rules_changed`）；`--line` 一行汇总（等你 / 进行中 / 完成，给状态栏，不读屏、不写文件）；`--pending [--except <slug>]...` 只列其他在等你的（给转达当脚注，没有就不输出）；`--live [--interval 15]` 循环重画（框线表，当前状态、要你做什么写长了在格子里折行、最多 3 行，比终端宽时任务列固定、其余整列左右滚，太窄退回列表），顺带补登记丢了名字的主会话，在看板 tab 里时 tab 名跟着汇总改、能点某一行的 [收尾]（留在看板，变"收尾中"）和 [全部收尾]（送完跳回主会话；都交给飞书插件的 `send-main.mjs` 送进主会话）、点 [<] [>] 或触控板左右划、← → 键左右滚，点别处什么都不做，按住左键拖动选中、松开自动复制（OSC 52，底下显示"已复制 N 行"；按下松开在同一格才算单击，拖动中不刷新），每轮写心跳 `board-live.json`；装着的 drover 换了版本（两边都取得到、又不一样）就试跑新代码、恢复终端、原样重新执行自己（进程号不变），新代码跑不起来时看板上写原因、接着用旧代码、下一轮再试；`--tab` 在主对话所在的 workspace 里开一个跑 `--live` 的"看板"tab（不抢焦点），已经有了就复用，复用时心跳没有、不是这个 pane 的、版本和装着的不一样、超过 120 秒没更新就 `herdr pane send-keys <pane> ctrl+c` 再 `pane run` 重启（`restarted: true`、`restart_reason`），顺带关掉 `--pane` 开的旧分屏（`--dry-run`）；`--pane` 是旧的下方分屏，留着向后兼容。文本、`--plain`、`--json`、`--live` 会触发自动更新检查 | **你**、主会话 |
| `rollup` | `--slug <slug>` 或 `--name <编排者名>`（二选一；`--name` 按 orch.json 找任务，同名取最新的，`watch --main` 的事件行带的就是编排者名）：打印 rollup 原文，开头一行说和主会话上次读的相比改了什么（第一次读 / 内容没变 / 改了 N 行并列出）。主会话读（`HERDR_PANE_ID` 等于 `main-session.json` 的 `pane_id`，`main-register` 登记时记的）同时记为已读（`main-seen.json`），读到新内容时在时间线记一条；别人读（编排者、worker、普通终端）照样输出，最前面多一行"# 不是主会话（原因），没记已读"，两样都不写 | 主会话；编排者也能用 |
| `rules` | 主会话读 drover 规则的改动，不用 `/clear`（2.7）：`AGENTS.md` 里的 drover 区块和主会话载入时的（`main-rules.json`）比，开头一句"从现在起以下面为准"，再是改动的行；`--full` 给整个区块。读完记成载入时，最近一次自动更新标成已报。`--hook` 给 Claude Code 的 UserPromptSubmit hook 用（只在主会话、规则变了时输出，退出码恒为 0） | 主会话 |
| `oncall` | 飞书 oncall（4.9）：`pair` 发起扫码配对，输出里的 `link` 是配对链接（Mac 桌面没弹二维码时用飞书打开它），`--wait` 等到配上再自动发测试消息；`unpair` 解绑、`test` 发测试消息、`on` / `off` 开关（交给插件执行，立即返回，真实结果看 `herdr plugin log list --plugin com.codreamer.herdr.oncall --limit 5`）；`status` 看插件版本、开关、飞书配没配、poller、`drover.env`、主会话登记了没有，不打印密钥 | **你** |
| `main-register` | 把当前会话在 herdr 里登记成主会话（改名为 `drover`）：已经是就什么都不做；别的 pane 已经登记了、自己叫别的名字、在编排者的 `orch` workspace 里都不改，报出原因。`--hook` 给 Claude Code 的 SessionStart hook 用（静默、退出码恒为 0）。认出主会话后记下载入时的规则（`main-rules.json`）、触发自动更新检查；登记上了写 `main-session.json`（`session_id`、`pane_id`、`source`，`rules --hook`、`watch --main`、`drover rollup` 用；Claude Code 不带 `--hook` 的兜底登记只在记的不是这个 pane 时补写，`session_id` 写 null） | 主会话（会话开始时）；你排障时手动跑 |
| `update` | 更新 drover（2.6）：按安装记录找到 clone，fetch 上游只快进，按原来的 `--mode` / `--host` 重跑 install、再 `--check`，最后几行中文说更新到哪、改了什么、下一步（规则变了就跑 `drover rules`）；本地有改动、分叉、连不上远端时什么都不改、退出码 1；跑着的版本真的换了时和自动更新一样记进 `auto-update.json`（看板第二行"drover 已更新 a→b（时间，手动）"）。`--dry-run` 只 fetch、看有没有新版本，`--json` 输出 JSON；`--auto` 是后台的自动更新（2.7：无人值守、没新提交不重跑 install，结果写 `auto-update.json`），一般不用手敲 | **你**、主会话；`--auto` 由主会话那边的命令在后台起 |
| `watch` | 追踪 agent 状态，变化时输出一行 `EVENT <名字> <状态> @<时间>`（编排者另有 `rollup:<状态>` 事件，rollup 按内容判断改没改）；`--once` 只查一次，`--until-event` 第一个事件就退出。`--main` 是主会话专用：不写名字就自动盯所有顶层编排者，只报要处理的事（未读的 rollup、确认框、接口错误、意外退出、停住），另外报 drover 自己的事（`drover updated(…)`、`drover update-blocked`、`drover update-unreachable(24h)`、`/clear` 之后的 `drover session-restarted`），报过的记在磁盘上，重挂不重报，两个同时跑也只报一次 | **你**、主会话、编排者 |
| `history` | 按仓库列出历史派发记录（结论、未决、踩坑），`--grep` 摘出相关行 | **你**、编排者 |
| `playbook` | 读团队 profile 和个人覆盖目录的规则：`general.md` 和每个 `--repo` 的 `repos/<仓库名>.md`，按层拼好；`--text` 只打印正文。配置了 profile 但目录不存在时报错 | **你**（看生效的规则）、编排者 |
| `preflight` | 检查是否在 Herdr 里、herdr server 是否在跑、常用工具装没装 | **你**（排障）、编排者 |
| `accept-summary` | 从验收存档重新打印摘要（含测试计数），可附失败输出 | **你**（想看验收细节时）、编排者 |
| `heavy` | 经调度执行构建 / 测试命令，输出原样透传，退出码同被执行的命令。按"机器还剩多少、这条要多少"决定马上跑还是排队：容量从本机硬件值起步（Apple 芯片的性能核数，其他机器的逻辑核数），之后按真实命令的表现自动校准（同时跑明显变慢就降、接近容量还几乎不变慢就加，config 可固定），需求按这条命令自己最近几次的实测（CPU 时间 ÷ 墙钟）算，它没跑过就看同类命令（同一命令形状）、再看同程序的；放行按实时 CPU（设计底线：CPU 空着不能干等）：整机实测没满、heavy 自己实测的占用加上这条不超过容量的 1.2 倍就跑，真满了才排队；没跑过的先按 1 核，历史上 10 秒内跑完的小命令不排队，排头等太久时后来的只放能在它开跑前跑完的，内存严重紧张时只放单独一条。排队时打印这条要多少、整机实测忙多少、heavy 占多少 / 容量、在跑的是谁；`--status` 看全机调度状态。细节和来由见第 6 节的 `locks/heavy/`。只执行构建 / 测试工具（跳过开头的 `env` 和 `VAR=值` 之后，程序名得是 mvn、gradle、npm、bun、vitest、tsc、python、node、pytest、make、go、cargo 这类），别的命令不执行、退出码 2：白名单 `Bash(~/.drover/bin/drover:*)` 放行了 heavy，它不能变成放行任意命令的口子。任务书里写成字面的 `~/.drover/bin/drover heavy -- <命令>`，worker 跑时命中白名单、不弹框 | worker（测试、lint、类型检查、构建一律经它）、编排者；你手动跑也可以用 |
| `orch-start` | 为一个任务起编排者：在 herdr 的 `orch` workspace 里开一个 tab，启动 claude 或 codex，发开场 prompt。启动弹窗和 `agent-start` 一样自动处理（包括 Claude Code 对 `--add-dir` 加进来的状态目录弹的 "Adding a new directory" 信任框），处理完再发开场 prompt | 主会话（上层编排者起子编排者也用它） |
| `prompt` | 把文件（或 `--text` 短消息）发给某个 agent，看到它开工（working）就返回（正常 1 秒内，最多等 30 秒），之后交给 `watch`；发给编排者成功后记一个 `answered.json`（看板靠它分出"已转答复，等它接着做"），时间线记一条转答复（内容以"收尾"开头的再记一条收尾请求） | 主会话（转达）、编排者 |
| `wt` | 建或复用 worktree。新建前先 fetch 源仓库主检出的上游，主检出干净且落后时快进（只快进，不硬覆盖），从远端最新起（主检出有改动、分叉时不动它，改从上游起并在 `warnings` 里报出；上游在远端已删当作没有上游；主检出不在远端默认分支上时提示一句；`--no-sync` 不同步）；复制 `.claude/settings.local.json`、`.env*`（源仓库没跟踪的，或在新 worktree 里被 gitignore 的），以及被 gitignore 的仓库规范文件（`CLAUDE.md` 等），以及仓库根目录 `.worktreeinclude`（Claude Code 的约定，gitignore 语法）匹配到的被忽略文件（如 Java 仓库的本地配置、本机测试页；在新 worktree 里不被忽略的不复制，列在 `worktreeinclude_skipped`）。多轮任务的新一轮（`--slug`，不传按分支名 `task/<slug>…` 归属；任务在这个仓库有推送记录时）：不传 `--base` 从上一轮推的远端分支起（推过两条以上要显式 `--base`）；建之前核实起点含上一轮推的提交（同分支看祖先，换分支按补丁内容比），不含就 `ok: false`、不建，列出缺的提交和两种做法；`--carry "<理由>"` 带上缺的提交照常建（理由记进 `carry.jsonl`）。没有推送记录时起点和以前一样 | 编排者 |
| `deps` | 在 worktree 里独立安装依赖（不软链源仓库） | 编排者 |
| `agent-start` | 在 pane 里启动 claude / codex worker，自动处理启动弹窗：新目录的信任弹窗、Claude Code 对 `--add-dir` 加进来的目录弹的 "Adding a new directory" 信任框（光标默认在 "No, exit"，移到 "Yes, add it" 再确认）、codex 的升级提示 | 编排者 |
| `brief` | 按模板生成任务书（必填完成证据），可先摸底，`--send` 生成后立刻发出（和 `prompt` 一样看到开工就返回）。模板里限了篇幅：摸底只写结论和证据位置，"和代码不符""需要决策"两部分写全、其余 100 行左右；报告每条 verification 一行、尽量 60 行内，worker 在对话里只回复报告路径和 status | 编排者 |
| `accept` | 验收：标记新文件、建图、改动分析、diff 概览、重跑验证命令；`--verify-baseline` 和基线比失败条目（基线上只重跑失败的文件），`--no-graph` 不建图（集成检查用）。建图和验证命令都经 heavy 调度；多条验证默认同时交给调度（能同时跑的同时跑，结果按传入顺序），前后有依赖的加 `--serial` 逐条跑 | 编排者 |
| `integrate` | 集成 worktree：从最新上游建（或复用），依次 cherry-pick 条目分支，装依赖。新建时和 `wt` 一样核实起点含上一轮推的提交（`--from` 的条目分支里已有的不算缺），`--carry` 同 `wt`；重建集成 worktree 不丢推送记录 | 编排者 |
| `push` | 集成 worktree 推送：上游有新提交就 rebase 并报告（这次不推），否则推送并 `ls-remote` 核对；每次推上去都记进任务的 `pushes.jsonl`（有没有集成状态都记），定不了任务就不推、提示加 `--slug` | 编排者 |
| `wrapup` | 收尾汇总的事实收集，不改仓库和任务记录：每个仓库每条分支的推送记录（`pushes.jsonl` 和旧任务的 `integrate-*.json`，patch 模式的也在）和远端核对、推送范围里的迁移 / 配置 / 部署 / 依赖文件和新增配置键线索、整体检查结果、待清理的 worktree 和分支（子编排者的一起收）；`--draft <文件>` 生成 wrapup.md 骨架（只写这一个文件，写成了在时间线记一条收尾汇总） | 编排者 |
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
~/.drover/bin/drover update                                      # 更新 drover（2.6）
~/.drover/bin/drover rules                                       # 主会话：读 drover 规则的改动（自动更新之后，不用 /clear）
```

另外，`/drover`（Claude Code）和 `$drover`（Codex）是编排者用的流程入口，日常不用手动敲。

## 6. 目录布局

### 包里的

```
drover/
├── bin/drover                      # drover CLI（Python 标准库，子命令输出 JSON）
├── flow/
│   ├── dispatch.md                 # 流程正文 = Claude Code 的 /drover 命令；附录 A 是 Claude Code，附录 B 是 Codex
│   ├── coding-rules.md             # 包默认的编码规范：brief 追加进每份 worker 任务书（config 的 default_coding_rules 可关）
│   └── routing.md                  # 主工作台的路由规则，安装时渲染进 <workspace>/AGENTS.md 的标记区块
├── profiles/                       # 团队 profile（见第 3.1 节）
│   ├── README.md                   # 三层关系、目录布局、怎么写新的团队 profile
│   └── example/                    # 示例团队 profile（虚构的团队和仓库），照着写你自己的
├── third_party/ub-mode/            # Robert C. Martin 的 ub-mode 原文和许可证（MIT），编码规范的通用原则改编自它
├── codex/skills/drover/SKILL.md    # Codex 的 $drover 技能：一层壳，让 codex 去读 flow/dispatch.md
├── hooks/cd_git_guard.py           # Claude Code 的 PreToolUse hook：拦下 cd <目录> && git …，提示改成 git -C（install 装进 ~/.claude/settings.json）
├── install.sh                      # 一键安装入口：检查 git / python3，再把参数交给 install.py
├── install.py                      # 安装脚本（Claude Code / Codex，link / copy；缺 herdr 时可以替你装；飞书 oncall 插件、主会话登记 hook）
├── tests/                          # install.py、install.sh、cd+git hook 和 drover update 的测试
├── selftest/                       # bin/drover 的自测：假 herdr、本地裸仓库，不碰真实状态；run.py 并行跑全部自测（第 10 节）
├── README.md
├── LICENSE                         # MIT
└── docs/
    ├── architecture.html           # 技术架构与流程说明
    ├── agent-install.md            # 写给 agent 的安装步骤（README 开头让 agent 先读它）
    └── images/                     # 插画（等距科技风）
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

每一项的说明见第 2.5 节。

### 运行时的

```
~/.drover/
├── <slug>/                          # 一个任务一个目录（slug 不能和固定目录重名：pkg、src、bin、backups、baselines、locks）
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
│   ├── pushes.jsonl                 # 推送记录：每次 drover push 推上去追加一行（目标分支、本地分支、远端提交、范围、提交、时间、核对过没有）
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
│                                    #   预计开跑前跑完的（按历史最长墙钟，EASY backfill），这是 CPU 可以空着还有人排队的唯一情形。codex 沙箱里
│                                    #   ps 不让执行、整机实测读得到：没有进程树实测的票按预留算、但不超过整机实测；整机也读不到时退回按预留
├── locks/update.lock                # 更新锁：同一时间只跑一个 drover update（自动的、手动的）
├── board-tab.json                   # 看板 tab 是哪个（tab、pane、workspace）、主会话在哪个 pane，board --tab 复用、--live 认出自己在看板 tab 里、补登记靠它
├── board-live.json                  # 看板 tab 里的 board --live 每轮写的心跳（pid、pane、它跑的代码的版本、时间）；board --tab 靠它认出旧代码的看板、重启
├── board-pane.json                  # 旧的看板分屏（board --pane）是哪个 pane、开在哪个主会话 pane 下面；board --tab 关掉分屏时一起删
├── board-mouse.log                  # 看板 tab 收到的最近 50 条原始鼠标序列（核对触控板左右划的编号用；拖动不记）
├── observe.json                     # watch --main / 看板最后一次看的时间
├── observe-gaps.jsonl               # 没人看的时段（一行 from / to，超过 60 秒才记）：这段时间时间线里的确认框、接口错误记不到
├── locks/observe.lock               # 读改 observe.json 时的锁
├── auto-update.json                 # 自动更新（2.7）：最近一次尝试的时间和结果、最近一次更新（from / to / 标题 / 规则变没变 / 自动还是手动 / 报过没有）
├── auto-update.log                  # 后台自动更新的日志
├── main-session.json                # 登记上的主会话：session_id、pane、source（startup / clear / compact / resume），main-register 写；drover rollup 按 pane 认主会话
├── main-rules.json                  # 主会话载入时的 drover 区块，drover rules 拿它比、读完更新
├── main-watch.json                  # watch --main 报过的 drover 自己的事（会话重开），多个 watch 只报一次靠它
└── baselines/                       # 基线比对缓存（按仓库 + 基线 commit + 命令 + 重跑的文件），以及临时的基线 worktree

~/.herdr/worktrees/<仓库>/…          # Herdr 管理的 worktree，条目的和集成的都在这里
```

任务目录不会被自动清理，它也是 `history` 的数据来源：以后派发到同一个仓库时，编排者会从这里找以前的决定和踩过的坑。

## 7. Codex 注意事项

主会话、编排者、worker 都可以跑在 Codex 里；主会话跑在 Codex 里时，没写 `--orch` / `--agent` 的编排者和 worker 默认也是 codex。和 Claude Code 不同的地方：

- **主会话和编排者要跑在 `danger-full-access`**。它们要连 Herdr 的 unix socket、写 `~/.drover`、git 联网；`workspace-write` 沙箱挡住 unix socket，herdr 命令会报 `Operation not permitted`。
- **主会话在第一条消息时登记**：drover 不给 Codex 装 SessionStart hook（codex 的这个 hook 第一条消息时才跑，新 hook 还要手动信任，信任前所有 codex 会话启动都卡在 "Hooks need review" 上）。Codex 主会话按路由规则在你的第一条消息时跑 `drover main-register`，在这之前飞书里不引用发的消息找不到它。
  - Codex 编排者（主会话是 Codex 时默认就是，或者写了 `--orch codex`）由 `orch-start` 起，脚本自动带 `-s danger-full-access -a on-request`，不用你管。
  - 主会话是你自己开的：用 `codex -s danger-full-access` 启动，或者把 `~/.codex/config.toml` 的 `sandbox_mode` 设成 `danger-full-access`。install 发现不是时只打印提示，不替你改。
- **worker 用 `workspace-write`**（`-a on-request -s workspace-write`）。worker 只在自己的 worktree 和任务目录里写文件，不碰 herdr；联网或写沙箱外路径会弹审批，由编排者处理。`agent-start` 另外用 `--add-dir` 放行 heavy 的调度目录 `~/.drover/locks/heavy/`，worker 的测试、构建照样进全机调度（以前写不了，不排队直接跑，调度器也看不见）。沙箱里 `ps` 和内存压力读不到：这些命令的内存峰值只记单个进程的、被中断时只结束直接子进程，内存信号按不紧张；进程树的实测 CPU 也没有，调度按预留算它、但不超过整机实测（整机 CPU 用 host_processor_info 读，用 codex 内嵌的 seatbelt 基础策略实测读得到）。
- **Codex 没有 Monitor**，主会话不能在后台挂着等编排者：
  - 每次收到你的消息，主会话先跑一次 `drover board --json`，`group` 是 `main` 的（看板最底下"主会话在处理"那一行）先处理：卡在确认框上的编排者先看屏幕批掉，主会话还没读过的完成、等你决定先审完转给你，再处理你这条消息。读过没有由 drover 记（主会话用 `drover rollup` 读），`/new` 之后也不会重复转。所以编排者做完时，Codex 主会话**不会主动开口**，要等你下一句话。
  - 你说"等着""盯着""等结果"时，它用 `drover watch --main --until-event --timeout 600` 阻塞等待：自动盯所有编排者，有要处理的事（新的 rollup、确认框、接口错误、编排者意外退出或停住）就退出，读 rollup 转给你，到期再挂一轮。
  - 看板 tab 两种宿主都能用：Codex 主会话登记完也跑 `drover board --tab`。
  - 编排者自己追踪 worker 时也是 `watch --until-event` 阻塞等待（1500 秒一轮）。
- **effort 默认也是 xhigh**。脚本给 codex 的 worker、编排者、复审都传 `-c model_reasoning_effort=xhigh`；不传的话会继承 `~/.codex/config.toml` 里的设置，配的是 ultra 就会很慢（实测 worker 改两个文件花了 19 分钟）。要换就在需求里写 `--effort`，或者在 `config.json` 里配 `codex_effort`（第 3 节，每次都用它）。
- **Codex 不会自动加载 `~/.claude/CLAUDE.md`**。用 codex 起编排者时，本机有这个文件的话，开场 prompt 会让它先读一遍。Codex 主会话没有这一步：你的全局规则写在 `~/.claude/CLAUDE.md` 的话，可以在 `AGENTS.md` 的 drover 区块外加一句"开始前先读 `~/.claude/CLAUDE.md`"。主工作台的路由规则写在 `AGENTS.md`，Codex 会自动加载，不受影响。
- 主会话派发时**不用**加载 `$drover`：按路由规则写 `request.md`、跑 `orch-start` 就够了。`$drover` 是给编排者用的，整份流程正文 500 多行，主会话读了白占上下文。
- 脚本启动 codex 编排者、worker、复审时都会带 `--no-daemon`（codex 0.157 起交互模式默认连共享后台 server，实测会直接报错退出）和 `--no-alt-screen`（输出留在滚动缓冲区，`herdr agent read` 才读得到），不用你管。

以上以 [`flow/dispatch.md`](flow/dispatch.md) 的附录 B 和 `bin/drover` 为准。

## 8. 排障

### 没装 herdr

`./install.sh` 计划的第一项是"安装 herdr"。在终端里直接跑会先问你一句 `[Y/n]`；在管道、CI 或者 `--json` 下不会装，计划里那一项是 `✗`，提示里给出官方命令，退出码 1。处理：

```bash
./install.sh --install-deps                      # 不问，直接用官方脚本装，装完接着把剩下的装好
# 或者自己装，再重跑 ./install.sh：
curl -fsSL https://herdr.dev/install.sh | sh     # 官方安装脚本，装到 ~/.local/bin/herdr
brew install herdr                               # 或者 Homebrew
```

- 装完提示 `~/.local/bin` 不在 PATH 里：把 `export PATH="$HOME/.local/bin:$PATH"` 加进 `~/.zshrc`（bash 用 `~/.bashrc`），开一个新终端。drover CLI 按名字调用 herdr，不在 PATH 里会报"找不到 herdr 命令"。
- 安装脚本失败（报 `curl` 退出码、下载超时、校验和不对）：多半是网络，换个网络或用 `brew install herdr`。官方脚本要 `curl`、`awk` 和一个算 sha256 的工具，没有 `curl` 时 install 会直接提示你先装 `curl` 或改用 Homebrew。
- 内网有 herdr 的镜像时，可以设环境变量 `DROVER_HERDR_INSTALLER="<你的安装命令>"`，install 会用它代替官方命令（照样要你同意或带 `--install-deps`）。
- 装好 herdr 之后还要在 Herdr 里开主会话，见下一条和第 4.1 节。

### 没装 claude / codex，或者缺 git、python3

- 两个宿主都没检测到：install 退出码 2，提示里列出 Claude Code（`curl -fsSL https://claude.ai/install.sh | bash`）和 Codex（`curl -fsSL https://chatgpt.com/codex/install.sh | sh`）的官方安装命令。装好一个再跑，或者先用 `--host` 指定把 drover 这边装好。
- `./install.sh` 报"缺 git""缺 python3"或者"python3 版本太旧"：按它打印的装法装好（macOS 一般 `xcode-select --install` 就都有了），再重跑。

### 不在 Herdr 里

`drover preflight` 报"不在 Herdr 中（HERDR_ENV 不是 1）"、"herdr server 没在运行"或"找不到 herdr 命令"。

主会话必须开在 Herdr 的 pane 里：先跑 `herdr` 进去，在里面的 pane 里 `cd` 到主工作台再启动 claude / codex。在普通终端里开的会话起不了编排者。

### agent 状态不对、一直是 unknown

Herdr 靠 agent 状态 hook 判断 working / idle / blocked，hook 没装或版本旧了，状态就不准。

```bash
herdr integration status            # claude、codex 应该是 current
herdr integration install claude    # 不是 current 就重装（codex 同理），或者重跑 python3 install.py
```

重装 hook 后，已经在跑的 agent 重启一下最稳妥。

### codex 编排者 / 主会话连不上 Herdr

报 `Operation not permitted`：跑在 `workspace-write` 沙箱里了，见第 7 节。Codex 编排者（主会话是 Codex 时默认就是，或者写了 `--orch codex`）由 `orch-start` 起，会自动带 `danger-full-access`；主会话要你自己用 `codex -s danger-full-access` 启动。`python3 install.py --check` 也会提示 `sandbox_mode`。

### 启动卡在信任 / MCP 弹窗

- worker 在新 worktree 里启动时的信任弹窗、Claude Code 对 `--add-dir` 加进来的目录（任务状态目录）弹的 "Adding a new directory" 信任框、codex 的升级提示，`agent-start` 和 `orch-start` 会自动处理（信任弹窗会把光标移到"Yes"再确认：Claude Code 的默认选项是"No, exit"，直接回车 agent 就退出了）。
- 其他弹窗（比如 MCP server 的批准）会让 agent 停在 `blocked`：worker 的由编排者看屏幕后处理，编排者的由主会话处理，破坏性的才问你。
- 编排者的工作目录就是主工作台：**先在主工作台里手动开一次 claude / codex，把信任和 MCP 弹窗点掉**，之后起编排者就不会卡在这里。
- 实在卡住了：`herdr agent focus <名字>` 过去手动选。

### 权限弹窗太多

```bash
python3 install.py --check    # 看权限规则装上没有
```

- 规则在 `~/.claude/settings.json` 和 `<workspace>/.claude/settings.local.json`，缺了重跑 `python3 install.py`。
- Claude Code 的编排者和 worker 跑在 `acceptEdits` 下：写文件自动放行，bash 命令要命中白名单前缀才不弹。有一类弹窗没有"don't ask again"选项（`$(...)` 命令替换、heredoc、`cd X && git …` 这类写法会触发），只能靠不写出这种命令来避免；流程和任务书里已经要求 agent 一条命令只做一件事、脚本先写成文件再执行。其中 `cd X && git …` 由 install 装的 cd+git 拦截 hook 兜底（子代理也管得到）：agent 会收到"改成 `git -C X …`"的拒绝理由，自己改写重试，不用你批。`--check` 里"Claude Code 的 cd+git 拦截 hook"一项是 `✗` 时重跑 install。
- 遇到新的弹窗类型，选"don't ask again"时优先选带 `*` 的宽前缀那项；编排者也会在汇报里提出来，补进规则。

### `--check` 报某项没装对

`--check` 的输出里，`✗` 是没装对（退出码 1），`!` 只是提示。每一项都带"要做"的说明，照着处理就行；一般重跑 `python3 install.py` 就好（已经装对的项会跳过；copy 模式记得带 `--mode copy`）。常见原因：

- clone 挪了位置或删了（link 模式软链断了）：在新位置重跑 install，或者改用 `--mode copy`；
- 某个位置被别的工具换成了别的文件：重跑 install 会先备份成 `.bak-drover-*` 再换回来；
- 只装了一个宿主，现在要用另一个：`python3 install.py --host both`；
- 必需依赖缺失（herdr、git、python3，claude / codex 一个都没有）：照那一行的"装法"装上，缺 herdr 也可以重跑 `./install.sh --install-deps`（见上面"没装 herdr"）；
- `!` 路由区块过期：`flow/routing.md` 更新过、install 没重跑（比如只 `git pull` 了），跑一次 `~/.drover/bin/drover update`（或重跑 install）刷新区块；
- `!` codex 的 `sandbox_mode`：见第 7 节。

安装时报"没检测到 Claude Code 或 Codex"（退出码 2）：PATH 里没有 `claude` / `codex`，也没有 `~/.claude`、`~/.codex`。按提示里的官方命令装一个，或者用 `--host` 指定（见上面"没装 claude / codex"）。

第一次安装报要用 `--workspace` 指定主工作台（退出码 2）：是在非交互环境里跑的（agent 的 shell、管道、CI、`--json`），带上 `--workspace <目录>` 重跑（选哪个目录见 2.4）。

起编排者时 `orch-start` 报 `/drover` 命令或 `$drover` 技能没装（`~/.claude/commands/drover.md`、`~/.codex/skills/drover/SKILL.md` 不存在）：编排者要用的那个宿主没装上，`python3 install.py --host claude`（或 `codex`、`both`）重跑。

报主工作台不能是家目录（退出码 2）：`--workspace` 给了家目录或它的上级（含 `/`），或者已有 `config.json` 里的 workspace 就是家目录（这时 `--check` 给 `!` 提示）。原因见 2.4。换成放代码仓库的那个上级目录，`python3 install.py --workspace <目录>` 重跑；原来家目录 `AGENTS.md` 里那段 drover 区块不会自动删，手动删掉两个标记之间的内容。

### 找不到仓库

报"找不到仓库 xxx：既不是仓库路径，也不在 … 下"：仓库名是按 `config.json` 的 `repo_dirs` 顺序找的。把仓库所在目录加进 `repo_dirs`（见第 3 节，改完立即生效），或者 `@` 后面直接写路径，比如 `@~/oss/some-repo`。

仓库不在 `workspace` 下面时，Herdr 建 worktree 要求信任仓库，脚本不会自动替你信任，编排者会先问你。

### 装完 `AGENTS.md` 里规则重复了

原来的 `AGENTS.md` 有内容、没有 drover 标记时，install 把区块追加到末尾，并提示"已有内容可能和路由规则重复，请检查"。它不去猜哪段是通用规则。处理：

- 把区块外和路由规则重复的通用内容删掉，只留你个人的部分；
- 想让区块放在别的位置，就把那对标记连同中间的内容挪过去。以后重跑 install 只整段替换标记之间的内容，不再追加。

还没装的话，按第 2.5 节的建议，先放一对空标记再装。

### 想回滚

```bash
python3 install.py --uninstall --dry-run    # 先看会撤掉什么
python3 install.py --uninstall
```

撤什么、留什么见第 2.6 节：只撤 install 加的东西，`config.json`、任务记录 `~/.drover/<slug>/`、worktree、herdr hook 都保留。安装时备份的 `*.bak-drover-<时间>` 不会自动还原，卸载时会列出来；也可以自己找：

```bash
find ~/.claude ~/.codex ~/.drover <主工作台> -maxdepth 3 -name "*.bak-drover-*"
```

`<主工作台>` 换成你的主工作台目录。要恢复哪个，就把它挪回原名。Codex 技能目录的备份在 `~/.drover/backups/codex-skills/` 里，恢复时挪回 `~/.codex/skills/` 并去掉 `.bak-drover-<时间>` 后缀。

### 飞书收不到消息

1. `~/.drover/bin/drover oncall status`，按 `hints` 处理：插件在 herdr 里被禁用（`herdr plugin enable com.codreamer.herdr.oncall`）、插件自己的开关关着（`drover oncall on`）、飞书还没配对（`drover oncall pair`）、`drover.env` 不在或过期（重跑 `python3 install.py`）。
2. `plugin_status.poller.alive` 是 `false`：插件收发飞书的常驻进程没起来。herdr 重启后会自动拉起；也可以手动重启它：`herdr plugin action invoke poll --plugin com.codreamer.herdr.oncall`。
3. 能收到推送、但飞书里回复没反应：`plugin_status.channels.feishu.replies_supported` 是 `false`，Node 低于 22，升级 Node（`brew install node`），再重启 herdr。
4. 只收到主会话的、编排者 / worker 的确认框没推：这是设计（4.9 的通知规则），它们卡满 5 / 10 分钟没人批才推。
5. 插件真实做了什么，看它的日志：`herdr plugin log list --plugin com.codreamer.herdr.oncall --limit 20`。

### 飞书配对失败

- **没弹二维码**（没装 Xcode 命令行工具、面板没编译，或者不是 macOS）：用 `drover oncall pair` 输出里的 `link`，在手机飞书里打开它，效果和扫码一样（终端里装时问的那一句也会打印这个链接）。想要桌面面板，`xcode-select --install` 后重跑 install。
- `link` 是空的、`plugin_message` 里有报错：按报错处理；更多看 `herdr plugin log list --plugin com.codreamer.herdr.oncall --limit 5`。
- 扫完不知道成没成：`drover oncall status` 看 `plugin_status.channels.feishu.paired`；或者一开始就用 `drover oncall pair --wait`，配上了它会自动发一条测试消息。链接有效期十来分钟，过期了重跑 `pair`。
- 已经配对过的机器再跑一次只会回"已配对"，不会新建机器人；要换账号先 `drover oncall unpair` 再 `pair`。

### 飞书租户不让建个人机器人

扫码配对要在你的飞书里建一个只属于你的应用（机器人）。有的企业租户关掉了个人建应用的权限，这时扫码后会提示没有权限：找飞书管理员开通，或者用个人飞书账号配对；实在不行就只用桌面面板（macOS，不配飞书也照常提醒），或者 `python3 install.py --no-oncall` 不接飞书（已经装上的插件不卸）。

### 主会话没登记上

飞书里不引用发的消息回"没找到主会话"，或者 `drover oncall status` 里 `drover.main_agent.registered` 是 `false`：

- 在主会话里跑一次 `~/.drover/bin/drover main-register`，它会说为什么没登记：`conflict`（另一个 pane 已经登记成 `drover`，比如开了第二个主会话：关掉那个再跑）、`named`（这个会话已经有别的名字）、`orch_workspace`（在编排者的 `orch` workspace 里开的会话，不算主会话）、`not_in_herdr`（不是在 Herdr 的 pane 里开的）。
- Claude Code：SessionStart hook 在不在，`python3 ~/.drover/pkg/install.py --check` 里"主会话登记 hook"一项；主会话要在主工作台目录本身开（子目录不算）。
- Codex：没有 hook，主会话在你发第一条消息时按路由规则登记；刚开的 Codex 会话还没说过话时，飞书消息找不到它。
- `/clear` 或退出重开后，herdr 会清掉名字，hook 会在一两秒内重新登记；这期间发的飞书消息会回"没找到主会话"，再发一次就好。

### 其他

- **编排者停了，rollup 却还是"进行中"**：主会话会看一眼它的屏幕，判断是在等后台任务还是卡住了；你也可以 `herdr agent focus <编排者名>` 自己看。
- **worker 好像卡住了**：`watch` 会报 `working-no-output(Nm)`（屏幕几分钟没动）或 `working-no-diff(Nm)`（worktree 很久没改动），编排者会去看、必要时打断并催报告。你不用盯。
- **卡在确认上**：`watch` 报 `blocked`，编排者看屏幕后批，破坏性的才问你。worker 连着弹两个确认时会报两次：同一状态下 herdr 的状态序号变了（中间那下 working 没被轮询到），或者弹框内容换了，`watch` 都会重报，不会再出现"批完第一个、第二个没人管"的空等。Claude Code 的 WebFetch 域名授权弹框（"Do you want to allow Claude to fetch this content?"）herdr 有时报成 done，`watch` 看到屏幕底部是确认弹框就改报 `blocked`。编排者 / worker 派的后台子 agent（Explore 等）弹确认时，主 agent 停在 "Waiting for N background agent(s) to finish"，herdr 按这一行报 working；`watch` 在 working 时也每 10 秒看一次屏幕，底部是确认框就报 `blocked`，子 agent 连着弹的每一个都会报（一直有弹框、herdr 的状态在 blocked 和 working 之间变了，也当作换了弹框重报一次）。唯一认不出的是两个弹框一字不差、中间那下又没轮询到，所以批完都会再看一眼屏幕。
- **被接口错误打断**：`watch` 报 `api-error`，屏幕上最后一个输出块是行首的 `API Error: 503 …`（Claude Code）或 `■ stream error …`（codex）；正文里提到这几个字、或者更早报过错但之后又有正常输出，都不算。这一轮被接口错误打断了，agent 自己不会再动。主会话会等一两分钟再让编排者接着做，编排者也会续上它的 worker。连续 3 次还报错，多半是 API 服务端（或你用的代理）的问题，会告诉你。
- **编排者写完 rollup**：`watch` 对编排者另外单独报一行 `rollup:<状态>`（状态变了就报；"等你决定 / 完成 / 受阻 / 需要换编排者"时内容变了也报，"进行中"只改进度不报）。改没改按内容判断：每行去掉首尾空白、删掉空行后比较，原样重写、只改空白空行不报；不看编排者手写的"更新："行（手写的时间不准，状态没变时还会漏报；这一行现在可以不写）。主会话用的 `watch --main` 再多一层：只报主会话还没用 `drover rollup` 读过的那一版，报过一次就记在磁盘上，重挂、`/clear` 都不重报。编排者写完"等你决定"后会继续挂着 watch 盯 worker（状态是 `waiting-background(N)`），主会话靠这行事件去读 rollup 转给你。
- **想知道某次为什么这么做**：读 `~/.drover/<slug>/dispatch.md`（状态文件），编排者每一步的决定和理由都记在里面。

## 9. 设计取舍与致谢

drover 的几条核心原则：

- **一个任务一个编排者**：主会话的上下文才是最稀缺的资源。所有任务都在主会话里分析和派发，摸底、设计稿、报告、验收输出很快就把它撑满了。
- **只交一页 rollup，不转贴 worker 的原始报告**：主会话和你只看结论，要细节去问编排者。
- **按证据验收**：任务书必须写"完成证据"（什么命令、看到什么结果算通过），验收时脚本自己重跑这些命令，不只看 agent 的自述。
- **复述，但不等确认**：第一条回复就用自己的话复述目标，然后接着干；理解错了你随时纠正。只有推送、删东西、写数据库、部署这类不可逆的事才停下来等你。

还有几条是踩坑踩出来的：

- **一个条目一个 worktree，全量测试只在集成时跑一次**：几个 worktree 同时跑全量测试会把 CPU 打满、用例超时，所以 worker 只跑相关测试，全量测试和上游同步各在集成时做一次。
- **重复操作收进脚本，输出 JSON**：建 worktree、装依赖、起 agent、发任务书、验收，都是一条命令，ID 从 JSON 里读，不靠 agent 猜。
- **流程正文只写一份**：Claude Code 和 Codex 共用 `flow/dispatch.md`，宿主差异放在附录里。
- **只在主会话需要你时才推飞书**：以前单独用 herdr-oncall 时谁弹框都推、每轮回答都推，2 小时 58 条，一大半本来有人接。集成进 drover 后按角色分：只推主会话的确认框、飞书提问那一轮的回答和以 `【转达】` 开头的转达，编排者 / worker 的确认框由上一层批，卡久了才升级。飞书用来接收审批和下达命令，不是把所有对话都转一遍。
- **确认框从源头少弹**：没法加白名单的确认（比如 `cd … && git …` 触发的）只能靠不写出那种命令；子代理看不到规则，就用 PreToolUse hook 拦下来、把改法回给它，让它自己改写重试。

致谢：[Herdr](https://herdr.dev)（牧场是它的，我们只是赶羊的），code-review-graph（验收时的那张图），以及 Claude Code 和 Codex 两位宿主。

默认编码规范（`flow/coding-rules.md`）的通用原则改编自 Robert C. Martin 的 [ub-mode](https://github.com/unclebob/ub-mode)（MIT），原文和许可证见 [`third_party/ub-mode/`](third_party/ub-mode/)。

## 10. 自测

改了 `bin/drover`（或 install.py、hook）以后跑一遍，确认 watch、accept、heavy、wt、integrate、push、wrapup、install、update 这几块没被改坏。改 CLI 照第 2.7 节的开发约定保持向后兼容（旧写法至少再留一个版本）：推到 main 并打了版本 tag（如 `v1.2.3`）之后，用户机器上的自动更新就会装上。在包的根目录（clone 或 worktree）里执行：

```bash
python3 selftest/run.py                        # 全部自测（selftest/ 加 tests/），按用例分给多个进程同时跑（10 核的机器上约 50～60 秒，串行要 7 分钟左右）
python3 selftest/run.py -j 4                   # 指定进程数，默认本机逻辑核数
python3 selftest/run.py test_push tests/test_install.py   # 只跑这几个文件（.py 和目录可省）
python3 selftest/run.py -v                     # 每个用例跑完打一行（结果、耗时）
```

`selftest/run.py` 只用标准库：主进程收集 `selftest/` 和 `tests/` 下 `test*.py` 的全部用例，N 个常驻的 worker 进程从同一个队列里取下一个用例跑（谁空谁取），所以墙钟取决于用例合计耗时和核数，而不是最慢的那个文件。失败的用例随出随打（traceback，加上这个用例收到的 stdout / stderr，包括它起的子进程的输出）；跑完按文件列用例耗时、最慢的 10 个用例、失败的用例，最后一行是 `===== N passed, M skipped in X.Xs (-j J) =====`。退出码：全过 0，有失败 / 出错 1，参数不对 2。集成检查（`accept --verify`）就用 `python3 selftest/run.py` 这一条，不经 shell；它会经 heavy 排队，自己手动跑时机器上还有别的重活可以 `~/.drover/bin/drover heavy -- python3 selftest/run.py`。

各套也能单独跑（单进程串行）：

```bash
python3 -m unittest discover -s selftest -v    # bin/drover 的自测（本机约 2 分钟）
python3 tests/test_install.py                  # install.py / install.sh 的测试（约 50 秒）
python3 tests/test_update.py                   # drover update：快进后按原模式重跑 install、拉不下来时什么都不改、--dry-run、--auto（自动更新）
python3 tests/test_cd_git_guard.py             # cd+git 拦截 hook 的判定、改写建议、出错放行（不到 1 秒）
```

`tests/test_install.py` 也不碰真实状态、不联网：临时 HOME；PATH 最前面是假 herdr（`plugin list / install / update / uninstall / config-dir` 也是假的，只改它自己的状态文件）和假 `node`；"没装 herdr"的用例会把 PATH 里所有含 herdr 的目录去掉，安装命令换成假的安装器（照官方脚本的行为把假 herdr 放进临时 HOME 的 `~/.local/bin`），PATH 里还放了一个只记日志的假 `curl`；其余用例的安装命令一律换成"禁止安装"，谁意外触发了安装，用例就报错。"没有 Node"的用例同样把 PATH 里含 node 的目录去掉。交互终端里"先问一句"的分支（缺 herdr 时问要不要装、装完问要不要配对飞书）用伪终端测；配对用例里假插件的 status 脚本按假 herdr 的状态报飞书配没配，假 `qrencode` 只打印一行。`tests/test_update.py` 同样用临时 HOME，远端是本地的 bare 仓库，不联网、不碰真实状态；`--auto` 的用例覆盖快进成功（连着两次没报的并成一次）、已是最新（不重跑 install）、有改动 / 分叉被挡住、远端没了、`DROVER_AUTO_UPDATE=0`、锁被占着，每种都核对 `auto-update.json`；无人值守那条把远端换成 `ssh://`、PATH 里放假 `ssh`（记参数，报连不上或一直挂着），核对 `BatchMode=yes`、`GIT_TERMINAL_PROMPT=0`、只试 1 次、用户自己的 `core.sshCommand` 不覆盖，挂着的那条在进程里把 60 秒超时换成 2 秒（只核对报错里的"命令超时（2 秒）"和只试 1 次，不数假 ssh 被调了几次、不量墙钟：机器忙时 2 秒内 git 可能还没起到假 ssh）；要等的另外两条也在进程里跑：远端没了那条把重试前的 sleep 换成只记下来（仓库不存在不会自己好：核对只试 1 次、一次都不等），锁被占着那条把等锁上限从 5 秒改成 0.3 秒。手动 `update` 换了版本也写 `auto-update.json` 的 update（source manual、announced true，上一次自动检查的 result / last_attempt / finished / error 原样）；之前有一次自动更新还没报的并成一次、announced 保持 false；没有新提交、`--dry-run`、被挡住、update_run 抛错都不写；上一次已经报过的不并；copy 模式 install 复制副本失败（clone 新了、副本标记还是旧的）不记、看板第二行没有"已更新"，接着重跑成功（clone 已是新的，副本换成新的）记旧副本版本→新版本、标题按这一段现算。

只跑一个文件：`python3 selftest/run.py test_push`，或者 `python3 -m unittest discover -s selftest -p test_push.py -v`。只用标准库，不用装包。`selftest/` 下没有 `__init__.py`，入口就是上面这条，不要加 `-t .`；公共工具在 `selftest_support.py`（临时环境、执行 / 加载 `bin/drover`、假时钟和在进程里跑命令）、`selftest_fakes.py`（假 herdr、假测试工具，测联网重试的用例按需装的假 ssh）、`selftest_repos.py`（integrate / push 用的仓库布局）、`selftest_screens.py`（watch 用例的真实屏幕：确认弹框、正文引用、后台子 agent 的确认框；启动弹窗用例的信任框）。

覆盖了什么（一个文件一块）：

| 文件 | 覆盖 |
|---|---|
| `test_vitest_recheck.py` | vitest 负载超时复核：只超时 → 单独重跑失败文件，通过就放行；超时 + 真失败、复核还失败、计数对不上都不放行；重跑命令去掉位置参数、保留选项的值；`../../..` 开头的路径换成相对 worktree 的 |
| `test_baseline.py` | `--verify-baseline`：vitest / pytest / tsc / eslint 四种解析（包脚本、绝对路径、只算 error）和认不出的输出；和基线比失败集合，新增列出、修好的进 `fixed`，第二次走缓存，新增全是超时先复核。基线只重跑失败的文件（假工具记下的参数为证）：vitest / pytest 按失败所在的测试文件、eslint 按报错的文件，选项保留、目录参数和 vitest 的 `--changed` 去掉，tsc 照旧整条；失败的文件 BASE 上没有 → 不放进基线命令、算新增，全都没有 → 不建基线 worktree；目标文件不同不命中旧缓存；失败路径对不上 worktree 里的文件、经 make 包装的命令 → 退回整条命令 |
| `test_accept_cli.py` | accept 端到端：`--verify` 和 `--verify-baseline` 对同一条失败命令的不同结论；不建图；`--no-graph` 不调用 code-review-graph（PATH 里放了只记日志的假命令为证）、`graph` 标 `skipped`，`--brief`、`accept-summary`、`review --dry-run` 读这份存档不报错、不当成"图谱不可用" |
| `test_integrate.py` | integrate：从最新上游建、按顺序 cherry-pick、重跑全跳过、冲突停下 → 解完重跑跳过；cherry-pick 被未跟踪文件挡住（git 没开始就拒绝）→ 记 `failed`，挪走后重跑真的应用上；条目的改动已被上游带进来（空提交）→ 跳过、记 `empty`，中文 locale 下照样认得出；新建时快进源仓库主检出、目标分支不重复 fetch；主检出的上游在远端已删不影响集成；条目 worktree 有没提交的改动时拒绝；`--dry-run` |
| `test_wt_sync.py` | wt 新建前同步源仓库主检出：落后 → 快进、从最新起；已是最新；本地领先 → 从 HEAD 起并提示；有已跟踪改动、分叉、快进被未跟踪文件挡住 → 主检出不动、从 `origin/<分支>` 起、`warnings` 写原因；`--base origin/<别的分支>` 先 fetch；和上游同一条只 fetch 一次；fetch 失败 → `ok: false`、不建，`--no-sync` 能建：远端仓库没了（永久错误）只试 1 次、不等，偶发错误（假 ssh 前两次报连接被重置）隔 5 秒重试、第 3 次成功照常建（假时钟）；`--no-sync`、`--dry-run`、复用已有 worktree 都不 fetch 不快进；没配上游回退 `origin/<分支>`、远端没有这条分支、detached 当作没有上游；配了上游但远端已删 → `upstream_gone`、不报错（不带 `--base` 从 HEAD 起并提示，带 `--base origin/<别的分支>` 从它最新起；中文 locale 下 git 的报错是中文，照样认得出）；主检出不在远端默认分支（`origin/HEAD`）上 → 提示一句（`--dry-run` 也有），在默认分支上、上游是默认分支、显式 `--base`、本地没有 `origin/HEAD` 都不提示；同一仓库的同步锁被占着时等待。`.env*` 复制：源仓库跟踪、基线分支上没有的不复制，跟踪但在新 worktree 里被忽略的照复制，没跟踪的照旧复制。`.worktreeinclude`（照 order-service 这类 Java 服务仓库的布局）：目录模式 `tools/` 和通配模式 `config/*.yml` 都复制，跟踪的、`!` 取反的、匹配了但没被忽略的不复制，和 `.env*` 重复的只复制一次；源仓库忽略、基线分支不忽略的不复制、列进 `worktreeinclude_skipped`；符号链接跳过；复用时补复制、不覆盖已有的；`--dry-run` 逐个列出匹配文件；没有这个文件时 `would_copy`、`copied` 和以前一样 |
| `test_push.py` | push：直接推并 `ls-remote` 核对、推送记进 `pushes.jsonl`（不再写进集成状态）；没有集成状态（patch 模式，wt 建的集成 worktree）也记，字段齐全，任务按分支名归属；定不了任务就不推、提示加 `--slug`，`--slug` 写错报没有任务目录；上游只改文档 / 改代码 → rebase、`needs_reverify`、base 回写（integrate 再加条目时读到新 base）；add/add 冲突 → 人工解完先停一次再推；手动 rebase 标 `manual`；push 遇到永久错误（推送地址指向不存在的仓库）只试 1 次、不等，遇到偶发错误（假 ssh 第一次报会话被对端关掉）隔 5 秒重试后推出并核对 |
| `test_wrapup.py` | wrapup：同一仓库两份 integrate 记录（不同上游分支，一份是归档名）→ 两行，同一上游的两份（复制归档）合成一行、推送只算一次；不改名归档、integrate 重建集成 worktree → 两条分支的推送都在，旧代码记在集成状态里的推送重建前转进 `pushes.jsonl`；patch 模式的推送（没有集成状态）列成一行、骨架的提交表有它；子编排者的推送记录带上；没有推送记录给提醒、没有状态文件报错；远端核对（远端头就是推送的提交、之后又有新提交、不再包含、`--no-remote`）；迁移 / 配置 / 依赖文件分类和新增配置键引用（删除行不算）；`--draft` 骨架和已有文件拒绝覆盖；待清理 worktree 和 `dirty` |
| `test_round_start.py` | 新一轮的起点：有推送记录时不传 `--base` 从上一轮推的 `origin/release` 起（不跟主检出的 main）、按祖先通过；没有推送记录带不带 `--slug` 起点都和以前一样；换到不含上一轮提交的新分支 → `ok: false`、不建，列出缺的提交（短 sha、标题、推到哪）和两种做法，`--dry-run` 只预测；`--carry` 建在新分支上、先 cherry-pick 缺的提交、`head_sha` 在它之后、理由记进 `carry.jsonl`；新分支里有挑过去的副本按补丁算通过；推过两条分支不传 `--base` 报错列出两条；integrate：`--from` 里没有上一轮的提交被挡（选项 `--target`），`--carry` 能建，条目 worktree `--carry` 过的按 `--from` 算覆盖；commit / patch 两种模式带上之后推到新分支上都不漏 |
| `test_cleanup.py` | cleanup：commit 模式条目（提交挑到集成分支推了）、干净的集成 worktree → 删 worktree、本地分支、关 workspace 里的 agent，不带 `--force`；patch 模式条目（改动没提交、内容已推）→ 带 `--force` 删；推上去之后远端又改了同一处 → 在推上去的那个提交里找到、照删，从没推过的不删（"在它们的历史里也没找到"）；只给 `--target` 时按碰过这些文件的提交往回找，两处改动分两次推、之后又改了一处 → 逐个文件落在各自的提交上、照删，往回查超过上限的核实不了、不删；严格比对不上、三方合并判包含的照删（远端在改动旁边加了行、表格相邻处两个条目各加一行、同一行里远端解冲突时又插了字、同一长行两边都改了（远端另外换掉了条目那边留着的原词）、commit 模式 cherry-pick 解过冲突、照一张 README 自测表：相邻几行两边各改、其中一行两边都改（条目改前半、远端行尾加一句）、条目还在中间插了两行（以前整块按词对齐误判成不在远端）），判不包含的不删（加的一行在文件别处出现过、远端同一处改成了别的、删掉的行远端还在、长行里同一个词两边改成不同的、条目新写的词远端没有；同一张自测表里：别处有的行插在这一处、同一行两边改成不同的词、条目新写的行远端没有、条目删掉的行远端改过几个字还留着）；没推的改动（列文件）、没推的提交（列 sha 和标题）、agent working（cwd 在里面）/ blocked（在它的 workspace 里）、cherry-pick 进行中 → 不删、写原因；`--dry-run` 只列、不调 `worktree remove`；`--probe` 先存 commits / worktree 两份 patch（能在远端起点上还原）再删，agent 在干活的不删不存，不配 `--wt` 不让跑；workspace 没开 → `git worktree remove`；没有推送记录不删，`--target origin/main` 补上核对分支后删，`--target` 写错不删；没有 worktree 的分支按同样核实；主检出、别的任务的 worktree 不动 |
| `test_heavy.py` | heavy：整机实测满了（假采样）时第二条排队并打印在跑的那条、第一条结束后开始；退出码透传；调度目录不可写时直接执行；accept 的验证命令进同一套调度；只执行构建 / 测试工具（`env JAVA_HOME=… mvn`、开头直接是 `JAVA_HOME=…`、`./.venv/bin/python -m pytest` 能跑，`git push`、`rm`、`bash -c` 退出码 2、没执行也没进调度） |
| `test_heavy_sched.py` | heavy 按资源调度（按实时 CPU 放行）：放行判定（读不到整机时按预留：合计放得下同时跑、超过上限排队并写原因、按独占的在别的只占零头时就开跑、小命令不计入、排头等太久时后来的不能插队、内存紧张只放单独一条；按实测：机器空着时"独占"的在跑也马上放、它刚放行时按真实核数算、进程树实测满了才排、整机被调度外的负载占满不往上加、没有 heavy 在跑总放一条、没有历史的先按 1 核跑起来按实测、没有 ps 时按预留但不超过整机实测、防饿死只放能在排头开跑前跑完的）；整机 CPU 的读法（采样窗口、两次采样才算数、32 位回绕、读不到、假采样文件、本机真的读得到）、进程树 CPU 的算法、ps 时间列；需求先取这条命令自己的实测最大值、没有就用同形状的、再没有用同程序的、都没有先按 1 核（同形状里有重的，轻文件自己跑过就按自己的算小命令、不排队）；命令形状（任务书点名的 7 条命令、同仓库各 worktree 相同、不同仓库和子目录不同、`-Dk=值`、提交、文件个数分档、带通配符的值不算文件）和同一条命令（`-p a.py` 和 `-p b.py` 不是同一条、另一个 worktree 的同一条相同）；历史条数上限。端到端（容量和 1 秒宽限期写进临时 config、实测直接写进 history.json、整机实测是假采样文件，假命令按标记文件开始 / 放行）：两条轻的同时跑；整机满了排队、打印要多少 / 整机实测 / heavy 占用 / 谁在占，`--status` 看得到，整机空出来不等前一条结束就开跑；没有历史的不独占、票里写进进程树实测、跑时别的轻命令和小命令照样进；codex 沙箱的退化（假 ps 失败：按预留，整机读不到时排队、读得到时压回去放行）；持有者被 SIGKILL 后回收；SIGTERM 结束子孙进程、退出码 143；记下的 CPU 时间含孙进程（孙进程按 CPU 时间空转，机器忙时也不偶发失败）。accept：两条轻 `--verify` 时间上重叠、结果顺序不变，`--serial` 逐条；`--no-lock` 不进调度；建图、基线上那一步、超时复核进调度，建基线 worktree / 复制 / 装依赖时没有排队票。codex worker 的 `--add-dir`、brief 的新约束。容量校准（硬件值和逻辑核数是造的，样本和时间也是造的）：连续 2 个明显变慢降一核、1 个不动、负载很低的变慢不算；连续 3 个接近容量几乎不变慢加一核、不超过逻辑核数、中间夹明显变慢重新攒；降下来过的值再升回去要 2 倍的样本；换了硬件值重新起步；从历史算样本（同一条命令单独跑的基准取中位数、按时间加权的实测负载含自己、小命令和退出码对不上的不算）；基准只认同一条命令（参数不同的同形状命令不互为基准）、单独跑不到 2 次不出样本、单独跑本身抖（最慢 ÷ 最快 > 1.2）不出样本；旧版本的校准状态（混了不同命令的基准降下来的容量和 fails）作废、容量回到硬件值，之后不同文件同时跑也不降；config 固定容量不校准；被杀、超时不记；`--status` 显示硬件值、现在用的值、调整依据和样本（完整命令、基准次数和中位数） |
| `test_watch_states.py` | watch 认出后台命令（`· N shell` / `· N monitor` → `waiting-background(N)`）和 `api-error`（行首的 Claude Code `API Error: 503`、codex 的 stream error，而且得是最后一个输出块；正文里提到、更早的块报错都不误报）；herdr 报 done 但屏幕底部是确认弹框（真实样本：WebFetch 授权、Bash 确认框，带 / 不带会话名横线）→ `blocked`，正文引用弹框文字不误报；正常 done 不误报 |
| `test_watch_repeat.py` | 同一状态重报：blocked / done 时 herdr 序号变了、blocked 时弹框换了（并行两个工具调用）→ 再报一次，`--until-event` 第二次时退出；两样都没变、弹框上方 `⏺` 闪烁、同一个 `waiting-background(N)`、working 都不重报；弹框指纹（保留普通数字，抹掉时长和时钟） |
| `test_watch_subagent.py` | herdr 报 working、屏幕底部是后台子 agent 的确认框（两屏真实样本）→ `blocked`（`--once`、`--until-event`）；正文引用弹框、只有 Waiting 行不误报，codex 不读屏；子 agent 连着弹：弹框 → 消失 → 下一个、直接换成下一个、herdr blocked → working 换弹框、同一弹框下 herdr 原始状态变了都再报一次，同一个弹框不重复报；节流（默认 5 秒一轮、10 秒读一次）和 15 秒内报出、卡在弹框上的时间不算进 working-no-xxx（假时钟）。orch-status 的 `screen_status` / `blocked` 和读屏失败按原值在 `test_orch.py` |
| `test_watch_rollup.py` | rollup 事件：有 orch.json 的 agent 报 `rollup:<状态>`（编排者挂着 Monitor 时也报），状态变化 → `--until-event` 退出；按内容判断：状态没变、内容变了，并且是"等你决定"等要处理的状态就再报（"更新："行没变、没有这一行也报），原样重写、只改空白空行不报，"进行中"下不报；`--once` 输出当前值；没有 orch.json / rollup.md / 状态行的不输出；watch 起来之后才写的 orch.json 能跟上；同名取修改时间最新的 |
| `test_watch_main.py` | `watch --main`：没有新东西时启动不输出；未读的"等你决定"报一次，重挂不再报，主会话用 `drover rollup` 读过之后也不报，编排者的 pane、普通终端读过照报；原样重写、只改空白不报，内容变了报；working / waiting-background / `rollup:进行中` 不报；同一个弹框跨重挂只报一次，弹框换了再报；`api-error` 报一次；意外 `gone` 报一次，完成之后的 `gone` 不报；`stalled`：进行中停着、转过答复却没更新，到 `--idle-minutes` 报一次（很小的阈值）；`--until-event` 第一轮有输出就退出；中途新出现的 orch.json 能跟上；不盯子编排者；每一轮记 `agent-state.json`，和看板记的一致（各种状态 watch 先记看板再看、看板先记 watch 再看，文件都不变）。drover 自己的事件在 `test_auto_update.py` |
| `test_auto_update.py` | 自动更新（2.7）在 `bin/drover` 里的部分。tick（进程里跑，后台命令换成假脚本）：`DROVER_AUTO_UPDATE=0` / `false` / `off`、`"auto_update": false` 关；间隔（默认 30 分钟、`auto_update_minutes`、记录的时间在将来）；锁被占着不起；起了不等它（假进程被挡着、等用例放行，tick 照样不到 1 秒返回），后台进程在新会话里、stdin 是 `/dev/null`、输出进 `auto-update.log`。调 tick 的只有 board 文本 / `--plain` / `--json` / `--live`、`watch --main`、main-register（先记规则再触发；源码里扫一遍调用方）。`drover rules`：第一次给整个区块、没变一句话、改了只给差别、改动过半给整个区块、`--full`，读完记成载入时、标 announced（幂等），没有区块 / 没配主工作台报错，区块标记和 install.py 一致。`rules --hook`：别的会话不输出、规则没变不输出、变了输出一次，坏的 stdin 也退出码 0，常见路径不跑 git、不调 herdr。`watch --main` 的 `drover updated(…)[ rules]`、`update-blocked`（同一个原因一次）、`update-unreachable(24h)` 各报一次、重挂不重报；`session-restarted`：clear / startup 报、compact 不报；两个 watch 同时跑，会话重开、自动更新、编排者的未读 rollup 都只报一次。看板第一行的版本、"已自动更新""自动更新暂停"两行，手动 update 的"drover 已更新 a→b（时间，手动）"（不接自动更新的 error；旧记录没有 source 当自动），`--json` 的 `drover` 字段（含 `auto_update.source`） |
| `test_board.py` | `board`：一行一个任务，按显示状态分组（等你决定 / 受阻 → 等你；进行中 / 已转答复 / 需要换编排者 / 没有 rollup → 进行中；完成 → 完成；空组不画），组内按状态持续长的在前（转过答复的按转答复的时间，不按 rollup 修改时间）；当前状态取"摘要："（没有就按状态取正文那一行的第一句），受阻、换编排者加前缀，已转答复写"已转答复，等它接着做"；任务取 rollup / request.md 的"标题："、再没有用代号；要你做什么取"要你做："、等你决定 / 受阻的旧 rollup 取"答法："、没有写通用动作、别的和已转答复写"无"；提醒跟着任务走（几个任务写了同一句各自都写），不再读"## 交付"（只进 `--json` 的 deliverables，没有是空的）；每格上限（Markdown、`--pending`：任务 24、当前状态 40、要你做什么 30、代号 24，中文两格，截断加 ASCII 的"..."算 3 格，状态持续、收尾不截；`--plain` 只截任务名）；折行：`wrap_width` 按显示宽度折，短的一行，英文单词、数字、路径（带 `~`）、sha 不从中间断，比一行长才硬断、剩下的接着排，行首不留空格，超过 3 行第 3 行末尾截成"..."；中文禁则：句末标点（全角，。；）」 等、英文 , . ; ) 等、"…"换成的"..."）不在行首（把上一行最后一个字 / 词带下来，连着两个标点再带一个），开括号、开引号不在行尾，带完上一行空了照原来断，以点开头的路径不算标点，主会话看到的"drover 三件修好、集成检查过；等 web-app 那件"28 格折成第二行"过；…"，看板 tab 的框线表里也是；三个示例任务的 `--live` 宽屏逐行比对（完成表那一行当前状态、要你做什么各折成两行，列宽 28 / 22），几行的任务只在第一行有任务名、代号和 [收尾]，任务之间横线、几张表竖线对齐，[收尾] 点第一行点得中、下面几行点不中，往右滚了以后照样；哪种写法、哪种宽度都没有"…""◀""▶"；表格的格子里宽度不定的字换成宽度确定的（`board_ascii`：表里的 … — “” · → ① 等，NFKC / NFKD，再不行写 "?"），rollup 原文带这些字时 `--live`、Markdown 的表格行除了框线没有宽度不定的字、竖线每一行一致、收尾原因按换完的宽度截、[收尾] 照样点得中，`--plain`、`--pending`、`--json` 是原文；状态持续的写法；原来"要主会话处理"的收成最底下一行，完成且已退出、子编排者不显示；`--json` 的字段（加 title、ask、display_state、state_since、state_seconds、total_seconds、wrapup_clicked、note）；提醒：三个示例任务的 `--live` 宽屏逐行比对（提醒写在那个任务最后一行下面，任务列空着、后面几列合成一格，和任务行之间有一条线，合并的几列在线上收成 ┴，提醒下面是底线时合并处一条直线、后面还有任务时下一个任务的列从这里分开（┬），表宽 116 不变），100 格逐行比对两张表（合成的一格 66 格、提醒折两行），往右滚过一列以后跟着变窄重新折、两条线跟着画出来的列画，只画了任务列和一列时那两条线没有 ┴ / ┬，提醒行和它上下那两条线以外竖线都对齐、线上的 ┴ / ┬ 正好在上面 / 下面那几列竖线的位置，底下没有单独的提醒段；两个任务写了同一句各自都有；很长的提醒折 3 行、末尾"..."，表宽不变；完成表里带提醒的任务和它下面那个任务的 [收尾] 都点得中（宽屏、滚过以后、只剩收尾列时）、点提醒那几行和它上下那两条线不送；窄屏列表、`--plain` 的第三行"  提醒：…"；Markdown 每张表下面"提醒（标题）：…"、只列本表的、标题按 24 格截、"|"转义；`--json` 的 note；`--line`（等你 / 进行中 / 完成，不读屏、不写文件）；`--pending`（"· 标题（代号）：要你做（已等 …）"，最多 3 个加"...还有 N 个"、`--except` 可以写多次、没有就不输出）；三个示例任务的 `--live`（宽屏）逐行比对：组间空行、任务之间横线、写长了的格子折行、两张表列宽一致、竖线对齐，Markdown、`--plain` 逐行比对；收尾列五种写法（[收尾] / 收尾中 / 已收尾 / 已收尾，留 …（"收尾：先别，已经收尾过了，留 …"，截到 10 个字）/ 先别：原因；后三种不能点、不在 `wrapup_ready`、[全部收尾] 不算）、点了 30 分钟变回、rollup 在点了之后重写按 rollup、[全部收尾] 的个数不算点过的、`wrapup_ready` 照样有点过的。横向滚动：`board_columns`（放得下全画、偏移夹住、任务列加最宽一列放不下退回列表）、100 格时就是往右滚了一列的样子（任务列固定、几张表一起滚、占两行的任务滚过以后照样两行、提示行、[<] [>] 能点、[收尾] 滚出屏幕就不能点、不切半个字）、没开鼠标只写还剩哪几列、窄屏列表、`board_draw` 夹偏移和滚屏行数。时长（假时钟：把 `agent-state.json` 的 since 往前拨）：rollup 15 分钟前写、1 分钟前才卡在确认框 → "卡在确认框 1 分钟"、不出现 15 分钟；第一次看到写"刚发现"不编时长；状态不变 since 不动、时长跟着涨；批完又弹一个（序号变、弹框换）、只是序号变、只是弹框换都重新算，⏺ 闪烁和读屏失败不算；接口错误、编排者已退出同样按状态本身；未读加卡在确认框各带各的时长，"停了 N 分钟没更新"、要换编排者后面不再接时长；`--json` 的 `reasons`（kind、text、since、since_source）。`--pane`：新建（split + run）、复用、主会话没登记就报错、`--dry-run`；`--live` 补登记：主会话 pane 里的 agent 没名字 → 改成 drover 并留一行提示，别的 agent 已经叫 drover、名字是别的、pane 里没 agent、没有 `board-pane.json` 都不改。`--tab`：新建（tab create 带 `--no-focus` + pane run，记 `board-tab.json`）、复用、看板 tab 被关了 / 主会话挪了 workspace 重开、主会话换了 pane 跟着改记录、主会话没登记报错、`--dry-run`、顺带关掉 `board-pane.json` 记的旧分屏（新看板起来之后才关；已经不在了只删记录）、pane run 失败不留记录；`--live` 在看板 tab 里：tab 名按计数变（"看板·等你1·完成1"，进行中不写）、没变不重复 rename，不在 herdr 里、在别的 tab 里不改名；按 `board-tab.json` 补登记。看板 tab 里（伪终端里跑真的 `bin/drover`）：开鼠标上报（1000、1002 拖动、1006 SGR）、cbreak；普通按键、没按下的松开和拖动、右键、竖向滚轮、点在按钮以外 → 什么都不做；点（按下、松开在同一格）某一行的 [收尾] → 假插件收到"收尾：<代号>"、看板上显示 notice、不切 tab、那一格变"收尾中"、记 `wrapup-clicked.json` 和时间线、再点不送；点 [全部收尾（1 个）] → 只送没点过的、切到主会话的 tab；横向滚动：SGR 67 往右、Shift+66 往左、→ ← 键（滚到头不再动）、点 [>] [<]，滚过以后点占两行的任务的 [收尾] 送得出、点它下面那一行不送，原始鼠标序列记进 `board-mouse.log`（只留 50 条，拖动不记）；拖选复制：按住左键拖过几行 → 照原样重画、选区反色（首行从按下那一列到行尾、中间整行、末行到现在这一列，中文字盖住左半、右半都算整个），不送东西；松开 → OSC 52 的 base64 解出来正好是选中的字（每行去掉行尾空格），底下"已复制 N 行"、高亮消掉；反着拖复制的一样；从 [收尾] 按下拖到 [全部收尾] 上松开只复制、不送不切 tab，没拖动的单击照旧送收尾；在空白处拖不复制；带提醒的任务：点提醒那一行和它上面那条线不送、拖选跨过那条线和提醒那一行复制出来的正好是画出来的几行、它和下面那个任务的 [收尾] 都送得出；拖动中（`--interval 0.3`）1.5 秒里不重画、不调 herdr、心跳不动，松开以后接着刷新；`board_selection` / `board_cut` / `board_copy` 的规则（反着排、滚出屏幕的行数、拖到最后一行下面、同一格不算选中、一个字都没选中不写剪贴板）；Ctrl-C 和 `--iterations` 退出都关掉鼠标上报、恢复终端属性；不在看板 tab 里不开鼠标；输入的解析（按下、拖动 32、松开，66 / 67 带修饰键、方向键，中键右键不算）。送进主会话（参数、工作目录、不带 [看板] 前缀、显示插件的 notice、出错写原因、没送成不算点过）和降级（没装插件、没有 `send-main.mjs`）。`drover rollup`：主会话第一次读、内容没变、改了几行，写 `main-seen.json`；别的 pane（编排者）读开头多一行"不是主会话（原因），没记已读"、其余和主会话读到的一字不差，`main-seen.json` 和 main-read 都不写、看板照样算未读，认主会话不调 herdr；不在 herdr 里、没有 `main-session.json`、记录里没 `pane_id` 都不记，Codex 的记录照样认；没有 rollup 报错；`--name` 找得到、找不到、同名取最新；prompt 发给编排者时写 `answered.json` |
| `test_board_reexec.py` | 看板换新代码（2.7）。`board --live` 在伪终端里跑临时目录里的一份 `bin/drover`（看板 tab 里，`--interval 0.3`）：link 模式（临时 clone）版本没变时跑几轮都不重新执行、心跳是这个进程 / pane / 版本；提交一版新代码（第一行改成"drover 新看板"）→ 同一个进程换过去，先关鼠标上报、恢复终端再重新打开，第一行版本和心跳跟着变，Ctrl-C 后终端属性和启动前一样；提交有语法错误的代码 → 不换、没动终端、看板上多一行"没换成新代码…SyntaxError…；接着用 <旧>，下一轮再试"，再提交能跑的 → 下一轮换过去；copy 模式（副本有 `.drover-copy`）clone 快进了、副本没重装不换，重装好换过去。假的版本来源加假时钟：版本没变、启动时 / 后来取不到都不换，变了每轮去换、原因那一行紧接第一行，变回来那一行没了；`os.execv` 报错返回原因、开着鼠标时关了再开，试跑没过不 exec、终端不动。`board --tab` 复用（假时钟、假版本）：心跳新、版本对、刚好 120 秒不重启，装着的版本或心跳里的版本取不到不重启；没有心跳、别的 pane 的、版本不对、121 秒没更新 → `pane send-keys <pane> ctrl+c` 再 `pane run`，返回 `restarted` 和原因；`--dry-run` 只列两条命令；`pane run` 失败报错。`--live` 只在看板 tab 里写心跳 |
| `test_timeline.py` | 时间线 `<slug>/timeline.jsonl`（统计数据）：命令当场记——`orch-start` 派发（start）、`--args "resume …"` 换编排者（replace，老任务先补一条估计的派发）、子编排者也记、`--dry-run` 不写；`drover prompt` 发给编排者记 answered（开头 20 个字），以"收尾"开头的再记 wrapup-sent，发送失败、发给 worker 不记；`wrapup` 只在 `--draft` 写成时记；主会话用 `drover rollup` 读到新内容才记 main-read（编排者读不记）；看板点收尾记 wrapup-clicked 和 `wrapup-clicked.json`。观察者补——显示状态变了才记 state（时间是 rollup 修改时间 / 转答复的时间 / 没有 rollup 时的派发时间，不是看到的时间；同一状态重写不记），`--json` 的 state_since / state_seconds / total_seconds（完成的算到这一段完成开始）；先看板后 watch、先 watch 后看板、4 个看板加 1 个 `watch --main` 子进程同时跑都只记一次；编排者每换一段状态记 agent（working / 确认框带弹框指纹 / done / 接口错误 / 退出）；停了没更新满 10 分钟记一次 stalled，等你决定的不算；观察中断（超过 60 秒才记、第一次不记、时间不往回走，`--line` / `--pending` 不算观察）；老任务补派发（request.md 和 rollup.md 里早的）和估计的状态，只补一次 |
| `test_watch_progress.py` | `working-no-output` / `working-no-diff`（阈值 0.02 分钟、假时钟，`--until-event` 退出；在跑命令、输出在变、worktree 在变时不报），屏幕输出指纹和 worktree 指纹；取指纹的 git 超时（刚进 working 就超时、取到一半超时、一直超时）→ watch 不退出、这一轮不算有没有变化，之后取到照常报 `working-no-diff` |
| `test_git_nolock.py` | drover 起的只读 git 不改写 index、不拿 index.lock：已跟踪文件只改了修改时间时，watch 取进度、accept 的 `status` 和 `diff <base> --stat`、集成列冲突文件（`diff --name-only --diff-filter=U`）跑完 index 和 git 目录都没变、输出和改之前的写法一样，对照（不带变量的原始 git）会改写 index；accept 在 `add -N` 之后显式刷新 index，detect-changes 默认写法的 `git diff --name-status <base>` 不再改写 index、不列只改了时间的文件；`git()`、`git_bytes()`、cleanup 的 `git worktree remove`、accept 的 detect-changes 的子进程环境带 `GIT_OPTIONAL_LOCKS=0`（外面是 1、调用方另给 env 时也是）；git 超时先 SIGTERM 进程树、等宽限期再 SIGKILL：`git commit -a` 卡在一直睡的 pre-commit hook 上（持着 index.lock），`git()` / `git_bytes()` 短超时后报命令超时、不留锁、hook 一起停，对照（原来的 `subprocess.run(timeout)`，SIGKILL）留锁；不理 SIGTERM 的假 git 宽限期后被 SIGKILL，总耗时有上界 |
| `test_isolation.py` | 隔离本身：`DROVER_STATE_DIR` 生效，herdr 解析到假的；写进任务书的 CLI 入口是字面的 `~/.drover/bin/drover`，和 install.py 的白名单对得上 |
| `test_orch.py` | 编排者相关：`orch-start` 的开场提示（claude / codex / 子编排者）、要用的 `/drover` 命令或 `$drover` 技能没装时报错、复用 `orch` workspace、写 `orch.json`；宿主不给 `--kind` 时跟主会话走：`orch-start` 按调用方 pane 的 agent 定宿主、`default_kind` 不受显式 `--kind` 影响、子编排者取上层的 `default_kind`、认不出又没给 `--kind` 报错，`agent-start` 先用 `default_kind` 再用调用方；slug 不能用保留名；`orch-status`（含按屏幕修正的 `screen_status` / `blocked`、读屏失败按原值、带"更新："行的旧 rollup；`rollup_summary` / `unread` / `answered_after_rollup`；`needs_attention` 只算未读（编排者读过不算读过）、状态按前缀匹配、完成且已退出的不算）；发 prompt 带 `--until working`，`started` / `settled` / `timeout` / stalled / blocked 的结果和 hint |
| `test_config_missing.py` | 主工作台没有默认值：没有 `config.json` 时不信任任何目录、只按路径找仓库，`orch-start` 报错要你先跑 install，`preflight` 报出缺配置，`wt` 不自动信任；有配置时 `repo_dirs` 默认是 workspace |
| `test_startup_dialogs.py` | 启动弹窗：`agent-start`、`orch-start` 起的 claude 停在 `--add-dir` 的 "Adding a new directory" 信任框（真实样本）→ 先按 down 把光标从 "No, exit" 移到 "Yes, add it" 再回车，等到 idle，`startup_dialogs_handled` 记 `trust_add_dir`，`orch-start` 之后照常发开场 prompt；问句折行也认；旧的 "Do you trust the files in this folder?" 照样认成 `trust_folder`；认不出的界面（Bash 确认框、标题是 "Moving to a new directory:" 的同类框）返回 `blocked`、不按任何键 |
| `test_playbook.py` | 团队 profile 和 playbook：config 的 `profile`（按名字 / 按路径 / 目录不存在报错）、`playbook_dirs` 排在团队之后、`drover playbook --repo`（`coding_rules` 里包默认那份排第一、`default_coding_rules` 关掉就不列，`default_coding_rules.sections` 报出会带哪几节）、brief 追加编码规范（包默认 `flow/coding-rules.md` 排在团队 profile、个人覆盖前面，`default_coding_rules: false` 时只少这一节；通用原则总在，有 `pom.xml` 才带 Java 一节，有被跟踪的 `.sql` 或依赖清单里有 mysql 才带 MySQL 一节、只有 ORM 不带，分节标记和注释不进任务书；团队规则两种 kind 都加，CLAUDE.md 那节只给 codex）、`third_party/ub-mode/` 的原文和许可证与上游一致（按 sha256 核对）、约束里的"不执行迁移、不写共享 / 生产数据库"（两种 kind 都有）；brief 的摸底模板和完成协议的篇幅要求、根治约束（摸底的根因链和证据标准、报告的 `root_cause` 写哪一层、为什么并附证据位置）、事实判断的证据约束（现象带原因、外部平台查官方文档、先核实）、heavy 写成字面的 `~` 路径、`--send` 的等待参数；brief 不给 `--kind` 时按收任务书的 agent 的实际宿主写（agent 没起时用 orch.json 的 `default_kind` 再用调用方），显式 `--kind` 对不上进 `warnings`（PATH 里是假 herdr） |
| `test_main_register.py` | main-register：没名字 → 改名为 drover；已经是 drover → 不动；别的 pane 已登记 → 不动并报出；自己叫别的名字、在 orch workspace 里 → 拒绝；`--hook`：工作目录不是主工作台直接结束（不调 herdr）、软链进的主工作台也算，先等 herdr 换上 stdin 里的 session 再判断（模拟 `/clear` 后旧名字过一会儿才被清掉、手动起的会话一开始查不到），任何情况退出码 0、stdout 为空；记下载入时的规则（`main-rules.json`）、登记上了写 `main-session.json`（`/clear` 后换成新的 session、已经是 drover 也写），没登记上不写 `main-session.json`、编排者两样都不记，Codex（不带 `--hook`）的 `session_id` 写 null，Claude Code 第一条消息时的兜底登记不动 hook 记的两份，记录不是这个 pane（hook 没登记上）或没有记录时补写 `main-session.json`（`session_id`、`source` 写 null） |
| `test_oncall_cli.py` | drover oncall：pair / unpair / test / on / off 映射到插件的 action；pair 从插件日志读出配对链接（`link`）、已配对时说已配对、`--wait` 等到配上再发 test、超时报出来；status 透传插件 status 脚本的 JSON、补上 drover 视角和提示，只报飞书（插件别的渠道没配过就不报）；没装插件、插件太旧没有 status 脚本、脚本出错；不输出 `.env` 和插件日志里的密钥，键名像密钥的值换成 `<redacted>`；和 install.py 的插件 id、最低版本、`drover.env` 内容一致 |

怎么隔离的：

- **不碰真实状态**：每个用例一个临时目录，里面有临时 HOME 和临时状态目录，`DROVER_STATE_DIR` 指向后者（见下），`config.json` 的 `workspace` / `repo_dirs` 也指向临时目录。用例结束整个删掉。
- **不调用真 herdr**：PATH 最前面放一个假 `herdr`，按状态文件返回 agent 状态和屏幕内容；`worktree create` 用 `git worktree add` 真的建在临时目录里，不认识的子命令一律报错。用例里断言 PATH 里的 herdr 是假的。
- **不联网**：远端是本地裸仓库。git 用临时的全局配置（`GIT_CONFIG_GLOBAL`），不读你的 `~/.gitconfig` 和系统配置。
- **不依赖 vitest / tsc / eslint / pytest**：PATH 里放同名的假脚本，按各自的格式打印失败条目（参数里点名了文件就只报这些文件的），测的是解析、复核和基线子集的逻辑。
- **不建图**：临时 PATH 去掉了 `code-review-graph` 所在的目录，accept 走"找不到命令"的分支，不会往临时仓库写 `.code-review-graph`。`--no-graph` 的用例另放一个只记日志的假 `code-review-graph`，不真的建图。
- 在进程里加载 `bin/drover` 的用例，先把环境变量换成临时的再加载（模块级常量在加载时就算好了），用例结束还原。
- **不真等**：要等的命令在进程里跑、把模块里的 `time` 换成假时钟（`selftest_support.FakeClock`），等待按假时间走、跑几轮是确定的：watch（跑满 `--timeout` 看有没有多报、`--idle-minutes` 这类阈值）和 oncall（等插件日志的轮询）走 `main()`（参数解析、分发、退出码和命令行同一条路，每次新加载一份模块，状态文件照常落盘），agent-start 处理弹窗时的停顿直接调处理函数。要真进程的（两个 `watch --main` 抢同一把锁、会话重开）才起子进程。
- **假工具每个进程写一次**：macOS 上新写出来的可执行文件第一次执行要多花 0.07～0.3 秒（同一个文件之后再执行没有这笔开销），所以假 herdr、假测试工具每个进程只写一份，各用例的临时目录里放硬链接（脚本按自己所在的目录找状态文件，互不影响）。
- **能并行跑**：每个用例的临时目录、HOME、状态目录、锁文件、假 herdr 的状态都是自己的，没有端口，不往仓库里写文件，所以 `selftest/run.py` 把用例分给多个进程同时跑不会互相干扰。

会跳过的情况：以 root 身份跑时跳过"锁文件不可写"那一条（root 无视目录权限）；临时 PATH 里还能找到 `code-review-graph` 时跳过 accept 端到端那几条（避免真的建图）。不需要本机装 vitest、node 或 uv。

**`DROVER_STATE_DIR`**：`bin/drover` 的状态目录默认是 `~/.drover`（`config.json`、任务记录、heavy 调度状态、基线缓存都在里面）。设了这个环境变量，整个状态目录就换成它指定的目录（`~` 会展开），自测就是靠它不碰真实状态的。手动调试脚本时也可以用，比如 `DROVER_STATE_DIR=/tmp/drover-debug ~/.drover/bin/drover watch --once <agent名>`。它只换状态目录，脚本本身的安装位置不变。

**`DROVER_HEAVY_CPU_FILE`**（自测用）：heavy 放行看的整机实测 CPU 不读系统，改读这个文件里写的核数（文件不在就当读不到）。自测默认写 0（`selftest_support`），测"整机满了"的用例自己改，免得结果跟着本机真实负载变。
