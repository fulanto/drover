# drover 安装说明（写给 agent）

用户让你安装 drover 时，按这份文档一步步做。"你"是 agent（Claude Code 或 Codex），"用户"是让你安装的人。

原则：

- **先问清楚，再动手。** 放哪、用哪个 agent、要不要团队规则，都由用户定，不要自己猜。问题发出去就停下，等用户回答。
- **先 dry-run 给用户看，用户明确确认后才正式安装。**
- **会改用户机器的每一步都要用户点头**：clone、往 `AGENTS.md` 加标记、装依赖、正式安装。只读的检查可以直接做，做完报告。
- 和预期不一样、拿不准、任何一步失败：停下，把原始输出给用户看，等用户定。
- **对用户说大白话，用用户说话的语言**（用户说中文就用中文）。用户多半不知道"工作台"是什么。下表左边的术语只在你自己心里用；对用户说的话里不出现，非出现不可时马上用右边的话解释。
- 细节以 [README](../README.md) 和 `python3 install.py --help` 为准，这里只写顺序和边界。

| 术语（只给你看） | 对用户这样说 |
|---|---|
| 主工作台 `--workspace` | 放代码仓库的那个文件夹，以后在这里打开 Claude Code / Codex 提需求 |
| 仓库目录 `--repo-dir` | 另外几个放代码仓库的文件夹 |
| 宿主 `--host` | 你用 Claude Code 还是 Codex |
| 团队 profile `--profile <名字或目录>` | 你们团队定的规则（比如推送前先确认分支） |
| link 模式（默认） | drover 这个文件夹装完要一直留着，不能挪、不能删 |
| copy 模式 | 复制一份装进去，原来的文件夹之后可以删 |
| dry-run | 先演练一遍：只列出会改什么，不真改 |

顺序：前置检查 → 摸底 → 一次问完 → clone → dry-run → 用户确认 → 正式安装 → 检查和交代。

## 1. 前置检查（只读，逐项报告）

一条命令只查一项。结果逐项告诉用户：有没有、版本号，没有的话怎么装。

| 检查 | 命令 | 算通过 |
|---|---|---|
| 系统 | `uname -s` | `Darwin`（macOS，完整支持）或 `Linux`（能装，但没有实测，要告诉用户） |
| git | `git --version` | 有版本号 |
| python3 | `python3 --version` | 3.9 及以上 |
| herdr | `command -v herdr`，没有再看 `ls ~/.local/bin/herdr` | 任一处有；没有也行，install 会一起装（见下） |
| claude / codex | `command -v claude`、`command -v codex` | 有没有都行：用户选了哪个，没装的 install 会一起装 |
| Node（只有飞书 oncall 要） | `node --version` | 22 及以上最好；18～21 能推送但收不到飞书回复；一个都没有时，用户要飞书 install 会一起装 |
| 能访问仓库 | `git ls-remote https://github.com/fulanto/drover.git HEAD` | 打印出一个提交号 |
| 装过没有 | `cat ~/.drover/config.json` | 文件不存在 = 首次安装 |

缺东西时：

- **系统不是 macOS / Linux（比如 Windows）**：告诉用户这一版不支持原生 Windows，可以在 WSL2 的 Ubuntu 里按 Linux 的方式试（没有实测，README 第 1 节"支持哪些系统"），不要往下装。Linux：告诉用户能装、但没有实测过。
- **git、python3 缺（或 python3 低于 3.9）**：install.sh 会停下，要用户先装，你不替用户装。
  - macOS：跑 `git --version` 时会弹出"安装命令行开发者工具"的系统框（没弹就跑 `xcode-select --install`）。请用户在框里点"安装"、等它装完（git 和 python3 都在里面），再告诉你继续。框要用户自己点，你不要想办法绕过。
  - Linux：把装法给用户（比如 `sudo apt-get install git python3`，其他发行版见 README 第 1 节），**请用户自己在终端里跑**：要 sudo 的命令你不跑。
