#!/usr/bin/env python3
"""hooks/cd_git_guard.py 的测试（标准库 unittest）：python3 tests/test_cd_git_guard.py

判定和改写建议直接调模块里的函数；stdin / stdout / 退出码这一层用 subprocess 喂真实格式的 hook JSON。
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parent.parent
HOOK = PKG / "hooks" / "cd_git_guard.py"


def load_hook():
    spec = importlib.util.spec_from_file_location("cd_git_guard", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = load_hook()


def hook_input(command, tool_name: str = "Bash") -> dict:
    """PreToolUse 的 stdin（字段照 Claude Code 文档和实测的子代理调用）。"""
    return {"session_id": "s-test", "transcript_path": "/tmp/t.jsonl", "cwd": "/tmp", "permission_mode": "acceptEdits",
            "hook_event_name": "PreToolUse", "tool_name": tool_name,
            "tool_input": {"command": command, "description": "test"}, "tool_use_id": "toolu_test",
            "agent_id": "a97874f5ba2d78347", "agent_type": "Explore"}


def run_hook(stdin: bytes) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, timeout=30)


class GuardCase(unittest.TestCase):
    def assert_blocked(self, command: str) -> "str | None":
        blocked, suggestion = guard.analyze(command)
        self.assertTrue(blocked, f"应该拦：{command!r}")
        return suggestion

    def assert_allowed(self, command: str) -> None:
        blocked, suggestion = guard.analyze(command)
        self.assertFalse(blocked, f"应该放行：{command!r}")
        self.assertIsNone(suggestion)


# ---------------------------------------------------------------- 必须拦


class MustBlockTests(GuardCase):
    def test_block_cd_and_git(self) -> None:
        self.assert_blocked("cd /a && git log")

    def test_block_cd_semicolon_git(self) -> None:
        self.assert_blocked("cd /a; git status")

    def test_block_cd_git_with_pipe_tail(self) -> None:
        self.assert_blocked("cd /a && git ls-tree -r HEAD | grep x | sed -n '1,5p'")

    def test_block_subshell(self) -> None:
        self.assert_blocked("(cd /a && git log)")

    def test_block_newline(self) -> None:
        self.assert_blocked("cd /a\ngit log")

    def test_block_cd_other_command_then_git(self) -> None:
        self.assert_blocked("cd /a && npm test && git status")

    def test_block_env_assignment_before_git(self) -> None:
        self.assert_blocked("cd /a && GIT_PAGER=cat git log")

    def test_block_other_separators_and_forms(self) -> None:
        for command in ["pushd /a && git log", "cd /a || git log", "cd /a & git log", "cd /a | git log",
                        "popd; git status", "cd /a && /usr/bin/git log", "cd /a && A=1 B='x y' git log",
                        "cd /a \\\n && git log", "cd /a 2>&1 && git log 2>&1 | head", "cd /a && ls | xargs git add",
                        "for d in a b; do cd $d && git pull; done", "{ cd /a; git log; }", "cd /a && time git log",
                        "echo `cd /a && git log`", "cd /a && (git log)", "cd /a # 先进去\ngit log"]:
            self.assert_blocked(command)


# ---------------------------------------------------------------- 必须放行


class MustAllowTests(GuardCase):
    def test_allow_git_dash_c(self) -> None:
        self.assert_allowed("git -C /a log")

    def test_allow_plain_git(self) -> None:
        self.assert_allowed("git log")

    def test_allow_cd_without_git(self) -> None:
        self.assert_allowed("cd /a && npm test")

    def test_allow_cd_after_git(self) -> None:
        self.assert_allowed("git status && cd /a")

    def test_allow_double_quoted(self) -> None:
        self.assert_allowed('git commit -m "cd x && git y"')

    def test_allow_single_quoted(self) -> None:
        self.assert_allowed("echo 'cd a && git b'")

    def test_allow_grep_pattern(self) -> None:
        self.assert_allowed('grep -r "cd .* && git" .')

    def test_allow_other_non_commands(self) -> None:
        for command in ["git -C /a ls-tree -r --name-only HEAD | grep -iE 'x|y' | sed -n '1,50p'",
                        "echo cd && git status",                     # cd 是参数，不是命令
                        "ls # cd /a && git log",                     # 注释里
                        "cat > /tmp/x.sh <<'EOF'\ncd /a && git log\nEOF\necho ok",  # heredoc 正文
                        "cat <<-EOF\n\tcd /a; git log\n\tEOF",
                        'git commit -m "$(cat <<\'EOF\'\ncd x && git y\nEOF\n)"',
                        "cd /a && echo git", "cd /a && gitk", "cd /a && npm run git",
                        "echo $'cd a \\' && git b'"]:
            self.assert_allowed(command)

    def test_allow_empty_command(self) -> None:
        for command in ["", "   ", "\n"]:
            p = run_hook(json.dumps(hook_input(command)).encode())
            self.assertEqual((p.returncode, p.stdout), (0, b""), command)

    def test_allow_non_bash_tool(self) -> None:
        p = run_hook(json.dumps(hook_input("cd /a && git log", tool_name="Read")).encode())
        self.assertEqual((p.returncode, p.stdout), (0, b""))

    def test_allow_stdin_not_json(self) -> None:
        for stdin in [b"not json", b"", b"[1, 2]", b"\xff\xfe\x00", b'{"tool_name": "Bash"}',
                      b'{"tool_name": "Bash", "tool_input": "cd /a && git log"}',
                      b'{"tool_name": "Bash", "tool_input": {"command": ["cd /a && git log"]}}']:
            p = run_hook(stdin)
            self.assertEqual((p.returncode, p.stdout), (0, b""), stdin)


# ---------------------------------------------------------------- 改写建议


class SuggestionTests(GuardCase):
    def test_suggestion_simple(self) -> None:
        self.assertEqual(self.assert_blocked("cd /a && git log"), "git -C /a log")

    def test_suggestion_keeps_pipe_tail(self) -> None:
        self.assertEqual(self.assert_blocked("cd /a && git ls-tree -r HEAD | grep x | sed -n '1,5p'"),
                         "git -C /a ls-tree -r HEAD | grep x | sed -n '1,5p'")

    def test_suggestion_keeps_env_and_quoting(self) -> None:
        self.assertEqual(self.assert_blocked("cd /a && GIT_PAGER=cat git log"), "GIT_PAGER=cat git -C /a log")
        self.assertEqual(self.assert_blocked('cd "/a b" && git status --short'), 'git -C "/a b" status --short')
        self.assertEqual(self.assert_blocked("cd ~/x; git status"), "git -C ~/x status")
        self.assertEqual(self.assert_blocked("cd /a\ngit log -1\n"), "git -C /a log -1")
        self.assertEqual(self.assert_blocked("(cd /a && git log)"), "git -C /a log")  # 子 shell 只是限定了 cd

    def test_no_suggestion_when_shape_differs(self) -> None:
        for command in ["cd /a && npm test && git status", "(cd /a && git log) | wc -l", "(cd /a && git log",
                        "cd /a && git log &", "cd /a && git log && echo done", "echo hi; cd /a && git log",
                        "cd - && git log", "cd && git log", "cd -P /a && git log", "pushd /a && git log",
                        "cd /a && ls | xargs git add", "cd /a && git commit -F - <<EOF\nmsg\nEOF"]:
            self.assertIsNone(self.assert_blocked(command), command)

    def test_reason_text(self) -> None:
        with_fix = guard.deny_reason("git -C /a log")
        self.assertIn("git -C <目录>", with_fix)
        self.assertIn("没法加白名单", with_fix)
        self.assertIn("管道接在后面照用", with_fix)
        self.assertIn("拆成单独的调用", with_fix)
        self.assertTrue(with_fix.endswith("这条可以直接改成：git -C /a log"))
        self.assertNotIn("直接改成", guard.deny_reason(None))


# ---------------------------------------------------------------- stdin / stdout / 退出码、出错放行


class HookIoTests(unittest.TestCase):
    def test_deny_json_shape(self) -> None:
        p = run_hook(json.dumps(hook_input("cd /tmp/x && git log")).encode())
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout.decode("utf-8"))
        self.assertEqual(list(out), ["hookSpecificOutput"])
        spec = out["hookSpecificOutput"]
        self.assertEqual((spec["hookEventName"], spec["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertTrue(spec["permissionDecisionReason"].endswith("这条可以直接改成：git -C /tmp/x log"))

    def test_allow_prints_nothing(self) -> None:
        p = run_hook(json.dumps(hook_input("git -C /tmp/x log")).encode())
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))

    def test_unbalanced_quote_allowed(self) -> None:
        with self.assertRaises(ValueError):
            guard.analyze("cd /a && git log 'oops")
        p = run_hook(json.dumps(hook_input("cd /a && git log 'oops")).encode())
        self.assertEqual((p.returncode, p.stdout), (0, b""))

    def test_exception_allowed(self) -> None:
        """analyze 里任何异常：main 返回 0、不输出。"""
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(hook_input("cd /a && git log")).encode()))
        stdout = io.TextIOWrapper(io.BytesIO())
        with mock.patch.object(guard, "analyze", side_effect=RuntimeError("boom")), \
                mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout):
            rc = guard.main()
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.buffer.getvalue(), b"")

    def test_non_utf8_locale_still_denies(self) -> None:
        """locale 不是 UTF-8 时照样输出（按字节写 UTF-8）。"""
        p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(hook_input("cd /a && git log")).encode(),
                           capture_output=True, timeout=30,
                           env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "PYTHONIOENCODING": "ascii"})
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout.decode("utf-8"))["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main(verbosity=2)
