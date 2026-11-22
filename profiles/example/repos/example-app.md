<!-- 示例仓库 playbook：example-app 是虚构的仓库（Node 服务 + PostgreSQL），命令和脚本名也是虚构的，只示范写法。
     编排者用 `drover playbook --repo example-app` 读到它：文件名就是仓库目录名。
     只写和 drover 流程有关、仓库自己的 AGENTS.md / CLAUDE.md 里没有的：仓库里已经写了构建命令的，第 1 节可以省掉。
     JDK、本地路径这类因机器而异的，放个人覆盖目录（playbook_dirs）里同名仓库的文件，这里只写和机器无关的部分。
     不写任何真实环境的连接方式（连接串、主机、账号）：连库按仓库自己的说明和本地不入库的配置。 -->

# example-app 的 playbook

仓库：example-app（Node 服务 + PostgreSQL，迁移文件在 `db/migrations/`）。按下面做；第 4 节是给 worker 的约束，编排者写进任务段的"不做"。

## 1. 构建与测试（编排者原样写进 worker 任务段和验收的 `--verify`）

- 类型检查：`npm run typecheck`
- 构建：`npm run build`
- 相关测试（条目验收用，不跑全量）：`npx vitest run --changed <BASE_SHA>`
- lint（只查改动的文件）：`npx eslint <改动的文件>`
- 全量测试：`npx vitest run`。基线本来就有失败，只在集成检查（flow/integrate.md 第 3 步）跑，放 `--verify-baseline`，不放 `--verify`。

## 2. 迁移授权例外（对应 dispatch.md 第 7 节"必须升级 → 数据库写操作"）

- 测试环境的迁移不用单独审批：编排者按第 3 节在推送前执行。
- 生产环境不在本地执行，随发布流程执行。
- 改数据（清理、修数据、改用户记录）不在这条授权里，仍按 dispatch.md 第 7 节升级给用户。

## 3. 迁移执行步骤（对应 flow/integrate.md 第 4 步）

时机：集成 worktree 整体检查通过后、推送前。

1. 确认这次的迁移文件都在 `db/migrations/`，文件名里的时间戳比测试环境已执行的最新一条晚。
2. 对测试环境执行：`npm run db:migrate`。
3. 核对：`npm run db:status` 里这几条都是已执行；再做只读查询，核对新表、新列和默认值。
4. 执行记录写到 `docs/migrations/<日期>-<主题>.md`（执行了哪几条、核对结果），单独提交。

## 4. 给 worker 的约束（编排者写进任务段的"不做"）

- 只写迁移文件和对应的测试，不执行迁移，不连任何共享数据库。迁移由编排者按第 3 节执行。