- **herdr、claude / codex（用户选的那个）、飞书要的 Node 缺了**：不用让用户先装。正式安装时加 `--install-deps`，install 会用官方安装脚本把它们装到用户的家目录（都不用 sudo），dry-run 里写清每一样要跑的命令、装到哪、改不改 shell 配置（第 5 步）；用户看过 dry-run 说"装"，就算同意了。
- herdr 在 `~/.local/bin` 但不在 PATH：算已装，记下来，最后交代时提醒用户（见第 7 步）。
- 有 Node 但低于 18（或 18～21）：install 不替换用户已有的 Node（飞书插件先用 PATH 上的那个，另装一个也用不上）。告诉用户升级的办法（`brew install node`，或 nodejs.org），装不装由用户定。只影响飞书 oncall（第 3 步第 7 项），drover 本身不需要 Node。
- Python 只要 3.9+。验收工具（crapper、dryer）要的 3.11+ 不用用户装：本机没有时，install 会用 uv 装一份进 `~/.drover/tools`（连 uv 也没有就先装一份 drover 私有的），dry-run 里能看到这几步。

访问仓库失败时，按报错告诉用户：

- 超时、解析不了域名、连接被重置：多半是网络或代理的问题（访问不了 GitHub），请用户检查网络或代理设置后再试。
- 提示仓库不存在、或者要输入账号密码：多半是地址写错了，核对是不是 `https://github.com/fulanto/drover.git`。这是公开仓库，clone 不需要登录。
- 你已经 clone 过一份（比如为了读这份文档），这一项就算通过。

已经装过（有 `config.json`）：这是重装或升级。把 config.json 的内容给用户看，读 README 2.6；沿用现有配置时不传 `--workspace` / `--repo-dir`。里面的 `workspace` 是家目录时 install 会拒绝，按第 3 步第 2 项和用户重新定一个。

## 2. 摸底（只读）

为了替用户想好建议，先查这几样：

- **读文档用的 clone**：应该在 `mktemp -d` 建的临时目录里（`git clone <地址> <临时目录>/drover`），记下路径；clone 日志等临时文件也放进同一个临时目录，不要往 `/tmp` 直接写。不要在用户的家目录或代码目录里新建文件夹，也不要直接 clone 进 `~`；已经这样做了，就告诉用户（第 3 步第 1 项）。临时 clone 不是正式位置，装完由你删掉（第 7 步）。
- **默认位置**：`ls -la ~/.drover/src`，已经存在就用 `git -C ~/.drover/src remote -v` 看是不是 drover。
- **用户放代码的文件夹**：数下面这些目录里各有几个 git 仓库。
  - 候选：`~/code`、`~/projects`、`~/workspace`、`~/work`、`~/src`、`~/dev`、`~/Developer`、`~/git`、`~/repos`；当前工作目录是某个仓库时，再加上它的上一级。
  - 每个存在的目录跑一次 `find <目录> -mindepth 2 -maxdepth 3 -name .git`。`<目录>/<仓库>/.git` 是直接放在里面的，`<目录>/<子目录>/<仓库>/.git` 是放在子目录里的，分开记。
  - 只看这一两层。**不要递归扫整个家目录**，也不要把 `~` 本身当候选。
- **排第一的候选里已有什么**：`ls -la <目录>/AGENTS.md <目录>/CLAUDE.md <目录>/.claude/settings.local.json`；有 `AGENTS.md` 就再 `grep -n "drover:routing" <目录>/AGENTS.md`，看有没有标记。

## 3. 一次问完

用附 A 的大白话清单一次问完。**问完就结束这一轮，等用户回答**，不要在同一轮里替用户选推荐项接着往下做。

用户回"按你推荐的来"：算每个问题都按你的建议，但**不算安装确认**，照样要 dry-run、原样贴出、等用户明确说"装吧 / 确认"。

每一项你要想清楚的是：

