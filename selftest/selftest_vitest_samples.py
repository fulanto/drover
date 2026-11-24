"""vitest 的真实输出样本：vitest 4.1.7 在本机实测（输出被截走、不是终端），原样收录，只把 RUN 行里的工程路径换成了
/x/probe。每份前面的注释写了命令和测试文件是什么样的，换 vitest 版本时照着重抓。

没注明 --reporter=default 的，都是 vitest 认出自己跑在 AI agent 里时的样子（4.1.x 按 std-env 的 isAgent 把默认 reporter 换成
agent：只列失败的文件、只打失败用例的 console 输出、不带颜色）。在 Claude Code 里跑 accept 时 vitest 就是这样打的。
default reporter 多列通过的文件（" ✓ 文件 (N tests)"）和通过用例的 console 输出；失败明细和汇总行两种 reporter 是同一段
代码打的，结构一样（1.6.1、3.2.4 的 default reporter 也是这个结构）。

输出的结构（解析靠它）：先是文件列表；"Failed Suites N" 一节（整个文件失败的 "文件 [ 文件 ]"、describe 级失败的
"文件 > describe 名"），再是 "Failed Tests N" 一节；每个 error 一行 FAIL、一段报错、一条分隔线 "⎯⎯⎯[i/总 error 数]⎯"
（error 相同的并成一组：几行 FAIL 共用一段报错、一条分隔线）；最后是汇总行。套件失败的用例在汇总里算 skipped，不算 failed。
"""

# describe 级套件失败：describe 里的 beforeAll 抛错，两条用例被跳过。
# vitest run src/suite-hook.test.ts，退出码 1
SUITE_HOOK = """
 RUN  v4.1.7 /x/probe

 ❯ src/suite-hook.test.ts (2 tests | 2 skipped) 2ms

⎯⎯⎯⎯⎯⎯ Failed Suites 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/suite-hook.test.ts > needs setup
Error: setup exploded
 ❯ src/suite-hook.test.ts:6:11
      4| describe("needs setup", () => {
      5|   beforeAll(() => {
      6|     throw new Error("setup exploded");
       |           ^
      7|   });
      8|

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯


 Test Files  1 failed (1)
      Tests  2 skipped (2)
   Start at  00:24:49
   Duration  72ms (transform 8ms, setup 0ms, import 12ms, tests 2ms, environment 0ms)

"""

# 一条用例两个 error：两条 expect.soft 都失败，同名的 FAIL 打两行；另一条用例通过。
# vitest run src/multi-error.test.ts，退出码 1
MULTI_ERROR = """
 RUN  v4.1.7 /x/probe

 ❯ src/multi-error.test.ts (2 tests | 1 failed) 3ms
     × two soft failures 3ms

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/multi-error.test.ts > soft > two soft failures
AssertionError: expected 1 to be 2 // Object.is equality

- Expected
+ Received

- 2
+ 1

 ❯ src/multi-error.test.ts:6:20
      4| describe("soft", () => {
      5|   it("two soft failures", () => {
      6|     expect.soft(1).toBe(2);
       |                    ^
      7|     expect.soft("a").toBe("b");
      8|   });

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/2]⎯

 FAIL  src/multi-error.test.ts > soft > two soft failures
AssertionError: expected 'a' to be 'b' // Object.is equality

Expected: "b"
Received: "a"

 ❯ src/multi-error.test.ts:7:22
      5|   it("two soft failures", () => {
      6|     expect.soft(1).toBe(2);
      7|     expect.soft("a").toBe("b");
       |                      ^
      8|   });
      9|

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[2/2]⎯


 Test Files  1 failed (1)
      Tests  1 failed | 1 passed (2)
   Start at  00:24:51
   Duration  70ms (transform 7ms, setup 0ms, import 11ms, tests 3ms, environment 0ms)

"""

