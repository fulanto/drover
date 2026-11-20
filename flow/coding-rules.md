<!-- drover 默认的编码规范（包默认这一层）。"通用原则"一节改编自 unclebob/ub-mode（MIT，Copyright (c) 2026 Robert C. Martin），
     原文和许可证见 third_party/ub-mode/（那里的 README 写了改写和没收的几节、为什么）。
     drover brief 把这份正文（去掉 HTML 注释）追加进每份 worker 任务书，claude、codex 都加，排在团队 profile、个人覆盖的
     coding-rules.md 前面（后面的优先）；小节标题自动加成"## 编码规范（drover 默认；必须遵守，仓库规范冲突时按仓库的）"，
     所以这里不要写 ## 级标题，用 ### 和列表。
     分节：第一个 drover:section 标记之前的部分（开头一句和通用原则）总带；"drover:section mysql""drover:section java"
     标记开始的两节按这次任务书涉及的 worktree 判断带不带（bin/drover 的 coding_rules_sections：Java 看根目录的
     pom.xml / build.gradle(.kts)，MySQL 看被 git 跟踪的 .sql 文件和依赖清单里的 mysql / mariadb 字样），一节到下一个标记或文件末尾，
     标记本身是 HTML 注释、不进任务书。判断错了只是多带或少带一节；验收按整份核对。
     config.json 写 "default_coding_rules": false 时整份不追加（团队 profile、个人覆盖照旧）；不执行迁移这类安全和流程规则
     在 bin/drover 的任务书模板和 flow/dispatch.md 里，不跟着关。
     编排者验收时也按这份核对（dispatch.md 第 6.1 节第 8 项；路径见 drover playbook 输出的 coding_rules）。
     只放和团队、机器无关的；每条一行，专项的每条开头写清适用条件。 -->

这是 drover 默认的编码规范，只在改到相关代码时适用；仓库自己的 `AGENTS.md` / `CLAUDE.md`、团队 profile、个人覆盖和这里冲突时按它们的。

### 通用原则

每次改动，先让它能用，再把它做对。

先让它能用（First Make it Work）：

- **少做**（Laziness Protocol）：一个决定只放在一处；做能解决问题的最小改动。
- **先打地基**（Foundational Thinking）：先把数据结构定对，再写逻辑；先做对后面每一步都有帮助的事；要是有并发写入会出问题，就不共享状态。
- **测试先行**（TDD；只用于改行为的代码，纯文档、配置不用）：改代码之前先写会失败的测试，看到它因为你预期的原因失败，再做让它通过的最小改动；写不出诚实的测试，就不写坏测试，在报告里说明。
- **从第一性原理重新设计**（Redesign From First Principles）：不把新需求硬接到旧设计上；想清楚如果一开始就有这个需求会怎么建，想透了再分几步交付。
- **质疑前提**（Attack the Premise）：同一个前提下的两个修法在同一关（同一个测试、同一项检查）都失败时，先用一句话写出这个前提并核实它，再动下一个修法。
- **先减后加**（Subtract Before You Add）：加任何东西之前，先把复杂度拿掉。
- **以结果为准**（Outcome-Oriented Execution）：瞄准能核对的最终状态；中途暂时坏掉可以，前提是事先计划好、能撤回。
- **造杠杆**（Build the Lever）：活儿一眼看不出怎么做完的，造一个工具来做；先手工做一个，再让工具做出来的和它一致。
- **架构**（Architecture）：
  - **为领域建模**（Model the Domain）：把领域放进一个让非法状态无法存在的结构里；项目里已有领域术语表的，跟着维护；代码本来就清楚、影响只在局部的，不动它。
  - **守好边界**（Boundary Discipline）：数据在进来的地方检查，进来以后就信任它；业务逻辑放在纯函数里。
  - **用好类型系统**（Type System Discipline；有类型系统的语言）：让非法的值写不出来；外部数据在进来的地方解析一次；不压制编译器的检查。
  - **操作幂等**（Make Operations Idempotent）：跑两次，或者跑到一半崩了再跑，最后的状态都一样。
  - **先分开，再谈串行化共享状态**（Separate Before Serializing Shared State）：不需要同一个可变对象的，各给一份；需要的，只允许一个写入方，并且用结构保证这一点。