1. **clone 位置**：默认 `~/.drover/src`，问用户要不要换。
   - 这是 drover 自己的目录 `~/.drover`（以后的配置和任务记录也放在这里），隐藏目录，访达里默认看不到，不占用户的代码文件夹。它不是 `~/.drover/pkg`（那是 install 自己建的包入口：link 模式下软链到这份 clone，copy 模式下是副本）。
   - 默认的 link 模式软链到这份 clone，装完不能挪、不能删。用户想放进自己的代码文件夹也可以；不想一直留着就用 `--mode copy`（README 2.3）。
   - 读文档用的临时 clone 不能当正式位置（link 模式软链到它，临时目录一清就断）。用户确认位置后在那里重新 clone；想从临时目录挪过去，先问。不想留 clone 的，也可以用 copy 模式从临时 clone 装。
   - 你已经 clone 进了用户的家目录或代码目录：告诉用户在哪，问是留着当正式位置，还是换到 `~/.drover/src`。
2. **主工作台 `--workspace`：不给默认值，也不直接问"工作台选哪个"。** 先拿扫描结果问"你平时把代码仓库放在哪个文件夹"，再由你推出来：
   - 主工作台 = 放代码仓库的那个**上级目录**。仓库都在 `~/code/xxx`，主工作台就是 `~/code`。
   - 为什么：在这里开主会话，输入框的 `@` 补全出来的是相对这里的路径，派发时能直接 `@仓库名`；路由规则只覆盖这个目录，在具体仓库里开的会话虽然会往上读到它，但区块里写明了要忽略。
   - 按 git 仓库数从多到少列给用户，推荐最多的那个，用一两句大白话说明以后怎么用（附 A 第 2 条），请用户确认或改。
   - 仓库分散在几个目录：仓库最多的当主工作台，其他的用 `--repo-dir` 补进去。补进去的照样能派发，只是输入框的 `@` 补全列不出来，要手打仓库名或路径。仓库主要在某个子目录里（如 `~/code/company/xxx`）时，把子目录也列进去，放在前面。传了 `--repo-dir` 就不再默认用主工作台找，主工作台自己要放仓库的话也列上。
   - 还没有统一放代码的文件夹：建议新建一个（比如 `~/code`），把常用仓库放进去。挪仓库由用户自己做。
   - 用户选家目录 `~`：劝阻并说明原因。放在家目录，以后在电脑上任何地方打开 Claude Code / Codex 都会读到 drover 的分派规则（Claude Code 往上找 `CLAUDE.md`，Codex 加载 `AGENTS.md`），权限设置还会落进 `~/.claude/`；install.py 也会拒绝（退出码 2）。家目录的上级（含 `/`）、`~/.herdr`、`~/.drover`、drover 的 clone 本身也不行。
   - 安装会在这里写：`AGENTS.md`（drover 路由区块）、`CLAUDE.md`（一行 `@AGENTS.md`）、`.claude/settings.local.json`（权限规则）。改已有文件前先备份。
   - 首次安装必须显式传 `--workspace`，dry-run 也一样：你的 shell 不是交互终端，不传会直接退出码 2。
