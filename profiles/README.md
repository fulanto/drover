# 团队 profile 和 playbook

## 规则放哪

先分清是谁的规则：

- **项目自己的规则**（构建 / 测试命令、目录约定、编码规范、迁移怎么写）优先写进项目仓库里提交了的 `AGENTS.md` / `CLAUDE.md`。所有在那个仓库干活的人和 agent（不管用不用 drover）都按它，编排者分析任务时也会读（dispatch.md 第 2 节）。
- **团队 profile** 只放跨项目、和 drover 流程有关的：推送前要确认什么、哪类改动验收时多看什么、编排者执行迁移的授权例外和步骤、哪些命令基线本来就有失败。
- **个人覆盖**（`config.json` 的 `playbook_dirs`）放只对你自己成立的：本机的镜像源、JDK / mvn 路径、你自己的习惯。

drover 读的规则分三层（包默认又分流程和编码规范两份），后面的覆盖前面的：

| 层 | 在哪 | 放什么 | 谁维护 |
|---|---|---|---|
| 包默认 | `flow/dispatch.md`、`bin/drover` | 和团队、仓库无关的通用流程：建 worktree、派发、追踪、验收、集成推送；安全规则（worker 不执行迁移、不写共享 / 生产数据库，数据库写操作默认升级给用户）；需要团队 / 仓库规则的地方挂"按 playbook 做"的钩子 | drover |
| 包默认编码规范 | `flow/coding-rules.md` | 通用的编码规范，每条只在改到相关代码时适用：通用原则（改编自 Robert C. Martin 的 ub-mode，原文和许可证见 `third_party/ub-mode/`；总带）、MySQL 建表的命名 / 注释 / 默认值 / 索引名前缀、Java 的 import 和不用内部类（这两节只给用得上的仓库带，见下面"谁读、什么时候读"）。`config.json` 写 `"default_coding_rules": false` 可以整份关掉，上一行的流程和安全规则不跟着关 | drover |
| 团队 profile | 团队自己仓库里的一个目录（推荐），或包里的 `profiles/<名字>/` | 团队稳定的、和 drover 流程有关的规则：对团队所有仓库适用的约定、编码规范、各仓库的 playbook（迁移授权和步骤、worktree 位置、基线有失败的命令） | 团队 |
| 个人覆盖 | `config.json` 的 `playbook_dirs` 里列的目录，按顺序 | 只对你自己成立的补充或改写 | 你自己 |

经常变的约定（比如各仓库这阵子推哪个分支）不进 profile：profile 只写"推送前确认目标分支"的钩子，具体值以用户每次的授权为准。

## 目录布局

团队 profile 和每个个人覆盖目录的布局相同，文件都是可选的：

```
<profile 目录>/
├── general.md              # 对所有仓库适用、编排者读的规则
├── coding-rules.md         # 编码规范：brief 追加进每份 worker 任务书
└── repos/
    └── <仓库目录名>.md      # 仓库专属 playbook，编排者读（文件名就是仓库目录名，如 example-app.md）
```

包里带一份示例 profile [`example`](example/)：虚构团队的示例，示范三种文件怎么写。编码规范的默认一层不在 profile 里，在 [`flow/coding-rules.md`](../flow/coding-rules.md)：不用配 profile 也生效，可以用 `config.json` 的 `default_coding_rules` 开关关掉（见上表）。

谁读、什么时候读：

- `general.md` 和 `repos/<仓库名>.md`：编排者（orchestrator）在分析任务时跑 `drover playbook --repo <仓库>`（dispatch.md 第 2 节），按层（团队 profile → `playbook_dirs` 依次）拼好输出，后读的优先。里面的仓库约定写进计划，给 worker 的约束写进任务段的"不做"，构建 / 测试命令原样写进任务段和验收的 `--verify`。第 3.1 节（worktree 位置）、第 7 节（迁移授权例外）、第 9.1 节（基线、迁移步骤、推送目标分支）都按它做。
- `coding-rules.md`：`drover brief` 自动追加，每层一节，标题写明来源。顺序是包默认（`flow/coding-rules.md`，"## 编码规范（drover 默认；必须遵守，仓库规范冲突时按仓库的）"）→ 团队 profile（如"## 编码规范（团队 profile：example；必须遵守）"）→ 个人覆盖，后面的优先。claude、codex 两种 worker 都加：两个宿主都不会自动加载它。另外 `~/.claude/CLAUDE.md` 的"## 编码规范"一节只给 codex 追加（claude 会自动加载 CLAUDE.md）。包默认那份按语言只带相关的：通用原则总带；Java 一节在任务书涉及的任一 worktree 根目录有 `pom.xml` / `build.gradle` / `build.gradle.kts` 时带；MySQL 一节在任一 worktree 有被 git 跟踪的 `.sql` 文件、或依赖清单（`pom.xml`、`build.gradle*`、`package.json`、`requirements*.txt`、`pyproject.toml`）里出现 mysql / mariadb 字样时带（只有 ORM 不算：ORM 可能连别的数据库）。`drover playbook` 输出的 `coding_rules` 按同样的顺序列出这些文件，`default_coding_rules` 写明包默认那份在哪、开没开、按这几个 `--repo` 会带哪几节（`sections`）。
- 这份 `README.md` 只给人看，命令不读。

