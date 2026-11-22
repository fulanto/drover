#!/usr/bin/env python3
"""Claude Code 的 PreToolUse hook：拦下同一条里既有 cd 又有 git 的 Bash 命令，提示改成 git -C <目录> …。

由 drover 的 install.py 装进 ~/.claude/settings.json 的 hooks.PreToolUse（matcher Bash），命令指向装好的包
~/.drover/pkg/hooks/cd_git_guard.py。

为什么要它：Bash 命令里有 cd 又有 git，会触发 Claude Code 内置的安全确认（"This command changes directory before
running a version-control command…"）。这个确认不走权限白名单，也没有 "don't ask again"，每条都要人批。编排者和
worker 派出去的子代理（Explore 等）看不到 CLAUDE.md 和任务书里"用 git -C"的规则，最爱这么写。hook 在内置检查之前
执行，对子代理的 Bash 调用也生效；deny 的理由会回给发命令的 agent，它照着改写重试，就不用人批了（实测）。

判定：引号外有一个 cd（pushd、popd、chdir 也算）命令，又有一个 git 命令（xargs … git 也算），不分先后 → 拒绝。命令之间
按 && || ; | & 换行 ( ) 和反引号分隔；git 前面带 VAR=val 环境变量赋值也算。单引号、双引号里的，注释里的，heredoc
正文里的都不算。
范围照 Claude Code 内置的这项检查（cd-git-compound）：2.1.330 的实现是 some(是 cd) && some(是 git)，不看先后（以前只拦
cd 在前的，git -C x status && cd x && bun test 这类放过去照样弹框）；只认 git 和 xargs … git，换目录只认
cd / pushd / popd / chdir；hg / svn 它不拦，我们也不拦（git -C 这个改法对它们也不适用）。
只拒绝、给提示，不改写命令（不用 updatedInput）。形状正好是 cd <目录> && git <参数…>（可带管道尾巴）时，提示里
直接给出改写好的整条命令。

输出（https://code.claude.com/docs/en/hooks 的 PreToolUse decision control）：拒绝时退出码 0，stdout 一个 JSON
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": …}}；
放行时什么都不输出。
出错一律放行：stdin 不是 JSON、不是 Bash、命令为空、引号没配对、任何异常，都退出码 0、不输出，不能因为 hook 自己的
问题挡住正常命令。每条 Bash 都会跑，所以只用 json、sys，不 import 别的模块。
"""

from __future__ import annotations

import json
import sys