3. **仓库目录 `--repo-dir`**：默认就是主工作台，不用传。只在上面说的分散情况才列，按查找顺序。
4. **宿主 `--host`**：用户没说用哪个（包括回"按你推荐的来"）时，**只装你自己**：你是 Claude Code 就 `--host claude`，是 Codex 就 `--host codex`。告诉用户以后要加另一个，用 `--host both` 重跑就行。用户说两个都用才 `both`。用户选的那个电脑上还没装的，install 会一起装上（官方安装脚本，装在用户的家目录）；**登录要用户自己来**：Claude Code 第一次运行 `claude` 时按浏览器提示登录，Codex 先跑 `codex login`。
5. **团队 profile `--profile`（可选）**：用户的团队有 drover 的团队规则（profile，比如推送前确认分支、改表结构要核对，README 3.1）时，用户给出名字或目录就加上；没有或不确定就不加，以后 `./install.sh --profile <名字或目录>` 单独加，不用重装。
6. **主工作台里已经有 `AGENTS.md`、没有 drover 标记**：区块会追加到末尾（先备份），可能和已有内容重复。建议先在用户想要的位置放一对空标记（README 2.5）。问用户要不要、放在哪；用户同意后你加，或者用户自己加。
7. **要不要装飞书 oncall**（README 4.9）：默认不装，用户说要才装。它从 GitHub 装一个 herdr 插件（herdr-oncall，要 Node 18+，收飞书回复要 22+）：离开电脑时，主会话需要用户的事（卡在确认框上；转达编排者要用户拍板、做完了、卡住了）会推到飞书，用户在飞书里收提问、直接给主会话下命令（它对飞书消息的回答挂回那条消息下面）；别的对话不推。用户要，就加 `--oncall`（会记住，以后重跑、更新都照旧），装完在第 7 步配对；没有 Node、公司网络访问不了 GitHub 时先跟用户说清楚。重装 / 升级时以前已经装上的不用加，install 照旧保留、照旧更新；用户这次说不要了，加 `--no-oncall`（已装的插件不卸）。

## 4. clone（用户确认位置后）

- 目标目录已经存在：先看里面是什么，不要覆盖，问用户。
- clone：`git clone https://github.com/fulanto/drover.git ~/.drover/src`（用户换了位置就用用户说的）。
- 读文档用的临时 clone 先留着，装完由你删（第 7 步）。
- 后面的命令都用这份 clone 里的 `install.sh`（`cd ~/.drover/src`，或写全路径）。link 模式软链的是**你运行的那份** clone。

## 5. dry-run

带上用户确认的全部参数，加 `--install-deps`（dry-run 不会真装，加了它，计划里缺的依赖显示成"安装 …：直接执行"，就是正式安装时要做的事）：

```bash
./install.sh --dry-run --install-deps --workspace <主工作台> --host <宿主> [--repo-dir <目录>]... [--mode copy] [--profile <名字或目录>] [--oncall]
```

- 把输出**原样**贴进你的回复：放进代码块，不改格式、不合并行、不删行，太长也全贴。工具调用的输出用户不一定看得到，不贴就等于没给用户看；也不要只给摘要。
- 代码块后面再用大白话说明：会改哪些地方、为什么、哪些先备份、怎么撤销。比如：

  ```
  上面是演练，什么都还没改。正式装会改这几处：
  - 你电脑上缺的 herdr、Claude Code（要飞书时还有 Node）会用它们官方的安装脚本装上：herdr、claude 放在 ~/.local/bin，Node 放在 ~/.nvm，都在你自己的家目录，不用管理员密码；
  - ~/.local/bin 现在不在你的 PATH 里，所以会在 ~/.zshrc 末尾加一段带 drover 标记的 PATH（改之前先备份），这样新开的终端里能直接输 herdr、claude；卸载时会把这一段删掉；
  - ~/.drover/ 下放 drover 的入口和配置，这是 drover 自己的文件夹；
  - 给 Claude Code 加几条权限和一个小检查（hook），让它派任务时少弹确认：那个 hook 会挡住"先 cd 再跑 git"这种每次都要你点确认的写法，让 agent 自己换成不用确认的写法（都在 ~/.claude/settings.json，只加不删，改之前先备份）；
  - ~/code 里放两个说明文件（AGENTS.md、CLAUDE.md）和一个权限设置文件，以后在 ~/code 打开的 agent 就知道怎么分派任务；
  - 让 Herdr 能看出每个 agent 是在干活还是在等你；
  - 从 GitHub 装飞书提醒插件（herdr-oncall），离开电脑时主会话需要你会推到飞书（你说要飞书才有这一条）；
  - 最后从 GitHub 装两个代码检查工具（crapper、dryer）到 ~/.drover/tools（各十几 MB）：以后验收时自动查改到的函数是不是太复杂、有没有重复代码；你电脑上没有 Python 3.11 的话会顺带装一份进同一个文件夹，不动你自己的 Python（这种情况下这个文件夹一共一百多 MB，装要一两分钟，看网速）。装不上也不影响 drover，只是少这项检查。
  想撤销，随时一条命令卸载；改之前备份的文件都留着（另外装的 herdr、Claude Code 这些不会跟着卸，你可能还会单独用）。可以正式装吗？
  ```