几条写法约定：

- 给维护者看的说明写成 HTML 注释 `<!-- … -->`，读出来时会去掉，不进 playbook 输出和任务书。
- `coding-rules.md` 会放在 brief 的一个 `##` 小节下面，正文里别再写 `##` 级标题，用 `###` 和列表。
- `coding-rules.md` 里只对 MySQL 或 Java 仓库成立的条目，可以像包默认那份一样分节：独占一行写 `<!-- drover:section mysql -->` 或 `<!-- drover:section java -->`，到下一个标记或文件末尾是一节，brief 按同样的判断决定带不带；第一个标记之前的部分总带。只认这两个名字，别的名字的节不会带。
- 每条规则写一句为什么（出处，或者要防的问题），以后好判断还成不成立。
- 写清楚是给编排者的还是给 worker 的：给 worker 的约束单独列一节，编排者会照抄进"不做"。

## 选用哪个 profile

`~/.drover/config.json`：

```json
{
  "workspace": "~/work",
  "repo_dirs": ["~/work/company", "~/work"],
  "profile": "~/work/team-profile",
  "playbook_dirs": ["~/my-playbooks"]
}
```

- `profile` 写路径（带 `/` 或以 `~` 开头）→ 用那个目录，团队把 profile 放在自己的仓库里维护时用；相对路径按 `config.json` 所在目录算。写名字（如 `"example"`）→ 用包里的 `profiles/<名字>/`。`null` 或不写：只有包默认规则（含包默认编码规范）。
- 切换：`python3 install.py --profile <名字|目录>`（只改这个键，改之前备份），`--profile none` 去掉。也可以直接编辑文件，下一条 `drover` 命令就生效。
- 配置了但目录不存在时，`drover playbook` 报 `ok: false`、`python3 install.py --check` 报 ✗、`drover preflight` 的 `profile.error` 写明怎么修：这时迁移规则会悄悄丢掉，不能将就用。
- 查看现在生效的内容：`~/.drover/bin/drover playbook --repo <仓库> --text`。
- 想先看看效果：`python3 install.py --profile example`，再对一个叫 `example-app` 的仓库目录跑上面那条；看完 `--profile none` 去掉。

## 写个人覆盖

1. 建一个目录（比如 `~/my-playbooks`），按上面的布局放文件，只放你要补充或改写的。
2. 手动把它加进 `config.json` 的 `playbook_dirs`（install.py 没有对应参数）。可以写多个，按顺序读，排在团队 profile 之后。
3. 覆盖是"后读的优先"：同一件事，团队 profile 和你的文件说法不同时，编排者按你的。要整条作废就在你的文件里明说"不按团队 profile 的 X 做，改成 Y"。

## 写一个新的团队 profile

1. 在团队自己的仓库里建一个目录（推荐：跟团队仓库一起版本管理，不用改 drover 包），布局同上，可以从 `profiles/example/` 复制一份改。
2. `general.md`：对团队所有仓库都成立、编排者需要知道的规则。比如推送前要确认什么、哪类改动验收时要多看什么、哪些操作在团队里有授权例外（只有仓库 playbook 写了例外的才例外）。
3. `coding-rules.md`：团队编码规范，worker 必须遵守的那几条。只属于某个项目的，写进那个项目的 `AGENTS.md` / `CLAUDE.md`。
4. `repos/<仓库名>.md`：仓库专属、和 drover 流程有关的。仓库自己的 `AGENTS.md` / `CLAUDE.md` 已经写了的不用重复；没写的，建议有一节"构建与测试"：能直接复制的构建、类型检查、相关测试、全量测试命令各一行，编排者原样写进 worker 任务段和验收的 `--verify`（dispatch.md 第 2 节第 1 步），worker 不用自己摸索 JDK、mvn 在哪。要设环境变量的写成 `env VAR=值 <命令>`（`accept --verify` 不经 shell，裸写的 `VAR=值 <命令>` 跑不起来）；JDK、mvn 路径这类因机器而异的放个人覆盖目录（`playbook_dirs`）里同名仓库的文件，团队 profile 只写和机器无关的部分。dispatch.md 里留了钩子的地方也都可以写：
   - worktree 位置（仓库规范只允许某些目录时）；
   - 数据库迁移的授权例外和执行步骤（哪个环境、谁执行、执行前后核对什么、执行记录写到哪）；没写的仓库，执行迁移一律升级给用户；
   - 整体检查时哪些命令基线本来就有失败（放 `--verify-baseline`）；
   - 给 worker 的约束（比如"只写迁移文件，不执行"）。
5. 用 `python3 install.py --profile <目录>` 切过去，`drover playbook --repo <仓库> --text` 看输出对不对。
