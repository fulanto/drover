---
name: drover
description: drover 的派发流程，给编排者（orchestrator）/ 子编排者用：通过 Herdr 为每个条目建 worktree、启动 claude 或 codex agent、追踪到有结果、验收、在集成 worktree 里统一检查和推送。由 `drover orch-start` 起的编排者在开场消息里要求使用它；用户写 `$drover …` 时也用它。主工作台的主会话（工作目录是 drover 配置的主工作台、AGENTS.md 里有 drover 路由区块的那个会话）做路由时不要加载它：按 AGENTS.md 的步骤写 request.md、跑 `drover orch-start` 就够了，流程细节交给编排者。
---

# drover（Codex 入口）

流程正文只有一份：主干 `~/.drover/pkg/flow/dispatch.md`（drover 包里的 `flow/dispatch.md`，install.py 把包装到 `~/.drover/pkg`；Claude Code 的 `/drover` 命令 `~/.claude/commands/drover.md` 也软链到它），加上同目录下走到那一步才读的几份（`integrate.md`、`wrapup.md`、`sub-orch.md`、`review.md`）。这个文件只告诉你在 Codex 里怎么用它们。

1. **完整读主干** `~/.drover/pkg/flow/dispatch.md`（分几段读完，不要只读开头），然后照它做；拆出去的几份不用先读，主干在走到那一步的地方写了先读哪份。开头 frontmatter 里的 `allowed-tools` 等字段是给 Claude Code 的，忽略。
2. 你跑在 Codex 里：正文里和宿主有关的做法（追踪 agent、向用户提问、长命令、子代理、沙箱、开始前要先读的规则和记忆）一律按主干文末 **「附录 B：Codex」**。
3. 正文里的 `$ARGUMENTS` 是调用方给的参数：编排者是开场消息里"`$ARGUMENTS` 就是："后面那串（以 `--orch <slug>` 开头，子编排者还带 `--parent <slug>`）；用户直接写 `$drover …` 时是后面的文字。
4. 第 0 节要加载的 herdr 技能：用 `$herdr`（`~/.codex/skills/herdr`；设了 `CODEX_HOME` 时在 `$CODEX_HOME/skills/herdr`。install.py 装的时候已有就不动，没有时软链到 `~/.claude/skills/herdr` 或用 `herdr --skill` 生成）；没列出来就直接读 `~/.codex/skills/herdr/SKILL.md`。
5. drover CLI 是 `~/.drover/bin/drover <子命令>`，输出 JSON；`<子命令> --help` 查参数。