- 核对开头几行：`包：` 是用户选的 clone 目录；`主工作台：`、`仓库目录：`、`团队 profile：`、`宿主：` 和用户说的一致。

出现下面任何一种，停下问用户，不要往下走：

- 路径不是用户说的那个；
- 要把一个已有的实体文件或目录换成软链（会先备份）：说明用户在那里有自己的东西；
- `AGENTS.md` 已有内容、没有标记，用户还没同意追加到末尾；
- 计划里出现 README 2.5 之外的位置；
- 汇总里有"失败"，或者退出码不是 0（比如主工作台是家目录，install 拒绝）。验收工具那几项（"验收工具…"）在 dry-run 里只是列出要装，不算这一条。

依赖那几项（herdr、Claude Code / Codex 命令、Node）的动作是"没自动装（不是交互终端，也没加 --install-deps）"：是 dry-run 漏了 `--install-deps`，加上重跑 dry-run。动作是"官方安装脚本只支持 macOS / Linux""要用 curl"：停下，把原话告诉用户。

"Node"那一项是"只提示"（用户电脑上有 Node，但版本低于 18，install 不替换），或者"herdr-oncall"那一项没法处理（herdr 没在运行、查不了插件）：告诉用户原因，问用户是先处理（升级 Node、在终端里运行 herdr），还是这次不装飞书（去掉 `--oncall`；以前已经装上的，加 `--no-oncall`）。

参数有任何改动，重新 dry-run。等用户明确说"确认""装吧"，才进下一步；第 3 步里的"按你推荐的来"不算。用户说"装"，就算同意了 dry-run 里列出的依赖安装。

## 6. 正式安装（用户确认后）

- 用**同一组参数**（包括 `--install-deps`），只去掉 `--dry-run`。
- 执行顺序：缺的外部依赖（herdr、Claude Code / Codex 命令、Node）和 PATH 在最前面，再 drover 自己的各项（最后一项是安装记录），验收工具（Python 3.11+、crapper、dryer）在最后：最慢、装不上也只是警告，放最后，被打断时 drover 自己的各项已经装好。
- 你的 shell 不是交互终端，install 不会提问。万一停在等输入：中断它，把输出给用户看，不要替用户回答。
- 输出原样贴给用户（贴法同第 5 步），指出汇总里的"备份："、"提示："和失败项。退出码不是 0 时，**不要自己去改用户的配置来"修"**。
- herdr 没装上（网络、下载失败）：其余项已经装好，用到 herdr 的几项（herdr 技能、状态 hook）报失败。把失败原因给用户看；网络好了用同一组参数再跑一次。重跑是幂等的，装对的项会跳过，已经装上的依赖不会重装。
- Claude Code / Codex 命令没装上：只记警告，退出码照样是 0，drover 这边已经装好。把原因和官方命令（提示里有）给用户看，装好之后不用重跑 install。
- 飞书 oncall 那一项失败（访问不了 GitHub、Node 没装上、herdr 没在运行）：其余项已经装好。把失败原因给用户看，问用户是处理好后重跑，还是加 `--no-oncall` 重跑（会记住）。
- 汇总里"只记警告"的（验收工具 crapper、dryer 或它们要的 Python 3.11+ 没装上）：不算失败，退出码照样是 0，drover 能正常用，只是验收时少了复杂度和重复代码的检查（报"测不了"）。把提示里的原因告诉用户，网络好了重跑 install 或 `~/.drover/bin/drover update` 会补装；用户不想要，就在 `~/.drover/config.json` 写 `"quality_tools": false`（用户让你改你才改，README 2.8）。

