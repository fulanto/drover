# 试集成、集成与推送

这一份是编排者流程主干（`~/.drover/pkg/flow/dispatch.md`）第 9.1 节拆出来的，走到这一步时读：所有条目验收通过、要请用户批推送之前（先做下面的"试集成"）；主会话转达了用户提交 / 推送的明确要求（带用户原话）时；你是子编排者、条目验收都通过要请上层批推送时，或收到上层转达的"提交推送"时；`wt` / `integrate` 因为"新一轮的起点"核实不过被拒时（看文末"新一轮的起点"）。

**推送只有一条路**：按条目提交 → `drover integrate` 建集成 worktree → `accept --integration` 整体检查 → 迁移 → `drover push`。用户授权之前不提交：条目验收（主干第 6.1 节）都通过后，先做一次试集成（下一节），把各条目没提交的改动在最新上游上叠起来、跑和正式集成同样的整体检查，结果写进请用户批推送的 rollup；试集成永远不推，推送照旧只走上面这条路。

## 试集成（请用户批推送之前；不提交、永不推）

用户批推送时看到的应该是合在一起、验过的结果：各条目各自验过，合在一起仍可能坏（冲突；一个条目删了另一个还在调的函数；一个测试断言了另一个条目会改的行为），只有叠起来才看得出来。每个要推的仓库、每条目标分支做一次。

1. **叠**：

   ```bash
   ~/.drover/bin/drover trial --repo <仓库> --slug <slug> --target <上游分支> --from task/<slug>-<条目1> --from task/<slug>-<条目2> ...
   ```

   `--from` 的顺序就是之后 integrate 的顺序。脚本先 fetch，从最新的 `origin/<上游分支>` 建（或复用）试集成 worktree（分支 `task/<slug>--trial-<目标>`），按顺序把每个条目的改动一步步叠上：起点之后已提交的提交逐个，再加条目 worktree 里没提交的改动（做成不挂任何分支、tag、reflog 的临时提交对象）；每步 `cherry-pick -n`，只改 index 和工作区，HEAD 永远停在上游，条目分支、集成分支、试集成分支都不多一个提交，条目的 index 和工作区不动。合并和正式 integrate 的 cherry-pick 是同一套，正式集成会遇到的冲突这里都会遇到。最后装依赖。可以重复执行。看返回：
   - 冲突就停下，返回冲突文件：在试集成 worktree 里解完 → `git -C <试集成WT> add <文件>`（没有进行中的 cherry-pick，不用 `--continue`；不要 reset、不要提交）→ 再跑一次同样的 `trial`，接着叠剩下的。冲突多、要动脑的，可以在试集成 worktree 的 pane 里起一个 worker 解。
   - git 拒绝（没有冲突、没叠上，比如会被覆盖的未跟踪文件）：把挡路的处理掉，重跑。
   - 起点带着目标分支没有的提交（`foreign`）：和 integrate 一样交用户定（`--target` 还是 `--carry-base`）；定了 `--carry-base` 的不做试集成，批推送后直接正式集成、在集成 worktree 上整体检查。
   - 重跑时和上次对得上的步骤不重叠（全对上就什么都不动，`applied` 为空）；某个条目之后又改了（发回 worker 修过），从它那一步起重叠，叠完之后在试集成里做过的改动先存成 `saved_fixes` 的 patch（`git -C <试集成WT> apply --3way <patch>` 打回去）。上游之后又动了不换起点（`upstream_moved`，已解的冲突不白解）；要在最新上游上重来加 `--restart`。
