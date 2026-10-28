#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把仓库里的“内网标识”替换成文档占位值，或检查是否还有残留。

两类规则：
  1) `tools/sanitize.local.json`（**不入库**，每台机器自己维护）：精确映射，形如
     [["<内网 IPv4 的正则>", "192.0.2.10"], ["<主机名>", "EXECUTOR-HOST"], ...]
     真实值只存在本机的这个文件里；仓库内的本脚本不含任何真实地址/主机名/账户名。
  2) 内置通用规则（可公开）：真实用户目录 -> `C:\\Users\\<account>`、
     私有 IPv4（RFC1918）-> `192.0.2.10`（RFC 5737 文档地址）。

用法（在仓库根目录）：

    python tools/sanitize.py --check     # 只检查，有残留退出码 1（CI 用这个）
    python tools/sanitize.py --write     # 就地替换

从生产目录同步文档后先跑 `--write`，再 `--check` 确认 0 残留，然后提交。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_MAP = ROOT / "tools" / "sanitize.local.json"
SKIP_FILES = {"tools/sanitize.py", "tools/sanitize.local.json"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "logs",
             ".script_server_state", ".pytest_cache", ".mypy_cache", "dist", "build"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".bundle", ".pyc", ".exe"}

# 通用规则（不含任何真实值，可安全公开）
GENERIC_RULES = [
    (r"C:\\Users\\(?!<account>)[A-Za-z0-9._-]+", r"C:\Users\<account>"),
    (r"\b(?:(?:192\.168|10)\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b",
     "192.0.2.10"),
]

# 通用检查（与通用规则对应，另加 token/邮箱）
GENERIC_CHECKS = [
    ("私有 IPv4", re.compile(r"\b(?:(?:192\.168|10)\.\d{1,3}\.\d{1,3}"
                             r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")),
    ("真实用户目录", re.compile(r"C:\\Users\\(?!<account>)[A-Za-z0-9._-]+")),
    ("疑似 token/长 hex", re.compile(r"\b[0-9a-fA-F]{32,}\b")),
    # 反斜杠转义后的 IPv4：JSON/正则示例里常写成 192\.168\.x\.y，肉眼仍是真实地址
    ("转义形式的 IPv4", re.compile(r"\b\d{1,3}(?:\\+\.\d{1,3}){3}\b")),
    ("非 GitHub 邮箱", re.compile(r"[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com)"
                                  r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
]

# Windows 控制台默认 GBK，打印中文/符号混排时会 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass


def load_local_rules() -> list[tuple[str, str]]:
    if not LOCAL_MAP.exists():
        return []
    try:
        data = json.loads(LOCAL_MAP.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[WARN] 读取 {LOCAL_MAP.name} 失败，仅使用通用规则: {exc}")
        return []
    rules: list[tuple[str, str]] = []
    for item in data:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            rules.append((str(item[0]), str(item[1])))
    return rules


def iter_files():
    """优先只看 git 跟踪的文件（避免扫到缓存/构建产物），没有 git 时退回整树扫描。"""
    tracked: list[str] = []
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                             capture_output=True, check=True).stdout
        tracked = [p for p in out.decode("utf-8").split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        tracked = []

    if tracked:
        for rel in tracked:
            path = ROOT / rel
            if rel in SKIP_FILES or not path.is_file():
                continue
            if path.suffix.lower() in BINARY_SUFFIXES:
                continue
            yield path, rel
        return

    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in SKIP_FILES or any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        yield path, rel


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def main(argv: list[str]) -> int:
    mode = "--write" if "--write" in argv else "--check"
    local_rules = load_local_rules()
    rules = local_rules + GENERIC_RULES
    if not local_rules:
        print("[WARN] 未找到 tools/sanitize.local.json：只能做通用替换/检查"
              "（私有 IP、用户目录、token、邮箱）。")

    changed = 0
    for path, rel in iter_files():
        text = read_text(path)
        if text is None:
            continue
        new = text
        for pattern, repl in rules:
            new = re.sub(pattern, repl.replace("\\", "\\\\"), new)
        if new == text:
            continue
        changed += 1
        if mode == "--write":
            path.write_text(new, encoding="utf-8", newline="\n")
            print(f"[FIX ] {rel}")
        else:
            print(f"[FAIL] {rel}: 含内部标识，可运行 tools/sanitize.py --write")

    offenders = 0
    if mode == "--check":
        checks = [(m[0], re.compile(m[0])) for m in local_rules] + GENERIC_CHECKS
        for path, rel in iter_files():
            text = read_text(path)
            if text is None:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                for name, pattern in checks:
                    if pattern.search(line):
                        offenders += 1
                        print(f"[FAIL] {rel}:{line_no} [{name}] {line.strip()[:110]}")

    print(f"== {mode}: {changed} 个文件需要/已替换，{offenders} 处残留")
    return 0 if offenders == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