## 7. 检查和交代

跑 `./install.sh --check`，结果给用户。`✓` 装对，`!` 只是提示，`✗` 没装对（README 2.6）。

- `!` codex 的 `sandbox_mode` 不是 `danger-full-access`：告诉用户原因（README 第 7 节），不要改 `~/.codex/config.toml`。
- herdr、claude 不在 PATH：汇总里"把装依赖的目录加进 PATH"那一项完成了的，告诉用户开一个新终端就好（install 已经把 `~/.local/bin` 加进了那一项写的 shell 启动文件）。那一项只是提示的（用户的 shell 认不出等），告诉用户把 `export PATH="$HOME/.local/bin:$PATH"` 加进自己 shell 的启动文件，再开新终端；用户让你改，你才改。
- `!` Node 低于 22：告诉用户能收到飞书推送，但飞书里的回复送不回来，升级 Node 就好。
- `!` 验收工具没装上、或者没有 Python 3.11+：只是提示，处理办法同第 6 步"只记警告"那一条。

**接飞书**（第 3 步用户选了装、插件也装好了：主动带用户配好）：

1. 跑 `~/.drover/bin/drover oncall pair`（要等插件这次的动作跑完，可能二三十秒）。输出里的 `link` 是配对链接，原样告诉用户："Mac 桌面上会弹出一个二维码，用手机飞书扫一下、按提示确认；没弹出来的话，在手机飞书里打开这个链接：<link>。"输出里 `already_paired` 是 `true` 就是已经配过了，告诉用户"飞书已配对"，跳到第 3 步。
2. 用户说扫好了，跑 `~/.drover/bin/drover oncall status`，看 `plugin_status.channels.feishu.paired` 是不是 `true`。还是 `false`：问用户是不是还没在飞书里点确认；链接十来分钟就过期，过期了重跑第 1 步。
3. 已配对后跑 `~/.drover/bin/drover oncall test`，问用户飞书收到测试消息没有。
4. 没收到、`link` 是空的、`pair` 报错：`still_running` 是 `true` 的是插件还没跑完，过一会儿看 `herdr plugin log list --plugin com.codreamer.herdr.oncall --limit 5` 里 `open:` 后面的链接；其他情况把 `hints`、`plugin_message` 和这条命令的输出给用户看（README 第 8 节"飞书收不到消息""飞书配对失败"）。飞书租户不让建个人机器人时，按 README 第 8 节告诉用户，不要自己想办法绕过。
5. 这几条命令要连 herdr：herdr 没在运行（报 `herdr plugin list 失败`）时，请用户先在终端里运行 `herdr`，再重试。
- `drover oncall pair --wait` 会一直等到配上（最多 5 分钟）再自动发测试消息，适合用户就在旁边、扫完不想再回话的情况；它会阻塞，平时按上面一步一步来。

然后删掉你为读文档建的临时 clone（不用问）：先确认路径就是你 `mktemp -d` 建的那个，而且 `ls -la ~/.drover/pkg` 不指向它，再删整个临时目录。别的目录一律不删。

最后照附 B 用大白话交代：以后去哪个文件夹开 agent、怎么提需求、怎么看进度、出问题怎么卸载、备份在哪。给你的要点：

- 开主会话（新手照着做得下来，附 B 第 1 条就是这几步；README 4.1）：
  1. 打开 Herdr：开一个新终端，运行 `herdr`（install 把 `~/.local/bin` 加进了 shell 配置，新开的终端里才生效）。那一项没做成的，用完整路径 `~/.local/bin/herdr`，并请用户把 `export PATH="$HOME/.local/bin:$PATH"` 加进 shell 的启动文件、再开一个新终端（drover 在 Herdr 里按名字调用 herdr、claude，不加会报找不到；用户让你改，你才改）。
  2. 第一次启动会自动开一个 workspace：点一下 pane 就能输入，然后 `cd <主工作台>`。
  3. 启动 `claude`，第一次按浏览器提示登录；Codex 先跑 `codex login`，再 `codex -s danger-full-access`（README 第 7 节）。
  4. 把信任框、MCP 弹窗点掉（README 第 8 节"启动卡在信任 / MCP 弹窗"），以后起编排者（orchestrator，drover 给每个任务起的会话）才不会卡住。
  5. 离开时按 `ctrl+b` 再按 `q`，agent 会接着跑；下次再运行 `herdr` 就回来了；`herdr server stop` 才是全部停掉。
