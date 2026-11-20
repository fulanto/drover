#!/usr/bin/env python3
"""drover 全部自测的并行运行器（只用标准库）：selftest/ 和 tests/ 下的 test*.py，按用例分给多个进程同时跑。

  python3 selftest/run.py                          # 全部自测，进程数 = 本机逻辑核数
  python3 selftest/run.py -j 4                     # 4 个进程
  python3 selftest/run.py test_push tests/test_install.py   # 只跑这几个文件（.py 和目录可省）
  python3 selftest/run.py -v                       # 每个用例跑完打一行

- 一条命令、不经 shell，可以直接当 accept 的 --verify（"python3 selftest/run.py"）。退出码：全过 0，有失败 / 出错 1，
  参数不对 / 没有用例 2，Ctrl-C 130。
- 粒度是单个用例：N 个常驻的 worker 进程从同一个队列里取下一个用例（谁空谁取，不需要历史耗时）。用例在 worker 里照 unittest
  的规矩跑（setUp / tearDown / addCleanup 照常；类级 fixture 每个用例各跑一次，和 `python3 -m unittest 模块.类.用例` 一样）。
  按文件分的话，最慢的文件（tests/test_install.py，100 秒上下）就是下限；按用例分，下限是最慢的那一个用例。
- 每个用例跑的时候 worker 的 fd 1 / 2 接到临时文件（用例起的子进程漏出来的输出也收进来），只在失败时打印。
- 输出：失败 / 出错随出随打；最后按文件一行（用例耗时加起来、个数、结果）、最慢的 10 个用例、失败的用例，最后一行
  `===== N passed, M skipped in X.Xs (-j J) =====`（accept 的计数行认得这种格式）。
- 不往仓库里写文件（tests/test_update.py 会把 worktree 里没被忽略的文件复制进它的种子仓库）。
- 旧用法照样能跑：python3 -m unittest discover -s selftest、python3 tests/test_install.py 等。
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import tempfile
import time
import traceback
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
SUITES = (PKG / "selftest", PKG / "tests")
SLOWEST = 10
OUTPUT_LIMIT = 20000  # 失败用例收到的输出最多打印这么多字符（取末尾）
RULE = "=" * 70


def add_paths() -> None:
    """两个目录都放进 sys.path：用例按顶层模块导入（selftest/ 下没有 __init__.py，同 discover -s）。worker 初始化也调它。"""
    for d in SUITES:
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))


def default_jobs() -> int:
    count = getattr(os, "process_cpu_count", os.cpu_count)()
    return count or 1


def test_files() -> tuple[dict, list]:
    """{模块名: 相对包根的路径}，和两个目录里重名的模块（重名时按模块名导入会拿错文件）。"""
    found, clashes = {}, []
    for d in SUITES:
        for path in sorted(d.glob("test*.py")):
            rel = str(path.relative_to(PKG))
            if path.stem in found:
                clashes.append(f"{found[path.stem]} 和 {rel}")
            found[path.stem] = rel
    return found, clashes


def flatten(suite) -> list:
    tests = []
    for item in suite:
        tests += flatten(item) if isinstance(item, unittest.TestSuite) else [item]
    return tests


def problem(label: str, test_id: str, kind: str, text: str) -> dict:
    return {"file": label, "id": test_id, "status": "error" if kind == "ERROR" else "fail", "seconds": 0.0,
            "problems": [(kind, test_id, text)], "output": ""}


def collect(files: dict) -> tuple[list, list]:
    """主进程导入各模块、列出用例：([(文件, 用例 id)], [导入失败的结果])。导入失败的模块 unittest 给的是一个
    _FailedTest，当场跑一下就得到那个报错。"""
    loader = unittest.TestLoader()
    items, broken = [], []
    for module, label in files.items():
        try:
            suite = loader.loadTestsFromName(module)
        except Exception:
            broken.append(problem(label, module, "ERROR", traceback.format_exc()))
            continue
        for test in flatten(suite):
            if type(test).__module__ == "unittest.loader":
                broken.append(run_test(label, test, test.id()))
            else:
                items.append((label, test.id()))
    return items, broken


def run_test(label: str, test, test_id: str) -> dict:
    """跑一个用例（或只含一个用例的 suite），fd 1 / 2 接到临时文件；返回可以 pickle 的结果。"""
    result = unittest.TestResult()
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1), os.dup(2)
    with tempfile.TemporaryFile() as capture:
        os.dup2(capture.fileno(), 1)
        os.dup2(capture.fileno(), 2)
        started = time.perf_counter()
        try:
            test.run(result)
        finally:
            seconds = time.perf_counter() - started
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
        capture.seek(0)
        output = capture.read().decode(errors="replace")
    problems = ([("ERROR", str(t), tb) for t, tb in result.errors]
                + [("FAIL", str(t), tb) for t, tb in result.failures]
                + [("UNEXPECTED SUCCESS", str(t), "") for t in result.unexpectedSuccesses])
    if result.errors:
        status = "error"
    elif problems:
        status = "fail"
    elif any(t.id() == test_id for t, _ in result.skipped):
        status = "skip"
    else:
        status = "pass"
    return {"file": label, "id": test_id, "status": status, "seconds": seconds, "problems": problems,
            "output": output if problems else ""}


def run_item(item: tuple) -> dict:
    """worker 里跑的：按 id 加载一个用例并跑它。"""
    label, test_id = item
    try:
        test = unittest.defaultTestLoader.loadTestsFromName(test_id)
    except Exception:
        return problem(label, test_id, "ERROR", traceback.format_exc())
    return run_test(label, test, test_id)


def print_problems(rec: dict) -> None:
    for kind, name, text in rec["problems"]:
        print(f"{RULE}\n{kind}: {name}  [{rec['file']}，{rec['seconds']:.1f}s]\n{'-' * 70}\n{text.rstrip()}", flush=True)
    if rec["output"].strip():
        out = rec["output"][-OUTPUT_LIMIT:]
        print(f"---- 这个用例的输出（stdout / stderr，含它起的子进程）----\n{out.rstrip()}", flush=True)


def print_summary(results: list, wall: float, jobs: int) -> None:
    by_file: dict = {}
    for rec in results:
        f = by_file.setdefault(rec["file"], {"seconds": 0.0, "count": 0, "fail": 0, "error": 0, "skip": 0})
        f["seconds"] += rec["seconds"]
        f["count"] += 1
        if rec["status"] in ("fail", "error", "skip"):
            f[rec["status"]] += 1
    print("\n按文件（用例耗时加起来，秒；并行时分散在各个进程里）：")
    for label, f in sorted(by_file.items(), key=lambda kv: -kv[1]["seconds"]):
        notes = "".join(f"，{word} {f[key]}" for key, word in (("fail", "失败"), ("error", "出错"), ("skip", "跳过"))
                        if f[key])
        print(f"  {f['seconds']:7.1f}  {label}（{f['count']} 个{notes}）")
    print(f"最慢的 {SLOWEST} 个用例（秒）：")
    for rec in sorted(results, key=lambda r: -r["seconds"])[:SLOWEST]:
        print(f"  {rec['seconds']:7.1f}  {rec['id']}")
    bad = [rec for rec in results if rec["status"] in ("fail", "error")]
    if bad:
        print("失败的用例：")
        for rec in sorted(bad, key=lambda r: r["id"]):
            print(f"  {rec['status'].upper():5}  {rec['id']}  [{rec['file']}]")
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("fail", "error", "pass", "skip")}
    parts = [f"{n} {word}" for n, word in ((counts["fail"], "failed"), (counts["pass"], "passed"),
                                            (counts["skip"], "skipped"),
                                            (counts["error"], "error" if counts["error"] == 1 else "errors")) if n]
    print(f"{'=' * 10} {', '.join(parts)} in {wall:.1f}s (-j {jobs}) {'=' * 10}", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 selftest/run.py",
                                     description="并行跑 drover 的全部自测（selftest/ 和 tests/），按用例分给多个进程。")
    parser.add_argument("names", nargs="*", metavar="文件",
                        help="只跑这些测试文件（如 test_push、tests/test_install.py），不写就是全部")
    parser.add_argument("-j", "--jobs", type=int, default=default_jobs(),
                        help=f"同时跑几个进程，默认本机逻辑核数（这台是 {default_jobs()}）")
    parser.add_argument("-v", "--verbose", action="store_true", help="每个用例跑完打一行：结果、耗时、用例 id")
    a = parser.parse_args(argv)
    if a.jobs < 1:
        parser.error("-j 至少是 1")
    files, clashes = test_files()
    if clashes:
        parser.error("两个目录里有同名的测试模块（按模块名导入会拿错文件）：" + "；".join(clashes))
    if a.names:
        wanted = {Path(n).stem for n in a.names}
        missing = sorted(wanted - files.keys())
        if missing:
            parser.error(f"没有这些测试文件：{'、'.join(missing)}（可选：{'、'.join(sorted(files))}）")
        files = {m: f for m, f in files.items() if m in wanted}
    add_paths()
    started = time.perf_counter()
    items, results = collect(files)
    if not items and not results:
        print("没有收集到用例", file=sys.stderr)
        return 2
    jobs = min(a.jobs, max(1, len(items)))
    print(f"drover 自测：{len(items) + len(results)} 个用例（{len(files)} 个文件），{jobs} 个进程", flush=True)
    for rec in results:
        print_problems(rec)
    pool = multiprocessing.get_context("spawn").Pool(jobs, initializer=add_paths)
    try:
        for rec in pool.imap_unordered(run_item, items):
            results.append(rec)
            if rec["problems"]:
                print_problems(rec)
            elif a.verbose:
                print(f"  {rec['status']:5} {rec['seconds']:6.2f}s  {rec['id']}", flush=True)
        pool.close()
    except KeyboardInterrupt:
        pool.terminate()
        pool.join()
        print("\n中断了", file=sys.stderr)
        return 130
    pool.join()  # 正常退出，worker 的 atexit（删假工具的临时目录）才会跑
    print_summary(results, time.perf_counter() - started, jobs)
    return 1 if any(r["status"] in ("fail", "error") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
