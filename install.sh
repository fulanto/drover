#!/bin/sh
# drover 一键安装入口：clone 之后在包根目录跑 ./install.sh [install.py 的参数...]
#
# 先检查系统（macOS / Linux）和 install.py 自己要的 git、python3（3.9 及以上）：缺了就给出官方的安装命令并停下，
# 不替你装（要管理员权限或弹系统对话框）。都在就把参数原样交给 install.py，后面的事（缺 herdr 时问你要不要装、
# 装 Claude Code / Codex 两套入口）都由它做。只用 POSIX sh。

set -u

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1

say() { printf '%s\n' "$*" >&2; }

os=$(uname -s 2>/dev/null || echo unknown)
case "$os" in
  Darwin) os_name=macOS ;;
  Linux) os_name=Linux ;;
  *)
    # 变量后面紧跟中文标点时要写 ${os}：macOS 的 /bin/sh（bash 3.2）会把后面的字节也当成变量名。
    say "install.sh：只支持 macOS 和 Linux（当前系统：${os}）。Windows 上请在 WSL 里装。"
    exit 1
    ;;
esac

missing=0

# 只看 command -v 不够：macOS 没装命令行工具时，/usr/bin/git、/usr/bin/python3 是占位程序，一运行就弹安装对话框并报错。
if ! git --version >/dev/null 2>&1; then
  missing=1
  say "缺 git（drover 给每个任务条目建 git worktree）。官方安装方式（https://git-scm.com/install/）："
  if [ "$os_name" = macOS ]; then
    say "  xcode-select --install     # Apple 的命令行工具，带 git 和 python3"
    say "  brew install git           # 或者用 Homebrew"
  else
    say "  sudo apt-get install git   # Debian / Ubuntu"
    say "  sudo dnf install git       # Fedora"
    say "  sudo pacman -S git         # Arch"
    say "  （其他发行版见 https://git-scm.com/install/linux）"
  fi
fi

py_ok=0
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  py_ok=1
elif python3 --version >/dev/null 2>&1; then
  missing=1
  say "python3 版本太旧：$(python3 --version 2>&1)，install.py 和 drover CLI 要 3.9 及以上。"
else
  missing=1
  say "缺 python3（install.py 和 drover CLI 都是 python3 标准库脚本）。"
fi
if [ "$py_ok" = 0 ]; then
  if [ "$os_name" = macOS ]; then
    say "  xcode-select --install     # Apple 的命令行工具自带 python3"
    say "  brew install python        # 或者 Homebrew"
    say "  或者 python.org 的安装包：https://www.python.org/downloads/macos/"
  else
    say "  多数发行版自带 python3；没有就用包管理器装，比如："
    say "  sudo apt-get install python3   # Debian / Ubuntu"
    say "  sudo dnf install python3       # Fedora"
  fi
fi

if [ "$missing" = 1 ]; then
  say ""
  say "装好上面缺的，再重跑 ./install.sh。"
  exit 1
fi

exec python3 "$here/install.py" "$@"