- 备份和卸载：备份是 install 输出里"备份："列出的 `*.bak-drover-*`（Codex 技能的在 `~/.drover/backups/codex-skills/`），卸载不会自动还原；卸载先 `./install.sh --uninstall --dry-run`，再 `./install.sh --uninstall`（README 2.6）。install 替用户装的 herdr、Claude Code / Codex、Node 卸载时不删，输出里会列出手动删法。
- link 模式：clone 不能挪、不能删。
- 更新：`~/.drover/bin/drover update`（拉新代码、按原来的模式重跑安装、检查，最后告诉你要不要 /clear），在主会话里说"更新 drover"也行；copy 模式没留 clone 的，它会给出手动步骤（README 2.6）。
- **你这个会话不会加载新的路由规则**，要用 drover 得在 Herdr 里新开一个会话。

## 8. 不要做的事

- 不改安装计划之外的文件（用户同意放的 `AGENTS.md` 空标记除外）。不替用户挪仓库，不在用户的家目录或代码目录里新建文件夹放临时 clone；除了你自己 `mktemp -d` 建的临时目录，什么都不删。
- 不删、不改用户已有的权限规则和配置。install 只追加，你也不要手动补。
- 不用 `sudo`（Linux 缺 git、python3 时把 sudo 命令给用户自己跑），不往系统目录装东西。依赖只经 install 装：用户看过 dry-run、说"装"之后加 `--install-deps`，不要自己另外去跑安装脚本。
- 不跳过 dry-run，不在用户确认之前正式安装，不把"按你推荐的来"当安装确认。
- 主工作台不给默认值，不用家目录；dry-run 的计划里出现意外（第 5 步列的几种），停下问。
- 任何一步失败，都不要自己"修"用户的配置，把输出给用户看。
- 不替用户回答 install 的提问；不改 `~/.codex/config.toml`。shell 配置只经 install 改（加 PATH 那一段，用户看过 dry-run 说"装"就算同意），你自己不去改，除非用户明确让你改。

## 9. 宿主差异

- **Claude Code**：默认每条 bash 命令都会弹审批，这是正常的，用户能看清每条命令再批。一条命令只做一件事，方便用户看。
- **Codex**：`workspace-write` 沙箱里写不了 `~/.claude`、`~/.codex`、`~/.drover`，默认也不能联网。访问仓库那一项检查、clone、正式安装都要用户批准提权（或者在沙箱外运行），申请时写清楚完整命令和原因。
  - Codex 尤其要守住这几条：问完就停、dry-run 输出原样贴、"按你推荐的来"不算安装确认、挪 clone 之前先问。装完的大白话总结别漏了"怎么看进度"和"有没有备份、备份在哪"（附 B 第 3、6 条）。
  - exec / 非交互模式下用户没法中途回答：问完就结束这次运行，不要自己往下推进。

## 附 A：安装前对用户说的话（模板）

一次问完，发出去就停下等回答。`……` 换成你查到的真实情况：