# 四个文件一起跑：describe 级套件失败、整个文件失败（import 时抛错）、一条用例两个 error、两条同名用例抛同样的 error
# （并成一组：两行 FAIL、一条分隔线）。FAIL 行 6 条，分隔线上的总数是 6，汇总行 "Tests  3 failed"。
# vitest run src/suite-hook.test.ts src/file-import.test.ts src/multi-error.test.ts src/same-name.test.ts，退出码 1
MIXED = """
 RUN  v4.1.7 /x/probe

 ❯ src/file-import.test.ts (0 test)
 ❯ src/suite-hook.test.ts (2 tests | 2 skipped) 2ms
 ❯ src/multi-error.test.ts (2 tests | 1 failed) 4ms
     × two soft failures 3ms
 ❯ src/same-name.test.ts (2 tests | 2 failed) 3ms
   × dup 3ms
   × dup 0ms

⎯⎯⎯⎯⎯⎯ Failed Suites 2 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/file-import.test.ts [ src/file-import.test.ts ]
Error: import exploded
 ❯ src/file-import.test.ts:4:7
      2|
      3| // 文件级失败（收集阶段）：模块顶层抛错，一个用例都收集不到。
      4| throw new Error("import exploded");
       |       ^
      5|
      6| it("never collected", () => {

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/6]⎯

 FAIL  src/suite-hook.test.ts > needs setup
Error: setup exploded
 ❯ src/suite-hook.test.ts:6:11
      4| describe("needs setup", () => {
      5|   beforeAll(() => {
      6|     throw new Error("setup exploded");
       |           ^
      7|   });
      8|

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[2/6]⎯


⎯⎯⎯⎯⎯⎯⎯ Failed Tests 3 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/multi-error.test.ts > soft > two soft failures
AssertionError: expected 1 to be 2 // Object.is equality

- Expected
+ Received

- 2
+ 1

 ❯ src/multi-error.test.ts:6:20
      4| describe("soft", () => {
      5|   it("two soft failures", () => {
      6|     expect.soft(1).toBe(2);
       |                    ^
      7|     expect.soft("a").toBe("b");
      8|   });

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[3/6]⎯

 FAIL  src/multi-error.test.ts > soft > two soft failures
AssertionError: expected 'a' to be 'b' // Object.is equality

Expected: "b"
Received: "a"

 ❯ src/multi-error.test.ts:7:22
      5|   it("two soft failures", () => {
      6|     expect.soft(1).toBe(2);
      7|     expect.soft("a").toBe("b");
       |                      ^
      8|   });
      9|

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[4/6]⎯

 FAIL  src/same-name.test.ts > dup
 FAIL  src/same-name.test.ts > dup
AssertionError: expected true to be false // Object.is equality

- Expected
+ Received

- false
+ true

 ❯ src/same-name.test.ts:6:18
      4| for (const _ of [1, 2]) {
      5|   it("dup", () => {
      6|     expect(true).toBe(false);
       |                  ^
      7|   });
      8| }

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[5/6]⎯


 Test Files  4 failed (4)
      Tests  3 failed | 1 passed | 2 skipped (6)
   Start at  00:48:44
   Duration  96ms (transform 35ms, setup 0ms, import 41ms, tests 9ms, environment 0ms)

"""

# 测试文件名不带 .test. / .spec.（配置的 include 是 checks/**/*.check.ts）：FAIL 行的文件 drover 认不出。
# vitest run checks/smoke.check.ts，退出码 1
UNPARSED_NAME = """
 RUN  v4.1.7 /x/probe

 ❯ checks/smoke.check.ts (1 test | 1 failed) 3ms
   × smoke 2ms

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  checks/smoke.check.ts > smoke
AssertionError: expected 'up' to be 'down' // Object.is equality

Expected: "down"
Received: "up"

 ❯ checks/smoke.check.ts:5:16
      3| // 文件名不带 .test. / .spec.：drover 的 FAIL 行正则认不出这一行，自检要兜住。
      4| it("smoke", () => {
      5|   expect("up").toBe("down");
       |                ^
      6| });
      7|

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯


 Test Files  1 failed (1)
      Tests  1 failed (1)
   Start at  00:24:52
   Duration  68ms (transform 6ms, setup 0ms, import 10ms, tests 3ms, environment 0ms)

"""