CD_NAMES = {"cd", "pushd", "popd", "chdir"}
# 出现在命令名前面、本身不是命令的词：保留字、管道取反、time。
PREFIX_WORDS = {"!", "{", "}", "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "time"}
# 词在这些字符处结束（引号外）。
WORD_END = set(" \t\r\n;&|()<>`")
# 改写建议只认这几种连接：cd <目录> 和 git 之间。
REWRITE_JOINS = {"&&", ";", "\n"}


def read_word(cmd: str, i: int) -> tuple:
    """从 i 读一个词，返回 (去掉引号后的值, 结束位置)。引号没配对时抛 ValueError（调用方放行）。"""
    n = len(cmd)
    buf = []
    while i < n:
        c = cmd[i]
        if c in WORD_END:
            break
        if c == "\\":
            if i + 1 < n and cmd[i + 1] != "\n":
                buf.append(cmd[i + 1])
            i += 2
        elif c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                raise ValueError("单引号没配对")
            buf.append(cmd[i + 1:j])
            i = j + 1
        elif c == '"':
            i += 1
            while True:
                if i >= n:
                    raise ValueError("双引号没配对")
                d = cmd[i]
                if d == '"':
                    i += 1
                    break
                if d == "\\" and i + 1 < n and cmd[i + 1] in '"\\$`\n':
                    if cmd[i + 1] != "\n":
                        buf.append(cmd[i + 1])
                    i += 2
                    continue
                buf.append(d)
                i += 1
        elif c == "$" and cmd[i + 1:i + 2] == "'":  # $'…'（ANSI-C 引号）
            i += 2
            while True:
                if i >= n:
                    raise ValueError("$'…' 没配对")
                d = cmd[i]
                if d == "'":
                    i += 1
                    break
                if d == "\\" and i + 1 < n:
                    buf.append(cmd[i:i + 2])
                    i += 2
                    continue
                buf.append(d)
                i += 1
        else:
            buf.append(c)
            i += 1
    return "".join(buf), i


def skip_heredocs(cmd: str, i: int, pending: list) -> int:
    """i 是 heredoc 所在行的下一行开头：依次跳过每个 heredoc 的正文，直到它的结束行。"""
    n = len(cmd)
    for delim, strip_tabs in pending:
        while i < n:
            j = cmd.find("\n", i)
            line = cmd[i:n if j < 0 else j]
            i = n if j < 0 else j + 1
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                break
    return i


def lex(cmd: str) -> tuple:
    """切成记号 [(种类, 值, 起点, 终点)]，种类是 word / op / redir。返回 (记号, 有没有 heredoc)。"""
    out = []
    n = len(cmd)
    i = 0
    pending = []          # 这一行里还没跳过正文的 heredoc：(结束标记, 是否去掉行首 tab)
    want_delim = None     # 刚读到 << / <<-：下一个词是结束标记；值是"是否去掉行首 tab"
    had_heredoc = False
    while i < n:
        c = cmd[i]
        if c == "\\" and cmd[i + 1:i + 2] == "\n":  # 续行
            i += 2
        elif c in " \t\r":
            i += 1
        elif c == "\n":
            out.append(("op", "\n", i, i + 1))
            i += 1
            if pending:
                i = skip_heredocs(cmd, i, pending)
                pending = []
        elif c == "#":  # 词首的 # 是注释，到行尾
            j = cmd.find("\n", i)
            i = n if j < 0 else j
        elif c in "&|;":
            two = cmd[i:i + 2]
            if two in ("&&", "||", ";;", "|&"):
                out.append(("op", two, i, i + 2))
                i += 2
            elif two == "&>":  # &> / &>> 重定向，不是后台运行
                j = i + (3 if cmd[i + 2:i + 3] == ">" else 2)
                out.append(("redir", cmd[i:j], i, j))
                i = j
            else:
                out.append(("op", c, i, i + 1))
                i += 1
        elif c in "()`":
            out.append(("op", c, i, i + 1))
            i += 1
        elif c in "<>":
            j = i + 1
            if c == "<" and cmd[j:j + 2] == "<<":       # <<< here-string
                j += 2
            elif c == "<" and cmd[j:j + 1] == "<":      # << / <<- heredoc
                j += 1
                strip_tabs = cmd[j:j + 1] == "-"
                if strip_tabs:
                    j += 1
                want_delim = strip_tabs
            elif cmd[j:j + 1] in (">", "&", "|") or (c == "<" and cmd[j:j + 1] == ">"):  # >> >& >| <& <>
                j += 1
            out.append(("redir", cmd[i:j], i, j))
            i = j
        else:
            value, j = read_word(cmd, i)
            out.append(("word", value, i, j))
            if want_delim is not None:
                pending.append((value, want_delim))
                had_heredoc = True
                want_delim = None
            i = j
    return out, had_heredoc


def split_commands(toks: list) -> list:
    """按 op 记号切成一条条简单命令：[(这条命令前面的 op 值或 None, [word / redir 记号])]。"""
    cmds = []
    joiner, cur = None, []
    for t in toks:
        if t[0] == "op":
            if cur:
                cmds.append((joiner, cur))
            joiner, cur = t[1], []
        else:
            cur.append(t)
    if cur:
        cmds.append((joiner, cur))
    return cmds


def name_index(words: list) -> int:
    """命令名在记号列表里的位置：跳过重定向（连同它的目标、前面紧挨着的 fd 数字）、VAR=val、保留字。没有返回 -1。"""
    skip_next = False
    for k, t in enumerate(words):
        if skip_next:
            skip_next = False
            continue
        if t[0] == "redir":
            skip_next = True
            continue
        nxt = words[k + 1] if k + 1 < len(words) else None
        if t[1].isdigit() and nxt is not None and nxt[0] == "redir" and nxt[2] == t[3]:
            continue  # 2>/dev/null 里的 2
        if is_assignment(t[1]) or t[1] in PREFIX_WORDS:
            continue
        return k
    return -1


def is_assignment(word: str) -> bool:
    eq = word.find("=")
    if eq <= 0:
        return False
    name = word[:eq]
    return name.isascii() and (name[0].isalpha() or name[0] == "_") and all(ch.isalnum() or ch == "_" for ch in name)


def is_git(name: str) -> bool:
    return name.rsplit("/", 1)[-1] == "git"


def runs_git(words: list, k: int) -> bool:
    """这条命令跑的是 git：命令名是 git（可以带路径），或者是 xargs … git。"""
    name = words[k][1]
    if is_git(name):
        return True
    return name == "xargs" and any(t[0] == "word" and is_git(t[1]) for t in words[k + 1:])


def analyze(command: str) -> tuple:
    """返回 (要不要拦, 改写建议或 None)。解析失败抛异常（main 放行）。"""
    toks, had_heredoc = lex(command)
    cmds = split_commands(toks)
    names = []
    for joiner, words in cmds:
        k = name_index(words)
        names.append((joiner, words, k, words[k][1] if k >= 0 else ""))
    has_cd = any(name in CD_NAMES for _, _, _, name in names)
    has_git = any(k >= 0 and runs_git(words, k) for _, words, k, _ in names)
    if not (has_cd and has_git):
        return False, None
    return True, None if had_heredoc else rewrite(command, toks, names)


def rewrite(command: str, toks: list, names: list) -> "str | None":
    """形状正好是 cd <目录> && git <参数…>（&& 也可以是 ; 或换行），后面只接管道时，给出 git -C <目录> <参数…> | …。"""
    if len(names) < 2:
        return None
    head_start, tail_end = names[0][1][0][2], names[-1][1][-1][3]
    ops = [t[1] for t in toks if t[0] == "op" and head_start <= t[2] < tail_end]
    # 首尾只许有换行、结尾的 ;、配对的一层括号（(cd /a && git log) 照样能改）；结尾的 & 是后台运行，不改。
    before = [t[1] for t in toks if t[0] == "op" and t[2] < head_start]
    after = [t[1] for t in toks if t[0] == "op" and t[2] >= tail_end]
    if (any(op not in ("(", "\n") for op in before) or any(op not in (")", "\n", ";") for op in after)
            or before.count("(") != after.count(")")):
        return None
    if not ops or ops[0] not in REWRITE_JOINS or any(op != "|" for op in ops[1:]):
        return None
    (_, cd_words, cd_k, cd_name), (_, git_words, git_k, git_name) = names[0], names[1]
    if cd_name != "cd" or cd_k != 0 or len(cd_words) != 2 or cd_words[1][0] != "word":
        return None
    target = cd_words[1]
    if not target[1] or target[1].startswith("-") or not is_git(git_name):
        return None
    git_tok = git_words[git_k]
    return (command[git_words[0][2]:git_tok[3]] + " -C " + command[target[2]:target[3]]
            + command[git_tok[3]:tail_end])


def deny_reason(suggestion: "str | None") -> str:
    text = ("drover 的 hook 拦下了这条命令：同一条命令里有 cd 又有 git（不分先后）会触发 Claude Code 内置的安全确认，"
            "这个确认没法加白名单，每次都要人批。请改写后重试：用 git -C <目录> <子命令…> 代替 cd <目录> && git <子命令…>，"
            "管道接在后面照用（git -C <目录> log | head）；还要 cd 去跑别的命令的，拆成单独的调用"
            "（git 那几步各自用 git -C <目录>，cd 和其他命令另起一条，不和 git 写在一起）。")
    if suggestion:
        text += "这条可以直接改成：" + suggestion
    return text


def main() -> int:
    try:
        data = json.loads(sys.stdin.buffer.read())
        if not isinstance(data, dict) or data.get("tool_name") != "Bash":
            return 0
        tool_input = data.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not command.strip():
            return 0
        blocked, suggestion = analyze(command)
        if not blocked:
            return 0
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                      "permissionDecisionReason": deny_reason(suggestion)}}
        # 按 UTF-8 写字节：不依赖终端 locale（locale 不是 UTF-8 时 print 中文会抛错，那样就拦不下了）。
        sys.stdout.buffer.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
        sys.stdout.flush()
    except Exception:  # 出错一律放行
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