2. **合并才有的修正**：只有合在一起才需要的改动（比如一个条目删了另一个条目还在调的函数）：能归到某个条目的发回那个条目的 worker 改，改完重跑 `trial`；两边都在才能改的，直接在试集成 worktree 里改（不提交）。重跑 `trial` 时 `fixes` 列出叠完之后改过的文件。
3. **整体检查**：命令和下面正式集成第 3 步一样，只是 `--wt` 换成试集成 worktree、存档换成 `accept-trial-<仓库名>.json`（`trial` 返回的 `next` 里有）。结论记进试集成状态：检查的是工作区整体状态写成的树（没提交，不是 HEAD 的树）；还没叠完、有没解决的冲突时不检查。没过就照主干第 6.1 节发回对应条目的 worker（或者改合并修正），重跑 `trial` 再检查。
4. **写进请用户批推送的 rollup**："需要你决定"里问推送那一条带上"验证"：一行试集成的整体检查（命令 → 结果 → 编排者重跑 → `accept-trial-<仓库名>.json`，写明试集成的树：`integration_check.tree` 的短 sha），一行解了哪些冲突（`resolved`：哪个条目、哪些文件、怎么合的），一行合并才有的修正（`fixes`，没有写"无"），写法同主干"编排者模式"的"验证"。然后照常标"等你决定"问推送；主会话转达前按这几行审（routing.md"转达前先审"第 5 条），没有试集成的会打回。
5. **批了之后**：照下面第 1–5 步正式集成。按条目提交时把条目 worktree 里的改动全部提交（和试集成叠的那份一样），integrate 遇到试集成里解过的同一个冲突（同一份提交、摘之前的树也一样）就按试集成解过的直接还原、接着摘（返回 `replayed`），不用再解。integrate 返回的 `trial` 写明集成完的树和试集成验过的树对不对得上（`matches_head`、`differs`）：对得上就跳过第 3 步的整体检查，push 认试集成的检查（`check_from: trial`）；只差合并修正时按返回的 `next` 补一个"集成修正"提交（`git -C <集成WT> restore --source=<试集成的树> --staged --worktree :/` 再提交），树就相等了；差的不止这些（批了之后条目又改过）就照第 3 步跑。
6. 试集成 worktree 留着，收尾时 `cleanup --slug` 和别的 worktree 一起核实、清掉（没提交的内容都在推上去的分支里才删）；不推了、要提前丢掉的用 `cleanup --slug <slug> --probe --wt <试集成WT>`（先存 patch）。

## 正式集成与推送（用户明确要求后）

同一个任务派出去的所有条目，最后都回到**编排者自己的集成 worktree**（每个仓库一个，分支 `task/<slug>`），在这里汇总一次、同步上游一次、整体检查一次、执行迁移、推送。不在源仓库的主检出里合并，不并进某个 worker 的 worktree，各条目也不各自 rebase。只有一个条目的任务也走这条路，流程统一。跨仓任务每个仓库各走一遍，推送顺序按主干第 2 节"跨仓任务"定；某个仓库交给了子编排者的，那个仓库的这一节由它来做，你按顺序给它发"推送"（sub-orch.md）。