# 认不出的文件名整个文件失败（import 时抛错），加上认得出的两条用例失败（一条断言、一条超时）。
# vitest run src/assert-and-timeout.test.ts checks/broken.check.ts，退出码 1
UNPARSED_FILE_LEVEL = """
 RUN  v4.1.7 /x/probe

 ❯ checks/broken.check.ts (0 test)
 ❯ src/assert-and-timeout.test.ts (3 tests | 2 failed) 55ms
     × adds 3ms
     × is slow 52ms

⎯⎯⎯⎯⎯⎯ Failed Suites 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  checks/broken.check.ts [ checks/broken.check.ts ]
Error: broken at import
 ❯ checks/broken.check.ts:4:7
      2|
      3| // 文件名不带 .test. / .spec. 的文件级失败：FAIL 行是 "checks/broken.check.ts [ chec…
      4| throw new Error("broken at import");
       |       ^
      5|
      6| it("never collected", () => {

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/3]⎯


⎯⎯⎯⎯⎯⎯⎯ Failed Tests 2 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/assert-and-timeout.test.ts > math > adds
AssertionError: expected 2 to be 3 // Object.is equality

- Expected
+ Received

- 3
+ 2

 ❯ src/assert-and-timeout.test.ts:6:19
      4| describe("math", () => {
      5|   it("adds", () => {
      6|     expect(1 + 1).toBe(3);
       |                   ^
      7|   });
      8|

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[2/3]⎯

 FAIL  src/assert-and-timeout.test.ts > math > is slow
Error: Test timed out in 50ms.
If this is a long-running test, pass a timeout value as the last argument or configure it globally with "testTimeout".
 ❯ src/assert-and-timeout.test.ts:9:3
      7|   });
      8|
      9|   it("is slow", async () => {
       |   ^
     10|     await new Promise((resolve) => setTimeout(resolve, 300));
     11|   }, 50);

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[3/3]⎯


 Test Files  2 failed (2)
      Tests  2 failed | 1 passed (3)
   Start at  00:48:44
   Duration  127ms (transform 16ms, setup 0ms, import 13ms, tests 55ms, environment 0ms)

"""

# 测试自己往 stdout 打了 "[1/3]"、"[2/3]" 两行（进度之类；不带 ⎯，不是分隔线），另有一条用例失败：
# 真的分隔线只有 "⎯⎯⎯[1/1]⎯" 一条。用 default reporter 抓的（通过的用例的 console 输出也打）。
# vitest run --reporter=default src/console-divider.test.ts，退出码 1
CONSOLE_COUNTER = """
 RUN  v4.1.7 /x/probe

stdout | src/console-divider.test.ts > progress > logs a bare counter
[1/3]

stdout | src/console-divider.test.ts > progress > then fails
[2/3]

 ❯ src/console-divider.test.ts (2 tests | 1 failed) 4ms
     ✓ logs a bare counter 1ms
     × then fails 2ms

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/console-divider.test.ts > progress > then fails
AssertionError: expected 'done' to be 'pending' // Object.is equality

Expected: "pending"
Received: "done"

 ❯ src/console-divider.test.ts:14:20
     12|   it("then fails", () => {
     13|     console.log("[2/3]");
     14|     expect("done").toBe("pending");
       |                    ^
     15|   });
     16| });

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯


 Test Files  1 failed (1)
      Tests  1 failed | 1 passed (2)
   Start at  01:11:27
   Duration  81ms (transform 9ms, setup 0ms, import 13ms, tests 4ms, environment 0ms)

"""

# 一条本来就有的失败（src/old.test.js > old broken），另一个文件通过。
# vitest run，退出码 1
OLD = """
 RUN  v4.1.7 /x/probe/old

 ❯ src/old.test.js (1 test | 1 failed) 3ms
   × old broken 2ms

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/old.test.js > old broken
AssertionError: expected 1 to be 2 // Object.is equality

- Expected
+ Received

- 2
+ 1

 ❯ src/old.test.js:4:13
      2|
      3| it("old broken", () => {
      4|   expect(1).toBe(2);
       |             ^
      5| });
      6|

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯


 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 1 passed (2)
   Start at  00:48:45
   Duration  74ms (transform 15ms, setup 0ms, import 24ms, tests 4ms, environment 0ms)

"""

