# 配置

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
| `workspace` | 主工作台目录：主会话和编排者的工作目录，`AGENTS.md` 路由区块就写在这里。它也是**可信仓库根目录**：在它下面的仓库，Herdr 建 worktree 要求信任时脚本自动处理；别处的仓库会先问你 | 没有默认值。install 第一次会问，非交互时要 `--workspace`（见[最短路径](install.md#最短路径)）；不能是家目录或它的上级。`bin/drover` 不自动信任任何目录，`orch-start` 报错让你先跑 install |
| `repo_dirs` | 按名字找仓库的目录列表，按顺序找第一个存在的 `<目录>/<仓库名>`。`@` 后面写路径（带 `/`）时直接按路径找，不看这个列表 | 没有默认值。`bin/drover` 只按路径找仓库；install 没传 `--repo-dir` 时写成 `[workspace]` |
| `profile` | 团队 profile（团队规则、编码规范、各仓库的 playbook，见[团队 profile 和 playbook](#团队-profile-和-playbook)）。写名字用包里的 `profiles/<名字>/`；写路径（带 `/` 或以 `~` 开头）用那个目录。配置了但目录不存在时 `drover playbook` 报错、`install.py --check` 报 ✗ | `null`（只有包默认规则） |
| `playbook_dirs` | 个人覆盖目录列表，布局和 profile 一样，排在团队 profile 之后读（后读的优先）。install 没有对应参数，手动编辑 | `[]` |
| `default_coding_rules` | 包默认的编码规范（`flow/coding-rules.md`）追加不追加进 worker 任务书，排在团队 profile、个人覆盖的编码规范前面。它分三部分：通用原则（改编自 Robert C. Martin 的 ub-mode：先定数据结构、改行为先写失败的测试、修根因不加兜底、测行为不测实现等，总带）、MySQL 建表的命名 / 注释 / 默认值 / 索引名前缀（仓库里有 `.sql` 文件或依赖里有 mysql / mariadb 时才带）、Java 的 import 和不用内部类（仓库根目录有 `pom.xml` / `build.gradle` 时才带），每条只在改到相关代码时适用。`false` 时整份不追加，团队 profile、个人覆盖、`~/.claude/CLAUDE.md` 的照旧；只管这一份编码规范，worker 不执行迁移、数据库写操作升级给用户这些安全和流程规则不跟着关。install 没有对应参数，手动编辑 | 没有这个键 = 开 |
| `oncall` | 接不接飞书 oncall（[飞书 oncall](oncall.md)，选装）。`true` 时 install 装插件、写 `drover.env`；`false` 时不装（已经装上的插件不卸）。`install.py --oncall` 写 `true`，`--no-oncall` 写 `false` | 没有这个键 = 没选过：以前已经接上的（安装记录里有、或者 herdr 里装着插件）照旧接，其余不接 |
| `claude_model` | Claude Code 编排者和 worker 的模型（`claude --model` 的值）。账号没有 1M 上下文档位、`opus[1m]` 起不来时改成 `opus` 等。只给一个任务换模型不用改它：派发时写 `--model <完整模型 ID>`（如 `/drover --model claude-fable-5-2 @仓库 …`），这个任务的编排者用它起、记进 `orch.json`，同宿主的 worker 和子编排者继承，别的任务不受影响（codex 编排者时传 `-m`，codex worker 和复审继承）。写完整 ID，不写别名：`fable`、`opus` 这类别名会被本机设置改指（`~/.claude/settings.json` 的 `env` 里 `ANTHROPIC_DEFAULT_FABLE_MODEL` 这类变量，写别名不保证是哪个模型） | `opus[1m]` |
| `claude_effort` | Claude Code 编排者和 worker 的 effort（`claude --effort`：low / medium / high / xhigh / max）。派发时写了 `--effort` 以那个为准 | `xhigh` |
| `codex_effort` | Codex 编排者、worker 和复审的 effort（`-c model_reasoning_effort=…`，比 claude 多一个 ultra）。派发时写了 `--effort` 以那个为准；codex 的模型没指定就不传（`--model` 见上一行），用 `~/.codex/config.toml` 的默认 | `xhigh` |
| `auto_update` | [自动更新](auto-update.md)。`false` 时这台机器不自动更新（手动 `drover update` 照常）；环境变量 `DROVER_AUTO_UPDATE=0` 也能关。install 没有对应参数，手动编辑 | 没有这个键 = 开 |
| `quality_tools` | [验收工具](quality-tools.md) crapper、dryer。`false` 时 install 不装、`--check` 不查，验收时两步都不跑（已经装的不卸）；环境变量 `DROVER_QUALITY_TOOLS=0` 也能关。install 没有对应参数，手动编辑 | 没有这个键 = 开 |
| `auto_update_minutes` | 自动更新多久查一次（分钟），离上次尝试满这么久才查 | 30 |
| `worker_idle_release_minutes` | worker 连续空闲多少分钟就自动释放：退出它的 agent 进程（连同 MCP 子进程）腾出内存，pane、worktree、分支都留着，之后给它发话会自动续上（[release](reference.md#release)）。`0` 关掉自动释放（手动 `drover release` 照常；后台维护照样每分钟给 herdr 同步续命令，见 [herdr 重启后按原参数拉回](reference.md#herdr-重启后按原参数拉回)）；环境变量 `DROVER_IDLE_RELEASE=0` 也能关。只管 `agent-start` 起的 worker，编排者、复审、主会话不受影响。install 没有对应参数，手动编辑 | 10 |
| `update_channel` | 更新通道（[自动更新](auto-update.md)）：`release` 只装发布的版本（上游历史上最新的 `vX.Y.Z` tag），`main` 跟上游最新（开发 drover 用）。手动 `update` 和自动更新都按它；写了别的值时 `update` 报错、什么都不改。install 没有对应参数，手动编辑 | `release` |
| `heavy_cpu_capacity` | heavy 调度的 CPU 容量（核）：几条构建 / 测试合计占用不超过它就同时跑。默认从硬件值起步、按真实命令的表现自动校准（[目录布局](reference.md#目录布局) `locks/heavy/`），一般不用写；写了就固定用它、不再校准（比如机器还要留给别的重活，或者 Linux 容器里 CPU 配额比看到的核数少） | 不写：Apple 芯片从性能核数（`hw.perflevel0.logicalcpu`）、其他从逻辑核数起步，之后自动校准，不超过逻辑核数 |
| `heavy_calib_slow` / `heavy_calib_fast` / `heavy_calib_down` / `heavy_calib_up` | 容量校准的阈值：同时跑的命令比同一条命令单独跑慢到 `slow` 倍算明显变慢、快于 `fast` 倍算几乎不变慢；连续 `down` 个明显变慢降一核，连续 `up` 个几乎不变慢（负载接近容量时）加一核。`fast` 也是单独跑基准的抖动界线：同一条命令几次单独跑最慢 ÷ 最快超过它，不拿它当基准（同时跑的变慢和它自己的抖动分不开） | 1.5 / 1.2 / 2 / 3 |
| `heavy_small_seconds` | 历史上最长也在这么多秒内跑完的命令算小命令：不排队、不计入占用 | 10 |
| `heavy_starve_seconds` | 排队的命令等了这么多秒还没开始，后来的只放能在它预计开跑前跑完的（防止它一直被插队） | 120 |
| `heavy_grace_seconds` | 刚放行的命令按预留算的秒数（之后按实测）：整机实测取最近 2 秒的平均、进程树实测要两次 ps 采样，再加上命令爬到稳定占用的时间 | 5 |
| `heavy_mem_psi` | 只对 Linux：`/proc/pressure/memory` 的 `some avg10` 到这个百分比算内存紧张（只放单独一条）。macOS 看系统的内存压力，到"严重"才算 | 10 |

路径里的 `~` 会展开。

**改了配置要不要重装？**

- 只改 `repo_dirs`：不用重装。drover CLI 每次执行都会重新读配置，下一条命令就生效。可以直接编辑文件，也可以 `python3 install.py --repo-dir <目录> [--repo-dir <目录>]...`：只改这个键，改之前先备份。`auto_update`、`auto_update_minutes`、`update_channel`、`worker_idle_release_minutes`、`heavy_*` 同样下一条命令就生效（已经在跑的 `watch --main`、`board --live`、在排队的 `heavy` 用的是启动时读到的配置，重开后才认）。`claude_model`、`claude_effort`、`codex_effort` 从下一个起的编排者、worker、复审开始用，已经起来的不变（例外：编排者报给 herdr 的续命令按报的时候的 config 补默认值，起来以后改了，herdr 重启后它按新默认回来，见 [herdr 重启后按原参数拉回](reference.md#herdr-重启后按原参数拉回)）；所有任务都跟着换，只想换一个任务的用派发时的 `--model` / `--effort`（任务 `orch.json` 记的优先于 config）。
- 改 `profile` / `playbook_dirs` / `default_coding_rules`：也不用重装，下一条命令就生效（已经发出去的任务书不变）。`python3 install.py --profile <名字|目录>` 只改 `profile` 这个键（改之前先备份，解析不到目录时直接报错、不写），`--profile none` 去掉它。
- 改 `workspace`：要重跑 `python3 install.py --workspace <新目录>`。路由区块、`CLAUDE.md` 和 `.claude/settings.local.json` 都写在主工作台里，新目录里要有这些；以后也在新目录里开主会话。
  - 只传 `--workspace` 时 `repo_dirs` 保持原样，仓库目录也要变就一起传 `--repo-dir`。
  - 旧主工作台 `AGENTS.md` 里那段 drover 区块不会自动删，不想留就手动删掉两个标记之间的内容。`--uninstall` 会把安装记录里所有主工作台的区块都撤掉。

## 团队 profile 和 playbook

规则分三层，后面的覆盖前面的：

1. **包默认**：`flow/dispatch.md` 和 `bin/drover` 里的通用流程，和团队、仓库无关，包括几条安全规则（worker 只写迁移文件和测试、不执行迁移、不写共享 / 生产数据库；数据库写操作默认升级给用户）。需要团队或仓库规则的地方（worktree 位置、迁移授权例外和执行步骤、基线本来就有失败的命令、推送目标分支）只挂一个"按 playbook 做"的钩子；迁移有授权、仓库又没写步骤时用默认步骤（`flow/integrate.md` 第 4 步）。另有一份包默认的编码规范 `flow/coding-rules.md`（通用原则，加上 MySQL 建表、Java 的几条，只在改到相关代码时适用；MySQL、Java 两节只给用得上的仓库带），不配 profile 也追加进每份 worker 任务书；不要就在 `config.json` 写 `"default_coding_rules": false`，只关这一份。
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

怎么写一个新的团队 profile、每个文件写什么，见包里的 [`profiles/README.md`](../profiles/README.md)；`profiles/example/` 可以当例子。