1. **按条目提交**：每个条目在自己的 worktree 里提交（编排者用 `git -C <WT> add <文件>` + `commit -F <消息文件>`，或者 brief 带 `--commit-allowed` 让 agent 提交），提交信息按仓库规范，写清为什么、改了什么、怎么验证的。做过试集成的，把条目 worktree 里的改动全部提交（和试集成叠的那份一样，冲突才能照试集成还原、树才对得上）。一条 worker 分支里混了两个条目的改动时，按 `-U0` hunk 过滤后 `git apply --cached` 拆开提交。integrate 不收有没提交改动的条目 worktree（报"先按条目提交"）。
2. **集成**（git 联网命令要在沙箱外执行：Claude Code 用不经沙箱的 Bash，Codex 编排者本来就是 danger-full-access；偶发 `Permission denied (publickey)` 脚本会自动重试三次）：

   ```bash
   ~/.drover/bin/drover integrate --repo <仓库> --slug <slug> --target <上游分支> --from task/<slug>-<条目1> --from task/<slug>-<条目2> ...
   ```

   脚本先 fetch，再从最新的 `origin/<上游分支>` 建集成 worktree（已有这个目标的就复用），按 `--from` 的顺序摘每个条目分支起点（主干第 3.1 节 `wt` 记的）之后、集成分支里还没有的提交，最后装依赖。可以重复执行。每个（仓库，目标分支）一个集成 worktree：同一轮推第二条分支只换 `--target`，脚本自动另建 `task/<slug>-<目标>`（`branch_note` 写明）。建、复用、"已应用"怎么核实见 docs/reference.md 的 integrate。看返回：
   - 起点带着目标分支没有的提交（条目从别的分支起、主检出有没推的本地提交）→ `ok: false`、一个都不摘，列出这些提交（`foreign`）和两种做法，写进 rollup 交用户定：`--target <起点那条线>`，或 `--carry-base "<用户的原话或理由>"` 带上；没有起点记录的旧条目照旧摘、`warnings` 提示，`picked` 逐个核对。
   - 遇到冲突就停下，返回冲突文件：在集成 worktree 里解完 → `git -C <集成WT> add <文件>` → `git -C <集成WT> -c core.editor=true cherry-pick --continue` → 再跑一次同样的 integrate。试集成里解过的同一个冲突不停，按试集成解过的还原、接着摘（`replayed`，状态里记 `replayed_from_trial`）。不要 `--abort`，要放弃某个提交先写明理由；abort / reset 过（HEAD 没前进）的冲突提交重跑时会重摘、列在 `abandoned`。cherry-pick 没能开始（git 直接拒绝、没有冲突文件，比如集成 worktree 里有会被覆盖的未跟踪文件）时状态记 `failed`、报错带 git 的输出：把挡路的原因处理掉后重跑同样的 integrate，会重试这个提交。
   - 做过试集成的看 `trial`：`matches_head: true` 是集成完的树就是试集成验过的树，第 3 步跳过；`false` 时 `differs` 列出差的文件，怎么补见上面"试集成"第 5 步（返回的 `next` 也写了）。
   - 目标分支远端还没有（新建远程分支，比如按日期建的 release）→ `ok: false`：从哪条分支建写进 rollup 交用户定，定了加 `--create-from <远端>/<源分支>`，push 照常核对和记录。
   - 新建集成 worktree 时同样核实起点含这个任务上一轮推到远端的提交（返回里有 `round_check`，见文末"新一轮的起点"）；`--from` 的条目分支里已有的不算缺（一轮推两条分支时第二条集成的就是同一批条目提交，条目 worktree 用 `--carry` 带上过的也在条目分支里）。还缺 → `ok: false`、不建，交用户定：加 `--carry "<理由>"` 先带上再摘条目，或者 `--target` 改回上一轮的分支。
   - `source_sync` / `warnings` 和主干第 3.1 节一样看；`base` 是下一步整体检查的 `--base`；`source_workspace_opened` 记进状态文件。
