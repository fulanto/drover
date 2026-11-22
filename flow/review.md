# 复审：codex 做 code review（可选，参数带 `--review` 时才做）

这一份是编排者流程主干（`~/.drover/pkg/flow/dispatch.md`）第 8 节拆出来的，走到这一步时读：带 `--review` 的任务，主 agent 的 DONE 通过第 6.1 节验收之后；或者用户事后要求复审时。没带 `--review`、用户也没要求的不做。

主 agent 的 DONE 通过第 6.1 节验收（`accept --slug --name` 已存 `accept-<agent名>.json`）后，立刻用一条命令起复审：

```bash
~/.drover/bin/drover review --slug <slug> --name <agent名>-rev --pane <主 agent 的 pane_id> --wt <WT_PATH> --base <BASE_SHA> [--focus "<本次改动背景与复审重点，1 到 3 句>"]
```

`review` 按复审者名自己找验收存档（`--accept-json` 可直接指定；怎么找见 docs/reference.md 的 review）。存档的 worktree、HEAD、改动指纹和现在的对不上（验收之后又改过、又提交过，或者是别的条目的）就拒绝，重新 accept 再起；有 verify 失败时也拒绝（验收没通过）：先让主 agent 修好，再重新 accept。`--tests` 一般不用传：脚本从存档的 verify 结果生成"命令、退出码、耗时、输出最后一行"；accept 时没跑 `--verify` 才手写 `--tests "<验证命令和结果，一行>"`。

脚本按固定模板生成复审 prompt（存到 `<slug>/<agent名>-rev.prompt.md`，模板内容见 docs/reference.md 的 review），在主 agent 右侧分屏启动只读 codex，立刻把 prompt 发出去，启动弹窗的处理和第 4 节相同。

**不要先 `review-start` 再手写 prompt**：实测 codex 起好后空等了 8 分钟才收到 prompt（当时编排者被一个审批弹窗卡住），它自己真正干活只用了 3 分 54 秒。中间任何耽搁都会变成 codex 空等，所以 prompt 要先准备好，和启动一起完成。要写的只有 `--focus` 那一两句。

返回值里有 `review_pane_id`、`started_at`、`prompt_sent_at`。然后用 watch 追踪 `<agent名>-rev`（第 6 节），等到 `done` / `idle` 后：

```bash
~/.drover/bin/drover collect --slug <slug> --name <agent名>-rev --pane <review_pane_id>
```

脚本把复审输出原文存到 `<slug>/<agent名>-rev.result.md`，提取 codex 自报的耗时（`codex_worked_for`），并关闭复审 pane。你再读这个文件判断。

High/Medium 问题交回主 agent 修复一轮，修复后的报告重新走第 6.1 节验收（重新建图）。不做第二轮复审。

**耗时分开记**，写进状态文件，供以后评估复审值不值：

- codex 实际耗时：`codex_worked_for`，或者 `prompt_sent_at` 到 watch 输出 `done` 的时间。
- 你自己的耗时：验收通过到 `review` 返回；watch 输出 `done` 到读完结果。这部分偏大说明流程里有手工步骤可以省掉。