再把它做对（Then Make it Right）：

- **修根因**（Fix Root Causes）：先复现，再修原因；只是把崩溃藏起来的防护不算修好。
- **分成能核对的小单元**（Sequence work into verifiable units）：做完一个小单元并核对过，再开始下一个。
- **测行为，不测实现**（Test Behavior, Not Implementation）：测试像使用者那样调用代码，检查使用者会看到的结果；每个函数都返回空时还能通过的测试，扔掉。
- **控制复杂度**（Crapper）：函数复杂到难测，就拆开、补测试，或者两样都做；case 语句值不值得减，掂量一下。
- **消除重复**（Dryer）：改到的地方不留重复。
- **补覆盖、删死代码**（Mutator）：没被测试覆盖的补上；死代码删掉。
- 装了 unclebob/crapper、dryer、mutator 的，按原阈值：CRAP < 10、相似度 ≥ 0.8 的重复要消掉、变异测试的存活者要杀掉。
- **重构**（Refactoring）：先钉住代码现在的行为，再改结构，最后证明行为没变。

<!-- drover:section mysql -->
### MySQL

- **MySQL 表名、列名不用关键字**（改动含 MySQL DDL 时）：建表、加列时，表名和列名不用 MySQL 保留字或非保留关键字（如 `active`、`status`、`type`、`order`、`key`、`desc`、`group`、`index` 等）。即使加反引号能规避语法错误，也改用语义等价的非关键字名（如 `is_active`、`order_num`）。不同 MySQL 版本 / 工具链对关键字的处理不一致，规避胜于修补。
- **MySQL 每列写注释和默认值**（改动含 MySQL DDL 时）：所有 DDL（`CREATE TABLE`、`ALTER TABLE … ADD COLUMN`、ORM 模型如 SQLAlchemy `mapped_column` / JPA 注解、启动期自动建列脚本）中，每一列都写 `COMMENT '…'`（说明含义、取值范围 / 枚举、单位或空值语义），并显式声明 `DEFAULT`（`NOT NULL` 列给业务空值如 `''`、`0`、`CURRENT_TIMESTAMP`；可空列写 `DEFAULT NULL`）。表本身也写 `COMMENT`。`TEXT` / `BLOB` / `JSON` 这类 MySQL 不允许字面默认值的列可以不写 `DEFAULT`，但注释不能省。ORM 里对应 `comment=` 与 `server_default=`（不是只有 Python 侧的 `default=`），保证生成的 DDL 与手写 SQL 一致。
- **MySQL 唯一索引名以 `uniq_` 开头**（改动含 MySQL DDL 时）：手写 SQL 和 ORM（`UniqueConstraint` / unique `Index` 的名字）一律用 `uniq_` 前缀，例如 `uniq_orders_user_id`，不用 `uq_`、`ux_` 之类；手写 SQL 与 ORM 生成的索引名要一致。普通索引的前缀没有硬性规定，按仓库现有惯例。

<!-- drover:section java -->
### Java

- **不内联全限定类名**（写 Java 时）：类型一律通过 `import` 引入后用简单名，不在代码里写 `com.xxx.Yyy` 全路径（字段、参数、`new`、方法调用、泛型、注解都适用）。只有两个同名类确实无法共存时，才对其中一个用全限定名，并加注释说明原因。写完检查不要遗留 `new com.`、`extends com.`、`com.xxx.Yyy.method(` 这类内联全路径。
- **不用嵌套类和内部类**（写 Java 时）：不在类里定义嵌套 / 内部类（含 `static` 静态嵌套类）。请求体、响应体、DTO / VO 等一律独立成文件，放到对应的 `model.dto` / `model.vo` 包下。只有框架强制的场景（如枚举内实现、注解声明）例外。