3. **整体检查一次**（集成完的树等于试集成验过的树时跳过：push 认试集成的检查，见第 5 步）：`drover accept --wt <集成WT> --base <base> --save <slug>/accept-integrate-<仓库名>.json --brief --integration --verify "<tsc>" --verify "<build>" --verify-baseline "<全量测试>" [--verify-baseline "<lint>"] [--verify "<i18n 等仓库门禁>"]`，放后台跑（见附录）。全量测试只在这里跑；负载超时会自动复核（主干第 6.1 节）。`--integration` 不建图、不跑 crapper，改由 dryer 扫全仓找重复；它还把检查的提交、树和过没过记进集成状态（返回的 `integration_check`），第 5 步 push 按它核对，所以整体检查一定带 `--integration`、在干净的（都提交了的）集成 worktree 上跑。通过后把返回的 `head`（这次全量通过的提交）记进状态文件，下面"再跑检查"要用。
   - 有前后依赖的加 `--serial` 逐条跑，比如某条门禁要读 build 的产物，或者两条命令会写同一个产物目录。
   - **重复**（`duplicates`，dryer，相似度 ≥ 0.8）：只留至少一边碰到这次改动行的重复对（`duplicates.pairs`，`文件:起-止` 两两一对；`candidates` 是全仓一共几对）。每一对要么合并，要么在汇报里写不合并的理由，发回对应条目的 worker 改或让它补理由；没碰到改动的旧重复不管。`duplicates.ok: false` 带 `error`（"测不了：…"）**不算不通过**，状态文件记一行；`skipped: true` 是开关关了。

   **和基线比**（`--verify-baseline`）：仓库基线本来就有失败的命令（常见是全量测试、lint；具体哪些，看仓库规范或 playbook）放 `--verify-baseline`，不放 `--verify`。命令通过就和普通 verify 一样；失败时脚本和 `--base` 比失败条目，只看有没有新增（基线上重跑哪些文件、缓存，见 docs/reference.md 的 accept）：
   - 没有新增失败 → 判通过，`note` 写明"失败 N 条全部是基线上本来就有的"，`baseline.fixed` 列出这次修好的（只统计重跑了的文件）；
   - 有新增 → 判失败，`baseline.new_failures` 列出来，发给 agent 修；vitest 新增的失败全是超时时，会先把这些文件单独重跑一次（负载超时）。
   - 基线是单独跑这几个文件，某条用例单独跑能过、整套跑才失败（执行顺序、共享状态）时，会被误报成新增（只会误报，不会漏报），`note` 里有提示。遇到时在集成 worktree 里也单独跑这个文件（`drover heavy --cwd <集成WT> -- <测试命令> <文件>`）：也能过、diff 又没碰它相关的代码，就不是这次引入的，汇报时写明；单独跑仍失败才是真的新增。
   - 支持 vitest、pytest、tsc、eslint。认不出的输出、退出码非 0 却解析不到失败条目、工具自己报的失败数和解析出的条数对不上、输出里看得出提前停了（pytest `-x` / `--maxfail` / 收集出错，vitest `--bail`；去掉再验）、基线不可信（基线 worktree 装依赖失败、基线命令没跑成）一律按失败处理，在 `baseline.error` 里说明。

   **再跑检查按增量定范围**：集成 worktree 已经在同一个上游上全量通过之后，又补了条目、修了一处、改了个名，再跑检查时看这次比上次全量通过的提交（状态文件里记的 `head`）多了什么（`git -C <集成WT> diff --stat <head>`），`--base` 照旧用 integrate 返回的上游 `base`：
   - 只加了条目提交（代码）：JS 仓库 `--verify "<tsc>"` 加 `--verify-baseline "vitest run --changed <head>"`（按 import 关系只跑受影响的测试；基线比对照常，基线上只跑失败的文件；一个测试都没跑到会判不通过，见主干第 6.1 节）；pytest 全量在两分钟以内的照跑全量，更长的跑相关测试文件。
   - 只改了文档：不跑（和第 5 步 push 的 `needs_reverify: false` 同一个意思）。
   - 依赖、配置、测试初始化文件变了（`package.json` / lockfile、tsconfig、vitest / pytest 配置、`conftest.py`、全局 setup）：照第 3 步跑全量。
   - 上游换了（重新 integrate 到了新的上游提交）不算"同一个上游"，照第 3 步跑全量。推送前 rebase 后的补检查见第 5 步，不变。
