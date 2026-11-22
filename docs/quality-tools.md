# 验收工具：crapper、dryer

默认编码规范里"控制复杂度""消除重复"两条（改编自 ub-mode，见[设计取舍与致谢](development.md#设计取舍与致谢)）原本靠 Robert C. Martin 的两个工具做约束。install 默认把它们装好，验收时 drover 自动跑，不用 worker 自己装：

| | crapper（[unclebob/crapper](https://github.com/unclebob/crapper)，0BSD） | dryer（[unclebob/dryer](https://github.com/unclebob/dryer)，0BSD） |
|---|---|---|
| 什么时候跑 | 条目验收（`accept`） | 集成检查（`accept --integration`，`flow/integrate.md` 第 3 步） |
| 跑什么 | 只算圈复杂度（不跑测试、不算覆盖率），零点几秒 | 扫全仓找结构相似的函数（相似度 ≥ 0.8），和验证命令一起交给 heavy 调度（大仓库单核约 85 秒） |
| 报什么 | 这次改动碰到的函数里圈复杂度 ≥ 10、而且是新函数或比原来更复杂的（`complexity.over`）；碰到但本来就这么复杂的只作参考（`already_complex`） | 至少一边碰到这次改动行的重复对（`duplicates.pairs`） |
| 要怎么处理 | worker 要么拆，要么在报告里写理由 | 要么合并，要么写理由 |

- **阈值的依据**：crapper 的 CRAP = CC² × (1 − 覆盖率)³ + CC，覆盖率 100% 时 CRAP 就是圈复杂度 CC。所以 CC ≥ 10 的函数测试写得再全，也到不了 ub-mode 要的"CRAP 10 以下"。不跑覆盖率时只能按这个下界查。
- **跑不了不挡验收**：没装、语法包下不来、工具出错时，结果里写"测不了：原因"，不算验收不通过。
- **不往你的仓库里写东西**：两个工具平时会在项目里写 `.metrics/`。drover 让 dryer 把它写进临时目录；crapper 不走它的命令行，直接调它取函数的那一层拿名字、复杂度和行号（它的输出没有行号，没法只挑改动的函数）。所以 worktree 里不会多出 `.metrics/`，仓库自己跟踪的 `.metrics/` 也不会被动到。
- **不分析的文件**：没有扩展名的脚本（比如 `bin/drover`）、测试文件、两个工具不支持的语言，列在 `complexity.skipped_files`。两个工具支持 Clojure、Java、Go、TypeScript / JavaScript、Rust、Python（dryer 不含 JavaScript）。

**怎么装的**：装进 `~/.drover/tools/<工具>`，钉提交（crapper `c757ec7`、dryer `66ff6d2`）：git clone、切到钉的提交、建 `.venv`、`pip install -e`（照工具自带入口脚本的装法，用你自己的 pip 配置），再跑一次 `--help` 确认能用。本机已有 Python 3.11+ 时，每个约 18MB、十几秒。本机没有 3.11+ 时，drover 还会再下一份自己的 Python（还有 uv）：全新环境实测 `~/.drover/tools` 一共 137MB、安装 114 秒，这里面有 pip、uv 的下载，各人网速不同，实际用时会有出入。

- **要 Python 3.11+**：install 先找 PATH 上的 python3.14…python3.11、python3（macOS 自带的是 3.9，不够），再问 uv。都没有时，用 uv 装一份 3.12 到 `~/.drover/tools/python`（`--no-bin`，不往 `~/.local/bin` 放东西）。连 uv 也没有，就先用 uv 官方安装脚本装一份 drover 私有的到 `~/.drover/tools/bin`（`UV_NO_MODIFY_PATH=1`，不改 shell 配置）。都在 drover 自己的目录里，卸载时一起删。
- **语法包**：第一次分析某种语言时，从 GitHub 下载这种语言的 tree-sitter 语法包，放 `~/.drover/tools/grammars`（python、typescript、tsx 三种实测约 29MB、17 秒）。下载失败那次报"测不了"，下次验收自动重试。
- **装不上**（没网、访问不了 GitHub / PyPI、pip 失败）：删掉半截的 `.venv`，这一项只记警告，drover 其余照装，install 退出码不变；`drover update` 的输出里写"只记警告"。之后重跑 install 或 `drover update` 会按钉的版本补装；`--check` 里只算提示（`!`）。
- **不想要**：`~/.drover/config.json` 写 `"quality_tools": false`（环境变量 `DROVER_QUALITY_TOOLS=0` 也行）。之后不装、不查，验收时两步都不跑；已经装的不会被卸掉，要删就 `rm -rf ~/.drover/tools`。

**手动跑带覆盖率的 crapper 和 mutator**：这两样不默认装、也不默认跑，原因是太重、坑也多（实测）：

- **带覆盖率的 crapper** 会先跑一遍全量测试拿覆盖率（大前端仓库实测 255 秒、约 6 核、4.2GB）。TS 项目还会 `npm install --no-save @vitest/coverage-v8`，改掉 bun / pnpm 装的 `node_modules`。Python 项目在没有 `.venv` 时，会往全局解释器里装 coverage，装不上就把函数全算成 0% 覆盖、误报一片。要跑的话经 heavy 排队，最好用集成检查已经产出的覆盖率：

  ```bash
  cd <仓库>
  ~/.drover/bin/drover heavy -- ~/.drover/tools/crapper/crapper --changed                         # 会跑全量测试
  ~/.drover/bin/drover heavy -- ~/.drover/tools/crapper/crapper --use-existing-coverage src/      # 只读已有的覆盖率报告
  ```

  跑完删掉它在仓库里写的 `.metrics/`（TS 项目还有 `target/`）。
- **mutator**（[unclebob/mutator](https://github.com/unclebob/mutator)，0BSD）要自己装。它要求 crapper 就在旁边的 `../crapper`，所以放 `~/.drover/tools/mutator`（crapper 已经在旁边了）。它自带的入口脚本会拿 PATH 上的 `python3` 建 `.venv`，macOS 上那是 3.9，建不成，所以用 3.11+ 的 python 先手动建好：

  ```bash
  git clone https://github.com/unclebob/mutator.git ~/.drover/tools/mutator
  python3.12 -m venv ~/.drover/tools/mutator/.venv          # 任何 3.11+ 的 python；没有就用 ~/.drover/tools/python 里 uv 装的那份
  ~/.drover/tools/mutator/.venv/bin/pip install -e ~/.drover/tools/mutator
  ```

  它默认把整个测试命令当基线来跑：全量测试本来就不稳（有偶发失败）时，会报 "Baseline failed"，一个变异都跑不了。所以要用 `--test-command` 指定只跑相关的测试文件；并发默认是每核一个 worker，而 heavy 给没见过的命令只按 1 核预留，所以再用 `--max-workers` 限一下：

  ```bash
  cd <仓库>
  ~/.drover/bin/drover heavy -- ~/.drover/tools/mutator/mutator --test-command "node_modules/.bin/vitest run src/lib/format.test.ts" --max-workers 2 src/lib/format.ts
  ```

  实测：覆盖率阶段照样会跑一遍全量；加 `--reuse-coverage` 读已有的覆盖率后，只剩变异阶段，13 秒。另外它在符号链接拼出来的 worker 目录里跑，测试要是用 `Path(__file__).resolve()` 找文件，会解析回原文件，出现假存活。跑完删掉它写的 `.metrics/`。
- 两个都按原阈值看：CRAP < 10、变异测试的存活者要杀掉。
