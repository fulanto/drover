<!-- 示例团队 profile 的通用规则。团队 example 和里面的仓库都是虚构的，只用来示范写法，不是谁的真实规则。
     试用：在 drover 包目录里跑 `python3 install.py --profile example`，再跑
     `~/.drover/bin/drover playbook --repo example-app --text` 看编排者读到什么（要有一个叫 example-app 的仓库目录）；
     不用了就 `python3 install.py --profile none`。
     这个文件放对团队所有仓库都成立、和 drover 流程有关的规则，编排者读；仓库专属的写在 repos/<仓库目录名>.md；
     编码规范写在 coding-rules.md（brief 自动追加给 worker，不用转抄）。像这样的 HTML 注释是给维护者看的，读出来时会去掉。 -->

# example 团队通用规则（所有仓库）

## 推送前确认目标分支（对应 dispatch.md 第 9.1 节第 2、5 步）

- 推送目标分支以用户这次的授权为准；没写目标分支就在 rollup 里问，不要默认推 main / master。这里不写具体分支名：发布分支经常换，写死了会过期。
- 定下来的分支同时用作 `integrate --target` 和 `push --target`，记进状态文件。

## 验收时多看的改动（对应 dispatch.md 第 6.1 节）

- 改了数据库结构（迁移文件、ORM 模型）：迁移有没有对应的回滚步骤；新增的非空列有没有默认值，不然对已有数据的表执行会失败。
- 改了对外接口（HTTP 路由、导出函数的签名）：调用方有没有一起改，接口文档有没有跟上。
- 不符合的发回 worker 改；写 request.md、转达这类任务时也把这几条带上。

## 授权例外（对应 dispatch.md 第 7 节）

- 执行迁移、执行 DDL、改共享环境的数据，默认都按 dispatch.md 第 7 节升级给用户。
- 只有仓库 playbook 写了授权例外的才例外（比如 `repos/example-app.md` 第 2 节），按那份 playbook 做；例外只对写了它的仓库有效，不外推到别的仓库。