# 同上，通过的那个文件里另有两个没人接的 rejection：多一节 Unhandled Errors（没有 FAIL 行、分隔线不带编号），
# 汇总多一行 "Errors  2 errors"。
# vitest run，退出码 1
OLD_UNHANDLED = """
 RUN  v4.1.7 /x/probe/old

 ❯ src/old.test.js (1 test | 1 failed) 3ms
   × old broken 3ms

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/old.test.js > old broken
AssertionError: expected 1 to be 2 // Object.is equality

- Expected
+ Received

- 2
+ 1

 ❯ src/old.test.js:4:13
      2|
      3| it("old broken", () => {
      4|   expect(1).toBe(2);
       |             ^
      5| });
      6|

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯

⎯⎯⎯⎯⎯⎯ Unhandled Errors ⎯⎯⎯⎯⎯⎯

Vitest caught 2 unhandled errors during the test run.
This might cause false positive tests. Resolve unhandled errors to make sure your tests are not affected.

⎯⎯⎯⎯ Unhandled Rejection ⎯⎯⎯⎯⎯
Error: unhandled boom
 ❯ src/fine.test.js:6:20
      4| it("fine", async () => {
      5|   if (process.env.DRV_UNHANDLED) {
      6|     Promise.reject(new Error("unhandled boom"));
       |                    ^
      7|     Promise.reject(new Error("unhandled boom again"));
      8|     await new Promise((resolve) => setTimeout(resolve, 20));
 ❯ ../node_modules/@vitest/runner/dist/chunk-artifact.js:302:11
 ❯ ../node_modules/@vitest/runner/dist/chunk-artifact.js:1903:26
 ❯ ../node_modules/@vitest/runner/dist/chunk-artifact.js:2326:20
 ❯ runWithCancel ../node_modules/@vitest/runner/dist/chunk-artifact.js:2323:10
 ❯ ../node_modules/@vitest/runner/dist/chunk-artifact.js:2305:20
 ❯ runWithTimeout ../node_modules/@vitest/runner/dist/chunk-artifact.js:2272:10
 ❯ ../node_modules/@vitest/runner/dist/chunk-artifact.js:2955:64

This error originated in "src/fine.test.js" test file. It doesn't mean the error was thrown inside the file itself, but while it was running.
The latest test that might've caused the error is "fine". It might mean one of the following:
- The error was thrown, while Vitest was running this test.
- If the error occurred after the test had been completed, this was the last documented test before it was thrown.

⎯⎯⎯⎯ Unhandled Rejection ⎯⎯⎯⎯⎯
Error: unhandled boom again
 ❯ src/fine.test.js:7:20
      5|   if (process.env.DRV_UNHANDLED) {
      6|     Promise.reject(new Error("unhandled boom"));
      7|     Promise.reject(new Error("unhandled boom again"));
       |                    ^
      8|     await new Promise((resolve) => setTimeout(resolve, 20));
      9|   }
 ❯ ../node_modules/@vitest/runner/dist/chunk-artifact.js:302:11
 ❯ ../node_modules/@vitest/runner/dist/chunk-artifact.js:1903:26
 ❯ ../node_modules/@vitest/runner/dist/chunk-artifact.js:2326:20
 ❯ runWithCancel ../node_modules/@vitest/runner/dist/chunk-artifact.js:2323:10
 ❯ ../node_modules/@vitest/runner/dist/chunk-artifact.js:2305:20
 ❯ runWithTimeout ../node_modules/@vitest/runner/dist/chunk-artifact.js:2272:10
 ❯ ../node_modules/@vitest/runner/dist/chunk-artifact.js:2955:64

This error originated in "src/fine.test.js" test file. It doesn't mean the error was thrown inside the file itself, but while it was running.
The latest test that might've caused the error is "fine". It might mean one of the following:
- The error was thrown, while Vitest was running this test.
- If the error occurred after the test had been completed, this was the last documented test before it was thrown.
⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯


 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 1 passed (2)
     Errors  2 errors
   Start at  00:48:46
   Duration  88ms (transform 13ms, setup 0ms, import 22ms, tests 24ms, environment 0ms)

"""

# --bail 1 串行：第一个文件失败就停，还没开始的两个文件不算进任何一项（各项之和小于总数）。
# vitest run --bail 1 --no-file-parallelism，退出码 1
BAIL_SERIAL = """
 RUN  v4.1.7 /x/probe/old

 ❯ src/old.test.js (1 test | 1 failed) 3ms
   × old broken 2ms

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/old.test.js > old broken
AssertionError: expected 1 to be 2 // Object.is equality

- Expected
+ Received

- 2
+ 1

 ❯ src/old.test.js:4:13
      2|
      3| it("old broken", () => {
      4|   expect(1).toBe(2);
       |             ^
      5| });
      6|

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯


 Test Files  1 failed (3)
      Tests  1 failed (1)
   Start at  00:49:16
   Duration  74ms (transform 6ms, setup 0ms, import 11ms, tests 3ms, environment 0ms)

"""

# --bail=1 并行：另外两个文件已经开始，用例被取消、算成 skipped，汇总行各项之和等于总数。
# vitest run --bail=1，退出码 1
BAIL_PARALLEL = """
 RUN  v4.1.7 /x/probe/old

 ❯ src/old.test.js (1 test | 1 failed) 3ms
   × old broken 3ms

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/old.test.js > old broken
AssertionError: expected 1 to be 2 // Object.is equality

- Expected
+ Received

- 2
+ 1

 ❯ src/old.test.js:4:13
      2|
      3| it("old broken", () => {
      4|   expect(1).toBe(2);
       |             ^
      5| });
      6|

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯


 Test Files  1 failed | 2 passed (3)
      Tests  1 failed | 6 skipped (7)
   Start at  00:48:49
   Duration  76ms (transform 20ms, setup 0ms, import 34ms, tests 10ms, environment 0ms)

"""