```
装 drover 之前，跟你确认几件事（都按我说的，回"可以"就行）：

1. drover 放在 ~/.drover/src，可以吗？这是 drover 自己的隐藏文件夹，访达里默认看不到。
   装完这个文件夹要一直留着，不能挪、不能删；想放别处也可以告诉我。
2. 你的代码仓库好像都在 ~/code（找到 12 个仓库；~/work 里还有 3 个）。
   以后你在 ~/code 打开 Claude Code，直接说要做什么，它会帮你分派给后台的 agent 去做；
   在某个具体仓库里打开就和平时一样。就用 ~/code，可以吗？
   我会在 ~/code 里放两个说明文件（AGENTS.md、CLAUDE.md）和一个权限设置文件，原来有的先备份。
   ~/work 那 3 个要不要也能派活？可以加上，只是输入 @ 时不会自动补全。
3. 你用 Claude Code 还是 Codex，还是两个都用？（你电脑上装了：……）
   不说的话，我只给我自己这个（……）装上，以后要加另一个随时可以。
   你选的那个电脑上还没装的话，我会一起装上（官方安装脚本，装在你的家目录）；登录要你自己来，装完我告诉你怎么登。
4. 你们团队有 drover 的团队规则（profile）吗？比如推送前先确认分支、改数据库表结构要核对这类。
   有的话告诉我它的名字或所在的文件夹；没有或不确定就先不加，以后再加。
5. 要不要装飞书 oncall？装上以后，离开电脑时它需要你（等你点确认、任务要你拍板或者做完了）会推到你的飞书，
   你在飞书里收它的提问、直接发消息给它下命令（派活、问进度）。默认不装；要的话装完我帮你扫码配对一下。
   （要 Node 18+，最好 22+：……）
（~/code 里已经有 AGENTS.md 时加一条：这个文件是给 agent 看的说明，drover 要往里加一段规则，
 默认加在末尾；你想放在别的位置也可以告诉我。）

要执行的命令（给你核对用）：先演练一遍，只列出会改什么、要装哪些东西，不真改；你看过说"装"，我再正式装
（缺的 herdr 这类依赖也一起装上）：
./install.sh --dry-run --install-deps --workspace ~/code --host claude
（加团队规则时末尾加 --profile <名字或目录>）
（要飞书时末尾加 --oncall）
```

## 附 B：装完对用户说的话（模板）

```
装好了。以后这样用：
1. 打开 Herdr：
   ① 开一个新终端（刚才装的时候把 ~/.local/bin 加进了 ~/.zshrc，新开的终端里才认得 herdr、claude），
      输入 herdr 回车。（没加上的话说：输入 ~/.local/bin/herdr；再把 export PATH="$HOME/.local/bin:$PATH"
      加进 ~/.zshrc、开一个新终端，以后直接输 herdr 就行）
   ② 第一次会自动开好一个窗口，点一下它就能打字，输入 cd ~/code 回车。
   ③ 输入 claude 回车，第一次会打开浏览器让你登录（用 Codex 的话：先输入 codex login 登录，
      再输入 codex -s danger-full-access）。
   ④ 第一次会弹"是否信任这个文件夹"之类的提示，选 Yes；还有别的弹窗也点掉。
   ⑤ 要离开时按 ctrl+b 再按 q：后台的 agent 照样在干活；下次输入 herdr 就回来了。
      herdr server stop 才是全部停掉。
2. 直接说要做什么，比如"@order-service 分页接口报 500，修一下"。它会派后台的 agent 去做，
   做完给你一页汇报；只有要你拍板的事才会来问你。
3. 想知道做到哪了，直接问它"进度怎么样"。
4. 我这个窗口用不了 drover，请按第 1 条新开一个。
5. 离开电脑时：它需要你会推到飞书；你在飞书里直接发消息（不引用）就是在跟它说话。
   飞书里发 /off 能一键关掉，/on 打开。（接了飞书才说）
6. 想卸载：在 ~/.drover/src 里运行 ./install.sh --uninstall（先加 --dry-run 看会撤掉什么）。
   这次改动前备份的文件在：……
   我顺带给你装的 ……（herdr / Claude Code / Node，有才说）卸载时不会删，你可能还会单独用；不要了，卸载的输出里有删法。
7. 我刚才为了读安装说明，临时把 drover 下载到了 ……，已经删掉了。（有这回事才说）
```
