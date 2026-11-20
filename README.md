# drover

多 agent 派发体系，跑在 [Herdr](https://herdr.dev) 上，Claude Code 和 Codex 都能用：你在一个会话里提需求，drover 给每个任务起一个编排者，编排者再派一群 agent 在各自的 git worktree 里干活；你只看一页汇报、只拍板。

## 让你的 agent 来安装

**如果你是 agent，用户让你安装 drover**：先完整读 [`docs/agent-install.md`](docs/agent-install.md)，按它一步步做。还没 clone 的话，先 clone 到 `mktemp -d` 建的临时目录里读，正式位置要问用户。不要直接跑 `./install.sh`：首次安装在非交互的 shell 里必须带 `--workspace`，这个目录要先问用户；安装还会改用户的 `~/.claude`、`~/.codex`、`~/.drover` 和主工作台。问完就停下等用户回答；dry-run 的输出原样给用户看，用户明确说装才装。

**如果你是人**：把下面这段话发给你正在用的 Claude Code 或 Codex：

```
帮我安装 drover：https://github.com/fulanto/drover 。先 clone 到临时目录里读说明，按仓库 README 开头给 agent 的说明来，问完问题先停下等我回答，安装前把演练结果原样给我看。
```

为什么要整段发：实测 Claude Code 只给地址也能按说明装好，Codex 不带后半句容易不等你确认就装，所以两个都建议整段发。它会先问你几个问题（drover 默认放在 `~/.drover/src`；以后在哪个文件夹打开 agent 提需求，会让你选），给你看演练结果，你确认后才装。想自己装，看[安装](#安装)。

---

![drover](docs/images/01-cover.jpg)

## 它解决什么

drover 的名字取"赶牧群的人"：Herdr 管牧场，drover 负责把一群 agent 赶到该去的地方。drover 既是这套体系的名字，也是它的命令（Claude Code 的 `/drover`、Codex 的 `$drover`）和 CLI（`~/.drover/bin/drover`）的名字。

**人是瓶颈。** 一个 agent 开一个终端的时候，人要盯着每个窗口：拆需求、开分支、贴 prompt、批弹窗、看报告、跑测试、合代码，agent 越多人越忙。drover 把这些活交给每个任务自己的编排者（orchestrator），人只剩三件事：

1. **提需求**：在主工作台的会话里直接说，不用写命令；
2. **看一页汇报**：每个任务一份不超过 40 行的 rollup；
3. **拍板**：只有要你决定的事（范围、方向、推送、删东西）才会来找你。

**离开电脑也不断线（选装）**：安装时加 `--oncall`，扫一次码配对飞书。之后主会话需要你时推到你的飞书；你在飞书里直接发消息，就是在跟主会话说话：派活、问进度、拍板、说"提交推送""收尾"都行。见 [docs/oncall.md](docs/oncall.md)。

## 架构

一共三层：**主会话**就是 drover 本身，负责路由、问答、审核、转达；**编排者**负责一个任务的全程编排：分析、拆条目、建 worktree、起 worker、追踪、按证据验收、集成和推送；**worker** 在各自的 worktree 里写代码。

[![总体架构：用户只和主会话说话；主会话用 orch-start 起编排者、只读 rollup；编排者通过 drover CLI 和 herdr 在各自的 worktree 里起 worker；改动经 integrate 归集到集成 worktree 后 push 到上游](docs/images/architecture-overview.svg)](docs/images/architecture-overview.svg)

**总体架构**（点图看大图）：用户只和主会话说话；主会话用 `orch-start` 起编排者、用 `watch --main` 追踪、只读 rollup，先审再转；编排者通过 `bin/drover` 和 herdr 指挥 worker，三层都可以是 Claude Code 或 Codex。worker 在自己的条目 worktree 里干活，改动最后由 `integrate` 归集到编排者的集成 worktree，再 `push` 到上游。左上角是主会话旁边的看板 tab 和后台自动更新；赤陶色是飞书 oncall，只为主会话推送，编排者和 worker 卡久了才升级。

[![一次派发的时序：用户说需求，主会话复述后用 orch-start 起编排者；编排者对每个条目执行 wt、deps、agent-start、brief --send，worker 写报告，accept 验收，rollup 交回主会话；主会话先审再转给用户；用户说提交推送后编排者执行 integrate、accept、push](docs/images/dispatch-sequence.svg)](docs/images/dispatch-sequence.svg)

**一次派发的时序**：实线是命令或消息，虚线是写文件、对方去读；赤陶色箭头是编排者发出的 CLI 命令。虚线框里那一段对每个条目各走一遍，同时进行。

它是一步步长出来的：一个 agent 一个终端 → 引入 Herdr 管多个 agent → Herdr 多 worktree 并行 → 把重复操作收进脚本（drover 的前身 dispatch）→ 交给编排者，主会话只做路由 → Codex 也能当宿主 → 打包成独立的 drover 包 → 离开电脑：内置飞书 oncall。每一步为什么这么改，见 [`docs/architecture.html`](docs/architecture.html)（技术架构与流程说明，配插画；clone 下来用浏览器打开）。

## 安装

**支持的系统**：macOS 完整支持；Linux 能装，但没有实测；Windows 暂不支持，可以在 WSL2 的 Ubuntu 里按 Linux 的方式试（未实测）。

**依赖**：必需 `herdr`、`git`、`python3`（3.9+），以及你选的宿主 `claude` 或 `codex`；飞书 oncall 另要 Node（18+，收飞书回复要 22+）。缺的 herdr、claude / codex、Node，install 问过你之后用官方安装脚本一次装齐（都在家目录、不用 sudo）；缺 git、python3 时它只给装法，要你自己装。完整的依赖表见 [docs/install.md](docs/install.md#依赖)。

默认 clone 到 `~/.drover/src`。默认的 link 模式会把 `~/.drover/pkg` 软链到这份 clone，装完别挪、别删：

```bash
git clone https://github.com/fulanto/drover.git ~/.drover/src
cd ~/.drover/src
./install.sh --dry-run   # 先看计划：每一项的路径、现状、要做什么、会不会备份
./install.sh             # 正式安装
./install.sh --check     # 检查安装状态和依赖
```

**主工作台**（`--workspace`）是以后开主会话、提需求的目录：

- 推荐用放代码仓库的那个上级目录：仓库都在 `~/code/xxx`，主工作台就是 `~/code`。
- 不能是家目录（或它的上级），install 会直接拒绝。
- 第一次安装时终端里会问，没有默认值；不在交互终端里跑（agent 的 shell、管道、CI）时要用 `--workspace` 指定。

常用参数：

- `--host claude|codex|both`：装哪个宿主的那一套，不传时按本机装了什么自动判断；
- `--mode copy`：复制一份装进去，装完 clone 可以删（默认 link）；
- `--repo-dir DIR`：仓库分散在几个目录时，补充按名字找仓库的目录；
- `--profile NAME|DIR`：团队 profile（团队规则、编码规范、各仓库的 playbook）；
- `--oncall`：接飞书 oncall；
- `--install-deps`：缺的依赖不问、直接装。

装好之后 drover 会在后台自己更新（见 [docs/auto-update.md](docs/auto-update.md)），也可以手动 `~/.drover/bin/drover update`。卸载先 `./install.sh --uninstall --dry-run` 看会撤掉什么，再 `./install.sh --uninstall`。每个参数、install 会改哪些地方、备份和回滚见 [docs/install.md](docs/install.md)。

## 开始使用

主会话要开在 Herdr 的 pane 里（install 汇总的"下一步"也是这几步）：

1. 开一个新终端，运行 `herdr`（不在 PATH 里就用 `~/.local/bin/herdr`）。
2. 第一次启动会自动开一个 workspace，点一下 pane，`cd <主工作台>`。
3. 启动 `claude`，第一次按浏览器提示登录；用 Codex 的话先 `codex login`，再 `codex -s danger-full-access`（原因见 [Codex 注意事项](docs/usage.md#codex-注意事项)）。
4. 把"是否信任这个文件夹"、MCP server 批准之类的弹窗点掉，以后起编排者才不会卡在这里。
5. 离开时按 `ctrl+b` 再按 `q`，agent 照样在跑；下次运行 `herdr` 就回来了。

然后直接说需求，不用写 `/drover`：

```
你：@order-service @web-admin 订单列表加一个"按渠道筛选"，后端接口和前端页面都要改
```

主会话的第一条回复先用自己的话复述目标，接着就交给新的编排者，不等你确认；复述错了直接纠正。之后：

- **看进度**：主会话旁边的"看板"tab 一行一个任务，tab 名就是汇总（比如"看板·等你2·完成1"）；也可以直接问"进度怎么样"。
- **拍板**：编排者要你决定的事，主会话审过再转给你，你的答复原样转回编排者。
- **提交推送**：说"提交推送"，编排者在集成 worktree 里整体检查一次、同步上游再推；不说就不提交。
- **收尾**：说"收尾"，编排者先出收尾汇总（变更、提交、数据库、配置、上线），再核实内容都在远端后清理 worktree 和分支。

参数（`--agent`、`--orch`、`--review`、`--effort`）、看板、收尾的细节见 [docs/usage.md](docs/usage.md)。

## 主要概念

| 概念 | 是什么 |
|---|---|
| 主工作台 | 放代码仓库的那个上级目录（install 的 `--workspace`）。主会话开在这里，路由规则写在它的 `AGENTS.md` 里 |
| 主会话 | 你在主工作台开的 claude 或 codex，就是 drover 本身：路由、问答、审核、转达；要改代码的活交给编排者 |
| 编排者（orchestrator） | 一个任务一个，在 herdr 的 `orch` workspace 里占一个 tab：分析、拆条目、起 worker、追踪、按证据验收、集成和推送 |
| 条目和 worker | 编排者把任务拆成条目，一个条目一个 git worktree、一个 worker（claude 或 codex）；互不重叠的条目同时派发 |
| 集成 worktree | 编排者的，每个仓库一个（分支 `task/<slug>`）：各条目的提交在这里归集、整体检查一次、同步上游、推送 |
| `request.md` / `rollup.md` | 主会话写给编排者的需求；编排者交回的一页汇报（≤ 40 行），主会话只读这一页 |
| 完成证据 | 每份任务书都写清"用什么命令、看到什么结果算通过"，验收时脚本重跑这些命令，不只看 worker 的自述 |
| 看板 | 主会话旁边的 tab，等你 / 进行中 / 完成三张表，一行一个任务 |
| 等你决定 | 要你拍板的事（范围、方向、推送、删东西），只有这些才会来找你 |
| 收尾 / 清理 | 你说了才做：先出收尾汇总，再核实内容都在远端后删 worktree 和分支 |
| heavy | 构建 / 测试命令经 `drover heavy` 按机器余量调度，几个 worker 同时跑也不会把机器挤爆 |
| 团队 profile | 团队的规则、编码规范、各仓库的 playbook；团队成员设好同一个 profile 就拿到同一套规则 |
| 飞书 oncall | 选装：离开电脑时，主会话需要你的事推到飞书，你在飞书里直接跟主会话说话 |

几条核心原则：一个任务一个编排者（主会话的上下文最稀缺）；只交一页 rollup，不转贴 worker 的原始报告；按证据验收；复述但不等确认，只有推送、删东西、写数据库、部署这类不可逆的事才停下来等你。来由见[设计取舍与致谢](docs/development.md#设计取舍与致谢)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/install.md](docs/install.md) | 依赖、安装参数、install 会改哪些地方、检查 / 更新 / 卸载 |
| [docs/auto-update.md](docs/auto-update.md) | 自动更新：更新通道、什么时候查、怎么关 |
| [docs/usage.md](docs/usage.md) | 日常用法：开主会话、提需求、参数、查进度、拍板、提交推送和收尾；Codex 注意事项 |
| [docs/oncall.md](docs/oncall.md) | 离开电脑：飞书 oncall |
| [docs/configuration.md](docs/configuration.md) | `config.json` 的每个字段，团队 profile 和 playbook |
| [docs/quality-tools.md](docs/quality-tools.md) | 验收工具 crapper、dryer；带覆盖率的 crapper 和 mutator 的手动跑法 |
| [docs/reference.md](docs/reference.md) | 子命令速查、目录布局 |
| [docs/troubleshooting.md](docs/troubleshooting.md) | 排障 |
| [docs/development.md](docs/development.md) | 设计取舍、开发约定、发版、自测 |
| [docs/agent-install.md](docs/agent-install.md) | 写给 agent 的安装步骤 |
| [docs/architecture.html](docs/architecture.html) | 技术架构与流程说明（配插画，用浏览器打开） |

## 致谢与许可

致谢：[Herdr](https://herdr.dev)（牧场是它的，我们只是赶羊的），code-review-graph（验收时的那张图），以及 Claude Code 和 Codex 两位宿主。默认编码规范的通用原则改编自 Robert C. Martin 的 [ub-mode](https://github.com/unclebob/ub-mode)（MIT，原文和许可证见 [`third_party/ub-mode/`](third_party/ub-mode/)）；验收用的 crapper、dryer 和可以手动跑的 mutator 也出自他（0BSD），drover 不带它们的代码。

drover 本身用 MIT 许可，见 [LICENSE](LICENSE)。
