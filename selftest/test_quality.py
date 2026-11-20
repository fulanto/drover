"""accept 的验收工具（crapper、dryer）：条目验收只报这次改动碰到的复杂函数（新的、或比 base 更复杂的；本来就复杂的只作参考），
集成检查（--integration）的 dryer 只留至少一边碰到改动行的重复对；工具没装、出错报"测不了"、不挡验收；worktree 里什么都不写
（仓库自己跟踪的 .metrics 也不动）；开关关掉两步都不跑。还有取函数行号的小脚本（CRAP_SPANS_SCRIPT）的输入输出格式。

假工具放在临时状态目录的 tools/<工具>/.venv/bin/python（accept 直接调它，不经工具的入口脚本）：
- 假 crapper 不管收到的脚本是什么，按同样的协议回话：stdin 的 {"root", "files": [{side, path, source?}]}，用 ast 找函数，
  圈复杂度取函数里 "# cc=N" 注释（没有就是 1），.py 以外和 test_ 开头的文件算不分析；每次调用记进 tools/crapper/calls.log。
- 假 dryer 按 tools/dryer/spec.json 列的候选（相对路径）照 dryer report.py 的格式写 <--root>/.metrics/dry.edn，路径换成
  被扫目录下的绝对路径；参数和环境记进 tools/dryer/calls.log。
两个都是：同目录上两级（工具目录）有 fail.txt 就把它的内容打到输出、退出码 1。
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from selftest_support import DroverTestCase

FAKE_CRAPPER_PY = r'''#!{python}
import ast, json, os, re, sys
from pathlib import Path
TOOL = Path(__file__).resolve().parent.parent.parent
req = json.loads(sys.stdin.read())
with (TOOL / "calls.log").open("a") as f:
    f.write(json.dumps({"args": sys.argv[1:3], "files": [[x["side"], x["path"], "source" in x] for x in req["files"]],
                        "cache": os.environ.get("TREE_SITTER_LANGUAGE_PACK_CACHE_DIR")}) + "\n")
if (TOOL / "fail.txt").exists():
    sys.stderr.write((TOOL / "fail.txt").read_text())
    sys.exit(1)
out = {"functions": [], "skipped": []}
for f in req["files"]:
    p = f["path"]
    if not p.endswith(".py") or Path(p).name.startswith("test_"):
        out["skipped"].append(p)
        continue
    src = f["source"] if "source" in f else (Path(req["root"]) / p).read_text()
    lines = src.splitlines()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef):
            m = re.search(r"# cc=(\d+)", "\n".join(lines[node.lineno - 1:node.end_lineno]))
            out["functions"].append({"side": f["side"], "path": p, "name": node.name, "namespace": p[:-3].replace("/", "."),
                                     "complexity": int(m.group(1)) if m else 1, "start_line": node.lineno,
                                     "end_line": node.end_lineno})
print(json.dumps(out))
'''

FAKE_DRYER_PY = r'''#!{python}
import json, os, sys
from pathlib import Path
TOOL = Path(__file__).resolve().parent.parent.parent
args = sys.argv[1:]
with (TOOL / "calls.log").open("a") as f:
    f.write(json.dumps({"args": args, "cache": os.environ.get("TREE_SITTER_LANGUAGE_PACK_CACHE_DIR")}) + "\n")
if (TOOL / "fail.txt").exists():
    print((TOOL / "fail.txt").read_text())
    sys.exit(1)
root, target = Path(args[args.index("--root") + 1]), args[-1]


def span(s):
    path = os.path.join(target, s[0]).replace("\\", "\\\\").replace('"', '\\"')
    return '{:file "%s", :start-line %d, :end-line %d}' % (path, s[1], s[2])


rows = [' {:score %s\n  :language "python"\n  :left %s\n  :right %s\n  :left-nodes 30\n  :right-nodes 31}'
        % (c["score"], span(c["left"]), span(c["right"])) for c in json.loads((TOOL / "spec.json").read_text())]
(root / ".metrics").mkdir(parents=True, exist_ok=True)
(root / ".metrics" / "dry.edn").write_text("{:candidates [\n" + "\n".join(rows) + "\n]}\n" if rows else "{:candidates []}\n")
print("Wrote " + str(root / ".metrics" / "dry.edn"), file=sys.stderr)
'''


def func(name: str, cc: int = 1, body_lines: int = 2) -> str:
    """一个 Python 函数的源码：圈复杂度写在注释里（假 crapper 读它），body_lines 行正文。"""
    body = "".join(f"    x{i} = {i}\n" for i in range(body_lines - 1))
    return f"def {name}():\n    # cc={cc}\n{body}    return 0\n\n\n"


class QualityTests(DroverTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tools = self.state / "tools"
        self.wt = self.work / "app"
        # base：f_old 本来就复杂（12）、g 简单、h 复杂但这次不碰；仓库自己跟踪一份 .metrics（accept 不能动它）。
        self.base = self.init_repo(self.wt, {
            "src/app.py": func("f_old", 12, 4) + func("g", 2) + func("h", 15),
            "src/b.py": func("b1", 1, 6),
            "src/c.py": func("c1", 1, 6),
            "README.md": "# app\n",
            ".metrics/crap.edn": "{:entries []}\n",
        })

    # ---- 假工具

    def install_fake(self, name: str, body: str) -> Path:
        py = self.tools / name / ".venv" / "bin" / "python"
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text(body.replace("{python}", sys.executable, 1))
        py.chmod(0o755)
        return self.tools / name

    def calls(self, name: str) -> list:
        log = self.tools / name / "calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def status_paths(self) -> set:
        out = self.git(self.wt, "status", "--porcelain", "--untracked-files=all")
        return {line[3:] for line in out.splitlines()}

    def accept(self, *args: str) -> dict:
        save = self.state / "s1" / "accept.json"
        res = self.drover_json("accept", "--wt", str(self.wt), "--base", self.base, "--save", str(save), *args)
        self.assertFalse(res.get("verify_failed"), res)
        return res

    # ---- 条目验收：crapper

    def test_item_reports_only_changed_functions(self) -> None:
        """只报这次改动碰到的函数：新函数 n（11）和比 base 更复杂的 g（2→10）要处理；f_old 碰到了但没变得更复杂（12→12）
        只作参考；h（15）没碰、k（3）不够阈值，都不报。README、测试文件进 skipped_files。base 只为有复杂函数的文件取。"""
        self.install_fake("crapper", FAKE_CRAPPER_PY)
        self.write_files(self.wt, {
            "src/app.py": func("f_old", 12, 5) + func("g", 10) + func("h", 15) + func("n", 11) + func("k", 3),
            "README.md": "# app\nmore\n",
            "tests/test_app.py": func("test_x", 20),
        })
        res = self.accept("--brief")
        c = res["complexity"]
        self.assertTrue(c["ok"], c)
        self.assertEqual(c["threshold"], 10)
        full = json.loads((self.state / "s1" / "accept.json").read_text())["complexity"]
        self.assertEqual({(r["name"], r["complexity"], r["base_complexity"]) for r in full["over"]},
                         {("g", 10, 2), ("n", 11, None)})
        self.assertEqual([(r["name"], r["base_complexity"]) for r in full["already_complex"]], [("f_old", 12)])
        self.assertEqual(sorted(full["skipped_files"]), ["README.md", "tests/test_app.py"])
        self.assertEqual(full["changed_functions"], 4)  # f_old、g、n、k；h 没碰
        n = next(r for r in full["over"] if r["name"] == "n")
        self.assertEqual((n["path"], n["namespace"]), ("src/app.py", "src.app"))
        # --brief：要处理的逐个列（新函数 / base 上多少），参考的、不分析的只给个数
        self.assertEqual(c["already_complex"], 1)
        self.assertEqual(c["skipped_files"], 2)
        self.assertTrue(any("n 圈复杂度 11（新函数）" in line for line in c["over"]), c["over"])
        self.assertTrue(any("g 圈复杂度 10（base 上 2）" in line for line in c["over"]), c["over"])
        self.assertNotIn("duplicates", res)
        # 第一次只给路径（小脚本自己读 worktree），第二次只为有复杂函数的 src/app.py 带上 base 的源码；语法包缓存在 tools/grammars
        calls = self.calls("crapper")
        self.assertEqual([call["args"] for call in calls], [["-I", "-c"], ["-I", "-c"]])
        self.assertEqual(calls[0]["files"], [["head", "README.md", False], ["head", "src/app.py", False],
                                             ["head", "tests/test_app.py", False]])
        self.assertEqual(calls[1]["files"], [["base", "src/app.py", True]])
        self.assertEqual(calls[0]["cache"], str(self.tools / "grammars"))
        summary = self.drover_json("accept-summary", str(self.state / "s1" / "accept.json"))
        self.assertEqual(summary["complexity"], c)

    def test_item_cannot_measure_does_not_fail(self) -> None:
        """测不了不挡验收：没装 → "测不了：crapper 没装"；小脚本出错（下不来语法包）→ "测不了：crapper 出错"带原因"""
        self.write_files(self.wt, {"src/app.py": func("n", 11)})
        res = self.accept("--verify", "tsc --noEmit")
        self.assertEqual(res["complexity"]["ok"], False)
        self.assertTrue(res["complexity"]["error"].startswith("测不了：crapper 没装"), res["complexity"])
        self.assertTrue(res["verify"][0]["ok"])
        tool = self.install_fake("crapper", FAKE_CRAPPER_PY)
        (tool / "fail.txt").write_text("tree_sitter_language_pack.DownloadError: failed to download python\n")
        res = self.accept("--brief")
        self.assertEqual(res["complexity"]["ok"], False)
        self.assertTrue(res["complexity"]["error"].startswith("测不了：crapper 出错（退出码 1）"), res["complexity"])
        self.assertIn("DownloadError", res["complexity"]["error"])

    def test_tools_leave_no_files_in_worktree(self) -> None:
        """两步都不往 worktree 写：跑前跑后 git status 里的路径一样，仓库跟踪的 .metrics/crap.edn 没被动过；dryer 的 --root
        是 worktree 外的临时目录、跑完就删了"""
        self.install_fake("crapper", FAKE_CRAPPER_PY)
        self.install_fake("dryer", FAKE_DRYER_PY)
        (self.tools / "dryer" / "spec.json").write_text(json.dumps([{"score": 0.9, "left": ["src/app.py", 1, 6],
                                                                      "right": ["src/b.py", 1, 8]}]))
        self.write_files(self.wt, {"src/app.py": func("n", 11), "src/new.py": func("m", 1)})
        before, tracked = self.status_paths(), (self.wt / ".metrics" / "crap.edn").read_bytes()
        self.accept()
        self.accept("--integration")
        self.assertEqual(self.status_paths(), before)
        self.assertEqual((self.wt / ".metrics" / "crap.edn").read_bytes(), tracked)
        root = Path(self.calls("dryer")[0]["args"][3])
        self.assertNotIn(self.wt.resolve(), [root, *root.parents])
        self.assertFalse(root.exists())

    # ---- 集成检查：dryer

    def test_integration_keeps_only_pairs_touching_changes(self) -> None:
        """--integration：不建图、不跑 crapper；dryer 扫全仓（--threshold 0.8、扫描路径是 worktree 的绝对路径），只留至少一边碰到
        改动行的重复对：app.py 只改了第 3 行，新文件 "d x.py"（名字带空格，git 在 +++ 行尾加 tab）整个都是改动；b↔c、和 app.py
        没改到的行配对的都不留。dry.edn 里转义过的路径（带引号）读回来是原样"""
        self.install_fake("crapper", FAKE_CRAPPER_PY)
        self.install_fake("dryer", FAKE_DRYER_PY)
        spec = [
            {"score": 0.95, "left": ["src/app.py", 2, 5], "right": ["src/b.py", 1, 8]},   # 左边碰到改动
            {"score": 0.91, "left": ["src/b.py", 1, 8], "right": ["src/c.py", 1, 8]},     # 两边都没碰
            {"score": 0.88, "left": ["src/c.py", 1, 8], "right": ["src/app.py", 20, 25]},  # app.py 没改到这几行
            {"score": 0.85, "left": ["src/q \"x\".py", 1, 8], "right": ["src/d x.py", 1, 4]},  # 右边是新文件
        ]
        (self.tools / "dryer" / "spec.json").write_text(json.dumps(spec))
        old = (self.wt / "src" / "app.py").read_text().splitlines(keepends=True)
        self.write_files(self.wt, {"src/app.py": "".join(old[:2] + ["    y = 1\n"] + old[3:]),
                                   "src/d x.py": func("d1", 1, 6)})
        res = self.accept("--integration", "--verify", "tsc --noEmit", "--brief")
        self.assertEqual(res["graph"], {"ok": False, "skipped": True,
                                        "reason": "--integration：集成检查不建图（图谱结论只在条目验收时用）"})
        self.assertNotIn("complexity", res)
        self.assertEqual(self.calls("crapper"), [])
        d = res["duplicates"]
        self.assertTrue(d["ok"], d)
        self.assertEqual((d["threshold"], d["candidates"]), (0.8, 4))
        self.assertEqual(d["pairs"], ["0.95 src/app.py:2-5 ↔ src/b.py:1-8", "0.85 src/q \"x\".py:1-8 ↔ src/d x.py:1-4"])
        call = self.calls("dryer")[0]
        self.assertEqual(call["args"][:2], ["-m", "dryer"])
        self.assertEqual(call["args"][4:], ["--threshold", "0.8", str(self.wt.resolve())])
        self.assertEqual(call["cache"], str(self.tools / "grammars"))
        full = json.loads((self.state / "s1" / "accept.json").read_text())["duplicates"]
        self.assertEqual(full["pairs"][0], {"score": 0.95, "left": "src/app.py:2-5", "right": "src/b.py:1-8"})
        # dryer 出错 → 测不了，不挡验收
        (self.tools / "dryer" / "fail.txt").write_text("Traceback: boom\n")
        res = self.accept("--integration", "--brief")
        self.assertTrue(res["duplicates"]["error"].startswith("测不了：dryer 出错（退出码 1）"), res["duplicates"])

    # ---- 开关

    def test_switch_off_runs_neither(self) -> None:
        """config 的 quality_tools 是 false（或 DROVER_QUALITY_TOOLS=0）：两步都不跑，结果里写明关了"""
        self.install_fake("crapper", FAKE_CRAPPER_PY)
        self.install_fake("dryer", FAKE_DRYER_PY)
        (self.tools / "dryer" / "spec.json").write_text("[]")
        self.write_files(self.wt, {"src/app.py": func("n", 11)})
        cfg = json.loads((self.state / "config.json").read_text())
        (self.state / "config.json").write_text(json.dumps({**cfg, "quality_tools": False}))
        item, integ = self.accept("--brief"), self.accept("--integration", "--brief")
        self.assertEqual(item["complexity"]["skipped"], True)
        self.assertIn("quality_tools", item["complexity"]["reason"])
        self.assertEqual(integ["duplicates"]["skipped"], True)
        (self.state / "config.json").write_text(json.dumps(cfg))
        res = self.drover_json("accept", "--wt", str(self.wt), "--base", self.base, env={"DROVER_QUALITY_TOOLS": "0"})
        self.assertEqual(res["complexity"]["skipped"], True)
        self.assertEqual((self.calls("crapper"), self.calls("dryer")), ([], []))

    # ---- 改动行、小脚本

    def test_changed_lines(self) -> None:
        """改动行按 git diff -U0 <base>：改的行、只删行的删除点（前面那一行）、新文件整个；删掉的文件不在里面；已经提交的也算"""
        self.write_files(self.wt, {"src/b.py": "def b1():\n    # cc=1\n    x0 = 0\n    return 0\n\n\n"})  # 删掉第 4–7 行
        (self.wt / "src" / "c.py").unlink()
        self.commit(self.wt, "worker 自己提交了一部分")
        self.write_files(self.wt, {"src/app.py": (self.wt / "src" / "app.py").read_text().replace("x0 = 0", "x0 = 9", 1),
                                   "src/new.py": "a = 1\nb = 2\n"})
        self.git(self.wt, "add", "-N", ".")
        d = self.load_drover()
        changed = d.changed_lines(str(self.wt), self.base)
        self.assertEqual(changed, {"src/app.py": {3}, "src/b.py": {3}, "src/new.py": {1, 2}})
        self.assertIn("tools", d.RESERVED_SLUGS)
        self.assertTrue({"crapper", "mutator"} <= d.HEAVY_TOOLS)

    def test_crap_spans_script_format(self) -> None:
        """取函数行号的小脚本（CRAP_SPANS_SCRIPT）：用的是 crapper 的 discover.language_of / is_test_file、
        languages.functions_in_file 和 Function 的五个字段（换 crapper 版本要核对的就是这些）；没给 source 就读 root/path，
        给了就用它；不认的语言和测试文件进 skipped。这里用同名的桩包代替真的 crapper。"""
        stub = self.tmp / "stub" / "crapper"
        self.write_files(stub, {
            "__init__.py": "",
            "discover.py": ("from pathlib import Path\n"
                            "def language_of(p):\n    return 'python' if str(p).endswith('.py') else None\n"
                            "def is_test_file(p):\n    return Path(p).name.startswith('test_')\n"),
            "languages/__init__.py": (
                "class F:\n    def __init__(self, **kw):\n        self.__dict__.update(kw)\n"
                "def functions_in_file(language, source, path, root):\n"
                "    return [F(name=line[4:line.index('(')], namespace=path[:-3].replace('/', '.') + '@' + root,\n"
                "              complexity=len(source.splitlines()), start_line=i + 1, end_line=i + 2)\n"
                "            for i, line in enumerate(source.splitlines()) if line.startswith('def ')]\n"),
        })
        root = self.tmp / "proj"
        self.write_files(root, {"pkg/m.py": "x = 1\ndef a():\n    pass\n"})
        d = self.load_drover()
        req = {"root": str(root), "files": [{"side": "head", "path": "pkg/m.py"},
                                            {"side": "base", "path": "pkg/m.py", "source": "def old():\n    pass\n"},
                                            {"side": "head", "path": "README"},
                                            {"side": "head", "path": "pkg/test_m.py"}]}
        p = subprocess.run([sys.executable, "-c", d.CRAP_SPANS_SCRIPT], input=json.dumps(req), capture_output=True,
                           text=True, env={**self.env, "PYTHONPATH": str(stub.parent)}, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout), {
            "functions": [
                {"side": "head", "path": "pkg/m.py", "name": "a", "namespace": f"pkg.m@{root}", "complexity": 3,
                 "start_line": 2, "end_line": 3},
                {"side": "base", "path": "pkg/m.py", "name": "old", "namespace": f"pkg.m@{root}", "complexity": 2,
                 "start_line": 1, "end_line": 2},
            ],
            "skipped": ["README", "pkg/test_m.py"],
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