4. **迁移**（有数据库迁移的仓库）：整体检查通过后、推送前执行。先看授权：仓库规范或仓库 playbook 没写编排者可以执行迁移的（主干第 7 节的例外），执行迁移属于第 7 节"必须升级"，先问用户。有授权时（仓库写了，或用户这次答应了）按仓库写的迁移步骤做（主干第 2 节第 1 步的 `AGENTS.md` / `CLAUDE.md` 和 `drover playbook --repo <仓库>`：怎么改名、在哪个环境执行、执行前后核对什么、执行记录写到哪、生产怎么上），和下面不同的以仓库为准；仓库没写步骤的按下面的默认步骤。生产只走发布流程，不在本地执行（第 7 节）。
   1. **定时间戳**：迁移文件名带时间戳的，执行前一刻按仓库现有文件名的格式定时间戳，`git mv` 改名并提交（这时还没推送），改完立即执行；一批多条的，每条都这样一条条来。提前写好或手工取整的时间戳，可能排在这期间别人已执行的迁移前面，文件名顺序和账本里的执行顺序就对不上了。
   2. **试跑**：先在事务里执行一遍再回滚，确认能跑通。数据库不支持事务里的 DDL 时（MySQL 的 DDL 会隐式提交，回滚不了）跳过这一步，在执行记录里写明。
   3. **执行**。
   4. **核对账本**：迁移工具的账本（如 Flyway 的 `flyway_schema_history`、Alembic 的 `alembic_version`）里有这一条，记录（版本号，有校验和的比校验和）和迁移文件一致。
   5. **只读验证**：用只读查询核对表、列、索引、约束和预期一致。
   6. **写执行记录**：时间、环境、执行了哪些迁移、上面各步的结果，写成文件单独一个提交（不和代码混在一起），放在仓库惯例的位置；状态文件也记一笔（第 6 步）。
5. **推送**（先确认目标分支 `--target`：以用户这次的授权为准，仓库规范或 playbook 有要求的按它；都没写就问，不要默认推 main / master）：

   ```bash
   ~/.drover/bin/drover push --wt <集成WT> --target <上游分支> --slug <slug>
   ```

   - 上游有新提交：脚本 rebase 过去，报告上游改了哪类文件（`upstream_files`：migrations / deps / code / docs）、和我们重叠的文件，**这一次不推**。返回的 `base` 是 rebase 到的新上游，已经回写进集成状态（脚本按 worktree 路径自动找状态文件，`--slug` 可限定），补检查就用它当 `accept --base`。`needs_reverify: false`（只改了文档）就直接再跑一次 push；`true` 就先补检查（带 `--integration`，不补 push 不推）：依赖变了先重跑 `deps`，再跑 tsc 和相关测试，必要时 build（第 2 步之后上游的代码通常和我们不重叠，不用再跑全量）。有 `migrations_note` 时确认上游新迁移和我们已执行的没有依赖。
   - rebase 冲突：解完 → `git -C <集成WT> add <文件>` → `git -C <集成WT> -c core.editor=true rebase --continue` → 用新 base 补检查 → 再跑 push。冲突是人工解的，没补检查时 push 按树核对会拒绝（`base` 已更新）。手动 rebase 过的，push 也会发现上游已在 HEAD 里，自动把 base 更新过来。
   - 上游没有新提交：先核对整体检查：检查通过、检查的就是现在的 HEAD（或者之后只改了文档，列在 `check.docs_only_after`）才 `push HEAD:refs/heads/<上游分支>`，再按完整引用名 ls-remote 核对，`verified: true` 才算推送成功。集成 worktree 上没有对现在这份内容的检查时，认同任务、同仓库、同目标的试集成检查（检查时的树和 HEAD 的树相同或只差文档，返回的 `check.check_from: trial`）；集成 worktree 上对同一份内容的检查没过的，不拿试集成的盖掉。没检查、没过、检查之后又改了代码都不推，`check_gap` 说明差在哪（试集成的树对不上时写明差几个、哪些文件），按它补检查再推；用户明确要跳过时才加 `--unchecked "<用户的原话或理由>"`，理由记进推送记录。集成状态记的目标不是这次的 `--target` 也不推（第二条分支先 integrate）；没有集成状态（不是 integrate 建的，试集成 worktree 也是）的也不推：回到第 2 步用 integrate 建。
   - 推送记录：每次推上去都追加一行到任务的 `<slug>/pushes.jsonl`（分支、提交、推送时的远端地址、覆盖它的整体检查或 `--unchecked` 的理由；字段见 docs/reference.md 的 push），新一轮的起点、`cleanup`、`wrapup` 都读它。任务按 `--slug` → 集成状态所在的任务 → 分支名 `task/<slug>…` 定，都定不了就不推、提示加 `--slug`（分支名不是 `task/<slug>…` 的，比如用户指定了分支名，要带 `--slug`）。
