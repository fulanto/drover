# 子编排者

这一份是编排者流程主干（`~/.drover/pkg/flow/dispatch.md`）"编排者模式"里拆出来的，走到这一步时读：你是上层、按主干"子编排者"那几句的条件决定给某个仓库起子编排者时；或者你自己就是子编排者（参数带 `--parent <上层 slug>`，开场 prompt 会让你读这一份）。

子编排者往上只交一份 rollup，不转贴 worker 的原始报告，上层的上下文只留它的汇总。

## 你是上层时

1. 用 Write 写 `~/.drover/<上层 slug>-<仓库>/request.md`：用户原话（从你的 request.md 原样抄）；"目标"：开头是用户的目标，后面接这个仓库要做成的事；这个仓库的条目和范围；你定下的跨仓约定（接口字段、发布顺序、迁移先后）；不做什么。
2. 启动：`drover orch-start --slug <上层 slug>-<仓库> --name orch-<缩写>-<仓库缩写> --parent <上层 slug> --request-file <上一步的文件> --args "@<仓库> <用户写的参数>"`。脚本会在你的状态文件里追加一行子编排者的记录，在子任务目录写 `parent.json`。
3. 用 watch 把子编排者和你自己的 worker 一起追踪。它变成 done / idle，或者 watch 报它的 `rollup:<状态>` 事件时（它写完 rollup 可能还挂着 watch 盯自己的 worker，这时状态行是 `waiting-background(N)`，只有 rollup 事件会叫醒你），读它的 `rollup.md`（直接读文件，或者 `drover rollup --slug <子 slug>` 都行；看别的任务的 rollup 也一样。`drover rollup` 只有主会话读才记成已读，你读不会让主会话漏转，输出开头会多一行"不是主会话，没记已读"）：能定的事你直接定，用 `drover prompt --name <子编排者>` 回复；要用户定的，写进你自己的 rollup 交给用户。你的 rollup 里只写汇总，不转贴子编排者的原文。
4. 用户要求提交推送之后（integrate.md 的前提，没要求就不发），按跨仓推送顺序逐个给子编排者转"提交推送"：写明"用户已要求推送"，带用户原话和这个仓库的目标分支（以用户这次的授权为准，同 integrate.md 第 5 步）；它推完写"完成"再发下一个仓库的。
5. 用户说"收尾"时，给每个子编排者发"收尾"，它们各自先写自己的 wrapup.md 再清理（wrapup.md）。它们的 rollup 都写了收尾的"完成"后，读它们的 wrapup.md（子编排者的汇总，不是 worker 原文，可以读），合进你自己的 wrapup.md：`drover wrapup --slug <上层 slug>` 会自动带上子任务的推送记录，变更总结、数据库、配置、上线这几节从它们的 wrapup.md 里合进来。然后用 `herdr tab close` 关掉它们的 tab，再做你自己的清理。
6. 子编排者上下文用满（rollup 标"需要换编排者"）时，由你用 `orch-start --parent … --args "resume <子 slug>"` 换人。

## 你是子编排者时（参数带 `--parent <上层 slug>`）

- 照主干 dispatch.md 做你这个仓库的条目、验收和集成检查。要决定的事、阶段结论写进你自己的 `rollup.md` 后停下，由上层读、上层转达；不用向用户提问的工具。rollup 开头的"状态："下面加一行 `上层：<上层 slug>`（"标题："、"摘要："这些排在它下面）。停下时照主干"编排者模式"的规则：写"等你决定"不停盯 worker 的 watch，写"完成 / 受阻 / 需要换编排者"之前停掉所有 Monitor 和后台命令；上层靠 watch 的 `rollup:<状态>` 事件收到信号。
- 条目验收都通过后，状态标"等你决定"，"需要你决定"里写"等上层转达提交推送"（附条目验收结果），然后停下；用户没要求之前不提交、不集成（同顶层：integrate.md 在用户明确要求后才做）。收到上层转达的"提交推送"（写明用户已要求推送、带用户原话和这个仓库的目标分支）后先读 `~/.drover/pkg/flow/integrate.md`，按它做完（目标分支以转达里的为准），推完写"完成"；没带原话或目标分支的不做，写进 rollup 问上层。
- 收到上层发的"收尾"后，先读 `~/.drover/pkg/flow/wrapup.md`，照它先写你自己的 wrapup.md 再清理，rollup 写收尾的"完成"后停下；你的 tab 由上层关。
- 跨仓约定和你仓库的代码对不上时，不要自己改约定，写进 rollup 让上层定。
