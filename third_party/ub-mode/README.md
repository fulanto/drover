# ub-mode（第三方原文）

- 来源：https://github.com/unclebob/ub-mode ，提交 `3da62bf3fcfd2034de41f30d920d2739e566171f`
- 许可证：MIT，Copyright (c) 2026 Robert C. Martin，全文见同目录的 [`LICENSE`](LICENSE)
- [`SKILL.md`](SKILL.md)、`LICENSE` 是这个提交里的原文，一字未改；ub-mode 自己的 README 没有拷进来

## drover 怎么用它

drover 不直接加载这份 `SKILL.md`，而是把其中的规则改编成中文，放进包默认编码规范 [`flow/coding-rules.md`](../../flow/coding-rules.md) 的"通用原则"一节（节名后括号里留着英文原名，可以和这里对照）。`drover brief` 把它追加进每份 worker 任务书，`config.json` 写 `"default_coding_rules": false` 可以整份关掉。

改写过的几节：

- Attack the Premise：原文"数一数哪些参与者持有失衡、去掉那个分配"不好照做，改成"同一个前提下的两个修法在同一关都失败时，先用一句话写出这个前提、核实它，再动下一个修法"。
- Crapper / Dryer / Mutator：原文要先装 Uncle Bob 的三个工具，改成不依赖工具的说法（函数复杂到难测就拆开或补测试；改到的地方不留重复；补没覆盖的、删死代码），装了这些工具的按原阈值。
- Model the Domain：术语表改成"项目里已有就维护"，免得 worker 在别人的仓库里新建文件。
- Type System Discipline：标明只对有类型系统的语言。
- TDD：标明只用于改行为的代码；写不出诚实的测试时不写坏测试，并在报告里说明。

没收的几节（Practice 一组）：

- Keep a Log：drover 的报告协议（verification 逐条对应完成证据）已经覆盖。
- Guard the Context Window：drover 自己的分层（主会话、编排者、worker）就是这件事。
- Worktree Cleanup：和 drover 冲突，drover 只在用户说"清理 / 收尾"时用 `drover cleanup` 删。
- Commit and Push：和 drover 冲突，worker 不提交不推送，推送要用户授权、先确认目标分支；"提交信息写清为什么、改了什么、怎么验证的"这半句收进了 `flow/dispatch.md` 第 9.1 节按条目提交那一步。