6. 状态文件记下集成 worktree、整体检查结果、迁移执行记录、推送的提交范围；rollup 写结论，状态标"完成"。推送后不自动删任何东西：条目 worktree、集成 worktree、分支、pane 等用户说"清理"或"收尾"时再清（wrapup.md）；多轮任务的下一轮从上一轮推的分支新起 worktree（下面"新一轮的起点"），旧的留着等清理。

## 新一轮的起点

这个任务在这个仓库推过之后，再建条目 worktree（`wt`）或集成 worktree（`integrate`）时用到。

起点 = 这一轮要推的那条远端分支（fetch 到最新），不是默认分支、也不是主检出所在的分支，和发没发布无关。`wt` 按 `--slug`（不传按分支名 `task/<slug>…` 归属）读任务的推送记录 `<slug>/pushes.jsonl`（上面第 5 步）：

- 没传 `--base`：从上一轮推的那条远端分支起。这个仓库推过两条以上（一轮推两条，或中途换过分支）时不猜，`ok: false` 列出各分支最后一次推送，按用户这一轮要推的分支传 `--base`（一轮推两条的从主要那条起，另一条集成时各自核实）。
- 传没传都一样：建之前核实上一轮推到远端的提交在起点里（同一条分支看祖先；换了分支按补丁内容比，挑过去的也算）。不过 → `ok: false`、不建，`error` 可以直接转给用户：缺哪几个提交（短 sha、标题、什么时候推到哪条分支）和两种做法（`missing` / `choices` 是结构化的同样内容）。写进 rollup"需要你决定"交用户定：①带上这些提交在新分支上起：重跑加 `--carry "<用户的原话或理由>"`，新 worktree 建好后先按顺序 cherry-pick 它们（`head_sha` 即 BASE_SHA 在它们之后；理由和带上的提交记进 `<slug>/carry.jsonl`；冲突时停在那个提交上，解完 `git -C <WT> -c core.editor=true cherry-pick --continue` 接着摘剩下的）；②在上一轮的分支上接着改：`--base <远端>/<上一轮的分支>`。
- 远端换过地址（比如仓库从一个托管搬到另一个，新地址上是重建的历史）：推送记录记着推送时的地址 `remote_url`，和现在同名 remote 的地址不同、或者这个 remote 已经没了的，算**推到了别处**：不拿来定起点（"推过两条以上"也只看推到这里的）、不拿来核实，也不会进 `--carry`（那些提交本来就不在新远端里，带上就是把那边的提交搬进现在的仓库）。它们列在 `round_check.other_remote`，`warnings` 里有一句，汇报里提。全推到了别处时起点照第一轮的规则定；有的推到这里时照常核实推到这里的。地址怎么比见 docs/reference.md 的 wt；对不上的结果只是不核实、给提示，不挡人。
- 旧版本记的推送没有 `remote_url`，照旧当作推到现在这个远端核实。缺的提交（`missing` 或 `unknown`）来自这种记录时，拒绝的 `error` 末尾多一段说明：remote 之后换过地址的话这些提交本来就不在新远端里，**不要 `--carry`**；在记录所在的文件里给这几条补上 `"remote_url": "<当时的地址>"` 再重跑，就按推到别处处理。结构化的在 `no_remote_url`（时间、`upstream`、记录所在文件的完整路径 `file`：`pushes.jsonl` 或旧的 `integrate-*.json`、现在的地址）。remote 换没换过、当时是哪个地址，问用户确认后再补，不要猜。有 `remote_url` 的记录缺提交是真的漏了，报错照上面一条，没有这段。
- 没有推送记录（第一轮）：起点照主干第 3.1 节，不变；复用已有 worktree 不核实。`base_from` 非空表示起点是按推送记录定的，汇报里提一句；`round_check.status` 各个值见 docs/reference.md 的 wt。`--dry-run` 不 fetch，按本地记录预测（`round_check.would_refuse`）。
